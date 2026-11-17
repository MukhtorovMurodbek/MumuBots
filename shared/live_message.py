from __future__ import annotations

import logging
import re
from collections import OrderedDict

from telegram.error import BadRequest

logger = logging.getLogger(__name__)

_CONTENT_REFUSED = re.compile(
    r"button_data_invalid|button_url_invalid|can't parse entities|message is too long|"
    r"text must be non-empty|reply_markup_too_long|entities_too_long", re.IGNORECASE)

MAX_TRACKED_CHATS = 2048

_last_incoming: "OrderedDict[int, int]" = OrderedDict()

_SEND_ONLY_KWARGS = frozenset({
    "do_quote", "reply_to_message_id", "allow_sending_without_reply",
    "reply_parameters", "disable_notification", "protect_content",
    "message_effect_id", "allow_paid_broadcast", "message_thread_id",
})


def note_incoming(chat_id: int, message_id: int) -> None:
    current = _last_incoming.get(chat_id, 0)
    if message_id > current:
        _last_incoming[chat_id] = message_id
    _last_incoming.move_to_end(chat_id)
    while len(_last_incoming) > MAX_TRACKED_CHATS:
        _last_incoming.popitem(last=False)


def note_update(update) -> None:
    message = getattr(update, "message", None) or getattr(update, "edited_message", None)
    if message is not None:
        note_incoming(message.chat_id, message.message_id)


def bump(chat_id: int, message_id: int) -> None:
    note_incoming(chat_id, message_id)


def _edit_kwargs(kwargs: dict) -> dict:
    return {k: v for k, v in kwargs.items() if k not in _SEND_ONLY_KWARGS}


class LiveMessage:
    __slots__ = ("chat_id", "message_id", "watermark")

    def __init__(self, chat_id: int, message_id: int, watermark: int | None = None):
        self.chat_id = chat_id
        self.message_id = message_id
        self.watermark = _last_incoming.get(chat_id, 0) if watermark is None else watermark

    def _still_last(self) -> bool:
        return self.message_id >= max(self.watermark, _last_incoming.get(self.chat_id, 0))


    @classmethod
    async def send(cls, bot, chat_id: int, text: str, **kwargs) -> "LiveMessage":
        message = await bot.send_message(chat_id=chat_id, text=text, **kwargs)
        return cls(chat_id, message.message_id)

    @classmethod
    async def reply_to(cls, message, text: str, **kwargs) -> "LiveMessage":
        sent = await message.reply_text(text, **kwargs)
        return cls(sent.chat_id, sent.message_id)

    @classmethod
    def adopt(cls, message) -> "LiveMessage":
        return cls(message.chat_id, message.message_id)


    def save(self) -> dict:
        return {"chat_id": self.chat_id, "message_id": self.message_id,
                "watermark": self.watermark}

    @classmethod
    def restore(cls, state) -> "LiveMessage | None":
        if not isinstance(state, dict):
            return None
        try:
            chat_id = int(state["chat_id"])
            message_id = int(state["message_id"])
        except (KeyError, TypeError, ValueError):
            return None
        stored = state.get("watermark") or 0
        return cls(chat_id, message_id, max(int(stored), _last_incoming.get(chat_id, 0)))


    async def set(self, bot, text: str, **kwargs) -> "LiveMessage":
        if self._still_last():
            try:
                await bot.edit_message_text(
                    chat_id=self.chat_id, message_id=self.message_id, text=text,
                    **_edit_kwargs(kwargs),
                )
                self.watermark = _last_incoming.get(self.chat_id, 0)
                return self
            except BadRequest as exc:
                lowered = str(exc).lower()
                if "not modified" in lowered:
                    return self
                if "not found" not in lowered and "can't be edited" not in lowered:
                    logger.debug("Live message edit refused (%s); sending a new one", exc)
            except Exception:
                logger.debug("Live message edit failed; sending a new one", exc_info=True)

        try:
            sent = await bot.send_message(chat_id=self.chat_id, text=text, **kwargs)
        except BadRequest as exc:
            if _CONTENT_REFUSED.search(str(exc)):
                raise
            logger.warning("Could not send a live message: %s", exc)
            return self
        except Exception:
            logger.warning("Could not send a live message", exc_info=True)
            return self
        self.message_id = sent.message_id
        self.watermark = _last_incoming.get(self.chat_id, 0)
        return self

    async def finish(self, bot, text: str, **kwargs) -> None:
        await self.set(bot, text, **kwargs)

    async def delete(self, bot) -> None:
        try:
            await bot.delete_message(chat_id=self.chat_id, message_id=self.message_id)
        except Exception:
            logger.debug("Could not delete a live message", exc_info=True)


async def edit_in_place(message, bot, text: str, **kwargs) -> "LiveMessage":
    return await LiveMessage.adopt(message).set(bot, text, **kwargs)


DEFAULT_KEY = "live_message"


async def show(context, chat_id: int, text: str, key: str = DEFAULT_KEY, **kwargs) -> "LiveMessage":
    live = LiveMessage.restore(context.user_data.get(key))
    if live is None:
        live = await LiveMessage.send(context.bot, chat_id, text, **kwargs)
    else:
        await live.set(context.bot, text, **kwargs)
    context.user_data[key] = live.save()
    return live


def drop(context, key: str = DEFAULT_KEY) -> None:
    context.user_data.pop(key, None)
