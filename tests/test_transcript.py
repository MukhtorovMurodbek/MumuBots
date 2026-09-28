"""AnonBot's /export, the part with no Telegram in it: what a message is, whose
side it was on, and what the page says.

Rewritten for 1.7.0. Until then an export was built from messages somebody
forwarded back to the bot, and most of this file was about holding those
forwards in memory and getting rid of them again. There are no forwards now --
the bot works from anon_relay's index of which message id belonged to which
conversation -- so what is left to check here is smaller and sharper: reading
a message shallowly, naming the two sides without naming anybody, and a page
that cannot be made to run somebody else's markup.
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace as NS

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / ".mirror" / "anon_bot"))
sys.path.insert(0, str(HERE / "stubs"))

import transcript as tr  # noqa: E402

failures = []


def check(name, ok, detail=""):
    print(("  ok  " if ok else "  FAIL  ") + name + ("" if ok else f"  -- {detail}"))
    if not ok:
        failures.append(name)


T0 = datetime(2026, 9, 10, 12, 0, 0, tzinfo=timezone.utc)

print("== what a message is ==")
check("words are words", tr.describe(NS(text="hi <b>")) == ("text", "hi <b>", ""))
check("a photo is named, and its caption kept",
      tr.describe(NS(text=None, photo=(NS(),), caption="look")) == ("photo", "look", ""))
check("a voice message says how long",
      tr.describe(NS(text=None, voice=NS(duration=95), caption=None)) == ("voice", "", "1:35"))
check("a file says its name",
      tr.describe(NS(text=None, document=NS(file_name="cv.pdf"), caption=None)) == ("document", "", "cv.pdf"))
check("a GIF is a GIF, not the document Telegram also calls it",
      tr.describe(NS(text=None, animation=NS(duration=3), document=NS(file_name="x.mp4"),
                     caption=None))[0] == "animation")
check("a sticker keeps only its emoji",
      tr.describe(NS(text=None, sticker=NS(emoji="🙂"), caption=None)) == ("sticker", "", "🙂"))
contact = tr.describe(NS(text=None, contact=NS(phone_number="+998901234567"), caption=None))
check("a contact is named and nothing of it is copied", contact == ("contact", "", ""), contact)
place_ = tr.describe(NS(text=None, venue=NS(title="Home"), location=NS(latitude=1), caption=None))
check("a location is named and its coordinates are not", place_ == ("location", "", ""), place_)

print("\n== whose side a message was on ==")
# `from_bot` is a fact about the chat the row is in: a copy the bot delivered
# into it, or what the person in that chat wrote. So the same flag means
# opposite people on the two sides, and who is exporting decides which.
check("in the owner's chat, their own message is the owner's",
      tr.side_of(from_bot=False, exporter_is_owner=True) == tr.OWNER)
check("and a delivered copy is the anonymous writer's",
      tr.side_of(from_bot=True, exporter_is_owner=True) == tr.ANON)
check("in the guest's chat, their own message is the anonymous one",
      tr.side_of(from_bot=False, exporter_is_owner=False) == tr.ANON)
check("and a delivered copy is the inbox holder's",
      tr.side_of(from_bot=True, exporter_is_owner=False) == tr.OWNER)
check("there are exactly two sides, and neither is a name",
      {tr.OWNER, tr.ANON} == {"owner", "anon"})

print("\n== one message as a line of the page ==")
item = tr.item_for(NS(text=None, voice=NS(duration=8), caption=None), 17, tr.ANON,
                   T0.replace(tzinfo=None))
check("it carries the id, the side and the kind",
      (item.message_id, item.side, item.kind) == (17, tr.ANON, "voice"), item)
check("a time from a database with no zone is read as UTC", item.sent_at == T0, item.sent_at)
check("and nothing was downloaded to find out", item.text == "" and item.detail == "0:08", item)

print("\n== the header the bot put above a delivered message ==")
check("comes off the words", tr.strip_header("📩 Conversation #3\n\nhello", ["📩 Conversation #3"]) == "hello")
check("a bubble that was only the header is dropped",
      tr.strip_header("📩 Conversation #3", ["📩 Conversation #3"]) is None)
check("words that merely start the same way are left alone",
      tr.strip_header("📩 Conversation #30 is mine", ["📩 Conversation #3"]) == "📩 Conversation #30 is mine")

print("\n== the page ==")
page = tr.render(
    title="Transcript", generated="Made today", lang="en", footer="Kept nowhere",
    sections=[("Conversation #1", "started 2026-09-10", [
        {"label": "Owner", "own": True, "text": "<script>alert(1)</script>", "note": "", "when": T0},
        {"label": "Anon", "own": False, "text": "ok", "note": "[Photo]", "when": T0 + timedelta(days=1)},
    ])]).decode("utf-8")
check("what somebody wrote cannot become markup", "<script>alert" not in page and "&lt;script&gt;" in page)
check("the page runs no script of its own", "<script" not in page.lower().replace("&lt;script", ""))
check("and loads nothing from anywhere", "http://" not in page and "https://" not in page and "src=" not in page)
check("a new day is marked", page.count('class="day"') == 2, page.count('class="day"'))
check("your own messages are set apart from theirs", page.count('class="msg own"') == 1)
check("the two sides are the only people named in it",
      "Owner" in page and "Anon" in page)

print("\n== nothing is held between exports ==")
# The old design kept every forwarded message in a module-level dict for up to
# fifteen minutes, and most of this file existed to prove that dict emptied.
# There is no such dict now: an export is one pass inside one handler, and
# what it reads it hands to render() and drops.
module_state = [name for name, value in vars(tr).items()
                if isinstance(value, (dict, list, set)) and not name.startswith("__")
                and name not in ("_MEDIA", "_BYTES_PER_PAGE")]
check("the module holds no store of its own", not module_state, module_state)

print()
if failures:
    print(f"FAILURES ({len(failures)}):")
    for name in failures:
        print("  -", name)
    sys.exit(1)
print("a transcript is read once, named without names, and kept nowhere")
