import asyncio, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".mirror", "sticker_bot"))
sys.path.insert(0, os.path.join(HERE, "stubs"))
import live_message as lm
from live_message import LiveMessage


class Msg:
    def __init__(self, chat_id, message_id, text=""):
        self.chat_id, self.message_id, self.text = chat_id, message_id, text


class FakeBot:
    """Records what happened, and hands out message ids the way Telegram
    really does: one counter per chat, shared by everyone in it, so a bot
    message sent after a user message always has the higher id. The whole
    approach rests on that, so the fake has to honour it -- and user_says()
    below drives the same counter."""
    def __init__(self):
        self.next_id = {}
        self.log = []
        self.texts = {}

    def _bump(self, chat_id):
        self.next_id[chat_id] = self.next_id.get(chat_id, 100) + 1
        return self.next_id[chat_id]

    async def send_message(self, chat_id, text, **kw):
        mid = self._bump(chat_id)
        self.log.append(("send", chat_id, mid, text))
        self.texts[(chat_id, mid)] = text
        return Msg(chat_id, mid, text)

    async def edit_message_text(self, chat_id, message_id, text, **kw):
        self.log.append(("edit", chat_id, message_id, text))
        self.texts[(chat_id, message_id)] = text
        return Msg(chat_id, message_id, text)

    async def delete_message(self, chat_id, message_id):
        self.log.append(("delete", chat_id, message_id))


def user_says(bot, chat_id):
    """A message from the user, taking the next id in that chat -- exactly
    what Telegram would do."""
    mid = bot._bump(chat_id)
    lm.note_incoming(chat_id, mid)
    return mid


async def main():
    failures = []
    # equals, not check: most of this suite spells an assertion
    # check(name, ok, detail), and a comparison written in that shape by
    # mistake passes whenever `got` is truthy.
    def equals(name, got, want):
        if got != want:
            failures.append(f"{name}: got {got!r}, want {want!r}")
        else:
            print(f"  ok  {name}")

    # ---- 1. nothing since -> edits in place ----
    lm._last_incoming.clear()
    bot = FakeBot()
    user_says(bot, 1)                     # the message that triggered us
    live = await LiveMessage.send(bot, 1, "step 1")
    first_id = live.message_id
    await live.set(bot, "step 2")
    await live.set(bot, "step 3")
    equals("stays on one message while the user is quiet",
          [k for k, *_ in bot.log], ["send", "edit", "edit"])
    equals("and that message is the original", live.message_id, first_id)

    # ---- 2. the user speaks -> a new message, and it evolves from there ----
    user_says(bot, 1)
    await live.set(bot, "step 4")
    equals("moves to a new message once the user speaks",
          bot.log[-1][0], "send")
    moved_id = live.message_id
    equals("and the new message is a different one", moved_id != first_id, True)
    await live.set(bot, "step 5")
    equals("then evolves the new one in place", bot.log[-1][0], "edit")
    equals("in place means the message it just sent", bot.log[-1][2], moved_id)

    # ---- 3. a button tap must NOT count as the user speaking ----
    lm._last_incoming.clear()
    bot = FakeBot()
    user_says(bot, 2)
    menu = await LiveMessage.send(bot, 2, "menu")
    class FakeUpdate:
        message = None                    # a callback query has no message of its own
        edited_message = None
    lm.note_update(FakeUpdate())
    await menu.set(bot, "submenu")
    equals("a tap keeps the menu editable", bot.log[-1][0], "edit")

    # ---- 4. save()/restore() carries the handle, not the optimism ----
    state = menu.save()
    equals("saved state is JSON-shaped", sorted(state), ["chat_id", "message_id", "watermark"])
    lm._last_incoming.clear()             # as after a restart: nothing is known
    revived = LiveMessage.restore(state)
    equals("restore keeps the message id", revived.message_id, menu.message_id)
    user_says(bot, 2)
    await revived.set(bot, "after a restart")
    equals("a restored handle still notices the user spoke", bot.log[-1][0], "send")

    # ---- 4b. a menu tapped after the user has typed underneath it ----
    lm._last_incoming.clear()
    bot = FakeBot()
    user_says(bot, 6)
    menu2 = await LiveMessage.send(bot, 6, "menu")
    user_says(bot, 6)                     # they typed something, then tapped
    tapped = LiveMessage.adopt(Msg(6, menu2.message_id))
    await tapped.set(bot, "submenu")
    equals("a menu buried by typing is re-sent, not rewritten above it",
          bot.log[-1][0], "send")

    equals("restore(None) is None", LiveMessage.restore(None), None)
    equals("restore(garbage) is None", LiveMessage.restore({"chat_id": "x"}), None)

    # ---- 5. bump(): the bot itself sending underneath ----
    lm._last_incoming.clear()
    bot = FakeBot()
    user_says(bot, 3)
    status = await LiveMessage.send(bot, 3, "status")
    extra = await bot.send_message(3, "a separate message with buttons")
    lm.bump(extra.chat_id, extra.message_id)
    await status.set(bot, "status again")
    equals("bump() moves the status below what was sent under it",
          bot.log[-1][0], "send")

    # ---- 6. an edit Telegram refuses as unchanged is not an error ----
    class Picky(FakeBot):
        async def edit_message_text(self, chat_id, message_id, text, **kw):
            from telegram.error import BadRequest
            raise BadRequest("Message is not modified")
    lm._last_incoming.clear()
    picky = Picky()
    live = await LiveMessage.send(picky, 4, "same")
    await live.set(picky, "same")
    equals('"not modified" does not send a duplicate',
          [k for k, *_ in picky.log], ["send"])

    # ---- 7. a message Telegram cannot find is re-sent ----
    class Gone(FakeBot):
        async def edit_message_text(self, chat_id, message_id, text, **kw):
            from telegram.error import BadRequest
            raise BadRequest("Message to edit not found")
    lm._last_incoming.clear()
    gone = Gone()
    live = await LiveMessage.send(gone, 5, "hello")
    await live.set(gone, "hello again")
    equals("a deleted message is replaced, not lost",
          [k for k, *_ in gone.log], ["send", "send"])

    # ---- 8. the watermark table stays bounded ----
    lm._last_incoming.clear()
    for chat in range(lm.MAX_TRACKED_CHATS + 500):
        lm.note_incoming(chat, 1)
    equals("watermarks are bounded", len(lm._last_incoming), lm.MAX_TRACKED_CHATS)

    print()
    if failures:
        print("FAILURES:")
        for f in failures:
            print("  -", f)
        return 1
    print("all live-message checks passed")
    return 0

sys.exit(asyncio.get_event_loop().run_until_complete(main()))
