#!/usr/bin/env python3
"""Every bot in the family: in one process (the default), or one process each.

    python main.py                  run every bot that has a token set, in this one process
    python main.py sticker anon     run only these (same as BOTS=sticker,anon)
    python main.py --separate       run each bot in a process of its own (same as
                                    MUMU_PROCESSES=separate); words pick the bots here too
    python main.py --check          load every bot, prove they are kept apart, run one conversion, exit
    python main.py --list           which bots exist and which have a token
    python main.py --module KEY ... run one module as a program of its own (the conversion
                                    worker and the big-file transfer are started this way)

Each bot is loaded as a package of its own, so `import db` inside it means its
own db. The code lives in bots/: bots/convert_bot/jobs.py is ConvertBot's `jobs`
(key `convert_bot.jobs`), and bots/shared/family_link.py (key `family_link`) is
a module every bot gets a copy of. index() finds them; a bot's modules are its
own folder's plus the shared ones, and only the ones a bot imports are ever
compiled. docs/architecture.md has the whole picture.

With --separate this process is only a supervisor: it loads no bot code, starts
`main.py <bot>` once per bot, restarts one that fails (after 5 s, then longer,
up to 5 minutes) while the others carry on, and passes a stop on to all of them.
"""
from __future__ import annotations

import asyncio
import builtins
import contextlib
import contextvars
import gc
import importlib
import importlib.abc
import importlib.machinery
import importlib.util
import inspect
import json
import logging
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
import types
import weakref
from collections.abc import MutableMapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent

DRAIN_SECONDS = float(os.environ.get("UNIFIED_DRAIN_SECONDS") or 30)

STATUS_MINUTES = float(os.environ.get("UNIFIED_STATUS_MINUTES") or 30)

STARTUP_REPORT_SECONDS = 300

PER_BOT_ONLY = ("DB_SCHEMA", "FAMILY_BOT_ID", "FAMILY_LABEL", "PRIVACY_URL", "TERMS_URL")
PER_BOT_ONLY_PREFIXES = ("PAYMENT_PROVIDER_TOKEN_",)

NOT_OVERRIDES = {"TOKEN", "USERNAME", "ADMIN_ID"}

POLICY_DOCUMENTS = {"PRIVACY_URL": "privacy", "TERMS_URL": "terms"}
LEGAL_URL = "https://github.com/MukhtorovMurodbek/MumuBots/blob/main/LEGAL.md"

BOTS = (
    ("sticker_bot", "StickerBot", "SBOT"),
    ("convert_bot", "ConvertBot", "CBOT"),
    ("downloader_bot", "DownloaderBot", "DBOT"),
    ("anon_bot", "AnonBot", "ABOT"),
    ("manager_bot", "ManagerBot", "MBOT"),
)
# ManagerBot is private: no donations, no /cancel, no translations.
NOT_FOR = {"manager_bot": {"shared_features"}}

CODE = ROOT / "bots"
SHARED_FOLDER = "shared"
_INDEX: dict[str, Path] = {}


def index() -> dict[str, Path]:
    """Every module's key and file: bots/shared/x.py is `x`, bots/<bot>_bot/x.py is `<bot>_bot.x`.
    Each bot gets its own copy of every module it uses (see _BotFinder), so the keys are not
    importable on their own."""
    if not _INDEX:
        for path in sorted(CODE.glob("*/*.py")):
            folder = path.parent.name
            _INDEX[path.stem if folder == SHARED_FOLDER else f"{folder}.{path.stem}"] = path
    return _INDEX


def source(key: str) -> str:
    return index()[key].read_text(encoding="utf-8")


def compiled(key: str):
    path = index()[key]
    return compile(path.read_bytes(), str(path), "exec")


ALIASES = {"parent": "manager_bot", "parentbot": "manager_bot", "chat": "anon_bot"}

log = logging.getLogger("family")


@dataclass(eq=False)
class Bot:
    folder: str
    name: str
    prefix: str
    directory: Path
    shared: tuple = ()
    files: dict = field(default_factory=dict)
    local: frozenset = frozenset()
    import_table: dict | None = None
    dotenv: dict = field(default_factory=dict)
    handlers: list = field(default_factory=list)
    initialized: bool = False
    module: types.ModuleType | None = None
    app: object | None = None
    polling: dict = field(default_factory=dict)
    context: contextvars.Context | None = None
    task: asyncio.Task | None = None
    state: str = "new"
    problem: str = ""
    data_key: bool = False
    # Kept apart per bot in one process: the threads its blocking work runs on,
    # the asyncio tasks it started (so a restart can end them all), and how often
    # in a row it has failed to load or start (how long the next try waits).
    executor: ThreadPoolExecutor | None = None
    tasks: weakref.WeakSet = field(default_factory=weakref.WeakSet)
    failures: int = 0
    retry: asyncio.TimerHandle | None = None
    restarting: bool = False
    quieted: tuple | None = None

    @property
    def names(self) -> set[str]:
        role = self.folder.removesuffix("_bot")
        found = {self.folder, role, self.name.lower(), self.prefix.lower()}
        found |= {alias for alias, folder in ALIASES.items() if folder == self.folder}
        return found

    def token_set(self) -> bool:
        return bool(setting(self, f"{self.prefix}_TOKEN"))

    def read_own_env_file(self) -> None:
        """Run from the development tree, each bot folder has a .env of its own, and what is in it is that bot's alone -- including the names that would otherwise be ignored process-wide, like DB_SCHEMA."""
        env_file = self.directory / ".env"
        if env_file.is_file():
            try:
                from dotenv import dotenv_values
            except ImportError:
                log.warning("%s has a .env but python-dotenv is not installed -- ignoring it.", self.name)
                return
            self.dotenv = {key: value for key, value in dotenv_values(env_file).items() if value}


def discover() -> tuple[list[Bot], str]:
    """The five bots. data/<folder>/ is each one's home: its logs, and its own .env if it has one."""
    shared = [key for key in index() if "." not in key]
    bots = [Bot(folder=folder, name=name, prefix=prefix, directory=ROOT / "data" / folder,
                files={key.split(".", 1)[1]: key for key in index() if key.startswith(folder + ".")},
                shared=tuple(key for key in shared if key not in NOT_FOR.get(folder, ())))
            for folder, name, prefix in BOTS]
    found = re.search(r'RELEASE = os.environ.get\("FAMILY_RELEASE", "([^"]+)"\)',
                      source("family_link") if "family_link" in index() else "")
    release = os.environ.get("FAMILY_RELEASE") or (found.group(1) if found else "?")
    return bots, release


def select(bots: list[Bot], wanted: list[str]) -> list[Bot]:
    if not wanted or wanted == ["all"]:
        return bots
    chosen, unknown = [], []
    for word in wanted:
        match = next((bot for bot in bots if word.lower().lstrip("@") in bot.names), None)
        if match is None:
            unknown.append(word)
        elif match not in chosen:
            chosen.append(match)
    if unknown:
        known = ", ".join(bot.folder.removesuffix("_bot") for bot in bots)
        raise SystemExit(f"Unknown bot(s): {', '.join(unknown)}. Known: {known}.")
    return chosen


def _per_bot_only(key: str) -> bool:
    return key in PER_BOT_ONLY or key.startswith(PER_BOT_ONLY_PREFIXES)


def setting(bot: Bot, key: str) -> str | None:
    """The value of `key` as `bot` sees it, or None. Empty counts as unset."""
    env, own = os.environ, bot.dotenv

    if key.startswith(bot.prefix + "_"):

        value = env.get(key) or own.get(key)
        if not value and key == f"{bot.prefix}_ADMIN_ID":
            value = env.get("ADMIN_ID") or own.get("ADMIN_ID")
        if value and key == f"{bot.prefix}_USERNAME":
            value = value.strip().lstrip("@")
        return value or None

    if key not in NOT_OVERRIDES:
        value = env.get(f"{bot.prefix}_{key}")
        if value:
            return value

    if _per_bot_only(key):
        if own.get(key):
            return own[key]
        if key == "DB_SCHEMA":
            return bot.folder
        legal = (env.get("LEGAL_URL") or LEGAL_URL).strip()
        if key in POLICY_DOCUMENTS and legal and bot.folder != "manager_bot":
            return f"{legal}#{bot.name.lower()}-{POLICY_DOCUMENTS[key]}"
        return None

    return env.get(key) or own.get(key) or None


class BotEnviron(MutableMapping):
    """os.environ as one bot sees it."""

    def __init__(self, bot: Bot):
        self._bot = bot

    def __getitem__(self, key):
        value = setting(self._bot, key)
        if value is None:
            raise KeyError(key)
        return value

    def __setitem__(self, key, value):
        os.environ[key] = value

    def __delitem__(self, key):
        del os.environ[key]

    def _names(self):
        names = set(os.environ) | set(self._bot.dotenv) | {"DB_SCHEMA"}
        for key in os.environ:
            if key.startswith(self._bot.prefix + "_"):
                names.add(key[len(self._bot.prefix) + 1:])
        return names

    def __iter__(self):
        return iter(sorted(name for name in self._names() if setting(self._bot, name) is not None))

    def __len__(self):
        return sum(1 for _ in self)

    def copy(self) -> dict:
        return dict(self)

    def __repr__(self):
        return f"<environment as {self._bot.name} sees it>"


