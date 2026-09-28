"""A Telegram server that runs inside the test process.

The other suites stub Telegram out one call at a time: `anon_flow.py` builds
a `SimpleNamespace` that looks enough like a `Message` for the handler it is
about to call, and calls that handler directly. That finds logic bugs and it
found several. What it cannot find is anything that lives *between* the
handlers -- handler order, a `pattern=` that does not match, a group that
swallows an update, a reply that anchors to a message id which is correct in
one chat and meaningless in another -- because none of that is exercised
when the test picks the handler itself.

This goes the other way round. `SimBot` is a real `telegram.Bot` with its
one network seam (`_do_post`) redirected into `FakeTelegram`, which keeps
chats, message ids, reply links and inline keyboards the way the real
service does. So the bot's own `main()` builds the real `Application`, real
`Update` objects go in the top, and what comes out the other end is what a
person would actually see in their chat -- in order, with the buttons.

Nothing here is AnonBot-specific; `anon_scenarios.py` is the file that uses
it. Requires python-telegram-bot (the real one, not `tests/stubs`) and a
Postgres for whichever bot is being driven.

The message-id rule this reproduces is the one AnonBot's routing rests on:
ids count up **per chat**, across everyone in it, so the same conversation
has a different id in each of the two chats it appears in. A relay row
written under the wrong chat's id is invisible here in exactly the way it is
invisible in production, which is the point.
"""
from __future__ import annotations

import asyncio
import itertools
import re
from typing import Any

from telegram import Bot, Update
from telegram.error import BadRequest, Forbidden


# Every content type a person can send that is not text, and the smallest
# payload `Message.de_json` accepts for each. They are here rather than in
# the AnonBot suite because the thing they exercise is not AnonBot-specific:
# what a bot can do with a message depends on which of these it is -- a
# sticker cannot carry a caption, a photo can -- and a relay that treats
# them all as photos never meets the difference.
MEDIA_PAYLOADS: dict[str, dict] = {
    "photo": {"photo": [{"file_id": "ph", "file_unique_id": "ph",
                         "width": 90, "height": 90, "file_size": 1}]},
    "sticker": {"sticker": {"file_id": "st", "file_unique_id": "st", "type": "regular",
                            "width": 512, "height": 512,
                            "is_animated": False, "is_video": False}},
    "animation": {"animation": {"file_id": "an", "file_unique_id": "an",
                                "width": 320, "height": 240, "duration": 3}},
    "video": {"video": {"file_id": "vi", "file_unique_id": "vi",
                        "width": 640, "height": 480, "duration": 9}},
    "voice": {"voice": {"file_id": "vo", "file_unique_id": "vo", "duration": 4}},
    "audio": {"audio": {"file_id": "au", "file_unique_id": "au", "duration": 120}},
    "document": {"document": {"file_id": "do", "file_unique_id": "do",
                              "file_name": "notes.txt"}},
    "video_note": {"video_note": {"file_id": "vn", "file_unique_id": "vn",
                                  "length": 240, "duration": 6}},
}
MEDIA_FIELDS = tuple(MEDIA_PAYLOADS)
# A sticker and a video note have nowhere to put words: Telegram rejects a
# caption on either, so a bot with something to say alongside one has to say
# it in a bubble of its own. That is what makes the two chats hold different
# numbers of messages for the same conversation.
UNCAPTIONABLE = ("sticker", "video_note")


# ---------------------------------------------------------------------------
# The server
# ---------------------------------------------------------------------------

