"""The combined runner (the C edition's main.py) and the data key guard.

Every bot's main() runs family_link.attach(), which ends the bot with a
DataKeyRefused (a SystemExit whose `data_key` is True) when the data key is
wrong, the data is not sealed, or the check could not be made. Alone, that ends
the process. In the combined edition main.py loads the five bots in one process
and catches SystemExit per bot so that one bot's missing token does not stop
the rest -- which, for the key guard, would leave a process running some bots
beside one that was refused. So main.py must stop, non-zero, so that Docker's
restart policy tries again:

    * when any bot's load raised something whose `data_key` is true;
    * when ManagerBot, which watches and reports on the others, failed to load
      for any reason.

Anything else is unchanged: a bot with no token, a bot that exits for another
reason, a bot that fails with a plain error -- the others run.

The bots here are stubs (no Telegram, no database, no network); main.py itself
is the real file. It is not in this repository: it is found at
../MumuBots2.2.0C/main.py, or wherever MUMU_COMBINED_MAIN says. Without it
this test says so and passes, so a checkout of the split source alone is not
broken by it.

    python tests/test_combined_guard.py
"""
from __future__ import annotations

import ast
import asyncio
import importlib.machinery
import importlib.util
import logging
import os
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

failures: list = []
_checks = 0


def check(label: str, condition: bool, detail: str = "") -> bool:
    global _checks
    _checks += 1
    if condition:
        print(f"  ok   {label}")
    else:
        failures.append(label)
        print(f"  FAIL {label}" + (f"\n       {detail}" if detail else ""))
    return bool(condition)


def section(title: str) -> None:
    print(f"\n== {title} ==")


# ---------------------------------------------------------------------------
# the guard's exception, in the shared source
# ---------------------------------------------------------------------------

