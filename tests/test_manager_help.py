"""ManagerBot's help per command: a page for each of the thirteen, and /help leading to them. No database, no network.

    python tests/test_manager_help.py

The owner (2026-10-17): each command that is complex or has options gets a page, "a detailed explanation and one
simple example", and every number on a page is read from the code that enforces it, when the page is shown.
2026-10-18: "make it work like the ping command works" -- /help carries one button per page and a tap sends the page
as a new message. ManagerBot has thirteen pages (pause ... say) and four aliases that show a page (/help warn, /help
resume, /help finishupdates, /help nightly). They are plain text, link previews off, owner-only like every command,
and in no menu. This holds the parts that decide whether that is right:

  * the pages: every one renders, in plain text, within a message, with no placeholder left, and ends with the way
    back to /help; every /command a page names is a command ManagerBot handles; every example is one the real
    command would take;
  * the wiring, from a real run of main(): /help and /start carry a button per page (in order, two to a row,
    "/name" and "helpdet:name"); a tap by the owner sends the page as a new message and edits nothing; a tap by
    anyone else is refused by guard and sends nothing; a forged name is refused with an alert; no help_<x> command
    is registered; the page handler sits before any catch-all callback; /help <name> (and /help help_<name>,
    /help <NAME>, an alias) gives the same page; a stranger gets "This is a private bot." and no page;
  * computed, not typed: change the constant a page reads and the page changes, and so does the command that
    enforces it (the constants that were literals inside the commands are named now, with the same values);
  * the text fixes the pages depend on (8.3 of the spec): /problems has no [mine], /reports has its own line, /logs
    and /broadcast say what they take, an --active broadcast says who it reaches, /pause says /resume.

To see a check catch breakage, copy the bot into tests/.mirror, break the copy and run this again: it imports from
the mirror when there is one.
"""
import ast
import asyncio
import contextlib
import inspect
import logging
import os
import pathlib
import re
import sys
import time as time_module
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent


def source_dir(folder):
    mirrored = HERE / ".mirror" / folder
    return mirrored if mirrored.exists() else REPO / folder


SRC = source_dir("manager_bot")
sys.path.insert(0, str(SRC))
sys.path.insert(1, str(REPO / "shared"))
sys.path.insert(0, str(HERE / "stubs"))

for var in ("SBOT", "CBOT", "DBOT", "ABOT", "MBOT"):
    os.environ[f"{var}_TOKEN"] = "123:test"
    os.environ[f"{var}_USERNAME"] = "test"
    os.environ[f"{var}_ADMIN_ID"] = "1"
os.environ.update(ENV_FILES="off", DATABASE_URL="postgresql://stub", MBOT_REGISTRY="")
os.environ.pop("SIBLING_BOTS", None)

logging.disable(logging.CRITICAL)

import bot  # noqa: E402  (ManagerBot's)
import family_link as fl  # noqa: E402
import lifecycle  # noqa: E402

failures = []
checks = 0


def check(name, ok, detail=""):
    global checks
    checks += 1
    print(("  ok  " if ok else "  FAIL  ") + name + (f" -- {detail}" if not ok and detail != "" else ""))
    if not ok:
        failures.append(name)


def previews_off(kwargs):
    """Was this reply sent with link previews disabled? (The stubbed LinkPreviewOptions keeps its arguments in .kwargs.)"""
    options = kwargs.get("link_preview_options")
    return bool(options is not None and (getattr(options, "is_disabled", None) or getattr(options, "kwargs", {}).get("is_disabled")))


def utf16(text):
    """Telegram's measure of a message: UTF-16 code units."""
    return len(text.encode("utf-16-le")) // 2


@contextlib.contextmanager
def changed(obj, name, value):
    """Set obj.name for a moment, and put it back."""
    old = getattr(obj, name)
    setattr(obj, name, value)
    try:
        yield
    finally:
        setattr(obj, name, old)


# One event loop for the whole run. A loop per call (asyncio.run) is a socket pair per call on Windows, and
# making a thousand of them now and then hangs in accept() until the machine looks away.
LOOP = asyncio.new_event_loop()


def run_async(coroutine):
    return LOOP.run_until_complete(coroutine)


OWNER = next(iter(bot.ADMIN_IDS))
STRANGER = 999
PAGES = ["pause", "gift", "promo", "broadcast", "balance", "addcredit", "run", "logs", "problems", "events", "feedback",
         "sql", "say"]
ALIASES = {"warn": "pause", "resume": "pause", "finishupdates": "pause", "nightly": "problems"}
PRIVATE = "This is a private bot."


# ---------------------------------------------------------------------------
# a fake Telegram: what a handler was asked to say, to whom
# ---------------------------------------------------------------------------

replies, sent, queued, edits, logged, asked = [], [], [], [], [], []
answered, sent_kwargs = [], []          # a tap's answer (args, kwargs); the options of each send_message


class Message:
    def __init__(self, text=""):
        self.text = text

    async def reply_text(self, text, **kwargs):
        replies.append((text, kwargs))


HELP_CHAT = 777           # the chat the help message (and so a tap on its buttons) is in


class Query:
    def __init__(self, data):
        self.data = data
        self.message = SimpleNamespace(tag="the message with the buttons", chat_id=HELP_CHAT)

    async def answer(self, *a, **k):
        answered.append((a, k))


async def send_message(chat_id=None, text=None, **kwargs):
    sent.append((chat_id, text))
    sent_kwargs.append(kwargs)


async def edit_in_place(message, bot_, text, **kwargs):
    edits.append(text)


def queue_command(target_bot, command, args, requested_by, reply_chat_id):
    queued.append((target_bot, command, args))
    return len(queued)


def log_event(*args, **kwargs):
    logged.append(args)


def reset():
    for box in (replies, sent, queued, edits, logged, asked, answered, sent_kwargs):
        box.clear()
    bot._reported_strangers.clear()


def update_for(user_id, text="", query=None):
    message = Message(text)
    return SimpleNamespace(message=message, effective_message=message, callback_query=query,
                           effective_user=SimpleNamespace(id=user_id, username=None),
                           effective_chat=SimpleNamespace(id=user_id * 10))


def context_for(*args, user_data=None):
    return SimpleNamespace(args=list(args), user_data={} if user_data is None else user_data,
                           bot=SimpleNamespace(send_message=send_message))


def run(handler, user_id, *args, text="", user_data=None):
    """Run a command handler as `user_id` would, and return what it replied: [(text, kwargs), ...]."""
    replies.clear()
    try:
        run_async(handler(update_for(user_id, text), context_for(*args, user_data=user_data)))
    except Exception as exc:                                            # noqa: BLE001 -- the caller asserts on what was said
        replies.append((f"<the handler raised {type(exc).__name__}: {exc}>", {}))
    return list(replies)


def tap(handler, user_id, data, user_data):
    replies.clear()
    run_async(handler(update_for(user_id, query=Query(data)), context_for(user_data=user_data)))


def press(user_id, data):
    """A tap on a button as `user_id` makes it, answered by whichever handler main() registered for that data
    (the first whose pattern matches, as python-telegram-bot picks it). Clears what was recorded before, and
    leaves in `answered`, `sent`, `sent_kwargs`, `edits` and `replies` what the tap caused. The update has no
    message, as a callback query's has none. Returns the handler, or None when none would take it."""
    for box in (replies, sent, sent_kwargs, answered, edits):
        box.clear()
    handler = next((cb for _, pattern, cb in BUTTONS if pattern is None or re.match(pattern, data)), None)
    if handler is None:
        return None
    update = SimpleNamespace(message=None, effective_message=None, callback_query=Query(data),
                             effective_user=SimpleNamespace(id=user_id, username=None),
                             effective_chat=SimpleNamespace(id=HELP_CHAT))
    run_async(handler(update, context_for()))
    return handler


