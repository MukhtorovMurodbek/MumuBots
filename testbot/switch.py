#!/usr/bin/env python3
"""TestBot as any of the five bots, switched from the chat.

    .\\testbot\\run.ps1                 all five, on the test token; carries on as whichever it was last
    .\\testbot\\run.ps1 all convert     all five, starting as ConvertBot
    .\\testbot\\run.ps1 anon            AnonBot alone (switch.py --only anon)
    .\\testbot\\run.ps1 audit           every command each bot handles, against its menus; then exit
    python testbot/switch.py [bot]     the same, without the PowerShell

Then, in the chat with the test bot:

    /asConvert   /asSticker   /asDownloader   /asAnon   /asManager
    /convertbot  /stickerbot  ...               the same thing, the other way round
    /as                                          which one it is now, and the list

WHAT IT IS
    Every bot's real code, loaded side by side in one process -- the same
    files run.ps1 <bot> runs one at a time. Each bot is imported as a package
    of its own, so `import db` inside convert_bot means convert_bot's db, and
    each sees its own environment: its own testbot_<bot> schema, its own id
    on the family bus. All five are started, and stay started; switching only
    changes which one the next update goes to. A conversion that is running
    keeps running, and keeps updating its card, while you talk to AnonBot.

ONE TOKEN, ONE POLLER
    Telegram allows one getUpdates consumer per token, so none of the five
    polls. This file does, once, and hands each update to one bot:

      a button press goes to the bot that drew the button;
      a reply to one of the bot's messages goes to the bot that wrote it --
        which is what makes answering an AnonBot message work after you have
        switched away;
      a payment goes to the bot that sent the invoice;
      everything else goes to whichever bot it is now.

THE MENU AND THE PROFILE
    Commands, description and short description belong to the token, not to
    a bot, so five bots publishing them at startup would leave whichever
    went last. Instead each bot's calls are recorded and only the current
    one's reach Telegram. A switch then changes one thing: the menu in the
    tester's own chat (TESTBOT_ADMIN_ID), which outranks every other scope --
    so the / menu you see is always the bot you are talking to, plus /as.
    Everybody else keeps the menu of the bot it started as. Rewriting all of
    it on every switch is what Telegram's rate limit punishes: a 285-second
    wait, then 1941 seconds with the wrong menu showing.

EVERYTHING IT DOES STAYS ITS OWN
    It runs on this machine, one at a time: a second copy refuses to start,
    because two pollers on one token split its updates between them. On
    either database it keeps to its own names and its own tables:

      each bot's tables are in testbot_<bot>, and what the real bots keep in
      the shared `family` schema -- heartbeats, the bus, settings, the ⚡
      wallet -- it keeps in testbot_family, so its credit is its own;
      every bot's BOT_NAME is replaced with its test id before its main()
      runs, because lifecycle keys the poll lease, the maintenance flag, the
      update waitlist and the expected-restart marker on it;
      Stars top-ups are sandboxed (FAMILY_STARS_SANDBOX): credited, never
      charged, and every message about one says so.

    The local database from docker-compose.yml is the default. The shared
    one works too, read-only by the database's own rules: TestBot runs there
    only as a login that cannot write outside testbot_* -- made once with
    testbot/supabase_role.sql -- and checks that before any bot loads. As the
    postgres user, it refuses to start.

WHERE ITS SETTINGS COME FROM
    testbot/token.env; any of them set in the environment wins over the
    file, so one run can go elsewhere without editing it.

WHAT IT WILL NOT DO
    It never reads the bots' .env files -- they hold the real tokens, and
    shared/.env or ManagerBot's the production database -- apart from the
    API id and hash of the Telegram app the big-file route logs in through,
    which are an app's credentials rather than a bot's and are what lets it
    move files over 20 MB. It refuses to start on a token that is one of the
    real bots'.

SHARED MODULES
    Since 2.0.0 the modules every bot shares live once, in shared/. Each bot
    still gets its own copy of each in memory -- loaded under its package,
    as the bot's own files are -- so a new shared module is in TestBot the
    next time it starts, with nothing to register.
"""
from __future__ import annotations

import asyncio
import builtins
import contextlib
import contextvars
import importlib
import importlib.machinery
import importlib.util
import inspect
import json
import logging
import os
import re
import sys
import time
import types
from collections import OrderedDict
from collections.abc import MutableMapping
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
PERSONA_FILE = HERE / ".persona"
LOCK_FILE = HERE / ".lock"
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

POLL_TIMEOUT = 30
REMEMBERED_MESSAGES = 20_000

# folder, name, env prefix. The order is the order of the list in the chat.
BOTS = (
    ("sticker_bot", "StickerBot", "SBOT"),
    ("convert_bot", "ConvertBot", "CBOT"),
    ("downloader_bot", "DownloaderBot", "DBOT"),
    ("anon_bot", "AnonBot", "ABOT"),
    ("manager_bot", "ManagerBot", "MBOT"),
)
# Other words for a bot, on top of its role, its name and its folder.
ALIASES = {"download": "downloader_bot", "chat": "anon_bot", "parent": "manager_bot"}

# Settings that must not reach a test run from whatever shell started it.
# The production database under ManagerBot's other name for it, a local Bot
# API server that belongs to somebody else's session, the registry and
# identity settings this file sets per bot below.
NEVER_INHERITED = ("CLOUD_DATABASE_URL", "LOCAL_DATABASE_URL", "LOCAL_BOT_API_URL", "LOCAL_BOT_API_MODE",
                   "FAMILY_REGISTRY", "DB_SCHEMA", "FAMILY_BOT_ID", "FAMILY_LABEL", "SIBLING_BOTS")
# What is taken from the bots' own .env files, and nothing else is: the
# credentials of the Telegram *app* the big-file route logs in through --
# my.telegram.org's API id and hash -- which are an app's, not a bot's, and
# are what lets TestBot move files over 20 MB. Read from token.env or the
# environment first, then each bot's .env, then shared/.env.
APP_CREDENTIALS = ("TELEGRAM_API_ID", "TELEGRAM_API_HASH", "CBOT_API_ID", "CBOT_API_HASH",
                   "DBOT_API_ID", "DBOT_API_HASH")
