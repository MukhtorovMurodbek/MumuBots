"""What adding a sticker and retagging one take, for the private chat (bot.py) and groups
(group_mode.py) alike.

Since 2.2.0 every sticker added gets a short reply of its own ("✅ Added as #4 with 😭"),
and its emoji can be changed later by replying to it with emoji. The owner asked for both
on 2026-10-28: several pictures sent quickly were all added, but the one status message
the bot kept updating read as if only one had been, and "reply to the sent sticker, or to
the reply to that added sticker, with the right emoji" is how a person points at one.

A reply has to say which sticker it means, and the two messages it can point at say it
differently:
  * a sticker message names its set and carries the sticker's file id itself, so that
    always works, for any sticker in a pack made here, however old;
  * the picture the person sent, or the bot's "Added" reply to it, says nothing about the
    sticker made from it. Those are remembered here, in memory: (chat, message) -> (pack,
    sticker), for the last REFS_MAX additions. After a restart only the sticker itself
    can be replied to.
"""
import asyncio
import secrets
import time
from collections import OrderedDict

from telegram import InputSticker
from telegram.error import TimedOut

import i18n
import problems
from emoji_utils import DEFAULT_EMOJI
from image_utils import to_sticker_png
from video_sticker import to_video_sticker_webm

REFS_MAX = 20000
_refs: "OrderedDict[tuple[int, int], tuple[str, str]]" = OrderedDict()


def remember(chat_id: int, message_id: int, pack_name: str, file_id: str) -> None:
    key = (chat_id, message_id)
    _refs[key] = (pack_name, file_id)
    _refs.move_to_end(key)
    while len(_refs) > REFS_MAX:
        _refs.popitem(last=False)


def sticker_of(message) -> "tuple[str, str] | None":
    """(pack name, sticker file id) that `message` stands for, or None."""
    if message is None:
        return None
    sticker = getattr(message, "sticker", None)
    if sticker is not None and sticker.set_name:
        return sticker.set_name, sticker.file_id
    return _refs.get((message.chat_id, message.message_id))


async def in_pack(bot, pack_name: "str | None", sticker) -> bool:
    """True if `sticker` is in pack `pack_name` right now.

    `sticker.set_name` alone isn't enough to decide this: it reflects whatever set the
    message's *sender* last saw the sticker belong to, and a sender's sticker panel can lag
    behind a removal the bot itself just made (remove it, then re-send the same sticker from
    the panel before Telegram's client has refreshed it). So once set_name points at the
    pack, it is checked against the pack's actual current contents."""
    if not pack_name or sticker is None or sticker.set_name != pack_name:
        return False
    try:
        sticker_set = await bot.get_sticker_set(pack_name)
    except Exception:
        return True  # can't verify right now -- fall back to trusting set_name
    return any(s.file_unique_id == sticker.file_unique_id for s in sticker_set.stickers)


# "Add it again?" questions waiting for a tap (group_mode.ask_add_again), in memory only: the
# button carries a token, because a sticker's file id does not fit in a button's 64 bytes.
# Gone on a restart, after ASKED_SECONDS, or past ASKED_MAX waiting at once; the button then
# says the question has expired.
ASKED_MAX = 1000
ASKED_SECONDS = 3600
_asked: "OrderedDict[str, tuple[float, dict]]" = OrderedDict()


def hold(question: dict) -> str:
    now = time.monotonic()
    while _asked and now - next(iter(_asked.values()))[0] > ASKED_SECONDS:
        _asked.popitem(last=False)
    token = secrets.token_hex(5)
    _asked[token] = (now, question)
    while len(_asked) > ASKED_MAX:
        _asked.popitem(last=False)
    return token


def held(token: str) -> "dict | None":
    found = _asked.get(token)
    if found is None or time.monotonic() - found[0] > ASKED_SECONDS:
        return None
    return found[1]


def release(token: str) -> None:
    _asked.pop(token, None)


class Unsupported(Exception):
    """The message holds nothing this bot can turn into a sticker."""