real = SimpleNamespace(queue=bot.db.queue_command, edit=bot.edit_in_place, log=bot.db.log_event)
bot.db.queue_command, bot.edit_in_place, bot.db.log_event = queue_command, edit_in_place, log_event


# ---------------------------------------------------------------------------
# main(), run as far as run_polling with every handler written down
# ---------------------------------------------------------------------------

class Ran(Exception):
    """run_polling reached: everything before it is wired."""


class FakeJobQueue:
    def run_repeating(self, *a, **k):
        return object()

    def run_once(self, *a, **k):
        return object()

    def run_daily(self, *a, **k):
        return object()


class FakeApp:
    persistence = None

    def __init__(self):
        self.job_queue = FakeJobQueue()
        self.handlers = []

    def add_handler(self, handler, group=0):
        self.handlers.append((group, handler))

    def add_error_handler(self, callback):
        pass

    def run_polling(self, **kwargs):
        raise Ran


class FakeBuilder:
    def token(self, *a):
        return self

    def post_init(self, *a):
        return self

    def post_stop(self, *a):
        return self

    def persistence(self, *a):
        return self

    def build(self):
        FakeBuilder.app = FakeApp()
        return FakeBuilder.app


class WroteCommand:
    def __init__(self, commands, callback=None, *a, **k):
        self.commands = [commands] if isinstance(commands, str) else list(commands)
        self.callback = callback


class WroteMessage:
    def __init__(self, filter_=None, callback=None, *a, **k):
        self.commands, self.callback = [], callback


class WroteButton:
    def __init__(self, callback=None, pattern=None, *a, **k):
        self.commands, self.callback, self.pattern = [], callback, pattern


def wire():
    """Run bot.main() with python-telegram-bot and the database faked. Returns the app's handlers in order:
    [(commands it answers, callback)] -- commands is [] for a handler that is not a CommandHandler."""
    saved = (bot.ApplicationBuilder, bot.CommandHandler, bot.MessageHandler, bot.init_db, bot.lifecycle.persistence,
             bot.lifecycle.install, fl.attach, bot.CallbackQueryHandler)
    bot.ApplicationBuilder, bot.CommandHandler, bot.MessageHandler = FakeBuilder, WroteCommand, WroteMessage
    bot.CallbackQueryHandler = WroteButton
    bot.init_db = lambda *a, **k: None
    bot.lifecycle.persistence, bot.lifecycle.install = (lambda: None), (lambda *a, **k: None)
    fl.attach = lambda *a, **k: None
    try:
        try:
            bot.main()
        except Ran:
            pass
    finally:
        (bot.ApplicationBuilder, bot.CommandHandler, bot.MessageHandler, bot.init_db, bot.lifecycle.persistence,
         bot.lifecycle.install, fl.attach, bot.CallbackQueryHandler) = saved
    return [(getattr(h, "commands", []), getattr(h, "callback", None)) for _, h in FakeBuilder.app.handlers]


WIRED = wire()
BY_NAME = {}
for names, callback in WIRED:
    for name in names:
        BY_NAME[name] = callback
# The button handlers in the order main() added them: (position among all handlers, pattern, callback).
BUTTONS = [(i, h.pattern, h.callback) for i, (_, h) in enumerate(FakeBuilder.app.handlers) if isinstance(h, WroteButton)]
HELP_PATTERN = r"^helpdet:[a-z]{1,20}$"


async def no_handler(update, context):
    await update.message.reply_text("<no handler is registered for this command>")


def handler_of(name):
    """What main() registered for /name; a command that is not registered answers with a sentence that matches nothing."""
    return BY_NAME.get(name, no_handler)


# ---------------------------------------------------------------------------
# the pages
# ---------------------------------------------------------------------------

print("\n== the pages ==")
check("the pages are the thirteen, in the order /help lists them", list(bot.HELP_PAGES) == PAGES, list(bot.HELP_PAGES))
check("the aliases are the four, and each shows the page it belongs with", bot.HELP_ALIASES == ALIASES, bot.HELP_ALIASES)
check("every alias is a page's", all(target in bot.HELP_PAGES for target in bot.HELP_ALIASES.values()))
check("no alias is also a page (a name means one thing)", not set(bot.HELP_ALIASES) & set(bot.HELP_PAGES))
check("the page names fit the button's callback data (helpdet:<a-z, 1 to 20 letters>)",
      all(re.fullmatch(HELP_PATTERN, "helpdet:" + n) for n in bot.HELP_PAGES))

for name in PAGES:
    body = bot.HELP_PAGES[name]()
    text = bot.help_page_text(name)
    others = [n for n in PAGES if n != name]
    check(f"{name}: a page is text, with a title line and an example", isinstance(body, str) and body.strip() != ""
          and "\nExample: /" in body, body[:60])
    check(f"{name}: no placeholder is left in it", "{" not in text and "}" not in text, re.findall(r"[{}][^\n]{0,20}", text))
    check(f"{name}: no blank line is doubled", "\n\n\n" not in text)
    check(f"{name}: it fits one message ({utf16(text)} of 4096 UTF-16 units)", utf16(text) <= 4096)
    check(f"{name}: it ends with the way back to /help, and nothing else after the page",
          text == body.rstrip() + "\n\nEverything: /help", text[-200:])
    check(f"{name}: no list of other pages and no /help_<x> command is left in it",
          "Other pages" not in text and "/help_" not in text and "In detail" not in text, text[-200:])

print("\n-- every /command a page names is one ManagerBot handles")
handled = set(BY_NAME)
ast_handled = set()
tree = ast.parse((REPO / "manager_bot" / "bot.py").read_text(encoding="utf-8"))
for node in ast.walk(tree):
    if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "CommandHandler" and node.args:
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            ast_handled.add(first.value)
        elif isinstance(first, (ast.List, ast.Tuple)):
            ast_handled.update(e.value for e in first.elts if isinstance(e, ast.Constant))
check("main() registers the same commands the source names (and none is a help_ page)",
      handled == ast_handled and not [n for n in handled if n.startswith("help_")], sorted(handled ^ ast_handled))
COMMAND_WORD = re.compile(r"(?<![\w/.:])/([a-z][a-z_]*)")
for name in PAGES:
    named = sorted(set(COMMAND_WORD.findall(bot.help_page_text(name))))
    missing = [c for c in named if c not in handled]
    check(f"{name}: it names {len(named)} commands, and ManagerBot handles every one" + (f" -- not: {missing}" if missing else ""),
          not missing)
named_in_help = sorted(set(COMMAND_WORD.findall(re.sub(r"<[^>]+>", "", bot.HELP))))
check("/help names only commands ManagerBot handles", all(c in handled for c in named_in_help),
      [c for c in named_in_help if c not in handled])

print("\n-- every example is one the real command takes")
examples = {}
for name in PAGES:
    found = re.findall(r"^Example: (/.*)$", bot.help_page_text(name), re.M)
    examples[name] = found
    check(f"{name}: exactly one example", len(found) == 1, found)
words = {n: examples[n][0].split() for n in PAGES if examples[n]}
gift = words["gift"]
check("gift: its bot is all, its key fits the rule, its amount is within the most",
      gift[1] == "all" and fl.GIFT_KEY_RE.fullmatch(gift[2]) and 0 < int(gift[3]) <= fl.GIFT_MAX_CREDIT, gift)
check("gift: its day is written the way the command wants it", re.fullmatch(r"\d{4}-\d{2}-\d{2}", gift[4])
      and fl._gift_last_day(gift[4]) is not None, gift)
