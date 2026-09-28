"""TestBot's switchboard: which bot gets an update, and what reaches Telegram.

    python tests/test_switch.py

testbot/switch.py runs all five bots on one token and one poller. This covers
the part that is its own logic -- no bot is loaded, nothing goes to Telegram:

  /asConvert, /convertbot and the rest switch, and nothing else is taken
  from the bot underneath (/ask, /assets, another bot's @mention, a stranger);
  a button goes to the bot that drew it, a reply to the bot that wrote the
  message, a payment to the bot that sent the invoice, the rest to the current;
  only the current bot's menu and profile reach Telegram, with /as added,
  and a switch changes one thing -- the menu in the tester's own chat --
  because Telegram's rate limit punishes more;
  on either database each bot runs under its test id, never the real bot's
  name -- which is what keeps a test run off the real bots' lease,
  maintenance flag and restart marker -- and keeps what the real bots keep in
  `family` in testbot_family instead, its wallet among it;
  Stars are sandboxed unless token.env says otherwise;
  settings come from token.env, the environment winning;
  one TestBot at a time on this machine;
  a real bot's token is refused, and on the shared database so is any login
  that could write outside testbot_* -- asked of a database it cannot reach,
  it refuses rather than guesses. What such a login can and cannot do is
  tests/testbot_role_scenarios.py, against Postgres.

The other half -- that the five real bots load side by side and each serves
its own menu -- is `python testbot/switch.py --audit`, which needs the test
token and a database.
"""
import asyncio
import importlib.util
import json
import subprocess
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

spec = importlib.util.spec_from_file_location("testbot_switch", ROOT / "testbot" / "switch.py")
switch = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = switch
spec.loader.exec_module(switch)

from telegram import Bot as TelegramBot, BotCommand, BotCommandScopeAllGroupChats, BotCommandScopeChat  # noqa: E402

failures = []
OWNER, STRANGER, ME = 42, 77, 999


def check(name, ok, detail=""):
    print(("  ok   " if ok else "  FAIL ") + name + ("" if ok else f"\n       {detail}"))
    if not ok:
        failures.append(name)


class Router:
    def __init__(self):
        self.said = []

    async def send_message(self, chat, text, **kwargs):
        self.said.append((chat, text))


class Telegram:
    """Stands in for Bot._post: what was called, and what it answers."""

    def __init__(self):
        self.calls = []
        self.answer = True

    async def __call__(self, bot_object, endpoint, data=None, *, api_kwargs=None, **timeouts):
        self.calls.append((bot_object, endpoint, dict(data or {})))
        return self.answer


def board(running=("sticker", "convert", "downloader", "anon", "manager")):
    bots = [switch.Bot(folder, name, prefix) for folder, name, prefix in switch.BOTS]
    for bot in bots:
        bot.state = "running" if bot.role in running else "failed"
        bot.problem = "" if bot.state == "running" else "MBOT_ADMIN_ID is empty"
        bot.app = types.SimpleNamespace(bot=TelegramBot(f"1{len(bot.role)}:{bot.role}"),
                                        update_queue=asyncio.Queue())
    sb = switch.Switchboard(bots, {"TESTBOT_ADMIN_ID": str(OWNER)})
    for bot in bots:
        sb.by_bot_object[id(bot.app.bot)] = bot
    sb.router, sb.me = Router(), types.SimpleNamespace(id=ME, username="Test_bot")
    return sb, {bot.role: bot for bot in bots}


def message(text, sender=OWNER, chat=OWNER, **extra):
    return dict({"message_id": 1, "text": text, "chat": {"id": chat, "type": "private"},
                 "from": {"id": sender, "is_bot": False, "first_name": "x"}, "date": 0}, **extra)


