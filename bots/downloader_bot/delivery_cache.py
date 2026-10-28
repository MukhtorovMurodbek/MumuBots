"""The last download (PER_USER, default 1), kept for LIFE_SECONDS (default 10
minutes), so the buttons under it and the same link sent again are answered
without fetching the post a second time.

What the owner asked (2026-10-12): after a download is delivered, keep the files a
few minutes; if the person taps the button under it ("get it as a file", "get it
compressed") or sends the same link again, send the kept files instead of going to
the site again; delete them as soon as the person sends a different link; every
re-use starts the clock again. LEGAL.md and /privacy say so. (2.2.0 keeps up to
PER_USER deliveries per person, which another link does not drop; the owner set it to
the last one, for 10 minutes, on 2026-10-28 -- so with the default, a different link
pushes the previous delivery out, as it did before 2.2.0.)

Entries are per person, keyed by (platform, url) exactly as _resolve_and_send was
given them. It holds the files and nothing else: no link text outside the key, no
name, no message. The files live in a folder of their own beside the downloads
(<work dir>/dbot-cache/<random>/), and the whole folder is wiped when the bot
starts, because anything in it then belongs to a process that is gone.

The rules, each of which has a test that fails without it:

  * A person keeps at most PER_USER deliveries, least recently used out first. A
    download is kept only if its link is still among the person's last PER_USER
    links (note_link, keep): a slow download whose link has since been pushed out
    is deleted, not kept.
  * A file is never deleted while a send from it may still be reading it (take pins
    the entry, release lets go; drop, expiry and eviction of a pinned entry only
    mark it dead, and the last release deletes it. Eviction to make room never
    takes a pinned entry at all).
  * Bounded three ways: one delivery over ENTRY_MAX_MB is not kept; the whole cache
    stays under TOTAL_MAX_MB, least recently used out first; and an entry is kept
    only if, after evicting what is needed, the disk still has the free space the
    downloads themselves need (the caller says how much). The cache gives way: a
    download that needs the space (a large file, a merge: make_room) deletes kept
    files first, so a kept delivery does not make a download refuse for space.
  * DBOT_CACHE_SECONDS=0 turns all of it off: every call below does nothing. So does
    a value that is not a whole number ("off"), and a negative one.
  * A sender id that stands for many people (an anonymous group admin, a channel,
    Telegram's own service account) is never cached, taken or noted: SHARED_SENDER_IDS.

Pure: no Telegram, no database, no threads. Every function is synchronous and is
called on the event loop thread, which is what makes the bookkeeping safe without
a lock. The clock can be replaced (`_clock`) so that tests need not wait.
"""
import logging
import os
import shutil
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass

logger = logging.getLogger(__name__)

MB = 1024 * 1024


def _env_int(name: str, default: int, unreadable: "int | None" = None) -> int:
    """A whole number from the environment, never negative; `unreadable` (the
    default when it is None) for anything else -- a typo here must not stop the bot
    starting. Not set, or empty, is the default."""
    try:
        return max(0, int(os.environ.get(name) or default))
    except ValueError:
        return default if unreadable is None else unreadable


# How long a delivery is kept, from the last time it was used. 0: no cache at all.
# Something that is not a whole number ("off") is 0 too, not the default: whoever
# typed it meant no.
LIFE_SECONDS = _env_int("DBOT_CACHE_SECONDS", 600, unreadable=0)
# However often it is used, a kept delivery goes this long after it was kept. The
# terms promise that a post removed at the platform stops being sent within 12 hours
# (sent_files.CONFIRM_HOURS for the file-id store); every copy of the link restarts
# LIFE_SECONDS, so without this a person re-sending it would be served a removed
# post for ever.
MAX_LIFE_SECONDS = 12 * 60 * 60
# One delivery (all its files) over this is not kept, and the whole cache stays under that.
ENTRY_MAX_MB = _env_int("DBOT_CACHE_ENTRY_MAX_MB", 100)
TOTAL_MAX_MB = _env_int("DBOT_CACHE_MAX_MB", 400)
# How many deliveries one person may have kept at once; the least recently used goes first.
PER_USER = max(1, _env_int("DBOT_CACHE_PER_USER", 1))