check("promo: it is /promo stop, a form the command takes", words["promo"] == ["/promo", "stop"], words["promo"])
check("pause: it starts with /pause and a number of minutes, as the command takes", re.match(r"/pause \d+,", examples["pause"][0]) is not None,
      examples["pause"])
broadcast = words["broadcast"]
check("broadcast: it names all, then --active, then words",
      broadcast[1] == "all" and broadcast[2] == "--active" and len(broadcast) > 3, broadcast)
check("balance: its id is a number and its change is one the command parses",
      words["balance"][1].isdigit() and bot._parse_balance_change(words["balance"][2]) == ("add", 50), words["balance"])
check("addcredit: an id and an amount above 0", words["addcredit"][1].isdigit() and int(words["addcredit"][2]) > 0)
check("run: a bot ManagerBot resolves and a command every bot has",
      (bot.resolve_bot(words["run"][1]) or {}).get("id") in {b["id"] for b in bot.CHILDREN}
      and words["run"][2] in fl.COMMANDS, words["run"])
check("logs: a bot, a number within the most, and one of the words that picks a log",
      bot.resolve_bot(words["logs"][1]) is not None and 0 < int(words["logs"][2]) <= fl.LOG_TAIL_MAX
      and words["logs"][3] in ("bot", "all", "problems"), words["logs"])
check("problems: hours within the range", 1 <= int(words["problems"][1]) <= bot.PROBLEMS_MAX_HOURS)
check("events: a bot and a number within the most",
      bot.resolve_bot(words["events"][1]) is not None and 0 < int(words["events"][2]) <= bot.EVENTS_MAX, words["events"])
check("feedback: one of the two words it takes", words["feedback"][1] in (fl.FEEDBACK_INSTANT, fl.FEEDBACK_NIGHTLY))
check("sql: starts with a word the command allows", words["sql"][1].lower() in ("select", "with", "table", "explain", "show"))
check("say: a bot ManagerBot resolves, a numeric id, and words",
      bot.resolve_bot(words["say"][1]) is not None and words["say"][2].isdigit() and len(words["say"]) > 3, words["say"])
sticker = [b for b in bot.CHILDREN if b["id"] == "stickerbot"]
sticker_source = (REPO / "sticker_bot" / "db.py").read_text(encoding="utf-8")
check("sql: the table in the example exists, in the schema the example names",
      "sticker_bot.packs" in examples["sql"][0] and bool(sticker) and sticker[0]["schema"] == "sticker_bot"
      and "CREATE TABLE IF NOT EXISTS packs" in sticker_source)

print("\n== /help ==")
shown = bot.HELP.format(bots=bot.bot_list_hint())
check("HELP fills in", "{" not in shown and "}" not in shown and "Bots: <code>" in shown)
check("HELP fits one message", utf16(shown) <= 4096, utf16(shown))
check("HELP names no /help_<x> command and has no 'In detail' line", "/help_" not in shown and "In detail" not in shown)
check("the line naming the pages and the /help_<name> handler factory are gone",
      not hasattr(bot, "help_pages_line") and not hasattr(bot, "help_page_command"))


def keyboard_of(reply):
    """[[(label, callback_data), ...], ...] under a reply."""
    markup = reply[1].get("reply_markup")
    return [[(b.args[0], b.kwargs.get("callback_data")) for b in row] for row in (markup.inline_keyboard if markup else [])]


WANT_ROWS = [[("/" + n, "helpdet:" + n) for n in PAGES[i:i + 2]] for i in range(0, len(PAGES), 2)]
for command in ("help", "start"):
    got = run(handler_of(command), OWNER)
    check(f"/{command} alone: the text, in HTML, with no 'In detail' line",
          len(got) == 1 and got[0][0] == shown and got[0][1].get("parse_mode") == bot.ParseMode.HTML, got)
    check(f"/{command} alone: one button per page, in the order of HELP_PAGES, two to a row, '/name' and 'helpdet:name'",
          keyboard_of(got[0]) == WANT_ROWS, keyboard_of(got[0]))
    check(f"/{command} alone: {len(PAGES)} buttons, none for an alias",
          sum(len(row) for row in keyboard_of(got[0])) == len(PAGES)
          and not [d for row in keyboard_of(got[0]) for _, d in row if d.split(":")[1] in ALIASES])
    check(f"/{command} alone: every callback is at most 64 bytes of UTF-8 and matches the handler's pattern",
          all(len(d.encode("utf-8")) <= 64 and re.fullmatch(HELP_PATTERN, d) for row in keyboard_of(got[0]) for _, d in row))
for word in ("nonsense", "full", "pausee", "", "help", "gift2", "/", "help_"):
    got = run(handler_of("help"), OWNER, word)
    check(f"/help {word!r} is not a page: the same text and buttons as /help alone",
          len(got) == 1 and got[0][0] == shown and keyboard_of(got[0]) == WANT_ROWS, got[:1])

print("\n== /help <name> ==")
for name in list(PAGES) + list(ALIASES):
    page = ALIASES.get(name, name)
    want = bot.help_page_text(page)
    check(f"/help_{name} is not a command any more", f"help_{name}" not in BY_NAME)
    got = run(handler_of("help"), OWNER, name)
    check(f"/help {name}: the {page} page, plain text, previews off, no buttons",
          len(got) == 1 and got[0][0] == want and "parse_mode" not in got[0][1] and "reply_markup" not in got[0][1]
          and previews_off(got[0][1]), got[:1])
    for typed in (name, "/help_" + name, "help_" + name, name.upper(), name.title(), name + "@MumuManagerBot",
                  "/help_" + name + "@mumumanagerbot"):
        got = run(handler_of("help"), OWNER, typed)
        check(f"/help {typed}: the same page", len(got) == 1 and got[0][0] == want
              and "parse_mode" not in got[0][1] and previews_off(got[0][1]), got[:1])
    got = run(handler_of("help"), OWNER, name, "and", "more")
    check(f"/help {name} and more: only the first word counts", len(got) == 1 and got[0][0] == want)
    got = run(handler_of("start"), OWNER, name)
    check(f"/start {name}: the same page (a deep link)", len(got) == 1 and got[0][0] == want)

def page_of(word):
    return run(handler_of("help"), OWNER, word)[0][0]


check("an alias is the page, not a page of its own: /help warn is /help pause", page_of("warn") == page_of("pause"))
check("/help nightly is /help problems", page_of("nightly") == page_of("problems"))
check("/help resume and /help finishupdates are /help pause",
      all(page_of(a) == bot.help_page_text("pause") for a in ("resume", "finishupdates")))
check("a typed /help_pause is not a page: it has no handler, so it falls to the catch-all",
      all(f"help_{n}" not in BY_NAME for n in list(PAGES) + list(ALIASES) + ["nonsense"]))

print("\n== a tap on a page button ==")
for name in PAGES:
    press(OWNER, "helpdet:" + name)
    check(f"{name}: a tap by the owner sends exactly one new message, the page, to the help message's chat",
          sent == [(HELP_CHAT, bot.help_page_text(name))], sent)
    check(f"{name}: ... as plain text with previews off and no keyboard",
          len(sent_kwargs) == 1 and "parse_mode" not in sent_kwargs[0] and "reply_markup" not in sent_kwargs[0]
          and previews_off(sent_kwargs[0]), sent_kwargs)
    check(f"{name}: ... answered once, without an alert, and the help message itself is not edited",
          answered == [((), {})] and edits == [] and replies == [], (answered, edits, replies))