class FakeTelegram:
    """Chats, messages and ids. One instance per test run."""

    def __init__(self, bot_id: int = 999_000_001, bot_username: str = "anonbot_test"):
        self.bot_user = {
            "id": bot_id, "is_bot": True, "first_name": "TestBot",
            "username": bot_username, "can_join_groups": False,
            "can_read_all_group_messages": False, "supports_inline_queries": False,
        }
        # chat_id -> [message dict], newest last. Deleted messages are removed.
        self.chats: dict[int, list[dict]] = {}
        self._next_id: dict[int, itertools.count] = {}
        self._update_ids = itertools.count(1)
        self.answers: list[dict] = []      # answerCallbackQuery payloads
        self.deleted: list[tuple[int, int]] = []
        self.blocked_by: set[int] = set()  # chats that answer Forbidden
        self.log: list[dict] = []          # every outbound call, in order

    # -- ids ---------------------------------------------------------------

    def next_message_id(self, chat_id: int) -> int:
        # Per chat, starting at 1, exactly as Telegram numbers them. This is
        # the property the relay table depends on and the one a shared
        # counter would quietly paper over.
        if chat_id not in self._next_id:
            self._next_id[chat_id] = itertools.count(1)
        return next(self._next_id[chat_id])

    def next_update_id(self) -> int:
        return next(self._update_ids)

    # -- storage -----------------------------------------------------------

    def _store(self, chat_id: int, message: dict) -> dict:
        self.chats.setdefault(chat_id, []).append(message)
        return message

    def find(self, chat_id: int, message_id: int) -> dict | None:
        for m in self.chats.get(chat_id, []):
            if m["message_id"] == message_id:
                return m
        return None

    def chat_of(self, chat_id: int) -> list[dict]:
        return self.chats.get(chat_id, [])

    # -- the API -----------------------------------------------------------

    async def api(self, endpoint: str, data: dict) -> Any:
        handler = getattr(self, f"_api_{_snake(endpoint)}", None)
        if handler is None:
            # Anything not implemented answers True rather than exploding:
            # a bot calling setMyDescription should not fail a routing test.
            self.log.append({"endpoint": endpoint, "data": _plain(data)})
            return True
        return handler(data)

    def _api_get_me(self, data):
        return dict(self.bot_user)

    def _api_send_message(self, data):
        chat_id = int(data["chat_id"])
        self._guard(chat_id)
        message = self._new_bot_message(chat_id, data)
        message["text"] = _code_in_place(data.get("text", ""))
        self._attach_reply(message, chat_id, data)
        self._attach_markup(message, data)
        self.log.append({"endpoint": "sendMessage", "chat_id": chat_id,
                         "text": message["text"], "message_id": message["message_id"]})
        return self._store(chat_id, message)

    def _api_copy_message(self, data):
        """copyMessage returns a MessageId, not a Message -- the real one does
        too, and code that assumes otherwise breaks only in production."""
        chat_id = int(data["chat_id"])
        self._guard(chat_id)
        source = self.find(int(data["from_chat_id"]), int(data["message_id"]))
        message = self._new_bot_message(chat_id, data)
        # A copy carries the content across but nothing about where it came
        # from -- which is the whole reason AnonBot copies instead of
        # forwarding.
        carried = False
        for field in MEDIA_FIELDS:
            if source and field in source:
                message[field] = source[field]
                carried = True
        if "caption" in data:
            message["caption"] = data["caption"]
        elif source and "caption" in source:
            message["caption"] = source["caption"]
        if source and "text" in source and not carried:
            message["text"] = source["text"]
        self._attach_reply(message, chat_id, data)
        self._attach_markup(message, data)
        self.log.append({"endpoint": "copyMessage", "chat_id": chat_id,
                         "message_id": message["message_id"],
                         "caption": message.get("caption")})
        self._store(chat_id, message)
        return {"message_id": message["message_id"]}

    def _api_send_document(self, data):
        chat_id = int(data["chat_id"])
        self._guard(chat_id)
        message = self._new_bot_message(chat_id, data)
        message["document"] = {"file_id": "doc", "file_unique_id": "doc",
                               "file_name": str(data.get("filename", "file"))}
        self.log.append({"endpoint": "sendDocument", "chat_id": chat_id})
        return self._store(chat_id, message)

    def _api_delete_message(self, data):
        chat_id, message_id = int(data["chat_id"]), int(data["message_id"])
        existing = self.find(chat_id, message_id)
        if existing is None:
            raise BadRequest("Message to delete not found")
        self.chats[chat_id].remove(existing)
        self.deleted.append((chat_id, message_id))
        self.log.append({"endpoint": "deleteMessage", "chat_id": chat_id,
                         "message_id": message_id})
        return True

    def _api_edit_message_text(self, data):
        chat_id, message_id = int(data["chat_id"]), int(data["message_id"])
        message = self.find(chat_id, message_id)
        if message is None:
            raise BadRequest("Message to edit not found")
        if message.get("text") == data.get("text") and _markup(data) == message.get("reply_markup"):
            raise BadRequest("Message is not modified")
        message["text"] = _code_in_place(data.get("text", ""))
        # An edit with no reply_markup takes the keyboard away, which is what
        # the real editMessageText does -- keeping it would let a test pass
        # with buttons still on screen that the user can no longer see.
        message.pop("reply_markup", None)
        self._attach_markup(message, data)
        self.log.append({"endpoint": "editMessageText", "chat_id": chat_id,
                         "message_id": message_id, "text": message["text"]})
        return message

    def _api_edit_message_reply_markup(self, data):
        chat_id, message_id = int(data["chat_id"]), int(data["message_id"])
        message = self.find(chat_id, message_id)
        if message is None:
            raise BadRequest("Message to edit not found")
        new = _markup(data)
        if new == message.get("reply_markup"):
            raise BadRequest("Message is not modified")
        if new is None:
            message.pop("reply_markup", None)
        else:
            message["reply_markup"] = new
        self.log.append({"endpoint": "editMessageReplyMarkup", "chat_id": chat_id,
                         "message_id": message_id})
        return message

    def _api_answer_callback_query(self, data):
        self.answers.append(_plain(data))
        return True

    def _api_set_my_commands(self, data):
        return True

    # -- helpers -----------------------------------------------------------

    def _guard(self, chat_id: int) -> None:
        if chat_id in self.blocked_by:
            raise Forbidden("Forbidden: bot was blocked by the user")

    def _new_bot_message(self, chat_id: int, data: dict) -> dict:
        return {
            "message_id": self.next_message_id(chat_id),
            "date": 1_700_000_000,
            "chat": {"id": chat_id, "type": "private"},
            "from": dict(self.bot_user),
        }

    def _attach_reply(self, message: dict, chat_id: int, data: dict) -> None:
        anchor = _reply_target(data)
        if anchor is None:
            return
        target = self.find(chat_id, anchor)
        if target is None:
            # Telegram's own answer when the message being replied to is
            # gone. relay_content() retries without the anchor; that retry is
            # only ever exercised if this raises the way the real one does.
            raise BadRequest("Bad Request: message to be replied not found")
        message["reply_to_message"] = {k: v for k, v in target.items()
                                       if k != "reply_to_message"}

    def _attach_markup(self, message: dict, data: dict) -> None:
        markup = _markup(data)
        if markup is not None:
            message["reply_markup"] = markup