SHARED_DIR = ROOT / "shared"

log = logging.getLogger("testbot")


@dataclass(eq=False)
class Bot:
    folder: str
    name: str
    prefix: str
    overlay: dict = field(default_factory=dict)   # what this bot sees differently from the rest
    local: frozenset = frozenset()
    files: dict = field(default_factory=dict)      # module name -> the file it is
    import_table: dict | None = None
    handlers: list = field(default_factory=list)
    module: types.ModuleType | None = None
    app: object | None = None
    polling: dict = field(default_factory=dict)
    context: contextvars.Context | None = None
    initialized: bool = False
    state: str = "new"
    problem: str = ""

    @property
    def role(self) -> str:
        return self.folder.removesuffix("_bot")

    @property
    def directory(self) -> Path:
        return ROOT / self.folder

    @property
    def names(self) -> set[str]:
        return ({self.role, self.folder, self.name.lower(), self.prefix.lower()}
                | {alias for alias, folder in ALIASES.items() if folder == self.folder})


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

# Read from the environment as well as token.env, and nothing else is.
ENVIRONMENT_SETTINGS = ("TESTBOT_TOKEN", "TESTBOT_USERNAME", "TESTBOT_ADMIN_ID", "TESTBOT_DATABASE_URL",
                        "FAMILY_LABEL", "FAMILY_BOT_ID", "SIBLING_BOTS", "FAMILY_STARS_SANDBOX",
                        "CBOT_API_ID", "CBOT_API_HASH")
# Where TestBot keeps what the real bots keep in `family`.
TEST_FAMILY_SCHEMA = "testbot_family"


def read_settings(path: Path | None = None) -> dict:
    """testbot/token.env, with whatever of it the environment sets winning --
    so one run can point elsewhere without editing the file."""
    path = path or HERE / "token.env"
    settings = {}
    if path.is_file():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            settings[key.strip()] = value.strip()
    for key in ENVIRONMENT_SETTINGS:
        if os.environ.get(key):
            settings[key] = os.environ[key]
    if not settings.get("TESTBOT_TOKEN"):
        raise SystemExit("No TESTBOT_TOKEN: copy testbot/token.env.example to testbot/token.env and paste "
                         "the test token into it, or set TESTBOT_TOKEN in the environment.")
    if not settings.get("TESTBOT_DATABASE_URL"):
        raise SystemExit("No TESTBOT_DATABASE_URL: the local database from docker-compose.yml, or the "
                         "shared one.")
    return settings


LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1", "host.docker.internal", "postgres")


def is_local(dsn: str) -> bool:
    return (urlsplit(dsn).hostname or "") in LOCAL_HOSTS


def writable_outside_testbot(dsn: str) -> list[str]:
    """What the login in `dsn` could change that is not TestBot's: every
    schema outside testbot_* it could create in or write a row of, by name,
    and "everything" for a superuser. Asked of the database, which is what
    enforces it -- so the answer is about the login, not about TestBot's code.
    An empty list is the only answer TestBot runs on the shared database with."""
    import psycopg
    with psycopg.connect(dsn, connect_timeout=15, autocommit=True) as conn:
        superuser, createrole = conn.execute(
            "SELECT rolsuper, rolcreaterole FROM pg_roles WHERE rolname = current_user").fetchone()
        if superuser:
            return ["everything (it is a superuser)"]
        found = ["other logins (it can create and alter roles)"] if createrole else []
        found += [row[0] for row in conn.execute(r"""
            SELECT n.nspname FROM pg_namespace n
            WHERE n.nspname NOT LIKE 'testbot\_%' AND n.nspname NOT LIKE 'pg\_%'
              AND n.nspname <> 'information_schema'
              AND (has_schema_privilege(n.oid, 'CREATE')
                   OR EXISTS (SELECT 1 FROM pg_class c WHERE c.relnamespace = n.oid AND (
                        (c.relkind IN ('r', 'p', 'v', 'm', 'f') AND (
                            has_table_privilege(c.oid, 'INSERT') OR has_table_privilege(c.oid, 'UPDATE')
                            OR has_table_privilege(c.oid, 'DELETE') OR has_table_privilege(c.oid, 'TRUNCATE')))
                        OR (c.relkind = 'S' AND has_sequence_privilege(c.oid, 'UPDATE')))))
            ORDER BY 1""")]
    return found


def refuse_writable_shared_database(dsn: str) -> None:
    """On the shared database TestBot is read-only, and the database is what
    makes it so: it starts there only as a login that can write nothing
    outside testbot_*. A rule enforced by the code it is protecting against
    would be as good as that code; this one is as good as Postgres."""
    if is_local(dsn):
        return
    try:
        found = writable_outside_testbot(dsn)
    except Exception as exc:
        said = re.sub(r'user "[^"]*"', 'user "..."', (str(exc).strip().splitlines() or [""])[0])
        raise SystemExit(f"Could not ask the database at {where(dsn)} what TestBot's login may change "
                         f"({type(exc).__name__}: {said}). Not starting on the shared database without "
                         f"knowing.")
    if found:
        raise SystemExit(
            f"On the shared database TestBot is read-only, and the login in TESTBOT_DATABASE_URL is not: it "
            f"could change {', '.join(found)}. Make TestBot's own login with testbot/supabase_role.sql "
            f"(Supabase -> SQL Editor) and put it in TESTBOT_DATABASE_URL -- or use the local database.")


def sandboxed() -> bool:
    """The test shared_features.STARS_SANDBOX makes, word for word."""
    return os.environ.get("FAMILY_STARS_SANDBOX", "").strip().lower() in ("1", "true", "yes", "on")


def hold_the_only_instance(path: Path = LOCK_FILE):
    """One TestBot on this machine at a time. Two would be two pollers on one
    token, and Telegram answers that by splitting the updates between them,
    so each would see about half of what you send. The lock is the operating
    system's: it goes when the process does, however it ends. Keep what this
    returns for as long as the process should hold it."""
    handle = open(path, "a+b")
    try:
        if sys.platform == "win32":
            import msvcrt
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        raise SystemExit("TestBot is already running on this machine -- one token, one poller. Stop that "
                         "one first (Ctrl-C in its window).")
    return handle