async def switching():
    print("== switching ==")
    sb, by = board(running=("sticker", "convert", "downloader", "anon"))
    for text, expected in [("/asConvert", "convert"), ("/stickerbot", "sticker"), ("/as_anon", "anon"),
                           ("/ASDOWNLOADER", "downloader"), ("/asDownload", "downloader"),
                           ("/asChat", "anon"), ("/convertbot@Test_bot", "convert"), ("/asSticker@test_BOT", "sticker")]:
        taken = await sb.switch_command(message(text))
        check(f"{text} makes it {by[expected].name}", taken and sb.current is by[expected],
              (taken, sb.current and sb.current.name))
    check("and says so in the chat, with the list", "StickerBot now" in sb.router.said[-1][1]
          and "/asConvert" in sb.router.said[-1][1], sb.router.said[-1])
    check("and remembers it for the next run", switch.PERSONA_FILE.read_text(encoding="utf-8") == "sticker")

    before = sb.current
    for text in ("/ask", "/assets", "/asFoo", "/foobot", "/asConvert@other_bot", "/start", "/as convert please"):
        taken = await sb.switch_command(message(text))
        check(f"{text} is left for the bot", not taken and sb.current is before)
    check("a stranger's /asConvert is left for the bot too",
          not await sb.switch_command(message("/asConvert", sender=STRANGER, chat=STRANGER)))

    said = len(sb.router.said)
    check("/as answers with where it is", await sb.switch_command(message("/as")) and sb.current is before
          and "StickerBot now" in sb.router.said[-1][1])
    check("and names the bot that did not start, and why",
          "ManagerBot is not running: MBOT_ADMIN_ID is empty" in sb.router.said[-1][1], sb.router.said[-1])
    await sb.switch_command(message("/asManager"))
    check("switching to a bot that did not start says why, and stays", sb.current is before
          and "not running" in sb.router.said[-1][1] and len(sb.router.said) == said + 2)


async def routing():
    print("\n== where an update goes ==")
    sb, by = board()
    telegram = Telegram()
    sb.current = by["convert"]

    telegram.answer = {"message_id": 10, "chat": {"id": OWNER}}
    await sb.post(telegram, by["anon"].app.bot, "sendMessage", {"chat_id": OWNER, "text": "hi"}, None)
    telegram.answer = [{"message_id": 11, "chat": {"id": OWNER}}, {"message_id": 12, "chat": {"id": OWNER}}]
    await sb.post(telegram, by["sticker"].app.bot, "sendMediaGroup", {"chat_id": OWNER}, None)
    telegram.answer = {"message_id": 13}
    await sb.post(telegram, by["downloader"].app.bot, "copyMessage", {"chat_id": OWNER}, None)
    telegram.answer = {"message_id": 14, "chat": {"id": OWNER}}
    await sb.post(telegram, by["sticker"].app.bot, "sendInvoice", {"chat_id": OWNER, "payload": "stars:5"}, None)

    def pressed(message_id):
        return {"callback_query": {"id": "1", "data": "x", "from": {"id": OWNER},
                                   "message": {"message_id": message_id, "chat": {"id": OWNER}}}}

    def reply_to(message_id, author=ME):
        return {"message": message("hello", reply_to_message={"message_id": message_id, "chat": {"id": OWNER},
                                                               "from": {"id": author}})}

    check("a button goes to the bot that drew it, not the current one", sb.route(pressed(10)) is by["anon"])
    check("the second picture of an album is its sender's too", sb.route(pressed(12)) is by["sticker"])
    check("a copied message, whose answer has no chat, is its sender's", sb.route(pressed(13)) is by["downloader"])
    check("a button on a message nobody here sent goes to the current bot", sb.route(pressed(99)) is by["convert"])
    check("a reply to AnonBot's message goes to AnonBot", sb.route(reply_to(10)) is by["anon"])
    check("a reply to your own message goes to the current bot", sb.route(reply_to(10, author=OWNER))
          is by["convert"])
    check("a plain message goes to the current bot", sb.route({"message": message("hi")}) is by["convert"])
    check("the checkout goes to the bot that sent the invoice",
          sb.route({"pre_checkout_query": {"id": "1", "invoice_payload": "stars:5"}}) is by["sticker"])
    check("and so does the payment",
          sb.route({"message": message("", successful_payment={"invoice_payload": "stars:5"})}) is by["sticker"])

    await sb.dispatch({"update_id": 1, "message": message("/start")})
    update = by["convert"].app.update_queue.get_nowait()
    check("an update reaches its bot's queue, bound to that bot's own Bot object",
          update.message.text == "/start" and update.get_bot() is by["convert"].app.bot)
    await sb.dispatch({"update_id": 2, "message": message("/asAnon")})
    check("a switch is not passed on to anybody", all(bot.app.update_queue.empty() for bot in by.values()))

    sb.current = None
    await sb.dispatch({"update_id": 3, "message": message("hello")})
    check("with nobody chosen yet, a message is answered with the list",
          "not being any bot yet" in sb.router.said[-1][1], sb.router.said[-1:])

    for number in range(switch.REMEMBERED_MESSAGES + 5):
        sb.remember(sb.origins, (1, number), by["anon"])
    check("what it remembers is bounded", len(sb.origins) == switch.REMEMBERED_MESSAGES
          and (1, 0) not in sb.origins)


