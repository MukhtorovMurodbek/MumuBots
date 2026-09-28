"""Files over the Bot API's limits: in over MTProto, out over MTProto, each in
a process of its own, and the bot unchanged when that route is missing or down.

    python3 tests/test_big_files.py

The cloud Bot API lets a bot download 20 MB and send 50. ConvertBot moves
anything bigger over MTProto (big_files.py), one child process per transfer so
the bot never carries Telethon's 27 MB. The parts that have to be right every
time are the edges:

  a file of exactly 20 MB still takes the Bot API, and one byte more does not;
  the ceiling is Telegram's own 2000 MB unless CONVERT_MAX_FILE_MB lowers it,
  and past it a file is refused with the bot's own sentence;
  a download keeps four 512 KB requests in flight, and an upload goes up in
  512 KB parts -- one 128 KB part per round trip was half a megabyte a second
  from the owner's laptop;
  a group gets the private-chat sentence, never an MTProto attempt;
  without CBOT_API_ID the bot is the bot it was -- 20 MB, Telegram's sentence;
  the route being down refuses the file with a sentence and keeps nothing;
  one failed connection is not retried for every file that follows;
  the login is made once and kept in a private file, never in the database;
  the fetched message is checked to be the one announced;
  a transfer that stops moving is stopped after STALL_S, and one that creeps
  at the limit its size allows -- each said as such;
  an album over the ceiling is refused before the rest of it is downloaded;
  a result is sent over MTProto only past 50 MB, and never past what its
  price covers;
  an API id and hash that are set but wrong are found out as the bot starts,
  and the bot then takes 20 MB, as if they were not set -- while a network
  that does not answer turns nothing off;
  and the bot's own process never imports Telethon.

No network and no Telethon: the children import a fake `telethon` package
written below, which keeps "Telegram" in a JSON file this test reads.
"""
import asyncio
import json
import os
import pathlib
import subprocess
import sys
import tempfile
from types import SimpleNamespace

HERE = pathlib.Path(__file__).resolve().parent
MIRROR = HERE / ".mirror" / "convert_bot"
SCRATCH = pathlib.Path(tempfile.mkdtemp(prefix="convertbot-big-"))
STAGE = SCRATCH / "stage"
STAGE.mkdir()
FAKE_SITE = SCRATCH / "fake_site"
STATE = SCRATCH / "telegram.json"

sys.path.insert(0, str(MIRROR))
sys.path.insert(0, str(HERE / "stubs"))
os.environ.pop("LOCAL_BOT_API_URL", None)
os.environ.pop("CONVERT_MAX_FILE_MB", None)
os.environ["CBOT_TOKEN"] = "123:test"
os.environ.setdefault("CBOT_USERNAME", "test")
os.environ.setdefault("DATABASE_URL", "postgresql://stub")
os.environ["CBOT_API_ID"] = "12345"
os.environ["CBOT_API_HASH"] = "0123456789abcdef"
os.environ["CONVERT_UPLOAD_DIR"] = str(STAGE)
# The session file goes in the temporary directory; this run gets its own.
for variable in ("TMP", "TEMP", "TMPDIR"):
    os.environ[variable] = str(SCRATCH)
tempfile.tempdir = None
os.environ["FAKE_TG_STATE"] = str(STATE)
os.environ["BIG_FILES_STALL_SECONDS"] = "1.5"
os.environ["PYTHONPATH"] = os.pathsep.join([str(FAKE_SITE), str(HERE / "stubs")])

MB = 1024 * 1024
failures = []


def check(name, ok, detail=""):
    print(("  ok  " if ok else "  FAIL  ") + name + ("" if ok else f"  -- {detail}"))
    if not ok:
        failures.append(name)


# ---------------------------------------------------------------------------
# A Telethon that talks to a JSON file
# ---------------------------------------------------------------------------

