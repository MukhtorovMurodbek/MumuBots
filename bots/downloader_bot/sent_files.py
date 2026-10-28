"""Telegram's file IDs for what this bot has already sent, kept so the same post can be sent
again without being downloaded again.

Telegram gives every file the bot sends a `file_id`, and the same bot can send that id to any
chat without uploading anything. So a post this bot has delivered once, to anybody, is
delivered again from its ids: no route is asked for the media, nothing is downloaded, nothing
is uploaded. The five-minute delivery cache (delivery_cache.py) does the same for one person
and a few minutes; this one is for everybody and days.

What it holds, and what it does not (PRIVACY.md says so too). One row per post and per form of
delivery, and the row holds nothing about people:

  * the key is a keyed hash of "version | platform | link | form" -- not the link, and not the
    person. The hash key comes from the bot's token, so nobody who can read the table can
    compute the key of a post they are curious about, and a rotated token makes every old
    row unreachable (the sweep deletes them). No new secret to deploy.
  * the value is the list of (kind, file id) in delivery order, the last time the post was
    seen public, and the day it was last used. No user id, chat id, name, link, platform or
    caption anywhere. /deletemydata has nothing of a person's to delete here.
  * `form` is "file" when the delivery is lossless (everything went as documents) and "media"
    otherwise: Telegram cannot send a video's id as a document, so they are two rows.

Telegram does not say how long an id lasts, so the stored ids are always tried and an id that
Telegram refuses as invalid deletes the row and the post is fetched as usual. What the store
must never do is send a post that was removed: a row is trusted for CONFIRM_HOURS after the
post was last seen public (by a fetch, or by resolvers.lookup), and only then asked again.

This module has no database and no Telegram in it (is_invalid_file_id imports the error class
only when it is called): bot.py makes the database calls and the sends, and hands the results
here. The clock can be replaced (`_clock`) so that tests need not wait.
"""
import hashlib
import hmac
import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import delivery_cache

logger = logging.getLogger(__name__)

# Bumping this makes every existing row unreachable at once (a change in how files are made,
# say); the sweep deletes them later.
STORE_VERSION = 1

# One chunk of an album. A post with more items than this is not stored: it would be sent in
# several chunks, and an id that is invalid in the second would re-send the first.
MAX_ITEMS = 10

KINDS = ("video", "photo")


def _whole_number(name: str, default: int):
    """The number in the environment, `default` when it is not set, None when it is not a
    whole number."""
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return None


def read_settings() -> "tuple[bool, int]":
    """(on, confirm hours) from the environment.

    DBOT_SENT_FILES: 1 (the default) is on, 0 is off, and so is anything that is not a whole
    number -- whoever typed "off" meant no, like DBOT_CACHE_SECONDS. DBOT_FILEID_CONFIRM_HOURS:
    12, and 12 again for a value that is unreadable or under 1: a typo here must not stop the
    bot starting, and must not make the check never happen. (How long unused rows are kept,
    DBOT_FILEID_KEEP_DAYS, is read where they are deleted: db.py.)"""
    flag = _whole_number("DBOT_SENT_FILES", 1)
    hours = _whole_number("DBOT_FILEID_CONFIRM_HOURS", 12)
    return (flag is not None and flag > 0), (hours if hours is not None and hours >= 1 else 12)


ENABLED, CONFIRM_HOURS = read_settings()

_clock = lambda: datetime.now(timezone.utc)      # noqa: E731 - replaced by tests

_key: "bytes | None" = None


def configure(token) -> None:
    """The hash key, from the bot's token. Without a token there is no key, and the store is
    off: a key anybody can guess would make the rows readable to anybody who sees them."""
    global _key
    _key = hashlib.sha256(b"dbot-sent-files|" + token.encode()).digest() if token else None


def enabled() -> bool:
    return ENABLED and _key is not None


def usable(user_id) -> bool:
    """Whether the store may be read or written for this sender: it is on, and the id is one
    person's. A sender id that stands for many people (an anonymous group admin, a channel,
    Telegram's own service account) never reads or writes it, as with the delivery cache."""
    return enabled() and user_id not in delivery_cache.SHARED_SENDER_IDS


def post_key(platform: str, url: str, lossless: bool) -> str:
    """The row of this post in this form: a hex HMAC-SHA256. See the module docstring."""
    form = "file" if lossless else "media"
    message = f"{STORE_VERSION}|{platform}|{url}|{form}".encode("utf-8")
    return hmac.new(_key, message, hashlib.sha256).hexdigest()


# ---------------------------------------------------------------------------
# What a row holds
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class StoredFile:
    """One stored file as bot._send_media takes it in place of a MediaItem: what it was (the
    kind of the item sent, whatever the form) and the id to send it by."""
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


def encode_items(pairs) -> "str | None":
    """[(kind, file id), ...] as the JSON text of a row, or None if it is not something a row
    may hold (1 to 10 items, kind video or photo, an id that is a non-empty string)."""
    if not _valid_pairs(pairs):
        return None
    return json.dumps([[kind, file_id] for kind, file_id in pairs], separators=(",", ":"))


def decode_items(text) -> "list[tuple[str, str]] | None":
    """The pairs in a row, or None for anything that is not a list of 1 to 10 [kind, id]: such
    a row is treated as if it were absent."""
    try:
        pairs = json.loads(text)
    except (TypeError, ValueError):
        return None
    if not _valid_pairs(pairs):
        return None
    return [(kind, file_id) for kind, file_id in pairs]


def stored_files(pairs) -> list:
    return [StoredFile(kind, file_id) for kind, file_id in pairs]


def is_fresh(confirmed_at, now=None) -> bool:
    """Whether the post was seen public less than CONFIRM_HOURS ago. A time in the future
    (the two clocks disagree) is fresh within five minutes of it and no more, so that a
    wrong clock cannot keep a post trusted for ever; anything that is not a time is not fresh."""
    if not isinstance(confirmed_at, datetime):
        return False
    now = now if now is not None else _clock()
    if confirmed_at.tzinfo is None:
        confirmed_at = confirmed_at.replace(tzinfo=timezone.utc)
    age = now - confirmed_at
    return -timedelta(minutes=5) <= age < timedelta(hours=CONFIRM_HOURS)


# What Telegram's refusal of an id says (lowercased). Anything else -- Forbidden (the person
# blocked the bot), RetryAfter (flood), TimedOut, NetworkError, any other BadRequest -- is an
# ordinary send failure that says nothing about the id.
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


# ---------------------------------------------------------------------------
# Counts for /status (since the bot started; nothing about who or what)
# ---------------------------------------------------------------------------

_counts = {"hit": 0, "stale": 0, "miss": 0, "present": 0, "missing": 0, "unknown": 0,
           "invalid": 0, "stored": 0}


def note(name: str) -> None:
    _counts[name] += 1


def stats() -> dict:
    return dict(_counts, enabled=enabled())


def reset_stats() -> None:
    for name in _counts:
        _counts[name] = 0
