"""ManagerBot's reserve copies (the owner, 2026-11-03). bots/shared/reserve.py
says why they exist; docs/reserve-copies.md is the manual.

Two copies, both family_db.py archives (the format /backup has always sent):

  nightly   the whole database, sent to the owner's Telegram with the nightly
            report and pinned there (nothing deletes them: "Nightly arrives in
            my telegram chat, so there's no point in deleting"); also kept on
            the volume, sealed, as nightly.mumu-reserve.
  latest    what matters -- every table but the logs and counters (LEAVE_OUT)
            -- written to the volume, sealed, as latest.mumu-reserve within
            about two minutes of any change to it. This is what makes a loss in
            the afternoon cost minutes rather than everything since 22:00.

Encrypted means encrypted to the owner's offline key (offline_key.py,
BACKUP_PUBLIC_KEY): the bots hold only the public half, so nothing on the
server opens a copy -- not the volume, which also holds the data key, and not
leaked variables. Until 2026-11-09 the volume copies were sealed with the data
key itself, so the volume alone gave the whole database away; a copy is now
written to the volume only when BACKUP_PUBLIC_KEY is set (volume_problem). The
Telegram copy is encrypted the same way when the key is set, and sent as it is
otherwise (what is personal in it is sealed column by column already); the
private key that opens them stays in the owner's password manager, and
scripts/restore_reserve.py takes it on the day of a restore.

A change is seen without touching the bots' writes: Postgres counts every
insert, update and delete per table (pg_stat_user_tables), and a sum of those
counts over the tables in `latest` moves whenever any of them changes.
"""
from __future__ import annotations

import json
import os
import tempfile
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg

import family_db
import family_link
import offline_key
import reserve

# 1 was sealed with the data key (2.2.0 before release; never deployed), 2 is
# encrypted to the owner's offline key. Only 2 is written or read.
MAGIC = b"MUMU-RESERVE 2\n"
LATEST_NAME = "latest.mumu-reserve"
NIGHTLY_NAME = "nightly.mumu-reserve"

# Logs, counters, caches and work in progress: rebuilt by the bots, or of no
# use after an emergency. Everything else -- settings, packs, links, payments,
# credit, feedback, the erasure notes -- is in `latest`. A new table is in it
# until it is named here.
LEAVE_OUT = frozenset((
    "activity_events", "usage_samples", "heartbeats", "events", "commands", "bot_state",
    "button_taps", "problem_reports", "download_events", "provider_health", "sent_files",
    "conversions_in_flight", "runtime_state",
))

# The quickest `latest` follows a change: a busy minute is one copy, not sixty.
MIN_SECONDS = int(os.environ.get("MBOT_RESERVE_MIN_SECONDS") or 120)


def seal_archive(archive: bytes, kind: str, taken_at: datetime) -> bytes:
    """The copy encrypted to the owner's offline key, behind a header that says what
    it is without opening it. The header is inside the encrypted part too, so a copy
    cannot be relabelled (made to look newer, or of the other kind) without the
    restore noticing (open_archive)."""
    public = offline_key.recipient()
    if not public:
        raise offline_key.OfflineKeyError(f"{offline_key.RECIPIENT_VARIABLE} is not set")
    header = json.dumps({"kind": kind, "taken_at": taken_at.isoformat(),
                         "to": offline_key.fingerprint(public)}, sort_keys=True).encode("ascii")
    return MAGIC + header + b"\n" + offline_key.encrypt(header + b"\n" + archive, public)


def read_header(blob: bytes) -> "dict | None":
    """A copy's kind, time and the public key it was encrypted to, readable without
    the private key; None for anything else (a plain archive from Telegram)."""
    if not blob.startswith(MAGIC):
        return None
    header, _, _ = blob[len(MAGIC):].partition(b"\n")
    return json.loads(header)


def open_archive(blob: bytes, identity: str) -> bytes:
    """The archive in a copy, with the owner's private key (AGE-SECRET-KEY-1...)."""
    header, _, sealed = blob[len(MAGIC):].partition(b"\n")
    inner, _, archive = offline_key.decrypt(sealed, identity).partition(b"\n")
    if inner != header:
        raise offline_key.OfflineKeyError("the copy's label was changed after it was written")
    return archive