async def profiles():
    print("\n== the menu and the profile ==")
    sb, by = board()
    telegram = Telegram()
    switch._ORIGINAL_POST = telegram
    sb.current = by["sticker"]
    everyone = [BotCommand("start", "go"), BotCommand("help", "help")]
    owner = BotCommandScopeChat(OWNER)

    for bot in (by["sticker"], by["convert"]):
        commands = everyone + [BotCommand(bot.role, "its own")]
        await sb.post(telegram, bot.app.bot, "setMyCommands", {"commands": commands, "language_code": None}, None)
        await sb.post(telegram, bot.app.bot, "setMyDescription",
                      {"description": f"I am {bot.name}", "language_code": "uz"}, None)
    await sb.post(telegram, by["sticker"].app.bot, "setMyCommands", {"commands": everyone, "scope": owner}, None)
    await sb.post(telegram, by["sticker"].app.bot, "setMyDescription",
                  {"description": "only StickerBot says this", "language_code": "ru"}, None)
    await sb.post(telegram, by["convert"].app.bot, "setMyShortDescription", {"short_description": "c"}, None)
    await sb.post(telegram, by["convert"].app.bot, "deleteMyCommands",
                  {"scope": BotCommandScopeAllGroupChats()}, None)
    sent = [(endpoint, data) for _, endpoint, data in telegram.calls]
    check("only the current bot's menu and profile reach Telegram",
          len(sent) == 4 and all("Convert" not in json.dumps(str(data)) for _, data in sent), sent)
    commands = [c.command for c in sent[0][1]["commands"]]
    check("with /as added to its menu", commands == ["start", "help", "sticker", "as"], commands)
    check("and the others' are kept for later", len(sb.profiles[by["convert"]]) == 4)

    sb.quiet = True
    telegram.calls.clear()
    await sb.post(telegram, by["convert"].app.bot, "sendMessage", {"chat_id": 1, "text": "x"}, None)
    check("--audit sends everything silently", telegram.calls[0][2].get("disable_notification") is True)
    sb.quiet = False

    telegram.calls.clear()
    sb.current = by["convert"]
    await sb.show_profile(by["convert"])
    calls = [(endpoint, data) for _, endpoint, data in telegram.calls]
    check("a switch to a bot with no menu of its own in the tester's chat takes StickerBot's down",
          calls == [("deleteMyCommands", {"scope": owner})], calls)
    check("and touches nothing else: not the menu everybody gets, not the descriptions",
          not any(e in ("setMyDescription", "setMyShortDescription") or d.get("scope") != owner
                  for e, d in calls), calls)

    anon_menu = [BotCommand("link", "l"), BotCommand("help", "h")]
    await sb.post(telegram, by["anon"].app.bot, "setMyCommands", {"commands": anon_menu, "scope": owner}, None)
    telegram.calls.clear()
    sb.current = by["anon"]
    await sb.show_profile(by["anon"])
    calls = [(endpoint, data) for _, endpoint, data in telegram.calls]
    check("a switch to AnonBot is one call: its menu, in the tester's chat, with /as",
          len(calls) == 1 and calls[0][0] == "setMyCommands" and calls[0][1].get("scope") == owner
          and [c.command for c in calls[0][1]["commands"]] == ["link", "help", "as"], calls)

    telegram.calls.clear()
    await sb.show_profile(by["anon"])
    check("switching to the bot it already is costs no calls at all", not telegram.calls, telegram.calls)

    telegram.calls.clear()
    sb.current = by["sticker"]
    await sb.show_profile(by["anon"])
    check("a switch overtaken by another does nothing", not telegram.calls)

    lone, lone_by = board()
    lone.admins = set()
    lone.current = lone_by["sticker"]
    for bot in (lone_by["sticker"], lone_by["convert"]):
        await lone.post(telegram, bot.app.bot, "setMyCommands",
                        {"commands": [BotCommand(bot.role, "x")], "language_code": None}, None)
        await lone.post(telegram, bot.app.bot, "setMyCommands",
                        {"commands": [BotCommand(bot.role, "x")], "language_code": "uz"}, None)
    telegram.calls.clear()
    lone.current = lone_by["convert"]
    await lone.show_profile(lone_by["convert"])
    calls = [(endpoint, data.get("language_code")) for _, endpoint, data in telegram.calls]
    check("with no tester set, the menu everybody gets switches instead, in every language",
          calls == [("setMyCommands", None), ("setMyCommands", "uz")], calls)

    print("\n== Telegram's rate limit ==")
    from telegram.error import RetryAfter

    class Limited(Telegram):
        def __init__(self, refusals):
            super().__init__()
            self.refusals = refusals

        async def __call__(self, bot_object, endpoint, data=None, **kwargs):
            if self.refusals:
                self.refusals -= 1
                raise RetryAfter(0)
            return await super().__call__(bot_object, endpoint, data, **kwargs)

    limited = Limited(refusals=2)
    switch._ORIGINAL_POST = limited
    sb.current = by["sticker"]
    sb.applied.clear()
    waits = []
    real_sleep = asyncio.sleep

    async def no_wait(seconds):
        waits.append(seconds)
        await real_sleep(0)
    switch.asyncio.sleep = no_wait
    try:
        await sb.show_profile(by["sticker"])
    finally:
        switch.asyncio.sleep = real_sleep
    check("a rate limit is waited out, as long as Telegram says, and not given up on",
          len(waits) == 2 and all(1 <= w < 2 for w in waits), waits)
    check("and everything still goes up after it",
          any(e == "setMyCommands" and d.get("scope") == owner for _, e, d in limited.calls)
          and all(key in sb.profiles[by["sticker"]] for key in sb.applied), limited.calls)

    limited = Limited(refusals=1)
    switch._ORIGINAL_POST = limited
    sb.current = by["convert"]

    async def switched_meanwhile(seconds):
        sb.current = by["anon"]
        await real_sleep(0)
    switch.asyncio.sleep = switched_meanwhile
    try:
        await sb.show_profile(by["convert"])
    finally:
        switch.asyncio.sleep = real_sleep
    check("a switch during the wait ends it: nothing more of the old one goes up", not limited.calls,
          limited.calls)
    sb.catching_up_until = switch.time.monotonic() + 30
    check("and /as says the menu is catching up", "goes up in about" in sb.status(), sb.status())