class _BotOs(types.ModuleType):
    """`os` as a bot's modules see it: the real one, except its environment."""

    def __init__(self, bot: Bot):
        super().__init__("os", os.__doc__)
        self.environ = BotEnviron(bot)
        self._bot = bot

    def __getattr__(self, attribute):
        return getattr(os, attribute)

    def getenv(self, key, default=None):
        return self.environ.get(key, default)

    def _exit(self, code):
        """os._exit(), as a bot in the family means it: ManagerBot's /restart
        ends the bot's process with 1 so the host starts it again. Here that
        would end all five, so a running family restarts this bot alone (code
        0: stops it) and the call returns to its caller, whose last line it is.
        With no family running (--check, a worker started with --module) it is
        the real os._exit."""
        family = _FAMILY[0] if _FAMILY else None
        loop = family.loop if family is not None else None
        if loop is None or loop.is_closed() or not loop.is_running():
            os._exit(code)
        loop.call_soon_threadsafe(family.request_restart, self._bot, code)


_PACKAGES: dict[str, Bot] = {}
_CURRENT: contextvars.ContextVar[Bot | None] = contextvars.ContextVar("family_bot", default=None)


class _BotFinder:
    """Finds `<bot>.<module>` for the packages registered below, and nothing else, so no other import in the process can be affected by it."""

    @staticmethod
    def find_spec(fullname, path=None, target=None):
        package, dot, name = fullname.partition(".")
        bot = _PACKAGES.get(package)
        if bot is None or not dot or "." in name:
            return None
        key = bot.files.get(name) or (name if name in bot.shared else None)
        if key is None:
            return None
        loader = _ModuleLoader(key, bot, str(bot.directory / f"{name}.py"))
        spec = importlib.util.spec_from_loader(fullname, loader, origin=str(index()[key]))
        spec.has_location = True
        return spec


class _ModuleLoader(importlib.abc.Loader):
    """One file under bots/, as that bot's own copy of the module."""

    def __init__(self, key, bot, seen_as):
        self.key, self.bot, self.seen_as = key, bot, seen_as

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        module.__builtins__ = self.bot.import_table
        module.__file__ = self.seen_as
        exec(compiled(self.key), module.__dict__)


_LOADING_IN_FAMILY = False


class _BotDotenv(types.ModuleType):

    def __init__(self):
        super().__init__("dotenv")

    def __getattr__(self, attribute):
        import dotenv
        return getattr(dotenv, attribute)

    @staticmethod
    def load_dotenv(*args, **kwargs):
        if _LOADING_IN_FAMILY:
            return False
        import dotenv
        return dotenv.load_dotenv(*args, **kwargs)


class _BotImportlib(types.ModuleType):
    """`importlib` as a bot's modules see it."""

    def __init__(self, bot: Bot):
        super().__init__("importlib", importlib.__doc__)
        self._bot = bot

    def __getattr__(self, attribute):
        return getattr(importlib, attribute)

    def import_module(self, name, package=None):
        if name in self._bot.local:
            return importlib.import_module(f"{self._bot.folder}.{name}")
        return importlib.import_module(name, package)


def _builtins_for(bot: Bot) -> dict:
    real_import = builtins.__import__
    proxies = {"importlib": _BotImportlib(bot), "os": _BotOs(bot), "dotenv": _BotDotenv()}

    def bot_import(name, globals=None, locals=None, fromlist=(), level=0):
        if level == 0:
            if name in bot.local:
                return importlib.import_module(f"{bot.folder}.{name}")
            top = name.partition(".")[0]
            if top in proxies and (name == top or not fromlist):
                real_import(name, globals, locals, fromlist, level)
                return proxies[top]
        return real_import(name, globals, locals, fromlist, level)

    table = dict(vars(builtins))
    table["__import__"] = bot_import
    return table


def register(bot: Bot) -> None:
    names = set(bot.files) | set(bot.shared)
    bot.local = frozenset(names)
    bot.import_table = _builtins_for(bot)
    package = types.ModuleType(bot.folder)
    package.__path__ = []
    package.__package__ = bot.folder
    sys.modules[bot.folder] = package
    _PACKAGES[bot.folder] = bot


def forget(bot: Bot) -> None:
    bot.quieted = None
    db = sys.modules.get(f"{bot.folder}.db")
    close = getattr(db, "close_pool", None)
    if callable(close):
        with contextlib.suppress(Exception):
            close()
    for logger, handler in bot.handlers:
        logger.removeHandler(handler)
        with contextlib.suppress(Exception):
            handler.close()
    bot.handlers.clear()
    for name in [name for name in sys.modules if name == bot.folder or name.startswith(bot.folder + ".")]:
        del sys.modules[name]
    bot.module = bot.app = None


@contextlib.contextmanager
def bot_environment(bot: Bot):
    """Whatever a bot writes into the real environment while it loads -- its own load_dotenv() does, in the development tree -- is undone afterwards, so the next bot does not inherit it."""
    saved = dict(os.environ)
    try:
        yield
    finally:
        # Put back only what changed: a bot may be loading again while the
        # others run and read the environment, so it is never emptied, even
        # for a moment.
        for key in [key for key in os.environ if key not in saved]:
            os.environ.pop(key, None)
        for key, value in saved.items():
            if os.environ.get(key) != value:
                os.environ[key] = value


_FORMAT = "%(asctime)s %(bot)-13s %(levelname)-7s %(where)s: %(message)s"


def _install_log_tagging() -> None:
    """Stamp every record with the bot it came from: by logger name when the logger belongs to a bot package, otherwise by the bot whose task or thread wrote it -- which is how python-telegram-bot's and psycopg's own lines get attributed."""
    base = logging.getLogRecordFactory()

    def factory(*args, **kwargs):
        record = base(*args, **kwargs)
        package, _, rest = record.name.partition(".")
        bot = _PACKAGES.get(package)
        if bot is not None:
            record.where = rest or package
        else:
            bot = _CURRENT.get()
            record.where = record.name
        record.bot = bot.name if bot is not None else "family"
        return record

    logging.setLogRecordFactory(factory)


class _OnlyBot(logging.Filter):
    def __init__(self, name: str):
        super().__init__()
        self.bot_name = name

    def filter(self, record) -> bool:
        return getattr(record, "bot", None) == self.bot_name


@contextlib.contextmanager
def adopting_handlers(bot: Bot):
    """While a bot loads, every handler it attaches to the root or `problems` logger is filtered down to that bot's own records -- so its /logs and its log files are its own -- and its console handler is dropped, because this process already prints one line per record for everybody."""
    root, problems = logging.getLogger(), logging.getLogger("problems")

    def adopt(logger):
        original = logger.addHandler

        def add(handler):
            if logger is root and type(handler) is logging.StreamHandler:
                return
            handler.addFilter(_OnlyBot(bot.name))
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