FAKE_TELETHON = r'''
import asyncio, json, os
from types import SimpleNamespace as NS

STATE = os.environ["FAKE_TG_STATE"]


def load():
    with open(STATE) as handle:
        return json.load(handle)


def save(state):
    with open(STATE, "w") as handle:
        json.dump(state, handle)


class ApiIdInvalidError(Exception):
    pass


class TelegramClient:
    def __init__(self, session, api_id, api_hash, **kwargs):
        self.session, self.connected = session, False
        state = load()
        state["client_kwargs"] = kwargs
        state["api_id"], state["api_hash"] = api_id, api_hash
        save(state)

    async def connect(self):
        state = load()
        state["connects"] += 1
        save(state)
        if state.get("noisy"):
            import sys
            sys.stderr.write("x" * 300000)   # one long line, no newline
            sys.stderr.flush()
        if state["fail_connect"]:
            raise ConnectionError("Telegram is not answering")
        self.connected = True

    def is_connected(self):
        return self.connected

    async def disconnect(self):
        self.connected = False

    async def is_user_authorized(self):
        return bool(self.session.key)

    async def sign_in(self, bot_token=None):
        state = load()
        if state.get("refuse_login"):
            raise ApiIdInvalidError("The api_id/api_hash combination is invalid "
                                    "(caused by ImportBotAuthorizationRequest)")
        state["logins"] += 1
        state["login_tokens"].append(bot_token)
        save(state)
        self.session.key = "fake-auth-key"

    async def get_me(self):
        return NS(id=123, bot=True)

    async def get_messages(self, entity, ids=None):
        found = load()["messages"].get(str(ids))
        if not found:
            return None
        chat_id, size = found
        return NS(id=ids, peer_id=NS(user_id=chat_id), media=NS(document=NS(size=size)))

    async def iter_download(self, media, offset=0, stride=None, request_size=None, file_size=None, **kwargs):
        state = load()
        state.setdefault("iter_calls", []).append([offset, stride, request_size])
        save(state)
        if state["hang"]:
            await asyncio.sleep(60)
        size = media.document.size - state["short_by"]
        position = offset
        while position < size:
            if state.get("creep"):
                await asyncio.sleep(0.2)
            yield b"\0" * min(request_size, size - position)
            position += stride or request_size
            await asyncio.sleep(0)

    async def upload_file(self, file, part_size_kb=None, file_name=None, progress_callback=None, **kwargs):
        state = load()
        state["upload_part_kb"] = part_size_kb
        save(state)
        size = os.path.getsize(file)
        if progress_callback is not None:
            progress_callback(size, size)
        return file

    async def download_media(self, message, file=None, progress_callback=None):
        state = load()
        if state["hang"]:
            await asyncio.sleep(60)
        size = message.media.document.size - state["short_by"]
        file.write(b"\0" * size)
        for done in (size // 2, size):
            progress_callback(done, size)
        return file

    async def send_file(self, entity, file=None, **kwargs):
        state = load()
        state["sent"].append({"user_id": entity.user_id, "file": file, "caption": kwargs.get("caption"),
                              "force_document": kwargs.get("force_document"),
                              "plain": "parse_mode" in kwargs and kwargs["parse_mode"] is None,
                              "filename": kwargs["attributes"][0].file_name})
        save(state)
'''
FAKE_SESSIONS = '''
class StringSession:
    def __init__(self, string=None):
        self.key = string or ""

    def save(self):
        return self.key
'''
FAKE_TYPES = '''
class PeerUser:
    def __init__(self, user_id):
        self.user_id = user_id


class DocumentAttributeFilename:
    def __init__(self, file_name):
        self.file_name = file_name
'''
(FAKE_SITE / "telethon").mkdir(parents=True)
(FAKE_SITE / "telethon" / "__init__.py").write_text(FAKE_TELETHON)
(FAKE_SITE / "telethon" / "sessions.py").write_text(FAKE_SESSIONS)
(FAKE_SITE / "telethon" / "types.py").write_text(FAKE_TYPES)
sys.path.append(str(FAKE_SITE))   # so the parent finds it installed -- and must never import it