def sim_bot(tele: FakeTelegram, token: str = "1:AAtest") -> Bot:
    """A real Bot whose only network call goes to FakeTelegram instead.

    The server is closed over rather than stored on the instance: a
    `TelegramObject` freezes itself at the end of `__init__` and defines
    `__slots__`, so there is nowhere on a Bot to put one.
    """

    class SimBot(Bot):
        async def _do_post(self, endpoint, data, **kwargs):
            return await tele.api(endpoint, data)

    return SimBot(token)


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------

_CODE_ANYWHERE = re.compile("🆔 ([A-Z]{2}(?:-[A-Z0-9]+)+)")


def _code_in_place(text: str) -> str:
    """A problem code belongs on a message's last line, where the Report
    button looks for it (problems.py). One anywhere else means something was
    added after i18n.t() had already ended the message, so it is refused here
    and the suite that sent it fails on the spot."""
    found = _CODE_ANYWHERE.findall(text or "")
    if found:
        import problems  # from the bot folder the suite put on sys.path
        if len(found) > 1 or problems.find_code(text) != found[0]:
            raise AssertionError(f"a problem code is not on the last line: {text!r}")
    return text


def _snake(name: str) -> str:
    out = []
    for ch in name:
        if ch.isupper():
            out.append("_")
            out.append(ch.lower())
        else:
            out.append(ch)
    return "".join(out).lstrip("_")