def _dotenv(path: Path) -> dict:
    if not path.is_file():
        return {}
    from dotenv import dotenv_values
    return {key: value for key, value in dotenv_values(path).items() if value}


def refuse_real_tokens(token: str) -> None:
    """The one mistake this file cannot undo: running five bots' worth of
    test traffic -- and a menu rewrite -- on a bot real people use."""
    places = [ROOT / folder / ".env" for folder, _, _ in BOTS] + [SHARED_DIR / ".env"]
    places += [ROOT.parent / folder / ".env" for folder, _, _ in BOTS]      # the repository checkout
    places += [ROOT.parent / "shared" / ".env"]
    places += [ROOT.parent / (ROOT.name + "C") / ".env"]   # the combined edition
    for place in places:
        for key, value in _dotenv(place).items():
            if key.endswith("_TOKEN") and value.strip() == token:
                raise SystemExit(f"TESTBOT_TOKEN is the same token as {key} in "
                                 f"{place.relative_to(ROOT.parent)} -- that is a real bot. Not starting.")


def where(dsn: str) -> str:
    """Host and database, without the credentials."""
    try:
        parts = urlsplit(dsn)
        return f"{parts.hostname or '?'}:{parts.port or 5432}{parts.path or ''}"
    except ValueError:
        return "(set, but not a URL I can read)"


def prepare_environment(settings: dict, bots: list[Bot]) -> None:
    """One token for five bots. What is the same for all of them goes in the
    real environment -- child processes inherit it -- and what differs is
    each bot's overlay."""
    for key in NEVER_INHERITED:
        os.environ.pop(key, None)
    for key in list(os.environ):
        if key.endswith(("_TOKEN", "_USERNAME", "_ADMIN_ID")) and key.split("_")[0] in {p for _, _, p in BOTS}:
            del os.environ[key]
    for bot in bots:
        os.environ[f"{bot.prefix}_TOKEN"] = settings["TESTBOT_TOKEN"]
        os.environ[f"{bot.prefix}_USERNAME"] = settings.get("TESTBOT_USERNAME", "")
        if settings.get("TESTBOT_ADMIN_ID"):
            os.environ[f"{bot.prefix}_ADMIN_ID"] = settings["TESTBOT_ADMIN_ID"]

    for key in APP_CREDENTIALS:
        value = settings.get(key) or next(
            (found for folder, _, _ in BOTS if (found := _dotenv(ROOT / folder / ".env").get(key))), None) \
            or _dotenv(SHARED_DIR / ".env").get(key)
        if value and not os.environ.get(key):
            os.environ[key] = value
    # The bots read their .env files through shared/envfiles.py; TestBot's
    # settings are the ones above, so they read none.
    os.environ["ENV_FILES"] = "off"
    os.environ["DATABASE_URL"] = settings.get("TESTBOT_DATABASE_URL", "")
    # Stars top-ups credited without charging anybody -- what the sandbox in
    # shared_features.py was built for -- unless token.env turns it off.
    os.environ["FAMILY_STARS_SANDBOX"] = settings.get("FAMILY_STARS_SANDBOX") or "1"
    os.environ["FAMILY_VERSION"] = "test"
    os.environ["DEPLOY_LEASE_WAIT_SECONDS"] = "0"   # see run.ps1: nothing to wait for on a test token

    stem = settings.get("FAMILY_BOT_ID") or "testbot"
    ids = {bot.folder: f"{stem}_{bot.role}" for bot in bots}
    for bot in bots:
        bot.overlay = {
            "DB_SCHEMA": f"testbot_{bot.folder}",
            "FAMILY_BOT_ID": ids[bot.folder],
            "FAMILY_LABEL": f"{settings.get('FAMILY_LABEL') or 'TestBot'} as {bot.name}",
            "SIBLING_BOTS": settings.get("SIBLING_BOTS", ""),
        }
    # ManagerBot looks after the four test bots in this process, under the ids
    # they are using, so /status, /logs convert and the bus all work on them.
    manager = next((bot for bot in bots if bot.folder == "manager_bot"), None)
    if manager is not None:
        manager.overlay["FAMILY_REGISTRY"] = ",".join(
            f"{ids[bot.folder]}:{bot.name}:testbot_{bot.folder}" for bot in bots if bot is not manager)


class BotEnviron(MutableMapping):
    """os.environ as one bot sees it: its overlay first, then the real one."""

    def __init__(self, bot: Bot):
        self._bot = bot

    def __getitem__(self, key):
        if key in self._bot.overlay:
            if self._bot.overlay[key] is None:
                raise KeyError(key)
            return self._bot.overlay[key]
        return os.environ[key]

    def __setitem__(self, key, value):
        self._bot.overlay[key] = value

    def __delitem__(self, key):
        self._bot.overlay[key] = None

    def __iter__(self):
        names = set(os.environ) | set(self._bot.overlay)
        return iter(sorted(name for name in names if self.get(name) is not None))

    def __len__(self):
        return sum(1 for _ in self)

    def copy(self) -> dict:
        return dict(self)


class _BotOs(types.ModuleType):
    """`os` as a bot's modules see it: the real one, except its environment."""

    def __init__(self, bot: Bot):
        super().__init__("os", os.__doc__)
        self.environ = BotEnviron(bot)

    def __getattr__(self, attribute):
        return getattr(os, attribute)

    def getenv(self, key, default=None):
        return self.environ.get(key, default)


# ---------------------------------------------------------------------------
# Loading five bots side by side
# ---------------------------------------------------------------------------
# Every bot folder has its own db.py, i18n.py, problems.py, family_link.py...
# and imports them by bare name. Each folder becomes a package here, and a
# bare `import db` inside it is answered with that package's db.

_PACKAGES: dict[str, Bot] = {}
_CURRENT: contextvars.ContextVar[Bot | None] = contextvars.ContextVar("testbot_bot", default=None)


