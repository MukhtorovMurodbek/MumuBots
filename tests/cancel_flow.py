"""Drives /cancel's ask-first menu in all four child bots, with Telegram
stubbed out entirely -- nothing here touches the network.

/cancel used to stop everything it found and then say what it had stopped.
It now says what it *could* stop and waits to be told which, because a bot
that is holding a half-built sticker pack and a donation prompt at the same
time had no way to be told "the prompt, not the pack". The behaviour that
has to hold in every bot:

  1. Nothing pending -> the old answer, immediately, with no buttons. This
     is the common case and the one that must not grow a confirmation step.
  2. Something pending -> a question, and nothing stopped yet.
  3. "Keep going" -> still nothing stopped.
  4. One item -> that item, and only that item.
  5. "All of it" -> the lot. Offered only when there is more than one thing
     it could mean.

3 and 4 are the point of the whole change, so they are checked by looking at
what is *still pending* afterwards rather than at what the bot said about it.

Run from the repo root:  python tests/cancel_flow.py
"""
import os
import subprocess
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
DSN = os.environ.get("TEST_DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/botfamily_test")

BOTS = {
    "sticker_bot":    {"SBOT_TOKEN": "1:AA", "SBOT_USERNAME": "x"},
    "convert_bot":    {"CBOT_TOKEN": "1:AA", "CBOT_USERNAME": "x"},
    "downloader_bot": {"DBOT_TOKEN": "1:AA", "DBOT_USERNAME": "x"},
    "anon_bot":       {"ABOT_TOKEN": "1:AA", "ABOT_USERNAME": "x"},
}