def setup_logging() -> None:
    """Information to stdout, warnings and errors to stderr: hosts like Railway mark a line's level by the stream it came on, so this is what makes an error show as an error in their log viewer."""
    _install_log_tagging()
    formatter = logging.Formatter(_FORMAT)
    ordinary = logging.StreamHandler(sys.stdout)
    ordinary.addFilter(lambda record: record.levelno < logging.WARNING)
    alarming = logging.StreamHandler(sys.stderr)
    alarming.setLevel(logging.WARNING)
    root = logging.getLogger()
    for handler in (ordinary, alarming):
        handler.setFormatter(formatter)
        root.addHandler(handler)
    root.setLevel(logging.INFO)
    for noisy in ("httpx", "httpcore", "telegram.ext.Updater", "apscheduler"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    logging.captureWarnings(True)


PLACEHOLDER = re.compile(r"<[^<>\s]*>")

DB_WAIT_SECONDS = float(os.environ.get("UNIFIED_DB_WAIT_SECONDS") or 120)


def _where(dsn: str) -> str:
    try:
        parts = urlsplit(dsn)
        return f"{parts.hostname or '?'}:{parts.port or 5432}"
    except ValueError:
        return "?"


DB_RETRY_MAX_SECONDS = 60
DB_WAIT_REPORT_SECONDS = 300


def _database_problem(dsn: str, sleep=time.sleep, clock=time.monotonic) -> str | None:
    import psycopg

    where = _where(dsn)
    started = clock()
    pause, reported = 10.0, started
    while True:
        try:
            with psycopg.connect(dsn, connect_timeout=10) as connection:
                connection.execute("SELECT 1")
            if clock() - started > DB_WAIT_SECONDS:
                log.warning("The database at %s answers again after %.0f minutes of waiting.",
                            where, (clock() - started) / 60)
            return None
        except Exception as exc:
            first = (str(exc).strip().splitlines() or [type(exc).__name__])[0]
            said = first.lower()
            waited = clock() - started
            if ("resolve host" in said or "name or service not known" in said
                    or "nodename nor servname" in said) and waited >= DB_WAIT_SECONDS:
                return (f"the database host in DATABASE_URL does not exist ({where}). "
                        f"The connection string was cut short or mistyped.")
            if "password authentication failed" in said:
                return (f"the database at {where} refused the password in DATABASE_URL. "
                        f"Characters like @ : / ? # and spaces in a password have to be "
                        f"percent-encoded (@ is %40).")
            if "tenant or user not found" in said:
                return (f"the pooler at {where} does not know the user in DATABASE_URL. "
                        f"Supabase's pooler wants postgres.<project ref> as the user.")
            if waited < DB_WAIT_SECONDS:
                log.warning("The database at %s is not answering yet (%s) -- trying again in %.0fs.",
                            where, first, pause)
            elif clock() - reported >= DB_WAIT_REPORT_SECONDS:
                reported = clock()
                log.error("The database at %s has not answered for %.0f minutes (%s). Still waiting: "
                          "ending would only use up the host's restarts.", where, waited / 60, first)
            sleep(pause)
            if waited >= DB_WAIT_SECONDS:
                pause = min(DB_RETRY_MAX_SECONDS, pause * 1.5)


def _database_answers(bots: list[Bot]) -> bool:
    import psycopg

    for dsn in sorted({setting(bot, "DATABASE_URL") for bot in bots} - {None}):
        try:
            with psycopg.connect(dsn, connect_timeout=10) as connection:
                connection.execute("SELECT 1")
        except Exception:
            return False
    return True


def preflight(bots: list[Bot]) -> str | None:
    """Everything wrong with the settings these bots depend on, or None."""
    problems: list[str] = []

    def problem(text):
        if text not in problems:
            problems.append(text)

    for bot in bots:
        for key in (f"{bot.prefix}_TOKEN", f"{bot.prefix}_USERNAME", f"{bot.prefix}_ADMIN_ID"):
            value = setting(bot, key) or ""
            name = "ADMIN_ID" if key.endswith("_ADMIN_ID") and not (
                os.environ.get(key) or bot.dotenv.get(key)) else key
            if PLACEHOLDER.search(value):
                problem(f"{name} still holds an example value from .env.example")
            elif value and (value != value.strip() or value[0] in "\"'"):
                problem(f"{name} has spaces or quotes around it")
        admins = setting(bot, f"{bot.prefix}_ADMIN_ID")
        if admins and not PLACEHOLDER.search(admins) and not all(
                part.strip().isdigit() for part in admins.split(",") if part.strip()):
            problem("ADMIN_ID must be numeric Telegram user ids, comma-separated -- "
                    "not usernames (@userinfobot shows the number)")

    urls = {setting(bot, "DATABASE_URL") for bot in bots}
    if None in urls:
        problem("DATABASE_URL is not set. Nothing runs without it: the Supabase connection "
                "string, from Project Settings -> Database -> Session pooler (port 5432)")
    for dsn in sorted(url for url in urls if url):
        if PLACEHOLDER.search(dsn):
            problem("DATABASE_URL is still the example from .env.example -- <ref>, <password> "
                    "and <region> are meant to be replaced. Paste the real one from Supabase: "
                    "Project Settings -> Database -> Session pooler (port 5432)")
        elif not dsn.startswith(("postgresql://", "postgres://")):
            problem("DATABASE_URL should start with postgresql://")
    if problems:
        return "; ".join(problems) + "."

    for dsn in sorted(url for url in urls if url):
        found = _database_problem(dsn)
        if found:
            return found[0].upper() + found[1:]
    return None


def _libc():
    if sys.platform != "linux":
        return None
    try:
        import ctypes
        return ctypes.CDLL("libc.so.6")
    except OSError:
        return None


_LIBC = _libc()


def cap_malloc_arenas() -> None:
    """glibc gives a threaded process up to 8 x cpu_count malloc arenas, and cpu_count in a container is the host's."""
    if _LIBC is not None and "MALLOC_ARENA_MAX" not in os.environ:
        with contextlib.suppress(Exception):
            _LIBC.mallopt(-8, 2)


def give_back_memory() -> None:
    """Return freed heap pages to the kernel."""
    gc.collect()
    if _LIBC is not None:
        with contextlib.suppress(Exception):
            _LIBC.malloc_trim(0)


def footprint() -> str:
    numbers = {}
    with contextlib.suppress(OSError):
        with open("/proc/self/status") as status:
            for line in status:
                key, _, value = line.partition(":")
                if key in ("VmRSS", "VmHWM", "Threads"):
                    numbers[key] = int(value.split()[0])
    if "VmRSS" not in numbers:
        return "memory not readable here"
    return (f"{numbers['VmRSS'] // 1024} MB resident (peak {numbers.get('VmHWM', 0) // 1024} MB), "
            f"{numbers.get('Threads', '?')} threads")


_ORIGINAL_RUN_POLLING = None


def _capture_run_polling(self, *args, **kwargs):
    """Each bot's main() ends in app.run_polling(), which would take the event loop for itself and never return."""
    bot = _CURRENT.get()
    if bot is None:
        return _ORIGINAL_RUN_POLLING(self, *args, **kwargs)
    arguments = inspect.signature(_ORIGINAL_RUN_POLLING).bind(self, *args, **kwargs).arguments
    arguments.pop("self", None)
    bot.app, bot.polling = self, dict(arguments)


def load(bot: Bot, wire: bool = True) -> bool:
    """Import the bot and run its main() as far as run_polling."""
    token = _CURRENT.set(bot)
    try:
        with bot_environment(bot), adopting_handlers(bot):
            bot.module = importlib.import_module(f"{bot.folder}.bot")
            if wire:
                bot.module.main()
                if bot.app is None:
                    raise RuntimeError("main() returned without starting to poll")
        bot.state = "loaded"
        return True
    except SystemExit as exc:
        bot.state, bot.problem = "skipped", str(exc)
        bot.data_key = bool(getattr(exc, "data_key", False))
        log.warning("%s is not running: %s", bot.name, exc)
    except Exception as exc:
        bot.state, bot.problem = "failed", f"{type(exc).__name__}: {exc}"
        bot.data_key = bool(getattr(exc, "data_key", False))
        log.exception("%s could not be loaded", bot.name)
    finally:
        _CURRENT.reset(token)
    forget(bot)
    return False

# Until 2.2.0 two things ended the whole process here: the data key guard
# refusing any bot, and ManagerBot failing to load. Neither does now (the owner,
# 2026-10-23: "if a functionality is not working properly, the rest of the bots
# should not be affected"). A wrong or missing key switches off what needs it
# (family_link), and a bot that fails is tried again on its own (Family.retry).


def _will_run(bot: Bot) -> bool:
    """Running, or to be tried again: not a bot that ended for good."""
    return bot.state not in ("skipped", "stopped") or (bot.state == "skipped" and bot.data_key)


def memory_reporter(bots: list[Bot]) -> Bot | None:
    """The one bot that records the service's memory (since 2.2.0 the container's,
    its workers included: the `resources` module), for ManagerBot's /usage and
    the memory alarm, so that /usage counts it once: ManagerBot when this service
    runs it -- also while it waits to be tried again, so that two bots never both
    report -- else the first bot that loaded. In a bot's own process
    (MUMU_PROCESSES=separate) the same rule over the whole service, MUMU_SERVICE_BOTS:
    None when the reporter is another process."""
    if os.environ.get(CHILD_SETTING):
        service = [folder for folder in (os.environ.get(SERVICE_BOTS_SETTING) or "").split(",") if folder]
        if service:
            chosen = "manager_bot" if "manager_bot" in service else service[0]
            return next((bot for bot in bots if bot.folder == chosen), None)
    manager = next((bot for bot in bots if bot.folder == "manager_bot" and _will_run(bot)), None)
    if manager is not None:
        return manager
    loaded = [bot for bot in bots if bot.state in ("loaded", "starting", "running")]
    return loaded[0] if loaded else None


def quiet_memory_reports(bot: Bot, reporter: Bot | None) -> None:
    if bot is reporter or bot.quieted is not None:
        return
    link = sys.modules.get(f"{bot.folder}.family_link")
    monitor = (sys.modules.get(f"{bot.folder}.shared_features")
               or sys.modules.get(f"{bot.folder}.monitoring"))
    if not (hasattr(link, "record_usage") and hasattr(monitor, "_check_usage_alarms")):
        log.warning("%s: could not route memory reports through %s -- /usage will "
                    "count this service more than once", bot.name,
                    reporter.name if reporter is not None else "another process")
        return
    record, alarms = link.record_usage, monitor._check_usage_alarms

    def record_usage(window, rss, peak, ceiling, cpu, *rest, _record=record, **named):
        return _record(window, None, None, None, None, *rest, **named)

    def check_alarms(numbers, *rest, _alarms=alarms, **named):
        quiet = dict(numbers, rss_mb=None, peak_rss_mb=None, ceiling_mb=None)
        return _alarms(quiet, *rest, **named)

    link.record_usage = record_usage
    monitor._check_usage_alarms = check_alarms
    bot.quieted = (link, record, monitor, alarms)


def report_memory_again(bot: Bot) -> bool:
    if bot.quieted is None:
        return False
    link, record, monitor, alarms = bot.quieted
    link.record_usage = record
    monitor._check_usage_alarms = alarms
    bot.quieted = None
    return True


def one_memory_reporter(bots: list[Bot]) -> None:
    reporter = memory_reporter(bots)
    for bot in bots:
        if bot.state == "loaded":
            quiet_memory_reports(bot, reporter)


# The running family, while there is one: how a bot's os._exit() reaches it.
_FAMILY: list = []

# Threads for each bot's blocking work (database, file I/O, yt-dlp, the album
# PDF): a pool of its own per bot since 2.2.0, so one bot's stuck threads --
# a dead network, a slow download -- cannot hold up another bot's database
# calls. 2.1.0 shared 16 between all five; on 2026-10-09, with 8, every bot's
# once-a-second database job was skipped for a minute and a half. An idle
# thread costs next to nothing, and threads are only made as work needs them:
# 16, since DownloaderBot may run up to 8 downloads (each holds a thread for
# yt-dlp) on a large service, and its database calls still need some.
THREADS_PER_BOT = int(os.environ.get("WORKER_THREADS_PER_BOT") or 16)

# When not one bot has been running for this long -- each failing and waiting
# for its next try -- the process ends non-zero, so the host starts it afresh.
NOTHING_RUNNING_SECONDS = float(os.environ.get("UNIFIED_NOTHING_RUNNING_SECONDS") or 900)

# The longest one step of stopping a bot (its updater, app.stop(), post_stop,
# shutdown, post_shutdown) may take before the next step goes ahead without it.
TEARDOWN_STEP_SECONDS = float(os.environ.get("UNIFIED_TEARDOWN_STEP_SECONDS") or 60)


def end_now(code: int) -> None:
    if _KEY_COPY:
        with contextlib.suppress(OSError):
            os.remove(_KEY_COPY)
    logging.shutdown()
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(Exception):
            stream.flush()
    os._exit(code)


class Family:
    """The bots of one process, on one event loop.

    Each is started, stopped and -- when it fails to load or to start, or when
    ManagerBot's /restart asks -- loaded again from its files on its own,
    after RESTART_DELAYS, while the others carry on. Nothing one bot does ends
    the others: the process ends when it is told to stop, when every bot has
    stopped, or when none has run for NOTHING_RUNNING_SECONDS."""

    def __init__(self, bots: list[Bot], retrying: list[Bot] = ()):
        self.retrying = [bot for bot in retrying if bot not in bots]
        self.bots = list(bots) + self.retrying
        self.by_app = {id(bot.app): bot for bot in bots}
        self.stopping = False
        self.waiting: set[Bot] = set()
        self.signal_handlers: dict[int, list] = {}
        self.stop_event: asyncio.Event | None = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self.exit_code = 0
        self.shared_executor: ThreadPoolExecutor | None = None

    def take_signals(self) -> None:
        loop = self.loop
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, self.on_signal, sig)
            except (NotImplementedError, RuntimeError, ValueError):

                with contextlib.suppress(ValueError, OSError):
                    signal.signal(sig, lambda number, frame: loop.call_soon_threadsafe(self.on_signal, number))
        loop.add_signal_handler = self._register_signal
        loop.remove_signal_handler = self._unregister_signal

    def _register_signal(self, sig, callback, *args) -> None:
        self.signal_handlers.setdefault(int(sig), []).append((_CURRENT.get(), callback, args))

    def _unregister_signal(self, sig) -> bool:
        bot = _CURRENT.get()
        kept = [entry for entry in self.signal_handlers.get(int(sig), []) if entry[0] is not bot]
        self.signal_handlers[int(sig)] = kept
        return True

    def on_signal(self, sig: int) -> None:
        try:
            name = signal.Signals(sig).name
        except ValueError:
            name = f"signal {sig}"
        if self.stopping:
            log.warning("%s again -- stopping now, without waiting for the bots to finish.", name)
            end_now(1)
            return
        self.stopping = True
        for bot in self.bots:
            if bot.retry is not None:
                bot.retry.cancel()
                bot.retry = None
        running = [bot for bot in self.bots if bot.state == "running"]
        log.info("%s -- stopping %d bot(s).", name, len(running))
        for bot in self.bots:
            if bot.task is not None and not bot.task.done():
                bot.task.cancel()
        handlers = [entry for entry in self.signal_handlers.get(sig, [])
                    if entry[0] is None or entry[0].state == "running"]
        self.waiting = {bot for bot, _, _ in handlers if bot is not None}
        for bot, callback, args in handlers:
            try:
                (bot.context.run(callback, *args) if bot is not None else callback(*args))
            except Exception:
                log.exception("%s: stop handler failed", bot.name if bot else "?")
        if not self.waiting:
            self.stop_event.set()
        else:
            self.loop.call_later(DRAIN_SECONDS, self.stop_event.set)

    def on_stop_running(self, app) -> None:
        """Application.stop_running(), as this process understands it."""
        bot = self.by_app.get(id(app))
        if bot is None:
            return
        if self.stopping:
            self.waiting.discard(bot)
            if not self.waiting:
                self.stop_event.set()
            return
        log.info("%s asked to stop; the other bots carry on.", bot.name)
        self.loop.create_task(self.stop_one(bot), context=bot.context)

    async def _initialize(self, bot: Bot) -> None:
        """app.initialize() is where the bot first talks to Telegram and reads its saved state."""
        from telegram.error import InvalidToken, NetworkError
        delay, attempt = 5, 0
        while True:
            try:
                await bot.app.initialize()
                return
            except InvalidToken:
                raise
            except Exception as exc:
                attempt += 1
                if self.stopping or (not isinstance(exc, NetworkError) and attempt >= 6):
                    raise
                log.warning("Could not start yet (%s: %s) -- trying again in %ss.",
                            type(exc).__name__, exc, delay)
                await asyncio.sleep(delay)
                delay = min(delay * 2, 120)

    def _polling_arguments(self, bot: Bot) -> dict:
        app = bot.app
        wanted = ("poll_interval", "timeout", "bootstrap_retries", "allowed_updates",
                  "drop_pending_updates")
        arguments = {key: value for key, value in bot.polling.items() if key in wanted}
        if not arguments.get("bootstrap_retries"):
            arguments["bootstrap_retries"] = -1

        def error_callback(exc):
            app.create_task(app.process_error(error=exc, update=None))

        arguments["error_callback"] = error_callback
        return arguments

    async def start(self, bot: Bot) -> None:
        app = bot.app
        bot.state = "starting"
        try:
            await self._initialize(bot)
            bot.initialized = True
            if app.post_init:
                await app.post_init(app)
            await app.updater.start_polling(**self._polling_arguments(bot))
            await app.start()
        except asyncio.CancelledError:
            bot.state = "stopped"
            await self.teardown(bot)
            raise
        except Exception as exc:
            bot.state, bot.problem = "failed", f"{type(exc).__name__}: {exc}"
            # A token Telegram rejects stays rejected until the settings change,
            # which takes a redeploy: that one is not tried again.
            again = type(exc).__name__ != "InvalidToken" and not self.stopping
            log.exception("Could not start -- %s",
                          "it will be tried again; the other bots carry on" if again else "the other bots carry on")
            await self.teardown(bot)
            if again:
                self.retry(bot)
            self.end_if_nothing_left()
            return
        bot.state = "running"
        bot.failures = 0
        log.info("Polling as @%s.", app.bot.username)

    async def teardown(self, bot: Bot) -> None:
        """Everything run_polling does on its way out, in the same order."""
        app = bot.app
        steps = []
        if app.updater is not None and app.updater.running:
            steps.append(app.updater.stop)
        if app.running:
            steps.append(app.stop)
        if bot.initialized:

            if app.post_stop:
                steps.append(lambda: app.post_stop(app))
            steps.append(app.shutdown)
            if app.post_shutdown:
                steps.append(lambda: app.post_shutdown(app))
        for step in steps:
            try:
                # Bounded: app.stop() waits for every handler still running, and one
                # stuck on a dead connection would hold a restart, or the process's
                # end, for ever.
                await asyncio.wait_for(step(), TEARDOWN_STEP_SECONDS)
            except asyncio.TimeoutError:
                log.error("%s: a step of stopping took over %.0fs -- going on without it.",
                          bot.name, TEARDOWN_STEP_SECONDS)
            except Exception:
                log.exception("Error while stopping")
        if not bot.initialized:

            close = getattr(sys.modules.get(f"{bot.folder}.db"), "close_pool", None)
            if callable(close):
                with contextlib.suppress(Exception):
                    await asyncio.to_thread(close)

    def hand_over_memory_reports(self) -> None:
        if self.stopping:
            return
        reporter = memory_reporter(self.bots)
        if reporter is not None and reporter.state == "running" and report_memory_again(reporter):
            log.info("%s reports the service's memory from now on.", reporter.name)

    async def stop_one(self, bot: Bot) -> None:
        await self.teardown(bot)
        bot.state = "stopped"
        self.hand_over_memory_reports()
        if not any(other.state in ("running", "starting", "loading") or other.retry is not None
                   for other in self.bots):
            self.stop_event.set()

    def end_if_nothing_left(self) -> None:
        """No bot running, starting, loading or waiting for its next try: end
        non-zero, so the host starts the process again."""
        if self.stopping or self.stop_event is None:
            return
        if any(bot.state in ("loaded", "starting", "running", "loading") or bot.retry is not None
               for bot in self.bots):
            return
        log.error("No bot is running, and none will be tried again.")
        self.exit_code = 1
        self.stop_event.set()

    # --- one bot again, alone ----------------------------------------------------

    def retry(self, bot: Bot) -> None:
        """Load and start `bot` again after a while: 5 s after its first failure in
        a row, twice as long each time after that, at most 5 minutes."""
        if self.stopping:
            return
        delay = RESTART_DELAYS[min(bot.failures, len(RESTART_DELAYS) - 1)]
        bot.failures += 1
        if bot.retry is not None:
            bot.retry.cancel()
        bot.state = "waiting"
        log.warning("%s will be loaded again in %ss.", bot.name, delay)
        bot.retry = self.loop.call_later(delay, self._reload_now, bot)

    def _reload_now(self, bot: Bot) -> None:
        bot.retry = None
        # In a context of its own, so the reload is not counted as one of the bot's own tasks.
        self.loop.create_task(self.reload(bot), context=contextvars.Context())

    def request_restart(self, bot: Bot, code: int) -> None:
        """What a bot's os._exit(code) means in this process: 0 stops that bot,
        anything else restarts it (ManagerBot's /restart). Never the others."""
        if self.stopping or bot not in self.bots:
            return
        if code == 0:
            log.info("%s asked to stop; the other bots carry on.", bot.name)
            self.loop.create_task(self.stop_one(bot), context=bot.context)
            return
        if bot.restarting:
            # /restart twice, or the bus delivering it twice: one restart is the
            # answer to both. Two at once would tear down and load one bot twice
            # over, on two threads.
            log.info("%s is already restarting.", bot.name)
            return
        log.warning("%s: restarting it alone, as asked; the other bots carry on.", bot.name)
        bot.restarting = True
        bot.failures = 0
        if bot.retry is not None:
            bot.retry.cancel()
            bot.retry = None
        self.loop.create_task(self.restart(bot), context=contextvars.Context())

    async def restart(self, bot: Bot) -> None:
        try:
            if bot.task is not None and not bot.task.done():
                bot.task.cancel()
                with contextlib.suppress(BaseException):
                    await bot.task
            elif bot.state == "running":
                await self.loop.create_task(self.teardown(bot), context=bot.context)
            bot.state = "stopped"
            await self.reload(bot)
        finally:
            bot.restarting = False

    async def reload(self, bot: Bot) -> None:
        """Load the bot again from its files, from scratch, and start it."""
        if self.stopping:
            return
        bot.state = "loading"
        self.end_tasks(bot)
        self.drop_executor(bot)
        self.signal_handlers = {sig: [entry for entry in entries if entry[0] is not bot]
                                for sig, entries in self.signal_handlers.items()}
        self.by_app.pop(id(bot.app), None)
        loaded = await self.loop.run_in_executor(self.shared_executor, self._load_again, bot)
        if self.stopping:
            if loaded:
                forget(bot)
            return
        if not loaded:
            if bot.state == "skipped" and not bot.data_key:
                log.error("%s is not running: %s. A restart cannot cure that, so it is not tried again.",
                          bot.name, bot.problem)
                self.hand_over_memory_reports()
                self.end_if_nothing_left()
            else:
                self.retry(bot)
            return
        bot.initialized = False
        self.by_app[id(bot.app)] = bot
        quiet_memory_reports(bot, memory_reporter(self.bots))
        log.info("%s loaded again.", bot.name)
        self.launch(bot)

    @staticmethod
    def _load_again(bot: Bot) -> bool:
        """On a worker thread: the bot's old modules go, and new ones are made from
        its files, while the other bots keep running on the event loop."""
        forget(bot)
        register(bot)
        return load(bot)

    def launch(self, bot: Bot) -> None:
        """Start a loaded bot in a context, with threads, of its own."""
        bot.executor = ThreadPoolExecutor(max_workers=THREADS_PER_BOT,
                                          thread_name_prefix=bot.folder.removesuffix("_bot"))
        bot.context = contextvars.copy_context()
        bot.context.run(_CURRENT.set, bot)
        # "starting" from now, not from when the task first runs: a bot that fails
        # at once must not find the others still "loaded" and conclude that
        # nothing is left.
        bot.state = "starting"
        bot.task = self.loop.create_task(self.start(bot), context=bot.context)

    @staticmethod
    def end_tasks(bot: Bot) -> None:
        """Cancel every task the bot started (its jobs, pumps, pending sends), so
        nothing of the old copy is left running beside the new one."""
        current = asyncio.current_task()
        for task in list(bot.tasks):
            if task is not current and not task.done():
                task.cancel()
        bot.tasks = weakref.WeakSet()

    @staticmethod
    def drop_executor(bot: Bot) -> None:
        if bot.executor is not None:
            bot.executor.shutdown(wait=False, cancel_futures=True)
            bot.executor = None

    # --- what the bots share, kept apart ---------------------------------------------

    def _take_executor(self) -> None:
        """Blocking work goes to the threads of the bot that asked: asyncio.to_thread()
        and run_in_executor(None, ...) look at which bot's context they run in. Work
        that is no bot's (a reload, the family's own) has a small pool of its own."""
        self.shared_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="family")
        self.loop.set_default_executor(self.shared_executor)
        original = self.loop.run_in_executor

        def run_in_executor(executor, func, *args):
            if executor is None:
                bot = _CURRENT.get()
                if bot is not None and bot.executor is not None:
                    executor = bot.executor
            return original(executor, func, *args)

        self.loop.run_in_executor = run_in_executor

        def keep_the_shared_one(executor):

            executor.shutdown(wait=False)

        self.loop.set_default_executor = keep_the_shared_one

    def _track_tasks(self) -> None:
        """Every task is noted against the bot that started it (by the context it
        starts in), so a restart can end that bot's tasks and no other's."""
        def factory(loop, coro, **named):
            task = asyncio.Task(coro, loop=loop, **named)
            context = named.get("context")
            bot = context.get(_CURRENT) if context is not None else _CURRENT.get()
            if bot is not None:
                bot.tasks.add(task)
            return task

        self.loop.set_task_factory(factory)

    async def _spectate(self) -> None:
        while True:
            await asyncio.sleep(STATUS_MINUTES * 60)
            give_back_memory()
            log.info("%s · %s", self.board(), footprint())

    async def _watch_for_nothing(self) -> None:
        idle_since = None
        while True:
            await asyncio.sleep(min(60.0, NOTHING_RUNNING_SECONDS / 4))
            if any(bot.state == "running" for bot in self.bots):
                idle_since = None
                continue
            idle_since = idle_since or time.monotonic()
            if time.monotonic() - idle_since >= NOTHING_RUNNING_SECONDS:
                if not await self.loop.run_in_executor(self.shared_executor, _database_answers, self.bots):
                    log.error("No bot has been running for %.0f minutes, and the database is not answering. "
                              "Waiting for it: a restart would not bring it back, and the host gives up "
                              "after a few.", (time.monotonic() - idle_since) / 60)
                    idle_since = time.monotonic()
                    continue
                log.error("No bot has been running for %.0f minutes -- ending, so the host starts afresh.",
                          NOTHING_RUNNING_SECONDS / 60)
                self.exit_code = 1
                self.stop_event.set()
                return

    def board(self) -> str:
        up = [bot.name for bot in self.bots if bot.state == "running"]
        down = [f"{bot.name} {bot.state}" for bot in self.bots if bot.state != "running"]
        return f"{len(up)}/{len(self.bots)} polling" + (f" (not: {', '.join(down)})" if down else "")

    async def run(self) -> int:
        self.loop = asyncio.get_running_loop()
        self.stop_event = asyncio.Event()
        self.take_signals()
        self._take_executor()
        self._track_tasks()
        first = [bot for bot in self.bots if bot not in self.retrying]
        for bot in first:
            self.launch(bot)
        for bot in self.retrying:
            self.retry(bot)
        starting = asyncio.gather(*(bot.task for bot in first), return_exceptions=True)
        stopped = self.loop.create_task(self.stop_event.wait())
        watcher = self.loop.create_task(self._watch_for_nothing())
        spectator = None
        try:

            await asyncio.wait({starting, stopped}, timeout=STARTUP_REPORT_SECONDS,
                               return_when=asyncio.FIRST_COMPLETED)
            if not stopped.done():

                give_back_memory()
                gc.freeze()
                log.info("%s · %s", self.board(), footprint())
                if STATUS_MINUTES > 0:
                    spectator = self.loop.create_task(self._spectate())
                await stopped
        finally:
            self.stopping = True
            for task in (spectator, watcher):
                if task is not None:
                    task.cancel()
            for bot in self.bots:
                if bot.retry is not None:
                    bot.retry.cancel()
                    bot.retry = None
            # A bot still starting -- Telegram not answering, say -- is stopped on
            # every way out, not only on a signal (start() tears it down itself):
            # otherwise the wait below never ends, and a process that meant to end
            # with 1 so the host starts it afresh stays up with nothing polling.
            unfinished = [bot.task for bot in self.bots if bot.task is not None and not bot.task.done()]
            for task in unfinished:
                task.cancel()
            await asyncio.gather(starting, *unfinished, return_exceptions=True)
            stopped.cancel()
            await asyncio.gather(*(self._stop_in_context(bot) for bot in self.bots
                                   if bot.state == "running"), return_exceptions=True)
            for bot in self.bots:
                self.drop_executor(bot)
            # asyncio.run() joins the default pool when this returns, with no limit
            # (Python 3.11): a reload still loading must not hold the end.
            self.shared_executor.shutdown(wait=False, cancel_futures=True)
            self.loop._default_executor = None
            log.info("Stopped.")
        if self.exit_code:
            log.error("Ending with exit code %s.", self.exit_code)
        return self.exit_code

    async def _stop_in_context(self, bot: Bot) -> None:
        task = self.loop.create_task(self.stop_one(bot), context=bot.context)
        await task


