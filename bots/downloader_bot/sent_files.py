"""Telegram's file IDs for what this bot has already sent, kept so the same post can be sent
again without being downloaded again.

Telegram gives every file the bot sends a `file_id`, and the same bot can send that id to any
chat without uploading anything. So a post this bot has delivered once, to anybody, is
delivered again from its ids: no route is asked for the media, nothing is downloaded, nothing
is uploaded. The ten-minute delivery cache (delivery_cache.py) does the same for one person
and a few minutes; this one is for everybody and days.

What it holds, and what it does not. One row per post and per form of delivery, and the row
holds nothing about people:

  * the key is a keyed hash of "version | platform | post | form" -- not the link, and not the
    person. The hash key comes from the bot's token, so nobody who can read the table can
    compute the key of a post they are curious about, and a rotated token makes every old
    row unreachable (the sweep deletes them). No new secret to deploy.
  * the value is the list of (kind, file id) in delivery order, the last time the post was
    seen public, and the day it was last used. No user id, chat id, name, link, platform or
    caption anywhere. /deletemydata has nothing of a person's to delete here.
  * `form` is "file" when the delivery is lossless (everything went as documents) and "media"
    otherwise: Telegram cannot send a video's id as a document, so they are two rows.
  * `post` is the post's identity (post_identity): the same reel shared as /p/<code>/ and as
    /reel/<code>/, or a post on X under whichever account's name, is one row, not two.

How it is laid out (since 2.2.0; the owner, 2026-11-09: more people means more stored posts,
so make the lookup cheap now rather than when it is already slow). The lookup has always been
one probe of the primary key's B-tree, which stays a few page reads at any size; what grows is
the table and its index, and an index that no longer fits in the database's memory is what
makes a lookup slow. So a row is kept small (measured at a million rows: 216 bytes a row
instead of 376, and an index of 38 MB instead of 119):

  * the key is the first 16 bytes of the HMAC as a UUID column -- a fixed 16 bytes compared
    with memcmp, where the hex text it replaces was 65 bytes in the table and again in the
    index. 128 bits cannot collide among any number of posts this bot will ever see;
  * the ids are stored as the bytes their base64url text encodes (a quarter smaller), with a
    byte for the kind and one for the length (encode_items);
  * marking a row used writes nothing when it was already marked today, so a popular post
    costs no new row version per delivery (db.sent_files_used), and the table is made with
    room on each page (fillfactor 90) so the daily mark is a heap-only update. last_used is
    deliberately not indexed: an index on it would make every one of those marks touch the
    index too. The daily sweep reads the table once instead, which costs a fraction of a
    second at this size.

And it is read through a small memory of the rows this process used lately (recall/remember):
a post that many people send within minutes costs one database round trip, not one each. The
memory never decides trust -- is_fresh() reads the stored confirmation time on every use, so a
remembered row is exactly as trusted as the database row it copies.

Telegram does not say how long an id lasts, so the stored ids are always tried and an id that
Telegram refuses as invalid deletes the row and the post is fetched as usual. What the store
must never do is send a post that was removed: a row is trusted for CONFIRM_HOURS after the
post was last seen public (by a fetch, or by resolvers.lookup), and only then asked again.

This module has no database and no Telegram in it (is_invalid_file_id imports the error class
only when it is called): bot.py makes the database calls and the sends, and hands the results
here. The clocks can be replaced (`_clock`, `_monotonic`) so that tests need not wait.
"""
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

logger = logging.getLogger(__name__)

# Bumping this makes every existing row unreachable at once (a change in how files are made,
# or in what names a post); the sweep deletes them later. 2: 2.2.0's post identities.
STORE_VERSION = 2

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
_monotonic = time.monotonic                     # replaced by tests

_key: "bytes | None" = None


def configure(token) -> None:
    """The hash key, from the bot's token. Without a token there is no key, and the store is
    off: a key anybody can guess would make the rows readable to anybody who sees them."""
    global _key
    _key = hashlib.sha256(b"dbot-sent-files|" + token.encode()).digest() if token else None
    forget_all()


def enabled() -> bool:
    return ENABLED and _key is not None


def usable(user_id) -> bool:
    """Whether the store may be read or written for this sender: it is on, and the id is one
    person's. A sender id that stands for many people (an anonymous group admin, a channel,
    Telegram's own service account) never reads or writes it, as with the delivery cache."""
    return enabled() and user_id not in delivery_cache.SHARED_SENDER_IDS