def media_of(message) -> "tuple[str, str] | None":
    """("static" | "video", file id) of what in `message` can become a sticker, or None.
    Animated (Lottie/.tgs) stickers are not supported anywhere in the family."""
    sticker = getattr(message, "sticker", None)
    if sticker is not None:
        if sticker.is_animated:
            return None
        return ("video" if sticker.is_video else "static"), sticker.file_id
    if message.photo:
        return "static", message.photo[-1].file_id
    if message.animation is not None:
        return "video", message.animation.file_id
    if message.video is not None:
        return "video", message.video.file_id
    document = message.document
    if document is not None:
        mime = (document.mime_type or "").lower()
        if mime.startswith("image/"):
            return "static", document.file_id
        if mime.startswith("video/"):
            return "video", document.file_id
    return None


async def download(bot, file_id: str) -> bytes:
    tg_file = await bot.get_file(file_id)
    return bytes(await tg_file.download_as_bytearray())


async def static_sticker(raw: bytes) -> InputSticker:
    # Pillow is CPU work: on the event loop it would hold up everybody's updates.
    png = (await asyncio.to_thread(to_sticker_png, raw)).getvalue()
    return InputSticker(sticker=png, emoji_list=[DEFAULT_EMOJI], format="static")


async def video_sticker(raw: bytes, lang: str) -> InputSticker:
    """Raises video_sticker.ConversionError when the clip cannot fit Telegram's limits."""
    webm = await asyncio.to_thread(to_video_sticker_webm, raw, lang)
    return InputSticker(sticker=webm, emoji_list=[DEFAULT_EMOJI], format="video")


async def add_to_pack(bot, owner_id: int, pack_name: str, input_sticker: InputSticker,
                      want_file_id: bool = True) -> "tuple[str | None, int | None]":
    """Adds to an existing pack, on its owner's behalf (Telegram ties a set to the user it
    was created for, whoever is sending). Returns (the new sticker's file id, its place in
    the pack), or (None, None) with want_file_id=False: the file id costs a getStickerSet,
    which a bulk import does not need for every one of a hundred stickers."""
    # Uploading a converted video sticker can take longer than python-telegram-bot's
    # default 5 seconds, which showed as a "Timed out" on an upload that went through.
    await bot.add_sticker_to_set(user_id=owner_id, name=pack_name, sticker=input_sticker,
                                 read_timeout=60, write_timeout=60)
    if not want_file_id:
        return None, None
    return await last_sticker(bot, pack_name)


async def last_sticker(bot, pack_name: str) -> "tuple[str, int]":
    sticker_set = await bot.get_sticker_set(pack_name)
    return sticker_set.stickers[-1].file_id, len(sticker_set.stickers)


# ---------- what a failed add says ----------

def error_key(exc: Exception) -> tuple:
    """Which explanation a raw Telegram error from set-creation/add calls
    gets, as (i18n key, fields)."""
    if isinstance(exc, TimedOut):
        # Not a rejection -- Telegram (or a local Bot API server) just
        # didn't confirm in time, most often on a slow connection uploading
        # a converted video sticker. The upload may well have gone through
        # anyway, so don't call it "rejected" -- that's actively misleading.
        return "err_timed_out", {}
    msg = str(exc)
    lower = msg.lower()
    if "invalid sticker set name" in lower or "sticker_set_name_invalid" in lower:
        return "err_invalid_name", {}
    if "name is already occupied" in lower or "sticker_set_name_occupied" in lower:
        return "err_name_occupied", {}
    if "stickers_too_much" in lower:
        return "err_too_many_stickers", {}
    if "png" in lower and "type" in lower or "webp" in lower and "type" in lower:
        return "err_bad_format", {}
    return "err_generic", {"msg": msg}


def explain_error(exc: Exception, lang: str) -> str:
    """Turns raw Telegram API errors from set-creation/add calls into
    something the user can actually act on, instead of a bare exception."""
    key, fields = error_key(exc)
    return i18n.t(lang, key, **fields)


def error_note(exc: Exception, lang: str) -> str:
    """The status note a failed sticker leaves: what went wrong, what to do
    next, and its problem code last, where the Report button looks for it."""
    key, _ = error_key(exc)
    return (explain_error(exc, lang) + "\n" + i18n.t(lang, "last_attempt_failed")
            + problems.code_line(problems.STICKER_ERRORS[key]))
