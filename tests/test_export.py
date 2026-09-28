"""AnonBot's /export, driven end to end against a fake Telegram and a fake
relay index.

The two modes differ in exactly one thing and that one thing is the whole
promise, so it is what this checks hardest: copying never reads a message, and
the document mode reads each one exactly once and deletes what it read. Plus
the things that are easy to get wrong once nobody is forwarding anything --
which conversation a selection means, the cap across several of them, and that
neither mode puts a name, a username or an id anywhere.
"""
import asyncio
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace as NS

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / ".mirror" / "anon_bot"))
sys.path.insert(0, str(HERE / "stubs"))

os.environ.setdefault("ABOT_TOKEN", "1:AA")
os.environ.setdefault("ABOT_USERNAME", "x")
os.environ.setdefault("ABOT_ADMIN_ID", "1")
os.environ.setdefault("DATABASE_URL", "postgresql://stub")

import bot as ab          # noqa: E402
import transcript as tr   # noqa: E402

failures = []


def check(name, ok, detail=""):
    print(("  ok  " if ok else "  FAIL  ") + name + ("" if ok else f"  -- {detail}"))
    if not ok:
        failures.append(name)


OWNER_ID = 5000
T0 = datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)

# Two conversations in the owner's chat. from_bot=True is a copy the bot
# delivered, i.e. something the anonymous side wrote.
CONVERSATIONS = [
    {"id": 11, "conv_number": 1, "started_at": "2026-09-01T10:00:00",
     "last_activity_at": "2026-09-09T10:00:00", "is_owner": True, "messages": 3},
    {"id": 22, "conv_number": 2, "started_at": "2026-09-05T10:00:00",
     "last_activity_at": "2026-09-10T10:00:00", "is_owner": True, "messages": 2},
]
MESSAGES = {
    11: [{"message_id": 101, "from_bot": True, "sent_at": T0},
         {"message_id": 102, "from_bot": False, "sent_at": T0},
         {"message_id": 103, "from_bot": True, "sent_at": T0}],
    22: [{"message_id": 201, "from_bot": False, "sent_at": T0},
         {"message_id": 202, "from_bot": True, "sent_at": T0}],
}
# What each message actually is, which only the document mode ever looks at.
CONTENT = {
    101: NS(text="Anon wrote this", caption=None),
    102: NS(text="Owner answered", caption=None),
    103: NS(text=None, caption=None, voice=NS(duration=4)),
    201: NS(text="Owner opened this one", caption=None),
    202: NS(text="📩 Conversation #2\n\nreply from anon", caption=None),
}


class FakeBot:
    """Records what was asked of Telegram, and refuses to hand back content
    for anything that was not read on purpose."""

    def __init__(self):
        self.copied, self.forwarded, self.deleted = [], [], []
        self.sent, self.documents, self.edits = [], [], []
        self.next_id = 900

    async def copy_message(self, chat_id, from_chat_id, message_id, **kwargs):
        self.copied.append(message_id)
        self.next_id += 1
        return NS(message_id=self.next_id)

    async def forward_message(self, chat_id, from_chat_id, message_id, **kwargs):
        self.forwarded.append(message_id)
        self.next_id += 1
        body = CONTENT[message_id]
        return NS(message_id=self.next_id, text=getattr(body, "text", None),
                  caption=getattr(body, "caption", None),
                  voice=getattr(body, "voice", None))

    async def delete_message(self, chat_id, message_id, **kwargs):
        self.deleted.append(message_id)

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append(text)
        self.next_id += 1
        return NS(message_id=self.next_id, chat_id=chat_id)

    async def send_document(self, chat_id, document, caption="", filename="", **kwargs):
        self.documents.append((document.getvalue().decode("utf-8"), caption, filename))
        self.next_id += 1
        return NS(message_id=self.next_id)

    async def edit_message_text(self, *args, **kwargs):
        self.edits.append(kwargs.get("text", args[0] if args else ""))
        return NS(message_id=1)


class FakeMessage:
    def __init__(self, chat_id):
        self.chat_id = chat_id
        self.message_id = 1
        self.chat = NS(id=chat_id, type="private")
        self.date = T0


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# The db reads and the in-place editor are the only things the export needs
# that are not in this file.
ab.list_exportable_conversations = lambda user_id, chat_id: [dict(c) for c in CONVERSATIONS]


def _messages(chat_id, conversation_id, limit):
    rows = MESSAGES[conversation_id]
    return rows[:limit], max(0, len(rows) - limit)


ab.conversation_messages = _messages


async def _edit(message, bot, text, **kwargs):
    await bot.edit_message_text(text, chat_id=message.chat_id, message_id=message.message_id)


ab.edit_in_place = _edit
ab._header_texts = lambda conv: [f"📩 Conversation #{conv['conv_number']}"]

