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
    try:
        return max(0, int(os.environ.get(name) or default))
    except ValueError:
        return default if unreadable is None else unreadable


LIFE_SECONDS = _env_int("DBOT_CACHE_SECONDS", 600, unreadable=0)
MAX_LIFE_SECONDS = 12 * 60 * 60
ENTRY_MAX_MB = _env_int("DBOT_CACHE_ENTRY_MAX_MB", 100)
TOTAL_MAX_MB = _env_int("DBOT_CACHE_MAX_MB", 400)
PER_USER = max(1, _env_int("DBOT_CACHE_PER_USER", 1))

LATEST_KEEP_SECONDS = 60 * 60

SHARED_SENDER_IDS = frozenset({1087968824, 136817688, 777000})

_clock = time.monotonic


@dataclass(eq=False)
class Entry:
    user_id: int
    key: tuple
    items: list
    dir: str
    size: int
    expires_at: float
    last_used: float
    pins: int = 0
    kept_at: float = 0.0
    dead: bool = False


_work_dir = None
_root = None
_entries: "dict[tuple, Entry]" = {}
_dead = []
_noted: "dict[int, OrderedDict]" = {}
_hits = 0
_misses = 0


def configure(work_dir) -> None:
    global _work_dir, _root
    _work_dir = work_dir
    _root = os.path.join(work_dir, "dbot-cache") if work_dir else None


def enabled() -> bool:
    return LIFE_SECONDS > 0 and _root is not None


def usable(user_id) -> bool:
    return enabled() and user_id not in SHARED_SENDER_IDS


def free_mb() -> "int | None":
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
    try:
        return _keep(user_id, (platform, url), list(items_with_paths), floor_mb)
    except Exception as exc:
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

    candidates = sorted((e for e in _entries.values() if e.pins == 0), key=lambda e: e.last_used)
    over = _used_bytes() + size - TOTAL_MAX_MB * MB
    need = 0
    freed = 0
    while over - freed > 0:
        if need >= len(candidates):
            return False
        freed += candidates[need].size
        need += 1
    if floor_mb > 0:
        free = free_mb()
        if free is None:
            return False
        freed_disk = sum(c.size for c in candidates[:need])
        while free + freed_disk / MB < floor_mb:
            if need >= len(candidates):
                return False
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
    return min(now + LIFE_SECONDS, entry.kept_at + MAX_LIFE_SECONDS)


def take(user_id: int, platform: str, url: str):
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
    entry.pins = max(0, entry.pins - 1)
    if entry.dead and entry.pins == 0:
        _dead[:] = [each for each in _dead if each is not entry]
        _remove_dir(entry.dir)


def drop(user_id: int) -> None:
    for entry in _of_user(user_id):
        _discard(entry)


def forget_user(user_id: int) -> None:
    drop(user_id)
    _noted.pop(user_id, None)


def sweep(now=None) -> None:
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
    _entries.clear()
    _dead.clear()
    _noted.clear()
    if _root is not None:
        _remove_dir(_root)


def stats() -> dict:
    return {"enabled": enabled(), "entries": len(_entries), "bytes": _used_bytes(),
            "hits": _hits, "misses": _misses}