def _plain(value):
    """Whatever `_do_post` was handed, as JSON-ish Python."""
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return value


def _markup(data: dict):
    """The inline keyboard on a send, or None.

    Only an inline keyboard becomes part of the resulting `Message`. A
    `ForceReply` or a `ReplyKeyboardMarkup` is an instruction to the sender's
    own client and never comes back in the message object -- storing one here
    made `Message.de_json` fail on a payload the real API cannot produce.
    """
    markup = data.get("reply_markup")
    if markup is None:
        return None
    plain = _plain(markup)
    if isinstance(plain, dict) and "inline_keyboard" in plain:
        return plain
    return None


def _reply_target(data: dict) -> int | None:
    """The message id this send is anchored to, however it was expressed.

    PTB moved from `reply_to_message_id` to `reply_parameters` and both are
    still in play depending on which call path a handler used, so the fake
    server has to understand both -- otherwise a reply that works in
    production silently arrives unanchored here and the test passes on a
    behaviour the user never gets.
    """
    if data.get("reply_to_message_id") is not None:
        return int(data["reply_to_message_id"])
    params = data.get("reply_parameters")
    if params is None:
        return None
    params = _plain(params)
    target = params.get("message_id")
    return int(target) if target is not None else None


# ---------------------------------------------------------------------------
# The people
# ---------------------------------------------------------------------------