press(OWNER, "helpdet:nosuchpage")
check("a forged tap helpdet:nosuchpage is answered with an alert and sends nothing",
      sent == [] and replies == [] and edits == []
      and answered == [(("That help page no longer exists. Send /help.",), {"show_alert": True})], (answered, sent))
for forged in ("helpdet:warn", "helpdet:nightly", "helpdet:help", "helpdet:pause\n"):
    press(OWNER, forged)
    check(f"{forged!r}: an alias or a non-page is not a page either", sent == [] and len(answered) == 1
          and answered[0][1] == {"show_alert": True}, (answered, sent))
for data in ("helpdet:", "helpdet:Pause", "helpdet:pause:x", "helpdet:" + "a" * 21):
    check(f"{data!r} does not match the pattern, so no handler takes it", press(OWNER, data) is None and sent == [])

print("\n== a stranger ==")
for label, command, args in (("/help", "help", ()), ("/start", "start", ()), ("/help pause", "help", ("pause",)),
                             ("/help warn", "help", ("warn",)), ("/help sql", "help", ("sql",))):
    reset()
    got = run(handler_of(command), STRANGER, *args)
    check(f"{label}: a stranger gets 'This is a private bot.' and nothing else", len(got) == 1 and got[0][0] == PRIVATE
          and "parse_mode" not in got[0][1], got)
for name in list(PAGES) + list(ALIASES):
    reset()
    got = run(handler_of("help"), STRANGER, name)
    check(f"/help {name}: a stranger gets 'This is a private bot.', no page", len(got) == 1 and got[0][0] == PRIVATE
          and "Everything: /help" not in got[0][0] and "Example" not in got[0][0], got)
for name in list(PAGES) + ["nosuchpage"]:
    reset()
    press(STRANGER, "helpdet:" + name)
    check(f"a tap on {name} by a stranger: guard refuses it, no page is sent to anyone, nothing is answered or edited",
          all(chat == OWNER and "Everything: /help" not in text and "Example" not in text for chat, text in sent)
          and answered == [] and edits == [] and replies == [], (sent, answered, edits, replies))
reset()
run(handler_of("help"), STRANGER, "gift")
check("... and the owner is told somebody found the bot, once, with nobody named",
      len(sent) == 1 and sent[0][0] == OWNER and str(STRANGER) not in sent[0][1] and len(logged) == 1, (sent, logged))
reset()
press(STRANGER, "helpdet:gift")
check("... a tap does the same, and sends the owner that one line only",
      len(sent) == 1 and sent[0][0] == OWNER and str(STRANGER) not in sent[0][1] and len(logged) == 1, (sent, logged))
reset()
got = run(handler_of("help"), OWNER, "gift")
check("the owner is not asked anything and nobody is told", len(got) == 1 and sent == [] and logged == [])
reset()
press(OWNER, "helpdet:gift")
check("... a tap by the owner likewise: the page and nothing else", len(sent) == 1 and sent[0][0] == HELP_CHAT and logged == [])

print("\n== wired in the right place, and in no menu ==")
catch_all = next(i for i, (names, cb) in enumerate(WIRED) if cb is bot.unknown_command)
help_at = next(i for i, (names, cb) in enumerate(WIRED) if "help" in names)
check("no help_<x> command is registered at all", not [n for names, _ in WIRED for n in names if n.startswith("help_")])
page_buttons = [(i, pattern) for i, pattern, cb in BUTTONS if cb is bot.help_page_button]
check("the page button handler is registered once, with the pattern r'^helpdet:[a-z]{1,20}$'",
      len(page_buttons) == 1 and page_buttons[0][1] == HELP_PATTERN, page_buttons)
at = page_buttons[0][0] if page_buttons else -1
check("... and no button handler before it takes a 'helpdet:' tap (none has no pattern, none matches)",
      not [p for i, p, cb in BUTTONS if i < at and (p is None or re.match(p, "helpdet:pause"))],
      [(i, p) for i, p, cb in BUTTONS if i < at])
check("... and any catch-all callback handler (one with no pattern, or one matching anything) comes after it",
      all(at < i for i, p, cb in BUTTONS if p is None or re.match(p, "a tap nobody has a button for")))
check("... and ping's buttons still go to their own handler", press(OWNER, "pingdet:1:2:*") is bot.ping_detail_button)
menu = [c.command for c in bot.BOT_COMMANDS] + [c.command for c in bot.STRANGER_COMMANDS]
check("no /help_ page is in the owner's menu or the stranger's", not [m for m in menu if m.startswith("help_")], menu)
check("no alias is in them either (the menu has /pause, /warn and /resume as themselves)",
      "help_warn" not in menu and "help_nightly" not in menu)
check("the stranger is still offered /start and nothing else", [c.command for c in bot.STRANGER_COMMANDS] == ["start"])

print("\n-- the same, read from the source (ast), as tests/test_menu.py reads the public bots")
source = (REPO / "manager_bot" / "bot.py").read_text(encoding="utf-8")
tree = ast.parse(source)


def module_dict(name):
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == name for t in node.targets):
            return node.value
    return None


pages_node, aliases_node = module_dict("HELP_PAGES"), module_dict("HELP_ALIASES")
check("HELP_PAGES is a module-level dict literal with string keys, and there is a page",
      isinstance(pages_node, ast.Dict) and pages_node.keys and all(isinstance(k, ast.Constant) and isinstance(k.value, str)
                                                                  for k in pages_node.keys))
check("HELP_ALIASES is a module-level dict literal with string keys and string values",
      isinstance(aliases_node, ast.Dict) and all(isinstance(k, ast.Constant) and isinstance(v, ast.Constant)
                                                 for k, v in zip(aliases_node.keys, aliases_node.values)))
page_keys = [k.value for k in pages_node.keys]
alias_map = {k.value: v.value for k, v in zip(aliases_node.keys, aliases_node.values)}
check("every alias in the source maps to a page", all(v in page_keys for v in alias_map.values()))
check("every page key is a command ManagerBot handles (a page is for a command)",
      all(k in ast_handled for k in page_keys), [k for k in page_keys if k not in ast_handled])
check("every alias is a command ManagerBot handles", all(k in ast_handled for k in alias_map),
      [k for k in alias_map if k not in ast_handled])


def menu_names(variable):
    out = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == variable for t in node.targets):
            out += [e.args[0].value for e in node.value.elts if isinstance(e, ast.Call) and e.args]
    return out


check("none of the /help_ names is in BOT_COMMANDS or STRANGER_COMMANDS in the source",
      not [m for m in menu_names("BOT_COMMANDS") + menu_names("STRANGER_COMMANDS") if m.startswith("help_")])
loops = [n for n in ast.walk(tree) if isinstance(n, ast.For) and "CommandHandler(f'help_" in ast.unparse(n)]
check("no loop registers CommandHandler(f'help_{name}', ...) any more", not loops, [ast.unparse(n)[:80] for n in loops])
button_calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "CallbackQueryHandler"
                and n.args and getattr(n.args[0], "id", None) == "help_page_button"]
check("main() has CallbackQueryHandler(help_page_button, pattern=r'^helpdet:[a-z]{1,20}$')",
      len(button_calls) == 1 and [ast.literal_eval(k.value) for k in button_calls[0].keywords if k.arg == "pattern"] == [HELP_PATTERN],
      [ast.unparse(n) for n in button_calls])
button_def = next((n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == "help_page_button"), None)
check("help_page_button asks guard first, as ping_detail_button does",
      button_def is not None and ast.unparse(button_def.body[1 if ast.get_docstring(button_def) else 0]) == (
          "if not await guard(update, context):\n    return"), ast.unparse(button_def)[:200] if button_def else None)