# A person's latest link is remembered this long, or until they send another, even
# when the cache's own life is shorter: a link that waited for a slot and then
# downloaded for longer than LIFE_SECONDS must still be kept when it lands.
LATEST_KEEP_SECONDS = 60 * 60

# Sender ids that stand for many people, not for one: the anonymous group admin,
# a message sent as a channel, and Telegram's own service account. One person's
# "latest link" and "kept delivery" would be everybody's, so nothing is cached, taken
# or noted for them. The one place this is decided is usable().
SHARED_SENDER_IDS = frozenset({1087968824, 136817688, 777000})

_clock = time.monotonic       # replaced by tests


@dataclass(eq=False)          # compared by identity: two entries are never "equal"
class Entry:
    user_id: int
    key: tuple                # (platform, url)
    items: list               # [(MediaItem, path in the cache)], in post order
    dir: str
    size: int                 # bytes
    expires_at: float
    last_used: float
    pins: int = 0             # sends in progress from these files
    kept_at: float = 0.0      # when it was kept, for MAX_LIFE_SECONDS
    dead: bool = False        # dropped while pinned: deleted at the last release


_work_dir = None              # set by configure()
_root = None
_entries: "dict[tuple, Entry]" = {}
_dead = []
_noted: "dict[int, OrderedDict]" = {}
_hits = 0
_misses = 0


def configure(work_dir) -> None:
    """Where downloads are staged; the cache folder is inside it."""
    global _work_dir, _root
    _work_dir = work_dir
    _root = os.path.join(work_dir, "dbot-cache") if work_dir else None


def enabled() -> bool:
    return LIFE_SECONDS > 0 and _root is not None


def usable(user_id) -> bool:
    """Whether anything may be cached, taken or noted for this sender: the cache is
    on and the id is one person's (not SHARED_SENDER_IDS)."""
    return enabled() and user_id not in SHARED_SENDER_IDS


def free_mb() -> "int | None":
    """Free space on the disk the downloads are staged on, or None."""
    try:
        return shutil.disk_usage(_work_dir).free // MB
    except (OSError, TypeError):
        return None


def _remove_dir(path: str) -> None:
    try:
        shutil.rmtree(path)
    except FileNotFoundError:
        pass
    except OSError as exc:
        # The kind of failure only: its text would carry the path.
        logger.warning("couldn't delete a cached delivery (%s)", type(exc).__name__)


def _discard(entry: Entry) -> None:
    if _entries.get((entry.user_id, entry.key)) is entry:
        del _entries[(entry.user_id, entry.key)]
    if entry.pins > 0:
        entry.dead = True
        if not any(each is entry for each in _dead):
            _dead.append(entry)
    else:
        _remove_dir(entry.dir)


def _used_bytes() -> int:
    return sum(e.size for e in _entries.values()) + sum(e.size for e in _dead)


def _of_user(user_id: int) -> "list[Entry]":
    return [entry for (owner, _key), entry in _entries.items() if owner == user_id]


NOTED_OFF = "off"
NOTED_NEW = "new"
NOTED_AGAIN_CACHED = "again.cached"
NOTED_AGAIN_NOT_CACHED = "again.not_cached"


def _live(entry: "Entry | None", now: float) -> bool:
    return entry is not None and entry.expires_at > now


def note_link(user_id: int, platform: str, url: str) -> str:
    """The person has just sent (or tapped) this link. It is now their latest (they
    keep their last PER_USER), and a live kept delivery of it has its life restarted;
    an expired one is dropped. Returns what it found (NOTED_*): whether this is the link
    they sent last, and if so whether a live delivery of it was kept."""
    if not usable(user_id):
        return NOTED_OFF
    now = _clock()
    key = (platform, url)
    noted = _noted.setdefault(user_id, OrderedDict())
    again = bool(noted) and next(reversed(noted)) == key
    noted[key] = now
    noted.move_to_end(key)
    while len(noted) > PER_USER:
        noted.popitem(last=False)
    entry = _entries.get((user_id, key))
    live = _live(entry, now)
    if entry is not None:
        if live:
            entry.expires_at = _renewed(entry, now)
            entry.last_used = now
        else:
            _discard(entry)
    if not again:
        return NOTED_NEW
    return NOTED_AGAIN_CACHED if live else NOTED_AGAIN_NOT_CACHED


