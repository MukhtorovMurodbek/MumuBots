import base64
import binascii
import hashlib
import hmac
import logging
import os
import re
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

import delivery_cache
import platforms

logger = logging.getLogger(__name__)

STORE_VERSION = 2

MAX_ITEMS = 10

KINDS = ("video", "photo")


def _whole_number(name: str, default: int):
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return None


def read_settings() -> "tuple[bool, int]":
    flag = _whole_number("DBOT_SENT_FILES", 1)
    hours = _whole_number("DBOT_FILEID_CONFIRM_HOURS", 12)
    return (flag is not None and flag > 0), (hours if hours is not None and hours >= 1 else 12)


ENABLED, CONFIRM_HOURS = read_settings()

_clock = lambda: datetime.now(timezone.utc)      # noqa: E731 - replaced by tests
_monotonic = time.monotonic

_key: "bytes | None" = None


def configure(token) -> None:
    global _key
    _key = hashlib.sha256(b"dbot-sent-files|" + token.encode()).digest() if token else None
    forget_all()


def enabled() -> bool:
    return ENABLED and _key is not None


def usable(user_id) -> bool:
    return enabled() and user_id not in delivery_cache.SHARED_SENDER_IDS


_IDENTITIES = (
    ("instagram", re.compile(r"https://www\.instagram\.com/(?:p|reel|tv)/([A-Za-z0-9_-]+)/")),
    ("twitter", re.compile(r"https://x\.com/[^/]+/status/(\d+)")),
    (platforms.UNADVERTISED, re.compile(re.escape(platforms.watch_link("")) + r"([A-Za-z0-9_-]{11})")),
)


def post_identity(platform: str, url: str) -> str:
    link, hash_mark, mark = url.partition("#")
    for name, pattern in _IDENTITIES:
        if platform == name:
            found = pattern.fullmatch(link)
            if found:
                return f"{name}:{found.group(1)}{hash_mark}{mark}"
    return url


def post_key(platform: str, url: str, lossless: bool) -> uuid.UUID:
    form = "file" if lossless else "media"
    message = f"{STORE_VERSION}|{platform}|{post_identity(platform, url)}|{form}".encode("utf-8")
    return uuid.UUID(bytes=hmac.new(_key, message, hashlib.sha256).digest()[:16])


@dataclass(frozen=True)
class StoredFile:
    kind: str
    file_id: str
    filename: str = "download"


def _valid_pairs(pairs) -> bool:
    if not isinstance(pairs, (list, tuple)) or not 1 <= len(pairs) <= MAX_ITEMS:
        return False
    for pair in pairs:
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            return False
        kind, file_id = pair
        if kind not in KINDS or not isinstance(file_id, str) or not file_id:
            return False
    return True


FORMAT = 1
TEXT_ID = 0x80
_KIND_CODES = {"video": 0, "photo": 1}
_KIND_NAMES = {code: kind for kind, code in _KIND_CODES.items()}


def _id_bytes(file_id: str) -> "tuple[int, bytes] | None":
    try:
        raw = base64.urlsafe_b64decode(file_id + "=" * (-len(file_id) % 4))
        if raw and base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=") == file_id:
            return (0, raw) if len(raw) <= 255 else None
    except (ValueError, binascii.Error):
        pass
    text = file_id.encode("utf-8")
    return (TEXT_ID, text) if len(text) <= 255 else None


def encode_items(pairs) -> "bytes | None":
    if not _valid_pairs(pairs):
        return None
    out = bytearray([FORMAT])
    for kind, file_id in pairs:
        kept = _id_bytes(file_id)
        if kept is None:
            return None
        flag, data = kept
        out += bytes([_KIND_CODES[kind] | flag, len(data)]) + data
    return bytes(out)


def decode_items(blob) -> "list[tuple[str, str]] | None":
    try:
        data = bytes(blob)
    except TypeError:
        return None
    if not data or data[0] != FORMAT:
        return None
    pairs, at = [], 1
    try:
        while at < len(data):
            if at + 2 > len(data):
                return None
            code, length = data[at], data[at + 1]
            chunk = data[at + 2:at + 2 + length]
            at += 2 + length
            kind = _KIND_NAMES.get(code & 0x7F)
            if kind is None or length == 0 or len(chunk) != length:
                return None
            if code & TEXT_ID:
                file_id = chunk.decode("utf-8")
            else:
                file_id = base64.urlsafe_b64encode(chunk).decode("ascii").rstrip("=")
            pairs.append((kind, file_id))
    except UnicodeDecodeError:
        return None
    return pairs if _valid_pairs(pairs) else None


def stored_files(pairs) -> list:
    return [StoredFile(kind, file_id) for kind, file_id in pairs]


def is_fresh(confirmed_at, now=None) -> bool:
    if not isinstance(confirmed_at, datetime):
        return False
    now = now if now is not None else _clock()
    if confirmed_at.tzinfo is None:
        confirmed_at = confirmed_at.replace(tzinfo=timezone.utc)
    age = now - confirmed_at
    return -timedelta(minutes=5) <= age < timedelta(hours=CONFIRM_HOURS)


RECENT_MAX = 2048
RECENT_SECONDS = 600

_recent: "OrderedDict[uuid.UUID, tuple[bytes, datetime, date | None, float]]" = OrderedDict()


def recall(key) -> "tuple[bytes, datetime, date | None] | None":
    entry = _recent.get(key)
    if entry is None:
        return None
    if _monotonic() - entry[3] > RECENT_SECONDS:
        del _recent[key]
        return None
    _recent.move_to_end(key)
    return entry[0], entry[1], entry[2]


def remember(key, items: bytes, confirmed_at: datetime, last_used: "date | None") -> None:
    _recent[key] = (bytes(items), confirmed_at, last_used, _monotonic())
    _recent.move_to_end(key)
    while len(_recent) > RECENT_MAX:
        _recent.popitem(last=False)


def forget(key) -> None:
    _recent.pop(key, None)


def forget_all() -> None:
    _recent.clear()


def used_today(key) -> bool:
    entry = _recent.get(key)
    return entry is not None and entry[2] == _clock().date()


def note_used(key, confirmed_at: "datetime | None" = None) -> None:
    entry = _recent.get(key)
    if entry is not None:
        _recent[key] = (entry[0], confirmed_at or entry[1], _clock().date(), entry[3])


_INVALID_ID_MARKERS = (
    "wrong file identifier", "wrong remote file identifier", "file_id",
    "type of file mismatch", "file reference", "file_reference", "media_empty",
)


def is_invalid_file_id(exc: BaseException) -> bool:
    from telegram.error import BadRequest
    if not isinstance(exc, BadRequest):
        return False
    message = str(exc).lower()
    return any(marker in message for marker in _INVALID_ID_MARKERS)


_counts = {"hit": 0, "stale": 0, "miss": 0, "present": 0, "missing": 0, "unknown": 0,
           "invalid": 0, "stored": 0, "remembered": 0}


def note(name: str) -> None:
    _counts[name] += 1


def stats() -> dict:
    return dict(_counts, enabled=enabled())


def reset_stats() -> None:
    for name in _counts:
        _counts[name] = 0