check("ManagerBot has the one /help and its pages: no /fullhelp (that is the public bots')", "fullhelp" not in BY_NAME)

print("\n== plain text, and no preview ==")
check("a page is sent without parse_mode (it holds < and >), with previews off, by /help <name> and by its button",
      all("parse_mode" not in run(handler_of("help"), OWNER, n)[0][1] for n in PAGES)
      and all(press(OWNER, "helpdet:" + n) and "parse_mode" not in sent_kwargs[0] and previews_off(sent_kwargs[0]) for n in PAGES))
check("the pages do name things in angle brackets, which is why",
      all("<" in bot.help_page_text(n) for n in ("gift", "broadcast", "balance", "addcredit", "run", "logs", "problems", "say")))
check("NO_PREVIEW switches previews off", previews_off({"link_preview_options": bot.NO_PREVIEW}))

# ---------------------------------------------------------------------------
# computed, not typed
# ---------------------------------------------------------------------------

print("\n== computed, not typed: change what a page reads, and the page changes ==")
SENTINEL = {
    "pause": [(lifecycle, "DEFAULT_MAINTENANCE_MINUTES", 4217, "about 4217 minutes"),
              (lifecycle, "DEFAULT_MAINTENANCE_MINUTES", 4217, "people told 4217 minutes"),
              (bot, "COMMAND_TIMEOUT_SECONDS", 7311, "after 7311 seconds")],
    "gift": [(fl, "GIFT_MAX_CREDIT", 4321, "from 1 to 4321"), (fl, "GIFT_MAX_DAYS", 6543, "up to 6543 days ahead"),
             (fl, "GIFT_KEY_RULE", "THE KEY RULE, WORDED NEW.", "THE KEY RULE, WORDED NEW. A key is given")],
    "promo": [(fl, "PROMO_CREDITS", 4747, "the 4747 ⚡ gift"), (fl, "PROMO_USERS", 3535, "Not started, 3535 reached"),
              (fl, "PROMO_CLAIM_DAYS", 61, "stays open for 61 days"), (fl, "PROMO_SPEND_DAYS", 71, "spent for 71 days from the day"),
              (bot, "PROMO_COUNT_SECONDS", 7 * 60, "Every 7 minutes")],
    "broadcast": [(fl, "BROADCAST_ACTIVE_DAYS", 1234, "in the last 1234 days"),
                  (fl, "BROADCAST_PER_SECOND", 31.0, "at most 31 messages a second"),
                  (fl, "BROADCAST_PER_SECOND", 12.5, "at most 12.5 messages a second")],
    "balance": [(bot, "BALANCE_OVERVIEW_ROWS", 3141, "with the 3141 largest balances"),
                (bot, "BALANCE_HISTORY_ROWS", 2718, "their last 2718 movements")],
    "run": [(bot, "COMMAND_TIMEOUT_SECONDS", 7312, "No answer in 7312 seconds")],
    "logs": [(fl, "LOG_TAIL_DEFAULT", 4041, "4041 if not said"), (fl, "LOG_TAIL_MAX", 5051, "at most 5051")],
    "problems": [(bot, "NIGHTLY_REPORT_HOURS", 4242, "4242 if not said"), (bot, "PROBLEMS_MAX_HOURS", 5353, "from 1 to 5353"),
                 (bot, "NIGHTLY_REPORT_AT_UTC", "03:45", "every day at 03:45 UTC")],
    "events": [(bot, "EVENTS_DEFAULT", 1717, "1717 if not said"), (bot, "EVENTS_MAX", 6161, "at most 6161")],
    "feedback": [(bot, "FEEDBACK_LIST_ROWS", 1919, "the latest 1919, newest first"),
                 (bot, "FEEDBACK_SEARCH_ROWS", 5757, "among the latest 5757"),
                 (fl, "FEEDBACK_RETENTION_DAYS", 3737, "kept 3737 days")],
    "sql": [(bot.db, "SQL_TIMEOUT_SECONDS", 1818, "stops it after 1818 seconds"),
            (bot.db, "SQL_MAX_ROWS", 5959, "At most 5959 rows")],
}
for page, rows in SENTINEL.items():
    for module, attr, value, expected in rows:
        with changed(module, attr, value):
            text = bot.help_page_text(page)
        check(f"{page}: {attr} = {value!r} shows ('{expected}')", expected in text, text[:0])
        check(f"{page}: ... and is back to what it was afterwards", expected not in bot.help_page_text(page))
check("the key rule on the gift page is the sentence _gift_args prints for a bad key",
      fl.GIFT_KEY_RULE in bot.help_page_text("gift") and fl._gift_args(["Bad_Key", "100", "2099-01-01"]) == fl.GIFT_KEY_RULE)
check("... and with the real values the pages say what the code says (30 days, 20 a second, 1000 credits, 400 days)",
      f"{fl.GIFT_MAX_CREDIT}" in bot.help_page_text("gift") and f"{fl.GIFT_MAX_DAYS} days ahead" in bot.help_page_text("gift")
      and f"{fl.BROADCAST_ACTIVE_DAYS} days" in bot.help_page_text("broadcast")
      and f"{fl.BROADCAST_PER_SECOND:g} messages a second" in bot.help_page_text("broadcast")
      and f"about {lifecycle.DEFAULT_MAINTENANCE_MINUTES} minutes" in bot.help_page_text("pause"))

print("\n-- the constants that were literals inside commands: same values, and the command reads them")
check("LOG_TAIL_DEFAULT is 40 and LOG_TAIL_MAX is 500, as the literals were", (fl.LOG_TAIL_DEFAULT, fl.LOG_TAIL_MAX) == (40, 500))
check("BALANCE_OVERVIEW_ROWS 20, BALANCE_HISTORY_ROWS 12, PROBLEMS_MAX_HOURS 168",
      (bot.BALANCE_OVERVIEW_ROWS, bot.BALANCE_HISTORY_ROWS, bot.PROBLEMS_MAX_HOURS) == (20, 12, 168))
check("EVENTS_DEFAULT 15, EVENTS_MAX 60, FEEDBACK_LIST_ROWS 10, FEEDBACK_SEARCH_ROWS 50",
      (bot.EVENTS_DEFAULT, bot.EVENTS_MAX, bot.FEEDBACK_LIST_ROWS, bot.FEEDBACK_SEARCH_ROWS) == (15, 60, 10, 50))
check("SQL_TIMEOUT_SECONDS 10 and SQL_MAX_ROWS 50", (bot.db.SQL_TIMEOUT_SECONDS, bot.db.SQL_MAX_ROWS) == (10, 50))
check("GIFT_KEY_RULE is the sentence the command always said",
      fl.GIFT_KEY_RULE == "The key is 3-40 lowercase letters, digits or dashes, starting with a letter or digit.")

# /logs, on the bots' side: the tail it prints follows the constants
logs_dir = pathlib.Path(fl.__file__).resolve().parent / "logs"
if not logs_dir.exists():
    memory = fl._RECENT_LOGS["errors.log"].lines
    saved_lines = list(memory)
    memory.clear()
    memory.extend(f"line {i}" for i in range(1, 801))
    try:
        def tail_of(*args):
            text, _, _ = run_async(fl._cmd_logs(None, list(args)))
            return [l for l in text.splitlines() if l.startswith("line ")]
        check("/logs with no number prints LOG_TAIL_DEFAULT lines (40)", len(tail_of()) == 40, len(tail_of()))
        check("/logs 9999 prints at most LOG_TAIL_MAX lines (500)", len(tail_of("9999")) == 500, len(tail_of("9999")))
        with changed(fl, "LOG_TAIL_DEFAULT", 7):
            check("the default is read from LOG_TAIL_DEFAULT: set to 7, /logs prints 7", len(tail_of()) == 7, len(tail_of()))
        with changed(fl, "LOG_TAIL_MAX", 9):
            check("the most is read from LOG_TAIL_MAX: set to 9, /logs 9999 prints 9", len(tail_of("9999")) == 9, len(tail_of("9999")))
    finally:
        memory.clear()
        memory.extend(saved_lines)
