"""Where a bot's settings come from: shared/envfiles.py, against made-up files.

The owner, 2026-09-28: "two types of .env files: first type is local, one
per bot, second type is global, lives in shared folder ... Locals can
override shared." And 2026-09-29 they wrote shared/.env with Railway's
names -- one ADMIN_ID for the five -- which the five had not read.

Checked here, each in a fresh environment:
  - the real environment beats the bot's own .env, which beats shared/.env;
  - `KEY=` in a bot's own file does not hide the shared value;
  - a setting one bot alone can mean is ignored in shared/.env;
  - ADMIN_ID stands in for <PREFIX>_ADMIN_ID, from either file or the
    environment, and the bot's own always wins;
  - ENV_FILES=off reads neither file and applies no ADMIN_ID (TestBot);
  - merged(), which the release script reads, agrees with load().

    python3 tests/test_envfiles.py
"""
import os
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "shared"))
import envfiles  # noqa: E402

failures = []


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + (f"  -- {detail}" if detail and not ok else ""))
    if not ok:
        failures.append(name)


KEYS = ("SBOT_TOKEN", "SBOT_ADMIN_ID", "MBOT_ADMIN_ID", "ADMIN_ID", "DATABASE_URL", "DB_SCHEMA",
        "FAMILY_X", "ENV_FILES")


def scenario(shared="", local="", env=None, bot="sticker_bot"):
    """load() for one bot, with these files and this environment; returns the
    environment it leaves, and what merged() says."""
    saved = {k: os.environ.pop(k) for k in KEYS if k in os.environ}
    with tempfile.TemporaryDirectory() as tmp:
        tmp = pathlib.Path(tmp)
        (tmp / "shared").mkdir()
        (tmp / bot).mkdir()
        (tmp / "shared" / ".env").write_text(shared, encoding="utf-8")
        (tmp / bot / ".env").write_text(local, encoding="utf-8")
        envfiles.SHARED_DIR = tmp / "shared"
        os.environ.update(env or {})
        try:
            envfiles.load(tmp / bot / "bot.py")
            loaded = {k: os.environ.get(k) for k in KEYS}
            merged = envfiles.merged(tmp / bot)
        finally:
            for k in KEYS:
                os.environ.pop(k, None)
            os.environ.update(saved)
    return loaded, merged


got, merged = scenario(shared="SBOT_TOKEN=shared\nDATABASE_URL=shared-db\n",
                       local="SBOT_TOKEN=local\n", env={"DATABASE_URL": "env-db"})
check("the bot's own .env beats shared/.env", got["SBOT_TOKEN"] == "local", got["SBOT_TOKEN"])
check("the environment beats both files", got["DATABASE_URL"] == "env-db", got["DATABASE_URL"])
check("merged(): the bot's own over shared", merged.get("SBOT_TOKEN") == "local", merged)

got, _ = scenario(shared="FAMILY_X=shared\n", local="FAMILY_X=\n")
check("an empty line in the bot's own file does not hide the shared value",
      got["FAMILY_X"] == "shared", got["FAMILY_X"])

got, merged = scenario(shared="DB_SCHEMA=convert_bot\n")
check("a one-bot setting is ignored in shared/.env", got["DB_SCHEMA"] is None, got["DB_SCHEMA"])
check("merged() ignores it too", "DB_SCHEMA" not in merged, merged)

got, merged = scenario(shared="ADMIN_ID=42\n")
check("ADMIN_ID in shared/.env becomes the bot's admin", got["SBOT_ADMIN_ID"] == "42", got["SBOT_ADMIN_ID"])
check("merged() says the same", merged.get("SBOT_ADMIN_ID") == "42", merged)
check("and only for the bot being loaded", got["MBOT_ADMIN_ID"] is None, got["MBOT_ADMIN_ID"])

got, merged = scenario(shared="ADMIN_ID=42\n", bot="manager_bot")
check("ManagerBot too, whose admin list is what it runs on", got["MBOT_ADMIN_ID"] == "42", got)
check("merged() for ManagerBot, which the release script reads", merged.get("MBOT_ADMIN_ID") == "42", merged)

got, merged = scenario(shared="ADMIN_ID=42\n", local="SBOT_ADMIN_ID=7\n")
check("the bot's own admin id wins over ADMIN_ID", got["SBOT_ADMIN_ID"] == "7", got["SBOT_ADMIN_ID"])
check("merged(): the bot's own wins", merged.get("SBOT_ADMIN_ID") == "7", merged)

got, _ = scenario(shared="ADMIN_ID=42\n", env={"SBOT_ADMIN_ID": "9"})
check("an admin id in the environment wins over ADMIN_ID", got["SBOT_ADMIN_ID"] == "9", got["SBOT_ADMIN_ID"])

got, _ = scenario(env={"ADMIN_ID": "5"})
check("ADMIN_ID in the environment alone (a Railway variable)", got["SBOT_ADMIN_ID"] == "5", got["SBOT_ADMIN_ID"])

got, _ = scenario(shared="ADMIN_ID=42\nSBOT_TOKEN=real\n", env={"ENV_FILES": "off", "ADMIN_ID": "5"})
check("ENV_FILES=off reads no file", got["SBOT_TOKEN"] is None, got["SBOT_TOKEN"])
check("ENV_FILES=off applies no ADMIN_ID (TestBot's admin is its own)", got["SBOT_ADMIN_ID"] is None,
      got["SBOT_ADMIN_ID"])

got, _ = scenario(shared="ADMIN_ID=42\n", bot="testbot")
check("a folder that is not one of the five gets no admin from ADMIN_ID",
      all(got[k] is None for k in ("SBOT_ADMIN_ID", "MBOT_ADMIN_ID")), got)

print()
if failures:
    print(f"FAILED ({len(failures)}):", *failures, sep="\n  ")
    sys.exit(1)
print("All envfiles checks passed.")
