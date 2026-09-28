"""ManagerBot's log commands read the right log, from a file or from memory.

    python3 tests/test_logs.py

/logs, /errorlog, /botlog and /problemlog all end in family_link._cmd_logs
inside the bot they name. A host that writes log files is read from disk; one
that does not -- Railway, unless LOG_TO_FILES=1 -- is read from the lines the
process has kept in memory since it started, so the commands answer either way.
"""
import asyncio
import logging
import pathlib
import shutil
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / ".mirror" / "convert_bot"))
sys.path.insert(0, str(HERE / "stubs"))

import family_link as fl  # noqa: E402

failures = []


def check(name, ok, detail=""):
    print(("  ok  " if ok else "  FAIL  ") + name + ("" if ok else f"  -- {detail}"))
    if not ok:
        failures.append(name)


def logs(*args):
    text, _, _ = asyncio.run(fl._cmd_logs(None, list(args)))
    return text


log_dir = pathlib.Path(fl.__file__).resolve().parent / "logs"
aside = log_dir.with_name("logs-aside-for-test")
if log_dir.exists():
    log_dir.rename(aside)

try:
    logging.getLogger().setLevel(logging.INFO)
    print("== from memory, where the host writes no log files ==")
    fl.keep_recent_log_lines()
    fl.keep_recent_log_lines()
    root = logging.getLogger()
    check("keeping recent lines twice installs them once",
          sum(1 for h in root.handlers if h in fl._RECENT_LOGS.values()) == 3)
    logging.getLogger("problems").info("FM-UNKNOWN-COMMAND incident K7Q2MX, no Report button")
    logging.getLogger("somewhere").warning("a warning")
    logging.getLogger("somewhere").info("an ordinary line")

    text = logs("problems")
    check("problems reads the problem lines and nothing else",
          "K7Q2MX" in text and "a warning" not in text and "ordinary" not in text, text)
    check("and says it came from memory", "memory" in text, text)
    text = logs()
    check("the default is errors: warnings, not ordinary lines", "a warning" in text and "ordinary" not in text, text)
    text = logs("bot")
    check("'bot' is everything", all(part in text for part in ("ordinary", "a warning", "K7Q2MX")), text)
    for number in range(60):
        logging.getLogger("somewhere").warning("numbered %d", number)
    text = logs("5")
    check("a count takes that many of the latest lines",
          "numbered 59" in text and "numbered 55" in text and "numbered 54" not in text, text)
    try:
        raise ValueError("boom")
    except ValueError:
        logging.getLogger("somewhere").exception("with a traceback")
    text = logs("3")
    check("a traceback is kept line by line, so a count counts lines", "ValueError: boom" in text, text)

    print("\n== from a file, where there is one ==")
    log_dir.mkdir()
    (log_dir / "problems.log").write_text(
        "2026-09-11 18:00:00,000 problems INFO CV-TIMEOUT incident ABCDEF, with a Report button\n",
        encoding="utf-8")
    text = logs("problems")
    check("a problems.log on disk is read instead of memory", "ABCDEF" in text and "memory" not in text, text)
    check("while a log with no file still falls back to memory", "memory" in logs(), logs())
    (log_dir / "problems.log").write_text("", encoding="utf-8")
    check("an empty file says so", "nothing yet" in logs("problems"), logs("problems"))
finally:
    if log_dir.exists():
        shutil.rmtree(log_dir)
    if aside.exists():
        aside.rename(log_dir)

print()
if failures:
    print(f"FAILURES ({len(failures)}):")
    for name in failures:
        print("  -", name)
    sys.exit(1)
print("the log commands read the right log")