class _BotFinder:
    @staticmethod
    def find_spec(fullname, path=None, target=None):
        package, dot, name = fullname.partition(".")
        bot = _PACKAGES.get(package)
        if bot is None or not dot or "." in name or name not in bot.local:
            return None
        real = bot.files[name]
        return importlib.util.spec_from_file_location(fullname, str(real), loader=_BotLoader(fullname, str(real), bot))


class _BotLoader(importlib.machinery.SourceFileLoader):
    def __init__(self, fullname, path, bot):
        super().__init__(fullname, path)
        self.bot = bot

    def exec_module(self, module):
        module.__builtins__ = self.bot.import_table
        super().exec_module(module)


class _BotImportlib(types.ModuleType):
    def __init__(self, bot: Bot):
        super().__init__("importlib", importlib.__doc__)
        self._bot = bot

    def __getattr__(self, attribute):
        return getattr(importlib, attribute)

    def import_module(self, name, package=None):
        if name in self._bot.local:
            return importlib.import_module(f"{self._bot.folder}.{name}")
        return importlib.import_module(name, package)


def register(bot: Bot) -> None:
    """The bot's own modules, and shared/'s under the bot's package -- so each
    bot gets its own family_link and its own lifecycle, as it would in a
    process of its own. The bot's own file wins a name. ManagerBot has
    monitoring.py where the others share shared_features.py, and is not
    handed theirs."""
    bot.files = {path.stem: path for path in SHARED_DIR.glob("*.py")
                 if not (bot.folder == "manager_bot" and path.stem == "shared_features")}
    bot.files.update({path.stem: path for path in bot.directory.glob("*.py")})
    bot.local = frozenset(bot.files)
    real_import = builtins.__import__
    proxies = {"importlib": _BotImportlib(bot), "os": _BotOs(bot)}

    def bot_import(name, globals=None, locals=None, fromlist=(), level=0):
        if level == 0:
            if name in bot.local:
                return importlib.import_module(f"{bot.folder}.{name}")
            top = name.partition(".")[0]
            if top in proxies and (name == top or not fromlist):
                real_import(name, globals, locals, fromlist, level)
                return proxies[top]
        return real_import(name, globals, locals, fromlist, level)

    bot.import_table = dict(vars(builtins), __import__=bot_import)
    package = types.ModuleType(bot.folder)
    package.__path__ = []
    package.__package__ = bot.folder
    sys.modules[bot.folder] = package
    _PACKAGES[bot.folder] = bot


class _Tagged(logging.Filter):
    def __init__(self, name: str):
        super().__init__()
        self.bot_name = name

    def filter(self, record) -> bool:
        return getattr(record, "bot", None) == self.bot_name