else:
    check("(this host keeps log files, so the printed tail is not exercised here; test_logs.py reads them)", True)

# /balance, /events, /problems, /feedback, /sql: the command reads the constant the page prints
print("\n-- ... and so do /balance, /events, /problems, /feedback and /sql")
calls, stubs = [], []


def stub(target, name, fn):
    """Replace target.name until the end of this section."""
    stubs.append((target, name, getattr(target, name)))
    setattr(target, name, fn)


totals = {"outstanding": 0, "wallets": 0, "topped_up": 0, "spent": 0, "stars_paid": 0, "bonus": 0, "balance": 0,
          "bonus_next_amount": 0, "bonus_next_expiry": None}
try:
    stub(fl, "star_balance_overview", lambda limit: (calls.append(("overview", limit)), (totals, []))[1])
    stub(fl, "star_totals", lambda uid: totals)
    stub(fl, "star_ledger_for", lambda uid, limit: (calls.append(("ledger", limit)), [])[1])
    stub(bot.db, "recent_events", lambda limit, bot_id=None: (calls.append(("events", limit)), [])[1])
    stub(fl, "recent_problem_reports_since", lambda hours: (calls.append(("problems", hours)), [])[1])
    stub(fl, "feedback_count_since", lambda hours: 0)
    stub(fl, "settings_with_prefix", lambda prefix: {})
    stub(bot, "build_problem_report", lambda *a, **k: "the report")
    stub(fl, "recent_feedback", lambda limit: (calls.append(("feedback", limit)), [])[1])
    stub(bot.db, "get_setting", lambda key, default=None: "instant")
    stub(bot.db, "run_readonly_query", lambda query, *a: (["n"], [(1,)]))

    def limit_of(kind, handler, *args, **kw):
        calls.clear()
        out = run(handler, OWNER, *args, **kw)
        return ([n for k, n in calls if k == kind], out)

    check("/balance asks for BALANCE_OVERVIEW_ROWS (20) largest balances", limit_of("overview", handler_of("balance"))[0] == [20])
    with changed(bot, "BALANCE_OVERVIEW_ROWS", 7):
        check("... set to 7, it asks for 7", limit_of("overview", handler_of("balance"))[0] == [7])
    check("/balance <id> asks for BALANCE_HISTORY_ROWS (12) movements", limit_of("ledger", handler_of("balance"), "5")[0] == [12])
    with changed(bot, "BALANCE_HISTORY_ROWS", 5):
        check("... set to 5, it asks for 5", limit_of("ledger", handler_of("balance"), "5")[0] == [5])
    check("/events asks for EVENTS_DEFAULT (15)", limit_of("events", handler_of("events"))[0] == [15])
    check("/events 9999 asks for EVENTS_MAX (60) at most", limit_of("events", handler_of("events"), "9999")[0] == [60])
    check("/events 3 asks for 3", limit_of("events", handler_of("events"), "3")[0] == [3])
    with changed(bot, "EVENTS_DEFAULT", 4):
        check("... EVENTS_DEFAULT set to 4: /events asks for 4", limit_of("events", handler_of("events"))[0] == [4])
    with changed(bot, "EVENTS_MAX", 8):
        check("... EVENTS_MAX set to 8: /events 9999 asks for 8", limit_of("events", handler_of("events"), "9999")[0] == [8])
    check("/problems 9999 looks back PROBLEMS_MAX_HOURS (168) at most", limit_of("problems", handler_of("problems"), "9999")[0] == [168])
    check("/problems 0 looks back 1 hour at least", limit_of("problems", handler_of("problems"), "0")[0] == [1])
    check("/problems with no number looks back NIGHTLY_REPORT_HOURS", limit_of("problems", handler_of("problems"))[0] == [bot.NIGHTLY_REPORT_HOURS])
    with changed(bot, "PROBLEMS_MAX_HOURS", 9):
        check("... PROBLEMS_MAX_HOURS set to 9: /problems 9999 looks back 9", limit_of("problems", handler_of("problems"), "9999")[0] == [9])
    check("/nightly is the same command as /problems", handler_of("nightly") is handler_of("problems"))
    bot._feedback_mode_cache.update(value="instant", read_at=time_module.monotonic())
    check("/feedback lists FEEDBACK_LIST_ROWS (10)", limit_of("feedback", handler_of("feedback"))[0] == [10])
    with changed(bot, "FEEDBACK_LIST_ROWS", 6):
        check("... set to 6, it lists 6", limit_of("feedback", handler_of("feedback"))[0] == [6])
    got, out = limit_of("feedback", handler_of("feedback"), "#31")
    check("/feedback #31 looks among FEEDBACK_SEARCH_ROWS (50), and says so when there is nothing",
          got == [50] and out[0][0] == "No feedback #31 among the latest 50.", (got, out))
    with changed(bot, "FEEDBACK_SEARCH_ROWS", 8):
        got, out = limit_of("feedback", handler_of("feedback"), "31")
        check("... set to 8, it looks among 8 and says 8", got == [8] and out[0][0] == "No feedback #31 among the latest 8.", (got, out))
    sent.clear()
    run(handler_of("sql"), OWNER, text="/sql SELECT 1")
    check("/sql says how many rows it is capped at, from SQL_MAX_ROWS (50)", any("capped at 50)" in t for _, t in sent), sent)
    with changed(bot.db, "SQL_MAX_ROWS", 11):
        sent.clear()
        run(handler_of("sql"), OWNER, text="/sql SELECT 1")
        check("... set to 11, it says 11", any("capped at 11)" in t for _, t in sent), sent)
finally:
    for target, name, old in reversed(stubs):
        setattr(target, name, old)

check("the query runner's row cap is SQL_MAX_ROWS", inspect.signature(bot.db.run_readonly_query).parameters["limit"].default
      == bot.db.SQL_MAX_ROWS == 50)
runner = (REPO / "manager_bot" / "db.py").read_text(encoding="utf-8")
runner = runner.split("def run_readonly_query", 1)[1].split("\ndef ", 1)[0]
check("... and the row cap is named where it is used: no 50 written in the runner's signature",
      "def run_readonly_query(sql: str, limit: int = SQL_MAX_ROWS):" in (REPO / "manager_bot" / "db.py").read_text(encoding="utf-8"))
check("... and its statement timeout is SQL_TIMEOUT_SECONDS, not a number written there",
      "SET LOCAL statement_timeout = '{SQL_TIMEOUT_SECONDS}s'" in runner and not re.search(r"statement_timeout = '\d", runner))