SHARED = "postgresql://postgres.ref:secret@aws-0-eu-central-1.pooler.supabase.com:5432/postgres"
LOCAL = "postgresql://postgres:postgres@localhost:5432/botfamily"


def settings_and_identity():
    print("\n== either database, under its own names ==")
    with tempfile.TemporaryDirectory() as work:
        env_file = Path(work) / "token.env"
        env_file.write_text(f"# a comment\nTESTBOT_TOKEN=1:test\nTESTBOT_DATABASE_URL={LOCAL}\n"
                            f"FAMILY_BOT_ID=testbot\n", encoding="utf-8")
        saved = {key: switch.os.environ.pop(key, None) for key in switch.ENVIRONMENT_SETTINGS}
        try:
            settings = switch.read_settings(env_file)
            check("token.env is read", settings["TESTBOT_TOKEN"] == "1:test" and settings["TESTBOT_DATABASE_URL"]
                  == LOCAL)
            switch.os.environ["TESTBOT_DATABASE_URL"] = SHARED
            check("the environment wins over the file",
                  switch.read_settings(env_file)["TESTBOT_DATABASE_URL"] == SHARED)
            switch.os.environ["TESTBOT_TOKEN"] = "2:from-the-shell"
            settings = switch.read_settings(Path(work) / "absent.env")
            check("and with no file, the environment alone will do",
                  settings["TESTBOT_TOKEN"] == "2:from-the-shell" and settings["TESTBOT_DATABASE_URL"] == SHARED)
            del switch.os.environ["TESTBOT_DATABASE_URL"]
            try:
                switch.read_settings(Path(work) / "absent.env")
                said = None
            except SystemExit as exc:
                said = str(exc)
            check("no database at all is said, not guessed", said and "TESTBOT_DATABASE_URL" in said, said)
        finally:
            for key, value in saved.items():
                switch.os.environ.pop(key, None)
                if value is not None:
                    switch.os.environ[key] = value

    check("the local database is local", switch.is_local(LOCAL) and switch.is_local("postgresql://u:p@127.0.0.1/x"))
    check("the shared one is not, and is allowed", not switch.is_local(SHARED))
    check("the banner names where, without the password",
          "secret" not in switch.where(SHARED) and "postgres.ref" not in switch.where(SHARED))

    bots = [switch.Bot(folder, name, prefix) for folder, name, prefix in switch.BOTS]
    saved = dict(switch.os.environ)
    try:
        switch.prepare_environment({"TESTBOT_TOKEN": "1:test", "TESTBOT_DATABASE_URL": SHARED,
                                    "FAMILY_BOT_ID": "testbot"}, bots)
        sandbox_by_default = switch.os.environ.get("FAMILY_STARS_SANDBOX")
        default_is_on = switch.sandboxed()
        switch.prepare_environment({"TESTBOT_TOKEN": "1:test", "TESTBOT_DATABASE_URL": SHARED,
                                    "FAMILY_STARS_SANDBOX": "0"}, bots)
        turned_off = switch.os.environ.get("FAMILY_STARS_SANDBOX"), switch.sandboxed()
    finally:
        switch.os.environ.clear()
        switch.os.environ.update(saved)
    check("Stars are sandboxed by default, as the bots read it", sandbox_by_default == "1" and default_is_on,
          sandbox_by_default)
    check("and token.env can turn that off", turned_off == ("0", False), turned_off)
    try:
        switch.prepare_environment({"TESTBOT_TOKEN": "1:test", "TESTBOT_DATABASE_URL": SHARED,
                                    "FAMILY_BOT_ID": "testbot"}, bots)
    finally:
        switch.os.environ.clear()
        switch.os.environ.update(saved)
    by = {bot.role: bot for bot in bots}
    check("each bot has its own schema and bus id",
          by["convert"].overlay["DB_SCHEMA"] == "testbot_convert_bot"
          and by["convert"].overlay["FAMILY_BOT_ID"] == "testbot_convert")
    check("and the test ManagerBot looks after the test bots only",
          by["manager"].overlay["FAMILY_REGISTRY"].split(",")[1] == "testbot_convert:ConvertBot:testbot_convert_bot"
          and "convertbot:" not in by["manager"].overlay["FAMILY_REGISTRY"])

    fakes = {}
    for bot in bots:
        bot.module = types.SimpleNamespace(BOT_NAME=bot.role + "bot")
        fakes[f"{bot.folder}.family_link"] = types.SimpleNamespace(FAMILY_SCHEMA="family")
    fakes["manager_bot.db"] = types.SimpleNamespace(FAMILY_SCHEMA="family")
    fakes["convert_bot.db"] = types.SimpleNamespace(DB_SCHEMA="testbot_convert_bot")
    by["manager"].module.ALL_BOTS = [{"id": "testbot_convert"}, {"id": "managerbot"}]
    saved_modules = {name: sys.modules.get(name) for name in fakes}
    sys.modules.update(fakes)
    try:
        for bot in bots:
            switch.take_test_identity(bot)
        check("every bot's BOT_NAME -- what lifecycle keys the lease, the pause and the restart marker on -- "
              "is its test id", all(bot.module.BOT_NAME == bot.overlay["FAMILY_BOT_ID"] for bot in bots),
              [bot.module.BOT_NAME for bot in bots])
        check("and ManagerBot lists itself under it",
              by["manager"].module.ALL_BOTS[-1]["id"] == "testbot_manager")
        schemas = {name: getattr(module, "FAMILY_SCHEMA", None) for name, module in fakes.items()}
        check("what the real bots keep in `family` -- the wallet among it -- TestBot keeps in testbot_family, "
              "in every bot and in ManagerBot's db.py",
              all(value == "testbot_family" for name, value in schemas.items() if name != "convert_bot.db")
              and not hasattr(fakes["convert_bot.db"], "FAMILY_SCHEMA"), schemas)
        del sys.modules["anon_bot.family_link"]
        by["anon"].module = types.SimpleNamespace(BOT_NAME="anonbot")
        try:
            switch.take_test_identity(by["anon"])
            said = None
        except RuntimeError as exc:
            said = str(exc)
        check("a bot whose family schema cannot be replaced does not start with the real one",
              bool(said) and by["anon"].module.BOT_NAME == "anonbot", said)
    finally:
        for name, module in saved_modules.items():
            sys.modules.pop(name, None)
            if module is not None:
                sys.modules[name] = module
    try:
        switch.take_test_identity(switch.Bot("x_bot", "X", "XBOT", overlay={"FAMILY_BOT_ID": "t"},
                                             module=types.SimpleNamespace()))
        said = None
    except RuntimeError as exc:
        said = str(exc)
    check("a bot with no BOT_NAME to replace does not start under a name it did not mean", bool(said))