def patch_python_telegram_bot(family_holder: list = _FAMILY) -> None:
    """The two places where python-telegram-bot assumes it owns the process."""
    global _ORIGINAL_RUN_POLLING
    from telegram.ext import Application

    _ORIGINAL_RUN_POLLING = Application.run_polling
    Application.run_polling = _capture_run_polling

    def stop_running(self):
        if family_holder:
            family_holder[0].on_stop_running(self)
    Application.stop_running = stop_running


def _load_dotenv() -> None:
    env_file = ROOT / ".env"
    if env_file.is_file():
        try:
            from dotenv import load_dotenv
            load_dotenv(env_file, override=False)
        except ImportError:
            log.warning(".env found but python-dotenv is not installed -- ignoring it.")


def _clear_per_bot_settings() -> None:
    for key in [key for key in os.environ if _per_bot_only(key)]:
        if os.environ[key]:
            shown = "<set>" if key.startswith(PER_BOT_ONLY_PREFIXES) else os.environ[key]
            log.warning("Ignoring %s=%s: in one process it would apply to every bot. "
                        "Give it to one bot with its prefix, e.g. SBOT_%s.", key, shown, key)
        del os.environ[key]


KEY_VARIABLE = "DATA_KEY"
_KEY_COPY: "str | None" = None