def reset_telegram(**changes):
    state = {"messages": {}, "fail_connect": False, "hang": False, "creep": False, "short_by": 0,
             "connects": 0, "logins": 0, "login_tokens": [], "sent": [], "client_kwargs": {}}
    state.update(changes)
    STATE.write_text(json.dumps(state))


def telegram():
    return json.loads(STATE.read_text())


def announce(message_id, chat_id, size):
    state = telegram()
    state["messages"][str(message_id)] = [chat_id, size]
    STATE.write_text(json.dumps(state))


reset_telegram()

import big_files        # noqa: E402
import bot              # noqa: E402
import convert_runner   # noqa: E402
import convert_utils    # noqa: E402
import i18n             # noqa: E402
import jobs             # noqa: E402
import problems         # noqa: E402

PRIVATE, GROUP = 7, -100123


# ---------------------------------------------------------------------------
# The receiving handler, driven the way test_batching.py drives it
# ---------------------------------------------------------------------------

class FakeFile:
    def __init__(self, size):
        self.size = size

    async def download_to_drive(self, custom_path):
        pathlib.Path(custom_path).write_bytes(b"\0" * self.size)


class FakeBot:
    def __init__(self):
        self.bot_api_downloads = []
        self.documents = []

    async def get_file(self, file_id):
        self.bot_api_downloads.append(file_id)
        return FakeFile(int(file_id.split(":")[1]))

    async def send_document(self, **kwargs):
        self.documents.append(kwargs)

    async def delete_message(self, *a, **k):
        return True


class FakeMessage:
    def __init__(self, chat_id, message_id, size, group=None, ext="mp4"):
        self.chat_id, self.message_id = chat_id, message_id
        self.document = SimpleNamespace(file_id=f"f:{size}", file_size=size, file_name=f"clip.{ext}")
        self.photo = self.video = self.audio = self.voice = self.animation = None
        self.media_group_id = group
        self.replies = []

    async def reply_text(self, text, **kwargs):
        self.replies.append(text)
        return FakeMessage(self.chat_id, self.message_id + 1000, 0)


class Status:
    """LiveMessage, minus the Telegram it would edit."""
    shown = []

    @classmethod
    async def reply_to(cls, message, text, **kwargs):
        cls.shown.append(text)
        return cls()

    async def set(self, bot_, text, **kwargs):
        Status.shown.append(text)
        return self

    async def delete(self, *a, **k):
        return None


class FakeJobQueue:
    def __init__(self):
        self.jobs = []

    def get_jobs_by_name(self, name):
        return [job for job in self.jobs if job.name == name and not job.removed]

    def run_once(self, callback, when, name=None, data=None, **kwargs):
        job = SimpleNamespace(callback=callback, name=name, data=data, removed=False)
        job.schedule_removal = lambda job=job: setattr(job, "removed", True)
        self.jobs.append(job)
        return job


def context():
    return SimpleNamespace(bot=FakeBot(), user_data={"lang": "en"},
                           job_queue=FakeJobQueue(), job=None)


def staged():
    return sorted(path.name for path in STAGE.iterdir() if path.is_file())


def clear_stage():
    for path in STAGE.iterdir():
        path.unlink()


async def send(ctx, chat_id, size, message_id=1, group=None, announced=True, ext="mp4"):
    """A file arrives. `announced` puts it where MTProto will find it."""
    message = FakeMessage(chat_id, message_id, size, group, ext)
    if announced:
        announce(message_id, chat_id, size)
    update = SimpleNamespace(message=message, effective_user=SimpleNamespace(id=4242, username="t"))
    await bot.convert_receive_file(update, ctx)
    return message


def said(message, key, **fields):
    return bool(message.replies) and message.replies[0] == i18n.t("en", key, **fields)


def fresh_route(**changes):
    big_files._resting_until = 0.0
    if big_files.SESSION_FILE.exists():
        big_files.SESSION_FILE.unlink()
    reset_telegram(**changes)


