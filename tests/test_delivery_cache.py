"""The last download, kept five minutes: the redo button and the same link, answered without fetching twice.

    python tests/test_delivery_cache.py

After a download is delivered DownloaderBot keeps its files (downloader_bot/delivery_cache.py) so that
the button under it ("get it as a file", "get it compressed") and the same link sent again are answered
from them -- one fetch, not two. Each use restarts the five minutes (DBOT_CACHE_SECONDS); a different
link deletes the files at once; /deletemydata, a restart and a full disk delete them too. PRIVACY.md,
TERMS.md and /privacy say so. Two halves:

The module on its own, with a fake clock and a temporary folder:
  * keep / take / release / drop / sweep / wipe; the life restarted by a use and by the same link
    noted again, and not by a different one; a different link dropping the entry at once;
  * the latest-link rule: a download is kept only if its link is still the person's latest (link A then
    link B pasted while A downloads: A is not kept, B is);
  * one delivery over DBOT_CACHE_ENTRY_MAX_MB is not kept; the whole cache stays under DBOT_CACHE_MAX_MB with
    the least recently used out first, and nothing is evicted for a delivery that would not fit anyway;
  * the disk floor: kept only if, after evicting what is needed, the free space is still the floor;
  * a file is never deleted while a send may be reading it: drop, replacement, expiry, a different link and
    the erase all wait for the last release; eviction does not take a pinned entry at all;
  * a move that fails halfway leaks nothing and gives the files back; two files with one name are refused
    rather than one overwritten by the other;
  * 0 turns it all off.

The real handlers, with the routes, the database and Telegram faked (the way test_merge drives them):
  * download, then the button: one fetch, and the second send is a document of the same bytes; a photo
    and an album the same; the button again sends the first form again, still from the kept files;
  * the same link pasted again within the life: no fetch; after it: a fetch; the allowance counts every
    one; a different link: the old files are gone from the disk before its download even starts;
  * a hit still takes the slots and the restart note, and needs neither a free disk nor a route; it
    never asks a route anything; a failed send says so and keeps the entry; a failed first delivery
    keeps nothing and leaves nothing on disk;
  * the cache off: today's behaviour exactly; the floor passed to the cache; /deletemydata (the cache,
    the memory behind the buttons, and a download still in flight); the startup wipe and the sweep job;
  * /status's line, which holds counts and nothing else; logs without the link or the id;
  * the merge path untouched, and the order of the calls read from the source.

No network, no database, nothing from Telegram.

To see a check catch breakage, copy the bot into tests/.mirror, break the copy (the way tests/README.md
describes) and run this again: it imports from the mirror when there is one.
"""
import asyncio
import inspect
import logging
import os
import re
import pathlib
import shutil
import sys
import tempfile
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent
SRC = HERE / ".mirror" / "downloader_bot"
if not SRC.exists():
    SRC = REPO / "downloader_bot"
sys.path.insert(0, str(SRC))
sys.path.insert(1, str(REPO / "shared"))
sys.path.insert(0, str(HERE / "stubs"))

SCRATCH = pathlib.Path(tempfile.mkdtemp(prefix="downloaderbot-cache-"))      # the bot's work directory
os.environ.update(ENV_FILES="off", DBOT_TOKEN="123:test", DBOT_USERNAME="test", DATABASE_URL="postgresql://stub",
                  DBOT_WORK_DIR=str(SCRATCH),
                  DBOT_SENT_FILES="0")      # the file-id store has its own test (test_sent_files.py); here only the disk cache
for key in ("DBOT_CACHE_SECONDS", "DBOT_CACHE_ENTRY_MAX_MB", "DBOT_CACHE_MAX_MB", "DBOT_MAX_DOWNLOAD_MB",
            "DBOT_MIN_FREE_MB", "DBOT_MAX_CONCURRENT_DOWNLOADS"):
    os.environ.pop(key, None)

import bot              # noqa: E402
import delivery_cache as dc      # noqa: E402
import i18n             # noqa: E402
import resolvers        # noqa: E402
import shared_features  # noqa: E402
import video            # noqa: E402

logging.disable(logging.CRITICAL)

MB = 1024 * 1024
failures = []


def check(name, ok, detail=""):
    print(("  ok  " if ok else "  FAIL  ") + name + (f" -- {detail}" if not ok and detail else ""))
    if not ok:
        failures.append(name)


def run(coro):
    return asyncio.run(coro)


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


CLOCK = Clock()
REAL_FREE_MB = dc.free_mb
DEFAULTS = (dc.LIFE_SECONDS, dc.ENTRY_MAX_MB, dc.TOTAL_MAX_MB)
dc._clock = CLOCK


def reset(root=None, life=300, entry_mb=100, total_mb=400, free=10_000):
    dc.configure(str(root or SCRATCH))
    dc.wipe()
    dc._hits = dc._misses = 0
    dc.LIFE_SECONDS, dc.ENTRY_MAX_MB, dc.TOTAL_MAX_MB = life, entry_mb, total_mb
    dc.free_mb = lambda: free
    CLOCK.now = 1000.0
    if (root or SCRATCH).exists():
        for p in (root or SCRATCH).iterdir():
            shutil.rmtree(p, ignore_errors=True) if p.is_dir() else p.unlink()


def cache_dirs(root=None):
    base = pathlib.Path(root or SCRATCH) / "dbot-cache"
    return sorted(p.name for p in base.iterdir()) if base.exists() else []


def cache_files(root=None):
    base = pathlib.Path(root or SCRATCH) / "dbot-cache"
    return sorted(str(p) for p in base.rglob("*") if p.is_file()) if base.exists() else []


def outside_cache(root=None):
    """What is in the work folder besides the cache: downloads that nobody has deleted."""
    root = pathlib.Path(root or SCRATCH)
    return sorted(p.name for p in root.iterdir() if p.name != "dbot-cache")


def files(root, count=1, size=1000, name="clip", ext="mp4"):
    """New downloaded files in the work folder, as (item, path) pairs, the way _resolve_and_send holds them."""
    pairs = []
    for n in range(count):
        path = pathlib.Path(root) / f"{uuid.uuid4().hex}.{ext}"
        with open(path, "wb") as handle:
            handle.write(bytes([n % 250 + 1]) * min(size, 4096))
            handle.truncate(size)
        pairs.append((SimpleNamespace(kind="video", filename=f"{name}{n}.{ext}"), str(path)))
    return pairs


# ---------------------------------------------------------------------------
# the module on its own
# ---------------------------------------------------------------------------

def module_checks():
    work = pathlib.Path(tempfile.mkdtemp(prefix="dcache-mod-"))
    try:
        _module_checks(work)
    finally:
        shutil.rmtree(work, ignore_errors=True)
        reset()