def refusals():
    print("\n== what it will not run on ==")
    try:
        switch.refuse_writable_shared_database("postgresql://postgres:postgres@localhost:1/nothing")
        said = None
    except SystemExit as exc:
        said = str(exc)
    check("the local database is not asked about -- nothing there is anybody else's", said is None, said)
    try:
        switch.refuse_writable_shared_database("postgresql://postgres.ref:secretpw@db.does-not-exist.invalid:5432/x")
        said = None
    except SystemExit as exc:
        said = str(exc)
    check("a shared database it cannot ask is refused, not assumed read-only", bool(said) and "Not starting" in said,
          said)
    check("  and the refusal carries no password or user", said and "secretpw" not in said and "postgres.ref" not in said,
          said)

    with tempfile.TemporaryDirectory() as work:
        lock = Path(work) / ".lock"
        held = switch.hold_the_only_instance(lock)
        second = subprocess.run([sys.executable, "-c",
                                 "import importlib.util, sys; from pathlib import Path;"
                                 f"spec = importlib.util.spec_from_file_location('s', r'{ROOT / 'testbot' / 'switch.py'}');"
                                 "m = importlib.util.module_from_spec(spec); sys.modules['s'] = m; spec.loader.exec_module(m);"
                                 f"m.hold_the_only_instance(Path(r'{lock}'))"],
                                capture_output=True, text=True, encoding="utf-8", timeout=60)
        check("a second TestBot on this machine refuses to start -- one token, one poller",
              second.returncode != 0 and "already running" in (second.stderr + second.stdout),
              (second.returncode, second.stderr[-200:]))
        held.close()
        third = subprocess.run(second.args, capture_output=True, text=True, encoding="utf-8", timeout=60)
        check("and starts once the first has gone", third.returncode == 0, third.stderr[-200:])

    with tempfile.TemporaryDirectory() as work:
        fake_root = Path(work) / "BotFamily9.9.9"
        (fake_root / "convert_bot").mkdir(parents=True)
        (fake_root / "convert_bot" / ".env").write_text("CBOT_TOKEN=123:real\n", encoding="utf-8")
        real_root, switch.ROOT = switch.ROOT, fake_root
        try:
            try:
                switch.refuse_real_tokens("123:real")
                refused = None
            except SystemExit as exc:
                refused = str(exc)
            check("a real bot's token is refused, naming where it is", refused and "CBOT_TOKEN" in refused
                  and "123:real" not in refused, refused)
            try:
                switch.refuse_real_tokens("456:test")
                refused = None
            except SystemExit as exc:
                refused = str(exc)
            check("a token of its own runs", refused is None, refused)
        finally:
            switch.ROOT = real_root


def main() -> int:
    with tempfile.TemporaryDirectory() as work:
        switch.PERSONA_FILE = Path(work) / ".persona"
        asyncio.run(switching())
        asyncio.run(routing())
        asyncio.run(profiles())
        settings_and_identity()
        refusals()
    print(f"\nFAILURES: {len(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