def keep(user_id: int, platform: str, url: str, items_with_paths, floor_mb: int = 0) -> bool:
    """Take over these delivered files. True: the cache now owns them and the caller
    must not delete them. False: they are the caller's, as before -- the cache is off,
    this link is not among the person's last PER_USER, it is too big, the disk is too full, or a
    move failed (and then nothing is left half-moved). Never raises.

    `floor_mb` is the free space the disk must still have with this entry kept."""
    try:
        return _keep(user_id, (platform, url), list(items_with_paths), floor_mb)
    except Exception as exc:
        # The kind of failure only, never exc_info or its text: they carry paths.
        logger.warning("couldn't keep a delivery (%s)", type(exc).__name__)
        return False


def _keep(user_id: int, key: tuple, items_with_paths: list, floor_mb: int) -> bool:
    if not usable(user_id) or not items_with_paths:
        return False
    if key not in _noted.get(user_id, ()):
        return False
    size = sum(os.path.getsize(path) for _, path in items_with_paths)
    if size > ENTRY_MAX_MB * MB:
        return False
    previous = _entries.get((user_id, key))
    if previous is not None:
        _discard(previous)
    mine = sorted((e for e in _of_user(user_id) if e.pins == 0), key=lambda e: e.last_used)
    while len(_of_user(user_id)) >= PER_USER and mine:
        _discard(mine.pop(0))

    # Who would have to go. Least recently used first; a pinned entry is being
    # sent from and cannot be freed now, so it is not a candidate.
    candidates = sorted((e for e in _entries.values() if e.pins == 0), key=lambda e: e.last_used)
    over = _used_bytes() + size - TOTAL_MAX_MB * MB
    need = 0                                    # how many of the candidates, from the front
    freed = 0
    while over - freed > 0:
        if need >= len(candidates):
            return False                        # even with all of them gone it does not fit
        freed += candidates[need].size
        need += 1
    if floor_mb > 0:
        free = free_mb()
        if free is None:
            return False
        freed_disk = sum(c.size for c in candidates[:need])
        while free + freed_disk / MB < floor_mb:
            if need >= len(candidates):
                return False                    # the disk is too full for this to stay
            freed_disk += candidates[need].size
            need += 1
    for victim in candidates[:need]:
        _discard(victim)

    directory = os.path.join(_root, uuid.uuid4().hex)
    os.makedirs(directory, mode=0o700)
    moved, kept = [], []
    try:
        for item, path in items_with_paths:
            destination = os.path.join(directory, os.path.basename(path))
            if os.path.lexists(destination):
                raise FileExistsError("two files with one name")
            os.replace(path, destination)
            moved.append((path, destination))
            kept.append((item, destination))
    except Exception:
        for path, destination in reversed(moved):
            try:
                os.replace(destination, path)
            except OSError:
                try:
                    os.remove(destination)
                except OSError:
                    pass
        _remove_dir(directory)
        return False
    now = _clock()
    _entries[(user_id, key)] = Entry(user_id, key, kept, directory, size, now + LIFE_SECONDS, now, kept_at=now)
    return True


def keep_bytes(user_id: int, platform: str, url: str, item, data: bytes, filename: str,
               floor_mb: int = 0) -> bool:
    if not usable(user_id) or _work_dir is None:
        return False
    path = os.path.join(_work_dir, f"{uuid.uuid4().hex}-{filename}")
    try:
        with open(path, "wb") as handle:
            handle.write(data)
    except OSError as exc:
        logger.warning("couldn't keep a picture (%s)", type(exc).__name__)
        return False
    kept = keep(user_id, platform, url, [(item, path)], floor_mb)
    if not kept:
        try:
            os.remove(path)
        except OSError:
            pass
    return kept