def setup_logging() -> None:
    """One console line per record, stamped with the bot it came from --
    by logger name when it is one of a bot's modules, otherwise by the bot
    whose task wrote it, which is how python-telegram-bot's lines get theirs."""
    base = logging.getLogRecordFactory()

    def factory(*args, **kwargs):
        record = base(*args, **kwargs)
        package, _, rest = record.name.partition(".")
        bot = _PACKAGES.get(package)
        record.where = rest or package if bot is not None else record.name
        bot = bot or _CURRENT.get()
        record.bot = bot.name if bot is not None else "TestBot"
        return record

    logging.setLogRecordFactory(factory)
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(logging.Formatter("%(asctime)s %(bot)-13s %(levelname)-7s %(where)s: %(message)s"))
    root = logging.getLogger()
    root.addHandler(console)
    root.setLevel(logging.INFO)
    for noisy in ("httpx", "httpcore", "telegram.ext.Updater", "apscheduler"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


@contextlib.contextmanager
def adopting_handlers(bot: Bot):
    """While a bot loads, its own console handler is dropped -- this process
    already prints every line once -- and its log files get its lines only."""
    root, problems = logging.getLogger(), logging.getLogger("problems")

    def adopt(logger):
        original = logger.addHandler

        def add(handler):
            if logger is root and type(handler) is logging.StreamHandler:
                return
            handler.addFilter(_Tagged(bot.name))
            bot.handlers.append((logger, handler))
            original(handler)
        logger.addHandler = add

    adopt(root)
    adopt(problems)
    try:
        yield
    finally:
        for logger in (root, problems):
            with contextlib.suppress(AttributeError):
                del logger.addHandler


def take_test_identity(bot: Bot) -> None:
    """Its BOT_NAME becomes its test id, before main() hands it to lifecycle.

    family_link already takes FAMILY_BOT_ID. lifecycle does not: it is given
    BOT_NAME, a constant, and keys on it the poll lease, the maintenance flag,
    the update waitlist and the expected-restart marker -- in the shared
    family schema. Under the real name, a test bot stopping would mark the
    real ConvertBot's restart as expected, and a /pause through the test
    ManagerBot would pause the real one. Every use reads the name when it
    runs, so rebinding it here is enough; ManagerBot also lists itself by it.

    The same for FAMILY_SCHEMA, the shared schema where the real bots keep
    their heartbeats, the bus, settings and the ⚡ wallet: TestBot's is
    testbot_family. family_link and lifecycle read it off family_link as each
    query runs; ManagerBot's db.py has a copy of its own."""
    module, test_id = bot.module, bot.overlay["FAMILY_BOT_ID"]
    real_id = getattr(module, "BOT_NAME", None)
    if real_id is None:
        raise RuntimeError(f"{bot.folder}/bot.py has no BOT_NAME to replace")
    link = sys.modules.get(f"{bot.folder}.family_link")
    if link is None or not hasattr(link, "FAMILY_SCHEMA"):
        raise RuntimeError(f"{bot.folder} has no family_link.FAMILY_SCHEMA to replace")
    module.BOT_NAME = test_id
    for entry in getattr(module, "ALL_BOTS", ()):
        if entry.get("id") == real_id:
            entry["id"] = test_id
    for part in ("family_link", "db"):
        shared = sys.modules.get(f"{bot.folder}.{part}")
        if shared is not None and hasattr(shared, "FAMILY_SCHEMA"):
            shared.FAMILY_SCHEMA = TEST_FAMILY_SCHEMA


def load(bot: Bot) -> bool:
    """Import the bot and run its main() as far as run_polling."""
    token = _CURRENT.set(bot)
    saved = dict(os.environ)
    try:
        with adopting_handlers(bot):
            bot.module = importlib.import_module(f"{bot.folder}.bot")
            take_test_identity(bot)
            bot.module.main()
        if bot.app is None:
            raise RuntimeError("main() returned without starting to poll")
        bot.state = "loaded"
        return True
    except SystemExit as exc:
        bot.state, bot.problem = "skipped", str(exc)
        log.warning("%s is not available: %s", bot.name, exc)
    except Exception as exc:
        bot.state, bot.problem = "failed", f"{type(exc).__name__}: {exc}"
        log.exception("%s could not be loaded", bot.name)
    finally:
        _CURRENT.reset(token)
        if dict(os.environ) != saved:
            os.environ.clear()
            os.environ.update(saved)
    return False


# ---------------------------------------------------------------------------
# Where an update goes, and what reaches Telegram
# ---------------------------------------------------------------------------

PROFILE = {"setMyCommands": "commands", "deleteMyCommands": "commands",
           "setMyDescription": "description", "setMyShortDescription": "short_description"}
SWITCH_COMMAND = ("as", "🧪 TestBot: be another bot")


def _profile_key(endpoint: str, data: dict) -> tuple:
    scope = data.get("scope")
    scope = scope.to_json() if hasattr(scope, "to_json") else json.dumps(scope, sort_keys=True)
    return PROFILE[endpoint], scope, data.get("language_code")


def _signature(endpoint: str, data: dict) -> str:
    """What a profile call puts up, comparable -- so a call that would put up
    what is already there is not made."""
    def plain(value):
        if hasattr(value, "to_dict"):
            return value.to_dict()
        if isinstance(value, (list, tuple)):
            return [plain(item) for item in value]
        return value
    return json.dumps([endpoint, {key: plain(value) for key, value in data.items() if value is not None}],
                      sort_keys=True, default=str)


def _seconds(retry_after) -> float:
    return retry_after.total_seconds() if hasattr(retry_after, "total_seconds") else float(retry_after)


class Switchboard:
    def __init__(self, bots: list[Bot], settings: dict):
        self.bots = bots
        self.by_bot_object: dict[int, Bot] = {}
        self.current: Bot | None = None
        self.admins = {int(part) for part in (settings.get("TESTBOT_ADMIN_ID") or "").split(",")
                       if part.strip().isdigit()}
        self.origins: OrderedDict = OrderedDict()   # (chat id, message id) -> Bot
        self.invoices: OrderedDict = OrderedDict()  # invoice payload -> Bot
        self.profiles: dict[Bot, OrderedDict] = {bot: OrderedDict() for bot in bots}
        # What is up on Telegram right now: key -> signature of the call that
        # put it there, or "?" for something an earlier run may have left.
        self.applied: dict = {}
        self.profile_lock = asyncio.Lock()
        self.catching_up_until = 0.0
        self.router = None
        self.me = None
        self.quiet = False          # --audit: whatever the bots send on the way up arrives silently

    # --- who is who ---
    def find(self, word: str) -> Bot | None:
        word = word.lower().lstrip("_")
        return next((bot for bot in self.bots if word in bot.names), None)

    def remember(self, table: OrderedDict, key, bot: Bot) -> None:
        table[key] = bot
        table.move_to_end(key)
        while len(table) > REMEMBERED_MESSAGES:
            table.popitem(last=False)

    def route(self, data: dict) -> Bot | None:
        query = data.get("callback_query")
        if query and query.get("message"):
            message = query["message"]
            found = self.origins.get((message["chat"]["id"], message["message_id"]))
            if found:
                return found
        checkout = data.get("pre_checkout_query")
        if checkout and checkout.get("invoice_payload") in self.invoices:
            return self.invoices[checkout["invoice_payload"]]
        message = data.get("message") or data.get("edited_message")
        if message:
            paid = message.get("successful_payment")
            if paid and paid.get("invoice_payload") in self.invoices:
                return self.invoices[paid["invoice_payload"]]
            replied = message.get("reply_to_message")
            if replied and (replied.get("from") or {}).get("id") == self.me.id:
                found = self.origins.get((replied["chat"]["id"], replied["message_id"]))
                if found:
                    return found
        return self.current

    # --- what the bots send ---
    async def post(self, original, bot_object, endpoint, data, api_kwargs, **timeouts):
        bot = self.by_bot_object.get(id(bot_object))
        if bot is None:
            return await original(bot_object, endpoint, data, api_kwargs=api_kwargs, **timeouts)
        if endpoint in PROFILE:
            call = dict(data or {}, **(api_kwargs or {}))
            key = _profile_key(endpoint, call)
            self.profiles[bot][key] = (endpoint, call)
            if bot is not self.current:
                return True
            result = await original(bot_object, *self._with_switch_command(endpoint, call), **timeouts)
            if endpoint == "deleteMyCommands":
                self.applied.pop(key, None)
            else:
                self.applied[key] = _signature(endpoint, call)
            return result
        if self.quiet and endpoint.startswith("send") and data is not None:
            data["disable_notification"] = True
        if endpoint in ("sendInvoice", "createInvoiceLink") and (data or {}).get("payload"):
            self.remember(self.invoices, data["payload"], bot)
        result = await original(bot_object, endpoint, data, api_kwargs=api_kwargs, **timeouts)
        for item in result if isinstance(result, list) else [result]:
            if isinstance(item, dict) and "message_id" in item:
                chat = (item.get("chat") or {}).get("id") or (data or {}).get("chat_id")
                if isinstance(chat, int):
                    self.remember(self.origins, (chat, item["message_id"]), bot)
        return result

    @staticmethod
    def _with_switch_command(endpoint, call):
        if endpoint != "setMyCommands":
            return endpoint, call
        from telegram import BotCommand
        commands = list(call.get("commands") or [])
        if len(commands) < 100 and not any(getattr(c, "command", None) == SWITCH_COMMAND[0] for c in commands):
            commands.append(BotCommand(*SWITCH_COMMAND))
        return endpoint, dict(call, commands=commands)

    def switches_with_it(self, key: tuple) -> bool:
        """Whether a switch changes this part of the token's profile.

        Only the tester's own chat menu. Telegram punishes rewriting a bot's
        menus: five bots' worth -- every language, and the two descriptions --
        in a minute earned a 285-second wait, and doing it again, 1941
        seconds, during which the menu stayed whichever bot's it had been. A
        chat's own menu outranks every other scope, so one call per switch
        gives the tester the right one; everybody else keeps the menu of the
        bot it started as, and the descriptions, which only an empty chat
        shows, stay put. With no tester set, the menu everybody gets
        switches instead."""
        kind, scope, _ = key
        if kind != "commands":
            return False
        if not self.admins:
            return scope == "null"
        found = json.loads(scope) if scope != "null" else {}
        return found.get("type") == "chat" and found.get("chat_id") in self.admins

    async def show_profile(self, bot: Bot, replay: bool = True) -> None:
        """Take down what of the switching part is up and is not this bot's,
        then put up what of this bot's is not up already.

        If Telegram still says to wait, it waits as long as Telegram says and
        carries on -- giving up there would leave the last bot's menu under
        the new one -- unless there has been another switch meanwhile, which
        then does the work instead."""
        from telegram.error import RetryAfter
        async with self.profile_lock:
            if bot is not self.current:
                return                      # switched again while waiting; that switch will do it
            mine = OrderedDict((key, call) for key, call in self.profiles[bot].items()
                               if self.switches_with_it(key))
            calls = []                      # (key, endpoint, data, what is up afterwards)
            for key in sorted(set(self.applied) - set(mine), key=str):
                if not self.switches_with_it(key):
                    continue
                kind, scope, language = key
                data = {"language_code": language}
                if scope != "null":
                    from telegram import BotCommandScope
                    data["scope"] = BotCommandScope.de_json(json.loads(scope), None)
                calls.append((key, "deleteMyCommands", data, None))
            for key, (endpoint, call) in mine.items():
                if endpoint == "deleteMyCommands":
                    if replay and key in self.applied:
                        calls.append((key, endpoint, call, None))
                elif not replay:
                    self.applied[key] = _signature(endpoint, call)   # it went up as the bot started
                elif self.applied.get(key) != _signature(endpoint, call):
                    calls.append((key, endpoint, call, _signature(endpoint, call)))
            for key, endpoint, call, after in calls:
                while bot is self.current:
                    try:
                        await _ORIGINAL_POST(self.router, *self._with_switch_command(
                            endpoint, {k: v for k, v in call.items() if v is not None}))
                    except RetryAfter as exc:
                        wait = _seconds(exc.retry_after) + 1
                        self.catching_up_until = time.monotonic() + wait
                        log.info("Telegram's rate limit: %s's menu goes up in %.0fs.", bot.name, wait)
                        await asyncio.sleep(wait)
                        continue
                    except Exception as exc:
                        log.info("Could not %s for %s (%s).", endpoint, bot.name, exc)
                    else:
                        if after is None:
                            self.applied.pop(key, None)
                        else:
                            self.applied[key] = after
                    break
            self.catching_up_until = 0.0

    def catching_up(self) -> str:
        left = self.catching_up_until - time.monotonic()
        return (f"\n\nIts / menu is waiting out Telegram's rate limit for a few quick switches, "
                f"and goes up in about {left:.0f}s." if left > 0 else "")

    # --- the chat side ---
    def menu(self) -> str:
        return "  ".join(f"/as{bot.role.capitalize()}" for bot in self.bots)

    def status(self) -> str:
        now = f"TestBot is {self.current.name} now." if self.current else "TestBot is not being any bot yet."
        down = [f"{bot.name} is not running: {bot.problem}" for bot in self.bots if bot.state != "running"]
        return "\n".join(["🧪 " + now + self.catching_up(), "", "Be another one:", self.menu(),
                          *([""] + down if down else [])])

    async def switch_command(self, message: dict) -> bool:
        """True when the message was a switch and has been answered."""
        text = (message.get("text") or "").strip()
        found = re.fullmatch(r"/(?:as_?(\w*)|([a-z]+?)_?bot)(?:@(\w+))?", text, re.IGNORECASE)
        if not found:
            return False
        word, mention = found.group(1) if found.group(1) is not None else found.group(2), found.group(3)
        if mention and self.me and mention.lower() != (self.me.username or "").lower():
            return False
        if word and self.find(word) is None:
            return False                    # /ask, /assets, /somethingbot: not ours, the bot answers
        sender = (message.get("from") or {}).get("id")
        if self.admins and sender not in self.admins:
            return False                    # not the tester: the bot answers as usual
        chat = message["chat"]["id"]
        if not word:
            await self.say(chat, self.status())
            return True
        bot = self.find(word)
        if bot.state != "running":
            await self.say(chat, f"🧪 {bot.name} is not running: {bot.problem}")
        else:
            self.current = bot
            with contextlib.suppress(OSError):
                PERSONA_FILE.write_text(bot.role, encoding="utf-8")
            log.info("Now %s.", bot.name)
            await self.say(chat, f"🧪 TestBot is {bot.name} now. Send /start to begin.\n\n{self.menu()}")
            asyncio.get_running_loop().create_task(self.show_profile(bot))
        return True

    async def say(self, chat: int, text: str) -> None:
        with contextlib.suppress(Exception):
            await self.router.send_message(chat, text, disable_notification=True)

    async def dispatch(self, data: dict) -> None:
        from telegram import Update
        message = data.get("message")
        if message and (message.get("text") or "").startswith("/") and await self.switch_command(message):
            return
        bot = self.route(data)
        if bot is None or bot.state != "running":
            chat = (message or {}).get("chat", {}).get("id")
            if chat is not None:
                await self.say(chat, self.status())
            return
        await bot.app.update_queue.put(Update.de_json(data, bot.app.bot))

    # --- running ---
    async def start(self, bot: Bot) -> None:
        app = bot.app
        try:
            await app.initialize()
            bot.initialized = True
            if app.post_init:
                await app.post_init(app)
            await app.start()
            bot.state = "running"
        except Exception as exc:
            bot.state, bot.problem = "failed", f"{type(exc).__name__}: {exc}"
            log.exception("Could not start")
            await self.teardown(bot)

    async def teardown(self, bot: Bot) -> None:
        app = bot.app
        steps = [app.stop] if app.running else []
        if bot.initialized:
            if app.post_stop:
                steps.append(lambda: app.post_stop(app))
            steps.append(app.shutdown)
            if app.post_shutdown:
                steps.append(lambda: app.post_shutdown(app))
        for step in steps:
            try:
                await step()
            except Exception:
                log.exception("Error while stopping")
        bot.state = "stopped"

    async def audit(self, running: list[Bot]) -> int:
        """Every command each bot really handles -- read off its running
        app, not its source -- against every menu it publishes, in every
        scope and language: what it asked Telegram to show, as recorded on
        the way up. Then the one menu a switch changes, live: switched to
        each bot in turn, what Telegram shows in the tester's chat. One call
        per bot, because the rate limit is what made the first audits take
        half an hour."""
        from telegram import BotCommandScopeChat
        from telegram.ext import CommandHandler, ConversationHandler
        remembered, findings = self.current, 0

        def handled_by(app) -> set[str]:
            found = set()

            def walk(handlers):
                for handler in handlers:
                    if isinstance(handler, CommandHandler):
                        found.update(handler.commands)
                    elif isinstance(handler, ConversationHandler):
                        walk(handler.entry_points)
                        walk(handler.fallbacks)
                        for state in handler.states.values():
                            walk(state)
            for group in app.handlers.values():
                walk(group)
            return found

        def scope_label(scope: str) -> str:
            found = json.loads(scope) if scope != "null" else {"type": "default"}
            if found.get("type") == "chat":
                return f"chat {found.get('chat_id')}"
            return {"default": "everyone"}.get(found["type"], found["type"])

        for bot in running:
            handled = handled_by(bot.app)
            print(f"\n== {bot.name}: handles {len(handled)} commands")
            shown_anywhere, by_scope = set(), {}
            for (kind, scope, language), (endpoint, call) in self.profiles[bot].items():
                if endpoint != "setMyCommands":
                    continue
                shown = [c.command for c in call.get("commands") or []]
                by_scope.setdefault(scope, {})[language] = shown
                shown_anywhere |= set(shown)
            for scope, languages in by_scope.items():
                untagged = languages.get(None)
                for language, shown in languages.items():
                    where_ = f"{scope_label(scope)}, {language or 'untagged'}"
                    dead = [c for c in shown if c not in handled]
                    print(f"  {'ok  ' if not dead else 'DEAD'}  {where_}: {len(shown)} commands"
                          + (f" -- nothing handles {dead}" if dead else ""))
                    findings += bool(dead)
                    if untagged is not None and shown != untagged:
                        print(f"  DIFF  {where_}: not the same list as untagged -- "
                              f"missing {[c for c in untagged if c not in shown]}, "
                              f"extra {[c for c in shown if c not in untagged]}")
                        findings += 1
            hidden = sorted(handled - shown_anywhere)
            print(f"  ....  handled but in no menu: {', '.join('/' + c for c in hidden) or 'none'}")

            self.current = bot
            await self.show_profile(bot)
            for admin in sorted(self.admins):
                served = [c.command for c in await self.router.get_my_commands(scope=BotCommandScopeChat(admin))]
                wanted = next((c for (kind, scope, _), (endpoint, c) in self.profiles[bot].items()
                               if endpoint == "setMyCommands" and self.switches_with_it((kind, scope, None))), None)
                wanted = [c.command for c in (wanted or {}).get("commands") or []] + [SWITCH_COMMAND[0]]
                live = served == wanted
                print(f"  {'ok  ' if live else 'LIVE'}  switched to it, chat {admin} shows its menu and /as"
                      + ("" if live else f" -- shows {served}, not {wanted}"))
                findings += not live

            # Under its own name everywhere it keeps state -- the thing that
            # keeps a run on the shared database off the real bot's.
            names = {part: getattr(sys.modules.get(f"{bot.folder}.{part}"), "_bot_id", None)
                     for part in ("lifecycle", "family_link")}
            wrong = {part: name for part, name in names.items() if name != bot.overlay["FAMILY_BOT_ID"]}
            wrong.update({f"{part}.FAMILY_SCHEMA": module.FAMILY_SCHEMA for part in ("family_link", "db")
                          if (module := sys.modules.get(f"{bot.folder}.{part}")) is not None
                          and getattr(module, "FAMILY_SCHEMA", TEST_FAMILY_SCHEMA) != TEST_FAMILY_SCHEMA})
            print(f"  {'ok  ' if not wrong else 'NAME'}  runs as {bot.overlay['FAMILY_BOT_ID']}"
                  + (f" -- but {wrong}" if wrong else f" in lifecycle and on the bus, family in {TEST_FAMILY_SCHEMA}"))
            findings += bool(wrong)
        self.current = remembered
        if remembered is not None:
            await self.show_profile(remembered)
        print(f"\n{findings} finding(s).")
        return 1 if findings else 0

    def in_context(self, bot: Bot, coroutine):
        return asyncio.get_running_loop().create_task(coroutine, context=bot.context)

    async def run(self, first: str | None, audit: bool = False) -> int:
        self.quiet = audit
        from telegram import Bot as TelegramBot, Update
        from telegram.error import Conflict, NetworkError, TimedOut
        loop = asyncio.get_running_loop()
        loop.add_signal_handler = lambda *args, **kwargs: None   # the bots' own stop handling is not wanted here
        self.router = TelegramBot(os.environ[f"{self.bots[0].prefix}_TOKEN"])
        await self.router.initialize()
        self.me = self.router.bot

        remembered = first or (PERSONA_FILE.read_text(encoding="utf-8").strip() if PERSONA_FILE.is_file() else "")
        self.current = self.find(remembered) if remembered else None
        loaded = [bot for bot in self.bots if bot.state == "loaded"]
        for bot in loaded:
            self.by_bot_object[id(bot.app.bot)] = bot
            bot.context = contextvars.copy_context()
            bot.context.run(_CURRENT.set, bot)
        await asyncio.gather(*(self.in_context(bot, self.start(bot)) for bot in loaded))
        if self.current is not None and self.current.state != "running":
            self.current = None
        running = [bot for bot in self.bots if bot.state == "running"]
        try:
            if not running:
                log.error("No bot could start.")
                return 1
            # Whatever the others published while starting, and whatever an
            # earlier run left up, is taken down now.
            for bot in self.bots:
                for key in self.profiles[bot]:
                    self.applied.setdefault(key, "?")
            if self.current is not None:
                await self.show_profile(self.current, replay=False)     # its own went up as it started
            if audit:
                return await self.audit(running)
            log.info("Polling as @%s: %s. Now %s.", self.me.username, ", ".join(bot.name for bot in running),
                     self.current.name if self.current else "nobody -- send /as in the chat")

            wanted = sorted({kind for bot in running
                             for kind in (bot.polling.get("allowed_updates") or Update.ALL_TYPES)} | {Update.MESSAGE})
            offset, delay = None, 1
            while True:
                try:
                    updates = await _ORIGINAL_POST(self.router, "getUpdates", {
                        "offset": offset, "timeout": POLL_TIMEOUT, "allowed_updates": wanted},
                        read_timeout=POLL_TIMEOUT + 10)
                    delay = 1
                except Conflict:
                    log.warning("Something else is polling this token (another run.ps1?). Trying again in 10s.")
                    await asyncio.sleep(10)
                    continue
                except (NetworkError, TimedOut) as exc:
                    log.info("Poll failed (%s), again in %ss.", exc, delay)
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, 30)
                    continue
                for data in updates:
                    offset = data["update_id"] + 1
                    try:
                        await self.dispatch(data)
                    except Exception:
                        log.exception("Could not hand over update %s", data.get("update_id"))
        finally:
            for bot in running:
                await self.in_context(bot, self.teardown(bot))
            with contextlib.suppress(Exception):
                await self.router.shutdown()
            log.info("Stopped.")