class Person:
    """One Telegram user, and their private chat with the bot.

    chat_id == user_id, which is what a private chat means, and is also why
    a relay row keyed on the wrong one of the two still looks right in a
    test that invents its own ids.
    """

    def __init__(self, sim: "Sim", user_id: int, name: str):
        self.sim = sim
        self.id = user_id
        self.name = name

    # -- what they say -----------------------------------------------------

    async def send(self, text: str, reply_to: int | None = None,
                   media_group_id: str | None = None, kind: str = "text",
                   caption: str | None = None) -> int:
        """Type something. Returns the message id in this person's chat."""
        return await self.sim.incoming(
            self, text=text, reply_to=reply_to, media_group_id=media_group_id,
            kind=kind, caption=caption,
        )

    async def photo(self, caption: str | None = None, reply_to: int | None = None,
                    media_group_id: str | None = None) -> int:
        return await self.media("photo", caption=caption, reply_to=reply_to,
                                media_group_id=media_group_id)

    async def media(self, kind: str, caption: str | None = None,
                    reply_to: int | None = None,
                    media_group_id: str | None = None) -> int:
        """Send one of sim.MEDIA_PAYLOADS -- a sticker, a gif, a voice note.

        Worth having as its own verb because the bot's behaviour genuinely
        differs by kind: what can carry a caption decides how many bubbles
        one relayed message becomes, and therefore whether the two chats
        stay in step."""
        return await self.sim.incoming(
            self, text=None, reply_to=reply_to, media_group_id=media_group_id,
            kind=kind, caption=caption,
        )

    async def album(self, count: int, caption: str | None = None,
                    reply_to: int | None = None) -> list[int]:
        """An album is several updates sharing a media_group_id -- Telegram
        has no single 'album' message, and treating it as one is how a
        three-photo send turns into three of everything."""
        group = f"grp{self.sim.next_group()}"
        ids = []
        for n in range(count):
            ids.append(await self.photo(
                caption=caption if n == 0 else None,
                reply_to=reply_to, media_group_id=group,
            ))
        return ids

    async def edit(self, message_id: int, text: str) -> None:
        await self.sim.incoming_edit(self, message_id, text)

    async def tap(self, label_contains: str, message_id: int | None = None) -> None:
        """Tap the button whose label contains this text, on the newest
        message in this chat that has one (or on `message_id`)."""
        await self.sim.tap(self, label_contains, message_id)

    async def tap_data(self, callback_data: str, message_id: int | None = None) -> None:
        await self.sim.tap(self, None, message_id, callback_data=callback_data)

    # -- what they see -----------------------------------------------------

    @property
    def chat(self) -> list[dict]:
        return self.sim.tele.chat_of(self.id)

    def last(self, n: int = 1) -> list[str]:
        return [_body(m) for m in self.chat[-n:]]

    def texts(self) -> list[str]:
        return [_body(m) for m in self.chat]

    def buttons(self, message_id: int | None = None) -> list[str]:
        message = (self.sim.tele.find(self.id, message_id) if message_id
                   else self._last_with_buttons())
        # A named message with no keyboard is the ordinary case for "has this
        # menu been taken away yet", so it answers [] rather than raising.
        # Only _last_with_buttons() guarantees a reply_markup is there.
        markup = (message or {}).get("reply_markup") or {}
        return [b["text"] for row in markup.get("inline_keyboard", []) for b in row]

    def _last_with_buttons(self) -> dict | None:
        for m in reversed(self.chat):
            if m.get("reply_markup", {}).get("inline_keyboard"):
                return m
        return None

    def said(self, needle: str) -> bool:
        return any(needle.lower() in (t or "").lower() for t in self.texts())

    def transcript(self) -> str:
        lines = []
        for m in self.chat:
            who = "bot" if m.get("from", {}).get("is_bot") else self.name
            body = " ".join((_body(m) or f"[{kind_of(m) or 'media'}]").split())
            if len(body) > 100:
                body = body[:99] + "…"
            anchor = m.get("reply_to_message", {}).get("message_id")
            tail = f"  ↳ re #{anchor}" if anchor else ""
            btns = ""
            if m.get("reply_markup"):
                names = [b["text"] for row in m["reply_markup"]["inline_keyboard"] for b in row]
                btns = "  [" + " | ".join(names) + "]"
            lines.append(f"  #{m['message_id']:<3} {who:<8} {body}{tail}{btns}")
        return "\n".join(lines)


def _body(message: dict) -> str | None:
    return message.get("text") if message.get("text") is not None else message.get("caption")


def kind_of(message: dict) -> str | None:
    """Which content type this message carries, or None for plain text.

    Named rather than private because a test asserting that a sticker
    arrived as a sticker should not have to know the field names."""
    for field in MEDIA_FIELDS:
        if field in message:
            return field
    return None


# ---------------------------------------------------------------------------
# The driver
# ---------------------------------------------------------------------------

