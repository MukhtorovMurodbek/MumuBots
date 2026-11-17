import asyncio
import logging
import secrets
import time
from collections import OrderedDict
from datetime import timedelta

from telegram import InputSticker
from telegram.error import BadRequest, NetworkError, RetryAfter, TimedOut

import i18n
import problems
from emoji_utils import DEFAULT_EMOJI
from image_utils import to_sticker_png
from video_sticker import to_video_sticker_webm

logger = logging.getLogger(__name__)

REFS_MAX = 20000
_refs: "OrderedDict[tuple[int, int], tuple[str, str]]" = OrderedDict()
TITLES_MAX = 5000
_titles: "OrderedDict[str, str]" = OrderedDict()


def remember(chat_id: int, message_id: int, pack_name: str, file_id: "str | None") -> None:
    if not file_id:
        return
    key = (chat_id, message_id)
    _refs[key] = (pack_name, file_id)
    _refs.move_to_end(key)
    while len(_refs) > REFS_MAX:
        _refs.popitem(last=False)


def sticker_of(message) -> "tuple[str, str] | None":
    if message is None:
        return None
    sticker = getattr(message, "sticker", None)
    if sticker is not None and sticker.set_name:
        return sticker.set_name, sticker.file_id
    return _refs.get((message.chat_id, message.message_id))


def seen_title(pack_name: str) -> "str | None":
    return _titles.get(pack_name)


def _note_title(pack_name: str, title: "str | None") -> None:
    if not title:
        return
    _titles[pack_name] = title
    _titles.move_to_end(pack_name)
    while len(_titles) > TITLES_MAX:
        _titles.popitem(last=False)


async def in_pack(bot, pack_name: "str | None", sticker) -> bool:
    if not pack_name or sticker is None or sticker.set_name != pack_name:
        return False
    try:
        sticker_set = await bot.get_sticker_set(pack_name)
    except Exception:
        return True
    return any(s.file_unique_id == sticker.file_unique_id for s in sticker_set.stickers)


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
    pass


def media_of(message) -> "tuple[str, str] | None":
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


TELEGRAM_TRIES = 3
FLOOD_WAIT_MAX = 30
NAME_TRIES = 3


def retry_after_seconds(exc) -> float:
    value = getattr(exc, "retry_after", None)
    if isinstance(value, timedelta):
        return value.total_seconds()
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(FLOOD_WAIT_MAX + 1)


async def telegram_call(call, *args, read: bool = False, **kwargs):
    for attempt in range(1, TELEGRAM_TRIES + 1):
        try:
            return await call(*args, **kwargs)
        except RetryAfter as exc:
            wait = retry_after_seconds(exc)
            if attempt == TELEGRAM_TRIES or wait > FLOOD_WAIT_MAX:
                raise
            logger.info("Telegram asked to wait %.0f s before %s; waiting", wait,
                        getattr(call, "__name__", "the next call"))
            await asyncio.sleep(wait + 0.5)
        except NetworkError as exc:
            if not read or isinstance(exc, BadRequest) or attempt == TELEGRAM_TRIES:
                raise
            await asyncio.sleep(2 * attempt)


def name_refused(exc: Exception) -> bool:
    lower = str(exc).lower()
    return isinstance(exc, BadRequest) and (
        "shortname_occupy_failed" in lower or "name is already occupied" in lower
        or "sticker_set_name_occupied" in lower)


async def download(bot, file_id: str) -> bytes:
    tg_file = await telegram_call(bot.get_file, file_id, read=True)
    return bytes(await telegram_call(tg_file.download_as_bytearray, read=True))


async def static_sticker(raw: bytes) -> InputSticker:
    png = (await asyncio.to_thread(to_sticker_png, raw)).getvalue()
    return InputSticker(sticker=png, emoji_list=[DEFAULT_EMOJI], format="static")


async def video_sticker(raw: bytes, lang: str, speed: float = 1.0, low_detail: bool = False,
                        low_fps: bool = False) -> InputSticker:
    webm = await asyncio.to_thread(to_video_sticker_webm, raw, lang, speed, low_detail, low_fps)
    return InputSticker(sticker=webm, emoji_list=[DEFAULT_EMOJI], format="video")


async def add_to_pack(bot, owner_id: int, pack_name: str, input_sticker: InputSticker,
                      want_file_id: bool = True) -> "tuple[str | None, int | None]":
    await telegram_call(bot.add_sticker_to_set, user_id=owner_id, name=pack_name, sticker=input_sticker,
                        read_timeout=60, write_timeout=60)
    if not want_file_id:
        return None, None
    return await last_sticker(bot, pack_name)


async def last_sticker(bot, pack_name: str) -> "tuple[str | None, int | None]":
    try:
        sticker_set = await telegram_call(bot.get_sticker_set, pack_name, read=True)
    except Exception:
        logger.warning("Couldn't read the pack back after adding a sticker", exc_info=True)
        return None, None
    _note_title(pack_name, getattr(sticker_set, "title", None))
    return sticker_set.stickers[-1].file_id, len(sticker_set.stickers)


def added_text(lang: str, title: str, number: "int | None", emoji: str, how: str = "") -> str:
    if how:
        if number is None:
            return i18n.t(lang, "sticker_added_unnumbered_fit", title=title, emoji=emoji, how=how)
        return i18n.t(lang, "sticker_added_to_fit", title=title, n=number, emoji=emoji, how=how)
    if number is None:
        return i18n.t(lang, "sticker_added_unnumbered", title=title, emoji=emoji)
    return i18n.t(lang, "sticker_added_to", title=title, n=number, emoji=emoji)


def error_key(exc: Exception) -> tuple:
    if isinstance(exc, TimedOut):
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
    key, fields = error_key(exc)
    return i18n.t(lang, key, **fields)


def error_note(exc: Exception, lang: str) -> str:
    key, _ = error_key(exc)
    return (explain_error(exc, lang) + "\n" + i18n.t(lang, "last_attempt_failed")
            + problems.code_line(problems.STICKER_ERRORS[key]))
