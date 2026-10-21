"""The same link twice while the first is still going: one job, and a thumbs-up on the second.

    python tests/test_link_dedupe.py

DownloaderBot starts a job for every link message. Somebody who pastes the same link again
before the first has landed (a double tap on "send", a nervous second paste, a forwarded
copy) used to start a second download, spend a second share of the day's allowance and put a
second "Fetching..." in the chat. Now the same person, in the same chat, sending the same
canonical link (platforms.canonical_link) for the same platform while a job they started
from a link message for it is queued or running gets no new job, no allowance check, no
record, no status message and no "busy" answer: one 👌 reaction on the copy. Everything else
(another link, another chat, another person, a redo or merge button, a link after the first
job ended) is handled as before.

Driven with the real handle_link and the real _spawn_download, with the routes, the database
and Telegram faked (the way test_delivery_cache drives them):

  * a duplicate while the first is queued and while it is running: no spawn, no allowance
    call, no record_download, one reaction, no message; tracker variants of one post are
    the same link;
  * the next copy is a normal job after the first succeeded, failed or raised;
  * another link, another chat, another person: normal;
  * refused by maintenance, by the full queue, by the allowance, by an exception, or with
    create_task raising: the mark is gone, and the next copy is handled normally;
  * two copies handled concurrently, with an await in front of everything: exactly one job;
  * a mark older than IN_FLIGHT_MAX_SECONDS is ignored; a reaction that raises changes
    nothing and logs only the kind of failure;
  * a redo or merge button never marks, and never clears;
  * the order of the calls, read from the source: the check is straight after the link is
    noted, before the refusals, the queue and the allowance, and nothing is awaited between
    the check and the mark.

No network, no database, nothing from Telegram.

To see a check catch breakage, copy the bot into tests/.mirror, break the copy (the way
tests/README.md describes) and run this again: it imports from the mirror when there is one.
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

SCRATCH = pathlib.Path(tempfile.mkdtemp(prefix="downloaderbot-dedupe-"))
os.environ.update(ENV_FILES="off", DBOT_TOKEN="123:test", DBOT_USERNAME="test", DATABASE_URL="postgresql://stub",
                  DBOT_WORK_DIR=str(SCRATCH))
for key in ("DBOT_CACHE_SECONDS", "DBOT_MAX_PENDING_PER_USER", "DBOT_SENT_FILES"):
    os.environ.pop(key, None)

import bot              # noqa: E402
import i18n             # noqa: E402

logging.disable(logging.CRITICAL)

failures = []
total = 0


def check(name, ok, detail=""):
    global total
    total += 1
    print(("  ok  " if ok else "  FAIL  ") + name + (f" -- {detail}" if not ok and detail else ""))
    if not ok:
        failures.append(name)


class Clock:
    def __init__(self):
        self.now = 50_000.0

    def monotonic(self):
        return self.now


CLOCK = Clock()
real_time = bot.time
bot.time = SimpleNamespace(time=real_time.time, monotonic=CLOCK.monotonic, sleep=real_time.sleep)


class World:
    def __init__(self):
        self.started = []          # (platform, url, chat, user) for every job that began
        self.finished = []
        self.calls = []            # "allowance" / ("record", platform)
        self.texts = []            # what reply_text was asked to send
        self.reactions = []        # (message number, reaction)
        self.reaction_raises = None
        self.gate = None           # an asyncio.Event the jobs wait for, when set
        self.outcome = "ok"        # "ok" | "fail" | "raise"
        self.allowance = {"allowed": True}
        self.allowance_raises = False
        self.refusal = None
        self.create_task_raises = False
        self.tasks = []
        self.lang_waits = False
        self.messages = 0


W = World()


async def fake_job(update, context, platform, url, **kwargs):
    W.started.append((platform, url, update.effective_chat.id, update.effective_user.id))
    if W.gate is not None:
        await W.gate.wait()
    W.finished.append(url)
    if W.outcome == "raise":
        raise RuntimeError("the job blew up")
    return W.outcome == "ok"


async def english(user_id, context=None):
    if W.lang_waits:
        await asyncio.sleep(0)
    return "en"


async def refusal(*args, **kwargs):
    if W.lang_waits:
        await asyncio.sleep(0)
    return W.refusal


def _allowance(user_id):
    W.calls.append("allowance")
    if W.allowance_raises:
        raise ConnectionError("the database is away")
    return W.allowance


def _record(user_id, platform):
    W.calls.append(("record", platform))


bot._resolve_and_send = fake_job
bot.refuse_new_work = refusal
bot.download_allowance = _allowance
bot.record_download = _record
bot._allowance_message = lambda lang, allowance: "ALLOWANCE REFUSED"
i18n.get_lang = english

USER, OTHER, CHAT, OTHER_CHAT = 7, 8, 700, 701
LINK = "https://www.instagram.com/reel/AAAAAAAAAAA/"
LINK_B = "https://www.instagram.com/reel/BBBBBBBBBBB/"


def update_with(text, user=USER, chat=CHAT):
    W.messages += 1
    number = W.messages

    async def reply_text(text_, **kwargs):
        W.texts.append(text_)

    async def set_reaction(reaction=None, **kwargs):
        if W.reaction_raises is not None:
            raise W.reaction_raises
        W.reactions.append((number, reaction))

    message = SimpleNamespace(text=text, reply_text=reply_text, set_reaction=set_reaction)
    return SimpleNamespace(callback_query=None, effective_user=SimpleNamespace(id=user),
                           effective_chat=SimpleNamespace(id=chat), effective_message=message, message=message)


def new_context():
    def create_task(coro, update=None):
        if W.create_task_raises:
            coro.close()
            raise RuntimeError("no loop")
        task = asyncio.ensure_future(coro)
        W.tasks.append(task)
        return task

    return SimpleNamespace(bot=SimpleNamespace(), user_data={}, application=SimpleNamespace(create_task=create_task))


async def paste(link, context, user=USER, chat=CHAT):
    update = update_with(f"look at this {link} please", user=user, chat=chat)
    await bot.handle_link(update, context)
    return update


async def settle():
    for _ in range(8):
        await asyncio.sleep(0)


async def drain():
    """Let the open jobs end."""
    if W.gate is not None:
        W.gate.set()
    await settle()
    for task in W.tasks:
        if not task.done():
            task.cancel()
    await asyncio.gather(*W.tasks, return_exceptions=True)


def fresh(**kw):
    global W
    W = World()
    bot._in_flight.clear()
    bot._user_pending.clear()
    CLOCK.now = 50_000.0
    for name, value in kw.items():
        setattr(W, name, value)
    return W


def scenario(fn):
    async def go():
        try:
            await fn()
        finally:
            await drain()
    asyncio.run(go())


def jobs():
    return len(W.started)


def allowances():
    return W.calls.count("allowance")


def records():
    return sum(1 for call in W.calls if isinstance(call, tuple))


# ---------------------------------------------------------------------------

async def first_and_second():
    print("\n== a copy while the first is queued, and while it is running ==")
    fresh()
    W.gate = asyncio.Event()
    context = new_context()
    await paste(LINK, context)
    check("the first copy is one job, one allowance check and one record",
          len(W.tasks) == 1 and allowances() == 1 and records() == 1, (W.tasks, W.calls))
    check("...and its mark stands", len(bot._in_flight) == 1)

    # The job has been created but has not run: queued.
    check("the job has not started yet (queued)", jobs() == 0)
    duplicate = await paste(LINK, context)
    check("a copy while it is queued: no new job", len(W.tasks) == 1)
    check("...no allowance check, no record", allowances() == 1 and records() == 1, W.calls)
    check("...a 👌 on the copy and nothing else", W.reactions == [(W.messages, "👌")] and W.texts == [], (W.reactions, W.texts))
    check("...the reaction is on the copy's own message",
          duplicate.effective_message is not None and W.reactions[0][0] == W.messages)

    await settle()
    check("the first job runs", jobs() == 1, W.started)
    W.reactions.clear()
    await paste(LINK, context)
    await paste(LINK, context)
    check("two more copies while it runs: still one job, one allowance check, one record",
          len(W.tasks) == 1 and jobs() == 1 and allowances() == 1 and records() == 1, (jobs(), W.calls))
    check("...two reactions, no text", len(W.reactions) == 2 and W.texts == [], (W.reactions, W.texts))
    check("...no status message of any kind, and the job is the first copy's",
          W.started == [("instagram", LINK, CHAT, USER)], W.started)

    W.gate.set()
    await settle()
    check("the job ends and the mark goes", W.finished == [LINK] and not bot._in_flight, (W.finished, bot._in_flight))
    await paste(LINK, context)
    await settle()
    check("the next copy is a normal job: allowance, record, a start",
          allowances() == 2 and records() == 2 and jobs() == 2, (jobs(), W.calls))


async def tracker_variants():
    print("\n== tracker variants of one post are one link ==")
    fresh()
    W.gate = asyncio.Event()
    context = new_context()
    await paste("https://www.instagram.com/reel/AAAAAAAAAAA/?igsh=MWN4ZHJ1cGZzcTA3Mg==", context)
    await settle()
    await paste("https://instagram.com/reel/AAAAAAAAAAA?utm_source=ig_web_copy_link", context)
    await paste("https://www.instagram.com/someone/reels/AAAAAAAAAAA/#x", context)
    check("three shares of one reel: one job", jobs() == 1 and allowances() == 1 and len(W.reactions) == 2, (jobs(), W.reactions))
    await drain()

    fresh()
    W.gate = asyncio.Event()
    context = new_context()
    await paste("https://youtu.be/dQw4w9WgXcQ?si=abc", context)
    await settle()
    await paste("https://www.youtube.com/watch?v=dQw4w9WgXcQ&pp=ygUJ", context)
    check("youtu.be/ID?si=x and watch?v=ID&pp=y: one job", jobs() == 1 and len(W.reactions) == 1, (jobs(), W.reactions))


async def after_the_end(outcome):
    print(f"\n== after the job {'succeeds' if outcome == 'ok' else 'fails' if outcome == 'fail' else 'raises'} ==")
    fresh(outcome=outcome)
    W.gate = asyncio.Event()
    context = new_context()
    await paste(LINK, context)
    await settle()
    W.gate.set()
    await settle()
    check("the mark is gone", not bot._in_flight, bot._in_flight)
    check("the pending count is gone too", not bot._user_pending, bot._user_pending)
    W.gate = asyncio.Event()
    await paste(LINK, context)
    await settle()
    check("the next copy is a normal job", jobs() == 2 and allowances() == 2 and records() == 2 and W.reactions == [],
          (jobs(), W.calls, W.reactions))


async def other_links():
    print("\n== another link, another chat, another person ==")
    fresh()
    W.gate = asyncio.Event()
    context = new_context()
    await paste(LINK, context)
    await settle()
    await paste(LINK_B, context)
    await settle()
    check("another link: a normal job", jobs() == 2 and W.reactions == [], (W.started, W.reactions))
    await paste(LINK, context, chat=OTHER_CHAT)
    await settle()
    check("the same link and person in another chat: a normal job", jobs() == 3 and W.reactions == [], (W.started, W.reactions))
    await paste(LINK, new_context(), user=OTHER)
    await settle()
    check("the same link in the same chat from another person: a normal job",
          jobs() == 4 and W.reactions == [] and allowances() == 4 and records() == 4, (W.started, W.reactions))
    await paste(LINK, context)
    check("and the first person's own copy is still merged", jobs() == 4 and len(W.reactions) == 1, (W.started, W.reactions))
    check("four marks stand, one per job", len(bot._in_flight) == 4, bot._in_flight)
    other_platform = "https://www.tiktok.com/@scout2015/video/6718335390845095173"
    await paste(other_platform, context)
    await settle()
    check("another platform: a normal job", jobs() == 5, W.started)
    W.gate.set()
    await settle()
    check("every mark goes when its job ends", not bot._in_flight and not bot._user_pending, (bot._in_flight, bot._user_pending))


async def refusals():
    print("\n== a copy that is refused leaves no mark ==")
    fresh()
    context = new_context()
    W.refusal = "We are updating, try again in a minute."
    await paste(LINK, context)
    check("refused by maintenance: told so, no job, no mark", jobs() == 0 and len(W.tasks) == 0 and not bot._in_flight
          and W.texts == [W.refusal] and allowances() == 0, (W.texts, bot._in_flight))
    W.refusal = None
    W.texts.clear()
    await paste(LINK, context)
    await settle()
    check("...and the next copy is a normal job (no thumbs-up)", jobs() == 1 and W.reactions == [] and allowances() == 1, (jobs(), W.reactions))
    await drain()

    fresh()
    context = new_context()
    bot._user_pending[USER] = bot.MAX_PENDING_PER_USER
    await paste(LINK, context)
    check("queue full: the usual answer, no job, no allowance, no mark",
          len(W.texts) == 1 and jobs() == 0 and allowances() == 0 and not bot._in_flight and W.reactions == [], (W.texts, bot._in_flight))
    bot._user_pending.clear()
    W.texts.clear()
    await paste(LINK, context)
    await settle()
    check("...and the next copy is a normal job", jobs() == 1 and W.reactions == [], (jobs(), W.reactions))
    await drain()

    fresh()
    context = new_context()
    W.allowance = {"allowed": False}
    await paste(LINK, context)
    check("refused by the allowance: told so, no record, no job, no mark",
          W.texts == ["ALLOWANCE REFUSED"] and records() == 0 and jobs() == 0 and not bot._in_flight, (W.texts, W.calls, bot._in_flight))
    W.allowance = {"allowed": True}
    W.texts.clear()
    await paste(LINK, context)
    await settle()
    check("...and the next copy is a normal job", jobs() == 1 and W.reactions == [] and records() == 1, (jobs(), W.reactions, W.calls))
    await drain()

    fresh()
    context = new_context()
    W.allowance_raises = True
    raised = None
    try:
        await paste(LINK, context)
    except ConnectionError as exc:
        raised = exc
    check("an exception in handle_link still escapes (to the error handler)", raised is not None)
    check("...and leaves no mark", not bot._in_flight, bot._in_flight)
    W.allowance_raises = False
    await paste(LINK, context)
    await settle()
    check("...and the next copy is a normal job", jobs() == 1 and W.reactions == [], (jobs(), W.reactions))
    await drain()

    fresh()
    context = new_context()
    W.create_task_raises = True
    raised = None
    try:
        await paste(LINK, context)
    except RuntimeError as exc:
        raised = exc
    check("create_task raising escapes too", raised is not None)
    check("...and leaves no mark", not bot._in_flight, bot._in_flight)
    W.create_task_raises = False
    await paste(LINK, context)
    await settle()
    check("...and the next copy is a normal job", jobs() == 1 and W.reactions == [], (jobs(), W.reactions))


async def at_the_same_time():
    print("\n== two copies handled at the same time ==")
    fresh()
    W.lang_waits = True
    W.gate = asyncio.Event()
    context = new_context()
    await asyncio.gather(paste(LINK, context), paste(LINK, context))
    await settle()
    check("exactly one job, one allowance check, one record, one reaction",
          jobs() == 1 and allowances() == 1 and records() == 1 and len(W.reactions) == 1,
          (jobs(), W.calls, W.reactions))
    await drain()

    fresh()
    W.lang_waits = True
    W.gate = asyncio.Event()
    context = new_context()
    await asyncio.gather(*(paste(LINK, context) for _ in range(5)))
    await settle()
    check("five at once: still one job and four reactions", jobs() == 1 and len(W.reactions) == 4, (jobs(), W.reactions))
    await drain()

    fresh()
    W.lang_waits = True
    W.gate = asyncio.Event()
    await asyncio.gather(paste(LINK, new_context()), paste(LINK_B, new_context()), paste(LINK, new_context(), chat=OTHER_CHAT))
    await settle()
    check("at once but different links or chats: all normal", jobs() == 3 and W.reactions == [], (jobs(), W.reactions))


async def old_marks():
    print("\n== a mark that was never cleared ==")
    fresh()
    W.gate = asyncio.Event()
    key = (CHAT, USER, "instagram", LINK)
    bot._in_flight[key] = CLOCK.now - (bot.IN_FLIGHT_MAX_SECONDS - 1)
    context = new_context()
    await paste(LINK, context)
    check("just under the limit: still a duplicate", jobs() == 0 and len(W.tasks) == 0 and len(W.reactions) == 1, (W.tasks, W.reactions))
    bot._in_flight[key] = CLOCK.now - (bot.IN_FLIGHT_MAX_SECONDS + 1)
    await paste(LINK, context)
    await settle()
    check("older than the limit: ignored, a normal job", jobs() == 1 and allowances() == 1, (jobs(), W.calls))
    check("...and the mark is overwritten with a new one", bot._in_flight[key] == CLOCK.now, bot._in_flight)
    check("the limit is an hour", bot.IN_FLIGHT_MAX_SECONDS == 3600)

    print("\n== a job that outlives the limit leaves a newer copy's mark alone ==")
    fresh()
    context = new_context()
    gate = asyncio.Event()

    async def slow_job():
        await gate.wait()

    bot._in_flight[key] = CLOCK.now
    bot._spawn_download(context, update_with(f"look at this {LINK} please"), slow_job, in_flight=key)
    await settle()
    CLOCK.now += bot.IN_FLIGHT_MAX_SECONDS + 1
    bot._in_flight[key] = CLOCK.now                 # a newer copy, accepted past the limit
    gate.set()
    await settle()
    check("the old job ends and the newer copy's mark still stands", bot._in_flight.get(key) == CLOCK.now, bot._in_flight)
    fresh()
    context = new_context()
    gate = asyncio.Event()
    bot._in_flight[key] = CLOCK.now
    bot._spawn_download(context, update_with(f"look at this {LINK} please"), slow_job, in_flight=key)
    await settle()
    gate.set()
    await settle()
    check("...while a job whose mark is still its own takes it down", key not in bot._in_flight, bot._in_flight)


async def reaction_trouble():
    print("\n== the reaction cannot be set ==")
    fresh()
    W.gate = asyncio.Event()
    context = new_context()
    await paste(LINK, context)
    await settle()
    W.reaction_raises = RuntimeError("Bad Request: REACTION_INVALID secret-link-text")
    try:
        await paste(LINK, context)
        raised = None
    except Exception as exc:                                       # noqa: BLE001
        raised = exc
    check("a reaction that raises is swallowed", raised is None, repr(raised))
    check("...no job, no message, no allowance", jobs() == 1 and W.texts == [] and allowances() == 1, (jobs(), W.texts, W.calls))

    records_ = []

    class Catch(logging.Handler):
        def emit(self, record):
            records_.append((record.levelno, record.getMessage(), bool(record.exc_info)))

    handler = Catch()
    logging.disable(logging.NOTSET)
    root = logging.getLogger()
    kept_handlers, kept_level = root.handlers[:], root.level
    root.handlers, root.level = [handler], logging.DEBUG
    try:
        await paste(LINK, context)
    finally:
        root.handlers, root.level = kept_handlers, kept_level
        logging.disable(logging.CRITICAL)
    text = "\n".join(message for _, message, _ in records_)
    check("it is logged once, at debug level, as the kind of failure only",
          len(records_) == 1 and records_[0][0] == logging.DEBUG and "RuntimeError" in text, records_)
    check("...with no text of the error, no link, and no traceback",
          "secret-link-text" not in text and "REACTION_INVALID" not in text and LINK not in text and not records_[0][2], records_)


async def buttons():
    print("\n== the buttons do not mark, and do not clear ==")
    fresh()
    W.gate = asyncio.Event()
    context = new_context()
    await paste(LINK, context)
    await settle()
    marks = dict(bot._in_flight)

    async def work():
        W.finished.append("button")

    update = update_with("x")
    bot._spawn_download(context, update, work)
    await settle()
    check("a job spawned the way the redo and merge buttons do it leaves the mark of the link job alone",
          bot._in_flight == marks and "button" in W.finished, (bot._in_flight, marks))
    for name in ("redeliver_callback", "merge_callback", "_merge_and_send"):
        check(f"{name} knows nothing of the marks", "_in_flight" not in inspect.getsource(getattr(bot, name)))
    calls = [m.group(1) for m in re.finditer(r"(?<!def )_spawn_download\(([^)]*)\)", inspect.getsource(bot))]
    check("only handle_link passes a key to _spawn_download",
          len(calls) == 3 and sum("in_flight" in args for args in calls) == 1
          and "in_flight" in re.search(r"(?<!def )_spawn_download\(([^)]*)\)", inspect.getsource(bot.handle_link)).group(1), calls)


def wiring():
    print("\n== the order of the calls, from the source ==")
    source = inspect.getsource(bot.handle_link)
    check("the check is straight after the link is noted, before the refusals, the queue and the allowance",
          source.index("note_link") < source.index("_in_flight") < source.index("refuse_new_work")
          < source.index("_user_pending") < source.index("download_allowance") < source.index("record_download"))
    between = source[source.index("_already_in_flight(key)"):source.index("_in_flight[key] =")]
    check("nothing is awaited between the check and the mark (but the answer to the copy, which returns)",
          "await" not in between.replace("await _answer_duplicate(update)", "") and "return" in between, between)
    check("the key is the chat, the person, the platform and the canonical link",
          "(update.effective_chat.id, update.effective_user.id, platform, url)" in source)
    spawn = inspect.getsource(bot._spawn_download)
    check("the mark is cleared in the same finally that lowers the pending count",
          re.search(r"finally:.*_user_pending.*_in_flight", spawn, re.S) is not None, spawn)
    check("a mark is cleared on every way out of handle_link that did not spawn the job",
          "finally:" in source and "spawned" in source)
    check("the duplicate answer is one small function", inspect.iscoroutinefunction(bot._answer_duplicate)
          and "set_reaction" in inspect.getsource(bot._answer_duplicate) and "👌" in inspect.getsource(bot._answer_duplicate))


def main():
    for fn in (first_and_second, tracker_variants, lambda: after_the_end("ok"), lambda: after_the_end("fail"),
               lambda: after_the_end("raise"), other_links, refusals, at_the_same_time, old_marks,
               reaction_trouble, buttons):
        scenario(fn)
    wiring()

    shutil.rmtree(SCRATCH, ignore_errors=True)
    print()
    if failures:
        print(f"FAILED ({len(failures)} of {total}):")
        for name in failures:
            print("  -", name)
        return 1
    print(f"all {total} checks passed: the same link twice is one job.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