def _key_from_variable() -> None:
    """DATA_KEY (a sealed variable on Railway) in place of a key file on a volume. The key goes to
    a private file in memory-backed /dev/shm for this run's bots to read, and out of the
    environment, so ffmpeg, yt-dlp, the conversion workers and the rest never inherit it."""
    global _KEY_COPY
    text = os.environ.pop(KEY_VARIABLE, "").strip()
    if not text:
        return
    if not re.fullmatch(r"[0-9a-fA-F]{64}|[A-Za-z0-9+/]{43}=", text):
        log.critical("%s is set but holds no data key (32 bytes, as base64 or 64 hex digits); "
                     "it is ignored.", KEY_VARIABLE)
        return
    if os.environ.get("DATA_KEY_FILE"):
        log.warning("Both %s and DATA_KEY_FILE are set; %s is used.", KEY_VARIABLE, KEY_VARIABLE)
    handle, path = tempfile.mkstemp(prefix="mumu-data-key-", dir="/dev/shm" if os.path.isdir("/dev/shm") else None)
    with os.fdopen(handle, "w", encoding="ascii") as out:
        out.write(text + "\n")
    os.environ["DATA_KEY_FILE"] = _KEY_COPY = path
    log.info("The data key comes from %s; this run reads it from %s.", KEY_VARIABLE, path)


