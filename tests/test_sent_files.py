"""A post sent before is sent again by Telegram's file ids, and the table behind that holds nothing about people.

    python tests/test_sent_files.py

Telegram gives every file DownloaderBot sends a file id, and the bot can send that id to any chat with
no upload. downloader_bot/sent_files.py and bot._resend_by_file_id keep those ids, so that a post
delivered once, to anybody, is delivered again without a download. What this pins down:

  * the flow: a fresh row is sent by its ids (no route, no download, no probe, no slot) and only its
    last_used day is touched; no row is a normal fetch that then stores its ids; a stale row asks
    resolvers.lookup first -- present: sent, and the confirmation renewed; missing: the row is deleted
    and the post is fetched as usual; unknown: the row is kept, nothing is sent by id, and the post is
    fetched as usual. A stored post is never sent unconfirmed;
  * an id that Telegram refuses as invalid (single file or album) deletes the row and the same job
    fetches the post; any other BadRequest also deletes the row but is an ordinary failure (nothing
    fetched, the person told what kind); a blocked bot, a flood or a timeout keeps the row;
  * stale rows asked about at once take at most MAX_CONCURRENT_LOOKUPS lookups at a time;
  * the form is part of the key (a "file" row never serves a "media" request and the other way round),
    and so is the version; an album is one row with its ids in order, re-sent as one group with the
    caption on the first; more than ten items, or a message that lacks the kind of file the form
    expects (a clip turned into an animation), is not stored;
  * the store off, a sender that is many people, a missing token and a database that fails: never a
    failed delivery, and no database call at all where there should be none;
  * only a fresh fetch stores: the disk-cache hit does not (a kept entry can outlive the post); the
    large-file, merge, Reddit-image and card paths never touch the store; the redo button under an
    id send works;
  * the allowance: a re-send counts like any download (handle_link records it before the job);
  * the row says nothing about people: no user id, chat id, name, link, platform or caption, and the
    key cannot be computed without the token;
  * resolvers.lookup on its own: yt-dlp is never asked, route health is never touched, "missing" only
    when every route asked said so, Reddit (yt-dlp only) is "unknown";
  * the small pieces: settings, the row's JSON, freshness, which errors are an invalid id, /status.

No network, no database (the five database functions are an in-memory fake), nothing from Telegram.
tests/sent_files_db_scenarios.py runs the real SQL against a scratch Postgres.

To see a check catch breakage, copy the bot into tests/.mirror, break the copy (the way tests/README.md
describes) and run this again: it imports from the mirror when there is one.
"""
import asyncio
import inspect
import logging
import os
import pathlib
import re
import shutil
import sys
import tempfile
import uuid
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent
SRC = HERE / ".mirror" / "downloader_bot"
if not SRC.exists():
    SRC = REPO / "downloader_bot"
sys.path.insert(0, str(SRC))
sys.path.insert(1, str(REPO / "shared"))
sys.path.insert(0, str(HERE / "stubs"))

SCRATCH = pathlib.Path(tempfile.mkdtemp(prefix="downloaderbot-sent-"))
TOKEN = "123:test-token-A"
os.environ.update(ENV_FILES="off", DBOT_TOKEN=TOKEN, DBOT_USERNAME="test", DATABASE_URL="postgresql://stub",
                  DBOT_WORK_DIR=str(SCRATCH))
for key in ("DBOT_SENT_FILES", "DBOT_FILEID_CONFIRM_HOURS", "DBOT_FILEID_KEEP_DAYS", "DBOT_CACHE_SECONDS",
            "DBOT_CACHE_ENTRY_MAX_MB", "DBOT_CACHE_MAX_MB", "DBOT_MAX_DOWNLOAD_MB", "DBOT_MIN_FREE_MB",
            "DBOT_MAX_CONCURRENT_DOWNLOADS", "DBOT_MAX_PENDING_PER_USER"):
    os.environ.pop(key, None)

import bot              # noqa: E402
import db               # noqa: E402
import delivery_cache as dc      # noqa: E402
import i18n             # noqa: E402
import platforms        # noqa: E402
import resolvers        # noqa: E402
import sent_files       # noqa: E402
import video            # noqa: E402
from telegram.error import BadRequest, Forbidden, RetryAfter, TimedOut      # noqa: E402

logging.disable(logging.CRITICAL)

failures = []
total = 0


def check(name, ok, detail=""):
    global total
    total += 1
    print(("  ok  " if ok else "  FAIL  ") + name + (f" -- {detail}" if not ok and detail else ""))
    if not ok:
        failures.append(name)


# ---------------------------------------------------------------------------
# the world: a clock, the database functions, Telegram, the routes
# ---------------------------------------------------------------------------

START = datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)
NOW = [START]
sent_files._clock = lambda: NOW[0]


def advance(**kw):
    NOW[0] = NOW[0] + timedelta(**kw)


class FakeDB:
    """The five functions of db.py that the store uses, in memory, with the clock of the test."""

    def __init__(self):
        self.rows = {}
        self.calls = []
        self.fail = {}

    def today(self):
        return NOW[0].date()

    def _go(self, name, key):
        self.calls.append((name, key))
        if name in self.fail:
            raise self.fail[name]

    def get(self, key):
        self._go("get", key)
        row = self.rows.get(key)
        return (row["items"], row["confirmed_at"]) if row else None

    def put(self, key, items):
        self._go("put", key)
        self.rows[key] = {"items": items, "confirmed_at": NOW[0], "last_used": self.today()}

    def confirmed(self, key):
        self._go("confirmed", key)
        if key in self.rows:
            self.rows[key].update(confirmed_at=NOW[0], last_used=self.today())

    def used(self, key):
        self._go("used", key)
        if key in self.rows:
            self.rows[key]["last_used"] = self.today()

    def drop(self, key):
        self._go("drop", key)
        self.rows.pop(key, None)

    def names(self):
        return [name for name, _ in self.calls]


class World:
    def __init__(self, kinds=("video",), lossless=False, caption=False):
        self.kinds, self.lossless, self.caption = list(kinds), lossless, caption
        self.resolves, self.downloads, self.lookups, self.probes = [], [], [], 0
        self.sent, self.shown, self.texts, self.jobs, self.after = [], [], [], [], 0
        self.deleted = 0
        self.during_send = []
        self.busy_entered = 0
        self.verdict = "present"
        self.id_error = None           # raised by any send whose media is a stored id
        self.animation = False         # a video comes back as an animation
        self.resolve_raises = None
        self.fids = 0
        self.calls = []
        self.spawned = []
        self.allowance = {"allowed": True}


W = World()
DB = FakeDB()


def new_fid():
    W.fids += 1
    return f"FID{W.fids}"


def reply_to(how, kind=None):
    """The message Telegram hands back for a send."""
    def make(media):
        fid = media if isinstance(media, str) else new_fid()
        if how == "document":
            return SimpleNamespace(document=SimpleNamespace(file_id=fid), video=None, photo=[], animation=None)
        if how == "video":
            if W.animation:
                return SimpleNamespace(video=None, animation=SimpleNamespace(file_id=fid), photo=[], document=None)
            return SimpleNamespace(video=SimpleNamespace(file_id=fid), photo=[], document=None, animation=None)
        return SimpleNamespace(photo=[SimpleNamespace(file_id=fid + "-small"), SimpleNamespace(file_id=fid)],
                               video=None, document=None, animation=None)
    return make


class Message:
    def _record(self, how, media, kwargs):
        if isinstance(media, str) and W.id_error is not None:
            raise W.id_error
        W.during_send.append((bot._slots()._value, bot.MAX_CONCURRENT_DOWNLOADS))
        W.sent.append({"how": how, "media": media if isinstance(media, str) else media.read(), "kw": kwargs})
        return reply_to(how)(media)

    async def reply_document(self, document, *args, **kwargs):
        return self._record("document", document, kwargs)

    async def reply_video(self, video_, *args, **kwargs):
        return self._record("video", video_, kwargs)

    async def reply_photo(self, photo, *args, **kwargs):
        return self._record("photo", photo, kwargs)

    async def reply_media_group(self, group, **kwargs):
        if W.id_error is not None and any(isinstance(m.args[0], str) for m in group):
            raise W.id_error
        W.during_send.append((bot._slots()._value, bot.MAX_CONCURRENT_DOWNLOADS))
        medias = [m.args[0] if isinstance(m.args[0], str) else m.args[0].read() for m in group]
        W.sent.append({"how": "group", "kinds": [type(m).__name__ for m in group], "media": medias,
                       "captions": [m.kwargs.get("caption") for m in group]})
        out = []
        for m in group:
            how = {"InputMediaVideo": "video", "InputMediaPhoto": "photo", "InputMediaDocument": "document"}[type(m).__name__]
            out.append(reply_to(how)(m.args[0]))
        return tuple(out)

    async def reply_text(self, text, **kwargs):
        W.texts.append((text, kwargs.get("reply_markup")))