async def run():
    async def fixed_lang(user_id, context=None):
        return "en"
    i18n.get_lang = fixed_lang
    bot.LiveMessage = Status
    bot._PROGRESS_EVERY_S = 0

    print("=== the limits this bot runs with ===")
    check("CBOT_API_ID and CBOT_API_HASH switch the route on", big_files.CONFIGURED)
    check("and the ceiling is then Telegram's own, 2000 MB", convert_utils.MAX_FILE_MB == 2000,
          convert_utils.MAX_FILE_MB)
    check("in a private chat", convert_utils.max_file_mb_in(PRIVATE) == 2000)
    check("but 20 MB in a group", convert_utils.max_file_mb_in(GROUP) == 20)
    check("a file of any size may take ten minutes to move, and 2000 MB over two hours, if it keeps moving",
          big_files.transfer_limit_s(40 * MB) == 600 and big_files.transfer_limit_s(2000 * MB) == 8000,
          (big_files.transfer_limit_s(40 * MB), big_files.transfer_limit_s(2000 * MB)))
    # The edges below at a ceiling of 100 MB, as CONVERT_MAX_FILE_MB=100 would
    # set it -- at 2000 MB the test would write two gigabytes to prove them.
    convert_utils.MAX_FILE_MB = 100

    print("\n=== around 20 MB, in a private chat ===")
    fresh_route()
    ctx = context()
    message = await send(ctx, PRIVATE, 20 * MB, message_id=11)
    check("exactly 20 MB still comes through the Bot API", ctx.bot.bot_api_downloads == [f"f:{20 * MB}"],
          ctx.bot.bot_api_downloads)
    check("with no MTProto connection made for it", telegram()["connects"] == 0, telegram()["connects"])
    check("and reaches the format menu", ctx.user_data.get("convert") is not None, message.replies)
    bot._discard_pending(ctx)

    ctx = context()
    Status.shown = []
    message = await send(ctx, PRIVATE, 20 * MB + 1, message_id=12)
    state = telegram()
    check("one byte more goes over MTProto", not ctx.bot.bot_api_downloads and state["connects"] == 1,
          (ctx.bot.bot_api_downloads, state["connects"]))
    check("logged in with this bot's own token", state["login_tokens"] == [os.environ["CBOT_TOKEN"]],
          state["login_tokens"])
    check("with the API id it was given", state["api_id"] == 12345 and state["api_hash"] == "0123456789abcdef")
    check("a session that never receives updates", state["client_kwargs"].get("receive_updates") is False,
          state["client_kwargs"])
    check("the login is kept in the temporary directory, for the next transfer",
          big_files.SESSION_FILE.is_file() and big_files.SESSION_FILE.parent == SCRATCH,
          big_files.SESSION_FILE)
    if os.name == "posix":
        check("readable by the bot's user alone", (big_files.SESSION_FILE.stat().st_mode & 0o077) == 0)
    state_after = ctx.user_data.get("convert")
    check("and the whole file is there for the menu",
          state_after is not None and state_after["size"] == 20 * MB + 1, state_after and state_after["size"])
    progress = [text for text in Status.shown
                if text.startswith(i18n.t("en", "card_downloading")) and "%" in text]
    check("the download showed its progress on the card", len(progress) >= 1, Status.shown)
    calls = state.get("iter_calls") or []
    check("four requests in flight at once, each for a 512 KB part, each taking every fourth part",
          sorted(call[0] for call in calls) == [0, 512 * 1024, 1024 * 1024, 1536 * 1024]
          and all(call[1:] == [4 * 512 * 1024, 512 * 1024] for call in calls), calls)
    check("and never claimed 100% before it was finished",
          not any("100%" in text for text in Status.shown), Status.shown)
    bot._discard_pending(ctx)

    ctx = context()
    await send(ctx, PRIVATE, 30 * MB, message_id=13)
    state = telegram()
    check("a second big file reuses the login rather than logging in again",
          state["connects"] == 2 and state["logins"] == 1, (state["connects"], state["logins"]))
    bot._discard_pending(ctx)

    fresh_route(noisy=True)
    ctx = context()
    began = asyncio.get_running_loop().time()
    await send(ctx, PRIVATE, 30 * MB, message_id=16)
    took = asyncio.get_running_loop().time() - began
    check("a transfer whose library floods stderr with one huge line still finishes, promptly",
          ctx.user_data.get("convert") is not None and took < 20, f"{took:.1f}s")
    bot._discard_pending(ctx)

    print("\n=== at the ceiling ===")
    ctx = context()
    message = await send(ctx, PRIVATE, 100 * MB, message_id=14)
    check("exactly 100 MB is taken", ctx.user_data.get("convert") is not None, message.replies)
    bot._discard_pending(ctx)
    ctx = context()
    connects = telegram()["connects"]
    message = await send(ctx, PRIVATE, 100 * MB + 1, message_id=15)
    check("one byte more is refused with the bot's own limit, not Telegram's",
          said(message, "file_too_large_convert", size=f"{(100 * MB + 1) / MB:.1f}", max_mb=100),
          message.replies)
    check("which carries CV-TOO-BIG", message.replies and message.replies[0].endswith("CV-TOO-BIG"))
    check("and nothing was downloaded", not staged() and telegram()["connects"] == connects, staged())

    print("\n=== in a group ===")
    ctx = context()
    message = await send(ctx, GROUP, 20 * MB, message_id=21)
    check("20 MB in a group still comes through the Bot API",
          ctx.bot.bot_api_downloads == [f"f:{20 * MB}"], ctx.bot.bot_api_downloads)
    bot._discard_pending(ctx)
    ctx = context()
    connects = telegram()["connects"]
    message = await send(ctx, GROUP, 20 * MB + 1, message_id=22)
    check("one byte more is sent to a private chat, by name",
          said(message, "file_too_large_in_group", limit=20, max_mb=100), message.replies)
    check("which carries CV-GROUP-LIMIT and no Report button",
          message.replies and message.replies[0].endswith("CV-GROUP-LIMIT")
          and "CV-GROUP-LIMIT" in problems.SIMPLE, message.replies)
    check("while the route being down does offer one", "CV-BIG-UNAVAILABLE" not in problems.SIMPLE)
    check("without an MTProto attempt", telegram()["connects"] == connects and not staged(), telegram()["connects"])

    print("\n=== the route is down ===")
    fresh_route(fail_connect=True)
    ctx = context()
    Status.shown = []
    await send(ctx, PRIVATE, 40 * MB, message_id=31)
    expected = i18n.t("en", "big_download_unavailable", limit=20)
    check("the file is refused with the route's own sentence", Status.shown[-1:] == [expected],
          Status.shown[-1:])
    check("which carries CV-BIG-UNAVAILABLE", expected.endswith("CV-BIG-UNAVAILABLE"))
    check("nothing is kept", not staged() and ctx.user_data.get("convert") is None, staged())
    ctx = context()
    Status.shown = []
    await send(ctx, PRIVATE, 40 * MB, message_id=32)
    check("the next big file is refused at once, not after another timeout", telegram()["connects"] == 1,
          telegram()["connects"])
    ctx = context()
    message = await send(ctx, PRIVATE, 5 * MB, message_id=33)
    check("while files under 20 MB carry on as before",
          ctx.user_data.get("convert") is not None, message.replies)
    bot._discard_pending(ctx)

    print("\n=== the message is not what was announced ===")
    fresh_route()
    ctx = context()
    Status.shown = []
    await send(ctx, PRIVATE, 40 * MB, message_id=41, announced=False)
    check("a message that is gone is a download failure, not a crash",
          Status.shown and Status.shown[-1].startswith("Couldn't download that file")
          and Status.shown[-1].endswith("CV-DOWNLOAD"), Status.shown[-1:])
    check("and leaves nothing behind", not staged(), staged())

    async def outcome_of(message_id, chat_id, expected):
        try:
            await big_files.download(chat_id, message_id, STAGE / "x", expected)
            return "downloaded"
        except big_files.NotThere:
            return "refused"
        finally:
            clear_stage()

    announce(42, PRIVATE + 1, 40 * MB)
    check("the same id in somebody else's chat is not fetched",
          await outcome_of(42, PRIVATE, 40 * MB) == "refused")
    announce(43, PRIVATE, 41 * MB)
    check("nor a file of a different size than announced", await outcome_of(43, PRIVATE, 40 * MB) == "refused")
    announce(44, PRIVATE, 40 * MB)
    reset_state = telegram()
    reset_state["short_by"] = 1
    STATE.write_text(json.dumps(reset_state))
    check("nor a download that stopped short", await outcome_of(44, PRIVATE, 40 * MB) == "refused")

    print("\n=== a transfer that stops moving ===")
    fresh_route(hang=True)
    ctx = context()
    Status.shown = []
    began = asyncio.get_running_loop().time()
    await send(ctx, PRIVATE, 40 * MB, message_id=51)
    took = asyncio.get_running_loop().time() - began
    check("is stopped once nothing has moved for STALL_S, not at its time limit", took < 15, f"{took:.1f}s")
    check("and said as such",
          Status.shown and i18n.t("en", "download_stalled", seconds=2) in Status.shown[-1], Status.shown[-1:])
    check("its process is gone and its file removed", not big_files._children and not staged(),
          (big_files._children, staged()))

    print("\n=== a transfer that creeps ===")
    fresh_route(creep=True)
    limit, rate = big_files.TRANSFER_TIME_LIMIT_S, big_files.MIN_RATE_BYTES_S
    big_files.TRANSFER_TIME_LIMIT_S, big_files.MIN_RATE_BYTES_S = 1, 10 ** 12
    ctx = context()
    Status.shown = []
    began = asyncio.get_running_loop().time()
    await send(ctx, PRIVATE, 40 * MB, message_id=52)
    took = asyncio.get_running_loop().time() - began
    big_files.TRANSFER_TIME_LIMIT_S, big_files.MIN_RATE_BYTES_S = limit, rate
    check("still moving, it is stopped at the limit its size allows", took < 15, f"{took:.1f}s")
    check("and that is said too, differently",
          Status.shown and i18n.t("en", "download_too_slow", minutes=0) in Status.shown[-1], Status.shown[-1:])
    check("and it too leaves nothing behind", not big_files._children and not staged(),
          (big_files._children, staged()))

    print("\n=== an album over the ceiling ===")
    fresh_route()
    ctx = context()
    first = await send(ctx, PRIVATE, 60 * MB, message_id=61, group="A")
    second = await send(ctx, PRIVATE, 60 * MB, message_id=62, group="A")
    third = await send(ctx, PRIVATE, 60 * MB, message_id=63, group="A")
    check("only the first file is downloaded", len(staged()) == 1, staged())
    for job in [job for job in ctx.job_queue.jobs if not job.removed]:
        ctx.job = job
        await job.callback(ctx)
    check("the album is refused whole, on its total",
          said(third, "file_too_large_convert", size=f"{180 * MB / MB:.1f}", max_mb=100),
          (first.replies, second.replies, third.replies))
    check("and the file it had is removed", not staged(), staged())

    print("\n=== without CBOT_API_ID ===")
    # -S: no site-packages in the child, so whether Telethon is "installed" is
    # decided here (FAKE_SITE) and not by what this machine has.
    probe = ("import convert_utils, big_files; "
             "print(big_files.CONFIGURED, convert_utils.MAX_FILE_MB, convert_utils.max_file_mb_in(7))")
    base_path = os.pathsep.join([str(MIRROR), str(HERE / "stubs")])

    def probe_with(**settings):
        env = {key: value for key, value in os.environ.items()
               if key not in ("CBOT_API_ID", "CBOT_API_HASH", "CONVERT_MAX_FILE_MB")}
        env["PYTHONPATH"] = base_path
        for key, value in settings.items():
            if key == "installed":
                env["PYTHONPATH"] += os.pathsep + str(FAKE_SITE) if value else ""
            else:
                env[key] = value
        return subprocess.run([sys.executable, "-S", "-c", probe], env=env, capture_output=True,
                              text=True).stdout.split()

    check("the route is off and the limit is Telegram's 20 MB",
          probe_with(installed=True) == ["False", "20", "20"])
    check("and CONVERT_MAX_FILE_MB cannot promise more than Telegram allows",
          probe_with(installed=True, CONVERT_MAX_FILE_MB="500") == ["False", "20", "20"])
    check("with the route on, the default is Telegram's own 2000",
          probe_with(installed=True, CBOT_API_ID="1", CBOT_API_HASH="h") == ["True", "2000", "2000"])
    check("set, but with Telethon missing, it stays off rather than failing later",
          probe_with(installed=False, CBOT_API_ID="1", CBOT_API_HASH="h") == ["False", "20", "20"])
    check("with the route on, CONVERT_MAX_FILE_MB sets it",
          probe_with(installed=True, CBOT_API_ID="1", CBOT_API_HASH="h", CONVERT_MAX_FILE_MB="200")
          == ["True", "200", "200"])
    saved_max, saved_in = convert_utils.MAX_FILE_MB, bot.max_file_mb_in
    convert_utils.MAX_FILE_MB, bot.max_file_mb_in = 20, (lambda chat_id: 20)
    ctx = context()
    message = await send(ctx, PRIVATE, 20 * MB + 1, message_id=71)
    check("and a file over it gets Telegram's sentence, as it always did",
          said(message, "file_too_large_download", max_mb=20), message.replies)
    convert_utils.MAX_FILE_MB, bot.max_file_mb_in = saved_max, saved_in

    print("\n=== what a result may be sent at ===")
    limit_for = jobs.send_limit_for
    check("in a group, 50 MB whatever it cost", limit_for(90 * MB, 90 * MB, GROUP) == 50 * MB)
    check("a small job in a private chat still gets 50", limit_for(1 * MB, 1 * MB, PRIVATE) == 50 * MB)
    check("a big one three times its estimated result, which is what its price covers",
          limit_for(100 * MB, 100 * MB, PRIVATE) == 300 * MB, limit_for(100 * MB, 100 * MB, PRIVATE) / MB)
    check("and never past MTProto's 2000 MB", limit_for(900 * MB, 900 * MB, PRIVATE) == 2000 * MB)
    check("a picture's lossless format is no longer off the menu in private at 150 MP",
          jobs.unsendable_targets(150, ["png"], 30 * MB, PRIVATE) == [])
    check("but still is in a group", jobs.unsendable_targets(150, ["png"], 30 * MB, GROUP) == ["png"])

    print("\n=== sending the result ===")
    fresh_route()
    application = SimpleNamespace(bot=FakeBot())
    runner = convert_runner.ConvertRunner(application, [], STAGE, lambda *a: "caption")
    result = STAGE / "result.mkv"
    result.write_bytes(b"0")
    job = jobs.Job(user_id=4242, chat_id=PRIVATE, paths=[], src_ext="mp4", target_ext="mkv",
                   size=90 * MB, price=0, lang="en", send_limit=400 * MB)
    await runner.deliver(job, jobs.Outcome(True, path=str(result), bytes=50 * MB))
    check("50 MB exactly goes through the Bot API",
          len(application.bot.documents) == 1 and not telegram()["sent"],
          (len(application.bot.documents), telegram()["sent"]))
    await runner.deliver(job, jobs.Outcome(True, path=str(result), bytes=50 * MB + 1))
    sent = telegram()["sent"]
    check("one byte more goes over MTProto", len(sent) == 1, sent)
    check("uploaded in 512 KB parts, not the 128 KB Telethon picks for a file this size",
          telegram().get("upload_part_kb") == 512, telegram().get("upload_part_kb"))
    check("to the chat it came from", sent and sent[0]["user_id"] == PRIVATE)
    check("as a document, with its dated name and a plain caption",
          sent and sent[0]["force_document"] and sent[0]["plain"]
          and sent[0]["filename"].startswith("converted_") and sent[0]["filename"].endswith(".mkv")
          and sent[0]["caption"] == "caption", sent)

    print("\n=== a result over its own limit ===")
    jobs.reset()

    class Runner(jobs.Runner):
        def __init__(self):
            self.delivered, self.ended = [], []

        def work_dir(self):
            return STAGE

        async def charge(self, job):
            return 100

        async def deliver(self, job, outcome):
            self.delivered.append(outcome.bytes)

        async def failed(self, job, kind, detail, refunded_to):
            self.ended.append(kind)

    for size, label, expected in ((400 * MB, "exactly 400 MB", "delivered"),
                                  (400 * MB + 1, "one byte more", jobs.TOO_LARGE)):
        async def worker(job, work_dir, stem, time_limit, size=size):
            result.write_bytes(b"0")
            return jobs.Outcome(True, path=str(result), bytes=size)
        jobs.run_worker = worker
        runner = Runner()
        job = jobs.Job(user_id=1, chat_id=PRIVATE, paths=[], src_ext="mp4", target_ext="mkv",
                       size=100 * MB, price=0, lang="en", send_limit=400 * MB)
        jobs.submit(None, job, runner)
        await job.task
        outcome = "delivered" if runner.delivered else (runner.ended or ["?"])[0]
        check(f"{label} against a 400 MB limit: {expected}", outcome == expected, outcome)

    print("\n=== an API id and hash that are set, but wrong ===")
    fresh_route()
    check("a login Telegram accepts leaves the route on",
          await big_files.verify() is True and big_files.CONFIGURED and big_files.VERIFIED is True)
    check("the check is a login and nothing more", telegram()["connects"] == 1 and not telegram()["sent"],
          telegram())
    fresh_route(fail_connect=True)
    big_files.VERIFIED = None
    check("a network that does not answer is not a verdict: the route stays on",
          await big_files.verify() is True and big_files.CONFIGURED and big_files.VERIFIED is None)
    fresh_route(refuse_login=True)
    convert_utils.MAX_FILE_MB = 2000
    check("a login Telegram refuses turns the route off",
          await big_files.verify() is False and not big_files.CONFIGURED and big_files.VERIFIED is False)
    check("and ConvertBot then takes 20 MB, in a private chat too",
          convert_utils.MAX_FILE_MB == 20 and convert_utils.max_file_mb_in(PRIVATE) == 20
          and not convert_utils.BIG_FILES, (convert_utils.MAX_FILE_MB, convert_utils.max_file_mb_in(PRIVATE)))
    check("its price list stops at 20 MB", "over 20 MB" in convert_utils.price_tiers_text("en"),
          convert_utils.price_tiers_text("en")[-200:])
    check("the startup line says why", "refused the API id and hash" in big_files.describe(), big_files.describe())
    connects = telegram()["connects"]
    ctx = context()
    message = await send(ctx, PRIVATE, 20 * MB + 1, message_id=91)
    check("a file over 20 MB then gets Telegram's sentence, and no connection is tried",
          said(message, "file_too_large_download", max_mb=20) and telegram()["connects"] == connects,
          (message.replies, telegram()["connects"], connects))
    check("and a file under it still goes through",
          (await send(context(), PRIVATE, 5 * MB, message_id=92)) is not None)
    check("asking again changes nothing, and tries no login", await big_files.verify() is False
          and telegram()["connects"] == connects)

    print("\n=== and the bot itself ===")
    check("the bot's process never imported Telethon, through all of the above",
          "telethon" not in sys.modules, sorted(name for name in sys.modules if name.startswith("telethon")))


asyncio.run(run())
print(f"\nFAILURES: {len(failures)}")
sys.exit(1 if failures else 0)