def derive_siblings(bots: list[Bot]) -> None:
    """SIBLING_BOTS -- how the public bots point at each other -- is nothing but their usernames, which are already set once each."""
    if os.environ.get("SIBLING_BOTS"):
        return
    entries = [f"{bot.name.lower()}:{bot.name}:{setting(bot, f'{bot.prefix}_USERNAME')}"
               for bot in bots
               if bot.folder != "manager_bot" and setting(bot, f"{bot.prefix}_USERNAME")]
    if entries:
        os.environ["SIBLING_BOTS"] = ",".join(entries)


def _worker_problem(jobs) -> str | None:
    """One real conversion through the worker process, the way jobs.py starts it."""
    import subprocess
    import tempfile
    from PIL import Image
    with tempfile.TemporaryDirectory() as work:
        source = Path(work) / "check.png"
        Image.new("RGB", (8, 8), (200, 40, 40)).save(source)
        job = {"in_paths": [str(source)], "src_ext": "png", "target_ext": "jpg", "work_dir": work,
               "stem": "check", "max_pixels": 1_000_000, "max_memory_mb": 0}
        done = subprocess.run([sys.executable, "-u", str(jobs.WORKER), *jobs.WORKER_ARGS, json.dumps(job)],
                              cwd=str(jobs.HERE), capture_output=True, timeout=120)
        outcome = jobs._parse(done.returncode, done.stdout, done.stderr)
        if not outcome.ok:
            return outcome.message or outcome.kind
        return None if outcome.bytes > 0 and Path(outcome.path).is_file() else "no file came out"


def _probe_problem(video) -> str | None:
    """DownloaderBot's video measuring on a real clip: a 64x36, two-frame video made
    with the ffmpeg on this machine must come back from `video.probe` as 64 wide and
    36 high. It proves the ffprobe arguments work on the ffmpeg the image installs."""
    import subprocess
    import tempfile
    with tempfile.TemporaryDirectory() as work:
        clip = Path(work) / "probe-check.mp4"
        made = subprocess.run(
            ["ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=size=64x36:rate=10",
             "-frames:v", "2", "-c:v", "mpeg4", "-pix_fmt", "yuv420p", str(clip)],
            capture_output=True, timeout=120)
        if made.returncode != 0 or not clip.is_file():
            return "ffmpeg could not make the test clip"
        shape = asyncio.run(video.probe(str(clip)))
        if shape is None:
            return "ffprobe gave no shape"
        return None if (shape.width, shape.height) == (64, 36) else f"it measured {shape.width}x{shape.height}"


