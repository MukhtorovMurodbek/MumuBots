"""What survives a redeploy, and what is deliberately dropped."""
import asyncio, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".mirror", "sticker_bot"))
sys.path.insert(0, os.path.join(HERE, "stubs"))
os.environ["DATABASE_URL"] = "postgresql://stub"

import lifecycle

failures = []
def equals(name, got, want):
    """Named for what it does, because the rest of this suite spells an
    assertion check(name, ok, detail) -- and a comparison written in that
    shape by mistake passes whenever `got` is truthy, which is a test that
    reports success without testing anything."""
    if got != want: failures.append(f"{name}: got {got!r}, want {want!r}")
    else: print(f"  ok  {name}")

# ---- a stand-in for the table ----
TABLE = {}
def fake_write(rows):
    for kind, key, value in rows:
        TABLE[(kind, key)] = value
def fake_delete(rows):
    for row in rows:
        TABLE.pop(tuple(row), None)
def fake_read(kind):
    return [(k[1], v) for k, v in TABLE.items() if k[0] == kind]
lifecycle._write_state = fake_write
lifecycle._delete_state = fake_delete
lifecycle._read_state = fake_read


class NotJson:
    """Stands in for a live telegram.Message object -- the sort of thing that
    cannot be written to the store and is dropped rather than fatal."""


