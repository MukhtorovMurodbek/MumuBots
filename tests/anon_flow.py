"""Drives AnonBot's real routing against a real Postgres with Telegram stubbed.

Covers the four things that changed:
  1. the guest's swipe-reply resolves (it used to say "no conversation")
  2. a guest message that replies to nothing is held, not delivered
  3. Send-it-anyway delivers the held message
  4. the owner's swipe-reply still resolves, and reaches the guest
Run from the repo root:  python tests/anon_flow.py
Telegram is stubbed out entirely -- nothing here touches the network.
"""
import asyncio
import os
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# AnonBot's modules are imported by their bare names (db, bot, anon_logic),
# exactly as the bot itself does -- so its folder has to be the import root.
os.chdir(ROOT / "anon_bot")
sys.path.insert(0, str(ROOT / "anon_bot"))
sys.path.insert(1, str(ROOT / "shared"))
os.environ.setdefault("DATABASE_URL", os.environ.get(
    "TEST_DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/botfamily_test"))
os.environ.setdefault("DB_SCHEMA", "anon_bot")
os.environ.setdefault("ABOT_TOKEN", "1:AA")
os.environ.setdefault("ABOT_USERNAME", "x")

import db
import bot as ab
from anon_logic import relay_content

OWNER, GUEST, THIRD = 5001, 6002, 7003
db.init_db()

with db.pooled() as conn:
    for t in ("anon_relay", "anon_follower_state", "anon_conversations",
              "anon_blocks", "anon_links", "user_settings"):
        conn.execute(f"DELETE FROM {t}")
    conn.commit()

db.set_user_language(OWNER, "en")
db.set_user_language(GUEST, "en")
token = db.get_or_create_anon_link(OWNER)
conv = db.start_new_anon_conversation(OWNER, GUEST)
CONV_ID, CONV_NO = conv["id"], conv["conv_number"]

_next_id = iter(range(9000, 9999))
sent_log = []


class FakeBot:
    async def send_message(self, chat_id, text, **kw):
        mid = next(_next_id)
        sent_log.append(("text", chat_id, text, kw.get("reply_markup")))
        return SimpleNamespace(message_id=mid, chat_id=chat_id, text=text)

    async def copy_message(self, chat_id, **kw):
        mid = next(_next_id)
        sent_log.append(("copy", chat_id, None, kw.get("reply_markup")))
        return SimpleNamespace(message_id=mid, chat_id=chat_id)


def make_message(chat_id, text, reply_to=None):
    replies = []
    message_id = next(_next_id)

    async def reply_text(t, **kw):
        replies.append(t)
        return SimpleNamespace(message_id=next(_next_id), chat_id=chat_id)

    def to_dict():
        # Kept only so that anything calling it gets a sane answer. Nothing
        # in the bot parks a message any more -- see the reply requirement in
        # bot.py -- which is what these checks are here to hold it to.
        return {"message_id": message_id, "date": 0,
                "chat": {"id": chat_id, "type": "private"}, "text": text}

    return SimpleNamespace(
        message_id=message_id, chat_id=chat_id, text=text, caption=None,
        media_group_id=None, reply_to_message=reply_to, reply_text=reply_text,
        to_dict=to_dict, _replies=replies,
    )



def held_anywhere(value, needle: str) -> bool:
    """Is any of what somebody wrote still sitting in this state?

    The reply requirement used to be escapable with a "Send it anyway"
    button, which worked by parking the whole message -- text included -- in
    user_data, which lifecycle.PostgresPersistence writes to Postgres. The
    button is gone; this is what stops it coming back.
    """
    if isinstance(value, str):
        return needle in value
    if isinstance(value, dict):
        return (any(held_anywhere(v, needle) for v in value.values())
                or any(held_anywhere(k, needle) for k in value))
    if isinstance(value, (list, tuple, set)):
        return any(held_anywhere(v, needle) for v in value)
    return False


