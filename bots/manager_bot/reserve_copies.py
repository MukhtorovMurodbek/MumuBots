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

MAGIC = b"MUMU-RESERVE 2\n"
LATEST_NAME = "latest.mumu-reserve"
NIGHTLY_NAME = "nightly.mumu-reserve"

LEAVE_OUT = frozenset((
    "activity_events", "usage_samples", "heartbeats", "events", "commands", "bot_state",
    "button_taps", "problem_reports", "download_events", "provider_health", "sent_files",
    "conversions_in_flight", "runtime_state",
))

MIN_SECONDS = int(os.environ.get("MBOT_RESERVE_MIN_SECONDS") or 120)


def seal_archive(archive: bytes, kind: str, taken_at: datetime) -> bytes:
    public = offline_key.recipient()
    if not public:
        raise offline_key.OfflineKeyError(f"{offline_key.RECIPIENT_VARIABLE} is not set")
    header = json.dumps({"kind": kind, "taken_at": taken_at.isoformat(),
                         "to": offline_key.fingerprint(public)}, sort_keys=True).encode("ascii")
    return MAGIC + header + b"\n" + offline_key.encrypt(header + b"\n" + archive, public)


def read_header(blob: bytes) -> "dict | None":
    if not blob.startswith(MAGIC):
        return None
    header, _, _ = blob[len(MAGIC):].partition(b"\n")
    return json.loads(header)


def open_archive(blob: bytes, identity: str) -> bytes:
    header, _, sealed = blob[len(MAGIC):].partition(b"\n")
    inner, _, archive = offline_key.decrypt(sealed, identity).partition(b"\n")
    if inner != header:
        raise offline_key.OfflineKeyError("the copy's label was changed after it was written")
    return archive


def build_archive(dsn: str, leave_out: frozenset = frozenset()) -> bytes:
    with tempfile.TemporaryDirectory(prefix="mbot-reserve-") as folder:
        path = Path(folder) / "copy.zip"
        try:
            family_db.backup(dsn, path, None, leave_out=leave_out, say=lambda *_: None)
        except SystemExit as exc:
            raise RuntimeError(str(exc) or "nothing to copy") from None
        return path.read_bytes()


def write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".part")
    with open(temporary, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def volume_problem() -> "str | None":
    if not reserve.RESERVE_DIR:
        return None
    try:
        if not offline_key.recipient():
            return (f"Reserve copies on the volume need {offline_key.RECIPIENT_VARIABLE}, the public half of "
                    f"the offline key (private/runbooks/data-protection.md): the volume also holds the data key, so a "
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
        return (f"RESERVE_DIR ({folder}) can't be written: {exc.strerror or exc}. The container has to start "
                f"as root (no RAILWAY_RUN_UID other than 0) for start.sh to hand the volume to the bots' user; "
                f"elsewhere, MUMU_VOLUME_PATH names the volume (private/runbooks/reserve-copies.md).")
    return None


def change_mark(dsn: str) -> int:
    with closing(psycopg.connect(dsn, connect_timeout=10)) as conn:
        return int(conn.execute(
            "SELECT coalesce(sum(n_tup_ins + n_tup_upd + n_tup_del), 0) FROM pg_stat_user_tables "
            "WHERE schemaname NOT LIKE 'pg\\_%%' AND schemaname NOT LIKE 'supabase\\_%%' "
            "AND schemaname <> ALL(%s) AND relname <> ALL(%s)",
            (list(family_db.SYSTEM_SCHEMAS), sorted(LEAVE_OUT)),
        ).fetchone()[0])


def write_latest(dsn: str, now: "datetime | None" = None) -> int:
    now = now or datetime.now(timezone.utc)
    data = seal_archive(build_archive(dsn, LEAVE_OUT), "latest", now)
    write_atomic(Path(reserve.RESERVE_DIR) / LATEST_NAME, data)
    return len(data)


def write_nightly(archive: bytes, taken_at: datetime) -> None:
    write_atomic(Path(reserve.RESERVE_DIR) / NIGHTLY_NAME, seal_archive(archive, "nightly", taken_at))


def prune_erasures(dsn: str, now: "datetime | None" = None) -> int:
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
        return datetime.max.replace(tzinfo=timezone.utc)


def human_size(size: int) -> str:
    return f"{size / 1024:.0f} KB" if size < 1024 * 1024 else f"{size / (1024 * 1024):.1f} MB"
