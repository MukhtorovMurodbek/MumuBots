import logging
import os
import time
from contextlib import closing, contextmanager
from datetime import datetime, timezone

from urllib.parse import urlsplit

import psycopg
from psycopg_pool import ConnectionPool

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/downloaderbot"
)


DB_SCHEMA = os.environ.get("DB_SCHEMA", "public")

TRANSACTION_POOLER_PORTS = {6543}


def check_database_url(dsn: str = DATABASE_URL) -> list[str]:
    problems: list[str] = []
    try:
        parts = urlsplit(dsn)
    except ValueError as exc:
        return [f"DATABASE_URL could not be parsed ({exc})."]

    if parts.netloc.count("@") > 1:
        problems.append(
            "DATABASE_URL contains more than one '@'. If that is a literal "
            "'@' in the password, percent-encode it (@ -> %40, : -> %3A, "
            "/ -> %2F, # -> %23); otherwise the host is read from the wrong "
            "part of the string."
        )

    try:
        port = parts.port
    except ValueError:
        problems.append(
            "DATABASE_URL's port is not a number -- an unencoded ':' or '@' "
            "in the password is the usual reason."
        )
        port = None

    if port in TRANSACTION_POOLER_PORTS:
        problems.append(
            f"DATABASE_URL points at port {port}, which is a TRANSACTION "
            f"pooler. search_path is passed as a connection option and "
            f"transaction pooling discards it, so this bot would silently "
            f"use the 'public' schema instead of {DB_SCHEMA!r}. Use the "
            f"session pooler (port 5432)."
        )

    return problems


POOL_MIN = int(os.environ.get("DB_POOL_MIN", "1"))
POOL_MAX = int(os.environ.get("DB_POOL_MAX", "3"))
POOL_MAX_IDLE = float(os.environ.get("DB_POOL_MAX_IDLE", "120"))
POOL_TIMEOUT = float(os.environ.get("DB_POOL_TIMEOUT", "15"))

POOL_CHECK_AFTER_IDLE = float(os.environ.get("DB_POOL_CHECK_AFTER_IDLE", "45"))

_pool: "ConnectionPool | None" = None
_last_known_good: "dict[int, float]" = {}


def _check_if_idle(conn) -> None:
    key = id(conn)
    now = time.monotonic()
    seen = _last_known_good.get(key)
    if seen is None or now - seen > POOL_CHECK_AFTER_IDLE:
        ConnectionPool.check_connection(conn)
    _last_known_good[key] = now
    if len(_last_known_good) > 4 * max(POOL_MAX, 1):
        for stale in [k for k, t in _last_known_good.items() if now - t > 3600]:
            _last_known_good.pop(stale, None)


def _get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            DATABASE_URL,
            min_size=POOL_MIN,
            max_size=POOL_MAX,
            max_idle=POOL_MAX_IDLE,
            timeout=POOL_TIMEOUT,
            kwargs={
                "options": f"-c search_path={DB_SCHEMA},public "
                           f"-c statement_timeout=60s -c idle_in_transaction_session_timeout=120s",
                "connect_timeout": 10, "tcp_user_timeout": 30000,
                "keepalives": 1, "keepalives_idle": 30,
                "keepalives_interval": 10, "keepalives_count": 5,
            },
            check=_check_if_idle,
            name=f"{DB_SCHEMA}",
            open=True,
        )
    return _pool


def pooled():
    return _get_pool().connection()


@contextmanager
def pooled_read():
    with _get_pool().connection() as conn:
        conn.set_autocommit(True)
        try:
            yield conn
        finally:
            try:
                conn.set_autocommit(False)
            except Exception:
                logging.getLogger(__name__).debug("Could not restore transaction mode", exc_info=True)


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


def connect(dsn: str = DATABASE_URL):
    return psycopg.connect(dsn, options=f"-c search_path={DB_SCHEMA},public")


def ensure_schema(dsn: str = DATABASE_URL) -> None:
    with closing(psycopg.connect(dsn)) as conn:
        conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{DB_SCHEMA}"')
        conn.commit()


def _column(conn, table: str, column: str):
    return conn.execute(
        "SELECT data_type, column_default FROM information_schema.columns "
        "WHERE table_schema = current_schema() AND table_name = %s AND column_name = %s",
        (table, column),
    ).fetchone()