def _module_checks(work):
    print("\n== the defaults ==")
    check("five minutes; 100 MB a delivery; 400 MB in all", DEFAULTS == (300, 100, 400), DEFAULTS)
    check("a bad number in the environment is the default, not a crash at start",
          dc._env_int("DBOT_NO_SUCH_SETTING_FOR_THIS_TEST", 7) == 7)
    os.environ["DBOT_CACHE_TEST_BAD"] = "five"
    os.environ["DBOT_CACHE_TEST_NEG"] = "-4"
    os.environ["DBOT_CACHE_TEST_ZERO"] = "0"
    check("...text is the default, a negative is 0, and 0 stays 0 (off)",
          dc._env_int("DBOT_CACHE_TEST_BAD", 300) == 300 and dc._env_int("DBOT_CACHE_TEST_NEG", 300) == 0
          and dc._env_int("DBOT_CACHE_TEST_ZERO", 300) == 0)

    print("\n== keep, take, release ==")
    reset(work)
    pairs = files(work, 2)
    originals = [p for _, p in pairs]
    contents = [pathlib.Path(p).read_bytes() for p in originals]
    dc.note_link(1, "instagram", "https://i/a")
    check("a delivered download is kept", dc.keep(1, "instagram", "https://i/a", pairs) is True)
    check("the files were moved into a folder of their own, under their own names, and are no longer where they were",
          not any(os.path.exists(p) for p in originals) and len(cache_dirs(work)) == 1
          and sorted(os.path.basename(p) for p in cache_files(work)) == sorted(os.path.basename(p) for p in originals),
          (cache_dirs(work), cache_files(work)))
    entry = dc.take(1, "instagram", "https://i/a")
    check("a hit gives the (item, path) pairs back, in order, with the same bytes",
          entry is not None and [it for it, _ in entry.items] == [it for it, _ in pairs]
          and [pathlib.Path(p).read_bytes() for _, p in entry.items] == contents)
    check("pinned for the send", entry.pins == 1)
    dc.release(entry)
    check("released", entry.pins == 0 and len(cache_files(work)) == 2)
    check("the folder is private to the bot's user where the platform has such a thing",
          os.name == "nt" or (os.stat(entry.dir).st_mode & 0o077) == 0)

    print("\n== not kept: not the latest link, nothing to keep ==")
    reset(work)
    pairs = files(work)
    check("never noted: not kept, and the files are still the caller's", dc.keep(1, "instagram", "https://i/a", pairs) is False
          and os.path.exists(pairs[0][1]) and cache_dirs(work) == [])
    dc.note_link(1, "instagram", "https://i/b")
    check("noted for another link: not kept", dc.keep(1, "instagram", "https://i/a", pairs) is False and os.path.exists(pairs[0][1]))
    check("another platform, the same url: not the same link", dc.keep(1, "tiktok", "https://i/b", pairs) is False)
    check("nothing to keep: False", dc.keep(1, "instagram", "https://i/b", []) is False)
    other = files(work)
    dc.note_link(2, "instagram", "https://i/b")
    check("another person's latest link does not count for this person",
          dc.keep(3, "instagram", "https://i/b", other) is False)

    print("\n== the latest link, and a different link dropping the entry at once ==")
    reset(work)
    a, b = files(work), files(work)
    dc.note_link(1, "instagram", "https://i/a")
    dc.note_link(1, "instagram", "https://i/b")          # B is pasted while A is still downloading
    check("A finishes after B was pasted: not kept (its files are the caller's to delete)",
          dc.keep(1, "instagram", "https://i/a", a) is False and os.path.exists(a[0][1]))
    check("B finishes: kept", dc.keep(1, "instagram", "https://i/b", b) is True)
    dc.note_link(1, "instagram", "https://i/a")
    check("link A sent again: B's files are gone from the disk at once", cache_files(work) == [] and cache_dirs(work) == [],
          cache_files(work))
    check("and there is nothing left to take", dc.take(1, "instagram", "https://i/b") is None)
    reset(work)
    a = files(work)
    dc.note_link(1, "instagram", "https://i/a")
    dc.keep(1, "instagram", "https://i/a", a)
    dc.note_link(1, "tiktok", "https://i/a")
    check("the same url on another platform is a different link", cache_files(work) == [])

    print("\n== the life: restarted by a use and by the same link, over without one ==")
    reset(work)
    dc.note_link(1, "instagram", "https://i/a")
    dc.keep(1, "instagram", "https://i/a", files(work))
    CLOCK.advance(299)
    entry = dc.take(1, "instagram", "https://i/a")
    check("at 299 s it is still there", entry is not None)
    dc.release(entry)
    CLOCK.advance(299)
    entry = dc.take(1, "instagram", "https://i/a")
    check("a use restarted the five minutes: 598 s after it was kept it is still there", entry is not None)
    dc.release(entry)
    CLOCK.advance(200)
    dc.note_link(1, "instagram", "https://i/a")
    CLOCK.advance(200)
    entry = dc.take(1, "instagram", "https://i/a")
    check("the same link sent again restarted it too: 400 s later it is still there", entry is not None)
    dc.release(entry)
    CLOCK.advance(300)
    check("exactly the life later it has expired", dc.take(1, "instagram", "https://i/a") is None)
    check("and taking it found the expired files and deleted them", cache_files(work) == [] and cache_dirs(work) == [])
    reset(work)
    dc.note_link(1, "instagram", "https://i/a")
    dc.keep(1, "instagram", "https://i/a", files(work))
    CLOCK.advance(301)
    dc.note_link(1, "instagram", "https://i/a")
    check("an expired entry is not brought back to life by the same link", cache_files(work) == [])

    print("\n== however often it is used, it goes 12 hours after it was kept ==")
    reset(work)
    dc.note_link(1, "instagram", "https://i/a")
    dc.keep(1, "instagram", "https://i/a", files(work))
    alive = True
    for _ in range(int(dc.MAX_LIFE_SECONDS // 240) - 1):     # the same link every four minutes
        CLOCK.advance(240)
        entry = dc.take(1, "instagram", "https://i/a")
        alive = alive and entry is not None
        if entry is not None:
            dc.release(entry)
    check("used every four minutes, it is still there just under 12 hours after it was kept", alive)
    check("...but its life no longer reaches past the 12 hours", dc._entries[1].expires_at <= 1000.0 + dc.MAX_LIFE_SECONDS, dc._entries[1].expires_at)
    CLOCK.now = 1000.0 + dc.MAX_LIFE_SECONDS
    dc.note_link(1, "instagram", "https://i/a")
    check("at 12 hours the same link finds nothing kept (the terms' 'within 12 hours')",
          dc.take(1, "instagram", "https://i/a") is None and cache_files(work) == [])
    check("12 hours is the most, and the five minutes are far less", dc.MAX_LIFE_SECONDS == 12 * 3600 and DEFAULTS[0] < dc.MAX_LIFE_SECONDS)

    print("\n== sweep ==")
    reset(work)
    for user in (1, 2):
        dc.note_link(user, "instagram", f"https://i/{user}")
        dc.keep(user, "instagram", f"https://i/{user}", files(work))
    CLOCK.advance(100)
    entry = dc.take(2, "instagram", "https://i/2")
    dc.release(entry)
    CLOCK.advance(250)
    dc.sweep()
    survivor = dc.take(2, "instagram", "https://i/2")
    check("an entry past its life is deleted by the sweep, one used since is not",
          len(cache_dirs(work)) == 1 and dc.take(1, "instagram", "https://i/1") is None and survivor is not None)
    dc.release(survivor)
    dc.note_link(5, "instagram", "https://i/5")
    CLOCK.advance(301)
    dc.sweep()
    check("a latest link is not forgotten when the cache's own life is over (a slow download is still waiting on it)",
          5 in dc._latest)
    dc.note_link(6, "instagram", "https://i/6")
    CLOCK.advance(dc.LATEST_KEEP_SECONDS - 1)
    dc.sweep()
    check("...nor after 59 minutes", 6 in dc._latest and 5 not in dc._latest, dict(dc._latest))
    CLOCK.advance(2)
    dc.sweep()
    check("the latest links nothing is waiting on are forgotten after an hour", 6 not in dc._latest)
    check("the hour is a named constant", dc.LATEST_KEEP_SECONDS == 3600)

    print("\n== a download that takes longer than the cache's life is still kept when it lands ==")
    reset(work)
    dc.note_link(1, "instagram", "https://i/slow")
    CLOCK.advance(dc.LIFE_SECONDS + 120)                  # it queued and downloaded for seven minutes
    dc.sweep()
    check("kept, because its link is still the person's latest",
          dc.keep(1, "instagram", "https://i/slow", files(work)) is True and len(cache_dirs(work)) == 1)
    reset(work)
    dc.note_link(1, "instagram", "https://i/slow")
    CLOCK.advance(dc.LATEST_KEEP_SECONDS + 1)
    dc.sweep()
    check("...but not after the hour", dc.keep(1, "instagram", "https://i/slow", files(work)) is False)
    reset(work)
    check("sweep takes the clock as an argument for the job queue", dc.sweep(now=CLOCK.now + 10_000) is None
          and cache_files(work) == [])

    print("\n== replacing ==")
    reset(work)
    first, second = files(work), files(work)
    dc.note_link(1, "instagram", "https://i/a")
    dc.keep(1, "instagram", "https://i/a", first)
    kept_first = cache_dirs(work)
    dc.keep(1, "instagram", "https://i/a", second)
    check("a person has one entry: keeping again replaces it and deletes the old files",
          len(cache_dirs(work)) == 1 and cache_dirs(work) != kept_first and len(cache_files(work)) == 1, cache_dirs(work))

    print("\n== the delivery cap ==")
    reset(work, entry_mb=1)
    big, small = files(work, size=MB + 1), files(work, size=MB)
    dc.note_link(1, "instagram", "https://i/a")
    check("one delivery over the cap is not kept, and its files stay the caller's",
          dc.keep(1, "instagram", "https://i/a", big) is False and os.path.exists(big[0][1]) and cache_dirs(work) == [])
    check("exactly the cap is", dc.keep(1, "instagram", "https://i/a", small) is True)
    reset(work, entry_mb=1)
    halves = files(work, count=3, size=MB // 2)
    dc.note_link(1, "instagram", "https://i/a")
    check("the cap is on all the files of a delivery together", dc.keep(1, "instagram", "https://i/a", halves) is False)

    print("\n== the total cap, least recently used out first ==")
    reset(work, total_mb=3)
    for user in (1, 2, 3):
        dc.note_link(user, "instagram", f"https://i/{user}")
        check(f"person {user}'s 1 MB is kept", dc.keep(user, "instagram", f"https://i/{user}", files(work, size=MB)) is True)
        CLOCK.advance(10)
    entry = dc.take(1, "instagram", "https://i/1")
    dc.release(entry)                           # 1 is now the most recently used; 2 is the least
    dc.note_link(4, "instagram", "https://i/4")
    check("a fourth is kept", dc.keep(4, "instagram", "https://i/4", files(work, size=MB)) is True)
    check("by sending out the least recently used (2), not the oldest kept (1)",
          dc.take(2, "instagram", "https://i/2") is None and all(dc.take(u, "instagram", f"https://i/{u}") for u in (1, 3, 4)),
          cache_dirs(work))
    check("whose files are gone from the disk", len(cache_files(work)) == 3)
    reset(work, total_mb=3)
    for user in (1, 2, 3):
        dc.note_link(user, "instagram", f"https://i/{user}")
        dc.keep(user, "instagram", f"https://i/{user}", files(work, size=MB))
    dc.note_link(4, "instagram", "https://i/4")
    huge = files(work, size=4 * MB)
    dc.ENTRY_MAX_MB = 100
    check("one that could not fit even with the whole cache gone is refused", dc.keep(4, "instagram", "https://i/4", huge) is False)
    check("and nobody was evicted for nothing", len(cache_dirs(work)) == 3)
    reset(work, total_mb=3)
    dc.note_link(1, "instagram", "https://i/1")
    dc.keep(1, "instagram", "https://i/1", files(work, size=2 * MB))
    dc.note_link(2, "instagram", "https://i/2")
    check("2 MB then 2 MB over a 3 MB cache: the first goes, the second stays",
          dc.keep(2, "instagram", "https://i/2", files(work, size=2 * MB)) is True and len(cache_dirs(work)) == 1
          and dc.take(1, "instagram", "https://i/1") is None)

    print("\n== the disk floor ==")
    reset(work, free=400)
    dc.note_link(1, "instagram", "https://i/1")
    check("nothing to evict and the disk already under the floor: not kept",
          dc.keep(1, "instagram", "https://i/1", files(work), floor_mb=444) is False and cache_dirs(work) == [])
    dc.free_mb = lambda: 444
    check("exactly the floor free: kept", dc.keep(1, "instagram", "https://i/1", files(work), floor_mb=444) is True)
    reset(work, free=420)
    for user in (1, 2, 3):
        dc.note_link(user, "instagram", f"https://i/{user}")
        dc.keep(user, "instagram", f"https://i/{user}", files(work, size=50 * MB))
        CLOCK.advance(10)
    dc.note_link(4, "instagram", "https://i/4")
    check("24 MB short of the floor: the least recently used 50 MB entry goes and then it fits",
          dc.keep(4, "instagram", "https://i/4", files(work, size=MB), floor_mb=444) is True
          and dc.take(1, "instagram", "https://i/1") is None
          and dc.take(2, "instagram", "https://i/2") is not None and dc.take(3, "instagram", "https://i/3") is not None)
    reset(work, free=100)
    for user in (1, 2):
        dc.note_link(user, "instagram", f"https://i/{user}")
        dc.keep(user, "instagram", f"https://i/{user}", files(work, size=20 * MB))
    dc.note_link(3, "instagram", "https://i/3")
    check("so short that evicting all of them still would not do: refused, and nobody was evicted for nothing",
          dc.keep(3, "instagram", "https://i/3", files(work), floor_mb=444) is False and len(cache_dirs(work)) == 2)
    dc.free_mb = lambda: None
    check("a disk that cannot be read is treated as full",
          dc.keep(3, "instagram", "https://i/3", files(work), floor_mb=444) is False)
    reset(work)

    def unreadable():
        raise AssertionError("the disk was read")
    dc.free_mb = unreadable
    dc.note_link(1, "instagram", "https://i/1")
    check("with no floor asked for, the disk is not read at all", dc.keep(1, "instagram", "https://i/1", files(work)) is True)
    dc.free_mb = REAL_FREE_MB
    got = dc.free_mb()
    check("the real reader of free space answers in whole MB, or None", got is None or (isinstance(got, int) and got > 0), got)

    print("\n== a file is never deleted while a send may be reading it ==")
    reset(work)
    dc.note_link(1, "instagram", "https://i/a")
    dc.keep(1, "instagram", "https://i/a", files(work, count=2))
    entry = dc.take(1, "instagram", "https://i/a")
    dc.drop(1)
    check("dropped while pinned: the files are still there", len(cache_files(work)) == 2)
    check("but it is no longer offered", dc.take(1, "instagram", "https://i/a") is None)
    dc.release(entry)
    check("the last release deletes them", cache_files(work) == [] and cache_dirs(work) == [], cache_files(work))
    check("a release too many does no harm", (dc.release(entry), True)[1] and entry.pins == 0)

    reset(work)
    dc.note_link(1, "instagram", "https://i/a")
    dc.keep(1, "instagram", "https://i/a", files(work))
    first = dc.take(1, "instagram", "https://i/a")
    second = dc.take(1, "instagram", "https://i/a")
    dc.drop(1)
    dc.release(first)
    check("two sends from one entry: the first to finish does not delete under the other", len(cache_files(work)) == 1)
    dc.release(second)
    check("the last does", cache_files(work) == [])

    reset(work)
    dc.note_link(1, "instagram", "https://i/a")
    dc.keep(1, "instagram", "https://i/a", files(work))
    entry = dc.take(1, "instagram", "https://i/a")
    CLOCK.advance(1000)
    dc.sweep()
    check("expired while pinned (a very slow send): the files are still there", len(cache_files(work)) == 1)
    dc.release(entry)
    check("and are deleted at the release", cache_files(work) == [])

    reset(work)
    dc.note_link(1, "instagram", "https://i/a")
    dc.keep(1, "instagram", "https://i/a", files(work))
    entry = dc.take(1, "instagram", "https://i/a")
    dc.note_link(1, "instagram", "https://i/b")
    check("a different link while a send is reading: the files wait for the release", len(cache_files(work)) == 1)
    dc.release(entry)
    check("then go", cache_files(work) == [])

    reset(work)
    dc.note_link(1, "instagram", "https://i/a")
    dc.keep(1, "instagram", "https://i/a", files(work))
    entry = dc.take(1, "instagram", "https://i/a")
    dc.keep(1, "instagram", "https://i/a", files(work))
    check("replaced while pinned: both sets are on disk", len(cache_files(work)) == 2)
    dc.release(entry)
    check("the replaced files go at the release, the new ones stay", len(cache_files(work)) == 1)

    reset(work)
    dc.note_link(1, "instagram", "https://i/a")
    dc.keep(1, "instagram", "https://i/a", files(work))
    entry = dc.take(1, "instagram", "https://i/a")
    dc.forget_user(1)
    check("erased while pinned: gone for everyone else, on disk until the send is over",
          len(cache_files(work)) == 1 and dc.take(1, "instagram", "https://i/a") is None)
    dc.release(entry)
    check("then deleted", cache_files(work) == [])

    reset(work, total_mb=2)
    dc.note_link(1, "instagram", "https://i/1")
    dc.keep(1, "instagram", "https://i/1", files(work, size=MB))
    pinned = dc.take(1, "instagram", "https://i/1")
    dc.note_link(2, "instagram", "https://i/2")
    dc.keep(2, "instagram", "https://i/2", files(work, size=MB))
    dc.note_link(3, "instagram", "https://i/3")
    check("room is made from the entries nobody is reading; the one being sent from is not touched",
          dc.keep(3, "instagram", "https://i/3", files(work, size=MB)) is True and pinned.dead is False
          and os.path.exists(pinned.items[0][1]) and dc.take(2, "instagram", "https://i/2") is None)
    dc.release(pinned)
    reset(work, total_mb=2)
    dc.note_link(1, "instagram", "https://i/1")
    dc.keep(1, "instagram", "https://i/1", files(work, size=MB))
    pinned = dc.take(1, "instagram", "https://i/1")
    dc.note_link(2, "instagram", "https://i/2")
    dc.keep(2, "instagram", "https://i/2", files(work, size=MB))
    dc.note_link(3, "instagram", "https://i/3")
    dc.take(2, "instagram", "https://i/2")                   # both are now being sent from
    check("when everything is being read there is nothing to evict: not kept, nothing deleted under a send",
          dc.keep(3, "instagram", "https://i/3", files(work, size=MB)) is False and len(cache_files(work)) == 2)
    reset(work, total_mb=2)
    dc.note_link(1, "instagram", "https://i/1")
    dc.keep(1, "instagram", "https://i/1", files(work, size=MB))
    entry = dc.take(1, "instagram", "https://i/1")
    dc.drop(1)
    dc.note_link(2, "instagram", "https://i/2")
    check("a dropped entry still being read counts against the total (it is on the disk)",
          dc.keep(2, "instagram", "https://i/2", files(work, size=2 * MB)) is False)
    dc.release(entry)

    print("\n== a move that goes wrong leaks nothing ==")
    reset(work)
    one, two = work / "one", work / "two"
    one.mkdir(), two.mkdir()
    pair_a = files(one, name="a")[0]
    pair_b = files(two, name="b")[0]
    same_a = (pair_a[0], str(one / "same.mp4"))
    os.replace(pair_a[1], same_a[1])
    same_b = (pair_b[0], str(two / "same.mp4"))
    os.replace(pair_b[1], same_b[1])
    before = (pathlib.Path(same_a[1]).read_bytes(), pathlib.Path(same_b[1]).read_bytes())
    dc.note_link(1, "instagram", "https://i/a")
    check("two files of one name are refused, not one written over the other",
          dc.keep(1, "instagram", "https://i/a", [same_a, same_b]) is False)
    check("both are where they were, untouched, and the cache folder holds nothing",
          os.path.exists(same_a[1]) and os.path.exists(same_b[1])
          and (pathlib.Path(same_a[1]).read_bytes(), pathlib.Path(same_b[1]).read_bytes()) == before
          and cache_files(work) == [] and cache_dirs(work) == [], (cache_files(work), cache_dirs(work)))
    check("and a retry with the one file works", dc.keep(1, "instagram", "https://i/a", [same_a]) is True)

    reset(work)
    pairs = files(work, count=3)
    real_replace, calls = os.replace, []

    def flaky(src, dst):
        calls.append(src)
        if len(calls) == 3:
            raise OSError("disk went away")
        return real_replace(src, dst)

    dc.note_link(1, "instagram", "https://i/a")
    os.replace = flaky
    try:
        outcome = dc.keep(1, "instagram", "https://i/a", pairs)
    finally:
        os.replace = real_replace
    check("a move failing on the third file: not kept", outcome is False)
    check("the two already moved were put back, the third never left, and no folder or file is left in the cache",
          all(os.path.exists(p) for _, p in pairs) and cache_files(work) == [] and cache_dirs(work) == [],
          (cache_files(work), cache_dirs(work)))
    check("nothing is left to take", dc.take(1, "instagram", "https://i/a") is None)

    reset(work)
    pairs = files(work, count=2)
    os.remove(pairs[1][1])
    dc.note_link(1, "instagram", "https://i/a")
    check("a file that is not there (cannot be measured): not kept, the other stays where it was, no raise",
          dc.keep(1, "instagram", "https://i/a", pairs) is False and os.path.exists(pairs[0][1]) and cache_dirs(work) == [])

    print("\n== files taken away from under it ==")
    reset(work)
    dc.note_link(1, "instagram", "https://i/a")
    dc.keep(1, "instagram", "https://i/a", files(work, count=2))
    os.remove(cache_files(work)[0])
    check("an entry whose file has gone is a miss, and is cleared", dc.take(1, "instagram", "https://i/a") is None
          and cache_files(work) == [] and cache_dirs(work) == [])

    print("\n== off: DBOT_CACHE_SECONDS=0 ==")
    reset(work, life=0)
    pairs = files(work)
    dc.note_link(1, "instagram", "https://i/a")
    check("nothing is noted, kept or taken; the files stay the caller's",
          dc.keep(1, "instagram", "https://i/a", pairs) is False and os.path.exists(pairs[0][1])
          and dc.take(1, "instagram", "https://i/a") is None and dc._latest == {} and cache_dirs(work) == [])
    check("and the counters do not move (it is not a miss, it is off)", dc.stats()["misses"] == 0 and not dc.stats()["enabled"])
    reset(work)
    dc.note_link(1, "instagram", "https://i/a")
    dc.keep(1, "instagram", "https://i/a", files(work))
    dc.LIFE_SECONDS = 0
    dc.sweep()
    check("switched off while something is kept: the next sweep clears it", cache_files(work) == [])
    dc.configure(None)
    dc.LIFE_SECONDS = 300
    check("no work folder is off too", not dc.enabled() and dc.keep(1, "instagram", "https://i/a", files(work)) is False)

    print("\n== wipe, drop, forget, stats ==")
    reset(work)
    for user in (1, 2):
        dc.note_link(user, "instagram", f"https://i/{user}")
        dc.keep(user, "instagram", f"https://i/{user}", files(work))
    stray = work / "dbot-cache" / "orphan"
    stray.mkdir()
    (stray / "left-by-a-process-that-is-gone.mp4").write_bytes(b"x")
    dc.wipe()
    check("wipe deletes everything in the cache folder, orphans too, and forgets all state",
          not (work / "dbot-cache").exists() and dc._entries == {} and dc._latest == {} and dc._dead == [])
    check("and the cache works again afterwards", (dc.note_link(1, "instagram", "https://i/1"),
                                                   dc.keep(1, "instagram", "https://i/1", files(work)))[1] is True)
    dc.drop(1)
    check("drop(user) removes their entry now", cache_files(work) == [])
    dc.drop(99)
    dc.forget_user(99)
    check("dropping or forgetting someone with nothing is fine", True)
    dc.note_link(7, "instagram", "https://i/7")
    dc.forget_user(7)
    check("forget_user also makes a download still in flight unkeepable (their latest link is gone)",
          dc.keep(7, "instagram", "https://i/7", files(work)) is False)
    reset(work)
    dc.note_link(1, "instagram", "https://i/1")
    dc.keep(1, "instagram", "https://i/1", files(work, size=1500))
    dc.take(1, "instagram", "https://i/1")
    dc.take(1, "instagram", "https://i/2")
    stats = dc.stats()
    check("stats are counts: one kept, its bytes, one hit, one miss",
          stats == {"enabled": True, "entries": 1, "bytes": 1500, "hits": 1, "misses": 1}, stats)
    reset(work)

    make_room_checks(work)
    shared_sender_checks(work)
    setting_checks()
    log_checks(work)


def make_room_checks(work):
    """A download that needs the disk deletes kept files first (least recently used first), so the
    cache gives way instead of making that download refuse for space."""
    print("\n== make_room: a download that needs the disk, and the cache gives way ==")
    state = {"free": 0}

    def hold(users):
        """Each user keeps a one-MB delivery; user 1 is the least recently used."""
        for user in users:
            dc.note_link(user, "instagram", f"https://i/{user}")
            assert dc.keep(user, "instagram", f"https://i/{user}", files(work, size=MB))
            CLOCK.advance(1)

    def disk(extra_mb=0):
        # a disk whose free space grows by what the cache deletes, as a real one does
        dc.free_mb = lambda: state["free"] + extra_mb + (before - dc.stats()["bytes"]) // MB

    reset(work)
    hold((1, 2, 3))
    before = dc.stats()["bytes"]
    state["free"] = 100
    disk()
    dc.make_room(10, 50)
    check("a disk that already has the room (10 MB + 50 MB left over): nothing is deleted",
          dc.stats()["entries"] == 3, dc.stats())
    state["free"] = 58
    dc.make_room(10, 50)          # needs 60, has 58: two one-MB deliveries have to go, the oldest first
    check("short by 2 MB: the least recently used goes first, and only as many as are needed",
          dc.stats()["entries"] == 1 and 3 in dc._entries and 1 not in dc._entries and 2 not in dc._entries,
          sorted(dc._entries))
    check("and its files are deleted from the disk", len(cache_dirs(work)) == 1, cache_dirs(work))

    reset(work)
    hold((1, 2, 3))
    before = dc.stats()["bytes"]
    state["free"] = 59
    disk()
    entry = dc.take(1, "instagram", "https://i/1")             # user 1 is now the most recently used
    dc.release(entry)
    dc.make_room(10, 50)
    check("a delivery that was used lately outlives an older one",
          1 in dc._entries and 2 not in dc._entries and 3 in dc._entries, sorted(dc._entries))

    reset(work)
    hold((1, 2))
    before = dc.stats()["bytes"]
    state["free"] = 0
    disk()
    pinned = dc.take(1, "instagram", "https://i/1")
    dc.make_room(10_000, 300)
    check("when more is needed than everything held could give: all that can be deleted is, and no more",
          dc.stats()["entries"] == 1 and 1 in dc._entries, sorted(dc._entries))
    check("a delivery that is being sent from is never deleted under the send",
          pinned.pins == 1 and all(os.path.exists(p) for _, p in pinned.items))
    dc.release(pinned)

    reset(work)
    hold((1,))
    dc.free_mb = lambda: None
    dc.make_room(10_000, 300)
    check("a disk whose free space cannot be read: nothing is deleted", dc.stats()["entries"] == 1)
    reset(work, life=0)
    dc.free_mb = lambda: 0
    dc.make_room(10_000, 300)
    check("the cache off: nothing to do, and no error", dc.stats()["entries"] == 0)
    reset(work)
    hold((1,))
    dc.free_mb = lambda: (_ for _ in ()).throw(RuntimeError("secret path C:/x"))
    try:
        dc.make_room(10_000, 300)
        raised = False
    except Exception:
        raised = True
    check("it never raises into a download", raised is False)
    reset(work)


def shared_sender_checks(work):
    print("\n== ids that stand for many people are never cached, taken or noted ==")
    check("the three ids: the anonymous group admin, a message sent as a channel, Telegram's service account",
          dc.SHARED_SENDER_IDS == {1087968824, 136817688, 777000}, dc.SHARED_SENDER_IDS)
    for sender in sorted(dc.SHARED_SENDER_IDS):
        reset(work)
        dc.note_link(sender, "instagram", "https://i/shared")
        check(f"{sender}: nothing is noted", sender not in dc._latest, dict(dc._latest))
        dc._latest[sender] = (("instagram", "https://i/shared"), CLOCK())     # even if something had noted it
        pairs = files(work)
        kept = dc.keep(sender, "instagram", "https://i/shared", pairs)
        check(f"{sender}: nothing is kept, and the files stay the caller's",
              kept is False and cache_dirs(work) == [] and all(os.path.exists(p) for _, p in pairs))
        dc._entries[sender] = dc.Entry(sender, ("instagram", "https://i/shared"), [], "x", 0, 1e9, 1.0)
        check(f"{sender}: nothing is taken", dc.take(sender, "instagram", "https://i/shared") is None)
        dc._entries.pop(sender)
        dc._latest.pop(sender, None)
        check(f"{sender}: and it is not counted as a miss", dc.stats()["misses"] == 0, dc.stats())
    reset(work)
    dc.note_link(1087968825, "instagram", "https://i/x")
    check("the id next to one of them is an ordinary person", 1087968825 in dc._latest)
    reset(work)


def setting_checks():
    print("\n== DBOT_CACHE_SECONDS: what cannot be read means off ==")
    import importlib.util

    def life_with(value, name="DBOT_CACHE_SECONDS"):
        saved = os.environ.get(name)
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value
        try:
            spec = importlib.util.spec_from_file_location("delivery_cache_fresh", dc.__file__)
            module = importlib.util.module_from_spec(spec)
            sys.modules["delivery_cache_fresh"] = module      # a dataclass looks its own module up
            try:
                spec.loader.exec_module(module)
            finally:
                sys.modules.pop("delivery_cache_fresh", None)
            return module
        finally:
            if saved is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = saved

    for value, wanted in ((None, 300), ("", 300), ("120", 120), ("0", 0), ("off", 0), ("five minutes", 0), ("2.5", 0),
                          ("-30", 0), ("-0", 0)):
        got = life_with(value).LIFE_SECONDS
        check(f"DBOT_CACHE_SECONDS={value!r}: {wanted}", got == wanted, got)
    check("a cache whose life reads as 0 is off", life_with("off").enabled() is False)
    check("the sizes keep their defaults when they cannot be read (only the life means off)",
          life_with("off", "DBOT_CACHE_ENTRY_MAX_MB").ENTRY_MAX_MB == 100
          and life_with("off", "DBOT_CACHE_MAX_MB").TOTAL_MAX_MB == 400)


def log_checks(work):
    """Logs say what kind of failure it was, never where: a path in a log line is a person's download."""
    print("\n== the module logs the kind of failure and no path ==")
    records = []

    class Catch(logging.Handler):
        def emit(self, record):
            records.append((record.getMessage(), bool(record.exc_info)))

    handler = Catch()
    logging.disable(logging.NOTSET)
    root = logging.getLogger()
    kept_handlers, kept_level = root.handlers[:], root.level
    root.handlers, root.level = [handler], logging.DEBUG
    real_rmtree = shutil.rmtree
    try:
        reset(work)
        dc.note_link(1, "instagram", "https://i/a")
        gone = str(work / "secret-folder" / "gone.mp4")
        check("keeping files that vanished fails quietly", dc.keep(1, "instagram", "https://i/a",
                                                                   [(SimpleNamespace(kind="video"), gone)]) is False)

        def refuse(path, *args, **kwargs):
            raise PermissionError(13, "denied", str(work / "secret-folder"))

        shutil.rmtree = refuse
        dc.note_link(2, "instagram", "https://i/b")
        dc.keep(2, "instagram", "https://i/b", files(work))
        dc.drop(2)
        shutil.rmtree = real_rmtree
    finally:
        shutil.rmtree = real_rmtree
        root.handlers, root.level = kept_handlers, kept_level
        logging.disable(logging.CRITICAL)
    text = "\n".join(message for message, _ in records)
    check("something was logged for each", len(records) == 2, records)
    check("no path, no folder name, no error text", "secret-folder" not in text and "gone.mp4" not in text
          and str(work) not in text and "denied" not in text, text)
    check("only the kind of failure", "FileNotFoundError" in text and "PermissionError" in text, text)
    check("and no traceback is attached to any of them", not any(attached for _, attached in records), records)
    reset(work)


# ---------------------------------------------------------------------------
# the real handlers
# ---------------------------------------------------------------------------

class World:
    def __init__(self, kinds=("video",), payload=b"BYTES-OF-THE-CLIP", send_fails=False, free=10_000, lossless=False):
        self.kinds = list(kinds)
        self.payload, self.send_fails, self.free, self.lossless = payload, send_fails, free, lossless
        self.resolves, self.downloads, self.sent, self.shown, self.texts, self.jobs, self.calls = [], [], [], [], [], [], []
        self.deleted = 0
        self.spawned = []
        self.during_send = []
        self.free_reads = 0
        self.busy_entered = 0
        self.allowance = {"allowed": True}
        self.answers = []


W = World()


class Message:
    def _record(self, how, handle, kwargs, **more):
        if W.send_fails:
            raise W.send_fails if isinstance(W.send_fails, Exception) else RuntimeError("Telegram said no")
        W.during_send.append((bot._slots()._value, bot.MAX_CONCURRENT_DOWNLOADS, W.busy_entered))
        W.sent.append(dict(more, how=how, data=handle.read() if handle is not None else None, kw=kwargs))

    async def reply_document(self, document, *args, **kwargs):
        self._record("document", document, kwargs, filename=kwargs.get("filename"))

    async def reply_video(self, video_, *args, **kwargs):
        self._record("video", video_, kwargs)

    async def reply_photo(self, photo, *args, **kwargs):
        self._record("photo", photo, kwargs)

    async def reply_media_group(self, group, **kwargs):
        if W.send_fails:
            raise W.send_fails if isinstance(W.send_fails, Exception) else RuntimeError("Telegram said no")
        W.during_send.append((bot._slots()._value, bot.MAX_CONCURRENT_DOWNLOADS, W.busy_entered))
        W.sent.append({"how": "group", "kinds": [type(m).__name__ for m in group],
                       "data": [m.args[0].read() for m in group],
                       "names": [m.kwargs.get("filename") for m in group]})

    async def reply_text(self, text, **kwargs):
        W.texts.append((text, kwargs.get("reply_markup")))


class Status:
    async def set(self, bot_, text, **kwargs):
        W.shown.append(text)
        return self

    async def delete(self, *args, **kwargs):
        W.deleted += 1


class FakeLive:
    @classmethod
    async def reply_to(cls, message, text, **kwargs):
        W.shown.append(text)
        return Status()


@asynccontextmanager
async def counting_busy(*args, **kwargs):
    W.busy_entered += 1
    yield


async def english(user_id, context=None):
    return "en"


async def fake_resolve(platform, url, max_mb=None):
    W.resolves.append((platform, url))
    items = []
    for n, kind in enumerate(W.kinds):
        ext = "mp4" if kind == "video" else "jpg"
        items.append(resolvers.MediaItem(kind, url=f"https://cdn.example/{uuid.uuid4().hex}/{n}", filename=f"clip{n}.{ext}"))
    return resolvers.Resolved(platform, "fake", items)


async def fake_download(item, work_dir=None, max_mb=None):
    if item.path:
        return item.path
    W.downloads.append(item.url)
    path = SCRATCH / f"{uuid.uuid4().hex}.{resolvers._ext_of(item.filename, 'jpg')}"
    path.write_bytes(W.payload + item.filename.encode())
    item.path = str(path)
    return str(path)


async def nothing(*args, **kwargs):
    return None


async def no_probe(path, run=None):
    return None


async def no_refusal(*args, **kwargs):
    return None


def _free():
    W.free_reads += 1
    return W.free


def _allowance(user_id):
    W.calls.append("allowance")
    return W.allowance


def _record(user_id, platform):
    W.calls.append(("record", platform))


def _spawn(context, update, work, in_flight=None):
    W.calls.append("spawn")

    async def run():
        # What the real _spawn_download does with the mark handle_link made: the
        # next copy of the link is a job of its own once this one has ended.
        try:
            await work()
        finally:
            if in_flight is not None:
                bot._in_flight.pop(in_flight, None)

    W.spawned.append(run)


resolvers.resolve, resolvers.download = fake_resolve, fake_download
video.probe = no_probe
bot.LiveMessage = FakeLive
bot.lifecycle.busy = counting_busy
bot.get_caption_enabled = lambda user_id: False
bot.get_lossless_enabled = lambda user_id: W.lossless
bot.note_job = lambda ok: W.jobs.append(ok)
bot._after_send = nothing
bot._free_mb = _free
bot.download_allowance = _allowance
bot.record_download = _record
bot._spawn_download = _spawn
bot.refuse_new_work = no_refusal
i18n.get_lang = english

USER, OTHER = 7, 8
MESSAGE = Message()
LINK_A = bot.platforms.detect_platform("https://www.instagram.com/reel/AAAAAAAAAAA/")[1]
LINK_B = bot.platforms.detect_platform("https://www.instagram.com/reel/BBBBBBBBBBB/")[1]


def update_with(user=USER, text=None, query=None):
    message = SimpleNamespace(text=text, reply_text=MESSAGE.reply_text, reply_document=MESSAGE.reply_document,
                              reply_video=MESSAGE.reply_video, reply_photo=MESSAGE.reply_photo,
                              reply_media_group=MESSAGE.reply_media_group)
    return SimpleNamespace(callback_query=query, effective_user=SimpleNamespace(id=user),
                           effective_chat=SimpleNamespace(id=user), effective_message=message, message=message)


def new_context():
    return SimpleNamespace(bot=SimpleNamespace(), user_data={}, application=SimpleNamespace())


async def paste(link, context, user=USER):
    await bot.handle_link(update_with(user=user, text=link), context)


async def run_work():
    while W.spawned:
        await W.spawned.pop(0)()


async def tap(context, token=None, user=USER, data=None):
    token = token or list(context.user_data["redeliver"])[-1]
    answers = []

    async def answer(text=None, **kw):
        answers.append((text, kw))

    async def edit(reply_markup=None):
        return None

    query = SimpleNamespace(data=data or f"redo:{token}", message=MESSAGE, answer=answer, edit_message_reply_markup=edit)
    await bot.redeliver_callback(update_with(user=user, query=query), context)
    return answers


def tokens(context):
    return list(context.user_data.get("redeliver", {}))


def fresh(**kw):
    global W
    W = World(**kw)
    reset(SCRATCH)
    for p in list(SCRATCH.iterdir()):
        shutil.rmtree(p, ignore_errors=True) if p.is_dir() else p.unlink()
    return W


def bot_checks():
    run(_bot_checks())
    run(_room_and_shared_checks())
    run(_tap_checks())


async def _tap_checks():
    """The numbers that decide how long the cache should keep a download (shared_features.note_tap,
    counts only): which way people redo, how long after the delivery, whether the cache answered, whether a
    delivery was kept, whether the same link came back. Nothing about who."""
    sf = shared_features
    seen = set()

    def counted():
        got = dict(sf._taps)
        seen.update(got)
        sf.take_taps()
        return got

    print("\n== counted, and nothing about who: the redo button ==")
    now = [1_000_000.0]
    real_time = bot.time
    bot.time = SimpleNamespace(time=lambda: now[0], monotonic=real_time.monotonic, sleep=real_time.sleep)
    try:
        edges = ((0, "lt1m"), (59.9, "lt1m"), (60, "1-2m"), (119.9, "1-2m"), (120, "2-5m"), (299.9, "2-5m"),
                 (300, "5-15m"), (899.9, "5-15m"), (900, "15-60m"), (3599.9, "15-60m"), (3600, "gt60m"),
                 (86400 * 3, "gt60m"), (-30, "lt1m"))
        wrong = [(seconds, bot._redo_delay_key({"at": now[0] - seconds}), f"redo.after.{name}")
                 for seconds, name in edges if bot._redo_delay_key({"at": now[0] - seconds}) != f"redo.after.{name}"]
        check("the delay buckets, at every edge (a bucket is [from, to): 60 s is 1-2m, 3600 s is over an hour)",
              not wrong, wrong)
        odd = [at for at in (None, "x", True, [], {}) if bot._redo_delay_key({"at": at}) != "redo.after.unknown"]
        check("no usable 'at' (saved by an older version): unknown", not odd and
              bot._redo_delay_key({}) == "redo.after.unknown", odd)

        fresh()
        counted()
        ctx = new_context()
        await paste(LINK_A, ctx)
        await run_work()
        token = tokens(ctx)[-1]
        check("a delivery remembers when it was made, as a number (it is saved as JSON)",
              ctx.user_data["redeliver"][token]["at"] == now[0], ctx.user_data["redeliver"][token])
        check("a first delivery counts that the cache kept it, and nothing about the link or the person",
              counted() == {"cache.kept": 1})
        now[0] += 130
        await tap(ctx)
        await run_work()
        check("the button, 130 s later, to a file: its direction, its delay bucket, and that the cache answered it",
              counted() == {"redo.to_file": 1, "redo.after.2-5m": 1, "redo.cache.hit": 1})
        now[0] += 3700
        CLOCK.advance(400)                                   # the kept files have run out
        await tap(ctx)
        await run_work()
        check("the new button, an hour later, back to compressed: the other direction, the last bucket, a miss, "
              "and the delivery it then made was kept",
              counted() == {"redo.to_compressed": 1, "redo.after.gt60m": 1, "redo.cache.miss": 1, "cache.kept": 1})
        await tap(ctx, token=token)
        check("the first button again (it was used): 'double', and nothing else",
              counted() == {"redo.spent": 1})
        await tap(ctx, token="0123456789")
        check("a button this bot has no memory of (a restart, or too old): 'expired', and nothing else",
              counted() == {"redo.expired": 1})

        fresh()
        counted()
        ctx = new_context()
        W.lossless = True
        await paste(LINK_A, ctx)
        await run_work()
        counted()
        await tap(ctx)
        check("a delivery sent as a file: the button goes to compressed", counted().get("redo.to_compressed") == 1)

        fresh()
        counted()
        ctx = new_context()
        await paste(LINK_A, ctx)
        await run_work()
        counted()
        del ctx.user_data["redeliver"][tokens(ctx)[-1]]["at"]
        await tap(ctx)
        check("a delivery with no time in it: 'unknown'", counted().get("redo.after.unknown") == 1)

        fresh()
        counted()
        ctx = new_context()
        await paste(LINK_A, ctx)
        await run_work()
        counted()

        async def merge_tap(token):
            query = SimpleNamespace(data=f"merge:pdf:{token}", message=MESSAGE, answer=nothing,
                                    edit_message_reply_markup=nothing)
            await bot.merge_callback(update_with(query=query), ctx)

        await merge_tap("0123456789")
        check("a merge button this bot has no memory of: 'merge.expired'", counted() == {"merge.expired": 1})

        print("\n== counted: the cache, and the same link again ==")
        fresh()
        counted()
        ctx = new_context()
        await paste(LINK_A, ctx)
        await run_work()
        check("a pasted link's first fetch is not a cache look-up: only that it was kept", counted() == {"cache.kept": 1})
        await paste(LINK_A, ctx)
        await run_work()
        check("the same link again, while the delivery is kept: 'cached' (and not counted as a button's look-up)",
              counted() == {"link.again.cached": 1})
        CLOCK.advance(400)
        await paste(LINK_A, ctx)
        await run_work()
        check("the same link again after the five minutes: 'not cached', and the fetch kept again",
              counted() == {"link.again.not_cached": 1, "cache.kept": 1})
        await paste(LINK_B, ctx)
        await run_work()
        await paste(LINK_A, ctx)
        await run_work()
        check("a different link, then the first: no 'again' (the latest link was the other one)",
              set(counted()) == {"cache.kept"})

        fresh()
        counted()
        dc.ENTRY_MAX_MB = 0
        ctx = new_context()
        await paste(LINK_A, ctx)
        await run_work()
        check("a delivery the cache could not keep: 'not kept'", counted() == {"cache.not_kept": 1})
        dc.ENTRY_MAX_MB = 100

        fresh()
        counted()
        dc.LIFE_SECONDS = 0
        ctx = new_context()
        await paste(LINK_A, ctx)
        await run_work()
        await paste(LINK_A, ctx)
        await run_work()
        await tap(ctx)
        await run_work()
        got = counted()
        check("the cache off: no cache or same-link number is counted; the button's own still are",
              not [k for k in got if k.startswith(("cache.", "link.again.", "redo.cache."))]
              and got.get("redo.to_file") == 1, got)
        dc.LIFE_SECONDS = 300

        for sender in sorted(dc.SHARED_SENDER_IDS):
            fresh()
            counted()
            ctx = new_context()
            await paste(LINK_A, ctx, user=sender)
            await run_work()
            await paste(LINK_A, ctx, user=sender)
            await run_work()
            await tap(ctx, user=sender)
            await run_work()
            got = counted()
            check(f"{sender}, who is many people: no cache or same-link number is counted for it",
                  not [k for k in got if k.startswith(("cache.", "link.again.", "redo.cache."))], got)
    finally:
        bot.time = real_time
        dc.LIFE_SECONDS, dc.ENTRY_MAX_MB = 300, 100

    print("\n== counted: nothing about who ==")
    bad = [key for key in seen if not re.fullmatch(r"[a-z0-9_]+(\.[a-z0-9_<>-]+)+", key)]
    check("every key these flows made is a dotted name", not bad, bad)
    forbidden = (str(USER), str(OTHER), "instagram", "AAAAAAAAAAA", "http", "0123456789", "7")
    leaked = [key for key in seen if any(word in key for word in forbidden[:-1])]
    check("none holds an id, a platform, a link or a token", not leaked, leaked)
    source = inspect.getsource(bot.redeliver_callback) + inspect.getsource(bot._resolve_and_send) \
        + inspect.getsource(bot.handle_link) + inspect.getsource(bot.merge_callback)
    calls = re.findall(r"note_tap\(([^)]*)\)", source)
    check("and every note_tap in the downloader's handlers is given a name written out (or a choice between two), "
          "never a value from the person",
          calls and all(set(re.findall(r"[A-Za-z_]+", re.sub(r'"[^"]*"', "", call)))
                        <= {"if", "else", "remembered", "get", "cached", "is", "not", "None", "kept", "_redo_delay_key"}
                        for call in calls), calls)

async def _room_and_shared_checks():
    print("\n== a sender that is many people is delivered to and keeps nothing ==")
    for sender in sorted(dc.SHARED_SENDER_IDS):
        fresh()
        ctx = new_context()
        await paste(LINK_A, ctx, user=sender)
        await run_work()
        check(f"{sender}: delivered as ever, and nothing kept or left behind",
              len(W.sent) == 1 and W.sent[0]["how"] == "video" and cache_files() == [] and outside_cache() == []
              and dc.stats()["entries"] == 0 and sender not in dc._latest, (W.sent, cache_files(), outside_cache()))
        await tap(ctx, user=sender)
        await run_work()
        check(f"{sender}: and its button fetches again, as before the cache",
              len(W.resolves) == 2 and W.sent[-1]["how"] == "document", W.resolves)

    print("\n== a large download, and a merge, make room first ==")
    real_resolve = resolvers.resolve
    real_max = bot.large_files.LARGE_MAX_MB
    saved = (bot.large_files.available, bot.get_large_files_enabled, bot.family_link.star_balance,
             bot.family_link.spend_stars, bot.family_link.move_stars)
    seen = []

    async def spy_resolve(platform, url, max_mb=None):
        seen.append((max_mb, dc.stats()["entries"]))
        return await real_resolve(platform, url, max_mb=max_mb)

    resolvers.resolve = spy_resolve
    bot.large_files.LARGE_MAX_MB = 2000
    bot.large_files.available = lambda chat_id: True
    bot.get_large_files_enabled = lambda user_id: True
    bot.family_link.star_balance = lambda user_id: 10 ** 6
    bot.family_link.spend_stars = lambda *args: 5
    bot.family_link.move_stars = lambda *args: 5
    try:
        floor = bot.MIN_FREE_MB + 2000
        for free, kept_after, what in ((floor, 1, "exactly the room it needs"), (floor - 1, 0, "one MB short")):
            fresh()
            ctx = new_context()
            await paste(LINK_A, ctx)
            await run_work()
            seen.clear()
            dc.free_mb = lambda free=free: free
            ok = await bot._large_download(update_with(), ctx, Status(), "en", "instagram", LINK_B)
            check(f"a large download with {what} ({free} MB free): "
                  + ("the kept delivery stays" if kept_after else "the kept delivery is gone before anything is fetched"),
                  ok is True and seen == [(2000, kept_after)] and len(W.sent) == 2, (ok, seen, len(W.sent)))
            check("  ...and it fetched with the large ceiling and delivered", W.sent[-1]["how"] == "video", W.sent[-1:])

        fresh()
        ctx = new_context()
        await paste(LINK_A, ctx)
        await run_work()
        seen.clear()
        pinned = dc.take(USER, "instagram", LINK_A)            # a send is reading it right now
        dc.free_mb = lambda: 0
        await bot._large_download(update_with(), ctx, Status(), "en", "instagram", LINK_B)
        dc.release(pinned)
        check("a kept delivery that a send is reading is not deleted for it", seen == [(2000, 1)] and len(cache_files()) == 1,
              (seen, cache_files()))

        fresh()
        bot.large_files.available = lambda chat_id: False       # refused: private chats only
        ctx = new_context()
        await paste(LINK_A, ctx)
        await run_work()
        dc.free_mb = lambda: 0
        await bot._large_download(update_with(user=OTHER), ctx, Status(), "en", "instagram", LINK_B)
        check("a person who is refused costs nobody their kept files", dc.stats()["entries"] == 1, dc.stats())
        bot.large_files.available = lambda chat_id: True

        merge_floor = bot.MIN_FREE_MB + bot.MERGE_MAX_MB * 2
        for free, kept_after, what in ((merge_floor, 1, "exactly the room it needs"), (merge_floor - 1, 0, "one MB short")):
            fresh(kinds=("photo", "photo"))
            ctx = new_context()
            await paste(LINK_A, ctx)
            await run_work()
            seen.clear()
            dc.free_mb = lambda free=free: free
            ok = await bot._merge_and_send(update_with(), ctx, "zip", "instagram", LINK_A)
            check(f"a merge with {what} ({free} MB free, for its inputs and its output): "
                  + ("the kept delivery stays" if kept_after else "the kept delivery is gone before it fetches"),
                  ok is True and seen == [(None, kept_after)], (ok, seen))
    finally:
        resolvers.resolve = real_resolve
        bot.large_files.LARGE_MAX_MB = real_max
        (bot.large_files.available, bot.get_large_files_enabled, bot.family_link.star_balance,
         bot.family_link.spend_stars, bot.family_link.move_stars) = saved
        dc.free_mb = lambda: 10_000
    large, merging = inspect.getsource(bot._large_download), inspect.getsource(bot._merge_and_send)
    check("in the source: before the large fetch, and before the merge looks at the disk",
          large.index("_make_room(large_files.LARGE_MAX_MB)") < large.index("resolvers.resolve")
          and merging.index("_make_room(MERGE_MAX_MB * 2)") < merging.index("_free_mb()")
          < merging.index("resolvers.resolve"))
    check("and the merge still does not use the kept files", "delivery_cache.take" not in merging)


async def _bot_checks():
    print("\n== a download, then the button ==")
    fresh()
    ctx = new_context()
    await paste(LINK_A, ctx)
    check("the link was noted before anything else: the person's latest link is this one",
          dc._latest.get(USER, (None,))[0] == ("instagram", LINK_A))
    await run_work()
    first = W.sent[0] if W.sent else {}
    check("delivered as a video the first time", first.get("how") == "video" and W.resolves == [("instagram", LINK_A)],
          (W.sent, W.resolves))
    check("and kept: the files are in the cache folder and no longer among the downloads",
          len(cache_files()) == 1 and outside_cache() == [], (cache_files(), outside_cache()))
    check("the cache holds it for this link", dc.stats()["entries"] == 1)
    answers = await tap(ctx)
    check("the button answers the query at once", answers == [(None, {})], answers)
    await run_work()
    second = W.sent[1] if len(W.sent) > 1 else {}
    check("the second send is the other form: a document", second.get("how") == "document", W.sent)
    check("with the very same bytes", second.get("data") == first.get("data") and first.get("data"),
          (first.get("data"), second.get("data")))
    check("under the same name, and not telling Telegram to sniff it into a video",
          second.get("filename") == "clip0.mp4" and second["kw"].get("disable_content_type_detection") is True, second)
    check("and nothing was fetched a second time: one resolve, one download",
          W.resolves == [("instagram", LINK_A)] and len(W.downloads) == 1, (W.resolves, W.downloads))
    check("the files are still kept (the button under that document can use them too)",
          len(cache_files()) == 1 and dc.stats()["entries"] == 1)
    check("it counted as a download, as a fetch would have",
          W.calls.count(("record", "instagram")) == 2, W.calls)
    check("the delivery is remembered under a new token with the form actually sent",
          len(tokens(ctx)) == 2 and ctx.user_data["redeliver"][tokens(ctx)[-1]]["lossless"] is True
          and ctx.user_data["redeliver"][tokens(ctx)[0]]["lossless"] is False)
    await tap(ctx)
    await run_work()
    third = W.sent[2] if len(W.sent) > 2 else {}
    check("the button under the document gives the video again, from the same files",
          third.get("how") == "video" and third.get("data") == first.get("data") and len(W.resolves) == 1,
          (W.sent, W.resolves))
    check("the status message was deleted each time and the job counted as done", W.deleted == 3 and W.jobs == [True] * 3,
          (W.deleted, W.jobs))
    check("the hits are counted", dc.stats()["hits"] == 2, dc.stats())

    print("\n== a photo, an album ==")
    fresh(kinds=("photo",))
    ctx = new_context()
    await paste(LINK_A, ctx)
    await run_work()
    await tap(ctx)
    await run_work()
    check("a photo then the button: a photo, then a document of the same bytes, one fetch",
          [s["how"] for s in W.sent] == ["photo", "document"] and W.sent[0]["data"] == W.sent[1]["data"]
          and len(W.resolves) == 1 and W.sent[1]["filename"] == "clip0.jpg", W.sent)
    fresh(kinds=("photo", "video", "photo"))
    ctx = new_context()
    await paste(LINK_A, ctx)
    await run_work()
    await tap(ctx)
    await run_work()
    album, again = (W.sent + [{}, {}])[:2]
    check("an album then the button: the album, then the album as documents, in order, with the same bytes",
          album.get("how") == "group" and again.get("how") == "group"
          and album["kinds"] == ["InputMediaPhoto", "InputMediaVideo", "InputMediaPhoto"]
          and again["kinds"] == ["InputMediaDocument"] * 3 and again["data"] == album["data"]
          and again["names"] == ["clip0.jpg", "clip1.mp4", "clip2.jpg"], W.sent)
    check("one fetch, three files kept", len(W.resolves) == 1 and len(cache_files()) == 3, (W.resolves, cache_files()))
    await tap(ctx)
    await run_work()
    check("and the button on that album goes back to the compressed form, still without fetching",
          len(W.sent) == 3 and W.sent[2]["kinds"] == ["InputMediaPhoto", "InputMediaVideo", "InputMediaPhoto"]
          and len(W.resolves) == 1, W.sent[2:])

    print("\n== the same link again, within the life and after it ==")
    fresh()
    ctx = new_context()
    await paste(LINK_A, ctx)
    await run_work()
    CLOCK.advance(120)
    await paste(LINK_A, ctx)
    await run_work()
    check("pasted again two minutes later: sent again, nothing fetched", len(W.sent) == 2 and len(W.resolves) == 1
          and W.sent[1]["data"] == W.sent[0]["data"] and W.sent[1]["how"] == "video", (W.sent, W.resolves))
    check("it counted as a download: every paste does", W.calls.count(("record", "instagram")) == 2)
    CLOCK.advance(250)
    await paste(LINK_A, ctx)
    await run_work()
    check("250 s after that use (370 after the first) it is still there: each use restarted the five minutes",
          len(W.resolves) == 1 and len(W.sent) == 3, (W.resolves, len(W.sent)))
    CLOCK.advance(301)
    await paste(LINK_A, ctx)
    await run_work()
    check("five minutes and a second after the last use: fetched again", len(W.resolves) == 2 and len(W.sent) == 4,
          (W.resolves, len(W.sent)))
    check("and kept again", dc.stats()["entries"] == 1 and len(cache_files()) == 1 and outside_cache() == [])
    CLOCK.advance(10)
    await paste(LINK_A, new_context())
    await run_work()
    check("(a different conversation of the same person is the same person)", len(W.resolves) == 2)
    await paste(LINK_A, new_context(), user=OTHER)
    await run_work()
    check("another person pasting the same link gets a fetch of their own: the cache is per person",
          len(W.resolves) == 3, W.resolves)

    print("\n== a different link ==")
    fresh()
    ctx = new_context()
    await paste(LINK_A, ctx)
    await run_work()
    check("A is kept", len(cache_files()) == 1)
    await paste(LINK_B, ctx)
    check("the moment B is pasted -- before its download has even started -- A's files are gone from the disk",
          cache_files() == [] and cache_dirs() == [] and dc.stats()["entries"] == 0 and W.resolves == [("instagram", LINK_A)],
          (cache_files(), W.resolves))
    await run_work()
    check("B is fetched and kept", len(W.resolves) == 2 and dc.stats()["entries"] == 1)
    await paste(LINK_A, ctx)
    await run_work()
    check("going back to A is a fetch again", len(W.resolves) == 3)

    print("\n== A pasted, then B while A is still downloading ==")
    fresh()
    ctx = new_context()
    await paste(LINK_A, ctx)
    await paste(LINK_B, ctx)
    work_a, work_b = W.spawned
    W.spawned.clear()
    await work_a()
    check("A finishes: delivered, but not kept; its files are deleted",
          len(W.sent) == 1 and cache_files() == [] and outside_cache() == [] and dc.stats()["entries"] == 0,
          (cache_files(), outside_cache()))
    await work_b()
    check("B finishes: delivered and kept", len(W.sent) == 2 and len(cache_files()) == 1 and outside_cache() == [])
    check("and it is B that is kept", dc.take(USER, "instagram", LINK_B) is not None)

    print("\n== the button taps are links too ==")
    fresh()
    ctx = new_context()
    await paste(LINK_A, ctx)
    await run_work()
    token_a = tokens(ctx)[-1]
    await paste(LINK_B, ctx)
    await run_work()
    await tap(ctx, token=token_a)
    check("tapping the button of an older download makes that link the latest, and B's files go at once",
          dc._latest[USER][0] == ("instagram", LINK_A) and cache_files() == [], (dc._latest.get(USER), cache_files()))
    await run_work()
    check("and A is fetched (it was not kept), delivered and kept from then on",
          len(W.resolves) == 3 and len(cache_files()) == 1)
    expired = await tap(ctx, data="redo:nothere")
    check("a button with an unknown token still answers 'expired' and leaves the cache alone",
          expired and expired[0][1] == {"show_alert": True} and len(cache_files()) == 1, expired)

    print("\n== what a hit still is: slots, restart note, no disk check, no route ==")
    fresh()
    ctx = new_context()
    await paste(LINK_A, ctx)
    await run_work()
    free_reads, entered = W.free_reads, W.busy_entered
    W.free = 0                                   # the disk is as full as it can be
    await tap(ctx)
    await run_work()
    check("a hit goes out even when the disk is too full for a download to start (the lookup is first)",
          len(W.sent) == 2 and W.sent[1]["how"] == "document" and W.free_reads == free_reads, (len(W.sent), W.free_reads))
    check("it took the restart note like any delivery", W.busy_entered == entered + 1, (W.busy_entered, entered))
    check("and a global slot while it sent", W.during_send[-1][0] < W.during_send[-1][1], W.during_send[-1])
    W.free = 10_000
    check("no route was asked, nothing downloaded", W.resolves == [("instagram", LINK_A)] and len(W.downloads) == 1)

    print("\n== a send that fails ==")
    fresh()
    ctx = new_context()
    await paste(LINK_A, ctx)
    await run_work()
    W.send_fails = FileNotFoundError(2, "No such file", str(SCRATCH / "dbot-cache" / "secretfolder" / "clip0.mp4"))
    W.shown.clear()
    await tap(ctx)
    await run_work()
    check("a kept delivery whose send fails tells the person, as a failed delivery does: what kind of failure it was",
          any("FileNotFoundError" in s for s in W.shown), W.shown)
    check("...and not what it said, which would carry the path of their files",
          not any("secretfolder" in s or "No such file" in s or str(SCRATCH) in s for s in W.shown), W.shown)
    check("counts as a failed job", W.jobs[-1] is False, W.jobs)
    check("the entry stays: it may have been a blip", len(cache_files()) == 1 and dc.stats()["entries"] == 1)
    entry = dc._entries[USER]
    check("and nothing is left pinned", entry.pins == 0)
    W.send_fails = False
    await paste(LINK_A, ctx)
    await run_work()
    check("the same link afterwards is served from it", len(W.resolves) == 1 and W.sent[-1]["how"] == "video")

    fresh(send_fails=True)
    ctx = new_context()
    await paste(LINK_A, ctx)
    await run_work()
    check("a first delivery whose send fails keeps nothing, and its files are deleted",
          cache_files() == [] and outside_cache() == [] and dc.stats()["entries"] == 0 and W.jobs == [False],
          (cache_files(), outside_cache(), W.jobs))

    print("\n== the cache off ==")
    fresh()
    dc.LIFE_SECONDS = 0
    ctx = new_context()
    await paste(LINK_A, ctx)
    await run_work()
    check("delivered, and its files deleted as before; the cache folder was never made",
          len(W.sent) == 1 and outside_cache() == [] and not (SCRATCH / "dbot-cache").exists(), outside_cache())
    await tap(ctx)
    await run_work()
    await paste(LINK_A, ctx)
    await run_work()
    check("the button and the same link are each a fetch: three resolves for three requests", len(W.resolves) == 3,
          W.resolves)
    check("and each cleaned up after itself", outside_cache() == [])
    dc.LIFE_SECONDS = 300

    print("\n== too big for the cache, or the disk too full ==")
    fresh()
    dc.ENTRY_MAX_MB = 0
    ctx = new_context()
    await paste(LINK_A, ctx)
    await run_work()
    check("a delivery over the cap is sent, not kept, and deleted", len(W.sent) == 1 and cache_files() == [] and outside_cache() == [])
    dc.ENTRY_MAX_MB = 100
    check("the bot's floor is the refusal level plus a full download for every slot: 300 + 3 x 48 = 444",
          bot._cache_floor_mb() == 444 == bot.MIN_FREE_MB + bot.MAX_CONCURRENT_DOWNLOADS * video.MAX_DOWNLOAD_MB,
          bot._cache_floor_mb())
    fresh()
    dc.free_mb = lambda: 443
    ctx = new_context()
    await paste(LINK_A, ctx)
    await run_work()
    check("a disk with 443 MB free (one under the floor): delivered, not kept, deleted",
          len(W.sent) == 1 and cache_files() == [] and outside_cache() == [], (cache_files(), outside_cache()))
    dc.free_mb = lambda: 444
    await paste(LINK_A, ctx)
    await run_work()
    check("with 444 MB free it is kept", len(cache_files()) == 1)

    print("\n== /deletemydata ==")
    erased = []

    async def fake_erase(update, context):
        erased.append((dc.stats()["entries"], "redeliver" in context.user_data))
        return "END"

    real_erase = bot.delete_my_data_chosen
    bot.delete_my_data_chosen = fake_erase
    try:
        fresh()
        ctx = new_context()
        await paste(LINK_A, ctx)
        await run_work()
        yes = SimpleNamespace(data=shared_features.ERASE_PREFIX + "yes")
        result = await bot._delete_my_data_chosen(update_with(query=yes), ctx)
        check("erasing drops the kept delivery and the memory behind the buttons, before the erase itself runs",
              erased == [(0, False)] and cache_files() == [] and "redeliver" not in ctx.user_data and result == "END", erased)
        check("it still runs the shared erase, and returns what it returned", len(erased) == 1)
        fresh()
        ctx = new_context()
        await paste(LINK_A, ctx)
        await run_work()
        old_token = tokens(ctx)[-1]
        no = SimpleNamespace(data=shared_features.ERASE_PREFIX + "no")
        await bot._delete_my_data_chosen(update_with(query=no), ctx)
        check("answering no changes nothing", len(cache_files()) == 1 and tokens(ctx) == [old_token])
        await bot._delete_my_data_chosen(update_with(user=OTHER, query=yes), new_context())
        check("another person erasing does not touch this person's", len(cache_files()) == 1)
        await bot._delete_my_data_chosen(update_with(query=yes), ctx)
        answers = await tap(ctx, token=old_token)
        check("an old button then answers 'expired', as designed", answers and answers[0][1] == {"show_alert": True}
              and answers[0][0] == i18n.t("en", "redeliver_expired"), answers)
        check("and fetched nothing", len(W.resolves) == 1)

        fresh()
        ctx = new_context()
        await paste(LINK_A, ctx)                             # still downloading when the person erases
        await bot._delete_my_data_chosen(update_with(query=yes), ctx)
        await run_work()
        check("a download still in flight when the person erases is delivered, but not kept",
              len(W.sent) == 1 and cache_files() == [] and outside_cache() == [] and dc.stats()["entries"] == 0,
              (cache_files(), outside_cache()))
    finally:
        bot.delete_my_data_chosen = real_erase
    check("the real handler for the erase button is the wrapper",
          inspect.getsource(bot.main).count("_delete_my_data_chosen, pattern") == 1
          and inspect.getsource(bot.main).count("delete_my_data_chosen, pattern") == 1)

    print("\n== the start and the sweep ==")
    fresh()
    junk = SCRATCH / "dbot-cache" / "orphan"
    junk.mkdir(parents=True)
    (junk / "from-a-process-that-is-gone.mp4").write_bytes(b"x")
    jobs = []

    class JobQueue:
        def run_repeating(self, callback, interval=None, first=None, **kw):
            jobs.append((callback.__name__, interval, first))

    saved = {n: getattr(bot, n) for n in ("tune_runtime", "load_provider_health", "publish_commands", "publish_profile")}
    saved_lifecycle, saved_load, saved_schedule = bot.lifecycle.on_start, bot.resolvers.load, bot.routewatch.schedule
    bot.tune_runtime = bot.publish_commands = bot.publish_profile = nothing
    bot.load_provider_health = lambda: {}
    bot.lifecycle.on_start = nothing
    bot.resolvers.load = lambda data: None
    bot.routewatch.schedule = lambda app: None
    try:
        await bot._post_init(SimpleNamespace(job_queue=JobQueue()))
    finally:
        for n, f in saved.items():
            setattr(bot, n, f)
        bot.lifecycle.on_start, bot.resolvers.load, bot.routewatch.schedule = saved_lifecycle, saved_load, saved_schedule
    check("starting wipes what a previous process left in the cache folder", not junk.exists(), list(SCRATCH.rglob("*")))
    check("and schedules the sweep every 15 seconds", ("_sweep_delivery_cache", 15, 15) in jobs, jobs)
    ctx = new_context()
    await paste(LINK_A, ctx)
    await run_work()
    CLOCK.advance(301)
    await bot._sweep_delivery_cache()
    check("the sweep job deletes what has expired", cache_files() == [] and dc.stats()["entries"] == 0)
    real_sweep = dc.sweep
    dc.sweep = lambda now=None: 1 / 0
    try:
        await bot._sweep_delivery_cache()
        survived = True
    except Exception:
        survived = False
    finally:
        dc.sweep = real_sweep
    check("a sweep that fails does not raise into the job queue", survived)

    print("\n== /status, logs ==")
    fresh()
    ctx = new_context()
    await paste(LINK_A, ctx)
    await run_work()
    await tap(ctx)
    await run_work()
    await paste(LINK_B, ctx)
    check("a different link took the entry: nothing is kept yet",
          bot._cache_status_line() == "Delivery cache: 0 kept (0.0 MB), 1 hits / 1 misses since start", bot._cache_status_line())
    await run_work()
    line = bot._cache_status_line()
    check("one line, counts only, in the format the owner asked for: kept, MB, hits, misses since start",
          line == "Delivery cache: 1 kept (0.0 MB), 1 hits / 2 misses since start", line)
    check("with no id, no link and no name in it", str(USER) not in line and "instagram" not in line.lower() and "http" not in line)
    dc.LIFE_SECONDS = 0
    check("off says so", bot._cache_status_line() == "Delivery cache: off")
    dc.LIFE_SECONDS = 300
    check("/status is the owner's: it asks who is asking first",
          inspect.getsource(bot.status_command).index("_is_admin") < inspect.getsource(bot.status_command).index("_cache_status_line"))

    records = []

    attached = []

    class Catch(logging.Handler):
        def emit(self, record):
            records.append(record.getMessage())
            if record.exc_info:
                attached.append(record.getMessage())

    handler = Catch()
    logging.disable(logging.NOTSET)
    root = logging.getLogger()
    kept_handlers, kept_level = root.handlers[:], root.level
    root.handlers, root.level = [handler], logging.DEBUG
    real_sweep = dc.sweep
    try:
        fresh()
        ctx = new_context()
        await paste(LINK_A, ctx)
        await run_work()
        W.send_fails = FileNotFoundError(2, "No such file", str(SCRATCH / "dbot-cache" / "secretfolder" / "clip0.mp4"))
        await tap(ctx)
        await run_work()
        W.send_fails = False
        await paste(LINK_B, ctx)
        await run_work()
        dc.sweep(now=CLOCK.now + 10_000)

        def broken_sweep(*args, **kwargs):
            raise OSError(5, "Access denied", str(SCRATCH / "dbot-cache" / "secretfolder"))

        dc.sweep = broken_sweep
        await bot._sweep_delivery_cache()
    finally:
        dc.sweep = real_sweep
        root.handlers, root.level = kept_handlers, kept_level
        logging.disable(logging.CRITICAL)
    text = "\n".join(records)
    check("nothing logged names the link, the person or a path", LINK_A not in text and LINK_B not in text
          and "AAAAAAAAAAA" not in text and str(SCRATCH) not in text and f"user {USER}" not in text
          and "secretfolder" not in text and "No such file" not in text and "Access denied" not in text, text[:300])
    check("(something was logged: the failed send)", "from the kept files failed" in text, text[:300])
    check("the failed send and the failed sweep each log only the kind of failure",
          "(FileNotFoundError)" in text and "sweep failed (OSError)" in text, text[:400])
    check("and neither attaches a traceback, which would carry the path", attached == [], attached)

    print("\n== what must not change ==")
    for name in ("_merge_and_send", "merge_callback"):
        check(f"{name} does not use the cache (a follow-up)", "delivery_cache" not in inspect.getsource(getattr(bot, name)))
    source = inspect.getsource(bot.handle_link)
    check("handle_link notes the link straight after recognising it, before the refusals, the queue and the allowance",
          source.index("detect_platform") < source.index("note_link") < source.index("refuse_new_work")
          < source.index("_user_pending") < source.index("download_allowance") < source.index("record_download"))
    source = inspect.getsource(bot.redeliver_callback)
    check("the button notes its link after it is known to be live, before the allowance",
          source.index("_spend_action") < source.index("note_link") < source.index("download_allowance")
          < source.index("record_download"))
    source = inspect.getsource(bot._resolve_and_send)
    check("a lookup happens inside the slots, before the disk is looked at and before a route is asked",
          source.index("async with _user_slot(") < source.index("delivery_cache.take") < source.index("_free_mb()")
          < source.index("resolvers.resolve"))
    check("the files are offered to the cache only after the send, the job count and the status clean-up",
          source.index("_send_media(update, context, list(zip") < source.index("note_job(True)", source.index("resolvers.resolve"))
          < source.index("delivery_cache.keep"))
    check("a kept hit is released whatever happened to the send",
          "finally:\n                delivery_cache.release(cached)" in source)
    check("the sweep is every 15 seconds", bot.DELIVERY_CACHE_SWEEP_SECONDS == 15)
    example = (REPO / "downloader_bot" / ".env.example").read_text(encoding="utf-8")
    check("the three settings are in .env.example, commented, with their defaults",
          all(f"# {name}={value}" in example for name, value in (
              ("DBOT_CACHE_SECONDS", 300), ("DBOT_CACHE_ENTRY_MAX_MB", 100), ("DBOT_CACHE_MAX_MB", 400))))


module_checks()
bot_checks()

shutil.rmtree(SCRATCH, ignore_errors=True)
print()
if failures:
    print(f"FAILED ({len(failures)}):")
    for name in failures:
        print("  -", name)
    sys.exit(1)
print("The last download is kept five minutes, and only the person it was for can have it back.")