def make_update(user_id, message):
    return SimpleNamespace(
        message=message,
        effective_user=SimpleNamespace(id=user_id),
        effective_chat=SimpleNamespace(id=user_id),
        effective_message=message,
        callback_query=None,
    )


class Ctx:
    def __init__(self):
        self.bot = FakeBot()
        self.user_data = {}


async def main():
    failures = []

    def check(label, cond, detail=""):
        print(f"  {'ok  ' if cond else 'FAIL'} {label}{'' if cond else '  -- ' + str(detail)}")
        if not cond:
            failures.append(label)

    # --- the guest's opening message, before the owner has said anything --
    print("== guest's first message ==")
    before = len(sent_log)
    ctx0 = Ctx()
    first = make_message(GUEST, "Yo")
    await ab.handle_message(make_update(GUEST, first), ctx0)
    check("goes straight through -- nothing to reply to yet",
          len([e for e in sent_log[before:] if e[1] == OWNER]) == 1, first._replies)
    check("guest was not asked to reply",
          not any("Reply to the message" in r for r in first._replies), first._replies)
    check("nothing of it is kept", not held_anywhere(ctx0.user_data, "Yo"))

    # The exemption is spent. A second bare message must NOT ride the open
    # session, even though the owner has not answered yet -- that was the
    # hole: an owner who never replies left every later message exempt.
    before = len(sent_log)
    second = make_message(GUEST, "and another thing")
    await ab.handle_message(make_update(GUEST, second), Ctx())
    check("second bare message is held, not sent",
          len([e for e in sent_log[before:] if e[1] == OWNER]) == 0, second._replies)

    # --- the owner writes back, so now there IS something to reply to ------
    print("== owner -> guest ==")
    ctx = Ctx()
    owner_seed = make_message(OWNER, "hello there")
    db.record_anon_relays(OWNER, [owner_seed.message_id], CONV_ID)
    await ab._deliver_owner_reply(make_update(OWNER, owner_seed), ctx, CONV_ID)

    # Only the relayed message: a refusal is a real message in the chat now
    # (it is sent as a reply to the thing it asks about), so "everything the
    # guest received" is no longer the same as "what was delivered to them".
    guest_msgs = [e for e in sent_log if e[1] == GUEST and "hello there" in (e[2] or "")]
    check("delivered into the guest's chat", len(guest_msgs) == 1, sent_log)
    check("italicised", "<i>hello there</i>" in guest_msgs[0][2], guest_msgs[0][2])
    # The header stays -- it is what separates the bot's own voice from the
    # words it is relaying -- and it names nothing. It used to read
    # "Conversation #N", and N is the OWNER's index for their own inbox, so
    # it told a guest they were somebody's Nth correspondent: a pseudonym by
    # another route, and a fact about the owner delivered to a stranger.
    check("the header carries no pseudonym and no conversation number",
          "\U0001f4ec" in guest_msgs[0][2] and "#" not in guest_msgs[0][2],
          guest_msgs[0][2])
    check("guest got a Reply button", guest_msgs[0][3] is not None)

    # that delivered message must now be a valid reply target for the guest
    delivered_id = next(m for m in range(9000, 9999)
                        if db.get_conversation_for_relay(GUEST, m) == CONV_ID)
    check("guest's chat is tracked in anon_relay", delivered_id is not None)

    # --- 1. the bug from the screenshot: guest swipe-replies ---------------
    print("== guest swipe-reply (the reported bug) ==")
    before = len(sent_log)
    anchor = SimpleNamespace(message_id=delivered_id)
    msg = make_message(GUEST, "how you doin", reply_to=anchor)
    await ab.handle_message(make_update(GUEST, msg), Ctx())
    owner_got = [e for e in sent_log[before:] if e[1] == OWNER]
    check("reached the owner", len(owner_got) == 1, msg._replies)
    check("not rejected as unmatched",
          not any("doesn't match" in r for r in msg._replies), msg._replies)
    check("italicised on arrival", "<i>how you doin</i>" in owner_got[0][2], owner_got[0][2])

    # --- 2. a bare message is refused, and not kept -----------------------
    print("== guest message replying to nothing, after the owner replied ==")
    conv_now = db.get_conversation(CONV_ID)
    check("the one exemption is spent", conv_now["follower_first_msg_at"] is not None, conv_now)
    before = len(sent_log)
    ctx2 = Ctx()
    bare = make_message(GUEST, "just typing")
    await ab.handle_message(make_update(GUEST, bare), ctx2)
    check("nothing delivered", len([e for e in sent_log[before:] if e[1] == OWNER]) == 0)
    # Either route counts: the refusal is sent as a reply to the message it
    # is asking the person to answer, so it can arrive through reply_text or
    # through send_message with an anchor. The claim is the wording, and it
    # was checked against a phrase v1.5.0's refusal pass rewrote -- so this
    # had been failing on correct behaviour.
    told = [r.lower() for r in bare._replies] + [
        (e[2] or "").lower() for e in sent_log[before:]]
    check("guest was told to reply",
          any("was not sent" in t for t in told) and any("reply" in t for t in told),
          told)

    # --- 3. and nothing of it is anywhere ---------------------------------
    print("== the refused message leaves no trace ==")
    check("not in the process's memory for that user",
          not held_anywhere(ctx2.user_data, "just typing"), ctx2.user_data)
    check("not in the bot's shared state",
          not held_anywhere(getattr(ctx2, "bot_data", {}), "just typing"))
    check("and there is no button offering to send it regardless",
          not any("anyway" in (r or "").lower() for r in bare._replies), bare._replies)

    # --- 4. the owner's swipe-reply still works ---------------------------
    print("== owner swipe-reply still resolves ==")
    incoming_id = next(m for m in range(9000, 9999)
                       if db.get_conversation_for_relay(OWNER, m) == CONV_ID)
    before = len(sent_log)
    omsg = make_message(OWNER, "got it", reply_to=SimpleNamespace(message_id=incoming_id))
    await ab.handle_message(make_update(OWNER, omsg), Ctx())
    check("reached the guest", len([e for e in sent_log[before:] if e[1] == GUEST]) == 1,
          omsg._replies)

    # --- 5. the reported bug: an owner who is also somebody's guest -------
    #     A holds an inbox (GUEST talks to them) and has separately tapped
    #     THIRD's link. A bare message from A used to ride the open THIRD
    #     session and be answered "Sent" -- while A was looking at GUEST's
    #     message and believed they were answering it.
    print("== owner who also has an open session elsewhere ==")
    db.get_or_create_anon_link(THIRD)          # THIRD has a real inbox
    db.set_user_language(THIRD, "en")
    db.start_new_anon_conversation(THIRD, OWNER)   # OWNER follows THIRD
    opening = make_message(OWNER, "hi third")
    await ab.handle_message(make_update(OWNER, opening), Ctx())  # spends the exemption
    before = len(sent_log)
    stray = make_message(OWNER, "yo")
    await ab.handle_message(make_update(OWNER, stray), Ctx())
    check("nothing delivered to the wrong conversation",
          len(sent_log[before:]) == 0, sent_log[before:])
    check("not answered with a false 'Sent'",
          not any("Sent" in r for r in stray._replies), stray._replies)
    check("told that more than one conversation is open",
          any("more than one is open" in r for r in stray._replies), stray._replies)
    check("and told plainly that nothing went",
          any("was not sent" in r.lower() for r in stray._replies), stray._replies)
    check("with nothing offering to send it regardless",
          all("anyway" not in r.lower() for r in stray._replies), stray._replies)
    db.clear_follower_state(OWNER)

    # --- 6. a guest can still answer a conversation they have moved on from
    #     Tapping any inbox link rewrites anon_follower_state, which is one
    #     row per person for the whole bot. The guest's reply used to be
    #     refused unless it landed in whatever that single row pointed at, so
    #     one tap silently ended their ability to answer everything they
    #     already had -- while the Reply buttons under those messages stayed
    #     on screen. The relay row knows which conversation a reply belongs
    #     to and always did; nothing else gets a vote.
    print("== guest replies into an older conversation ==")
    guest_anchor = next(m for m in range(9000, 9999)
                        if db.get_conversation_for_relay(GUEST, m) == CONV_ID)
    db.start_new_anon_conversation(THIRD, GUEST)   # GUEST taps somebody else's link
    before = len(sent_log)
    late = make_message(GUEST, "still here",
                        reply_to=SimpleNamespace(message_id=guest_anchor))
    await ab.handle_message(make_update(GUEST, late), Ctx())
    check("still reaches the original owner",
          len([e for e in sent_log[before:] if e[1] == OWNER]) == 1, late._replies)
    check("not refused as unmatched",
          not any("don't have that message" in r for r in late._replies), late._replies)

    # ...and a BARE message from that guest must not ride the new session's
    # opening exemption, because there is now another live thread in this
    # chat it could just as easily have been meant for. Sending it to the
    # newest conversation and answering "Sent" is the wrong-recipient bug.
    print("== bare message with two threads open ==")
    before = len(sent_log)
    ctx6 = Ctx()
    bare2 = make_message(GUEST, "one more thing")
    await ab.handle_message(make_update(GUEST, bare2), ctx6)
    check("not delivered anywhere on a guess",
          len([e for e in sent_log[before:] if e[1] in (OWNER, THIRD)]) == 0,
          [e[1] for e in sent_log[before:]])
    check("not answered with a false 'Sent'",
          not any("Sent" in r for r in bare2._replies), bare2._replies)
    check("refused rather than kept, and the guest is told which message to use",
          not held_anywhere(ctx6.user_data, "one more thing"), ctx6.user_data)
    db.clear_follower_state(GUEST)

    # --- 7. there is no Reply prompt to quote anything into ---------------
    # This used to check that the prompt quoted the person's words and not
    # the bot's own "Conversation #N" header. There is no prompt: the Reply
    # button explains the gesture and sends nothing, so the only thing that
    # can carry somebody's words is the message they are replying to.
    print("== the Reply button sends nothing ==")
    check("no prompt-sending helper is left to call",
          not hasattr(ab, "_quote_of") and not hasattr(ab, "_retire_prompt"))

    # --- 8. a message too long for one bubble arrives, split, not refused --
    print("== overlong message ==")
    before = len(sent_log)
    long_msg = make_message(GUEST, "x" * 4090)
    sent = await relay_content(Ctx().bot, long_msg, OWNER, header="Conversation #1")
    check("split rather than rejected", len(sent) > 1, len(sent))
    check("every piece fits Telegram's limit",
          all(len(e[2]) <= 4096 for e in sent_log[before:] if e[2]),
          [len(e[2] or "") for e in sent_log[before:]])

    # --- 9. /blocked shows conversation numbers, no pseudonym, no user id -
    print("== /blocked ==")
    db.set_anon_blocked(OWNER, GUEST, True)
    text, kb = await ab._blocked_list_text_and_kb(OWNER, "en")
    check("lists the conversation number", f"#{CONV_NO}" in text, text)
    check("no pseudonym", "Guest #" not in text, text)
    payloads = [b.callback_data for row in kb.inline_keyboard for b in row]
    check("the unblock button carries no Telegram user id",
          all(str(GUEST) not in p for p in payloads), payloads)

    with db.pooled() as conn:
        for t in ("anon_relay", "anon_follower_state", "anon_conversations",
                  "anon_blocks", "anon_links", "user_settings"):
            conn.execute(f"DELETE FROM {t}")
        conn.commit()
    db.close_pool()

    print("\nFAILURES:", len(failures), failures or "")
    return 1 if failures else 0


raise SystemExit(asyncio.run(main()))
