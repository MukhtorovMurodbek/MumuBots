import logging
import os
import time
from contextlib import closing, contextmanager
from datetime import datetime, timezone

from urllib.parse import urlsplit

import psycopg
from psycopg_pool import ConnectionPool

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/convertbot"
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
            CREATE TABLE IF NOT EXISTS user_settings (
                user_id BIGINT PRIMARY KEY,
                language TEXT
            )
            """
        )
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
            CREATE TABLE IF NOT EXISTS conversions_in_flight (
                job_id TEXT PRIMARY KEY,
                user_id BIGINT NOT NULL,
                chat_id BIGINT NOT NULL,
                price INTEGER NOT NULL,
                lang TEXT,
                pair TEXT,
                owner TEXT NOT NULL,
                touched_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                recovered BOOLEAN NOT NULL DEFAULT false
            )
            """
        )
        _note_plain_names(conn)
        conn.commit()


def hold_conversion(job_id: str, user_id: int, chat_id: int, price: int, lang: str | None,
                    pair: str, owner: str) -> None:
    with pooled() as conn:
        conn.execute(
            "INSERT INTO conversions_in_flight (job_id, user_id, chat_id, price, lang, pair, owner) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT (job_id) DO NOTHING",
            (job_id, user_id, chat_id, price, lang, pair, owner),
        )
        conn.commit()


def touch_conversions(owner: str) -> int:
    with pooled() as conn:
        cur = conn.execute(
            "UPDATE conversions_in_flight SET touched_at = now() WHERE owner = %s AND NOT recovered",
            (owner,),
        )
        conn.commit()
    return cur.rowcount or 0


def release_conversion(job_id: str) -> str:
    with pooled() as conn:
        row = conn.execute(
            "DELETE FROM conversions_in_flight WHERE job_id = %s RETURNING recovered", (job_id,),
        ).fetchone()
        conn.commit()
    if row is None:
        return "absent"
    return "recovered" if row[0] else "held"


def claim_orphaned_conversions(owner: str, stale_seconds: int) -> list[dict]:
    with pooled() as conn:
        rows = conn.execute(
            "UPDATE conversions_in_flight SET recovered = true, touched_at = now() "
            "WHERE NOT recovered AND owner <> %s "
            "AND touched_at < now() - make_interval(secs => %s) "
            "RETURNING job_id, user_id, chat_id, price, lang, pair",
            (owner, stale_seconds),
        ).fetchall()
        conn.commit()
    return [dict(zip(("job_id", "user_id", "chat_id", "price", "lang", "pair"), row)) for row in rows]


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
            "SELECT user_id FROM user_settings "
            "UNION "
            "SELECT DISTINCT user_id FROM activity_events"
        )
        return [int(r[0]) for r in cur.fetchall()]


def get_user_language(user_id: int) -> str | None:
    with pooled_read() as conn:
        cur = conn.execute("SELECT language FROM user_settings WHERE user_id = %s", (user_id,))
        row = cur.fetchone()
        return row[0] if row else None


def set_user_language(user_id: int, language: str) -> None:
    with pooled() as conn:
        conn.execute(
            """
            INSERT INTO user_settings (user_id, language) VALUES (%s, %s)
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


def _username(sealed, plain=None):
    return open_name(sealed, NAME_PURPOSE) or plain


def get_star_transaction(payload: str) -> dict | None:
    plain = ", username" if _plain_names.get("star_transactions") else ", NULL"
    with pooled_read() as conn:
        cur = conn.execute(
            "SELECT user_id, username_sealed, amount_stars, item, status, payload, charge_id, created_at"
            f"{plain} FROM star_transactions WHERE payload = %s",
            (payload,),
        )
        row = cur.fetchone()
        if not row:
            return None
        keys = ["user_id", "username", "amount_stars", "item", "status", "payload", "charge_id", "created_at"]
        found = dict(zip(keys, row))
        found["username"] = _username(row[1], row[8])
        return found


def get_user_star_summary(user_id: int, limit: int = 20) -> tuple[int, list[dict]]:
    with pooled_read() as conn:
        cur = conn.execute(
            "SELECT COALESCE(SUM(amount_stars), 0) FROM star_transactions "
            "WHERE user_id = %s AND status = 'paid' AND currency = 'XTR'",
            (user_id,),
        )
        total = cur.fetchone()[0]
        cur = conn.execute(
            """
            SELECT amount_stars, item, status, currency, created_at FROM star_transactions
            WHERE user_id = %s ORDER BY created_at DESC LIMIT %s
            """,
            (user_id, limit),
        )
        history = [
            {"amount_stars": r[0], "item": r[1], "status": r[2], "currency": r[3], "created_at": r[4]}
            for r in cur.fetchall()
        ]
        return total, history


def get_star_ledger(limit: int = 20) -> tuple[dict, list[dict]]:
    with pooled_read() as conn:
        cur = conn.execute(
            """
            SELECT
                COALESCE(SUM(amount_stars) FILTER (WHERE status = 'paid'), 0),
                COUNT(*) FILTER (WHERE status = 'paid'),
                COALESCE(SUM(amount_stars) FILTER (WHERE status = 'refunded'), 0),
                COUNT(*) FILTER (WHERE status = 'refunded'),
                COUNT(*) FILTER (WHERE status = 'free'),
                COUNT(DISTINCT user_id) FILTER (WHERE status = 'paid')
            FROM star_transactions WHERE currency = 'XTR'
            """
        )
        paid_total, paid_count, refunded_total, refunded_count, free_count, paying_users = cur.fetchone()
        summary = {
            "paid_total": paid_total,
            "paid_count": paid_count,
            "refunded_total": refunded_total,
            "refunded_count": refunded_count,
            "free_count": free_count,
            "paying_users": paying_users,
        }
        plain = ", username" if _plain_names.get("star_transactions") else ", NULL"
        cur = conn.execute(
            f"""
            SELECT user_id, username_sealed, amount_stars, item, status, currency, created_at{plain}
            FROM star_transactions ORDER BY created_at DESC LIMIT %s
            """,
            (limit,),
        )
        recent = [
            {
                "user_id": r[0], "username": _username(r[1], r[7]), "amount_stars": r[2],
                "item": r[3], "status": r[4], "currency": r[5], "created_at": r[6],
            }
            for r in cur.fetchall()
        ]
        return summary, recent


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


ACTIVITY_RETENTION_DAYS = int(os.environ.get("ACTIVITY_RETENTION_DAYS", "90"))


def prune_old_data() -> int:
    with pooled() as conn:
        cur = conn.execute(
            "DELETE FROM activity_events WHERE occurred_at < now() - make_interval(days => %s)",
            (ACTIVITY_RETENTION_DAYS,),
        )
        removed = cur.rowcount
        cur = conn.execute(
            "DELETE FROM conversions_in_flight WHERE (recovered AND touched_at < now() - interval '1 day') "
            "OR touched_at < now() - interval '2 days'"
        )
        removed += cur.rowcount or 0
        conn.commit()
    return removed


def erase_user(user_id: int) -> int:
    removed = 0
    with pooled() as conn:
        for table in ("user_settings", "donation_prompts", "activity_events", "conversions_in_flight"):
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
