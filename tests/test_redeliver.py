"""DownloaderBot's "get it as a file" / "get it as compressed" button, tapped.

    python tests/test_redeliver.py

Under a finished download there is one button that does that delivery again the
other way round (bot.py, `redeliver_callback`). It counts against the same
daily allowance as pasting the link again would, so what a tap does has to be
exact. Two things are pinned here, each found by a review and each of which a
green suite had missed:

  * A tap that is REFUSED by the download allowance gives the button back.
    The first tap spends the button (a double tap is two updates in flight,
    and the second must not download the file a second time), and the markup
    is edited at once; when the allowance then says no, nothing was started,
    so the person must not be left with a message that has no button and a
    note that they are out of downloads -- they will be able to tap again
    later. The allowance message is sent, nothing is recorded, nothing starts.
  * A tap takes off only that button (the ConvertBot row stays), and a second
    tap of the same button -- one after the other, or both in flight at once
    while Telegram is still answering the first -- is told "expired", starts
    nothing and records nothing. An old or unknown token says the same.

Telegram, the database and the download itself are faked: the real
`redeliver_callback`, `_nudge_kb`, `_spend_redo` and `_unspend_redo` run.

To see a check catch breakage, copy the bot into tests/.mirror, break the copy
(the way tests/README.md describes) and run this again: it imports from the
mirror when there is one.
"""
import ast
import asyncio
import json
import logging
import os
import pathlib
import sys
import tempfile
from types import SimpleNamespace

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent
SRC = HERE / ".mirror" / "downloader_bot"
if not SRC.exists():
    SRC = REPO / "downloader_bot"
sys.path.insert(0, str(SRC))
sys.path.insert(1, str(REPO / "shared"))
sys.path.insert(0, str(HERE / "stubs"))

os.environ.update(ENV_FILES="off", DBOT_TOKEN="123:test", DBOT_USERNAME="test", DATABASE_URL="postgresql://stub",
                  DBOT_WORK_DIR=tempfile.mkdtemp(prefix="downloaderbot-redeliver-"))

import bot             # noqa: E402
import i18n            # noqa: E402

logging.disable(logging.CRITICAL)

failures = []


def check(name, ok, detail=""):
    print(("  ok  " if ok else "  FAIL  ") + name + (f" -- {detail}" if not ok and detail else ""))
    if not ok:
        failures.append(name)


def button_data(button):
    return getattr(button, "callback_data", None) or getattr(button, "kwargs", {}).get("callback_data")


def button_text(button):
    return getattr(button, "text", None) or (getattr(button, "args", None) or [None])[0]


def row_data(markup):
    return [[button_data(b) for b in row] for row in (markup.inline_keyboard if markup else [])]


# ---------------------------------------------------------------------------
# The world around the handler
# ---------------------------------------------------------------------------

class World:
    def __init__(self, allowance=None):
        self.allowance = allowance or {"allowed": True}
        self.calls = []             # in order: "answer", "edit", "allowance", ("record", platform), "spawn"
        self.texts = []             # what reply_text was asked to send
        self.spawned = []           # the work() each spawn was handed
        self.sent_with = []         # what that work asked _resolve_and_send for


W = World()


class Message:
    async def reply_text(self, text, **kwargs):
        W.texts.append(text)


MESSAGE = Message()


def _fake_allowance(user_id):
    W.calls.append("allowance")
    return W.allowance


def _fake_record(user_id, platform):
    W.calls.append(("record", platform))


def _fake_spawn(context, update, work):
    W.calls.append("spawn")
    W.spawned.append(work)


async def _fake_resolve_and_send(update, context, platform, url, **kwargs):
    W.sent_with.append((platform, url, kwargs))
    return True


async def english(user_id, context=None):
    return "en"


bot.download_allowance = _fake_allowance
bot.record_download = _fake_record
bot._spawn_download = _fake_spawn
bot._resolve_and_send = _fake_resolve_and_send
i18n.get_lang = english

TOKEN = "tok1234567"
TAP = f"redo:{TOKEN}"
EXPIRED = [(i18n.t("en", "redeliver_expired"), {"show_alert": True})]
STARTED = [(None, {})]
NUDGE_ONLY = row_data(bot._nudge_kb())          # what a spent button leaves: the ConvertBot row, or nothing