_ORIGINAL_POST = None
_LOCK = None


def patch_python_telegram_bot(switchboard_holder: list) -> None:
    """Three places where python-telegram-bot assumes one bot per process."""
    global _ORIGINAL_POST
    from telegram import Bot as TelegramBot
    from telegram.ext import Application

    original_run_polling = Application.run_polling

    def capture_run_polling(self, *args, **kwargs):
        # Each bot's main() ends here; this is where its app is collected
        # instead of polling.
        bot = _CURRENT.get()
        if bot is None:
            return original_run_polling(self, *args, **kwargs)
        arguments = inspect.signature(original_run_polling).bind(self, *args, **kwargs).arguments
        arguments.pop("self", None)
        bot.app, bot.polling = self, dict(arguments)

    def stop_running(self):
        log.warning("A bot asked to stop; the switchboard keeps it running. Ctrl-C stops everything.")

    _ORIGINAL_POST = TelegramBot._post

    async def post(self, endpoint, data=None, *, api_kwargs=None, **timeouts):
        if not switchboard_holder:
            return await _ORIGINAL_POST(self, endpoint, data, api_kwargs=api_kwargs, **timeouts)
        return await switchboard_holder[0].post(_ORIGINAL_POST, self, endpoint, data, api_kwargs, **timeouts)

    Application.run_polling = capture_run_polling
    Application.stop_running = stop_running
    TelegramBot._post = post