class RecordingConnection:
    def __init__(self):
        self.statements = []

    def execute(self, sql, *a, **k):
        self.statements.append(sql)
        return SimpleNamespace(description=None, fetchmany=lambda n: [])

    def rollback(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


recorder = RecordingConnection()
real_pooled = bot.db.pooled
bot.db.pooled = lambda: recorder
try:
    with changed(bot.db, "SQL_TIMEOUT_SECONDS", 3):
        bot.db.run_readonly_query("SELECT 1")
finally:
    bot.db.pooled = real_pooled
check("the runner puts SQL_TIMEOUT_SECONDS into the statement timeout it sets (3 -> '3s'), read-only first",
      "SET LOCAL statement_timeout = '3s'" in recorder.statements and recorder.statements[0] == "SET TRANSACTION READ ONLY",
      recorder.statements)

# ---------------------------------------------------------------------------
# the --active broadcast card
# ---------------------------------------------------------------------------

print("\n== /broadcast --active says who it reaches ==")
real_targets = bot.CHILDREN
broadcast = handler_of("broadcast")
pending = {}
got = run(broadcast, OWNER, "all", "hello", "there", user_data=pending)
card = got[0][0]
check("an ordinary broadcast: 'to everyone they know', the text, 'cannot be taken back'",
      "to everyone they know?" in card and "<pre>hello there</pre>" in card and "This cannot be taken back." in card
      and "active in the last" not in card, card)
check("... queued as typed, nothing sent yet", pending[bot.BROADCAST_PENDING]["text"] == "hello there" and queued == [])
check("... with 'Send it' and 'Cancel'",
      [b.args[0] for row in got[0][1]["reply_markup"].inline_keyboard for b in row] == ["Send it", "Cancel"])
pending = {}
got = run(broadcast, OWNER, "all", "--active", "The", "bots", "stop", "at", "22:00", user_data=pending)
card = got[0][0]
days = fl.BROADCAST_ACTIVE_DAYS
check(f"--active: 'to everyone active in the last {days} day(s)' instead of 'they know'",
      f"to everyone active in the last {days} day(s)?" in card and "they know" not in card, card)
check("... the text is shown without the flag", "<pre>The bots stop at 22:00</pre>" in card and "--active" not in card, card)
check("... the queued text still has the flag, as the child bot reads it",
      pending[bot.BROADCAST_PENDING]["text"] == "--active The bots stop at 22:00", pending)
with changed(fl, "BROADCAST_ACTIVE_DAYS", 11):
    got = run(broadcast, OWNER, "stick", "--active", "hi", user_data={})
    check("... N is family_link.BROADCAST_ACTIVE_DAYS, read now: 11 day(s), to one bot named by its start",
          "Send as StickerBot, to everyone active in the last 11 day(s)?" in got[0][0], got[0][0])
check("--active only counts as the first word of the text: 'hello --active' is an ordinary broadcast",
      "they know" in run(broadcast, OWNER, "all", "hello", "--active", user_data={})[0][0])
check("... and '--activeX' is not the flag", "they know" in run(broadcast, OWNER, "all", "--activeX", "hi", user_data={})[0][0])
pending = {}
got = run(broadcast, OWNER, "all", "--active", user_data=pending)
check("--active with no words after it: 'Nothing to say', no card, nothing waiting",
      len(got) == 1 and got[0][0] == "Nothing to say — give it some text." and pending == {} and "reply_markup" not in got[0][1], got)
got = run(broadcast, OWNER, "all", user_data={})
check("a bot and no text: the usage line, which now shows [--active]",
      got[0][0] == f"Usage: /broadcast &lt;bot|all&gt; [--active] &lt;text&gt;\nKnown: {bot.bot_list_hint()}", got)
got = run(broadcast, OWNER, "hello", "there", user_data={})
check("'/broadcast hello there' is refused, because hello is not a bot (as the broadcast page says)",
      got[0][0].startswith("No such bot: hello.") and "reply_markup" not in got[0][1], got)
pending = {}
run(broadcast, OWNER, "convert", "--active", "x  y", user_data=pending)
reset()
tap(bot.broadcast_button, OWNER, "upd:send", pending)
check("Send it queues the text with the flag for the bot, and says where",
      queued == [("convertbot", "broadcast", "--active x  y")] and edits and edits[0].startswith("📢 Queued to convertbot."), (queued, edits))
reset()
tap(bot.broadcast_button, OWNER, "upd:send", {})
check("Send it with nothing waiting sends nothing", queued == [] and "no longer waiting" in edits[0])
check("the page's first claim holds: a newer /broadcast replaces the one waiting",
      (lambda ud: (run(broadcast, OWNER, "all", "first", user_data=ud), run(broadcast, OWNER, "all", "second", user_data=ud),
                   ud[bot.BROADCAST_PENDING]["text"])[2])({}) == "second")

# ---------------------------------------------------------------------------
# the text fixes the pages depend on
# ---------------------------------------------------------------------------

print("\n== the text fixes (8.3 of the spec) ==")
flat = re.sub(r"\n\s+", " ", bot.HELP)


def entry(prefix):
    """HELP's entry that starts with `prefix`, joined onto one line ('' if there is none)."""
    start = flat.find(prefix)
    return "" if start < 0 else flat[start:flat.index("\n", start)]


check("1. HELP: /problems takes [hours] and not [mine]", "/problems [hours] — what the bots showed people" in flat
      and "/problems [hours] [mine]" not in flat)
reports = entry("/reports [n] [mine]")
check("1. HELP: /reports has a line of its own, with what it takes and what mine does",
      "newest first" in reports and "incident code" in reports and "mine: only those somebody attached their details to" in reports, reports)
order = [flat.find(p) for p in ("/problems [hours]", "/reports [n] [mine]", "/report &lt;incident&gt;")]
check("1. ... right after the /problems entry and before /report", -1 not in order and order == sorted(order), order)
check("1. HELP: 'Still works' says /nightly → /problems, and no longer /reports", "/nightly → /problems · /finishupdates → /resume" in flat
      and "/nightly and /reports" not in flat)
check("1. /reports is a command of its own, in the menu, so it is not 'still works'",
      "reports" in BY_NAME and "reports" in [c.command for c in bot.BOT_COMMANDS])
check("1. the /reports sentence on the problems page says the same as HELP's line (newest first, incident code, mine)",
      all(w in bot.REPORTS_LINE for w in ("/reports [n]", "newest first", "incident code", "/reports mine", "attached their details"))
      and bot.REPORTS_LINE in bot.help_page_text("problems"))
check("2. HELP: /logs <bot> [n] [bot|problems] — tail a log: errors by default, \"bot\" or \"problems\" for the other two",
      '/logs &lt;bot&gt; [n] [bot|problems] — tail a log: errors by default, "bot" or "problems" for the other two' in flat
      and "say \"bot\" or \"problems\"" not in flat)
got = run(handler_of("logs"), OWNER)
check("2. /logs with nothing says: Usage: /logs <bot> [lines] [bot|problems]", got[0][0] == "Usage: /logs <bot> [lines] [bot|problems]", got)
check("3. HELP: /broadcast <bot|all> [--active] <text>", "/broadcast &lt;bot|all&gt; [--active] &lt;text&gt; — one message to everyone" in flat
      and "/broadcast &lt;bot|all&gt; &lt;text&gt;" not in flat)
got = run(handler_of("broadcast"), OWNER, "all")
check("3. /broadcast's usage: [--active] shown, HTML-escaped", "[--active] &lt;text&gt;" in got[0][0], got)
reset()
got = run(handler_of("pause"), OWNER, "all", "30")
check("4. /pause's reply says 'until /resume', not /finishupdates", "They stay paused until /resume, however many deploys that takes." in got[0][0]
      and "/finishupdates" not in got[0][0] and len(queued) == 4, got)
got = run(handler_of("resume"), OWNER, "nobot")
check("4. /resume with a bot that does not exist: 'Usage: /resume [bot|all]'", got[0][0].startswith("Usage: /resume [bot|all]. Known: "), got)
check("4. /finishupdates is the same handler as /resume", handler_of("finishupdates") is handler_of("resume"))
check("4. no text of the bot still sends the owner to /finishupdates, except to say it is the old name",
      source.count("until /finishupdates") == 0 and source.count('Usage: /finishupdates') == 0)
check("4. the page says it is the old name of /resume, and HELP still lists it under 'Still works'",
      "/finishupdates is the old name of /resume." in bot.help_page_text("pause") and "/finishupdates → /resume" in flat)

# ---------------------------------------------------------------------------
# each fact a page states about the code, where the code can be asked
# ---------------------------------------------------------------------------

print("\n== what the pages say the code does ==")
real.heartbeat = bot.db.heartbeat_of
bot.db.heartbeat_of = lambda bot_id: None      # no bot has said what it answers: the shared commands are what it takes


def queue_of(handler, *args, **kw):
    """What a command of the owner's put on the bots' queue: [(bot, command, args)], and what it replied."""
    reset()
    out = run(handler, OWNER, *args, **kw)
    return list(queued), out


CHILD_IDS = [b["id"] for b in bot.CHILDREN]
check("ManagerBot knows the four public bots, and it is not one of them", len(CHILD_IDS) == 4 and bot.BOT_NAME not in CHILD_IDS)
check("pause: all four bots, one bot by the start of its name, never ManagerBot, and 'hello' is no bot",
      len(bot._targets("all")) == 4 and [b["id"] for b in bot._targets("stick")] == ["stickerbot"]
      and bot._targets("manager") is None and bot._targets("managerbot") is None and bot._targets("hello") is None)
got, _ = queue_of(handler_of("pause"))
check("pause: /pause is all four bots, and tells them no number (they quote their own default)",
      got == [(bid, "pause", "") for bid in CHILD_IDS], got)
got, _ = queue_of(handler_of("pause"), "30")
check("pause: /pause 30 is all four, 30 minutes", got == [(bid, "pause", "30") for bid in CHILD_IDS], got)
got, _ = queue_of(handler_of("pause"), "convert")
check("pause: /pause convert is one bot", got == [("convertbot", "pause", "")], got)
got, out = queue_of(handler_of("pause"), "convert", "30")
check("pause: /pause convert 30 is one bot, 30 minutes", got == [("convertbot", "pause", "30")], got)
check("pause: ... and the reply tells how long people are told: 30 minutes", "come back in about 30 minute(s)" in out[0][0], out)
check("pause: with no number the reply promises the default the page prints",
      f"about {lifecycle.DEFAULT_MAINTENANCE_MINUTES} minute(s)" in queue_of(handler_of("pause"))[1][0][0])
got, _ = queue_of(handler_of("warn"), "stick")
check("pause: /warn [bot|all] queues warnbusy to that bot", got == [("stickerbot", "warnbusy", "")], got)
got, _ = queue_of(handler_of("resume"), "all")
check("pause: /resume [bot|all] queues resume to every bot", got == [(bid, "resume", "") for bid in CHILD_IDS], got)
got, _ = queue_of(handler_of("finishupdates"), "anon")
check("pause: /finishupdates is the old name of /resume", got == [("anonbot", "resume", "")], got)

tomorrow = (datetime.now(timezone.utc) + timedelta(days=30)).strftime("%Y-%m-%d")
cards = {}
got, out = queue_of(handler_of("gift"), "all", "welcome-200", "200", tomorrow, user_data=cards)
check("gift: every bot is first asked only to count (no --go), with the key, the credits and the day",
      got == [(bid, "gift", f"welcome-200 200 {tomorrow}") for bid in CHILD_IDS], got)
check("gift: the card has 'Give it' and 'Cancel', and says nothing is given until Give it",
      [b.args[0] for row in out[0][1]["reply_markup"].inline_keyboard for b in row] == ["Give it", "Cancel"]
      and "Nothing is given until you press <b>Give it</b>" in out[0][0], out)
check("gift: the page's example key and amount are ones the command's parser takes",
      isinstance(fl._gift_args(["welcome-200", "200", tomorrow]), tuple))
check("gift: the key 'welcome-200' is what the key rule allows; 'Welcome 200' is not",
      isinstance(fl._gift_args(["welcome-200", "200", tomorrow]), tuple) and fl._gift_args(["Welcome 200", "200", tomorrow]) == fl.GIFT_KEY_RULE)
check("balance: a bare number is refused on purpose; +, - and = are the three forms",
      bot._parse_balance_change("500") is None and bot._parse_balance_change("+500") == ("add", 500)
      and bot._parse_balance_change("-100") == ("add", -100) and bot._parse_balance_change("=0") == ("set", 0))
got, out = queue_of(handler_of("logs"), "convert", "100", "problems")
check("logs: /logs convert 100 problems asks ConvertBot for 'logs 100 problems'", got == [("convertbot", "logs", "100 problems")], got)
check("logs: /errorlog with no bot asks which, with a button for each of the four bots",
      queue_of(handler_of("errorlog"))[1][0][0] == "Whose errors.log?" and queued == []
      and len([b for row in replies[0][1]["reply_markup"].inline_keyboard for b in row]) == 4)
fl_source = (REPO / "shared" / "family_link.py").read_text(encoding="utf-8")
check("logs: 'bot' and 'all' pick bot.log, 'problems' problems.log, anything else errors.log (the bot's side, read off the source)",
      all(s in fl_source for s in ('args[-1] in ("bot", "all")', 'args[-1] in ("problems", "problem")', 'which = "errors.log"')))
got, out = queue_of(handler_of("run"), "managerbot", "status")
check("run: ManagerBot itself is refused, and nothing is queued", got == [] and out[0][0].startswith("That one is me."), (got, out))
got, out = queue_of(handler_of("run"), "convert", "nonsense")
check("run: a command the bot does not know is refused before anything is sent", got == [] and "has no 'nonsense'" in out[0][0], (got, out))
got, out = queue_of(handler_of("run"), "convert", "status")
check("run: /run convert status is queued, and you are told 'queued as #n'",
      got == [("convertbot", "status", "")] and out[0][0] == "→ ConvertBot · status — queued as #1", (got, out))
out = queue_of(handler_of("run"))[1]
check("run: /run on its own lists the bots and every command", "<b>Bots:</b>" in out[0][0] and "<b>Commands:</b>" in out[0][0]
      and all(f"<code>{name}</code>" in out[0][0] for name in fl.COMMANDS if name in fl.COMMAND_HELP))
got, out = queue_of(handler_of("say"), "all", "123", "hi")
check("say: never all", got == [] and out[0][0].startswith("No such bot: all"), (got, out))
got, _ = queue_of(handler_of("say"), "convert", "123", "The", "fix", "is", "live")
check("say: one bot, the id, and the words joined with single spaces (line breaks and double spaces cannot survive)",
      got == [("convertbot", "message", "123 The fix is live")], got)
check("say: /say with too little says how to use it", queue_of(handler_of("say"), "convert")[1][0][0] == "Usage: /say <bot> <user_id> <text>")
check("say: the bot's side sends plain text and answers 'Sent.' (read off the source)",
      'send_message(chat_id=int(args[0]), text=" ".join(args[1:]))' in fl_source and 'return "Sent.", None, None' in fl_source)
check("broadcast: the bot's side sends plain text, and says it cannot tell who is active",
      "send_message(chat_id=chat_id, text=text)" in fl_source and "This bot cannot tell who is active" in fl_source)
bot.db.heartbeat_of = real.heartbeat

bot.db.queue_command, bot.edit_in_place, bot.db.log_event = real.queue, real.edit, real.log
LOOP.close()

print()
if failures:
    print(f"FAILED ({len(failures)} of {checks}):")
    for name in failures:
        print("  -", name)
    sys.exit(1)
print(f"{checks} checks. Thirteen pages and four aliases: owner-only, plain text, out of the menus, and every number read from the code that enforces it.")