async def main():
    # ---- what _clean keeps and what it refuses ----
    kept = lifecycle._clean({
        "lang": "uz", "sticker_count": 3, "pack_created": True,
        "_last_seen": 1756742400.5, "title": "Ali's pack",
        "status": {"chat_id": 7, "message_id": 42, "watermark": 41},
        "nothing": None,
    })
    equals("plain values are kept", kept, {
        "lang": "uz", "sticker_count": 3, "pack_created": True,
        "_last_seen": 1756742400.5, "title": "Ali's pack",
        "status": {"chat_id": 7, "message_id": 42, "watermark": 41},
        "nothing": None,
    })

    dropped = lifecycle._clean({"lang": "en", "held": NotJson()})
    equals("an object that is not JSON is dropped, not fatal", dropped, {"lang": "en"})

    equals("a tuple is refused rather than turned into a list",
          lifecycle._json_safe(("a", 1)), None)
    equals("a dict with int keys is refused (JSON would stringify them)",
          lifecycle._json_safe({1: "a"}), None)
    equals("a Path-like object is refused", lifecycle._json_safe(object()), None)

    # ---- AnonBot's held message: the one dropped value a user could see ----
    # A guest whose message named no conversation is offered "send it anyway",
    # and the message itself is parked until they tap. Parked as a (Message,
    # int) tuple it was refused twice over -- tuple and Message -- so a tap
    # that crossed a redeploy answered "that message expired" on a button the
    # user had been looking at seconds earlier. AnonBot stores the Message's
    # own to_dict() now, which is plain JSON and comes back through de_json().
    equals("the tuple AnonBot used to park would not have survived",
          lifecycle._json_safe((NotJson(), 5)), None)
    held = {
        "message": {
            "message_id": 9, "date": 1756742400,
            "chat": {"id": 7, "type": "private"},
            "from": {"id": 7, "is_bot": False, "first_name": "A"},
            "text": "is this thing on?",
        },
        "conversation_id": 5,
    }
    equals("the dict it parks now survives the store intact",
          lifecycle._json_safe(held), held)

    # ---- a round trip through the store ----
    TABLE.clear()
    p = lifecycle.PostgresPersistence()
    await p.update_user_data(111, {"lang": "ru", "sticker_count": 2, "held": NotJson()})
    await p.update_user_data(222, {"lang": "en"})
    await p.update_conversation("stickerbot_pack_editing", (5, 111), 1)
    await p.flush()

    equals("one row per user", sorted(k for k in TABLE if k[0] == "user"),
          [("user", "111"), ("user", "222")])
    equals("the unserialisable value did not stop the rest",
          TABLE[("user", "111")], {"lang": "ru", "sticker_count": 2})
    equals("the open conversation was written",
          TABLE[("conversation", "stickerbot_pack_editing|5:111")], {"state": 1})

    # ---- what the next process sees ----
    fresh = lifecycle.PostgresPersistence()
    equals("user state comes back keyed by int id",
          await fresh.get_user_data(),
          {111: {"lang": "ru", "sticker_count": 2}, 222: {"lang": "en"}})
    equals("the conversation comes back with the right key shape",
          await fresh.get_conversations("stickerbot_pack_editing"), {(5, 111): 1})
    equals("a handler with no saved conversations gets an empty dict",
          await fresh.get_conversations("something_else"), {})

    # ---- ending a conversation removes it ----
    await p.update_conversation("stickerbot_pack_editing", (5, 111), None)
    equals("a finished conversation is deleted, not left open",
          ("conversation", "stickerbot_pack_editing|5:111") in TABLE, False)

    # ---- a user whose state empties out loses their row ----
    await p.update_user_data(222, {})
    await p.flush()
    equals("an emptied user_data deletes the row rather than storing {}",
          ("user", "222") in TABLE, False)

    # ---- and one that is dropped from memory ----
    await p.drop_user_data(111)
    await p.flush()
    equals("dropping a user drops their row", ("user", "111") in TABLE, False)

    # ---- a write that fails is tried again, not forgotten ----
    # flush() used to clear its list of who had changed *before* writing, so a
    # database that was down for one pass lost every waiting user's saved
    # state until they next did something -- and the ones most worth keeping
    # are the ones who are waiting on a button.
    TABLE.clear()
    s = lifecycle.PostgresPersistence()
    await s.update_user_data(301, {"lang": "uz", "step": "format"})
    await s.update_user_data(302, {"lang": "en"})
    real_write = lifecycle._write_state

    def failing_write(rows):
        raise RuntimeError("the database is away")
    lifecycle._write_state = failing_write
    await s.flush()
    lifecycle._write_state = real_write
    equals("nothing was saved while the database was away", sorted(k for k in TABLE if k[0] == "user"), [])
    await s.flush()
    equals("the next pass saved them, though neither had changed since",
          sorted(k for k in TABLE if k[0] == "user"), [("user", "301"), ("user", "302")])
    equals("with what they had", TABLE.get(("user", "301")), {"lang": "uz", "step": "format"})

    # a user who changed again, or was dropped, while the write was failing keeps that
    TABLE.clear()
    t = lifecycle.PostgresPersistence()
    await t.update_user_data(401, {"lang": "en", "step": "one"})
    await t.update_user_data(402, {"lang": "en"})

    def failing_write_meanwhile(rows):
        raise RuntimeError("the database is away")
    lifecycle._write_state = failing_write_meanwhile
    flush = asyncio.ensure_future(t.flush())
    await flush
    lifecycle._write_state = real_write
    await t.drop_user_data(402)                       # they were dropped after the failed pass
    await t.update_user_data(401, {"lang": "en", "step": "two"})
    await t.flush()
    equals("a user who changed again is saved as they are now, once",
          TABLE.get(("user", "401")), {"lang": "en", "step": "two"})
    equals("a user who was dropped is not brought back by the retry", ("user", "402") in TABLE, False)

    # ---- a conversation key of a different arity still round-trips ----
    TABLE.clear()
    q = lifecycle.PostgresPersistence()
    await q.update_conversation("per_user_only", (777,), "WAITING")
    r = lifecycle.PostgresPersistence()
    equals("a one-part conversation key keeps its shape",
          await r.get_conversations("per_user_only"), {(777,): "WAITING"})

    # ---- the advisory-lock key is stable and fits in a signed bigint ----
    key = lifecycle._lease_key("stickerbot")
    equals("the lease key is stable", key, lifecycle._lease_key("stickerbot"))
    equals("different bots get different keys",
          key != lifecycle._lease_key("convertbot"), True)
    equals("the lease key fits a signed bigint", -2**63 <= key < 2**63, True)

    # ---- busy() tracks and releases ----
    equals("nothing in flight to start with", len(lifecycle._in_flight), 0)
    async with lifecycle.busy(9, "send it again"):
        equals("in-flight work is visible while it runs",
              list(lifecycle._in_flight.values()), [(9, "send it again")])
    equals("and gone once it finishes", len(lifecycle._in_flight), 0)

    try:
        async with lifecycle.busy(9, "a download that blows up"):
            raise ValueError("boom")
    except ValueError:
        pass
    equals("work that raised is not left marked as in flight", len(lifecycle._in_flight), 0)

    # ---- who gets told about a restart ----
    # The message is handed in already translated: lifecycle.py is
    # byte-identical in five bots and has no business reaching for i18n.
    told = []
    class Bot:
        async def send_message(self, chat_id, text): told.append((chat_id, text))
    async with lifecycle.busy(42, "🔄 Сейчас обновляюсь — отправьте ещё раз."):
        await lifecycle._tell_the_interrupted(Bot())
    equals("the interrupted user is told once", len(told), 1)
    equals("in the chat that was waiting", told[0][0], 42)
    equals("in their own language, verbatim",
          told[0][1], "🔄 Сейчас обновляюсь — отправьте ещё раз.")

    told.clear()
    await lifecycle._tell_the_interrupted(Bot())
    equals("and nobody is told when nothing was in flight", told, [])

    print()
    if failures:
        print("FAILURES:")
        for f in failures: print("  -", f)
        return 1
    print("all state/lifecycle checks passed")
    return 0

sys.exit(asyncio.get_event_loop().run_until_complete(main()))