def source_checks() -> None:
    section("shared/family_link.py: the exception the guard raises")
    tree = ast.parse((ROOT / "shared" / "family_link.py").read_text(encoding="utf-8"))
    classes = {node.name: node for node in tree.body if isinstance(node, ast.ClassDef)}
    node = classes.get("DataKeyRefused")
    check("DataKeyRefused exists and is a SystemExit", node is not None
          and [getattr(base, "id", None) for base in node.bases] == ["SystemExit"])
    marked = node is not None and any(
        isinstance(stmt, ast.Assign) and [getattr(t, "id", None) for t in stmt.targets] == ["data_key"]
        and isinstance(stmt.value, ast.Constant) and stmt.value.value is True for stmt in node.body)
    check("...with the class attribute data_key = True", marked)
    refuse = next((n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_refuse"), None)
    raised = [getattr(getattr(n.exc, "func", None), "id", None) for n in ast.walk(refuse)
              if isinstance(n, ast.Raise)] if refuse is not None else []
    check("...and _refuse(), which every refusal goes through, raises it (and nothing else)",
          raised == ["DataKeyRefused"], str(raised))


# ---------------------------------------------------------------------------
# main.py with stub bots
# ---------------------------------------------------------------------------

def find_main() -> "Path | None":
    given = os.environ.get("MUMU_COMBINED_MAIN")
    path = Path(given) if given else ROOT.parent / "MumuBots2.2.0C" / "main.py"
    return path if path.is_file() else None


def load_module(path: Path):
    name = "mumu_combined_main_under_test"
    spec = importlib.util.spec_from_loader(name, importlib.machinery.SourceFileLoader(name, str(path)))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module            # @dataclass looks its module up here
    spec.loader.exec_module(module)
    return module


class DataKeyRefused(SystemExit):
    """The shape family_link.DataKeyRefused has."""
    data_key = True


class NotAKeyRefusal(SystemExit):
    data_key = False


class KeyTrouble(Exception):
    """Not a SystemExit, but marked: any exception with a true data_key counts."""
    data_key = True


FOLDERS = {"sticker_bot": "StickerBot", "convert_bot": "ConvertBot", "downloader_bot": "DownloaderBot",
           "anon_bot": "AnonBot", "manager_bot": "ManagerBot"}
PREFIXES = {"sticker_bot": "SBOT", "convert_bot": "CBOT", "downloader_bot": "DBOT",
            "anon_bot": "ABOT", "manager_bot": "MBOT"}


class Collect(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.records: list = []

    def emit(self, record):
        self.records.append(record)


def run_family(M, behaviour: dict, without_token: tuple = ()) -> "tuple[int, list, list]":
    """M.main([]) with five stub bots; `behaviour` maps a folder to what that bot's main() does
    (a callable; absent = starts fine). Returns (exit code, the bots the family ran, log records)."""
    ran: list = []
    scratch = Path(tempfile.mkdtemp(prefix="combined_guard_"))

    def start_fine():
        bot = M._CURRENT.get()
        bot.app, bot.polling = object(), {}

    bots = [M.Bot(folder=folder, name=name, prefix=PREFIXES[folder], directory=scratch / folder)
            for folder, name in FOLDERS.items()]

    class StubFamily:
        def __init__(self, loaded):
            self.loaded = loaded

        async def run(self):
            ran.append([bot.name for bot in self.loaded])
            return 0

    saved_attrs = {name: getattr(M, name) for name in (
        "discover", "setup_logging", "_load_dotenv", "_clear_per_bot_settings", "cap_malloc_arenas",
        "derive_siblings", "preflight", "patch_python_telegram_bot", "one_memory_reporter", "Family")}
    saved_env = dict(os.environ)
    saved_meta = list(sys.meta_path)
    saved_modules = set(sys.modules)
    collector = Collect()
    M.log.addHandler(collector)
    previous_level, previous_propagate = M.log.level, M.log.propagate
    M.log.setLevel(logging.DEBUG)
    M.log.propagate = False
    try:
        M.discover = lambda: (bots, None, "test")
        M.setup_logging = M._load_dotenv = M._clear_per_bot_settings = M.cap_malloc_arenas = lambda *a: None
        M.derive_siblings = lambda everything: None
        M.preflight = lambda wanted: None
        M.patch_python_telegram_bot = lambda holder: None
        M.one_memory_reporter = lambda loaded: None
        M.Family = StubFamily
        for key in [k for k in os.environ if k.endswith("_TOKEN") or k == "BOTS"]:
            del os.environ[key]
        for folder, prefix in PREFIXES.items():
            if folder not in without_token:
                os.environ[f"{prefix}_TOKEN"] = f"{len(prefix)}{prefix.lower()}:token-for-{folder}"
        for folder in FOLDERS:
            stub = types.ModuleType(f"{folder}.bot")
            stub.main = behaviour.get(folder, start_fine)
            sys.modules[f"{folder}.bot"] = stub
        code = M.main([])
    finally:
        for name, value in saved_attrs.items():
            setattr(M, name, value)
        M.log.removeHandler(collector)
        M.log.setLevel(previous_level)
        M.log.propagate = previous_propagate
        os.environ.clear()
        os.environ.update(saved_env)
        sys.meta_path[:] = saved_meta
        for name in set(sys.modules) - saved_modules:
            if name.split(".")[0] in FOLDERS:
                del sys.modules[name]
        M._PACKAGES.clear()
    return code, ran, collector.records


def raising(error):
    def main():
        raise error
    return main


def critical_text(records) -> str:
    return " | ".join(r.getMessage() for r in records if r.levelno >= logging.CRITICAL)


def main_checks(path: Path) -> None:
    M = load_module(path)
    section("main.py with stub bots (" + path.name + ")")

    code, ran, records = run_family(M, {})
    check("baseline: five bots that start fine are run, and main() returns 0",
          code == 0 and len(ran) == 1 and len(ran[0]) == 5, f"{code} {ran}")

    for folder in ("anon_bot", "sticker_bot", "convert_bot", "downloader_bot"):
        code, ran, records = run_family(M, {folder: raising(DataKeyRefused("The data key is not usable: test"))})
        text = critical_text(records)
        check(f"{FOLDERS[folder]} refused by the key guard (a SystemExit with data_key True): "
              "main() returns non-zero and runs no bot",
              code not in (0, None) and ran == [], f"{code} {ran}")
        check(f"...with a CRITICAL naming {FOLDERS[folder]} and the reason",
              FOLDERS[folder] in text and "The data key is not usable: test" in text, text)

    code, ran, records = run_family(M, {"convert_bot": raising(KeyTrouble("the key could not be checked"))})
    check("any exception with a true data_key attribute counts, SystemExit or not",
          code not in (0, None) and ran == [] and "ConvertBot" in critical_text(records), f"{code} {ran}")

    code, ran, records = run_family(M, {"sticker_bot": raising(DataKeyRefused("one")),
                                        "downloader_bot": raising(DataKeyRefused("two"))})
    text = critical_text(records)
    check("two bots that would be refused: the first one stops the loading, and the CRITICAL names it",
          code not in (0, None) and ran == [] and "StickerBot" in text, text)

    code, ran, records = run_family(M, {"manager_bot": raising(RuntimeError("boom"))})
    text = critical_text(records)
    check("ManagerBot failing to load with a plain error: main() returns non-zero, runs no bot, "
          "and a CRITICAL names it and the reason",
          code not in (0, None) and ran == [] and "ManagerBot" in text and "boom" in text, f"{code} {ran} {text}")
    code, ran, records = run_family(M, {"manager_bot": raising(SystemExit("Set MBOT_USERNAME first"))})
    check("ManagerBot ending itself with a plain SystemExit (a missing setting, which a restart cannot cure): "
          "the other four run, and main() does not exit",
          code == 0 and len(ran) == 1 and sorted(ran[0]) == sorted(
              ["StickerBot", "ConvertBot", "DownloaderBot", "AnonBot"]) and not critical_text(records), f"{code} {ran}")

    loaded_after: list = []

    def must_not_load():
        loaded_after.append(1)
        raise RuntimeError("loaded after a key refusal")
    code, ran, records = run_family(M, {"sticker_bot": raising(DataKeyRefused("refused first")),
                                        "convert_bot": must_not_load, "manager_bot": must_not_load})
    check("after the first key refusal no further bot is loaded (each would wait out its own key check), "
          "and main() still returns non-zero naming the refused bot",
          code not in (0, None) and ran == [] and loaded_after == [] and "StickerBot" in critical_text(records),
          f"{code} {ran} {loaded_after}")

    # everything else is unchanged
    code, ran, records = run_family(M, {"convert_bot": raising(RuntimeError("boom"))})
    check("another bot failing with a plain error: unchanged, the other four run, and nothing is CRITICAL",
          code == 0 and len(ran) == 1 and sorted(ran[0]) == sorted(
              ["StickerBot", "DownloaderBot", "AnonBot", "ManagerBot"]) and not critical_text(records), f"{code} {ran}")
    code, ran, records = run_family(M, {"sticker_bot": raising(SystemExit("not configured"))})
    check("another bot ending with a plain SystemExit: unchanged, the other four run",
          code == 0 and len(ran) == 1 and len(ran[0]) == 4 and not critical_text(records), f"{code} {ran}")
    code, ran, records = run_family(M, {"anon_bot": raising(NotAKeyRefusal("a SystemExit whose data_key is False"))})
    check("a SystemExit whose data_key is False is not a key refusal",
          code == 0 and len(ran) == 1 and "AnonBot" not in ran[0], f"{code} {ran}")
    code, ran, records = run_family(M, {}, without_token=("manager_bot",))
    check("ManagerBot with no token is a bot that was never asked to start, not one that failed to load: "
          "the other four run",
          code == 0 and len(ran) == 1 and len(ran[0]) == 4 and "ManagerBot" not in ran[0], f"{code} {ran}")
    code, ran, records = run_family(M, {"sticker_bot": raising(RuntimeError("x")), "anon_bot": raising(SystemExit("y")),
                                        "convert_bot": raising(RuntimeError("z")),
                                        "downloader_bot": raising(RuntimeError("w")),
                                        "manager_bot": raising(RuntimeError("v"))})
    check("when nothing can load, main() still returns non-zero (as before)", code not in (0, None) and ran == [])


def main() -> int:
    source_checks()
    path = find_main()
    if path is None:
        print("\nSKIPPED the main.py checks: no combined main.py at ../MumuBots2.2.0C/main.py "
              "(set MUMU_COMBINED_MAIN to its path).")
    elif sys.version_info < (3, 10):
        print("\nSKIPPED the main.py checks: the combined runner needs Python 3.10 or later.")
    else:
        main_checks(path)
    print(f"\n{_checks} checks, FAILURES: {len(failures)}")
    for name in failures:
        print("  -", name)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