def main(argv: list[str]) -> int:
    setup_logging()
    # Every bot's bot.py calls load_dotenv() on import, which would read its
    # folder's .env -- the real tokens, and ManagerBot's production database.
    import dotenv
    dotenv.load_dotenv = lambda *args, **kwargs: False
    dotenv.main.load_dotenv = dotenv.load_dotenv
    # What was asked for first: a mistyped bot name costs nothing, not a
    # connection to the database.
    bots = [Bot(folder, name, prefix) for folder, name, prefix in BOTS]
    audit = "--audit" in argv
    only = "--only" in argv
    argv = [arg for arg in argv if arg not in ("--audit", "--only")]
    first = argv[0] if argv else None
    if first and not any(first.lower() in bot.names for bot in bots):
        raise SystemExit(f"Unknown bot {first}. Known: {', '.join(bot.role for bot in bots)}.")
    if only:
        if not first:
            raise SystemExit("--only needs a bot: --only convert")
        bots = [bot for bot in bots if first.lower() in bot.names]

    settings = read_settings()
    refuse_real_tokens(settings["TESTBOT_TOKEN"])
    global _LOCK
    _LOCK = hold_the_only_instance()   # held until the process ends
    refuse_writable_shared_database(settings["TESTBOT_DATABASE_URL"])
    prepare_environment(settings, bots)
    dsn = os.environ["DATABASE_URL"]
    print(f"\nTestBot: {bots[0].name + ' alone' if only else 'all five bots, switched from the chat'}\n"
          f"  token     @{settings.get('TESTBOT_USERNAME') or '?'}\n"
          f"  database  {where(dsn)}"
          f"{'' if is_local(dsn) else '  -- the shared one, read-only outside testbot_*'}\n"
          f"  schemas   testbot_<bot>_bot and {TEST_FAMILY_SCHEMA}, bus ids "
          f"{bots[0].overlay['FAMILY_BOT_ID'].rsplit('_', 1)[0]}_<bot>\n"
          f"  Stars     {'sandboxed: credited, never charged' if sandboxed() else 'REAL -- FAMILY_STARS_SANDBOX is off'}\n"
          f"  big files {'on' if os.environ.get('CBOT_API_ID') else 'off (no CBOT_API_ID)'}\n", flush=True)

    sys.meta_path.insert(0, _BotFinder)
    holder: list[Switchboard] = []
    patch_python_telegram_bot(holder)
    for bot in bots:
        register(bot)
        started = time.monotonic()
        if load(bot):
            log.info("%s loaded in %.1fs.", bot.name, time.monotonic() - started)
    if not any(bot.state == "loaded" for bot in bots):
        log.error("No bot could be loaded.")
        return 1
    switchboard = Switchboard(bots, settings)
    holder.append(switchboard)
    try:
        return asyncio.run(switchboard.run(first, audit))
    except (KeyboardInterrupt, asyncio.CancelledError):
        return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
