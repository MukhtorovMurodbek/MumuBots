# `delivery_cache.py`

[↑ downloader_bot](README.md) · [code](delivery_cache.py)

The last download (PER_USER, default 1), kept for LIFE_SECONDS (default 10
minutes), so the buttons under it and the same link sent again are answered
without fetching the post a second time.

What was asked for (2026-10-12): after a download is delivered, keep the files a
few minutes; if the person taps the button under it ("get it as a file", "get it
compressed") or sends the same link again, send the kept files instead of going to
the site again; delete them as soon as the person sends a different link; every
re-use starts the clock again. LEGAL.md and /privacy say so. (2.2.0 keeps up to
PER_USER deliveries per person, which another link does not drop; it was set to the last one, for 10 minutes, on 2026-10-28 -- so with the default, a different link
pushes the previous delivery out, as it did before 2.2.0.)

Entries are per person, keyed by (platform, url) exactly as \_resolve_and_send was
given them. It holds the files and nothing else: no link text outside the key, no
name, no message. The files live in a folder of their own beside the downloads
(&lt;work dir>/dbot-cache/&lt;random>/), and the whole folder is wiped when the bot
starts, because anything in it then belongs to a process that is gone.

The rules, each of which has a test that fails without it:

```text
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
```

Pure: no Telegram, no database, no threads. Every function is synchronous and is
called on the event loop thread, which is what makes the bookkeeping safe without
a lock. The clock can be replaced (`_clock`) so that tests need not wait.

### `_env_int()`

A whole number from the environment, never negative; `unreadable` (the
default when it is None) for anything else -- a typo here must not stop the bot
starting. Not set, or empty, is the default.

_At `LIFE_SECONDS = _env_int("DBOT_CACHE_SECONDS", 600, unreadable=0)`:_

How long a delivery is kept, from the last time it was used. 0: no cache at all.
Something that is not a whole number ("off") is 0 too, not the default: whoever
typed it meant no.

_At `MAX_LIFE_SECONDS = 12 * 60 * 60`:_

However often it is used, a kept delivery goes this long after it was kept. The
terms promise that a post removed at the platform stops being sent within 12 hours
(sent_files.CONFIRM_HOURS for the file-id store); every copy of the link restarts
LIFE_SECONDS, so without this a person re-sending it would be served a removed
post for ever.

_At `ENTRY_MAX_MB = _env_int("DBOT_CACHE_ENTRY_MAX_MB", 100)`:_

One delivery (all its files) over this is not kept, and the whole cache stays under that.

_At `PER_USER = max(1, _env_int("DBOT_CACHE_PER_USER", 1))`:_

How many deliveries one person may have kept at once; the least recently used goes first.

_At `LATEST_KEEP_SECONDS = 60 * 60`:_

A person's latest link is remembered this long, or until they send another, even
when the cache's own life is shorter: a link that waited for a slot and then
downloaded for longer than LIFE_SECONDS must still be kept when it lands.

_At `SHARED_SENDER_IDS = frozenset({1087968824, 136817688, 777000})`:_

Sender ids that stand for many people, not for one: the anonymous group admin,
a message sent as a channel, and Telegram's own service account. One person's
"latest link" and "kept delivery" would be everybody's, so nothing is cached, taken
or noted for them. The one place this is decided is usable().

- `_clock = time.monotonic` — replaced by tests

### `Entry`

- `@dataclass(eq=False)` — compared by identity: two entries are never "equal"
- `key: tuple` — (platform, url)
- `items: list` — [(MediaItem, path in the cache)], in post order
- `size: int` — bytes
- `pins: int = 0` — sends in progress from these files
- `kept_at: float = 0.0` — when it was kept, for MAX_LIFE_SECONDS
- `dead: bool = False` — dropped while pinned: deleted at the last release

- `_work_dir = None` — set by configure()

### `configure()`

Where downloads are staged; the cache folder is inside it.

### `usable()`

Whether anything may be cached, taken or noted for this sender: the cache is
on and the id is one person's (not SHARED_SENDER_IDS).

### `free_mb()`

Free space on the disk the downloads are staged on, or None.

### `_remove_dir()`

- At `logger.warning("couldn't delete a cached delivery (%s)", type(exc).__name__)`:
  The kind of failure only: its text would carry the path.

### `note_link()`

The person has just sent (or tapped) this link. It is now their latest (they
keep their last PER_USER), and a live kept delivery of it has its life restarted;
an expired one is dropped. Returns what it found (NOTED\_\*): whether this is the link
they sent last, and if so whether a live delivery of it was kept.

### `keep()`

Take over these delivered files. True: the cache now owns them and the caller
must not delete them. False: they are the caller's, as before -- the cache is off,
this link is not among the person's last PER_USER, it is too big, the disk is too full, or a
move failed (and then nothing is left half-moved). Never raises.

`floor_mb` is the free space the disk must still have with this entry kept.

- At `logger.warning("couldn't keep a delivery (%s)", type(exc).__name__)`:
  The kind of failure only, never exc_info or its text: they carry paths.

### `_keep()`

- At `candidates = sorted((e for e in _entries.values() if e.pins == 0), key=lambda e: e.last_ …`:
  Who would have to go. Least recently used first; a pinned entry is being
  sent from and cannot be freed now, so it is not a candidate.
- `need = 0` — how many of the candidates, from the front
- `return False` — even with all of them gone it does not fit
- `return False` — the disk is too full for this to stay

### `_renewed()`

LIFE_SECONDS again from now, but never past MAX_LIFE_SECONDS after keeping.

### `take()`

The person's kept delivery of exactly this link, pinned for the send (call
release() when it is over, whatever happens) and with its life restarted; or
None.

### `release()`

The send from `entry` is over.

### `forget_user()`

/deletemydata: the kept delivery, and also what makes a download that is still
in flight kept when it lands (their latest link).

### `sweep()`

Drop what has expired, and forget latest links that nothing is waiting on (an
hour old, LATEST_KEEP_SECONDS: not the cache's own life, which a slow download
outlasts). Every few seconds from the job queue.

### `make_room()`

A download is about to need up to `mb` MB of the disk, on top of the
`min_free_mb` it must always leave free: delete kept deliveries, least recently
used first, until the disk has both or nothing that can be deleted is left. A
kept delivery that is being sent from is not touched (it frees nothing now).
This is what makes the cache give way, so that it never makes a download refuse
for space. Only frees space; never raises.

### `wipe()`

Delete everything in the cache folder (at startup: it can only hold what a
previous process left behind) and forget all state.

### `stats()`

Counts only, for the operator's /status.
