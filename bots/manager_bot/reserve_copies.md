# `reserve_copies.py`

[↑ manager_bot](README.md) · [code](reserve_copies.py)

ManagerBot's reserve copies (2026-11-03). shared/security/reserve.py
says why they exist; private/runbooks/reserve-copies.md is the manual.

Two copies, both family_db.py archives (the format /backup has always sent):

```text
nightly   the whole database, sent to the operator's Telegram with the nightly report and pinned there (nothing deletes them: they arrive in a Telegram chat, so there is no point in deleting them); also kept on
          the volume, sealed, as nightly.mumu-reserve.
latest    what matters -- every table but the logs and counters (LEAVE_OUT)
          -- written to the volume, sealed, as latest.mumu-reserve within
          about two minutes of any change to it. This is what makes a loss in
          the afternoon cost minutes rather than everything since 22:00.
```

Since 2026-11-18 an accepted /shutdown also sends the whole database to the
operator's Telegram, the way the nightly copy is sent, and writes `latest`
again at once (bot.py's shutdown_copy). Every copy sent to Telegram is the
whole database, so `latest`'s tables are always inside it.

Encrypted means encrypted to the operator's offline key (offline_key.py,
BACKUP_PUBLIC_KEY): the bots hold only the public half, so nothing on the
server opens a copy -- not the volume, which also holds the data key, and not
leaked variables. Until 2026-11-09 the volume copies were sealed with the data
key itself, so the volume alone gave the whole database away; a copy is now
written to the volume only when BACKUP_PUBLIC_KEY is set (volume_problem). The
Telegram copy is encrypted the same way when the key is set, and sent as it is
otherwise (what is personal in it is sealed column by column already); the
private key that opens them stays in the operator's password manager, and
scripts/security/restore_reserve.py takes it on the day of a restore.

A change is seen without touching the bots' writes: Postgres counts every
insert, update and delete per table (pg_stat_user_tables), and a sum of those
counts over the tables in `latest` moves whenever any of them changes.

_At `MAGIC = b"MUMU-RESERVE 2\n"`:_

1 was sealed with the data key (2.2.0 before release; never deployed), 2 is
encrypted to the operator's offline key. Only 2 is written or read.

_At `LEAVE_OUT = frozenset((`:_

Logs, counters, caches and work in progress: rebuilt by the bots, or of no
use after an emergency. Everything else -- settings, packs, links, payments,
credit, feedback, the erasure notes -- is in `latest`. A new table is in it
until it is named here.

_At `MIN_SECONDS = int(os.environ.get("MBOT_RESERVE_MIN_SECONDS") or 120)`:_

The quickest `latest` follows a change: a busy minute is one copy, not sixty.

### `seal_archive()`

The copy encrypted to the operator's offline key, behind a header that says what
it is without opening it. The header is inside the encrypted part too, so a copy
cannot be relabelled (made to look newer, or of the other kind) without the
restore noticing (open_archive).

### `read_header()`

A copy's kind, time and the public key it was encrypted to, readable without
the private key; None for anything else (a plain archive from Telegram).

### `open_archive()`

The archive in a copy, with the operator's private key (AGE-SECRET-KEY-1...).

### `build_archive()`

family_db's archive, in memory: tens of kilobytes for `latest`, a few
megabytes for the whole database.

### `write_atomic()`

Written beside, then renamed over: a crash halfway leaves the old copy whole.

### `volume_problem()`

Why the volume can't be written, in words, or None. Railway mounts a
volume as root, and the bots run as their own user. And a copy goes there
only encrypted to the operator's offline key: the volume holds the data key
too, so anything sealed with that key would leak with it.

### `change_mark()`

A number that moves whenever a table in `latest` gains, changes or loses a row.

### `write_latest()`

`latest` on the volume now; its size in bytes.

### `prune_erasures()`

Erasure notes older than reserve.KEEP_DAYS go, from the table and from the
volume's journal: no copy that old is ever restored. Returns how many.

### `_journal_time()`

- At `return datetime.max.replace(tzinfo=timezone.utc)`:
  A torn last line (the process died mid-write) is kept, not guessed at.