NAME_PURPOSE = "stars.username"
OWNER_PURPOSE = "sticker.pack.owner"
_plain_names: "dict[str, bool]" = {}


def _names_sealable() -> bool:
    import family_link

    return family_link.vault_usable()


def seal_name(value, purpose: str):
    if value is None or not _names_sealable():
        return None
    import vault

    return vault.seal_json(value, purpose) if not isinstance(value, str) else vault.seal_text(value, purpose)


def open_name(blob, purpose: str, as_json: bool = False):
    if blob is None or not _names_sealable():
        return None
    import vault

    try:
        return vault.open_json(blob, purpose) if as_json else vault.open_text(blob, purpose)
    except (vault.VaultError, ValueError, UnicodeDecodeError):
        return None


def _note_plain_names(conn) -> None:
    for table, column in (("star_transactions", "username"), ("packs", "owner_username")):
        _plain_names[table] = _column(conn, table, column) is not None


def init_db(dsn: str = DATABASE_URL) -> None:
    for _problem in check_database_url(dsn):
        logging.getLogger(__name__).warning("%s", _problem)

    ensure_schema(dsn)
    with closing(connect(dsn)) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS settings (
                user_id BIGINT PRIMARY KEY,
                caption_enabled INTEGER NOT NULL DEFAULT 1
            )
            """
        )
        if _column(conn, "settings", "language") is None:
            conn.execute("ALTER TABLE settings ADD COLUMN IF NOT EXISTS language TEXT")
        if _column(conn, "settings", "lossless_enabled") is None:
            conn.execute("ALTER TABLE settings ADD COLUMN IF NOT EXISTS lossless_enabled INTEGER NOT NULL DEFAULT 0")
        if _column(conn, "settings", "large_files_enabled") is None:
            conn.execute("ALTER TABLE settings ADD COLUMN IF NOT EXISTS large_files_enabled INTEGER NOT NULL DEFAULT 0")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS star_transactions (
                id BIGSERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                username_sealed BYTEA,
                amount_stars BIGINT NOT NULL,
                item TEXT NOT NULL,
                status TEXT NOT NULL,
                payload TEXT NOT NULL UNIQUE,
                charge_id TEXT,
                currency TEXT NOT NULL DEFAULT 'XTR',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        if _column(conn, "star_transactions", "username_sealed") is None:
            conn.execute("ALTER TABLE star_transactions ADD COLUMN IF NOT EXISTS username_sealed BYTEA")
        amount = _column(conn, "star_transactions", "amount_stars")
        if amount is not None and amount[0] != "bigint":
            conn.execute("ALTER TABLE star_transactions ALTER COLUMN amount_stars TYPE BIGINT")
        if _column(conn, "star_transactions", "currency") is None:
            conn.execute("ALTER TABLE star_transactions ADD COLUMN IF NOT EXISTS currency TEXT NOT NULL DEFAULT 'XTR'")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS donation_prompts (
                user_id BIGINT PRIMARY KEY,
                action_count INTEGER NOT NULL DEFAULT 0,
                last_shown_at TEXT
            )
            """
        )
        if _column(conn, "donation_prompts", "times_shown") is None:
            conn.execute("ALTER TABLE donation_prompts ADD COLUMN IF NOT EXISTS times_shown INTEGER NOT NULL DEFAULT 0")
        if _column(conn, "donation_prompts", "pinned_message_id") is None:
            conn.execute("ALTER TABLE donation_prompts ADD COLUMN IF NOT EXISTS pinned_message_id BIGINT")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS activity_events (
                id BIGSERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                occurred_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_activity_events_occurred_at ON activity_events (occurred_at)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS download_events (
                id BIGSERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                platform TEXT,
                occurred_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_download_events_user_time "
            "ON download_events (user_id, occurred_at DESC)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS provider_health (
                provider TEXT PRIMARY KEY,
                ok_count BIGINT NOT NULL DEFAULT 0,
                fail_count BIGINT NOT NULL DEFAULT 0,
                consecutive_fails INTEGER NOT NULL DEFAULT 0,
                last_ok_at DOUBLE PRECISION,
                last_fail_at DOUBLE PRECISION,
                last_error TEXT
            )
            """
        )
        old = _column(conn, "sent_files", "post_key")
        if old is not None and old[0] != "uuid":
            conn.execute("DROP TABLE IF EXISTS sent_files")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sent_files (
                post_key     UUID PRIMARY KEY,
                items        BYTEA NOT NULL,
                confirmed_at TIMESTAMPTZ NOT NULL,
                last_used    DATE NOT NULL DEFAULT ((now() AT TIME ZONE 'UTC')::date)
            ) WITH (fillfactor = 90)
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS group_settings (
                chat_id    BIGINT PRIMARY KEY,
                settings   JSONB NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        _note_plain_names(conn)
        conn.commit()


def get_group_settings(chat_id: int):
    with pooled_read() as conn:
        row = conn.execute("SELECT settings FROM group_settings WHERE chat_id = %s", (chat_id,)).fetchone()
        return row[0] if row else None


def set_group_settings(chat_id: int, settings: dict) -> None:
    from psycopg.types.json import Jsonb
    with pooled() as conn:
        conn.execute(
            "INSERT INTO group_settings (chat_id, settings, updated_at) VALUES (%s, %s, now()) "
            "ON CONFLICT (chat_id) DO UPDATE SET settings = excluded.settings, updated_at = now()",
            (chat_id, Jsonb(settings)),
        )
        conn.commit()


def forget_group(chat_id: int) -> None:
    with pooled() as conn:
        conn.execute("DELETE FROM group_settings WHERE chat_id = %s", (chat_id,))
        conn.commit()


def channel_ids() -> "list[int]":
    with pooled_read() as conn:
        rows = conn.execute(
            "SELECT chat_id FROM group_settings WHERE settings->>'kind' = 'channel' "
            "ORDER BY updated_at DESC LIMIT 500"
        ).fetchall()
        return [row[0] for row in rows]


def move_group_settings(old_chat_id: int, new_chat_id: int) -> None:
    with pooled() as conn:
        conn.execute(
            "INSERT INTO group_settings (chat_id, settings, updated_at) "
            "SELECT %s, settings, updated_at FROM group_settings WHERE chat_id = %s "
            "ON CONFLICT (chat_id) DO NOTHING",
            (new_chat_id, old_chat_id),
        )
        conn.execute("DELETE FROM group_settings WHERE chat_id = %s", (old_chat_id,))
        conn.commit()


def record_activity_batch(user_ids) -> None:
    ids = list(user_ids)
    if not ids:
        return
    with pooled() as conn:
        conn.execute(
            "INSERT INTO activity_events (user_id, occurred_at) "
            "SELECT unnest(%s::bigint[]), now()",
            (ids,),
        )
        conn.commit()


def count_active_users_since(since) -> int:
    with pooled_read() as conn:
        cur = conn.execute(
            "SELECT COUNT(DISTINCT user_id) FROM activity_events WHERE occurred_at >= %s",
            (since,),
        )
        return cur.fetchone()[0]


def active_user_ids_since(since) -> list[int]:
    with pooled_read() as conn:
        cur = conn.execute(
            "SELECT DISTINCT user_id FROM activity_events WHERE occurred_at >= %s",
            (since,),
        )
        return [row[0] for row in cur.fetchall()]


def list_all_users() -> list[int]:
    with pooled_read() as conn:
        cur = conn.execute(
            "SELECT user_id FROM settings "
            "UNION "
            "SELECT DISTINCT user_id FROM activity_events"
        )
        return [int(r[0]) for r in cur.fetchall()]


def get_caption_enabled(user_id: int) -> bool:
    with pooled_read() as conn:
        cur = conn.execute("SELECT caption_enabled FROM settings WHERE user_id = %s", (user_id,))
        row = cur.fetchone()
        return bool(row[0]) if row else True


def set_caption_enabled(user_id: int, enabled: bool) -> None:
    with pooled() as conn:
        conn.execute(
            """
            INSERT INTO settings (user_id, caption_enabled) VALUES (%s, %s)
            ON CONFLICT (user_id) DO UPDATE SET caption_enabled = excluded.caption_enabled
            """,
            (user_id, int(enabled)),
        )
        conn.commit()


def get_lossless_enabled(user_id: int) -> bool:
    with pooled_read() as conn:
        cur = conn.execute("SELECT lossless_enabled FROM settings WHERE user_id = %s", (user_id,))
        row = cur.fetchone()
        return bool(row[0]) if row else False


def set_lossless_enabled(user_id: int, enabled: bool) -> None:
    with pooled() as conn:
        conn.execute(
            """
            INSERT INTO settings (user_id, lossless_enabled) VALUES (%s, %s)
            ON CONFLICT (user_id) DO UPDATE SET lossless_enabled = excluded.lossless_enabled
            """,
            (user_id, int(enabled)),
        )
        conn.commit()


def get_large_files_enabled(user_id: int) -> bool:
    with pooled_read() as conn:
        cur = conn.execute("SELECT large_files_enabled FROM settings WHERE user_id = %s", (user_id,))
        row = cur.fetchone()
        return bool(row[0]) if row else False


def set_large_files_enabled(user_id: int, enabled: bool) -> None:
    with pooled() as conn:
        conn.execute(
            """
            INSERT INTO settings (user_id, large_files_enabled) VALUES (%s, %s)
            ON CONFLICT (user_id) DO UPDATE SET large_files_enabled = excluded.large_files_enabled
            """,
            (user_id, int(enabled)),
        )
        conn.commit()


def get_user_language(user_id: int) -> str | None:
    with pooled_read() as conn:
        cur = conn.execute("SELECT language FROM settings WHERE user_id = %s", (user_id,))
        row = cur.fetchone()
        return row[0] if row else None


def set_user_language(user_id: int, language: str) -> None:
    with pooled() as conn:
        conn.execute(
            """
            INSERT INTO settings (user_id, language) VALUES (%s, %s)
            ON CONFLICT (user_id) DO UPDATE SET language = excluded.language
            """,
            (user_id, language),
        )
        conn.commit()


def record_star_invoice(
    user_id: int,
    username: str | None,
    amount_stars: int,
    item: str,
    payload: str,
    status: str = "invoiced",
    currency: str = "XTR",
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with pooled() as conn:
        conn.execute(
            """
            INSERT INTO star_transactions
                (user_id, username_sealed, amount_stars, item, status, payload, currency, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (user_id, seal_name(username, NAME_PURPOSE), amount_stars, item, status, payload, currency, now, now),
        )
        conn.commit()


def update_star_transaction(payload: str, status: str, charge_id: str | None = None) -> None:
    with pooled() as conn:
        conn.execute(
            "UPDATE star_transactions SET status = %s, charge_id = COALESCE(%s, charge_id), updated_at = %s "
            "WHERE payload = %s AND (status <> 'refunded' OR %s = 'refunded')",
            (status, charge_id, datetime.now(timezone.utc).isoformat(), payload, status),
        )
        conn.commit()


def bump_donation_action(user_id: int) -> tuple[int, str | None, int, bool]:
    with pooled() as conn:
        cur = conn.execute(
            """
            WITH bumped AS (
                INSERT INTO donation_prompts (user_id, action_count, last_shown_at)
                VALUES (%(uid)s, 1, NULL)
                ON CONFLICT (user_id) DO UPDATE
                   SET action_count = donation_prompts.action_count + 1
                RETURNING action_count, last_shown_at, times_shown
            )
            SELECT b.action_count, b.last_shown_at, b.times_shown,
                   EXISTS (SELECT 1 FROM star_transactions t
                            WHERE t.user_id = %(uid)s AND t.status = 'paid')
            FROM bumped b
            """,
            {"uid": user_id},
        )
        row = cur.fetchone()
        conn.commit()
        return row[0], row[1], row[2] or 0, bool(row[3])


def reset_donation_prompt(user_id: int) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with pooled() as conn:
        conn.execute(
            """
            INSERT INTO donation_prompts (user_id, action_count, last_shown_at, times_shown)
            VALUES (%s, 0, %s, 1)
            ON CONFLICT (user_id) DO UPDATE SET
                action_count = 0,
                last_shown_at = excluded.last_shown_at,
                times_shown = donation_prompts.times_shown + 1
            """,
            (user_id, now),
        )
        conn.commit()


def get_pinned_donation_message(user_id: int) -> int | None:
    with pooled_read() as conn:
        cur = conn.execute(
            "SELECT pinned_message_id FROM donation_prompts WHERE user_id = %s", (user_id,)
        )
        row = cur.fetchone()
        return row[0] if row else None


def set_pinned_donation_message(user_id: int, message_id: int | None) -> None:
    with pooled() as conn:
        conn.execute(
            """
            INSERT INTO donation_prompts (user_id, action_count, pinned_message_id)
            VALUES (%s, 0, %s)
            ON CONFLICT (user_id) DO UPDATE SET pinned_message_id = excluded.pinned_message_id
            """,
            (user_id, message_id),
        )
        conn.commit()


DOWNLOADS_PER_HOUR = int(os.environ.get("DBOT_DOWNLOADS_PER_HOUR", "12"))
DOWNLOADS_PER_DAY = int(os.environ.get("DBOT_DOWNLOADS_PER_DAY", "40"))
DONOR_MULTIPLIER = float(os.environ.get("DBOT_DONOR_MULTIPLIER", "4"))
DOWNLOAD_RETENTION_DAYS = int(os.environ.get("DBOT_DOWNLOAD_RETENTION_DAYS", "2"))


def download_allowance(user_id: int) -> dict:
    with pooled_read() as conn:
        cur = conn.execute(
            """
            SELECT
              count(*) FILTER (WHERE occurred_at > now() - interval '1 hour'),
              count(*) FILTER (WHERE occurred_at > now() - interval '1 day'),
              min(occurred_at) FILTER (WHERE occurred_at > now() - interval '1 hour'),
              min(occurred_at) FILTER (WHERE occurred_at > now() - interval '1 day'),
              EXISTS (SELECT 1 FROM star_transactions t
                       WHERE t.user_id = %(uid)s AND t.status = 'paid')
            FROM download_events WHERE user_id = %(uid)s
            """,
            {"uid": user_id},
        )
        used_hour, used_day, oldest_hour, oldest_day, donor = cur.fetchone()

    factor = DONOR_MULTIPLIER if donor else 1
    hour_max = int(DOWNLOADS_PER_HOUR * factor) if DOWNLOADS_PER_HOUR > 0 else 0
    day_max = int(DOWNLOADS_PER_DAY * factor) if DOWNLOADS_PER_DAY > 0 else 0
    state = {
        "hour": used_hour or 0, "day": used_day or 0,
        "hour_max": hour_max, "day_max": day_max,
        "donor": bool(donor), "allowed": True, "scope": None, "wait_min": 0,
    }

    def _minutes_until(oldest, seconds: int) -> int:
        if oldest is None:
            return 1
        gone = (datetime.now(timezone.utc) - oldest).total_seconds()
        return max(1, int((seconds - gone) // 60) + 1)

    if day_max and state["day"] >= day_max:
        state.update(allowed=False, scope="day",
                     wait_min=_minutes_until(oldest_day, 86400))
    elif hour_max and state["hour"] >= hour_max:
        state.update(allowed=False, scope="hour",
                     wait_min=_minutes_until(oldest_hour, 3600))
    return state


def record_download(user_id: int, platform: str | None = None) -> None:
    with pooled() as conn:
        conn.execute(
            "INSERT INTO download_events (user_id) VALUES (%s)",
            (user_id,),
        )
        conn.commit()


ACTIVITY_RETENTION_DAYS = int(os.environ.get("ACTIVITY_RETENTION_DAYS", "90"))


def load_provider_health() -> list[dict]:
    with pooled_read() as conn:
        rows = conn.execute(
            "SELECT provider, ok_count, fail_count, consecutive_fails, "
            "last_ok_at, last_fail_at, last_error FROM provider_health"
        ).fetchall()
    cols = ("provider", "ok_count", "fail_count", "consecutive_fails",
            "last_ok_at", "last_fail_at", "last_error")
    return [dict(zip(cols, row)) for row in rows]


def save_provider_health(rows: list[tuple]) -> None:
    if not rows:
        return
    with pooled() as conn:
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO provider_health (provider, ok_count, fail_count,
                        consecutive_fails, last_ok_at, last_fail_at, last_error)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (provider) DO UPDATE SET
                    ok_count = EXCLUDED.ok_count,
                    fail_count = EXCLUDED.fail_count,
                    consecutive_fails = EXCLUDED.consecutive_fails,
                    last_ok_at = EXCLUDED.last_ok_at,
                    last_fail_at = EXCLUDED.last_fail_at,
                    last_error = EXCLUDED.last_error
                """,
                rows,
            )
        conn.commit()


def _days_from_env(name: str, default: int) -> int:
    try:
        days = int(os.environ.get(name) or default)
    except ValueError:
        return default
    return days if days >= 1 else default


SENT_FILES_KEEP_DAYS = _days_from_env("DBOT_FILEID_KEEP_DAYS", 90)


def sent_files_get(post_key):
    with pooled_read() as conn:
        row = conn.execute(
            "SELECT items, confirmed_at, last_used FROM sent_files WHERE post_key = %s",
            (post_key,),
        ).fetchone()
    return (bytes(row[0]), row[1], row[2]) if row else None


def sent_files_put(post_key, items: bytes) -> None:
    with pooled() as conn:
        conn.execute(
            """
            INSERT INTO sent_files (post_key, items, confirmed_at, last_used)
            VALUES (%s, %s, now(), (now() AT TIME ZONE 'UTC')::date)
            ON CONFLICT (post_key) DO UPDATE SET
                items = EXCLUDED.items,
                confirmed_at = now(),
                last_used = (now() AT TIME ZONE 'UTC')::date
            """,
            (post_key, items),
        )
        conn.commit()


def sent_files_confirmed(post_key) -> None:
    with pooled() as conn:
        conn.execute(
            "UPDATE sent_files SET confirmed_at = now(), last_used = (now() AT TIME ZONE 'UTC')::date "
            "WHERE post_key = %s",
            (post_key,),
        )
        conn.commit()


def sent_files_used(post_key) -> None:
    with pooled() as conn:
        conn.execute(
            "UPDATE sent_files SET last_used = (now() AT TIME ZONE 'UTC')::date "
            "WHERE post_key = %s AND last_used < (now() AT TIME ZONE 'UTC')::date",
            (post_key,),
        )
        conn.commit()


def sent_files_drop(post_key) -> None:
    with pooled() as conn:
        conn.execute("DELETE FROM sent_files WHERE post_key = %s", (post_key,))
        conn.commit()


def prune_old_data() -> int:
    with pooled() as conn:
        cur = conn.execute(
            "DELETE FROM activity_events WHERE occurred_at < now() - make_interval(days => %s)",
            (ACTIVITY_RETENTION_DAYS,),
        )
        removed = cur.rowcount
        cur = conn.execute(
            "DELETE FROM download_events WHERE occurred_at < now() - make_interval(days => %s)",
            (DOWNLOAD_RETENTION_DAYS,),
        )
        removed += cur.rowcount
        cur = conn.execute(
            "DELETE FROM sent_files WHERE last_used < (now() AT TIME ZONE 'UTC')::date - make_interval(days => %s)",
            (SENT_FILES_KEEP_DAYS,),
        )
        removed += cur.rowcount
        conn.commit()
    return removed


def erase_user(user_id: int) -> int:
    removed = 0
    with pooled() as conn:
        for table in ("settings", "donation_prompts", "activity_events", "download_events"):
            cur = conn.execute(f"DELETE FROM {table} WHERE user_id = %s", (user_id,))
            removed += cur.rowcount or 0
        conn.execute(
            "UPDATE star_transactions SET username_sealed = NULL "
            "WHERE user_id = %s AND username_sealed IS NOT NULL",
            (user_id,),
        )
        if _plain_names.get("star_transactions"):
            conn.execute(
                "UPDATE star_transactions SET username = NULL "
                "WHERE user_id = %s AND username IS NOT NULL",
                (user_id,),
            )
        conn.commit()
    return removed


def dump_database_csv_zip() -> bytes:
    import csv
    import io
    import zipfile

    buf = io.BytesIO()
    with pooled() as conn, zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        cur = conn.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = %s AND table_type = 'BASE TABLE' ORDER BY table_name",
            (DB_SCHEMA,),
        )
        tables = [row[0] for row in cur.fetchall()]
        for table in tables:
            cur = conn.execute(f'SELECT * FROM "{table}"')
            columns = [desc.name for desc in cur.description]
            rows = cur.fetchall()
            csv_buf = io.StringIO()
            writer = csv.writer(csv_buf)
            writer.writerow(columns)
            writer.writerows(rows)
            zf.writestr(f"{table}.csv", csv_buf.getvalue())
    return buf.getvalue()
