"""ConvertBot's live status card: what it says, and that it stays one message.

    python3 tests/test_card.py

While a conversion runs, its status message is redrawn every few seconds --
the stage, a progress bar when the work can say how far it has got, the time
so far and, once there is a rate worth believing, the time left. The parts
that have to be right:

  the bar never shows 100% before the work is done;
  "time left" appears only once it is more than a guess;
  every string it uses exists in all three languages;
  a redraw that would say the same thing is not sent;
  the Stop button is there while converting and gone while sending;
  when something newer is below the card, it moves to the bottom and the copy
  above is deleted, so a conversion never has two status messages.
"""
import asyncio
import os
import pathlib
import sys
from types import SimpleNamespace

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / ".mirror" / "convert_bot"))
sys.path.insert(0, str(HERE / "stubs"))
os.environ.setdefault("CBOT_TOKEN", "1:test")
os.environ.setdefault("CBOT_USERNAME", "test")
os.environ.setdefault("DATABASE_URL", "postgresql://stub")
for variable in ("CBOT_API_ID", "CBOT_API_HASH", "LOCAL_BOT_API_URL"):
    os.environ.pop(variable, None)

import convert_runner as cr  # noqa: E402
import i18n                   # noqa: E402
import jobs                   # noqa: E402
import live_message           # noqa: E402

failures = []


def check(name, ok, detail=""):
    print(("  ok  " if ok else "  FAIL  ") + name + ("" if ok else f"  -- {detail}"))
    if not ok:
        failures.append(name)


def job(price=140, **extra):
    j = jobs.Job(user_id=7, chat_id=7, paths=[], src_ext="mp4", target_ext="webm",
                 size=10, price=price, lang="en")
    j.started_at = 1000.0
    j.extra.update(extra)
    return j


print("== the pieces ==")
check("half done is five cells of ten", cr.progress_bar(0.5) == "🟩" * 5 + "⬜" * 5 + " 50%", cr.progress_bar(0.5))
check("never 100% while it is still running", "100%" not in cr.progress_bar(1.0) and "99%" in cr.progress_bar(1.0),
      cr.progress_bar(1.0))
check("never below nothing", cr.progress_bar(-3).startswith("⬜" * 10))
check("times read as minutes and seconds", (cr.clock(7), cr.clock(102), cr.clock(725)) == ("0:07", "1:42", "12:05"))
check("no time left from the first second", cr.time_left(10, 100, 1.0) is None)
check("nor from the first 3% of the work", cr.time_left(2, 100, 30.0) is None)
check("then the rate so far", abs(cr.time_left(25, 100, 10.0) - 30.0) < 0.01, cr.time_left(25, 100, 10.0))
check("and nothing once it is done", cr.time_left(100, 100, 10.0) is None)

print("\n== the card ==")
paid = cr.status_card("en", job(stage="converting", stage_started=1000.0, progress=(25, 100),
                                balance_after=360), now=1010.0)
lines = paid.split("\n")
check("it names the conversion", lines[0] == "🔄 MP4 → WEBM", lines)
check("the stage", lines[1] == i18n.t("en", "card_converting"), lines)
check("the bar", lines[2] == cr.progress_bar(0.25), lines)
check("the time so far and the time left",
      lines[3] == i18n.t("en", "card_elapsed", elapsed="0:10") + " · " + i18n.t("en", "card_left", left="0:30"),
      lines)
check("and what it cost", lines[4] == i18n.t("en", "card_paid", price=140, balance=360), lines)
early = cr.status_card("en", job(stage="converting", stage_started=1000.0, progress=None, balance_after=0),
                       now=1002.0).split("\n")
check("without progress there is no bar, only the time so far",
      len(early) == 4 and early[2] == i18n.t("en", "card_elapsed", elapsed="0:02"), early)
free = cr.status_card("en", job(price=0, stage="sending", stage_started=1005.0), now=1008.0).split("\n")
check("a free one says so, and sending says so", free[1] == i18n.t("en", "card_sending")
      and free[-1] == i18n.t("en", "card_free"), free)
