"""ManagerBot's /backup, with a stubbed telegram and a fake family_db.

The owner asked for three things of it: the whole database, in a form that
can be redeployed from without losing anything; sent to them and pinned; and
never unpinned. The first is family_db.py's archive and is exercised against
a real database in DEPLOY.md's restore walk-through. This holds the other two,
and the part of the first that lives in the bot: a file over what a bot may
send arrives as parts that join back into exactly the archive.
"""
import asyncio
import io
import json
import os
import re
import sys
import types
import zipfile
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE / ".mirror" / "manager_bot"))
sys.path.insert(0, str(HERE / "stubs"))
os.environ.setdefault("MBOT_TOKEN", "x")
os.environ.setdefault("MBOT_USERNAME", "x")
os.environ.setdefault("MBOT_ADMIN_ID", "1")
os.environ.setdefault("DATABASE_URL", "postgresql://stub")

import bot as mb  # noqa: E402

failures = []


def check(name, ok, detail=""):
    print(("  ok  " if ok else "  FAIL  ") + name + ("" if ok else f"  -- {detail}"))
    if not ok:
        failures.append(name)


print("== cutting an archive into parts a bot may send ==")
check("a small archive is one part", mb._backup_parts(10, 49) == [(0, 10)], mb._backup_parts(10, 49))
check("exactly the limit is still one part", mb._backup_parts(49, 49) == [(0, 49)])
check("a larger one is cut at the limit, and nothing is lost",
      mb._backup_parts(100, 49) == [(0, 49), (49, 98), (98, 100)], mb._backup_parts(100, 49))
check("the default limit is under the Bot API's 50 MB", mb.BACKUP_PART_BYTES < 50 * 1024 * 1024)

# A family_db that writes a real zip, so the bot's own reading of it runs.
ROWS = "\n".join(f"{n},{'x' * 40}{n}" for n in range(400))
fake = types.ModuleType("family_db")


def fake_backup(dsn, out_path, schemas):
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_STORED) as archive:
        archive.writestr("family/star_balances.csv", "user_id,note\n" + ROWS + "\n")
        archive.writestr("MANIFEST.json", json.dumps({
            "format": "csv-copy-v1",
            "tables": [{"schema": "family", "table": "star_balances",
                        "columns": ["user_id", "note"], "approx_rows": 400}]}))


fake.backup = fake_backup
sys.modules["family_db"] = fake


class Bot:
    def __init__(self):
        self.calls = []

    async def send_document(self, **kwargs):
        kwargs["data"] = kwargs["document"].getvalue()
        self.calls.append(("send", kwargs))
        return types.SimpleNamespace(message_id=1000 + len(self.calls))

    async def pin_chat_message(self, **kwargs):
        self.calls.append(("pin", kwargs))
        return True

    async def unpin_chat_message(self, **kwargs):
        self.calls.append(("unpin", kwargs))

    async def unpin_all_chat_messages(self, **kwargs):
        self.calls.append(("unpin", kwargs))


class Note:
    async def delete(self):
        pass


edits = []


async def fake_edit(message, bot, text, **kwargs):
    edits.append(text)


async def allowed(update, context):
    return True


async def reply_text(text, **kwargs):
    return Note()


mb.guard = allowed
mb.edit_in_place = fake_edit


def run_backup():
    bot = Bot()
    update = types.SimpleNamespace(
        effective_chat=types.SimpleNamespace(id=77),
        effective_user=types.SimpleNamespace(id=1, username="owner"),
        message=types.SimpleNamespace(reply_text=reply_text))
    context = types.SimpleNamespace(bot=bot, args=[])
    asyncio.run(mb.backup_command(update, context))
    return bot.calls


print("\n== one archive, one pinned document ==")
calls = run_backup()
sends = [kw for kind, kw in calls if kind == "send"]
pins = [kw for kind, kw in calls if kind == "pin"]
check("one document is sent", len(sends) == 1, str([k for k, _ in calls]))
check("named as the zip it is", sends and sends[0]["filename"].endswith(".zip"), sends and sends[0]["filename"])
check("and it is the whole archive, readable as one",
      sends and zipfile.ZipFile(io.BytesIO(sends[0]["data"])).testzip() is None)
check("it is pinned", len(pins) == 1 and pins[0]["message_id"] == 1001, str(pins))
check("without a notification", pins and pins[0].get("disable_notification") is True)
check("in the chat it was asked from", pins and pins[0]["chat_id"] == 77)
check("nothing is unpinned", not [k for k, _ in calls if k == "unpin"])
check("the caption carries the checksum", sends and "sha256" in sends[0]["caption"])
check("and the reply says how to restore", edits and "family_db.py restore" in edits[-1], edits[-1:])

print("\n== an archive over the limit ==")
mb.BACKUP_PART_BYTES = 4096
edits.clear()
calls = run_backup()
sends = [kw for kind, kw in calls if kind == "send"]
pins = [kw for kind, kw in calls if kind == "pin"]
check("it goes in several parts", len(sends) > 1, len(sends))
check("each within the limit", all(len(kw["data"]) <= 4096 for kw in sends))
check("numbered in order", [kw["filename"][-4:] for kw in sends]
      == [f".{n:03d}" for n in range(1, len(sends) + 1)], [kw["filename"] for kw in sends])
joined = b"".join(kw["data"] for kw in sends)
check("which join back into the archive", zipfile.ZipFile(io.BytesIO(joined)).testzip() is None)
check("every part is pinned", len(pins) == len(sends)
      and [p["message_id"] for p in pins] == [1001 + 2 * n for n in range(len(sends))], str(pins))
check("and the reply says how to join them", edits and "copy /b" in edits[-1], edits[-1:])

print("\n== nothing in ManagerBot unpins ==")
callers = []
for path in sorted((REPO / "manager_bot").glob("*.py")):
    text = path.read_text(encoding="utf-8")
    if re.search(r"\bunpin(_all)?_chat_messages?\b|\bunpinChatMessage\b|\bunpinAllChatMessages\b", text):
        callers.append(path.name)
check("no file calls an unpin method", not callers, str(callers))

print()
if failures:
    print(f"FAILURES ({len(failures)}):")
    for name in failures:
        print("  -", name)
    sys.exit(1)
print("backups are restorable, and stay pinned")
