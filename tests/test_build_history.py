"""MumuBotsHistory carries every old version and none of its secrets.

scripts/build_history.py copies the version folders before 2.0.0 into what
becomes the MumuBotsHistory repository, so what it leaves out and what it
redacts is all that stands between a GitHub push and the bot tokens 0.1.1 and
0.3.0 hard-code. Built here from a made-up laptop in a temporary folder:
settings files, database dumps, SQLite files and their sidecars, logs, Telethon
sessions, nested git and a coding assistant's worktree are left out; tokens,
database passwords and API hashes are redacted where the code keeps them;
2.0.0 and its C edition stay out; and a secret no pattern knows the shape of
is still found by its value, which stops the build. Needs git for the last
part; part of run.py.
"""
import contextlib
import importlib.util
import io
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

failures = []


def check(label, ok, detail=""):
    print(("  ok   " if ok else "  FAIL ") + label + ("" if ok else "  [" + str(detail) + "]"))
    if not ok:
        failures.append(label)


def load():
    spec = importlib.util.spec_from_file_location("build_history", REPO / "scripts" / "build_history.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write(path: Path, text: str | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(text, bytes):
        path.write_bytes(text)
    else:
        path.write_text(text, encoding="utf-8")


def run(history, *argv):
    saved = sys.argv
    sys.argv = ["build_history.py", *argv]
    buffer = io.StringIO()
    try:
        with contextlib.redirect_stdout(buffer):
            code = history.main()
    except SystemExit as exc:
        code = exc.code
    except Exception as exc:   # reported as a failure, not a crash that hides the rest
        code = f"{type(exc).__name__}: {exc}"
    finally:
        sys.argv = saved
    return code, buffer.getvalue()


# Made up, but shaped like the real thing: a bot token is digits, a colon, 35.
TOKEN_OLD = "1111111111:" + "A" * 17 + "b" * 18
TOKEN_ENV = "2222222222:" + "C" * 17 + "d" * 18
TOKEN_CLAUDE = "3333333333:" + "E" * 17 + "f" * 18
TOKEN_NEW = "4444444444:" + "G" * 17 + "h" * 18
PASSWORD = "n0tAPlaceh0lder"
API_HASH = "0123456789abcdef0123456789abcdef"
EXAMPLE = "8123456789:AAH0pQ7-abcdefGHIJklmnOPqrstUVwxyz0"


def laptop(parent: Path) -> None:
    v010 = parent / "BotFamily0.1.0"
    write(v010 / "bot.py", f'BOT_TOKEN = "{TOKEN_OLD}"\nprint("hello")\n')
    write(v010 / "fstik.db", b"SQLite format 3\x00 real people")
    write(v010 / "fstik.db-wal", b"pages")
    write(v010 / "fstik.db-shm", b"index")
    write(v010 / "README.md", f"Paste a token like `{EXAMPLE}`.\n")

    v100 = parent / "BotFamily1.0.0"
    write(v100 / "convert_bot" / ".env",
          f"CBOT_TOKEN={TOKEN_ENV}\nCBOT_API_HASH={API_HASH}\n"
          f"DATABASE_URL=postgresql://postgres.ref:{PASSWORD}@pooler.example:5432/postgres\n")
    write(v100 / "convert_bot" / ".env.example", "CBOT_TOKEN=\nDATABASE_URL=postgresql://postgres:postgres@localhost/x\n")
    write(v100 / "convert_bot" / "bot.py", "import os\n")
    write(v100 / "convert_bot" / "notes.md",
          f"We connected with postgresql://postgres.ref:{PASSWORD}@pooler.example:5432/postgres\n"
          f"and CBOT_API_HASH={API_HASH}.\n")
    write(v100 / "convert_bot" / "convertbot.session", b"logged in")
    write(v100 / "convert_bot" / "logs" / "bot.log", "user 42 sent a file\n")
    write(v100 / "backups" / "README.md", "Dumps go here.\n")
    write(v100 / "backups" / "2026-09-01.sql", "INSERT INTO users VALUES (42);\n")
    write(v100 / "testbot" / "token.env", f"TESTBOT_TOKEN={TOKEN_ENV}\n")
    write(v100 / ".git" / "config", "[core]\n")
    write(v100 / ".claude" / "worktrees" / "x" / "bot.py", f'TOKEN = "{TOKEN_CLAUDE}"\n')
    write(v100 / "CHANGES_v1.0.0.md", "# v1.0.0 — the first real deploy\n")

    write(parent / "BotFamily1.2.2R" / "CHANGES_v1.2.2R.md", "# v1.2.2R — a retouch\n")
    # A version whose every file is left out still has a folder and a commit.
    write(parent / "BotFamily1.1.0" / ".env", f"CBOT_TOKEN={TOKEN_ENV}\n")
    write(parent / "BotFamily1.2.3" / "bot.py", "x = 1\n")

    # The versions MumuBots starts from, and the C edition: not history, but
    # what their settings hold is still checked for.
    write(parent / "BotFamily2.0.0" / "manager_bot" / ".env", f"MBOT_TOKEN={TOKEN_NEW}\n")
    write(parent / "BotFamily2.0.0" / "manager_bot" / "bot.py", "y = 2\n")
    write(parent / "BotFamily2.0.0C" / ".env", f"SBOT_TOKEN={TOKEN_NEW}\n")
    write(parent / "Design" / "Logo.png", b"\x89PNG")
    write(parent / "scripts" / "run_latest_release.py", "pass\n")


def main() -> int:
    history = load()

    # -- what is left out -----------------------------------------------------
    for relative, is_dir, out in [
        (".env", False, True), ("convert_bot/.env", False, True), ("testbot/token.env", False, True),
        ("fstik.db", False, True), ("fstik.db-wal", False, True), ("fstik.db-shm", False, True),
        ("x.sqlite3", False, True), ("convertbot.session", False, True), ("scripts/release.log", False, True),
        ("backups/2026-09-01.sql", False, True), ("backups/README.md", False, False),
        (".git", True, True), (".claude", True, True), ("logs", True, True), ("tests/.mirror", True, True),
        ("__pycache__", True, True),
        ("bot.py", False, False), (".env.example", False, False), ("testbot/token.env.example", False, False),
        ("README.md", False, False), ("logo.png", False, False),
    ]:
        check(f"{relative}{'/' if is_dir else ''} is {'left out' if out else 'kept'}",
              history.skipped(Path(relative), is_dir) is out)

    # -- what is redacted -----------------------------------------------------
    text, n = history.scrub(f'T = "{TOKEN_OLD}"\nU = "{EXAMPLE}"\n'
                            f"postgresql://postgres.ref:{PASSWORD}@host/db\n"
                            f"postgresql://postgres:postgres@localhost/db\n"
                            f"postgresql://postgres.<ref>:<password>@aws/db\n"
                            f"API_HASH = '{API_HASH}'\n")
    check("a token is redacted", TOKEN_OLD not in text and "<REDACTED-BOT-TOKEN>" in text, text)
    check("the documentation's example token is not", EXAMPLE in text, text)
    check("a database password is redacted", PASSWORD not in text, text)
    check("a placeholder password is not", "postgres:postgres@localhost" in text
          and "<ref>:<password>@aws" in text, text)
    check("an API hash is redacted", API_HASH not in text, text)
    check("... and each is counted", n == 3, n)

    # -- a whole laptop ---------------------------------------------------------
    with tempfile.TemporaryDirectory() as tmp:
        parent = Path(tmp) / "laptop"
        laptop(parent)
        history.PARENT = parent
        history.ROOT = parent / "BotFamily2.0.0"
        out = Path(tmp) / "MumuBotsHistory"

        code, said = run(history, "--out", str(out))
        check("it builds", code == 0, said[-400:])
        folders = sorted(p.name for p in out.iterdir() if p.is_dir())
        check("every version before 2.0.0, and the Design folder, and nothing else",
              folders == ["BotFamily0.1.0", "BotFamily1.0.0", "BotFamily1.1.0", "BotFamily1.2.2R",
                          "BotFamily1.2.3", "Design"],
              folders)
        files = {str(p.relative_to(out)).replace("\\", "/") for p in out.rglob("*") if p.is_file()}
        for gone in ("BotFamily0.1.0/fstik.db", "BotFamily0.1.0/fstik.db-wal", "BotFamily0.1.0/fstik.db-shm",
                     "BotFamily1.0.0/convert_bot/.env", "BotFamily1.0.0/convert_bot/convertbot.session",
                     "BotFamily1.0.0/convert_bot/logs/bot.log", "BotFamily1.0.0/backups/2026-09-01.sql",
                     "BotFamily1.0.0/testbot/token.env", "BotFamily1.0.0/.git/config",
                     "BotFamily1.0.0/.claude/worktrees/x/bot.py"):
            check(f"left out: {gone}", gone not in files)
        check("a version with nothing left says so rather than vanishing",
              files >= {"BotFamily1.1.0/EMPTY.md"} and not any(f.startswith("BotFamily1.1.0/.env") for f in files),
              sorted(f for f in files if f.startswith("BotFamily1.1.0")))
        for kept in ("BotFamily1.0.0/backups/README.md", "BotFamily1.0.0/convert_bot/.env.example",
                     "BotFamily0.1.0/bot.py", "README.md", "Design/Logo.png"):
            check(f"kept: {kept}", kept in files)
        everything = b"".join(p.read_bytes() for p in out.rglob("*") if p.is_file())
        for label, secret in (("the hard-coded token", TOKEN_OLD), ("a .env's token", TOKEN_ENV),
                              ("the worktree's token", TOKEN_CLAUDE), ("2.0.0's token", TOKEN_NEW),
                              ("the database password", PASSWORD), ("the API hash", API_HASH)):
            check(f"nowhere in it: {label}", secret.encode() not in everything)
        old_bot = (out / "BotFamily0.1.0" / "bot.py").read_text(encoding="utf-8")
        check("the old bot.py still reads as the program it was",
              'BOT_TOKEN = "<REDACTED-BOT-TOKEN>"' in old_bot and 'print("hello")' in old_bot, old_bot)
        readme = (out / "README.md").read_text(encoding="utf-8")
        check("the README lists the versions in order, with what each was",
              readme.index("BotFamily1.2.2R") < readme.index("BotFamily1.2.3")
              and "a retouch" in readme and "the first real deploy" in readme, readme[-500:])

        code, said = run(history, "--out", str(out))
        check("an existing build is not overwritten without --replace", code == 1, said)
        write(Path(tmp) / "unrelated" / "keep.txt", "mine")
        code, said = run(history, "--out", str(Path(tmp) / "unrelated"), "--replace")
        check("--replace never deletes a folder this script did not make",
              code == 1 and (Path(tmp) / "unrelated" / "keep.txt").exists(), said)

        # A secret no pattern knows: written in words, not as an address.
        write(parent / "BotFamily1.2.3" / "diary.md", f"The password is {PASSWORD}, do not tell.\n")
        code, said = run(history, "--out", str(out), "--replace")
        check("a secret no pattern catches is caught by its value, and stops the build",
              code == 2 and "NOT SAFE" in said and "diary.md" in said, said[-400:])
        check("... and the report names where it came from, never the secret",
              "DATABASE_URL" in said and PASSWORD not in said, said[-400:])
        (parent / "BotFamily1.2.3" / "diary.md").unlink()

        if subprocess.run(["git", "--version"], capture_output=True).returncode == 0:
            code, said = run(history, "--out", str(out), "--replace", "--git")
            check("--git builds", code == 0, said[-300:])
            log = subprocess.run(["git", "log", "--format=%s", "--reverse"], cwd=out, capture_output=True,
                                 text=True, encoding="utf-8").stdout.split("\n")
            titles = [line for line in log if line]
            check("one commit per version, oldest first, after the README",
                  titles == ["MumuBotsHistory: what is here, and what is not", "BotFamily0.1.0",
                             "BotFamily1.0.0", "BotFamily1.1.0", "BotFamily1.2.2R", "BotFamily1.2.3"], titles)
            remotes = subprocess.run(["git", "remote"], cwd=out, capture_output=True, text=True).stdout.strip()
            check("... and nowhere to push to", remotes == "", remotes)

    print("")
    print("FAILURES: " + str(len(failures)))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