def build_archive(dsn: str, leave_out: frozenset = frozenset()) -> bytes:
    """family_db's archive, in memory: tens of kilobytes for `latest`, a few
    megabytes for the whole database."""
    with tempfile.TemporaryDirectory(prefix="mbot-reserve-") as folder:
        path = Path(folder) / "copy.zip"
        try:
            family_db.backup(dsn, path, None, leave_out=leave_out, say=lambda *_: None)
        except SystemExit as exc:
            raise RuntimeError(str(exc) or "nothing to copy") from None
        return path.read_bytes()


def write_atomic(path: Path, data: bytes) -> None:
    """Written beside, then renamed over: a crash halfway leaves the old copy whole."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".part")
    with open(temporary, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def volume_problem() -> "str | None":
    """Why the volume can't be written, in words, or None. Railway mounts a
    volume as root, and the bots run as their own user. And a copy goes there
    only encrypted to the owner's offline key: the volume holds the data key
    too, so anything sealed with that key would leak with it."""
    if not reserve.RESERVE_DIR:
        return None
    try:
        if not offline_key.recipient():
            return (f"Reserve copies on the volume need {offline_key.RECIPIENT_VARIABLE}, the public half of "
                    f"your offline key (docs/data-protection.md): the volume also holds the data key, so a "
                    f"copy sealed with that key would give the database away with the volume.")
    except offline_key.OfflineKeyError as exc:
        return str(exc)
    folder = Path(reserve.RESERVE_DIR)
    try:
        folder.mkdir(parents=True, exist_ok=True)
        probe = folder / ".write-test"
        probe.write_bytes(b"ok")
        probe.unlink()
    except OSError as exc:
        return (f"RESERVE_DIR ({folder}) can't be written: {exc.strerror or exc}. On Railway, "
                f"set the variable RAILWAY_RUN_UID=0 so the volume is writable (docs/reserve-copies.md).")
    return None


def change_mark(dsn: str) -> int:
    """A number that moves whenever a table in `latest` gains, changes or loses a row."""
    with closing(psycopg.connect(dsn, connect_timeout=10)) as conn:
        return int(conn.execute(
            "SELECT coalesce(sum(n_tup_ins + n_tup_upd + n_tup_del), 0) FROM pg_stat_user_tables "
            "WHERE schemaname NOT LIKE 'pg\\_%%' AND schemaname NOT LIKE 'supabase\\_%%' "
            "AND schemaname <> ALL(%s) AND relname <> ALL(%s)",
            (list(family_db.SYSTEM_SCHEMAS), sorted(LEAVE_OUT)),
        ).fetchone()[0])


def write_latest(dsn: str, now: "datetime | None" = None) -> int:
    """`latest` on the volume now; its size in bytes."""
    now = now or datetime.now(timezone.utc)
    data = seal_archive(build_archive(dsn, LEAVE_OUT), "latest", now)
    write_atomic(Path(reserve.RESERVE_DIR) / LATEST_NAME, data)
    return len(data)


def write_nightly(archive: bytes, taken_at: datetime) -> None:
    write_atomic(Path(reserve.RESERVE_DIR) / NIGHTLY_NAME, seal_archive(archive, "nightly", taken_at))


def prune_erasures(dsn: str, now: "datetime | None" = None) -> int:
    """Erasure notes older than reserve.KEEP_DAYS go, from the table and from the
    volume's journal: no copy that old is ever restored. Returns how many."""
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=reserve.KEEP_DAYS)
    with closing(psycopg.connect(dsn, connect_timeout=10)) as conn:
        removed = conn.execute(
            f"DELETE FROM {family_link.FAMILY_SCHEMA}.erasures WHERE erased_at < %s", (cutoff,)).rowcount or 0
        conn.commit()
    path = reserve.journal_path()
    if path is not None and path.exists():
        lines = path.read_text(encoding="ascii").splitlines()
        kept = [line for line in lines if line.strip() and _journal_time(line) >= cutoff]
        if len(kept) != len(lines):
            write_atomic(path, "".join(line + "\n" for line in kept).encode("ascii"))
            removed += len(lines) - len(kept)
    return removed


def _journal_time(line: str) -> datetime:
    try:
        return datetime.fromisoformat(json.loads(line)["at"])
    except (ValueError, KeyError, TypeError):
        # A torn last line (the process died mid-write) is kept, not guessed at.
        return datetime.max.replace(tzinfo=timezone.utc)


def human_size(size: int) -> str:
    return f"{size / 1024:.0f} KB" if size < 1024 * 1024 else f"{size / (1024 * 1024):.1f} MB"