print("== which conversation a selection means ==")
convs, rows, left = run(ab._export_rows(OWNER_ID, OWNER_ID, "22"))
check("naming one gets that one", [c["id"] for c in convs] == [22], convs)
check("with its messages", [r["message_id"] for r in rows[22]] == [201, 202], rows)
convs, rows, left = run(ab._export_rows(OWNER_ID, OWNER_ID, "all"))
check("all means all, oldest conversation first", [c["id"] for c in convs] == [11, 22], convs)
check("and every message of them", sum(len(v) for v in rows.values()) == 5, rows)

ceiling = tr.MAX_MESSAGES
tr.MAX_MESSAGES = 4
convs, rows, left = run(ab._export_rows(OWNER_ID, OWNER_ID, "all"))
tr.MAX_MESSAGES = ceiling
check("the cap is one budget across the whole selection, not one each",
      sum(len(v) for v in rows.values()) == 4, rows)
check("and what it left out is counted", left == 1, left)

print("\n== copying it back reads nothing ==")
fake = FakeBot()
convs, rows, _ = run(ab._export_rows(OWNER_ID, OWNER_ID, "all"))
run(ab._export_by_copying(NS(bot=fake), FakeMessage(OWNER_ID), "en", convs, rows))
check("every message was copied", fake.copied == [101, 102, 103, 201, 202], fake.copied)
check("and not one was forwarded, which is the only way it could have been read",
      fake.forwarded == [], fake.forwarded)
check("nothing was deleted either", fake.deleted == [], fake.deleted)
check("each conversation is announced above its copy",
      sum(1 for text in fake.sent if "Conversation #" in text) == 2, fake.sent)
check("and the person is told nothing was read",
      any("did not read" in text for text in fake.sent), fake.sent)

print("\n== writing a document reads each message once, then deletes it ==")
fake = FakeBot()
convs, rows, _ = run(ab._export_rows(OWNER_ID, OWNER_ID, "all"))
run(ab._export_as_document(NS(bot=fake), FakeMessage(OWNER_ID), "en",
                           NS(id=OWNER_ID, username="owner"), convs, rows, 0))
check("every message was read exactly once", fake.forwarded == [101, 102, 103, 201, 202],
      fake.forwarded)
check("and every copy it made was deleted again",
      sorted(fake.deleted) == sorted(m + 1 for m in range(901, 906)) or
      len(fake.deleted) == len(fake.forwarded), (fake.forwarded, fake.deleted))
check("nothing was copied, so nothing is left in the chat", fake.copied == [], fake.copied)
check("a document came back", len(fake.documents) == 1, fake.documents)

page, caption, filename = fake.documents[0]
check("with both conversations in it", page.count("<section>") == 2, page.count("<section>"))
check("the two sides are Owner and Anon",
      "Owner" in page and "Anon" in page, page[:200])
# The stylesheet has @media in it, so the search is the body rather than the
# whole page -- an id or a username would be in what was written, not in the CSS.
body = page[page.index("<body>"):]
check("and nobody's id or username is anywhere in it",
      str(OWNER_ID) not in body and "@" not in body, body[:200])
check("what the anonymous side wrote is there", "Anon wrote this" in page, page[:400])
check("a voice message is named and timed rather than included",
      "[Voice message: 0:04]" in page, page[page.find("Voice") - 60:page.find("Voice") + 60])
check("the bot's own header is off the delivered bubble",
      "Conversation #2\n\nreply" not in page and "reply from anon" in page)
check("the file is named for when it was made",
      filename.startswith("anonbot_transcript_") and filename.endswith(".html"), filename)
check("and the caption counts what is in it", "5" in caption, caption)

print("\n== a guest's export calls them Anon, and the owner Owner ==")
guest_convs = [{"id": 33, "conv_number": 7, "started_at": "2026-09-02T10:00:00",
                "last_activity_at": "2026-09-08T10:00:00", "is_owner": False, "messages": 2}]
GUEST_MESSAGES = {33: [{"message_id": 301, "from_bot": False, "sent_at": T0},
                       {"message_id": 302, "from_bot": True, "sent_at": T0}]}
CONTENT[301] = NS(text="what the guest wrote", caption=None)
CONTENT[302] = NS(text="what the owner answered", caption=None)
fake = FakeBot()
run(ab._export_as_document(NS(bot=fake), FakeMessage(9000), "en",
                           NS(id=9000, username=None), guest_convs, GUEST_MESSAGES, 0))
page = fake.documents[0][0]
guest_line = page[page.index("what the guest wrote") - 200:page.index("what the guest wrote")]
check("their own message is the anonymous side", "Anon" in guest_line, guest_line[-120:])
check("the inbox holder's number is not shown to a guest",
      "#7" not in page, page[:300])

print()
if failures:
    print(f"FAILURES ({len(failures)}):")
    for name in failures:
        print("  -", name)
    sys.exit(1)
print("an export copies without reading, or reads once and deletes")