CHILD = r'''
import asyncio, os, sys
from types import SimpleNamespace
sys.path.insert(0, os.getcwd())
sys.path.insert(1, os.path.join(os.path.dirname(os.getcwd()), "shared"))

import db, i18n, bot
import shared_features as sf

UID = 424243
BOT_NAME = os.path.basename(os.getcwd())

db.init_db()

failures = []


def check(label, cond):
    print(("  ok   " if cond else "  FAIL ") + label)
    if not cond:
        failures.append(label)


# --- Telegram, reduced to what /cancel actually touches -------------------

# The menu is rewritten through live_message.edit_in_place, which edits by
# (chat_id, message_id) on the BOT rather than calling message.edit_text --
# so an edit has to be routed back to the Msg it lands on, or every rewrite
# looks to this harness like a message that was never rewritten.
_msgs_by_id = {}


class FakeBot:
    def __init__(self):
        self.deleted, self.sent = [], []

    async def delete_message(self, chat_id, message_id):
        self.deleted.append(message_id)

    async def send_message(self, chat_id, text, **kw):
        self.sent.append(text)
        return SimpleNamespace(message_id=1, chat_id=chat_id, text=text)

    async def edit_message_text(self, chat_id, message_id, text, **kw):
        target = _msgs_by_id.get(message_id)
        if target is None:
            raise RuntimeError(f"edit to an unknown message {message_id}")
        target.edits.append(text)
        return target


class Msg:
    def __init__(self, text="/cancel"):
        self.message_id, self.chat_id, self.text = 10, UID, text
        self.caption, self.reply_to_message = None, None
        self.replies, self.edits = [], []
        _msgs_by_id[self.message_id] = self

    async def reply_text(self, text, **kw):
        reply = Msg(text)
        self.replies.append((text, kw.get("reply_markup")))
        return reply

    async def edit_text(self, text, **kw):
        self.edits.append(text)
        return self


class Ctx:
    def __init__(self):
        self.user_data, self.chat_data, self.bot_data = {}, {}, {}
        self.args = []
        self.bot = FakeBot()


def command_update(msg=None):
    msg = msg or Msg()
    return SimpleNamespace(
        message=msg, effective_message=msg, callback_query=None,
        effective_user=SimpleNamespace(id=UID, username="tester", full_name="Tester"),
        effective_chat=SimpleNamespace(id=UID),
    )


def tap_update(menu_msg, key):
    query = SimpleNamespace(
        data=sf.CANCEL_PICK_PREFIX + key, message=menu_msg,
        answer=lambda *a, **k: asyncio.sleep(0),
    )
    return SimpleNamespace(
        message=None, effective_message=menu_msg, callback_query=query,
        effective_user=SimpleNamespace(id=UID, username="tester", full_name="Tester"),
        effective_chat=SimpleNamespace(id=UID),
    )


def buttons(markup):
    return [b for row in markup.inline_keyboard for b in row]


def keys_of(markup):
    return [b.callback_data.split(":", 1)[1] for b in buttons(markup)]


# --- what each bot can be caught waiting on -------------------------------
# A donation prompt is the one every bot has. The second item is that bot's
# own, and is set up the cheapest way that is still the real state its
# /cancel reads: no Telegram calls, no files on disk.

def arm_donation(ctx):
    ctx.user_data["donate_custom_currency"] = "XTR"
    ctx.user_data[sf.FORCE_REPLY_KEY] = 99


def arm_own(ctx):
    if BOT_NAME == "sticker_bot":
        ctx.user_data["mode"] = "new"          # a pack being named
    elif BOT_NAME == "convert_bot":
        ctx.user_data["convert"] = {"src_ext": "mp4", "path": None}
    elif BOT_NAME == "anon_bot":
        # Points the follower's routing state at a fresh conversation, which
        # is exactly what tapping someone's inbox link does.
        db.start_new_anon_conversation(UID + 1, UID)


def clear_own():
    if BOT_NAME == "anon_bot":
        db.clear_follower_state(UID)


HAS_OWN_ITEM = BOT_NAME != "downloader_bot"


async def pending(ctx):
    """What the bot would offer right now, by its own reckoning."""
    return [item.key for item in await bot._cancel_items(command_update(), ctx, "en")]


async def run():
    # Whatever a previous run (or a crash mid-run) left behind. The in-memory
    # states go with the Ctx; the database-backed one does not.
    clear_own()

    # ---- 1. nothing pending: answer, do not ask --------------------------
    ctx = Ctx()
    update = command_update()
    await bot.cancel_command(update, ctx)
    text, markup = update.message.replies[-1]
    check("nothing pending -> the plain answer", text == i18n.t("en", "cancel_nothing"))
    check("nothing pending -> no buttons", markup is None or not getattr(markup, "inline_keyboard", None))

    # ---- 2. one thing pending: ask, stop nothing yet ---------------------
    ctx = Ctx()
    arm_donation(ctx)
    update = command_update()
    await bot.cancel_command(update, ctx)
    text, markup = update.message.replies[-1]
    check("one pending -> asks first", text.startswith(i18n.t("en", "cancel_ask")))
    check("one pending -> nothing stopped yet", await pending(ctx) == ["donation"])
    check("one pending -> no 'all of it' button",
          keys_of(markup) == ["donation", sf.CANCEL_PICK_NONE])

    # ---- 3. "keep going" changes nothing ---------------------------------
    menu = Msg(text)
    await bot.cancel_choice_callback(tap_update(menu, sf.CANCEL_PICK_NONE), ctx)
    check("keep going -> says so", menu.edits and menu.edits[-1] == i18n.t("en", "cancel_kept"))
    check("keep going -> the donation prompt survives", await pending(ctx) == ["donation"])

    if not HAS_OWN_ITEM:
        # ---- one item, cancelled ----------------------------------------
        menu = Msg(text)
        await bot.cancel_choice_callback(tap_update(menu, "donation"), ctx)
        check("picking it -> stopped", await pending(ctx) == [])
        check("picking it -> reported", i18n.t("en", "cancel_item_donation") in menu.edits[-1])
        return

    # ---- 4. two things pending: pick one, keep the other -----------------
    ctx = Ctx()
    arm_donation(ctx)
    arm_own(ctx)
    update = command_update()
    await bot.cancel_command(update, ctx)
    text, markup = update.message.replies[-1]
    offered = await pending(ctx)
    check("two pending -> both offered", len(offered) == 2 and "donation" in offered)
    check("two pending -> 'all of it' appears",
          keys_of(markup) == offered + [sf.CANCEL_PICK_ALL, sf.CANCEL_PICK_NONE])

    own_key = [k for k in offered if k != "donation"][0]
    menu = Msg(text)
    await bot.cancel_choice_callback(tap_update(menu, own_key), ctx)
    left = await pending(ctx)
    check(f"picking {own_key} -> stops it", own_key not in left)
    check(f"picking {own_key} -> leaves the donation prompt alone", left == ["donation"])
    check("what it stopped is named", i18n.t("en", "cancel_item_donation") not in menu.edits[-1])

    # ---- 5. "all of it" ---------------------------------------------------
    clear_own()
    ctx = Ctx()
    arm_donation(ctx)
    arm_own(ctx)
    update = command_update()
    await bot.cancel_command(update, ctx)
    text, markup = update.message.replies[-1]
    menu = Msg(text)
    await bot.cancel_choice_callback(tap_update(menu, sf.CANCEL_PICK_ALL), ctx)
    check("all of it -> nothing left pending", await pending(ctx) == [])
    check("all of it -> the stale force-reply prompt was taken down too",
          99 in ctx.bot.deleted)
    clear_own()


try:
    asyncio.run(run())
finally:
    clear_own()
    db.close_pool()
sys.exit(1 if failures else 0)
'''


def main() -> int:
    failures = 0
    for folder, extra in BOTS.items():
        print(f"== {folder} ==")
        env = dict(os.environ)
        env.update({
            "DATABASE_URL": DSN, "DB_SCHEMA": folder, "PYTHONIOENCODING": "utf-8",
            "SIBLING_BOTS": "stickerbot:StickerBot:a,convertbot:ConvertBot:b,"
                            "downloaderbot:DownloaderBot:c,anonbot:AnonBot:d",
        })
        env.update(extra)
        proc = subprocess.run(
            [sys.executable, "-c", CHILD], cwd=ROOT / folder, env=env,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        out = proc.stdout.rstrip()
        if out:
            print(out)
        if proc.returncode != 0:
            failures += 1
            if proc.stderr.strip():
                print(proc.stderr.rstrip()[-2500:])
    print("\nFAILURES:", failures)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