# ---------------------------------------------------------------------------
# Which post a link names
# ---------------------------------------------------------------------------
# The links arrive cleaned by platforms.canonical_link, which keeps what a fetch needs: an
# Instagram link stays /p/ or /reel/ as it was shared, an X link keeps the account name. For
# the store only the post matters. Each pattern reads one shape canonical_link writes; anything
# else is named by its link, as before.
_IDENTITIES = (
    ("instagram", re.compile(r"https://www\.instagram\.com/(?:p|reel|tv)/([A-Za-z0-9_-]+)/")),
    ("twitter", re.compile(r"https://x\.com/[^/]+/status/(\d+)")),
    ("youtube", re.compile(r"https://www\.youtube\.com/watch\?v=([A-Za-z0-9_-]{11})")),
)


def post_identity(platform: str, url: str) -> str:
    """The post this (canonical) link names, written one way: "instagram:<code>",
    "twitter:<status id>", "youtube:<video id>", or else the link itself. A quality mark the
    picker added ("#q720", "#upto480") stays on the end: a capped copy is another delivery.
    Canonical links carry no fragment, so whatever follows a "#" is such a mark."""
    link, hash_mark, mark = url.partition("#")
    for name, pattern in _IDENTITIES:
        if platform == name:
            found = pattern.fullmatch(link)
            if found:
                return f"{name}:{found.group(1)}{hash_mark}{mark}"
    return url


def post_key(platform: str, url: str, lossless: bool) -> uuid.UUID:
    """The row of this post in this form: the first 128 bits of an HMAC-SHA256, as the UUID
    column holds them. See the module docstring."""
    form = "file" if lossless else "media"
    message = f"{STORE_VERSION}|{platform}|{post_identity(platform, url)}|{form}".encode("utf-8")
    return uuid.UUID(bytes=hmac.new(_key, message, hashlib.sha256).digest()[:16])


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


# The row's bytes: FORMAT, then for each item a code byte (the kind, plus TEXT_ID when the id
# is kept as its text), a length byte, and the id. A file id is base64url text without padding,
# so it is kept as the bytes it encodes -- but only when encoding them again gives back exactly
# the same text, which is checked for every id; one that does not (Telegram changing its ids'
# alphabet, say) is kept as its text, flagged, rather than refused.
FORMAT = 1
TEXT_ID = 0x80
_KIND_CODES = {"video": 0, "photo": 1}
_KIND_NAMES = {code: kind for kind, code in _KIND_CODES.items()}


def _id_bytes(file_id: str) -> "tuple[int, bytes] | None":
    """(flag, bytes) to keep this id as, or None when it is too long for a length byte."""
    try:
        raw = base64.urlsafe_b64decode(file_id + "=" * (-len(file_id) % 4))
        if raw and base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=") == file_id:
            return (0, raw) if len(raw) <= 255 else None
    except (ValueError, binascii.Error):
        pass
    text = file_id.encode("utf-8")
    return (TEXT_ID, text) if len(text) <= 255 else None


def encode_items(pairs) -> "bytes | None":
    """[(kind, file id), ...] as the bytes of a row, or None if it is not something a row may
    hold (1 to 10 items, kind video or photo, a non-empty id of at most 255 bytes)."""
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
    """The pairs in a row, or None for anything that does not read as 1 to 10 items exactly:
    such a row is treated as if it were absent."""
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


# ---------------------------------------------------------------------------
# The rows this process used lately
# ---------------------------------------------------------------------------
# A copy of a database row, kept RECENT_SECONDS: (items bytes, confirmed_at, last_used). Only
# this process writes the table in normal running (one DownloaderBot at a time); for the minute
# two overlap during a redeploy, the worst a stale copy can do is send an id the other one has
# just dropped, which Telegram refuses, and that path drops the row and downloads as usual.
RECENT_MAX = 2048
RECENT_SECONDS = 600

_recent: "OrderedDict[uuid.UUID, tuple[bytes, datetime, date | None, float]]" = OrderedDict()


def recall(key) -> "tuple[bytes, datetime, date | None] | None":
    """The remembered row for this key, if it is recent enough; None to ask the database."""
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
    """Whether this process has seen the row marked used today, so marking it again would
    write nothing anyway (db.sent_files_used) and need not cost a round trip."""
    entry = _recent.get(key)
    return entry is not None and entry[2] == _clock().date()


def note_used(key, confirmed_at: "datetime | None" = None) -> None:
    """The row was marked used today (and, with `confirmed_at`, seen public then)."""
    entry = _recent.get(key)
    if entry is not None:
        _recent[key] = (entry[0], confirmed_at or entry[1], _clock().date(), entry[3])


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
           "invalid": 0, "stored": 0, "remembered": 0}


def note(name: str) -> None:
    _counts[name] += 1


def stats() -> dict:
    return dict(_counts, enabled=enabled())


def reset_stats() -> None:
    for name in _counts:
        _counts[name] = 0