check("the time is the whole conversion's, not the stage's",
      free[2] == i18n.t("en", "card_elapsed", elapsed="0:08"), free)
card_keys = ("card_downloading", "card_download_sizes", "card_converting", "card_sending", "card_elapsed",
             "card_left", "card_paid", "card_free")
missing = [(lang, key) for lang in ("en", "uz", "ru") for key in card_keys if key not in i18n.STRINGS[lang]]
check("every string on the card exists in all three languages", not missing, missing)
for lang in ("uz", "ru"):
    text = cr.status_card(lang, job(stage="converting", stage_started=1000.0, progress=(50, 100),
                                    balance_after=10), now=1020.0)
    check(f"and renders in {lang}", "🟩" in text and "{" not in text, text)
download = cr.download_card("en", 12 * 1024 * 1024, 40 * 1024 * 1024, 6.0).split("\n")
check("the download card: what, the bar, the sizes and the time left",
      download[0] == i18n.t("en", "card_downloading") and download[1] == cr.progress_bar(0.3)
      and download[2].startswith(i18n.t("en", "card_download_sizes", done="12.0 MB", total="40.0 MB") + " · "),
      download)
check("before anything has arrived it is one line", cr.download_card("en", 0, 0, 0)
      == i18n.t("en", "card_downloading"))


print("\n== one message, kept current ==")


class FakeBot:
    def __init__(self):
        self.edits, self.sent, self.deleted = [], [], []
        self.next_id = 500

    async def edit_message_text(self, chat_id, message_id, text, **kwargs):
        self.edits.append((message_id, text, kwargs.get("reply_markup")))

    async def send_message(self, chat_id, text, **kwargs):
        self.next_id += 1
        self.sent.append((self.next_id, text, kwargs.get("reply_markup")))
        return SimpleNamespace(chat_id=chat_id, message_id=self.next_id)

    async def delete_message(self, chat_id, message_id):
        self.deleted.append(message_id)


async def one_message():
    bot = FakeBot()
    runner = cr.ConvertRunner(SimpleNamespace(bot=bot), [], HERE, lambda *a: "")
    j = job(stage="converting", stage_started=0.0, progress=None, balance_after=5)
    j.started_at = None
    j.extra["status_message"] = SimpleNamespace(chat_id=7, message_id=400)
    live_message.note_incoming(7, 399)

    await runner.card(j)
    check("the first draw edits the message the conversion started from",
          [e[0] for e in bot.edits] == [400] and not bot.sent, (bot.edits, bot.sent))
    check("with the Stop button while converting", bot.edits[-1][2] is not None)
    await runner.card(j)
    check("a redraw that would say the same thing is not sent", len(bot.edits) == 1, len(bot.edits))

    j.extra["progress"] = (1, 4)
    live_message.bump(7, 450)            # a progress note, or the person writing
    await runner.card(j)
    check("with something newer below it, the card moves to the bottom",
          len(bot.sent) == 1 and bot.sent[0][0] == 501, bot.sent)
    check("and the copy left above is deleted", bot.deleted == [400], bot.deleted)
    j.extra["progress"] = (2, 4)
    await runner.card(j)
    check("after which it is edited where it now is", bot.edits[-1][0] == 501 and len(bot.sent) == 1,
          (bot.edits[-1:], bot.sent))

    j.extra.update(stage="sending", progress=None)
    await runner.card(j)
    check("sending has no Stop button", bot.edits[-1][2] is None, bot.edits[-1])
    await runner.failed(j, jobs.CRASH, "boom", None)
    check("and the ending lands on the same message", bot.edits[-1][0] == 501 and len(bot.sent) == 1,
          (bot.edits[-1:], bot.sent))


asyncio.run(one_message())

print()
if failures:
    print(f"FAILURES ({len(failures)}):")
    for name in failures:
        print("  -", name)
    sys.exit(1)
print("the card says what is happening, in one message")