class Status:
    async def set(self, bot_, text, **kwargs):
        W.shown.append(text)
        return self

    async def delete(self, *args, **kwargs):
        W.deleted += 1


class FakeLive:
    @classmethod
    async def reply_to(cls, message, text, **kwargs):
        W.shown.append(text)
        return Status()


@asynccontextmanager
async def counting_busy(*args, **kwargs):
    W.busy_entered += 1
    yield


async def english(user_id, context=None):
    return "en"


async def fake_resolve(platform, url, max_mb=None):
    W.resolves.append((platform, url))
    if W.resolve_raises is not None:
        raise W.resolve_raises
    items = []
    for n, kind in enumerate(W.kinds):
        ext = "mp4" if kind == "video" else "jpg"
        items.append(resolvers.MediaItem(kind, url=f"https://cdn.example/{uuid.uuid4().hex}/{n}", filename=f"clip{n}.{ext}"))
    return resolvers.Resolved(platform, "fake", items)


async def fake_download(item, work_dir=None, max_mb=None):
    if item.path:
        return item.path
    W.downloads.append(item.url)
    path = SCRATCH / f"{uuid.uuid4().hex}.{resolvers._ext_of(item.filename, 'jpg')}"
    path.write_bytes(b"BYTES-OF-THE-CLIP" + item.filename.encode())
    item.path = str(path)
    return str(path)


async def fake_lookup(platform, url):
    W.lookups.append((platform, url))
    return W.verdict


async def nothing(*args, **kwargs):
    return None


async def after(*args, **kwargs):
    W.after += 1


async def probe(path, run=None):
    W.probes += 1
    return None


async def no_refusal(*args, **kwargs):
    return None


def _allowance(user_id):
    W.calls.append("allowance")
    return W.allowance


def _record(user_id, platform):
    W.calls.append(("record", platform))


def _spawn(context, update, work, in_flight=None):
    W.calls.append("spawn")

    async def run():
        try:
            await work()
        finally:
            if in_flight is not None:
                bot._in_flight.pop(in_flight, None)

    W.spawned.append(run)


REAL_LOOKUP = resolvers.lookup
resolvers.resolve, resolvers.download, resolvers.lookup = fake_resolve, fake_download, fake_lookup
video.probe = probe
bot.LiveMessage = FakeLive
bot.lifecycle.busy = counting_busy
bot.get_caption_enabled = lambda user_id: W.caption
bot.get_lossless_enabled = lambda user_id: W.lossless
bot.note_job = lambda ok: W.jobs.append(ok)
bot._after_send = after
bot._free_mb = lambda: 10_000
bot.download_allowance = _allowance
bot.record_download = _record
bot._spawn_download = _spawn
bot.refuse_new_work = no_refusal
bot.sent_files_get, bot.sent_files_put = lambda k: DB.get(k), lambda k, v: DB.put(k, v)
bot.sent_files_confirmed, bot.sent_files_used = lambda k: DB.confirmed(k), lambda k: DB.used(k)
bot.sent_files_drop = lambda k: DB.drop(k)
i18n.get_lang = english

USER, OTHER, CHAT = 7, 8, 700
LINK = platforms.detect_platform("https://www.instagram.com/reel/AAAAAAAAAAA/")[1]
LINK_B = platforms.detect_platform("https://www.instagram.com/reel/BBBBBBBBBBB/")[1]
MESSAGE = Message()


def update_with(user=USER, chat=None, text=None, username="someone", query=None):
    message = SimpleNamespace(text=text, reply_text=MESSAGE.reply_text, reply_document=MESSAGE.reply_document,
                              reply_video=MESSAGE.reply_video, reply_photo=MESSAGE.reply_photo,
                              reply_media_group=MESSAGE.reply_media_group)
    return SimpleNamespace(callback_query=query, effective_user=SimpleNamespace(id=user, username=username),
                           effective_chat=SimpleNamespace(id=chat if chat is not None else user),
                           effective_message=message, message=message)


def new_context():
    return SimpleNamespace(bot=SimpleNamespace(), user_data={}, application=SimpleNamespace())


def fresh(**kw):
    global W, DB
    W = World(**kw)
    DB = FakeDB()
    NOW[0] = START
    bot._download_slots = None
    bot._lookup_slots = None
    bot._in_flight.clear()
    bot._user_pending.clear()
    sent_files.reset_stats()
    sent_files.STORE_VERSION = 1
    sent_files.ENABLED, sent_files.CONFIRM_HOURS = True, 12
    sent_files.configure(TOKEN)
    dc.configure(str(SCRATCH))
    dc.wipe()
    dc.LIFE_SECONDS = 0                      # the disk cache is its own test; off unless a check turns it on
    for p in list(SCRATCH.iterdir()):
        shutil.rmtree(p, ignore_errors=True) if p.is_dir() else p.unlink()
    return W


def run(coro):
    return asyncio.run(coro)


async def send(user=USER, context=None, link=LINK, lossless_override=None, update=None, platform="instagram", **kw):
    """One delivery, straight through _resolve_and_send."""
    context = context or new_context()
    update = update or update_with(user=user)
    ok = await bot._resolve_and_send(update, context, platform, link, lossless_override=lossless_override, **kw)
    return ok, context


def key_of(link=LINK, lossless=False, platform="instagram"):
    return sent_files.post_key(platform, link, lossless)


def only_row():
    return next(iter(DB.rows.values())) if len(DB.rows) == 1 else None


def items_of(row):
    return sent_files.decode_items(row["items"])


def uploads():
    return [s for s in W.sent if isinstance(s.get("media"), bytes) or s["how"] == "group" and any(isinstance(m, bytes) for m in s["media"])]


def by_id():
    return [s for s in W.sent if isinstance(s.get("media"), str) or s["how"] == "group" and any(isinstance(m, str) for m in s["media"])]


def failed_text(kind):
    return i18n.t("en", "download_failed", error=kind)