def delivery(lossless=False):
    return SimpleNamespace(bot=SimpleNamespace(), user_data={"redeliver": {TOKEN: {
        "platform": "tiktok", "url": "https://vm.tiktok.com/x", "lossless": lossless}}})


def update_with(query):
    return SimpleNamespace(callback_query=query, effective_user=SimpleNamespace(id=7),
                           effective_chat=SimpleNamespace(id=7), effective_message=MESSAGE, message=MESSAGE)


async def tap(world, context, data=TAP, yielding=False):
    """One tap of `data`. `yielding`: Telegram takes a moment to answer, as it does for real, and anything else
    that is in flight gets to run meanwhile. Returns (what the tap answered with, the markups it edited in)."""
    global W
    W = world
    answers, edits = [], []

    async def answer(text=None, **kw):
        W.calls.append("answer")
        answers.append((text, kw))
        if yielding:
            await asyncio.sleep(0)

    async def edit(reply_markup=None):
        W.calls.append("edit")
        edits.append(reply_markup)
        if yielding:
            await asyncio.sleep(0)

    query = SimpleNamespace(data=data, message=MESSAGE, answer=answer, edit_message_reply_markup=edit)
    await bot.redeliver_callback(update_with(query), context)
    return answers, edits


def recorded(world):
    return [c for c in world.calls if isinstance(c, tuple)]