def check(bots: list[Bot]) -> int:
    """Load every bot without starting any, and prove each got its own modules."""
    failures = []

    def expect(what, ok):
        if not ok:
            failures.append(what)
            print(f"  FAIL  {what}")

    started = time.monotonic()
    loaded = [bot for bot in bots if load(bot, wire=False)]
    seconds = time.monotonic() - started
    expect("every bot loads", len(loaded) == len(bots))
    for bot in loaded:
        package = bot.folder
        mine = {name: module for name, module in sys.modules.items() if name.startswith(package + ".")}
        db = mine.get(f"{package}.db")
        link = mine.get(f"{package}.family_link")
        expect(f"{bot.name}: db.DB_SCHEMA is {package}", getattr(db, "DB_SCHEMA", None) == package)
        expect(f"{bot.name}: family_link uses its own db", getattr(link, "db", None) is db)
        if hasattr(link, "_i18n"):
            expect(f"{bot.name}: family_link finds its own translations",
                   link._i18n() is mine.get(f"{package}.i18n"))
        if hasattr(link, "_monitoring"):
            expect(f"{bot.name}: family_link finds its own monitoring",
                   link._monitoring() in (mine.get(f"{package}.shared_features"),
                                          mine.get(f"{package}.monitoring")))
        for name in bot.shared:
            module = mine.get(f"{package}.{name}")
            if module is not None:
                expect(f"{bot.name}: {name} sees itself in its bot's folder",
                       Path(module.__file__).parent == bot.directory)

        view = getattr(db, "os", None)
        expect(f"{bot.name}: reads settings through its own view", isinstance(view, _BotOs))
        if isinstance(view, _BotOs):
            expect(f"{bot.name}: sees DB_SCHEMA={package} after loading too",
                   view.environ.get("DB_SCHEMA") == package)
            for key, value in os.environ.items():
                other = next((b for b in bots if key.startswith(b.prefix + "_")), None)
                name = key.split("_", 1)[1] if other else None
                if not value or name in NOT_OVERRIDES or name is None:
                    continue
                if other is bot:
                    expect(f"{bot.name}: {key} reaches it as {name}", view.environ.get(name) == value)
                elif _per_bot_only(name):
                    expect(f"{bot.name}: {key} does not leak into it", view.environ.get(name) != value)
        admins = os.environ.get("ADMIN_ID")
        own_admins = f"{bot.prefix}_ADMIN_ID"
        if (admins and hasattr(bot.module, "ADMIN_IDS")
                and not (os.environ.get(own_admins) or bot.dotenv.get(own_admins))):
            expect(f"{bot.name}: ADMIN_ID is its admin list",
                   bot.module.ADMIN_IDS == {int(x) for x in admins.split(",") if x.strip()})
        print(f"  ok    {bot.name}: {len(mine)} modules, schema {getattr(db, 'DB_SCHEMA', '?')}")
    links = [sys.modules.get(f"{bot.folder}.family_link") for bot in loaded]
    expect("no two bots share a family_link", len({id(link) for link in links}) == len(links))
    leaked = sorted(name for bot in loaded for name in bot.local
                    if getattr(sys.modules.get(name), "__file__", None)
                    and ROOT in Path(sys.modules[name].__file__).resolve().parents)
    expect(f"no bot module loaded under its bare name ({', '.join(leaked)})", not leaked)
    jobs = sys.modules.get("convert_bot.jobs")
    if jobs is not None:
        problem = _worker_problem(jobs)
        expect(f"ConvertBot's worker converts a picture ({problem})", problem is None)
        if problem is None:
            print("  ok    ConvertBot: the conversion worker ran, as", " ".join(jobs.WORKER_ARGS))
        # Text recognition reads a line of English and a line of Russian from a page
        # it draws, so an image without the language data, or with a PyMuPDF that has
        # no engine in it, fails the build here and not on somebody's scan. The
        # Dockerfile sets TESSDATA_PREFIX; a checkout without it says it skipped.
        if os.environ.get("TESSDATA_PREFIX"):
            try:
                importlib.import_module("convert_bot.ocr_convert").selftest()
                ocr_problem = None
            except SystemExit:
                ocr_problem = "its self-test failed: see the line above"
            except Exception as exc:
                ocr_problem = f"{type(exc).__name__}: {exc}"
            expect(f"ConvertBot reads text from a picture ({ocr_problem})", ocr_problem is None)
            if ocr_problem is None:
                print("  ok    ConvertBot: text recognition read its test page")
        else:
            print("  --    ConvertBot: text recognition not checked (TESSDATA_PREFIX is not set)")
    video = sys.modules.get("downloader_bot.video")
    if video is not None:
        # Videos are sent with their real width and height (video.probe); a build
        # whose ffprobe does not answer to those arguments fails here, not on a reel.
        # Skipped, with a note, where there is no ffmpeg (a bare checkout).
        import shutil
        if shutil.which("ffmpeg") and shutil.which("ffprobe"):
            try:
                shape_problem = _probe_problem(video)
            except Exception as exc:
                shape_problem = f"{type(exc).__name__}: {exc}"
            expect(f"DownloaderBot measures a video's shape ({shape_problem})", shape_problem is None)
            if shape_problem is None:
                print("  ok    DownloaderBot: ffprobe measured a 64x36 test clip")
            deno = shutil.which("deno")
            expect("DownloaderBot has deno, without which yt-dlp gets 403s from YouTube", deno is not None)
            if deno:
                print("  ok    DownloaderBot: deno is there to answer YouTube's challenges")
        else:
            print("  --    DownloaderBot: video shape not checked (ffmpeg is not on PATH)")
    give_back_memory()
    print(f"\n{len(loaded)} bot(s) loaded in {seconds:.1f}s; {footprint()}")
    if failures:
        print(f"{len(failures)} check(s) failed.")
        return 1
    print("All checks passed.")
    return 0


def offer_to_the_oom_killer() -> None:
    """A job's own process (`--module`: ConvertBot's worker and probes, a big-file
    transfer) is the one the kernel kills first when the service runs out of
    memory: one job lost, never the bots. Linux; ffmpeg and anything else the
    job starts inherit it. Raising one's own score needs no privilege."""
    try:
        with open("/proc/self/oom_score_adj", "w") as score:
            score.write("1000")
    except OSError:
        pass


def run_module(key: str, args: list[str]) -> int:
    """`main.py --module KEY ...`: one module as a program of its own, for the
    processes the bots start -- ConvertBot's worker, and the big-file transfer.
    A bot's module runs with that bot's modules and settings, as it would
    inside the bot; a shared one with nothing but itself."""
    if key not in index():
        raise SystemExit(f"No module {key!r}.")
    offer_to_the_oom_killer()
    sys.argv = [key, *args]
    path = index()[key]
    names = {"__name__": "__main__", "__spec__": None, "__file__": str(path)}
    if "." in key:
        folder, name = key.split(".", 1)
        bot = next((b for b in discover()[0] if b.folder == folder), None)
        if bot is None:
            raise SystemExit(f"No bot {folder!r}.")
        bot.read_own_env_file()
        sys.meta_path.insert(0, _BotFinder)
        register(bot)
        names.update(__builtins__=bot.import_table, __file__=str(bot.directory / f"{name}.py"))
    exec(compiled(key), names)
    return 0


# --- one process per bot -------------------------------------------------------

PROCESSES_SETTING = "MUMU_PROCESSES"
# Set in every child, so a child never becomes a supervisor itself.
CHILD_SETTING = "MUMU_CHILD"
# The bots this service (the container) runs, whichever process each is in: set
# here, inherited by the children, read by the `resources` module (how much of
# the machine a bot's work may count on) and by memory_reporter.
SERVICE_BOTS_SETTING = "MUMU_SERVICE_BOTS"
# How long a child that ended with an error waits before it is started again:
# 5 s, then twice as long each time, up to 5 minutes. A child that ran
# HEALTHY_SECONDS before it failed starts over from 5 s.
RESTART_DELAYS = (5, 10, 20, 40, 80, 160, 300)
HEALTHY_SECONDS = 600
# After a stop is passed on, how long the children get to finish their work
# (each drains for up to DRAIN_SECONDS itself) before they are killed.
STOP_GRACE_SECONDS = DRAIN_SECONDS + 15


def process_groups(bots: list[Bot], everything: list[Bot], flags: set[str]) -> "list[list[Bot]] | None":
    """None runs every bot in this process; otherwise, the bots of each process.

    MUMU_PROCESSES is "one" (the default), "separate" (a process per bot), or groups split
    by ";": "manager;convert" gives those two a process each and the rest one together, and
    "manager;convert;sticker,downloader" names every process."""
    if os.environ.get(CHILD_SETTING):
        return None
    mode = "separate" if "--separate" in flags else (os.environ.get(PROCESSES_SETTING) or "").strip().lower()
    if mode in ("", "one", "single"):
        return None
    if mode == "separate":
        return [[bot] for bot in bots]
    groups: list[list[Bot]] = []
    placed: set[str] = set()
    for part in mode.split(";"):
        names = [name.strip() for name in part.split(",") if name.strip()]
        if not names:
            continue
        try:
            named = select(everything, names)
        except SystemExit as exc:
            raise SystemExit(f"{PROCESSES_SETTING}: {exc}") from None
        group = [bot for bot in named if bot in bots]
        twice = [bot.name for bot in group if bot.folder in placed]
        if twice:
            raise SystemExit(f"{PROCESSES_SETTING} puts {', '.join(twice)} in two processes.")
        placed |= {bot.folder for bot in group}
        if group:
            groups.append(group)
    rest = [bot for bot in bots if bot.folder not in placed]
    if rest:
        groups.append(rest)
    return groups


def child_command(bots: list[Bot]) -> tuple[list[str], dict]:
    env = {key: value for key, value in os.environ.items() if key not in ("BOTS", PROCESSES_SETTING)}
    env[CHILD_SETTING] = "1"
    return [sys.executable, "-u", str(ROOT / "main.py"), *(bot.folder.removesuffix("_bot") for bot in bots)], env


def _spawn(bots: list[Bot]):
    command, env = child_command(bots)
    return subprocess.Popen(command, env=env, cwd=str(ROOT), start_new_session=sys.platform != "win32")


@dataclass(eq=False)
class Child:
    bots: list
    process: object | None = None
    started: float = 0.0
    failures: int = 0
    restart_at: float | None = None
    finished: bool = False

    @property
    def name(self) -> str:
        return ", ".join(bot.name for bot in self.bots)