class Catch(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append((record.levelno, record.getMessage(), bool(record.exc_info)))


class Logs:
    """Everything logged while it is open, at every level."""

    def __enter__(self):
        self.handler = Catch()
        logging.disable(logging.NOTSET)
        self.root = logging.getLogger()
        self.kept = (self.root.handlers[:], self.root.level)
        self.root.handlers, self.root.level = [self.handler], logging.DEBUG
        return self

    def __exit__(self, *exc):
        self.root.handlers, self.root.level = self.kept
        logging.disable(logging.CRITICAL)

    @property
    def text(self):
        return "\n".join(message for _, message, _ in self.handler.records)

    @property
    def tracebacks(self):
        return [r for r in self.handler.records if r[2]]


# ---------------------------------------------------------------------------
# the flow
# ---------------------------------------------------------------------------

async def flow_miss_then_hit():
    print("\n== no row: fetched as usual and stored; then a fresh row is sent by its ids ==")
    fresh()
    ok, _ = await send()
    check("no row: a normal fetch, delivered", ok is True and len(W.resolves) == 1 and len(uploads()) == 1 and W.jobs == [True], (ok, W.resolves, W.jobs))
    check("...the store was read once, before the fetch", DB.names()[:1] == ["get"] and DB.names().count("get") == 1, DB.calls)
    row = only_row()
    check("...and one row was stored", row is not None and DB.names().count("put") == 1, DB.calls)
    check("...with the id Telegram gave the video, in order", row is not None and items_of(row) == [("video", "FID1")], row)
    check("...confirmed now, used today", row is not None and row["confirmed_at"] == START and row["last_used"] == START.date(), row)
    check("...under the key of this post in this form", list(DB.rows) == [key_of()], list(DB.rows))
    check("counted: one row read and none found, one stored", sent_files.stats()["miss"] == 1 and sent_files.stats()["stored"] == 1, sent_files.stats())

    advance(hours=3)
    DB.rows[key_of()]["last_used"] = date(2026, 9, 1)
    probes_before, sends_before = W.probes, len(W.sent)
    ok, ctx = await send(user=OTHER)
    check("another person, three hours later: delivered", ok is True and W.jobs == [True, True])
    check("...by the stored id, as a video, in one send", len(W.sent) == sends_before + 1 and W.sent[-1]["how"] == "video" and W.sent[-1]["media"] == "FID1", W.sent[-1])
    check("...no route asked, nothing downloaded, nothing looked up, nothing measured",
          len(W.resolves) == 1 and len(W.downloads) == 1 and W.lookups == [] and W.probes == probes_before, (W.resolves, W.lookups))
    check("...no shape sent with the id (Telegram has it)", not {"width", "height", "duration"} & set(W.sent[-1]["kw"]), W.sent[-1]["kw"])
    check("...no slot held for it", W.during_send[-1][0] == W.during_send[-1][1], W.during_send)
    check("...the status message cleaned up, the after-send done once more", W.deleted == 2 and W.after == 2, (W.deleted, W.after))
    check("...last_used touched", DB.rows[key_of()]["last_used"] == NOW[0].date(), DB.rows[key_of()])
    check("...and confirmed_at NOT touched (only a fetch or a lookup proves the post public)", DB.rows[key_of()]["confirmed_at"] == START, DB.rows[key_of()])
    check("...nothing written but the day", [n for n in DB.names() if n not in ("get",)] == ["put", "used"], DB.calls)
    token = list(ctx.user_data["redeliver"])[-1]
    check("...the redo button is remembered for the form sent", ctx.user_data["redeliver"][token]["lossless"] is False
          and ctx.user_data["redeliver"][token]["url"] == LINK)
    check("counted: a fresh row", sent_files.stats()["hit"] == 1, sent_files.stats())


async def flow_stale():
    print("\n== a row older than the confirmation window asks first ==")
    # present
    fresh()
    await send()
    advance(hours=13)
    resolves = len(W.resolves)
    ok, _ = await send(user=OTHER)
    check("stale + present: sent by its id", ok is True and W.sent[-1]["media"] == "FID1" and len(W.resolves) == resolves, W.sent[-1])
    check("...after exactly one lookup", W.lookups == [("instagram", LINK)], W.lookups)
    check("...and the confirmation renewed, with the day", DB.rows[key_of()]["confirmed_at"] == NOW[0]
          and DB.rows[key_of()]["last_used"] == NOW[0].date() and "confirmed" in DB.names())
    check("counted: stale and present", sent_files.stats()["stale"] == 1 and sent_files.stats()["present"] == 1, sent_files.stats())
    advance(hours=11)
    await send(user=OTHER)
    check("...and 11 hours after that it is fresh again: no second lookup", len(W.lookups) == 1, W.lookups)

    # missing
    fresh()
    await send()
    advance(hours=13)
    W.verdict = "missing"
    ok, _ = await send(user=OTHER)
    check("stale + missing: the row was deleted", "drop" in DB.names(), DB.calls)
    check("...then the post is fetched as usual in the same job", ok is True and len(W.resolves) == 2 and len(uploads()) == 2 and by_id() == [], (W.resolves, W.sent))
    check("...and the new delivery is stored again, with its own id", only_row() is not None and items_of(only_row()) == [("video", "FID2")], DB.rows)
    check("counted: a lookup that said missing", sent_files.stats()["missing"] == 1, sent_files.stats())

    # missing, and the post really is gone
    fresh()
    await send()
    advance(hours=13)
    W.verdict = "missing"
    W.resolve_raises = resolvers.NothingWorked("instagram", {"x": resolvers.ProviderFailed("gone", "missing")})
    ok, _ = await send(user=OTHER)
    check("stale + missing + really gone: the usual answer for a removed post, and the row is gone",
          ok is False and W.shown[-1] == i18n.t("en", "download_missing") and DB.rows == {} and by_id() == [], (ok, W.shown, DB.rows))

    # unknown
    fresh()
    await send()
    advance(hours=13)
    W.verdict = "unknown"
    W.resolve_raises = resolvers.NothingWorked("instagram", {"x": resolvers.ProviderFailed("timeout", "error")})
    before = dict(DB.rows[key_of()])
    ok, _ = await send(user=OTHER)
    check("stale + unknown: never sent unconfirmed", by_id() == [] and ok is False, W.sent)
    check("...the row is kept, as it was", DB.rows.get(key_of()) == before and "drop" not in DB.names(), DB.rows)
    check("...and the normal path ran (and said what it says)", len(W.resolves) == 2 and W.shown[-1] == i18n.t("en", "download_all_routes_failed"), W.shown)

    fresh()
    await send()
    advance(hours=13)
    W.verdict = "unknown"
    ok, _ = await send(user=OTHER)
    check("stale + unknown, and the fetch works: delivered from bytes, and the row renewed with the new ids",
          ok is True and by_id() == [] and len(uploads()) == 2 and items_of(only_row()) == [("video", "FID2")]
          and only_row()["confirmed_at"] == NOW[0], (W.sent, DB.rows))
    check("counted: unknown", sent_files.stats()["unknown"] == 1, sent_files.stats())

    # the lookup itself blows up
    fresh()
    await send()
    advance(hours=13)

    async def explode(platform, url):
        raise RuntimeError("secret-lookup-text")

    real = resolvers.lookup
    resolvers.lookup = explode
    try:
        with Logs() as logs:
            ok, _ = await send(user=OTHER)
    finally:
        resolvers.lookup = real
    check("a lookup that raises is 'unknown': fetched as usual", ok is True and by_id() == [] and len(W.resolves) == 2, W.sent)
    check("...logged as the kind of failure", "RuntimeError" in logs.text and "secret-lookup-text" not in logs.text, logs.text)

    print("\n== the window is the setting ==")
    fresh()
    sent_files.CONFIRM_HOURS = 2
    await send()
    advance(hours=1, minutes=59)
    await send(user=OTHER)
    check("under it: fresh", W.lookups == [] and len(by_id()) == 1, W.lookups)
    advance(hours=2)
    await send(user=OTHER)
    check("past it: asks", len(W.lookups) == 1, W.lookups)

    print("\n== stale rows asked about at once: a few lookups at a time ==")
    fresh()
    await send()
    advance(hours=13)
    gate, running, peak = asyncio.Event(), [0], [0]

    async def slow_lookup(platform, url):
        running[0] += 1
        peak[0] = max(peak[0], running[0])
        await gate.wait()
        running[0] -= 1
        return "present"

    saved = resolvers.lookup
    resolvers.lookup = slow_lookup
    try:
        tasks = [asyncio.ensure_future(send(user=100 + n)) for n in range(5)]
        for _ in range(30):
            await asyncio.sleep(0)
        check("five people sending an old post at once: two lookups run, three wait for a place",
              running[0] == bot.MAX_CONCURRENT_LOOKUPS == 2 and peak[0] == 2, (running, peak))
        gate.set()
        results = await asyncio.gather(*tasks)
    finally:
        resolvers.lookup = saved
    check("...and once they answer, all five are sent by id, never more than two asking",
          [ok for ok, _ in results] == [True] * 5 and len(by_id()) == 5 and peak[0] == 2, (results, peak))


async def flow_invalid_ids():
    print("\n== an id Telegram refuses: forget it, and fetch in the same job ==")
    for text in ("Bad Request: wrong file identifier/HTTP URL specified", "Bad Request: Wrong remote file identifier specified",
                 "Bad Request: invalid file_id", "Bad Request: type of file mismatch",
                 "Bad Request: FILE REFERENCE expired", "Bad Request: MEDIA_EMPTY"):
        fresh()
        await send()
        W.id_error = BadRequest(text)
        resolves = len(W.resolves)
        ok, ctx = await send(user=OTHER)
        W.id_error = None
        check(f"'{text[:44]}': the post is fetched in the same job and delivered",
              ok is True and len(W.resolves) == resolves + 1 and len(uploads()) == 2, (ok, W.resolves, W.sent))
    fresh()
    await send()
    W.id_error = BadRequest("Bad Request: wrong file identifier SECRET-FILE-ID https://secret.example/x")
    ok, ctx = await send(user=OTHER)
    W.id_error = None
    check("the row was deleted and a new one stored", "drop" in DB.names() and DB.names().count("put") == 2
          and items_of(only_row()) == [("video", "FID2")], (DB.calls, DB.rows))
    check("no 'download failed' was shown, and the job is counted once, as a success", failed_text("BadRequest") not in W.shown and W.jobs == [True, True], (W.shown, W.jobs))
    check("the status message was reused: 'Fetching...' then the normal path's own", W.shown.count(i18n.t("en", "fetching")) == 2, W.shown)
    check("only the token of the delivery that happened is remembered", len(ctx.user_data["redeliver"]) == 1, ctx.user_data)
    check("counted: one invalid id", sent_files.stats()["invalid"] == 1, sent_files.stats())

    fresh()
    await send()
    W.id_error = BadRequest("Bad Request: wrong file identifier SECRET-FILE-ID https://secret.example/x")
    with Logs() as logs:
        await send(user=OTHER)
    W.id_error = None
    check("logged once as the kind of failure: no id, no link, no text of the error",
          "BadRequest" in logs.text and "SECRET-FILE-ID" not in logs.text and "secret.example" not in logs.text
          and "FID1" not in logs.text and "instagram.com" not in logs.text and not logs.tracebacks, logs.text)

    print("\n== ...an album the same ==")
    fresh(kinds=("photo", "photo", "photo"))
    await send()
    W.id_error = BadRequest("Bad Request: wrong remote file identifier specified")
    ok, _ = await send(user=OTHER)
    W.id_error = None
    check("an invalid id in an album: fetched in the same job, delivered as a group from bytes",
          ok is True and len(W.resolves) == 2 and len(uploads()) == 2, (ok, W.resolves, [s["how"] for s in W.sent]))
    check("...the old row deleted and the new ids stored", "drop" in DB.names() and items_of(only_row()) == [("photo", "FID4"), ("photo", "FID5"), ("photo", "FID6")], DB.rows)


async def flow_other_failures():
    print("\n== any other failure to send by id is an ordinary failure ==")
    for exc in (Forbidden("Forbidden: bot was blocked by the user"), RetryAfter(5), TimedOut("Timed out"),
                BadRequest("Bad Request: chat not found"), BadRequest("Bad Request: message text is empty"),
                BadRequest("Bad Request: DOCUMENT_INVALID")):
        fresh()
        await send()
        before = dict(DB.rows[key_of()])
        W.id_error = exc
        resolves, jobs = len(W.resolves), list(W.jobs)
        ok, _ = await send(user=OTHER)
        W.id_error = None
        kind = type(exc).__name__
        check(f"{kind} ({str(exc)[:30]!r}): the person is told what kind, nothing is fetched",
              ok is False and W.shown[-1] == failed_text(kind) and len(W.resolves) == resolves, (ok, W.shown))
        if isinstance(exc, BadRequest):
            # The review's S2: a wording is_invalid_file_id does not know would otherwise
            # fail every send of this post, for everyone, until the 90-day sweep.
            check("...a BadRequest drops the row all the same (not only the known wordings), counted as a failed job",
                  key_of() not in DB.rows and "drop" in DB.names() and W.jobs == jobs + [False], (DB.rows, W.jobs))
            resolves = len(W.resolves)
            ok, _ = await send(user=OTHER)
            check("...so the next send of the post fetches it and stores fresh ids",
                  ok is True and len(W.resolves) == resolves + 1 and key_of() in DB.rows, (ok, W.resolves, DB.rows))
        else:
            check("...the row is kept as it was (a blocked bot, a flood or a timeout says nothing about the ids), counted as a failed job",
                  DB.rows.get(key_of()) == before and "drop" not in DB.names() and W.jobs == jobs + [False], (DB.rows, W.jobs))


async def flow_forms():
    print("\n== a file row never serves a media request, and the other way round ==")
    fresh()
    await send()                                               # media
    check("a media delivery makes the media row", list(DB.rows) == [key_of(lossless=False)])
    W.lossless = True
    ok, _ = await send(user=OTHER)
    check("a lossless request is not served by it: fetched", ok is True and len(W.resolves) == 2 and by_id() == [], (W.resolves, W.sent))
    check("...delivered as a document, and stored under the other key", W.sent[-1]["how"] == "document"
          and set(DB.rows) == {key_of(lossless=False), key_of(lossless=True)}, (W.sent[-1], list(DB.rows)))
    resolves = len(W.resolves)
    ok, ctx = await send(user=OTHER)
    check("the lossless request again: by id, as a document", ok is True and len(W.resolves) == resolves
          and W.sent[-1]["how"] == "document" and isinstance(W.sent[-1]["media"], str)
          and W.sent[-1]["kw"].get("disable_content_type_detection") is True, W.sent[-1])
    token = list(ctx.user_data["redeliver"])[-1]
    check("...and the redo button remembers the lossless form", ctx.user_data["redeliver"][token]["lossless"] is True)
    W.lossless = False
    ok, _ = await send(user=OTHER)
    check("the media request again: by id, as a video, from the other row", W.sent[-1]["how"] == "video" and W.sent[-1]["media"] == "FID1", W.sent[-1])

    fresh()
    W.lossless = True
    await send()
    W.lossless = False
    ok, _ = await send(user=OTHER)
    check("the other way round: a file row does not serve a media request", ok is True and len(W.resolves) == 2 and by_id() == [], (W.resolves, W.sent))
    fresh()
    await send()
    ok, _ = await send(user=OTHER, lossless_override=True)
    check("the override of the redo button picks the row of the form it asks for", len(W.resolves) == 2 and W.sent[-1]["how"] == "document", W.sent)
    check("...stored under that form", key_of(lossless=True) in DB.rows)

    print("\n== the version is part of the key ==")
    fresh()
    await send()
    sent_files.STORE_VERSION = 2
    ok, _ = await send(user=OTHER)
    check("after a bump the old row is unreachable: fetched", ok is True and len(W.resolves) == 2 and by_id() == [], W.resolves)
    check("...and the new row is a different one", len(DB.rows) == 2, list(DB.rows))
    sent_files.STORE_VERSION = 1
    await send(user=OTHER)
    check("back at the old version the old row is reachable again", len(by_id()) == 1 and W.sent[-1]["media"] == "FID1", W.sent[-1])


async def flow_albums():
    print("\n== an album is one row, with its ids in order ==")
    fresh(kinds=("photo", "video", "photo"), caption=True)
    ok, _ = await send()
    check("a three-file post, delivered as one group", ok is True and W.sent[-1]["how"] == "group" and len(W.sent) == 1, W.sent)
    check("one row, three ids, in delivery order", len(DB.rows) == 1 and items_of(only_row()) == [("photo", "FID1"), ("video", "FID2"), ("photo", "FID3")], DB.rows)
    sent_before, probes_before = len(W.sent), W.probes
    ok, ctx = await send(user=OTHER)
    group = W.sent[-1]
    check("sent again as one group by the three ids, in order", ok is True and len(W.sent) == sent_before + 1
          and group["how"] == "group" and group["media"] == ["FID1", "FID2", "FID3"], group)
    check("...of the right kinds", group["kinds"] == ["InputMediaPhoto", "InputMediaVideo", "InputMediaPhoto"], group["kinds"])
    credit = i18n.t("en", "download_credit_caption", username=bot.BOT_USERNAME)
    check("...with the caption on the first item only", group["captions"] == [credit, None, None], group["captions"])
    check("...the album's own line follows, with the buttons", W.texts and W.texts[-1][0] == i18n.t("en", "album_delivered", count=3) and W.texts[-1][1] is not None, W.texts)
    remembered = list(ctx.user_data["redeliver"].values())[-1]
    check("...and the memory behind the buttons has the counts (photos, items) a merge needs", (remembered["photos"], remembered["items"]) == (2, 3), remembered)
    check("no route, no download, and the video in it is not measured (Telegram has its shape)",
          len(W.resolves) == 1 and len(W.downloads) == 3 and W.probes == probes_before, (W.resolves, W.probes, probes_before))

    print("\n== limits on what is stored ==")
    fresh(kinds=("photo",) * 10)
    await send()
    check("ten items (one chunk): stored", len(DB.rows) == 1 and len(items_of(only_row())) == 10, DB.rows)
    ok, _ = await send(user=OTHER)
    check("...and sent again as one group of ten", len(by_id()) == 1 and len(W.sent[-1]["media"]) == 10, W.sent[-1])
    fresh(kinds=("photo",) * 11)
    ok, _ = await send()
    check("eleven items (two chunks): delivered, and not stored", ok is True and DB.rows == {} and DB.names().count("put") == 0
          and len([s for s in W.sent if s["how"] == "group"]) == 2, (DB.rows, W.sent))
    await send(user=OTHER)
    check("...and it is one", len(W.resolves) == 2 and by_id() == [])
    fresh()
    W.animation = True
    ok, _ = await send()
    check("an animation where a video was expected: delivered, and nothing stored", ok is True and DB.rows == {} and DB.names().count("put") == 0, (DB.rows, DB.calls))
    fresh(kinds=("photo", "video"))
    W.animation = True
    await send()
    check("...an album with one such message: nothing stored either", DB.rows == {}, DB.rows)

    fresh()
    real_reply = Message._record

    def lacking(self, how, media, kwargs):
        real_reply(self, how, media, kwargs)
        return SimpleNamespace()

    Message._record = lacking
    try:
        ok, _ = await send()
    finally:
        Message._record = real_reply
    check("a reply with no file in it at all: delivered, nothing stored", ok is True and DB.rows == {}, DB.rows)
    fresh()
    real_send = bot._send_media

    async def returns_nothing(*args, **kwargs):
        await real_send(*args, **kwargs)

    bot._send_media = returns_nothing
    try:
        ok, _ = await send()
    finally:
        bot._send_media = real_send
    check("a send that reports no ids: delivered, nothing stored", ok is True and DB.rows == {})


async def flow_off_and_errors():
    print("\n== the store off, a sender that is many people, no token: no database call at all ==")
    fresh()
    await send()
    DB.calls.clear()
    sent_files.ENABLED = False
    resolves = len(W.resolves)
    counted = {k: v for k, v in sent_files.stats().items() if k != "enabled"}
    ok, _ = await send(user=OTHER)
    check("DBOT_SENT_FILES=0: a normal fetch and not one database call", ok is True and len(W.resolves) == resolves + 1 and DB.calls == [] and W.lookups == [], (DB.calls, W.lookups))
    check("...nothing counted, and /status says off", sent_files.stats()["enabled"] is False
          and {k: v for k, v in sent_files.stats().items() if k != "enabled"} == counted, sent_files.stats())
    fresh()
    sent_files.configure(None)
    ok, _ = await send()
    check("no token, so no key: off", ok is True and DB.calls == [] and sent_files.enabled() is False, DB.calls)
    sent_files.configure("")
    check("an empty token: off", sent_files.enabled() is False)
    for sender in sorted(dc.SHARED_SENDER_IDS):
        fresh()
        await send(user=sender)
        await send(user=sender)
        check(f"{sender}, who is many people: never reads, never writes", DB.calls == [] and W.lookups == [] and len(W.resolves) == 2 and by_id() == [], (DB.calls, W.resolves))
    fresh()
    await send(user=OTHER)
    ok, _ = await send(user=1087968824)
    check("...and a stored post is not served to it either", DB.names() == ["get", "put"] and len(W.resolves) == 2 and by_id() == [], DB.calls)

    print("\n== a database that fails costs a fetch, never a delivery ==")
    fresh()
    DB.fail["get"] = ConnectionError("secret-dsn://user:pw@host")
    with Logs() as logs:
        ok, _ = await send()
    check("a read that fails: a normal fetch, delivered, nothing shown", ok is True and len(W.resolves) == 1 and failed_text("ConnectionError") not in W.shown
          and W.shown[-1] != failed_text("ConnectionError"), (ok, W.shown))
    check("...logged as the kind of failure only", "ConnectionError" in logs.text and "secret-dsn" not in logs.text and "pw@host" not in logs.text and not logs.tracebacks, logs.text)
    check("...counted as no row", sent_files.stats()["miss"] == 1, sent_files.stats())

    fresh()
    await send()
    DB.fail["get"] = TimeoutError("db away")
    ok, _ = await send(user=OTHER)
    check("a read that fails with a row there: still a normal fetch", ok is True and len(W.resolves) == 2 and by_id() == [])

    fresh()
    DB.fail["put"] = ConnectionError("secret-write-text")
    with Logs() as logs:
        ok, _ = await send()
    check("a write that fails: delivered all the same", ok is True and W.jobs == [True] and failed_text("ConnectionError") not in W.shown and W.deleted == 1, (ok, W.shown, W.jobs))
    check("...logged as the kind of failure only", "ConnectionError" in logs.text and "secret-write-text" not in logs.text and not logs.tracebacks, logs.text)

    fresh()
    await send()
    DB.fail["used"] = ConnectionError("secret-touch-text")
    with Logs() as logs:
        ok, _ = await send(user=OTHER)
    check("a touch that fails after a send by id: delivered, and still a success", ok is True and W.jobs == [True, True] and len(by_id()) == 1, (ok, W.jobs))
    check("...logged as the kind of failure only", "ConnectionError" in logs.text and "secret-touch-text" not in logs.text, logs.text)

    fresh()
    await send()
    DB.fail["drop"] = ConnectionError("secret-drop-text")
    W.id_error = BadRequest("Bad Request: wrong file identifier")
    ok, _ = await send(user=OTHER)
    W.id_error = None
    check("a drop that fails after an invalid id: the post is still fetched and delivered", ok is True and len(W.resolves) == 2, (ok, W.resolves))

    print("\n== a row that cannot be read is no row ==")
    for junk in ("not json", "[]", "{}", "null", '[["video", ""]]', '[["audio", "x"]]', '[["video"]]', '[["video", 5]]', "[" + ",".join(['["photo","x"]'] * 11) + "]", '"x"', ""):
        fresh()
        DB.rows[key_of()] = {"items": junk, "confirmed_at": START, "last_used": START.date()}
        ok, _ = await send()
        check(f"{junk[:40]!r}: a normal fetch, delivered, and the row replaced", ok is True and by_id() == [] and len(W.resolves) == 1
              and items_of(DB.rows[key_of()]) == [("video", "FID1")], (ok, DB.rows))
    fresh()
    DB.rows[key_of()] = {"items": '[["video","X"]]', "confirmed_at": "yesterday", "last_used": START.date()}
    W.verdict = "unknown"
    ok, _ = await send()
    check("a confirmation that is not a time counts as stale", W.lookups == [("instagram", LINK)] and by_id() == [], W.lookups)


# ---------------------------------------------------------------------------
# the other paths
# ---------------------------------------------------------------------------

async def flow_cache_and_paths():
    print("\n== the disk-cache hit does not store its ids ==")
    fresh()
    sent_files.ENABLED = False
    dc.LIFE_SECONDS, dc.ENTRY_MAX_MB, dc.TOTAL_MAX_MB = 300, 100, 400
    dc.free_mb = lambda: 10_000
    context = new_context()
    text = f"look at {LINK} please"

    async def paste(user=USER):
        await bot.handle_link(update_with(user=user, text=text), context)
        while W.spawned:
            await W.spawned.pop(0)()

    await paste()
    check("the first delivery (store off): fetched, kept on disk, nothing stored", len(W.resolves) == 1 and DB.rows == {} and dc.stats()["entries"] == 1, (W.resolves, DB.rows))
    sent_files.ENABLED = True
    await paste()
    check("the same link again, store on: no row, so the disk cache answers (no second fetch)", len(W.resolves) == 1 and len(uploads()) == 2, (W.resolves, W.sent))
    # The review's S3: a kept entry is renewed by every copy of the link and can outlive
    # the post, while a stored id is dated "seen public just now"; storing here would let
    # a deleted post be served to everyone for another 12 hours, again and again.
    check("...and that send stored nothing: only a fresh fetch stores", DB.rows == {} and "put" not in DB.names(), DB.rows)
    await paste(user=OTHER)
    check("...so somebody else sending it gets a fresh fetch, which stores", len(W.resolves) == 2 and len(DB.rows) == 1, (W.resolves, DB.rows))
    dc.LIFE_SECONDS = 0
    dc.wipe()

    print("\n== a re-send is a download: the allowance counts it (decision D4) ==")
    fresh()
    await send()
    W.calls.clear()
    context = new_context()

    async def paste_other():
        await bot.handle_link(update_with(user=OTHER, text=f"{LINK}"), context)
        while W.spawned:
            await W.spawned.pop(0)()

    await paste_other()
    check("a link answered by file ids still went through the allowance and was recorded, once",
          W.calls.count("allowance") == 1 and W.calls.count(("record", "instagram")) == 1 and len(by_id()) == 1, (W.calls, W.sent))

    print("\n== the large-file, merge, Reddit-image and card paths never touch the store ==")
    fresh()
    path = SCRATCH / "small.mp4"
    path.write_bytes(b"x" * 100)
    item = resolvers.MediaItem("video", path=str(path), filename="small.mp4")
    await bot._send_large(update_with(), new_context(), [(item, str(path))], "en")
    check("_send_large delivers and makes no database call", len(W.sent) == 1 and DB.calls == [], DB.calls)
    await bot._reply_image_with_button(update_with(), new_context(), b"\xff\xd8jpeg", "reddit", "en", "reddit", "https://www.reddit.com/r/x/comments/1/t/")
    check("a card or a Reddit image makes no database call", DB.calls == [], DB.calls)
    for name in ("_large_download", "_send_large", "_merge_and_send", "merge_callback", "_reply_image", "_reply_image_with_button",
                 "_handle_reddit", "_handle_twitter"):
        src = inspect.getsource(getattr(bot, name))
        check(f"{name} does not mention the store", "sent_files" not in src and "_resend_by_file_id" not in src and "_store_sent_files" not in src)
    check("the merge module does not either", "sent_files" not in inspect.getsource(bot.merge))

    print("\n== the redo button under an id send ==")
    fresh()
    await send()
    ctx = new_context()
    update = update_with(user=OTHER)
    await send(context=ctx, update=update)
    check("by id first", len(by_id()) == 1 and len(W.resolves) == 1)
    token = list(ctx.user_data["redeliver"])[-1]
    answers = []

    async def answer(text=None, **kw):
        answers.append((text, kw))

    async def edit(reply_markup=None):
        return None

    query = SimpleNamespace(data=f"redo:{token}", message=MESSAGE, answer=answer, edit_message_reply_markup=edit)
    await bot.redeliver_callback(update_with(user=OTHER, query=query), ctx)
    while W.spawned:
        await W.spawned.pop(0)()
    check("the tap is taken and the other form is delivered as a document", answers and answers[0][0] is None and W.sent[-1]["how"] == "document", (answers, W.sent[-1]))
    check("...fetched (nothing of the other form was stored or kept), and stored under the other key",
          len(W.resolves) == 2 and key_of(lossless=True) in DB.rows, (W.resolves, list(DB.rows)))
    token2 = list(ctx.user_data["redeliver"])[-1]
    check("...and its own button is for the way back", ctx.user_data["redeliver"][token2]["lossless"] is True)


# ---------------------------------------------------------------------------
# nothing about people
# ---------------------------------------------------------------------------

async def privacy():
    print("\n== the row says nothing about people ==")
    fresh(caption=True)
    PERSON, GROUP = 123456789, 987654321
    update = update_with(user=PERSON, chat=GROUP, username="alice")
    ok, _ = await send(update=update, user=PERSON)
    check("delivered to user 123456789 in chat 987654321 as @alice", ok is True)
    keys = list(DB.rows)
    row = DB.rows[keys[0]]
    shown = " | ".join([keys[0], row["items"], str(row["confirmed_at"]), str(row["last_used"])])
    caption = i18n.t("en", "download_credit_caption", username=bot.BOT_USERNAME)
    for what, needle in (("the user id", str(PERSON)), ("the chat id", str(GROUP)), ("the username", "alice"),
                         ("the link", "instagram.com"), ("the code of the post", "AAAAAAAAAAA"),
                         ("the platform", "instagram"), ("the caption", caption), ("the bot's name", bot.BOT_USERNAME)):
        check(f"no column holds {what}", needle.lower() not in shown.lower(), shown)
    check("the key is 64 hex characters", re.fullmatch(r"[0-9a-f]{64}", keys[0]) is not None, keys[0])
    check("the row has exactly the columns of the table: key, items, a moment, a day", set(row) == {"items", "confirmed_at", "last_used"}
          and type(row["last_used"]) is date, row)
    check("the day is a day, not a moment", not isinstance(row["last_used"], datetime))

    print("\n== the key ==")
    a = sent_files.post_key("instagram", LINK, False)
    check("it differs for the two forms", a != sent_files.post_key("instagram", LINK, True))
    check("...for another post", a != sent_files.post_key("instagram", LINK_B, False))
    check("...for another platform", a != sent_files.post_key("tiktok", LINK, False))
    check("...for another version", a != (setattr(sent_files, "STORE_VERSION", 2), sent_files.post_key("instagram", LINK, False))[1])
    sent_files.STORE_VERSION = 1
    check("it is the same for the same post, however many times it is asked", a == sent_files.post_key("instagram", LINK, False))
    sent_files.configure("123:another-token")
    b = sent_files.post_key("instagram", LINK, False)
    sent_files.configure(TOKEN)
    check("a different token gives a different key: nobody without the token can compute it", a != b)
    import hashlib
    import hmac
    expected = hmac.new(hashlib.sha256(b"dbot-sent-files|" + TOKEN.encode()).digest(),
                        f"1|instagram|{LINK}|media".encode(), hashlib.sha256).hexdigest()
    check("it is the HMAC-SHA256 the spec names: key = sha256('dbot-sent-files|' + token), message = 'version|platform|link|form'", a == expected)
    check("neither the token nor the link is in it", TOKEN not in a and "instagram" not in a)

    fresh()
    await send(user=OTHER)
    ok, _ = await send(user=USER)
    check("another person gets the post a first person's delivery stored (it is filed under the post, not the person)", len(by_id()) == 1 and len(W.resolves) == 1, W.sent)
    fresh()
    await send(user=OTHER, update=update_with(user=OTHER, chat=900))
    ok, _ = await send(user=OTHER, update=update_with(user=OTHER, chat=901))
    check("...and the same person in another chat as well", len(by_id()) == 1 and len(W.resolves) == 1, W.sent)

    print("\n== /deletemydata has nothing to delete here ==")
    check("erase_user does not touch sent_files", "sent_files" not in inspect.getsource(db.erase_user))
    check("nor does the bot's erase handler", "sent_files" not in inspect.getsource(bot._delete_my_data_chosen))


# ---------------------------------------------------------------------------
# resolvers.lookup
# ---------------------------------------------------------------------------

async def lookup_checks():
    print("\n== resolvers.lookup ==")
    real_providers = resolvers.PROVIDERS
    real_ok, real_fail = resolvers._record_ok, resolvers._record_fail
    asked = []
    recorded = []
    resolvers._record_ok = lambda name: recorded.append(("ok", name))
    resolvers._record_fail = lambda name, error: recorded.append(("fail", name))

    def route(name, outcome):
        async def fn(url):
            asked.append(name)
            if callable(outcome):
                return await outcome()
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome
        return (name, fn)

    one = resolvers.Resolved("p", "r", [resolvers.MediaItem("video", url="https://x")])
    empty = resolvers.Resolved("p", "r", [])
    gone = resolvers.ProviderFailed("post is gone", "missing")
    limited = resolvers.ProviderFailed("rate limited", "blocked")
    broken = resolvers.ProviderFailed("500", "error")
    dirty_before = set(resolvers._health_dirty)
    state_before = {n: (h.ok, h.failed, h.streak, h.last_fail) for n, h in resolvers._health.items()}

    async def ask(*routes):
        asked.clear()
        recorded.clear()
        resolvers.PROVIDERS = {"p": list(routes)}
        return await REAL_LOOKUP("p", "https://example.com/post")

    try:
        check("one route returns an item: present", await ask(route("a", one)) == "present")
        check("present as soon as one route does, without asking the rest", await ask(route("a", gone), route("b", one), route("c", one)) == "present" and asked == ["a", "b"], asked)
        check("a route that returns nothing counts as missing", await ask(route("a", empty), route("b", empty)) == "missing")
        check("every route asked said missing: missing", await ask(route("a", gone), route("b", gone)) == "missing" and asked == ["a", "b"], asked)
        check("one route's word is not enough when others were not heard (a timeout)", await ask(route("a", gone), route("b", asyncio.TimeoutError())) == "unknown")
        check("...nor when another route said it is blocked", await ask(route("a", gone), route("b", limited)) == "unknown")
        check("...nor another error", await ask(route("a", gone), route("b", broken)) == "unknown")
        check("...nor when a route raised something of its own", await ask(route("a", gone), route("b", ValueError("bug"))) == "unknown")
        check("a single route that says missing is missing", await ask(route("a", gone)) == "missing")
        check("all of them failing in other ways: unknown", await ask(route("a", broken), route("b", limited)) == "unknown")
        check("no route at all: unknown", await ask() == "unknown")
        check("a route that skips another is honoured (the skipped one is not asked)",
              await ask(route("a", resolvers.ProviderFailed("video pin", "missing", skip=("b",))), route("b", one), route("c", gone)) == "missing" and asked == ["a", "c"], asked)

        print("\n== ...never asks yt-dlp ==")
        verdict = await ask(route("ytdlp:p", one), route("a", gone))
        check("a yt-dlp route is skipped even when it would say present", verdict == "missing" and asked == ["a"], (verdict, asked))
        check("only yt-dlp routes (Reddit): unknown, and none asked", await ask(route("ytdlp:reddit", one)) == "unknown" and asked == [], asked)
        resolvers.PROVIDERS = real_providers
        check("the real Reddit chain has nothing but yt-dlp, so it is unknown", [n for n, _ in resolvers.PROVIDERS["reddit"]] == ["ytdlp:reddit"]
              and await REAL_LOOKUP("reddit", "https://www.reddit.com/r/x/comments/1/t/") == "unknown")
        check("every other platform has a route that is not yt-dlp to ask",
              all(any(not n.startswith("ytdlp:") for n, _ in chain) for p, chain in resolvers.PROVIDERS.items() if p != "reddit"))

        print("\n== ...never touches route health ==")
        await ask(route("a", gone), route("b", broken), route("c", asyncio.TimeoutError()))
        await ask(route("a", one))
        check("no route is recorded as having worked or failed", recorded == [], recorded)
        check("nothing is marked for saving, and no score moved", resolvers._health_dirty == dirty_before
              and {n: (h.ok, h.failed, h.streak, h.last_fail) for n, h in resolvers._health.items() if n in state_before} == state_before)

        print("\n== ...and gives a slow route its usual time, no more ==")
        real_timeout = resolvers.PROVIDER_TIMEOUT_S

        async def hang():
            await asyncio.sleep(30)

        resolvers.PROVIDER_TIMEOUT_S = 0.05
        try:
            started = asyncio.get_running_loop().time()
            verdict = await ask(route("slow", hang), route("a", gone))
            took = asyncio.get_running_loop().time() - started
        finally:
            resolvers.PROVIDER_TIMEOUT_S = real_timeout
        check("a route that never answers is cut off at its limit, and is not a 'missing'", verdict == "unknown" and took < 5, (verdict, took))
        src = inspect.getsource(REAL_LOOKUP)
        check("it walks the chain in the usual order and under the usual limits", "_ordered(" in src and "_timeout_for(" in src and "wait_for" in src)
        check("it does not go through _resolve, nor the recorders", "_resolve(" not in src and "_record_" not in src and "_cooldown" not in src)
    finally:
        resolvers.PROVIDERS = real_providers
        resolvers._record_ok, resolvers._record_fail = real_ok, real_fail


# ---------------------------------------------------------------------------
# the small pieces
# ---------------------------------------------------------------------------

def small_pieces():
    print("\n== settings ==")
    saved = {k: os.environ.get(k) for k in ("DBOT_SENT_FILES", "DBOT_FILEID_CONFIRM_HOURS", "DBOT_FILEID_KEEP_DAYS")}

    def read(**env):
        for k in saved:
            os.environ.pop(k, None)
        os.environ.update({k: str(v) for k, v in env.items()})
        return sent_files.read_settings()

    try:
        check("nothing set: on, 12 hours", read() == (True, 12))
        check("DBOT_SENT_FILES=1: on", read(DBOT_SENT_FILES=1)[0] is True)
        check("DBOT_SENT_FILES=0: off", read(DBOT_SENT_FILES=0)[0] is False)
        check("an unreadable value is off, like DBOT_CACHE_SECONDS", read(DBOT_SENT_FILES="off")[0] is False and read(DBOT_SENT_FILES="yes")[0] is False)
        check("a negative one is off", read(DBOT_SENT_FILES=-1)[0] is False)
        check("an empty one is the default (on)", read(DBOT_SENT_FILES="")[0] is True)
        check("hours: 6 is 6", read(DBOT_FILEID_CONFIRM_HOURS=6)[1] == 6)
        check("hours: 1 is 1", read(DBOT_FILEID_CONFIRM_HOURS=1)[1] == 1)
        check("hours: under 1 is 12", read(DBOT_FILEID_CONFIRM_HOURS=0)[1] == 12 and read(DBOT_FILEID_CONFIRM_HOURS=-4)[1] == 12)
        check("hours: unreadable is 12", read(DBOT_FILEID_CONFIRM_HOURS="soon")[1] == 12 and read(DBOT_FILEID_CONFIRM_HOURS="1.5")[1] == 12)
        check("a typo in one does not change the other", read(DBOT_SENT_FILES="x", DBOT_FILEID_CONFIRM_HOURS=3) == (False, 3))
        check("the module read the defaults when it was imported", (sent_files.STORE_VERSION, sent_files.MAX_ITEMS) == (1, 10))
        check("days: nothing set is 90", db._days_from_env("DBOT_FILEID_KEEP_DAYS", 90) == 90)
        os.environ["DBOT_FILEID_KEEP_DAYS"] = "30"
        check("days: 30 is 30", db._days_from_env("DBOT_FILEID_KEEP_DAYS", 90) == 30)
        for bad in ("0", "-5", "many", "1.5", ""):
            os.environ["DBOT_FILEID_KEEP_DAYS"] = bad
            check(f"days: {bad!r} is 90", db._days_from_env("DBOT_FILEID_KEEP_DAYS", 90) == 90)
        check("the sweep's constant is 90 by default", db.SENT_FILES_KEEP_DAYS == 90)
    finally:
        for k, v in saved.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v

    print("\n== the row's JSON ==")
    good = [("video", "AAA"), ("photo", "BBB")]
    text = sent_files.encode_items(good)
    check("round trip", sent_files.decode_items(text) == good and sent_files.decode_items(sent_files.encode_items(good[:1])) == good[:1])
    check("ten items are fine", sent_files.encode_items([("photo", f"x{n}") for n in range(10)]) is not None)
    check("eleven are not", sent_files.encode_items([("photo", f"x{n}") for n in range(11)]) is None)
    for name, bad in (("none", []), ("not a list", "x"), ("a kind that is not video or photo", [("audio", "x")]), ("a kind that is a document", [("document", "x")]),
                      ("an empty id", [("video", "")]), ("an id that is a number", [("video", 5)]), ("a triple", [("video", "x", "y")]),
                      ("a single", [("video",)]), ("a dict", [{"kind": "video"}]), ("None", None)):
        check(f"encode refuses {name}", sent_files.encode_items(bad) is None)
    for name, bad in (("garbage", "not json"), ("an empty list", "[]"), ("an object", "{}"), ("None", None), ("a number", "5"),
                      ("a bad kind", '[["gif","x"]]'), ("an empty id", '[["video",""]]'), ("11 items", "[" + ",".join(['["photo","x"]'] * 11) + "]")):
        check(f"decode treats {name} as absent", sent_files.decode_items(bad) is None)
    check("the stored files carry kind and id", sent_files.stored_files(good)[0].kind == "video" and sent_files.stored_files(good)[1].file_id == "BBB"
          and all(getattr(f, "filename", None) for f in sent_files.stored_files(good)))

    print("\n== freshness ==")
    n = START
    check("just confirmed is fresh", sent_files.is_fresh(n, n))
    check("11:59:59 old is fresh", sent_files.is_fresh(n - timedelta(hours=11, minutes=59, seconds=59), n))
    check("exactly 12 hours old is not", not sent_files.is_fresh(n - timedelta(hours=12), n))
    check("a day old is not", not sent_files.is_fresh(n - timedelta(days=1), n))
    check("a minute in the future (the clocks disagree) is fresh", sent_files.is_fresh(n + timedelta(minutes=1), n))
    check("an hour in the future is not (a wrong clock must not trust a post for ever)", not sent_files.is_fresh(n + timedelta(hours=1), n))
    check("a time with no zone is read as UTC", sent_files.is_fresh(datetime(2026, 10, 17, 11, 0), n) and not sent_files.is_fresh(datetime(2026, 10, 16, 11, 0), n))
    for junk in (None, "2026-10-17", 5, 0.0, date(2026, 10, 17)):
        check(f"{junk!r} is not fresh", not sent_files.is_fresh(junk, n))
    sent_files.CONFIRM_HOURS = 1
    check("the window is the setting", sent_files.is_fresh(n - timedelta(minutes=59), n) and not sent_files.is_fresh(n - timedelta(minutes=61), n))
    sent_files.CONFIRM_HOURS = 12

    print("\n== which errors are an invalid file id ==")
    for text in ("Bad Request: wrong file identifier/HTTP URL specified", "bad request: WRONG REMOTE FILE IDENTIFIER specified",
                 "Bad Request: invalid file_id", "Bad Request: type of file mismatch", "Bad Request: file reference expired",
                 "Bad Request: MEDIA_EMPTY", "Bad Request: failed to get HTTP URL content (file_id)"):
        check(f"BadRequest {text[:44]!r}: invalid", sent_files.is_invalid_file_id(BadRequest(text)))
    for exc in (BadRequest("Bad Request: chat not found"), BadRequest("Bad Request: message is too long"), BadRequest("Bad Request: reply message not found"),
                BadRequest("Bad Request: not enough rights to send photos"), BadRequest(""), Forbidden("Forbidden: bot was blocked by the user"),
                Forbidden("wrong file identifier"), RetryAfter(3), TimedOut("Timed out"), TimeoutError("wrong file identifier"),
                RuntimeError("wrong file identifier"), OSError("file_id"), KeyError("MEDIA_EMPTY"), ConnectionError("x")):
        check(f"{type(exc).__name__} {str(exc)[:36]!r}: an ordinary failure", not sent_files.is_invalid_file_id(exc))

    print("\n== /status ==")
    fresh()
    off = bot._sent_files_status_line()
    check("on, nothing yet", off == "Sent-file store: rows read 0 fresh / 0 stale / 0 none; lookups 0 present / 0 missing / 0 unknown; 0 invalid ids, 0 stored since start", off)
    for name in ("hit", "hit", "stale", "miss", "miss", "miss", "present", "missing", "unknown", "unknown", "invalid", "stored"):
        sent_files.note(name)
    line = bot._sent_files_status_line()
    check("the counts, in words", line == "Sent-file store: rows read 2 fresh / 1 stale / 3 none; lookups 1 present / 1 missing / 2 unknown; 1 invalid ids, 1 stored since start", line)
    sent_files.ENABLED = False
    check("off says so", bot._sent_files_status_line() == "Sent-file store: off")
    sent_files.ENABLED = True
    check("it holds counts and nothing else", not re.search(r"https?:|instagram|[0-9a-f]{32}|FID", line))
    src = inspect.getsource(bot.status_command)
    check("it follows the cache line, after the admin check", src.index("_is_admin") < src.index("_cache_status_line") < src.index("_sent_files_status_line"))
    fresh()
    check("a reset starts the counts again", sent_files.stats()["hit"] == 0 and sent_files.stats()["stored"] == 0)


def wiring():
    print("\n== the order of the calls, from the source ==")
    src = inspect.getsource(bot._resolve_and_send)
    check("the store is asked after the maintenance refusal and after the status message exists, before the queue note and the slots",
          src.index("refuse_new_work") < src.index("LiveMessage.reply_to") < src.index("_resend_by_file_id")
          < src.index("_slots().locked()") < src.index("async with _user_slot("))
    check("...and before the disk cache, the disk check and any route", src.index("_resend_by_file_id") < src.index("delivery_cache.take") < src.index("_free_mb()") < src.index("resolvers.resolve"))
    check("a re-send ends the way the cache-hit path does: the after-send, then the answer",
          re.search(r"if resent is not None:\s+if resent:\s+await _after_send\(update, context, lang\)\s+return resent", src) is not None)
    fetch = src.index("_send_media(update, context, list(zip")
    check("bytes are stored after the send, the status clean-up and the job count, after a fresh fetch",
          src.index("note_job(True)", fetch) < src.index("_store_sent_files(", fetch))
    check("...in one place only: never after the disk-cache hit", src.count("_store_sent_files(") == 1
          and "_store_sent_files(" not in src[src.index("if cached is not None:"):src.index("resolvers.resolve(")])
    check("the ids come from the send: sent = await _send_media", src.count("sent = await _send_media(") == 1)
    check("a stale row's lookup waits for one of a few lookup places",
          re.search(r"async with _lookups\(\):\s+verdict = await resolvers\.lookup\(", inspect.getsource(bot._resend_by_file_id)) is not None
          and bot.MAX_CONCURRENT_LOOKUPS == 2)
    send_src = inspect.getsource(bot._resend_by_file_id)
    check("a re-send by id uses the same _send_media, the status clean-up and the job count as the cache hit",
          "_send_media(" in send_src and "status.delete" in send_src and "note_job(True)" in send_src and "_remember_delivery(" in send_src)
    check("it never takes a slot, and never reads the disk", "_slots()" not in send_src and "_user_slot" not in send_src and "_free_mb" not in send_src
          and "delivery_cache" not in send_src and "resolvers.resolve" not in send_src and "resolvers.download" not in send_src)
    check("the lookup happens before the send, only for a row that is not fresh", send_src.index("resolvers.lookup") < send_src.index("_send_media(") and "if not fresh" in send_src)
    check("it asks usable() first: a shared sender never reads or writes", send_src.index("sent_files.usable") < send_src.index("sent_files_get")
          and "usable" in inspect.getsource(bot._store_sent_files))
    check("a fresh row only touches the day: sent_files_used for fresh, sent_files_confirmed for a lookup", "sent_files_used if fresh else sent_files_confirmed" in send_src)
    check("logs name the kind of failure only", all(re.search(r"type\(exc\)\.__name__\)", line) for line in re.findall(r"logger\.\w+\([^\n]*\n?[^\n]*", send_src + inspect.getsource(bot._store_sent_files)
                                                                                              + inspect.getsource(bot._forget_sent_files)) if "exc" in line) and "exc_info" not in send_src)
    check("a stored id travels as a StoredFile in place of a path", "stored_files(" in send_src and "(file, None)" in send_src)
    check("only the two stores read and write the table", all(name in inspect.getsource(bot) for name in ("sent_files_get", "sent_files_put", "sent_files_confirmed", "sent_files_used", "sent_files_drop")))
    check("the key is configured from the bot's own token at import", "sent_files.configure(BOT_TOKEN)" in inspect.getsource(bot))
    dbsrc = inspect.getsource(db)
    check("the table is created by a literal CREATE TABLE IF NOT EXISTS, with the four columns",
          re.search(r"CREATE TABLE IF NOT EXISTS sent_files \(\s+post_key\s+TEXT PRIMARY KEY,\s+items\s+TEXT NOT NULL,\s+confirmed_at TIMESTAMPTZ NOT NULL,\s+last_used\s+DATE NOT NULL DEFAULT CURRENT_DATE", dbsrc) is not None)
    check("the sweep deletes rows unused for the keep days",
          "DELETE FROM sent_files WHERE last_used < CURRENT_DATE - make_interval(days => %s)" in inspect.getsource(db.prune_old_data)
          and "SENT_FILES_KEEP_DAYS" in inspect.getsource(db.prune_old_data))
    check("and nothing about a person is in the table's definition", not re.search(r"user|chat|name|url|platform|caption", re.search(r"CREATE TABLE IF NOT EXISTS sent_files \(.*?\)\n", dbsrc, re.S).group(0), re.I))


def main():
    for fn in (flow_miss_then_hit, flow_stale, flow_invalid_ids, flow_other_failures, flow_forms, flow_albums,
               flow_off_and_errors, flow_cache_and_paths, privacy, lookup_checks):
        run(fn())
    small_pieces()
    wiring()

    shutil.rmtree(SCRATCH, ignore_errors=True)
    print()
    if failures:
        print(f"FAILED ({len(failures)} of {total}):")
        for name in failures:
            print("  -", name)
        return 1
    print(f"all {total} checks passed: a post sent before is sent again by its ids, and the table knows nobody.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