async def main():
    print("== a tap ==")
    context, world = delivery(), World()
    answers, edits = await tap(world, context)
    check("answers the query first, takes the button off, asks the allowance, records the download, then starts",
          world.calls == ["answer", "edit", "allowance", ("record", "tiktok"), "spawn"], world.calls)
    check("the button it leaves has no redo in it: only the ConvertBot row, or nothing",
          row_data(edits[0]) == NUDGE_ONLY and not any(d.startswith("redo:") for r in row_data(edits[0]) for d in r),
          row_data(edits[0]))
    await world.spawned[0]()
    check("what it starts is the delivery of the stored link, the other way round from how it was delivered",
          world.sent_with == [("tiktok", "https://vm.tiktok.com/x", {"lossless_override": True})], world.sent_with)
    context, world = delivery(lossless=True), World()
    await tap(world, context)
    await world.spawned[0]()
    check("a file delivery is redone as compressed",
          world.sent_with and world.sent_with[0][2] == {"lossless_override": False}, world.sent_with)

    print("\n== the allowance says no: the button comes back ==")
    out_of = {"allowed": False, "scope": "hour", "hour": 12, "hour_max": 12, "wait_min": 5, "donor": True}
    context, world = delivery(), World(allowance=out_of)
    answers, edits = await tap(world, context)
    check("the person is told so, nothing is recorded, nothing starts",
          world.calls == ["answer", "edit", "allowance", "edit"] and not recorded(world) and not world.spawned
          and world.texts == [bot._allowance_message("en", out_of)], (world.calls, world.texts))
    check("the first edit took the button off, the second puts it back",
          row_data(edits[0]) == NUDGE_ONLY and row_data(edits[1])[0] == [TAP], [row_data(e) for e in edits])
    check("and the restored button says what it said before (here: get it as a file)",
          button_text(edits[1].inline_keyboard[0][0]) == i18n.t("en", "redeliver_as_file"),
          button_text(edits[1].inline_keyboard[0][0]))
    check("the restored keyboard is the one the delivery was sent with: the button, then the ConvertBot row",
          row_data(edits[1]) == row_data(bot._nudge_kb("en", TOKEN, False)), row_data(edits[1]))
    check("the delivery is not spent: the memory holds it, and it can be saved as JSON (user_data is)",
          TOKEN in context.user_data["redeliver"] and not context.user_data["redeliver"][TOKEN].get("spent")
          and json.loads(json.dumps(context.user_data)) == context.user_data, context.user_data)
    world = World()                         # later, with downloads to spare, the same button works
    answers, edits = await tap(world, context)
    check("so a later tap of the same button starts the delivery after all",
          answers == STARTED and world.calls.count("spawn") == 1 and recorded(world) == [("record", "tiktok")],
          (answers, world.calls))
    context = delivery(lossless=True)
    world = World(allowance=out_of)
    answers, edits = await tap(world, context)
    check("a compressed-again button comes back as that (the label follows the delivery)",
          button_text(edits[1].inline_keyboard[0][0]) == i18n.t("en", "redeliver_as_compressed"),
          button_text(edits[1].inline_keyboard[0][0]))
    context = delivery()
    world = World(allowance=out_of)
    await tap(world, context)
    answers, _ = await tap(World(allowance=out_of), context)
    check("refused again and again: each time it is given back and the message is sent again",
          answers == STARTED and not context.user_data["redeliver"][TOKEN].get("spent"), (answers, context.user_data))

    print("\n== a double tap ==")
    context, world = delivery(), World()
    first, _ = await tap(world, context)
    second, second_edits = await tap(world, context)
    check("the second tap of the same button is answered as an expired button, and touches nothing",
          first == STARTED and second == EXPIRED and not second_edits, (first, second))
    check("one download recorded and one delivery started, not two",
          world.calls.count("spawn") == 1 and recorded(world) == [("record", "tiktok")]
          and world.calls.count("allowance") == 1, world.calls)
    check("which is the existing DL-BUTTON-EXPIRED", second[0][0].endswith("DL-BUTTON-EXPIRED"), second)
    check("the album stays in memory, marked spent, and what is remembered can be saved as JSON (user_data is)",
          context.user_data["redeliver"][TOKEN].get("spent") is True
          and json.loads(json.dumps(context.user_data)) == context.user_data, context.user_data)

    context, world = delivery(), World()
    results = await asyncio.gather(tap(world, context, yielding=True), tap(world, context, yielding=True))
    began = [a for a, _ in results if a == STARTED]
    refused = [a for a, _ in results if a == EXPIRED]
    check("both taps in flight at once, each waiting on Telegram's answer: one starts it, the other is told it expired",
          len(began) == 1 and len(refused) == 1 and world.calls.count("spawn") == 1
          and recorded(world) == [("record", "tiktok")], (results, world.calls))

    print("\n== a token the bot no longer holds ==")
    context, world = delivery(), World()
    answers, edits = await tap(world, context, data="redo:nothere")
    check("an unknown token is answered with the expired-button pop-up, and nothing else happens",
          answers == EXPIRED and world.calls == ["answer"] and not edits and not world.spawned, (answers, world.calls))
    check("and it spent nothing", not context.user_data["redeliver"][TOKEN].get("spent"))
    context = SimpleNamespace(bot=SimpleNamespace(), user_data={})
    world = World()
    answers, edits = await tap(world, context)
    check("a restart that forgot everything says the same", answers == EXPIRED and world.calls == ["answer"], answers)

    print("\n== the keyboard a delivery is sent with ==")
    rows = row_data(bot._nudge_kb("en", TOKEN, False))
    check("the button first (redo:<token>, 15 bytes, under Telegram's 64), the ConvertBot row after it",
          rows[0] == [TAP] and rows[1:] == NUDGE_ONLY and len(TAP.encode()) < 64, rows)
    check("without a token there is no button, only the ConvertBot row (or nothing)",
          row_data(bot._nudge_kb("en", None, False)) == NUDGE_ONLY)

    print("\n== the wiring, read from the source ==")
    tree = ast.parse((SRC / "bot.py").read_text(encoding="utf-8"))
    registered = [ast.unparse(call) for call in ast.walk(tree)
                  if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                  and call.func.attr == "add_handler" and "redeliver_callback" in ast.unparse(call)]
    check("the callback is registered for redo: buttons", len(registered) == 1 and "'^redo:'" in registered[0].replace('"', "'"),
          registered)
    spend = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == "redeliver_callback")
    body = ast.unparse(spend)
    check("the spend is made before the first await that follows the lookup (no await between the look and the mark)",
          body.index("_spend_redo") < body.index("await query.answer()"))
    check("a refusal gives it back", body.index("download_allowance") < body.index("_unspend_redo") < body.index("reply_text"))

    print()
    if failures:
        print(f"FAILED ({len(failures)}):")
        for name in failures:
            print("  -", name)
        sys.exit(1)
    print("All checks passed.")


asyncio.run(main())