class Supervisor:
    """Starts one process per group of bots (process_groups), keeps them running, and
    stops them together.

    A child that ends with an error is started again after a while; the others
    are never touched by it. ManagerBot's /restart <bot> is such an end: the bot
    exits with code 1, so here it is back 5 seconds later, alone. A child that
    ends with exit code 0 stopped on purpose and is left stopped. The supervisor
    ends when it is told to stop, or when every child has stopped on purpose. It
    never starts a second copy of a bot: a child is started again only after its
    last process has ended."""

    def __init__(self, groups: list[list[Bot]], spawn=_spawn, clock=time.monotonic, sleep=time.sleep,
                 windows: bool = sys.platform == "win32"):
        self.children = [Child(group) for group in groups]
        self.spawn, self.clock, self.sleep, self.windows = spawn, clock, sleep, windows
        self.stop_asked: float | None = None
        self.stop_now = False
        self.passed_on = False

    def on_signal(self, number, _frame=None) -> None:
        if self.stop_asked is not None:
            self.stop_now = True
            return
        self.stop_asked = self.clock()
        try:
            name = signal.Signals(number).name
        except ValueError:
            name = f"signal {number}"
        log.info("%s -- stopping %d bot process(es).", name, sum(1 for c in self.children if self.alive(c)))

    @staticmethod
    def alive(child: Child) -> bool:
        return child.process is not None and child.process.poll() is None

    def start(self, child: Child) -> None:
        child.restart_at = None
        try:
            child.process = self.spawn(child.bots)
        except Exception as exc:
            child.process = None
            log.error("%s: could not start its process (%s: %s).", child.name, type(exc).__name__, exc)
            self.schedule_restart(child, ran=0.0)
            return
        child.started = self.clock()
        log.info("%s: started in a process of %s own (pid %s).", child.name,
                 "its" if len(child.bots) == 1 else "their",
                 getattr(child.process, "pid", "?"))

    def schedule_restart(self, child: Child, ran: float) -> float:
        if ran >= HEALTHY_SECONDS:
            child.failures = 0
        delay = RESTART_DELAYS[min(child.failures, len(RESTART_DELAYS) - 1)]
        child.failures += 1
        child.restart_at = self.clock() + delay
        return delay

    def ended(self, child: Child, code: int) -> None:
        ran = self.clock() - child.started
        child.process = None
        if self.stop_asked is not None:
            child.finished = True
            log.info("%s stopped (exit code %s).", child.name, code)
        elif code == 0:
            child.finished = True
            log.info("%s stopped on purpose (exit code 0) after %.0fs; not starting it again.",
                     child.name, ran)
        else:
            delay = self.schedule_restart(child, ran)
            log.warning("%s ended with exit code %s after %.0fs; starting it again in %ss. "
                        "The other bots carry on.", child.name, code, ran, delay)

    def pass_on_stop(self) -> None:
        """SIGTERM to every running child, once (Linux). On Windows they had the
        console's Ctrl+C themselves."""
        self.passed_on = True
        if self.windows:
            return
        for child in self.children:
            if self.alive(child):
                with contextlib.suppress(Exception):
                    child.process.send_signal(signal.SIGTERM)

    def kill_all(self) -> None:
        for child in self.children:
            if self.alive(child):
                log.warning("%s did not stop in time; killing it.", child.name)
                with contextlib.suppress(Exception):
                    child.process.kill()

    def step(self) -> bool:
        """One look at the children. False once there is nothing left to do."""
        for child in self.children:
            if child.process is not None:
                code = child.process.poll()
                if code is not None:
                    self.ended(child, code)
        if self.stop_asked is not None:
            for child in self.children:
                child.restart_at = None
            if not self.passed_on:
                self.pass_on_stop()
            if not any(self.alive(child) for child in self.children):
                return False
            if self.stop_now or self.clock() - self.stop_asked > STOP_GRACE_SECONDS:
                self.kill_all()
                for child in self.children:
                    if child.process is not None:
                        with contextlib.suppress(Exception):
                            child.process.wait(timeout=10)
                return False
            return True
        now = self.clock()
        for child in self.children:
            if child.restart_at is not None and now >= child.restart_at:
                self.start(child)
        return not all(child.finished for child in self.children)

    def take_signals(self) -> None:
        for sig in (signal.SIGTERM, signal.SIGINT):
            with contextlib.suppress(ValueError, OSError):
                signal.signal(sig, self.on_signal)

    def run(self) -> int:
        self.take_signals()
        log.info("Bot family, %d processes: %s", len(self.children),
                 " | ".join(child.name for child in self.children))
        for child in self.children:
            self.start(child)
        while self.step():
            self.sleep(0.5)
        log.info("Stopped.")
        return 0


def with_tokens(bots: list[Bot]) -> list[Bot]:
    """The bots that can run: each needs a token, and no two may share one."""
    tokens: dict[str, Bot] = {}
    wanted = []
    for bot in bots:
        if not bot.token_set():
            bot.state, bot.problem = "skipped", f"no {bot.prefix}_TOKEN"
            log.info("%s skipped: %s is not set.", bot.name, f"{bot.prefix}_TOKEN")
            continue
        token = setting(bot, f"{bot.prefix}_TOKEN")
        if token in tokens:
            bot.state, bot.problem = "skipped", f"same token as {tokens[token].name}"
            log.error("%s skipped: it has the same token as %s, and two pollers on one "
                      "token split its updates between them.", bot.name, tokens[token].name)
            continue
        tokens[token] = bot
        wanted.append(bot)
    if not wanted:
        log.error("Nothing to run: no bot has a token. Set at least one of %s.",
                  ", ".join(f"{bot.prefix}_TOKEN" for bot in bots))
    return wanted


def main(argv: list[str]) -> int:
    global _LOADING_IN_FAMILY
    if argv[:1] == ["--module"] and len(argv) > 1:
        return run_module(argv[1], argv[2:])
    cap_malloc_arenas()
    setup_logging()
    _load_dotenv()
    _clear_per_bot_settings()
    _key_from_variable()

    flags = {arg for arg in argv if arg.startswith("-")}
    words = [arg for arg in argv if not arg.startswith("-")]
    if not words and os.environ.get("BOTS"):
        words = [word.strip() for word in os.environ["BOTS"].split(",") if word.strip()]

    everything, version = discover()
    if not everything:
        raise SystemExit(f"No bots found next to {ROOT}.")
    for bot in everything:
        bot.read_own_env_file()
    derive_siblings(everything)
    bots = select(everything, words)

    if "--list" in flags:
        for bot in everything:
            mark = "token set" if bot.token_set() else f"no {bot.prefix}_TOKEN"
            print(f"  {bot.folder.removesuffix('_bot'):<11} {bot.name:<14} {mark}")
        return 0

    groups = None if "--check" in flags else process_groups(bots, everything, flags)
    if groups is not None:
        log.info("Bot family %s, %d processes.", version, len(groups))
        wanted = with_tokens(bots)
        if not wanted:
            return 1
        groups = [kept for kept in ([bot for bot in group if bot in wanted] for group in groups) if kept]
        os.environ[SERVICE_BOTS_SETTING] = ",".join(bot.folder for bot in wanted)
        give_back_memory()
        return Supervisor(groups).run()

    _LOADING_IN_FAMILY = True
    sys.meta_path.insert(0, _BotFinder)
    for bot in bots:
        register(bot)
    if "--check" in flags:
        return check(bots)

    log.info("Bot family %s, %s: %s", version,
             ("a process of its own" if len(bots) == 1 else "a process of their own")
             if os.environ.get(CHILD_SETTING) else "one process",
             ", ".join(bot.name for bot in bots))
    wanted = with_tokens(bots)
    if not wanted:
        return 1
    if not os.environ.get(CHILD_SETTING):
        os.environ[SERVICE_BOTS_SETTING] = ",".join(bot.folder for bot in wanted)

    problem = preflight(wanted)
    if problem:
        log.error("Not starting: %s", problem)
        return 1

    patch_python_telegram_bot()
    runnable = [bot for bot in wanted if load(bot)]
    # A bot that failed is tried again later, alone. One that ended itself with a
    # plain SystemExit (a setting it needs is missing) is not: a restart cannot
    # cure that.
    retrying = [bot for bot in wanted if bot.state == "failed" or (bot.state == "skipped" and bot.data_key)]
    if not runnable:
        reasons = sorted({bot.problem for bot in wanted if bot.problem})
        log.error("No bot could be loaded: %s", " / ".join(reasons) or "see the errors above")
        return 1
    one_memory_reporter(runnable + retrying)

    family = Family(runnable, retrying)
    _FAMILY.append(family)
    return asyncio.run(family.run())


if __name__ == "__main__":
    if sys.argv[1:2] == ["--module"]:
        # A job's own process: an ordinary exit, which flushes whatever it wrote.
        sys.exit(main(sys.argv[1:]))
    end_now(main(sys.argv[1:]))