class Sim:
    """Builds the bot's real Application and pushes real Updates through it."""

    def __init__(self, app, tele: FakeTelegram):
        self.app = app
        self.tele = tele
        self.people: dict[int, Person] = {}
        self._groups = itertools.count(1)

    def next_group(self) -> int:
        return next(self._groups)

    def person(self, user_id: int, name: str) -> Person:
        person = Person(self, user_id, name)
        self.people[user_id] = person
        return person

    # -- updates -----------------------------------------------------------

    def _user(self, person: Person) -> dict:
        return {"id": person.id, "is_bot": False, "first_name": person.name,
                "language_code": "en"}

    async def incoming(self, person: Person, text: str | None, reply_to: int | None,
                       media_group_id: str | None, kind: str,
                       caption: str | None) -> int:
        message = {
            "message_id": self.tele.next_message_id(person.id),
            "date": 1_700_000_000,
            "chat": {"id": person.id, "type": "private"},
            "from": self._user(person),
        }
        if kind == "text":
            message["text"] = text
            if text and text.startswith("/"):
                command = text.split()[0]
                message["entities"] = [
                    {"type": "bot_command", "offset": 0, "length": len(command)}
                ]
        else:
            if kind not in MEDIA_PAYLOADS:
                raise AssertionError(
                    f"no payload for a {kind!r}; add one to sim.MEDIA_PAYLOADS")
            message.update(MEDIA_PAYLOADS[kind])
            if caption is not None:
                if kind in UNCAPTIONABLE:
                    raise AssertionError(f"Telegram does not allow a caption on a {kind}")
                message["caption"] = caption
        if media_group_id:
            message["media_group_id"] = media_group_id
        if reply_to is not None:
            target = self.tele.find(person.id, reply_to)
            if target is None:
                raise AssertionError(
                    f"{person.name} tried to reply to #{reply_to}, which is not in their chat"
                )
            message["reply_to_message"] = {k: v for k, v in target.items()
                                           if k != "reply_to_message"}
        self.tele._store(person.id, message)
        await self._process({"update_id": self.tele.next_update_id(), "message": message})
        return message["message_id"]

    async def incoming_edit(self, person: Person, message_id: int, text: str) -> None:
        message = self.tele.find(person.id, message_id)
        if message is None:
            raise AssertionError(f"no message #{message_id} in {person.name}'s chat")
        message["text"] = text
        await self._process({"update_id": self.tele.next_update_id(),
                             "edited_message": message})

    async def tap(self, person: Person, label_contains: str | None,
                  message_id: int | None = None, callback_data: str | None = None) -> None:
        message = (self.tele.find(person.id, message_id) if message_id
                   else person._last_with_buttons())
        if message is None and callback_data is not None:
            # A forged tap. A hostile client can send any callback_data it
            # likes against any message it can see, so the test has to be
            # able to do the same -- there does not have to be a button.
            message = person.chat[-1] if person.chat else None
        if message is None:
            raise AssertionError(f"nothing with buttons in {person.name}'s chat")
        if callback_data is None:
            for row in message.get("reply_markup", {}).get("inline_keyboard", []):
                for button in row:
                    if label_contains.lower() in button["text"].lower():
                        callback_data = button["callback_data"]
                        break
                if callback_data:
                    break
        if callback_data is None:
            raise AssertionError(
                f'no button matching "{label_contains}" on #{message["message_id"]} '
                f'in {person.name}\'s chat; buttons are {person.buttons(message["message_id"])}'
            )
        await self._process({
            "update_id": self.tele.next_update_id(),
            "callback_query": {
                "id": str(self.tele.next_update_id()),
                "from": self._user(person),
                "chat_instance": "1",
                "message": message,
                "data": callback_data,
            },
        })

    async def _process(self, update_dict: dict) -> None:
        update = Update.de_json(update_dict, self.app.bot)
        await self.app.process_update(update)
        # process_update dispatches synchronously unless a handler was marked
        # blocking=False; give anything that was a turn to finish so an
        # assertion right after this call sees a settled chat.
        await asyncio.sleep(0)


async def build(module, tele: FakeTelegram):
    """Run a bot module's real main() with the network swapped out.

    `main()` ends in run_polling, so that is the one thing replaced -- every
    handler registration, every job, every attach above it runs for real.
    """
    from telegram.ext import Application, ApplicationBuilder

    bot = sim_bot(tele)
    captured: dict = {}

    real_token = ApplicationBuilder.token
    real_run = Application.run_polling

    def fake_token(self, token):          # noqa: ARG001 - signature must match
        return self.bot(bot)

    def fake_run_polling(self, *args, **kwargs):
        captured["app"] = self
        captured["kwargs"] = kwargs

    ApplicationBuilder.token = fake_token
    Application.run_polling = fake_run_polling
    try:
        module.main()
    finally:
        ApplicationBuilder.token = real_token
        Application.run_polling = real_run

    app = captured["app"]
    await app.initialize()
    return Sim(app, tele), captured["kwargs"]