def _renewed(entry: Entry, now: float) -> float:
    """LIFE_SECONDS again from now, but never past MAX_LIFE_SECONDS after keeping."""
    return min(now + LIFE_SECONDS, entry.kept_at + MAX_LIFE_SECONDS)


def take(user_id: int, platform: str, url: str):
    """The person's kept delivery of exactly this link, pinned for the send (call
    release() when it is over, whatever happens) and with its life restarted; or
    None."""
    global _hits, _misses
    if not usable(user_id):
        return None
    now = _clock()
    key = (platform, url)
    entry = _entries.get((user_id, key))
    if entry is not None and entry.expires_at <= now:
        _discard(entry)
        entry = None
    if entry is not None and not all(os.path.exists(p) for _, p in entry.items):
        _discard(entry)
        entry = None
    if entry is None:
        _misses += 1
        return None
    entry.pins += 1
    entry.expires_at = _renewed(entry, now)
    entry.last_used = now
    _hits += 1
    return entry


def has(user_id: int, platform: str, url: str) -> bool:
    entry = _entries.get((user_id, (platform, url))) if usable(user_id) else None
    return _live(entry, _clock()) and all(os.path.exists(p) for _, p in entry.items)


def release(entry) -> None:
    """The send from `entry` is over."""
    entry.pins = max(0, entry.pins - 1)
    if entry.dead and entry.pins == 0:
        _dead[:] = [each for each in _dead if each is not entry]
        _remove_dir(entry.dir)


def drop(user_id: int) -> None:
    for entry in _of_user(user_id):
        _discard(entry)


def forget_user(user_id: int) -> None:
    """/deletemydata: the kept delivery, and also what makes a download that is still
    in flight kept when it lands (their latest link)."""
    drop(user_id)
    _noted.pop(user_id, None)


def sweep(now=None) -> None:
    """Drop what has expired, and forget latest links that nothing is waiting on (an
    hour old, LATEST_KEEP_SECONDS: not the cache's own life, which a slow download
    outlasts). Every few seconds from the job queue."""
    now = _clock() if now is None else now
    for entry in list(_entries.values()):
        if not enabled() or entry.expires_at <= now:
            _discard(entry)
    for user_id, noted in list(_noted.items()):
        for key, at in list(noted.items()):
            if (user_id, key) not in _entries and (not enabled() or at + LATEST_KEEP_SECONDS <= now):
                del noted[key]
        if not noted:
            del _noted[user_id]


def make_room(mb: int, min_free_mb: int = 0) -> None:
    """A download is about to need up to `mb` MB of the disk, on top of the
    `min_free_mb` it must always leave free: delete kept deliveries, least recently
    used first, until the disk has both or nothing that can be deleted is left. A
    kept delivery that is being sent from is not touched (it frees nothing now).
    This is what makes the cache give way, so that it never makes a download refuse
    for space. Only frees space; never raises."""
    try:
        if not enabled():
            return
        free = free_mb()
        if free is None:
            return
        need = min_free_mb + mb
        freed = 0
        for victim in sorted((e for e in _entries.values() if e.pins == 0), key=lambda e: e.last_used):
            if free + freed / MB >= need:
                break
            freed += victim.size
            _discard(victim)
    except Exception as exc:
        logger.warning("couldn't make room (%s)", type(exc).__name__)


def wipe() -> None:
    """Delete everything in the cache folder (at startup: it can only hold what a
    previous process left behind) and forget all state."""
    _entries.clear()
    _dead.clear()
    _noted.clear()
    if _root is not None:
        _remove_dir(_root)


def stats() -> dict:
    """Counts only, for the owner's /status."""
    return {"enabled": enabled(), "entries": len(_entries), "bytes": _used_bytes(),
            "hits": _hits, "misses": _misses}
