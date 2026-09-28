"""Drives every child bot's /start and /language against a real Postgres,
with Telegram stubbed out entirely -- nothing here touches the network.

v0.7.0 turned /start into the language picker in all four child bots, and
v0.7.3 puts the 0.6.0 behaviour back with the picker moved to a /language
command of its own. The resulting rule is three lines long, which is exactly
why it is worth a test: it is easy to state, easy to agree with, and easy to
undo by accident in one bot out of four.

  1. A user with no language on record gets the picker from /start. Once.
  2. Everybody else gets the instructions, in the language on record.
  3. /language is the picker, always, with a tick on the language in force.
  4. The same tap twice is not a crash. A tap is answered by editing the
     picker in place, and Telegram rejects an edit that would change
     nothing -- so a double tap used to reach the user as a BadRequest.
  5. A tap answered after the user has said something else does not edit
     a message that has scrolled away. Editing in place is only correct
     while the message is still the last one in the chat; past that the
     answer has to be a new message or nobody sees it.

Each is asserted per bot, and 2 and 3 in all three languages -- because the
thing that would actually break here is one bot's start() keeping the old
shape while the other three move, and a single-language test would still
pass on a bot that had lost its Uzbek or Russian help text.

Run from the repo root:  python tests/start_flow.py
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

# Same dummy tokens startup.py uses: bot.py reads them at import time, and
# nothing in this test reaches the point of using one.
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

import db, i18n, live_message, bot
from telegram.error import BadRequest

UID = 424242

db.init_db()


# StickerBot, ConvertBot and AnonBot keep the language in user_settings;
# DownloaderBot keeps it in settings, alongside the caption preference. Ask
# the schema which one this bot has rather than hard-coding four answers.
def settings_table():
    with db.pooled() as conn:
        cur = conn.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = %s AND table_name IN ('user_settings', 'settings')",
            (db.DB_SCHEMA,),
        )
        names = [r[0] for r in cur.fetchall()]
    assert names, "this bot has no settings table to read a language out of"
    return names[0]


def forget_language():
    with db.pooled() as conn:
        conn.execute(f"DELETE FROM {settings_table()} WHERE user_id = %s", (UID,))
        conn.commit()


class Msg:
    def __init__(self):
        self.message_id, self.chat_id, self.text = 1, UID, "/start"
        self.caption, self.reply_to_message = None, None
        self.replies = []

    async def reply_text(self, text, **kw):
        self.replies.append((text, kw.get("reply_markup")))
        return SimpleNamespace(message_id=2, chat_id=UID, text=text)


class PickerMsg:
    """The picker message a language button sits on. Since live_message.py
    every edit goes through the Bot rather than through the Message, so
    this only has to be identifiable -- FakeBot below is what records."""
    def __init__(self):
        self.message_id, self.chat_id = 3, UID
        self.text, self.caption, self.reply_to_message = i18n.LANGUAGE_PROMPT, None, None


class FakeBot:
    """Telegram, as far as live_message can tell.

    edit_message_text() answers a second identical edit the way Telegram
    does -- with a BadRequest -- rather than shrugging it off, because
    that is half of what is being tested. The other half is send_message:
    a live message that is no longer last is supposed to arrive as a new
    message, and the only way to see that is to count what was sent."""
    def __init__(self):
        self.edits, self.sent = [], []
        self._next_id = 100

    async def edit_message_text(self, chat_id, message_id, text, **kw):
        seen = (message_id, text, kw.get("reply_markup"))
        if self.edits and self.edits[-1] == seen:
            raise BadRequest(
                "Message is not modified: specified new message content and reply "
                "markup are exactly the same as a current content and reply markup "
                "of the message"
            )
        self.edits.append(seen)
        return SimpleNamespace(message_id=message_id, chat_id=chat_id, text=text)

    async def send_message(self, chat_id, text, **kw):
        self._next_id += 1
        self.sent.append((chat_id, text, kw.get("reply_markup")))
        return SimpleNamespace(message_id=self._next_id, chat_id=chat_id, text=text)


class Query:
    def __init__(self, data, message):
        self.data, self.message = data, message
        self.answers = []

    async def answer(self, text=None, **kw):
        self.answers.append(text)


class Ctx:
    def __init__(self, args=None, bot=None):
        self.user_data, self.chat_data, self.bot_data = {}, {}, {}
        self.args = args or []
        self.bot = bot or FakeBot()


def fresh(args=None):
    msg = Msg()
    update = SimpleNamespace(
        message=msg, effective_message=msg,
        effective_user=SimpleNamespace(id=UID, username="tester", full_name="Tester"),
        effective_chat=SimpleNamespace(id=UID),
        callback_query=None,
    )
    return update, Ctx(args)


def tap(lang, picker, fake_bot):
    """A tap on the picker: a callback query, and no message of its own."""
    q = Query(f"setlang:{lang}", picker)
    return SimpleNamespace(
        message=None, effective_message=picker,
        effective_user=SimpleNamespace(id=UID, username="tester", full_name="Tester"),
        effective_chat=SimpleNamespace(id=UID),
        callback_query=q,
        get_bot=lambda: fake_bot,
    )


def buttons(markup):
    assert markup is not None, "the picker came back with no keyboard"
    return [b for row in markup.inline_keyboard for b in row]


failures = []


def check(label, cond):
    print(("  ok   " if cond else "  FAIL ") + label)
    if not cond:
        failures.append(label)


# ---- 1. no language on record: /start asks -------------------------------
forget_language()
u, c = fresh()
asyncio.run(bot.start(u, c))
text, markup = u.message.replies[-1]
check("first /start shows the picker", text == i18n.LANGUAGE_PROMPT)
check("first /start offers all three languages",
      [b.callback_data for b in buttons(markup)] == ["setlang:en", "setlang:uz", "setlang:ru"])
check("first /start ticks nothing (no language yet)",
      not any(b.text.startswith("✅") for b in buttons(markup)))
check("first /start says nothing else", len(u.message.replies) == 1)

# ---- 2. a language on record: /start prints the instructions --------------
# ---- 3. /language is the picker, whatever the language on record ----------
for lang in ("en", "uz", "ru"):
    db.set_user_language(UID, lang)

    u, c = fresh()
    asyncio.run(bot.start(u, c))
    text, markup = u.message.replies[-1]
    # Against the bot's own build_help_text rather than the raw i18n string:
    # ConvertBot's help text is a template with the price tiers filled in.
    check(f"[{lang}] /start prints the instructions, not the picker",
          text != i18n.LANGUAGE_PROMPT and bot.build_help_text(lang) in text)

    u, c = fresh()
    asyncio.run(bot.language_command(u, c))
    text, markup = u.message.replies[-1]
    ticked = [b.text for b in buttons(markup) if b.text.startswith("✅")]
    check(f"[{lang}] /language shows the picker", text == i18n.LANGUAGE_PROMPT)
    check(f"[{lang}] /language ticks the language in force",
          len(ticked) == 1 and i18n.LANGUAGE_LABELS[lang] in ticked[0])

# ---- 4. the same tap twice ------------------------------------------------
# A tap on the picker is answered by editing the picker into the
# instructions. Tap twice before the first edit lands -- an impatient thumb,
# a slow connection -- and the same edit is asked for twice, which Telegram
# answers with "Message is not modified" rather than a shrug. That reached
# the user as an unhandled BadRequest until the edit started going through
# live_message.py, which treats it as the no-op it is.
forget_language()
live_message._last_incoming.clear()
picker, tg = PickerMsg(), FakeBot()
ctx = Ctx(bot=tg)
asyncio.run(bot.language_chosen(tap("en", picker, tg), ctx))
check("tapping a language edits the picker into the instructions",
      len(tg.edits) == 1 and tg.edits[0][0] == picker.message_id)
check("...in place, without sending a second message", not tg.sent)

try:
    asyncio.run(bot.language_chosen(tap("en", picker, tg), ctx))
    survived = True
except Exception as exc:
    survived = False
    print(f"   {type(exc).__name__}: {exc}")
check("a second identical tap does not crash the handler", survived)
check("...and leaves the message exactly as the first tap made it", len(tg.edits) == 1)
check("...and does not answer it with a new message either", not tg.sent)

# ---- 5. a tap answered after the user has moved on ------------------------
# The picker is only editable while it is still the last thing in the chat.
# Once the user has sent anything of their own, an edit lands above what
# they are looking at -- on a phone, off-screen -- and the bot has answered
# where nobody will see it. live_message watches for exactly that and sends
# rather than edits. Whether a bot leaves a stale picker sitting there is a
# per-bot choice; that the answer is *visible* is not.
forget_language()
live_message._last_incoming.clear()
picker, tg = PickerMsg(), FakeBot()
ctx = Ctx(bot=tg)
# the user types something after the picker was sent, so it is no longer last
live_message.note_incoming(UID, picker.message_id + 1)
asyncio.run(bot.language_chosen(tap("en", picker, tg), ctx))
check("a tap answered after the user has spoken sends rather than edits",
      len(tg.sent) == 1 and not tg.edits)
check("...and the answer is the instructions, not the picker",
      tg.sent[0][1] != i18n.LANGUAGE_PROMPT)
live_message._last_incoming.clear()

# ---- the command menu has to advertise it, or nobody finds it ------------
check("/language is in BOT_COMMANDS",
      any(cmd.command == "language" for cmd in bot.BOT_COMMANDS))

forget_language()
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
