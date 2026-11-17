import logging
import os
import time
import uuid
from contextlib import closing, contextmanager
from datetime import datetime, timezone

from urllib.parse import urlsplit

import psycopg
from psycopg_pool import ConnectionPool

import vault

_log = logging.getLogger(__name__)

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/anonbot"
)


DB_SCHEMA = os.environ.get("DB_SCHEMA", "public")

PAYER_DOMAIN = "anon.payer"

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


def _open_id(table: str, row, blob, purpose: str, expected: int) -> "int | None":
    try:
        return vault.open_id(blob, purpose, expected=expected)
    except (vault.VaultError, TypeError, ValueError):
        _log.warning("Skipping a row of %s: its sealed id did not open or does not belong "
                     "to the row (row %s)", table, row)
        return None


def init_db(dsn: str = DATABASE_URL) -> None:
    for _problem in check_database_url(dsn):
        logging.getLogger(__name__).warning("%s", _problem)

    ensure_schema(dsn)
    with closing(connect(dsn)) as conn:
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
        amount = _column(conn, "star_transactions", "amount_stars")
        if amount is not None and amount[0] != "bigint":
            conn.execute("ALTER TABLE star_transactions ALTER COLUMN amount_stars TYPE BIGINT")
        if _column(conn, "star_transactions", "currency") is None:
            conn.execute("ALTER TABLE star_transactions ADD COLUMN IF NOT EXISTS currency TEXT NOT NULL DEFAULT 'XTR'")
        if _column(conn, "star_transactions", "username_sealed") is None:
            conn.execute("ALTER TABLE star_transactions ADD COLUMN IF NOT EXISTS username_sealed BYTEA")
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
            CREATE TABLE IF NOT EXISTS user_settings (
                user_id BIGINT PRIMARY KEY,
                language TEXT,
                user_sealed BYTEA,
                from_activity BOOLEAN NOT NULL DEFAULT FALSE
            )
            """
        )
        if _column(conn, "user_settings", "user_sealed") is None:
            conn.execute("ALTER TABLE user_settings ADD COLUMN IF NOT EXISTS user_sealed BYTEA")
        if _column(conn, "user_settings", "from_activity") is None:
            conn.execute("ALTER TABLE user_settings ADD COLUMN IF NOT EXISTS "
                         "from_activity BOOLEAN NOT NULL DEFAULT FALSE")
        knows = _column(conn, "user_settings", "knows_reactions")
        if knows is None:
            conn.execute("ALTER TABLE user_settings ADD COLUMN IF NOT EXISTS knows_reactions BOOLEAN")
        if knows is None or (knows[1] or "").lower() != "true":
            conn.execute("ALTER TABLE user_settings ALTER COLUMN knows_reactions SET DEFAULT TRUE")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS anon_links (
                owner_user_id BIGINT PRIMARY KEY,
                token TEXT NOT NULL UNIQUE,
                is_paused INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                owner_sealed BYTEA
            )
            """
        )
        if _column(conn, "anon_links", "owner_sealed") is None:
            conn.execute("ALTER TABLE anon_links ADD COLUMN IF NOT EXISTS owner_sealed BYTEA")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS anon_conversations (
                id BIGSERIAL PRIMARY KEY,
                owner_user_id BIGINT NOT NULL,
                follower_user_id BIGINT NOT NULL,
                conv_number INTEGER NOT NULL,
                started_at TEXT NOT NULL,
                last_activity_at TEXT NOT NULL,
                last_owner_msg_id BIGINT,
                last_follower_msg_id BIGINT,
                owner_sealed BYTEA,
                follower_sealed BYTEA
            )
            """
        )
        if _column(conn, "anon_conversations", "owner_sealed") is None:
            conn.execute("ALTER TABLE anon_conversations ADD COLUMN IF NOT EXISTS owner_sealed BYTEA")
        if _column(conn, "anon_conversations", "follower_sealed") is None:
            conn.execute("ALTER TABLE anon_conversations ADD COLUMN IF NOT EXISTS follower_sealed BYTEA")
        if _column(conn, "anon_conversations", "follower_first_msg_at") is None:
            conn.execute("ALTER TABLE anon_conversations ADD COLUMN IF NOT EXISTS follower_first_msg_at TEXT")
        if _column(conn, "anon_conversations", "owner_replied_at") is not None:
            conn.execute("ALTER TABLE anon_conversations DROP COLUMN IF EXISTS owner_replied_at")
        if _column(conn, "anon_conversations", "archived_at") is None:
            conn.execute("ALTER TABLE anon_conversations ADD COLUMN IF NOT EXISTS archived_at TEXT")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_anon_conversations_pair "
            "ON anon_conversations (owner_user_id, follower_user_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_anon_conversations_owner_activity "
            "ON anon_conversations (owner_user_id, last_activity_at DESC)"
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_anon_conversations_natural "
            "ON anon_conversations (owner_user_id, follower_user_id, conv_number)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS anon_follower_state (
                follower_user_id BIGINT PRIMARY KEY,
                owner_user_id BIGINT NOT NULL,
                conversation_id BIGINT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS anon_relay (
                chat_id BIGINT NOT NULL,
                message_id BIGINT NOT NULL,
                conversation_id BIGINT NOT NULL,
                PRIMARY KEY (chat_id, message_id)
            )
            """
        )
        if _column(conn, "anon_relay", "is_prompt") is None:
            conn.execute("ALTER TABLE anon_relay ADD COLUMN IF NOT EXISTS is_prompt BOOLEAN NOT NULL DEFAULT FALSE")
        if _column(conn, "anon_relay", "peer_message_id") is None:
            conn.execute("ALTER TABLE anon_relay ADD COLUMN IF NOT EXISTS peer_message_id BIGINT")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_anon_relay_conversation "
            "ON anon_relay (chat_id, conversation_id, message_id)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS anon_blocks (
                owner_user_id BIGINT NOT NULL,
                follower_user_id BIGINT NOT NULL,
                blocked_at TEXT NOT NULL,
                PRIMARY KEY (owner_user_id, follower_user_id)
            )
            """
        )
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
            CREATE TABLE IF NOT EXISTS promo_seen (
                user_id BIGINT NOT NULL,
                promo TEXT NOT NULL,
                PRIMARY KEY (user_id, promo)
            )
            """
        )
        conn.commit()


def record_activity_batch(user_ids) -> None:
    people = sorted({int(u) for u in user_ids})
    if not people:
        return
    ids = [vault.pseudonym(u) for u in people]
    sealed = [vault.seal_id(u, "anon.user") for u in people]
    with pooled() as conn:
        conn.execute(
            "INSERT INTO activity_events (user_id, occurred_at) "
            "SELECT unnest(%s::bigint[]), now()",
            (ids,),
        )
        conn.execute(
            "INSERT INTO user_settings (user_id, user_sealed, from_activity) "
            "SELECT m.pseudo, m.sealed, TRUE FROM unnest(%s::bigint[], %s::bytea[]) AS m(pseudo, sealed) "
            "ON CONFLICT (user_id) DO NOTHING",
            (ids, sealed),
        )
        conn.execute(
            "UPDATE user_settings s SET user_sealed = m.sealed "
            "FROM unnest(%s::bigint[], %s::bytea[]) AS m(pseudo, sealed) "
            "WHERE s.user_id = m.pseudo AND s.user_sealed IS NULL",
            (ids, sealed),
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
            "SELECT DISTINCT a.user_id, s.user_sealed FROM activity_events a "
            "LEFT JOIN user_settings s ON s.user_id = a.user_id "
            "WHERE a.occurred_at >= %s",
            (since,),
        )
        rows = cur.fetchall()
    out, unresolved = [], 0
    for pseudonym, sealed in rows:
        if sealed is None:
            unresolved += 1
            continue
        real = _open_id("user_settings", pseudonym, sealed, "anon.user", pseudonym)
        if real is not None:
            out.append(real)
    if unresolved:
        _log.warning("%s recently active people have no sealed id, so an aimed broadcast "
                     "cannot reach them", unresolved)
    return out


def list_all_users() -> list[int]:
    with pooled_read() as conn:
        cur = conn.execute("SELECT user_id, user_sealed FROM user_settings")
        rows = cur.fetchall()
    out = []
    for pseudonym, sealed in rows:
        real = _open_id("user_settings", pseudonym, sealed, "anon.user", pseudonym)
        if real is not None:
            out.append(real)
    return out


def get_user_language(user_id: int) -> str | None:
    with pooled_read() as conn:
        cur = conn.execute(
            "SELECT language FROM user_settings WHERE user_id = %s", (vault.pseudonym(user_id),)
        )
        row = cur.fetchone()
        return row[0] if row else None


def set_user_language(user_id: int, language: str) -> None:
    with pooled() as conn:
        conn.execute(
            """
            INSERT INTO user_settings (user_id, language, user_sealed, from_activity) VALUES (%s, %s, %s, FALSE)
            ON CONFLICT (user_id) DO UPDATE SET
                language = excluded.language, user_sealed = excluded.user_sealed, from_activity = FALSE
            """,
            (vault.pseudonym(user_id), language, vault.seal_id(user_id, "anon.user")),
        )
        conn.commit()


def claim_reactions_note(user_id: int) -> bool:
    with pooled() as conn:
        row = conn.execute(
            "UPDATE user_settings SET knows_reactions = TRUE "
            "WHERE user_id = %s AND knows_reactions IS NOT TRUE RETURNING user_id",
            (vault.pseudonym(user_id),),
        ).fetchone()
        conn.commit()
    return row is not None


def claim_promo_seen(user_id: int, promo: str) -> bool:
    with pooled() as conn:
        row = conn.execute(
            "INSERT INTO promo_seen (user_id, promo) VALUES (%s, %s) "
            "ON CONFLICT DO NOTHING RETURNING user_id",
            (vault.pseudonym(user_id), promo),
        ).fetchone()
        conn.commit()
    return row is not None


def get_reaction_route(chat_id: int, message_id: int) -> "dict | None":
    with pooled_read() as conn:
        row = conn.execute(
            """
            SELECT r.peer_message_id, c.id, c.owner_user_id, c.follower_user_id,
                   EXISTS (SELECT 1 FROM anon_blocks b
                           WHERE b.owner_user_id = c.owner_user_id
                             AND b.follower_user_id = c.follower_user_id),
                   c.owner_sealed, c.follower_sealed
            FROM anon_relay r
            JOIN anon_conversations c ON c.id = r.conversation_id
            WHERE r.chat_id = %s AND r.message_id = %s
            """,
            (vault.pseudonym(chat_id), message_id),
        ).fetchone()
    if row is None or row[0] is None:
        return None
    owner = _open_id("anon_conversations", row[1], row[5], "anon.conversation.owner", row[2])
    follower = _open_id("anon_conversations", row[1], row[6], "anon.conversation.follower", row[3])
    if owner is None or follower is None:
        return None
    return {"peer_message_id": int(row[0]), "conversation_id": int(row[1]),
            "owner_user_id": owner, "follower_user_id": follower,
            "blocked": bool(row[4])}


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
            (vault.pseudonym_in(user_id, PAYER_DOMAIN), vault.seal_text(username, "stars.username"),
             amount_stars, item, status, payload, currency, now, now),
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
                            WHERE t.user_id = %(payer)s AND t.status = 'paid')
            FROM bumped b
            """,
            {"uid": vault.pseudonym(user_id), "payer": vault.pseudonym_in(user_id, PAYER_DOMAIN)},
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
            (vault.pseudonym(user_id), now),
        )
        conn.commit()


def get_pinned_donation_message(user_id: int) -> int | None:
    with pooled_read() as conn:
        cur = conn.execute(
            "SELECT pinned_message_id FROM donation_prompts WHERE user_id = %s",
            (vault.pseudonym(user_id),),
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
            (vault.pseudonym(user_id), message_id),
        )
        conn.commit()


def get_or_create_anon_link(owner_user_id: int) -> tuple[str, bool]:
    owner = vault.pseudonym(owner_user_id)
    with pooled() as conn:
        cur = conn.execute(
            "SELECT token, is_paused FROM anon_links WHERE owner_user_id = %s", (owner,)
        )
        row = cur.fetchone()
        if row:
            return row[0], bool(row[1])
        token = uuid.uuid4().hex
        conn.execute(
            "INSERT INTO anon_links (owner_user_id, owner_sealed, token, is_paused, created_at) "
            "VALUES (%s, %s, %s, 0, %s) ON CONFLICT (owner_user_id) DO NOTHING",
            (owner, vault.seal_id(owner_user_id, "anon.link.owner"), token,
             datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        return token, False


def regenerate_anon_link(owner_user_id: int) -> tuple[str, bool]:
    token = uuid.uuid4().hex
    with pooled() as conn:
        cur = conn.execute(
            """
            INSERT INTO anon_links (owner_user_id, owner_sealed, token, is_paused, created_at)
            VALUES (%s, %s, %s, 0, %s)
            ON CONFLICT (owner_user_id) DO UPDATE SET
                token = excluded.token, owner_sealed = excluded.owner_sealed
            RETURNING token, is_paused
            """,
            (vault.pseudonym(owner_user_id), vault.seal_id(owner_user_id, "anon.link.owner"),
             token, datetime.now(timezone.utc).isoformat()),
        )
        row = cur.fetchone()
        conn.commit()
    return row[0], bool(row[1])


def get_link_view(token: str, follower_user_id: int) -> tuple[int | None, bool, bool]:
    with pooled_read() as conn:
        cur = conn.execute(
            """
            SELECT l.owner_user_id, l.owner_sealed, l.is_paused,
                   EXISTS (SELECT 1 FROM anon_blocks b
                           WHERE b.owner_user_id = l.owner_user_id
                             AND b.follower_user_id = %s)
            FROM anon_links l WHERE l.token = %s
            """,
            (vault.pseudonym(follower_user_id), token),
        )
        row = cur.fetchone()
        if not row:
            return None, False, False
        owner = _open_id("anon_links", row[0], row[1], "anon.link.owner", expected=row[0])
        if owner is None:
            return None, False, False
        return owner, bool(row[2]), bool(row[3])


def get_delivery_context(owner_user_id: int, follower_user_id: int) -> tuple[bool, bool, str | None]:
    with pooled_read() as conn:
        cur = conn.execute(
            """
            SELECT EXISTS (SELECT 1 FROM anon_blocks
                           WHERE owner_user_id = %s AND follower_user_id = %s),
                   EXISTS (SELECT 1 FROM anon_links WHERE owner_user_id = %s),
                   (SELECT language FROM user_settings WHERE user_id = %s)
            """,
            (vault.pseudonym(owner_user_id), vault.pseudonym(follower_user_id),
             vault.pseudonym(owner_user_id), vault.pseudonym(owner_user_id)),
        )
        blocked, inbox_exists, language = cur.fetchone()
        return bool(blocked), bool(inbox_exists), language


def set_anon_link_paused(owner_user_id: int, paused: bool) -> None:
    with pooled() as conn:
        conn.execute(
            "UPDATE anon_links SET is_paused = %s WHERE owner_user_id = %s",
            (int(paused), vault.pseudonym(owner_user_id)),
        )
        conn.commit()


def get_anon_link_state(owner_user_id: int) -> dict | None:
    with pooled_read() as conn:
        cur = conn.execute(
            "SELECT token, is_paused, created_at FROM anon_links WHERE owner_user_id = %s",
            (vault.pseudonym(owner_user_id),),
        )
        row = cur.fetchone()
        if not row:
            return None
        return {"token": row[0], "is_paused": bool(row[1]), "created_at": row[2]}


def start_new_anon_conversation(owner_user_id: int, follower_user_id: int) -> dict:
    owner = vault.pseudonym(owner_user_id)
    follower = vault.pseudonym(follower_user_id)
    with pooled() as conn:
        cur = conn.execute(
            "SELECT COALESCE(MAX(conv_number), 0) FROM anon_conversations "
            "WHERE owner_user_id = %s",
            (owner,),
        )
        conv_number = cur.fetchone()[0] + 1
        now = datetime.now(timezone.utc).isoformat()
        cur = conn.execute(
            """
            INSERT INTO anon_conversations
                (owner_user_id, follower_user_id, owner_sealed, follower_sealed,
                 conv_number, started_at, last_activity_at,
                 last_owner_msg_id, last_follower_msg_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, NULL, NULL)
            RETURNING id
            """,
            (owner, follower,
             vault.seal_id(owner_user_id, "anon.conversation.owner"),
             vault.seal_id(follower_user_id, "anon.conversation.follower"),
             conv_number, now, now),
        )
        conversation_id = cur.fetchone()[0]
        conn.execute(
            """
            INSERT INTO anon_follower_state (follower_user_id, owner_user_id, conversation_id)
            VALUES (%s, %s, %s)
            ON CONFLICT (follower_user_id) DO UPDATE SET
                owner_user_id = excluded.owner_user_id, conversation_id = excluded.conversation_id
            """,
            (follower, owner, conversation_id),
        )
        conn.commit()
        return {
            "id": conversation_id, "conv_number": conv_number,
            "owner_user_id": owner_user_id, "follower_user_id": follower_user_id,
            "last_owner_msg_id": None, "last_follower_msg_id": None,
        }


def get_active_conversation_for_follower(follower_user_id: int) -> dict | None:
    with pooled_read() as conn:
        cur = conn.execute(
            "SELECT owner_user_id, conversation_id FROM anon_follower_state WHERE follower_user_id = %s",
            (vault.pseudonym(follower_user_id),),
        )
        row = cur.fetchone()
        if not row:
            return None
        owner_pseudonym, conversation_id = row
        cur = conn.execute(
            "SELECT conv_number, last_owner_msg_id, last_follower_msg_id, follower_first_msg_at, "
            "owner_sealed FROM anon_conversations WHERE id = %s AND owner_user_id = %s",
            (conversation_id, owner_pseudonym),
        )
        conv_row = cur.fetchone()
        if not conv_row:
            return None
        owner_user_id = _open_id("anon_conversations", conversation_id, conv_row[4],
                                 "anon.conversation.owner", expected=owner_pseudonym)
        if owner_user_id is None:
            return None
        return {
            "id": conversation_id, "owner_user_id": owner_user_id, "follower_user_id": follower_user_id,
            "conv_number": conv_row[0], "last_owner_msg_id": conv_row[1], "last_follower_msg_id": conv_row[2], "follower_first_msg_at": conv_row[3],
        }


def get_conversation(conversation_id: int) -> dict | None:
    with pooled_read() as conn:
        cur = conn.execute(
            """
            SELECT owner_user_id, follower_user_id, conv_number, last_owner_msg_id,
                   last_follower_msg_id, follower_first_msg_at, started_at,
                   last_activity_at, archived_at, owner_sealed, follower_sealed
            FROM anon_conversations WHERE id = %s
            """,
            (conversation_id,),
        )
        row = cur.fetchone()
        if not row:
            return None
        owner = _open_id("anon_conversations", conversation_id, row[9],
                         "anon.conversation.owner", expected=row[0])
        follower = _open_id("anon_conversations", conversation_id, row[10],
                            "anon.conversation.follower", expected=row[1])
        if owner is None or follower is None:
            return None
        return {
            "id": conversation_id, "owner_user_id": owner, "follower_user_id": follower,
            "conv_number": row[2], "last_owner_msg_id": row[3], "last_follower_msg_id": row[4],
            "follower_first_msg_at": row[5], "started_at": row[6],
            "last_activity_at": row[7], "archived_at": row[8],
        }


def record_anon_relays(chat_id: int, message_ids, conversation_id: int) -> None:
    ids = list(message_ids)
    if not ids:
        return
    with pooled() as conn:
        conn.execute(
            "INSERT INTO anon_relay (chat_id, message_id, conversation_id) "
            "SELECT %s, unnest(%s::bigint[]), %s "
            "ON CONFLICT (chat_id, message_id) DO UPDATE SET "
            "conversation_id = excluded.conversation_id",
            (vault.pseudonym(chat_id), ids, conversation_id),
        )
        conn.commit()


def forget_anon_relay(chat_id: int, message_id: int) -> None:
    with pooled() as conn:
        conn.execute(
            "DELETE FROM anon_relay WHERE chat_id = %s AND message_id = %s",
            (vault.pseudonym(chat_id), message_id),
        )
        conn.commit()


def record_relay_and_touch(
    conversation_id: int,
    owner_chat_id: int, owner_message_ids,
    follower_chat_id: int, follower_message_ids,
    last_owner_msg_id: int, last_follower_msg_id: int,
) -> None:
    with pooled() as conn:
        conn.execute(
            """
            WITH written AS (
                INSERT INTO anon_relay (chat_id, message_id, conversation_id, peer_message_id)
                SELECT chat_id, message_id, %(conv)s, peer FROM (
                    SELECT %(ochat)s::bigint AS chat_id, unnest(%(oids)s::bigint[]) AS message_id,
                           %(fmsg)s::bigint AS peer
                    UNION ALL
                    SELECT %(fchat)s::bigint, unnest(%(fids)s::bigint[]), %(omsg)s::bigint
                ) s
                ON CONFLICT (chat_id, message_id) DO UPDATE
                   SET conversation_id = excluded.conversation_id,
                       peer_message_id = excluded.peer_message_id
            )
            UPDATE anon_conversations
               SET last_owner_msg_id = %(omsg)s, last_follower_msg_id = %(fmsg)s,
                   last_activity_at = %(now)s, archived_at = NULL
             WHERE id = %(conv)s
            """,
            {
                "conv": conversation_id,
                "ochat": vault.pseudonym(owner_chat_id), "oids": list(owner_message_ids),
                "fchat": vault.pseudonym(follower_chat_id), "fids": list(follower_message_ids),
                "omsg": last_owner_msg_id, "fmsg": last_follower_msg_id,
                "now": datetime.now(timezone.utc).isoformat(),
            },
        )
        conn.commit()


def mark_follower_opened(conversation_id: int, chat_id: int) -> int | None:
    with pooled() as conn:
        cur = conn.execute(
            "UPDATE anon_conversations SET follower_first_msg_at = %s "
            "WHERE id = %s AND follower_first_msg_at IS NULL "
            "RETURNING (SELECT min(message_id) FROM anon_relay "
            "           WHERE chat_id = %s AND conversation_id = %s)",
            (datetime.now(timezone.utc).isoformat(), conversation_id,
             vault.pseudonym(chat_id), conversation_id),
        )
        row = cur.fetchone()
        conn.commit()
        return row[0] if row else None


def cancel_unopened_conversation(conversation_id: int, follower_user_id: int) -> bool:
    with pooled() as conn:
        cur = conn.execute(
            "DELETE FROM anon_conversations "
            "WHERE id = %s AND follower_user_id = %s AND follower_first_msg_at IS NULL "
            "AND last_follower_msg_id IS NULL AND last_owner_msg_id IS NULL",
            (conversation_id, vault.pseudonym(follower_user_id)),
        )
        if cur.rowcount == 0:
            conn.rollback()
            return False
        conn.execute("DELETE FROM anon_relay WHERE conversation_id = %s", (conversation_id,))
        conn.execute(
            "DELETE FROM anon_follower_state WHERE follower_user_id = %s AND conversation_id = %s",
            (vault.pseudonym(follower_user_id), conversation_id),
        )
        conn.commit()
        return True


def count_owner_conversations(owner_user_id: int) -> int:
    with pooled_read() as conn:
        cur = conn.execute(
            "SELECT count(*) FROM anon_conversations WHERE owner_user_id = %s "
            "AND (follower_first_msg_at IS NOT NULL OR last_owner_msg_id IS NOT NULL)",
            (vault.pseudonym(owner_user_id),),
        )
        return cur.fetchone()[0]


def get_routing_context(user_id: int, chat_id: int) -> dict:
    with pooled_read() as conn:
        cur = conn.execute(
            """
            WITH state AS (
                SELECT owner_user_id, conversation_id
                FROM anon_follower_state WHERE follower_user_id = %(uid)s
            )
            SELECT
              s.owner_user_id, s.conversation_id,
              c.conv_number, c.last_owner_msg_id, c.last_follower_msg_id, c.follower_first_msg_at,
              (SELECT count(*) FROM anon_conversations o
                WHERE o.owner_user_id = %(uid)s
                  AND (o.follower_first_msg_at IS NOT NULL OR o.last_owner_msg_id IS NOT NULL)),
              (SELECT r.conversation_id FROM anon_relay r
                WHERE r.chat_id = %(chat)s
                ORDER BY r.message_id DESC LIMIT 1),
              -- The newest thing this bot put in this chat for the ACTIVE
              -- conversation, which is the message a refusal points at. A
              -- refusal that says "reply to the message you are answering"
              -- without saying which one is the frustrating half of this
              -- rule; sending it as a reply to this id makes it visible.
              (SELECT r.message_id FROM anon_relay r
                WHERE r.chat_id = %(chat)s AND r.conversation_id = s.conversation_id
                ORDER BY r.message_id DESC LIMIT 1),
              c.owner_sealed
            FROM (SELECT 1) AS one
            LEFT JOIN state s ON TRUE
            LEFT JOIN anon_conversations c
                   ON c.id = s.conversation_id AND c.owner_user_id = s.owner_user_id
            """,
            {"uid": vault.pseudonym(user_id), "chat": vault.pseudonym(chat_id)},
        )
        row = cur.fetchone()
        owner_pseudonym, conversation_id = row[0], row[1]
        active = None
        if conversation_id is not None and row[2] is not None:
            owner_id = _open_id("anon_conversations", conversation_id, row[9],
                                "anon.conversation.owner", expected=owner_pseudonym)
            if owner_id is not None:
                active = {
                    "id": conversation_id, "owner_user_id": owner_id, "follower_user_id": user_id,
                    "conv_number": row[2], "last_owner_msg_id": row[3],
                    "last_follower_msg_id": row[4], "follower_first_msg_at": row[5],
                }
        newest = row[7]
        newest_is_active = newest is None or newest == conversation_id
        return {"active": active, "owned": row[6] or 0,
                "newest_is_active": newest_is_active,
                "active_anchor": row[8]}


def get_relay_row(chat_id: int, message_id: int) -> "dict | None":
    with pooled_read() as conn:
        cur = conn.execute(
            "SELECT conversation_id, peer_message_id FROM anon_relay "
            "WHERE chat_id = %s AND message_id = %s",
            (vault.pseudonym(chat_id), message_id),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return {"conversation_id": row[0], "peer_message_id": row[1]}


def get_conversation_for_relay(chat_id: int, message_id: int) -> "int | None":
    row = get_relay_row(chat_id, message_id)
    return row["conversation_id"] if row else None


def clear_follower_state(follower_user_id: int) -> bool:
    with pooled() as conn:
        cur = conn.execute(
            "DELETE FROM anon_follower_state WHERE follower_user_id = %s",
            (vault.pseudonym(follower_user_id),),
        )
        conn.commit()
        return cur.rowcount > 0


def set_anon_blocked(owner_user_id: int, follower_user_id: int, blocked: bool) -> None:
    owner = vault.pseudonym(owner_user_id)
    follower = vault.pseudonym(follower_user_id)
    with pooled() as conn:
        if blocked:
            conn.execute(
                "INSERT INTO anon_blocks (owner_user_id, follower_user_id, blocked_at) VALUES (%s, %s, %s) "
                "ON CONFLICT (owner_user_id, follower_user_id) DO NOTHING",
                (owner, follower, datetime.now(timezone.utc).isoformat()),
            )
        else:
            conn.execute(
                "DELETE FROM anon_blocks WHERE owner_user_id = %s AND follower_user_id = %s",
                (owner, follower),
            )
        conn.commit()


def list_anon_blocks_with_conversations(owner_user_id: int) -> list[tuple[int | None, list[int]]]:
    with pooled_read() as conn:
        cur = conn.execute(
            """
            SELECT MIN(c.id),
                   COALESCE(ARRAY_AGG(c.conv_number ORDER BY c.conv_number)
                            FILTER (WHERE c.conv_number IS NOT NULL), '{}')
            FROM anon_blocks b
            LEFT JOIN anon_conversations c
                   ON c.owner_user_id = b.owner_user_id
                  AND c.follower_user_id = b.follower_user_id
            WHERE b.owner_user_id = %s
            GROUP BY b.follower_user_id, b.blocked_at
            ORDER BY b.blocked_at DESC
            """,
            (vault.pseudonym(owner_user_id),),
        )
        return [(row[0], list(row[1])) for row in cur.fetchall()]


CONVERSATIONS_PER_PAGE = 6

_LIVE_CONVERSATIONS = """
    FROM anon_conversations c
    LEFT JOIN anon_blocks b
           ON b.owner_user_id = c.owner_user_id
          AND b.follower_user_id = c.follower_user_id
   WHERE c.owner_user_id = %(owner)s
     AND (c.follower_first_msg_at IS NOT NULL OR c.last_owner_msg_id IS NOT NULL)
"""


def count_owner_conversation_states(owner_user_id: int) -> dict:
    with pooled_read() as conn:
        cur = conn.execute(
            """
            SELECT
              count(*) FILTER (WHERE b.follower_user_id IS NULL AND c.archived_at IS NULL),
              count(*) FILTER (WHERE b.follower_user_id IS NULL AND c.archived_at IS NOT NULL),
              count(*) FILTER (WHERE b.follower_user_id IS NOT NULL)
            """
            + _LIVE_CONVERSATIONS,
            {"owner": vault.pseudonym(owner_user_id)},
        )
        row = cur.fetchone()
        return {"open": row[0] or 0, "archived": row[1] or 0, "blocked": row[2] or 0}


def list_owner_conversations(owner_user_id: int, archived: bool = False,
                             offset: int = 0, limit: int = CONVERSATIONS_PER_PAGE) -> list[dict]:
    with pooled_read() as conn:
        cur = conn.execute(
            """
            SELECT c.id, c.conv_number, c.started_at, c.last_activity_at, c.archived_at
            """
            + _LIVE_CONVERSATIONS
            + """
     AND b.follower_user_id IS NULL
     AND (c.archived_at IS NOT NULL) = %(archived)s
   ORDER BY c.last_activity_at DESC, c.conv_number DESC
   LIMIT %(limit)s OFFSET %(offset)s
            """,
            {"owner": vault.pseudonym(owner_user_id), "archived": archived,
             "limit": limit, "offset": max(0, offset)},
        )
        return [
            {"id": r[0], "conv_number": r[1], "started_at": r[2],
             "last_activity_at": r[3], "archived_at": r[4]}
            for r in cur.fetchall()
        ]


def set_conversation_archived(conversation_id: int, owner_user_id: int, archived: bool) -> bool:
    with pooled() as conn:
        cur = conn.execute(
            "UPDATE anon_conversations SET archived_at = %s "
            "WHERE id = %s AND owner_user_id = %s",
            (datetime.now(timezone.utc).isoformat() if archived else None,
             conversation_id, vault.pseudonym(owner_user_id)),
        )
        changed = cur.rowcount > 0
        conn.commit()
        return changed


def first_relay_message(chat_id: int, conversation_id: int) -> int | None:
    with pooled_read() as conn:
        cur = conn.execute(
            "SELECT min(message_id) FROM anon_relay "
            "WHERE chat_id = %s AND conversation_id = %s",
            (vault.pseudonym(chat_id), conversation_id),
        )
        row = cur.fetchone()
        return row[0] if row else None


def get_anon_stats(owner_user_id: int) -> dict:
    with pooled_read() as conn:
        cur = conn.execute(
            "SELECT COUNT(DISTINCT follower_user_id), COUNT(*) FROM anon_conversations "
            "WHERE owner_user_id = %s "
            "AND (follower_first_msg_at IS NOT NULL OR last_owner_msg_id IS NOT NULL)",
            (vault.pseudonym(owner_user_id),),
        )
        distinct_followers, conversations = cur.fetchone()
        return {"distinct_followers": distinct_followers or 0, "conversations": conversations or 0}


ACTIVITY_RETENTION_DAYS = int(os.environ.get("ACTIVITY_RETENTION_DAYS", "90"))
RELAY_RETENTION_DAYS = int(os.environ.get("RELAY_RETENTION_DAYS", "30"))


def prune_old_data() -> int:
    with pooled() as conn:
        cur = conn.execute(
            "DELETE FROM activity_events WHERE occurred_at < now() - make_interval(days => %s)",
            (ACTIVITY_RETENTION_DAYS,),
        )
        removed = cur.rowcount
        cur = conn.execute(
            "DELETE FROM user_settings s WHERE s.from_activity "
            "AND NOT EXISTS (SELECT 1 FROM activity_events a WHERE a.user_id = s.user_id)"
        )
        removed += cur.rowcount
        cur = conn.execute(
            """
            DELETE FROM anon_relay WHERE conversation_id IN (
                SELECT id FROM anon_conversations
                WHERE last_activity_at < to_char(
                    now() - make_interval(days => %s), 'YYYY-MM-DD"T"HH24:MI:SS'
                )
            )
            """,
            (RELAY_RETENTION_DAYS,),
        )
        removed += cur.rowcount
        cur = conn.execute(
            """
            UPDATE anon_conversations SET archived_at = %s
             WHERE archived_at IS NULL
               AND last_activity_at < to_char(
                   now() - make_interval(days => %s), 'YYYY-MM-DD"T"HH24:MI:SS'
               )
            """,
            (datetime.now(timezone.utc).isoformat(), RELAY_RETENTION_DAYS),
        )
        archived = cur.rowcount
        conn.commit()
    if archived:
        logging.getLogger(__name__).info(
            "Archived %s conversation(s) with no reply anchors left", archived)
    return removed


def erase_user(user_id: int) -> int:
    removed = 0
    person = vault.pseudonym(user_id)
    with pooled() as conn:
        cur = conn.execute(
            "SELECT id FROM anon_conversations WHERE owner_user_id = %s OR follower_user_id = %s",
            (person, person),
        )
        conversations = [row[0] for row in cur.fetchall()]
        if conversations:
            cur = conn.execute(
                "DELETE FROM anon_relay WHERE conversation_id = ANY(%s)", (conversations,)
            )
            removed += cur.rowcount or 0
            cur = conn.execute("DELETE FROM anon_conversations WHERE id = ANY(%s)", (conversations,))
            removed += cur.rowcount or 0
        for statement, params in (
            ("DELETE FROM anon_follower_state WHERE follower_user_id = %s OR owner_user_id = %s",
             (person, person)),
            ("DELETE FROM anon_blocks WHERE owner_user_id = %s OR follower_user_id = %s",
             (person, person)),
            ("DELETE FROM anon_links WHERE owner_user_id = %s", (person,)),
        ):
            cur = conn.execute(statement, params)
            removed += cur.rowcount or 0
        for table in ("user_settings", "donation_prompts", "activity_events", "promo_seen"):
            cur = conn.execute(f"DELETE FROM {table} WHERE user_id = %s", (person,))
            removed += cur.rowcount or 0
        conn.execute(
            "UPDATE star_transactions SET username_sealed = NULL "
            "WHERE user_id = %s AND username_sealed IS NOT NULL",
            (vault.pseudonym_in(user_id, PAYER_DOMAIN),),
        )
        conn.commit()
    return removed


class PersonalDataError(RuntimeError):
    pass


@contextmanager
def _in_schema(conn, schema: str):
    from psycopg import sql

    if conn.autocommit:
        raise RuntimeError("personal-data conversion runs inside the caller's transaction; "
                           "this connection is in autocommit mode")
    previous = conn.execute("SELECT current_setting('search_path')").fetchone()[0]
    conn.execute("SELECT set_config('search_path', %s, true)",
                 (sql.Identifier(schema).as_string(conn),))
    try:
        yield
    finally:
        try:
            conn.execute("SELECT set_config('search_path', %s, true)", (previous,))
        except Exception:
            pass


def _has_table(conn, schema: str, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_schema = %s AND table_name = %s",
        (schema, table),
    ).fetchone() is not None


def _has_column(conn, schema: str, table: str, column: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_schema = %s AND table_name = %s AND column_name = %s",
        (schema, table, column),
    ).fetchone() is not None


def _ready(conn, schema: str, counts: dict, table: str, *columns: str) -> bool:
    if not _has_table(conn, schema, table):
        counts[f"skipped:{table}"] = 0
        _log.info("Personal-data step skipped: no table %s in schema %s", table, schema)
        return False
    for column in columns:
        if not _has_column(conn, schema, table, column):
            counts[f"skipped:{table}.{column}"] = 0
            _log.info("Personal-data step skipped: no column %s.%s in schema %s", table, column, schema)
            return False
    return True


def _changed_one(cur, what: str) -> None:
    if cur.rowcount != 1:
        raise RuntimeError(f"personal-data conversion expected to change one row of {what}, "
                           f"changed {cur.rowcount}")


def _plain(value) -> bool:
    return value is not None and not vault.is_pseudonym(value)


def _pseudonyms_of(ids, domain: "str | None" = None) -> "tuple[list[int], list[int]]":
    plain = sorted({int(i) for i in ids if _plain(i)})
    if domain is None:
        return plain, [vault.pseudonym(i) for i in plain]
    return plain, [vault.pseudonym_in(i, domain) for i in plain]


def convert_personal_data_v1(conn, schema: str) -> dict:
    counts: dict = {}
    ceiling = vault.PSEUDONYM_CEILING
    with _in_schema(conn, schema):
        if _ready(conn, schema, counts, "anon_links", "owner_user_id"):
            if not _has_column(conn, schema, "anon_links", "owner_sealed"):
                conn.execute("ALTER TABLE anon_links ADD COLUMN IF NOT EXISTS owner_sealed BYTEA")
            rows = conn.execute(
                "SELECT owner_user_id FROM anon_links WHERE owner_user_id >= %s", (ceiling,)
            ).fetchall()
            for (owner,) in rows:
                _changed_one(conn.execute(
                    "UPDATE anon_links SET owner_user_id = %s, owner_sealed = %s WHERE owner_user_id = %s",
                    (vault.pseudonym(owner), vault.seal_id(owner, "anon.link.owner"), owner),
                ), "anon_links")
            counts["anon_links"] = len(rows)

        if _ready(conn, schema, counts, "anon_conversations", "id", "owner_user_id", "follower_user_id"):
            if not _has_column(conn, schema, "anon_conversations", "owner_sealed"):
                conn.execute("ALTER TABLE anon_conversations ADD COLUMN IF NOT EXISTS owner_sealed BYTEA")
            if not _has_column(conn, schema, "anon_conversations", "follower_sealed"):
                conn.execute("ALTER TABLE anon_conversations ADD COLUMN IF NOT EXISTS follower_sealed BYTEA")
            rows = conn.execute(
                "SELECT id, owner_user_id, follower_user_id, owner_sealed, follower_sealed "
                "FROM anon_conversations WHERE owner_user_id >= %s OR follower_user_id >= %s",
                (ceiling, ceiling),
            ).fetchall()
            for row_id, owner, follower, owner_sealed, follower_sealed in rows:
                if _plain(owner):
                    owner, owner_sealed = (vault.pseudonym(owner),
                                           vault.seal_id(owner, "anon.conversation.owner"))
                if _plain(follower):
                    follower, follower_sealed = (vault.pseudonym(follower),
                                                 vault.seal_id(follower, "anon.conversation.follower"))
                _changed_one(conn.execute(
                    "UPDATE anon_conversations SET owner_user_id = %s, follower_user_id = %s, "
                    "owner_sealed = %s, follower_sealed = %s WHERE id = %s",
                    (owner, follower, owner_sealed, follower_sealed, row_id),
                ), "anon_conversations")
            counts["anon_conversations"] = len(rows)

        if _ready(conn, schema, counts, "anon_follower_state", "follower_user_id", "owner_user_id"):
            rows = conn.execute(
                "SELECT follower_user_id, owner_user_id FROM anon_follower_state "
                "WHERE follower_user_id >= %s OR owner_user_id >= %s",
                (ceiling, ceiling),
            ).fetchall()
            for follower, owner in rows:
                _changed_one(conn.execute(
                    "UPDATE anon_follower_state SET follower_user_id = %s, owner_user_id = %s "
                    "WHERE follower_user_id = %s",
                    (vault.pseudonym(follower) if _plain(follower) else follower,
                     vault.pseudonym(owner) if _plain(owner) else owner,
                     follower),
                ), "anon_follower_state")
            counts["anon_follower_state"] = len(rows)

        if _ready(conn, schema, counts, "anon_relay", "chat_id"):
            plain, pseudo = _pseudonyms_of(
                r[0] for r in conn.execute(
                    "SELECT DISTINCT chat_id FROM anon_relay WHERE chat_id >= %s", (ceiling,)))
            changed = 0
            if plain:
                changed = conn.execute(
                    "UPDATE anon_relay t SET chat_id = m.pseudo "
                    "FROM unnest(%s::bigint[], %s::bigint[]) AS m(plain, pseudo) "
                    "WHERE t.chat_id = m.plain",
                    (plain, pseudo),
                ).rowcount
            counts["anon_relay"] = changed
            dropped = 0
            for column in ("sent_at", "from_bot"):
                if _has_column(conn, schema, "anon_relay", column):
                    dropped += 1
            if dropped:
                conn.execute("ALTER TABLE anon_relay DROP COLUMN IF EXISTS sent_at, "
                             "DROP COLUMN IF EXISTS from_bot")
            counts["anon_relay.columns_dropped"] = dropped

        if _ready(conn, schema, counts, "anon_blocks", "owner_user_id", "follower_user_id"):
            rows = conn.execute(
                "SELECT owner_user_id, follower_user_id FROM anon_blocks "
                "WHERE owner_user_id >= %s OR follower_user_id >= %s",
                (ceiling, ceiling),
            ).fetchall()
            for owner, follower in rows:
                _changed_one(conn.execute(
                    "UPDATE anon_blocks SET owner_user_id = %s, follower_user_id = %s "
                    "WHERE owner_user_id = %s AND follower_user_id = %s",
                    (vault.pseudonym(owner) if _plain(owner) else owner,
                     vault.pseudonym(follower) if _plain(follower) else follower,
                     owner, follower),
                ), "anon_blocks")
            counts["anon_blocks"] = len(rows)

        if _ready(conn, schema, counts, "user_settings", "user_id"):
            if not _has_column(conn, schema, "user_settings", "user_sealed"):
                conn.execute("ALTER TABLE user_settings ADD COLUMN IF NOT EXISTS user_sealed BYTEA")
            if not _has_column(conn, schema, "user_settings", "from_activity"):
                conn.execute("ALTER TABLE user_settings ADD COLUMN IF NOT EXISTS "
                             "from_activity BOOLEAN NOT NULL DEFAULT FALSE")
            rows = conn.execute(
                "SELECT user_id FROM user_settings WHERE user_id >= %s", (ceiling,)
            ).fetchall()
            for (user,) in rows:
                _changed_one(conn.execute(
                    "UPDATE user_settings SET user_id = %s, user_sealed = %s WHERE user_id = %s",
                    (vault.pseudonym(user), vault.seal_id(user, "anon.user"), user),
                ), "user_settings")
            counts["user_settings"] = len(rows)

        if _ready(conn, schema, counts, "donation_prompts", "user_id"):
            rows = conn.execute(
                "SELECT user_id FROM donation_prompts WHERE user_id >= %s", (ceiling,)
            ).fetchall()
            for (user,) in rows:
                _changed_one(conn.execute(
                    "UPDATE donation_prompts SET user_id = %s WHERE user_id = %s",
                    (vault.pseudonym(user), user),
                ), "donation_prompts")
            counts["donation_prompts"] = len(rows)

        if _ready(conn, schema, counts, "promo_seen", "user_id"):
            plain, pseudo = _pseudonyms_of(
                r[0] for r in conn.execute(
                    "SELECT DISTINCT user_id FROM promo_seen WHERE user_id >= %s", (ceiling,)))
            changed = 0
            if plain:
                changed = conn.execute(
                    "UPDATE promo_seen t SET user_id = m.pseudo "
                    "FROM unnest(%s::bigint[], %s::bigint[]) AS m(plain, pseudo) "
                    "WHERE t.user_id = m.plain",
                    (plain, pseudo),
                ).rowcount
            counts["promo_seen"] = changed
            dropped = 0
            if _has_column(conn, schema, "promo_seen", "seen_at"):
                conn.execute("ALTER TABLE promo_seen DROP COLUMN IF EXISTS seen_at")
                dropped = 1
            counts["promo_seen.columns_dropped"] = dropped

        if _ready(conn, schema, counts, "activity_events", "user_id"):
            plain, pseudo = _pseudonyms_of(
                r[0] for r in conn.execute(
                    "SELECT DISTINCT user_id FROM activity_events WHERE user_id >= %s", (ceiling,)))
            changed = 0
            added = 0
            if plain and _ready(conn, schema, counts, "user_settings", "user_id", "user_sealed",
                                "from_activity"):
                added = conn.execute(
                    "INSERT INTO user_settings (user_id, user_sealed, from_activity) "
                    "SELECT m.pseudo, m.sealed, TRUE FROM unnest(%s::bigint[], %s::bytea[]) AS m(pseudo, sealed) "
                    "ON CONFLICT (user_id) DO NOTHING",
                    (pseudo, [vault.seal_id(i, "anon.user") for i in plain]),
                ).rowcount
            counts["user_settings.added_for_activity"] = added
            if plain:
                changed = conn.execute(
                    "UPDATE activity_events t SET user_id = m.pseudo "
                    "FROM unnest(%s::bigint[], %s::bigint[]) AS m(plain, pseudo) "
                    "WHERE t.user_id = m.plain",
                    (plain, pseudo),
                ).rowcount
            counts["activity_events"] = changed

        if _ready(conn, schema, counts, "star_transactions", "id", "user_id"):
            if not _has_column(conn, schema, "star_transactions", "username_sealed"):
                conn.execute("ALTER TABLE star_transactions ADD COLUMN IF NOT EXISTS username_sealed BYTEA")
            plain, pseudo = _pseudonyms_of(
                (r[0] for r in conn.execute(
                    "SELECT DISTINCT user_id FROM star_transactions WHERE user_id >= %s", (ceiling,))),
                PAYER_DOMAIN)
            changed = 0
            if plain:
                changed = conn.execute(
                    "UPDATE star_transactions t SET user_id = m.pseudo "
                    "FROM unnest(%s::bigint[], %s::bigint[]) AS m(plain, pseudo) "
                    "WHERE t.user_id = m.plain",
                    (plain, pseudo),
                ).rowcount
            counts["star_transactions"] = changed
            sealed_names, dropped = 0, 0
            if _has_column(conn, schema, "star_transactions", "username"):
                rows = conn.execute(
                    "SELECT id, username FROM star_transactions "
                    "WHERE username IS NOT NULL AND username_sealed IS NULL"
                ).fetchall()
                for row_id, username in rows:
                    _changed_one(conn.execute(
                        "UPDATE star_transactions SET username_sealed = %s WHERE id = %s",
                        (vault.seal_text(username, "stars.username"), row_id),
                    ), "star_transactions")
                sealed_names = len(rows)
                conn.execute("ALTER TABLE star_transactions DROP COLUMN IF EXISTS username")
                dropped = 1
            counts["star_transactions.username_sealed"] = sealed_names
            counts["star_transactions.username_dropped"] = dropped
    return counts


_IDENTITY_CHECKS = (
    ("anon_links", "owner_user_id",
     "SELECT count(*), count(*) FILTER (WHERE owner_user_id >= %s) FROM anon_links"),
    ("anon_conversations", "owner_user_id",
     "SELECT count(*), count(*) FILTER (WHERE owner_user_id >= %s) FROM anon_conversations"),
    ("anon_conversations", "follower_user_id",
     "SELECT count(*), count(*) FILTER (WHERE follower_user_id >= %s) FROM anon_conversations"),
    ("anon_follower_state", "follower_user_id",
     "SELECT count(*), count(*) FILTER (WHERE follower_user_id >= %s) FROM anon_follower_state"),
    ("anon_follower_state", "owner_user_id",
     "SELECT count(*), count(*) FILTER (WHERE owner_user_id >= %s) FROM anon_follower_state"),
    ("anon_relay", "chat_id",
     "SELECT count(*), count(*) FILTER (WHERE chat_id >= %s) FROM anon_relay"),
    ("anon_blocks", "owner_user_id",
     "SELECT count(*), count(*) FILTER (WHERE owner_user_id >= %s) FROM anon_blocks"),
    ("anon_blocks", "follower_user_id",
     "SELECT count(*), count(*) FILTER (WHERE follower_user_id >= %s) FROM anon_blocks"),
    ("user_settings", "user_id",
     "SELECT count(*), count(*) FILTER (WHERE user_id >= %s) FROM user_settings"),
    ("donation_prompts", "user_id",
     "SELECT count(*), count(*) FILTER (WHERE user_id >= %s) FROM donation_prompts"),
    ("promo_seen", "user_id",
     "SELECT count(*), count(*) FILTER (WHERE user_id >= %s) FROM promo_seen"),
    ("activity_events", "user_id",
     "SELECT count(*), count(*) FILTER (WHERE user_id >= %s) FROM activity_events"),
    ("star_transactions", "user_id",
     "SELECT count(*), count(*) FILTER (WHERE user_id >= %s) FROM star_transactions"),
)


_PAYER_CLASH_CHECKS = (
    ("anon_links", "owner_user_id",
     "SELECT count(*) FROM star_transactions t WHERE t.user_id IN (SELECT owner_user_id FROM anon_links)"),
    ("anon_conversations", "owner_user_id",
     "SELECT count(*) FROM star_transactions t WHERE t.user_id IN (SELECT owner_user_id FROM anon_conversations)"),
    ("anon_conversations", "follower_user_id",
     "SELECT count(*) FROM star_transactions t WHERE t.user_id IN (SELECT follower_user_id FROM anon_conversations)"),
    ("anon_follower_state", "follower_user_id",
     "SELECT count(*) FROM star_transactions t WHERE t.user_id IN (SELECT follower_user_id FROM anon_follower_state)"),
    ("anon_follower_state", "owner_user_id",
     "SELECT count(*) FROM star_transactions t WHERE t.user_id IN (SELECT owner_user_id FROM anon_follower_state)"),
    ("anon_relay", "chat_id",
     "SELECT count(*) FROM star_transactions t WHERE t.user_id IN (SELECT chat_id FROM anon_relay)"),
    ("anon_blocks", "owner_user_id",
     "SELECT count(*) FROM star_transactions t WHERE t.user_id IN (SELECT owner_user_id FROM anon_blocks)"),
    ("anon_blocks", "follower_user_id",
     "SELECT count(*) FROM star_transactions t WHERE t.user_id IN (SELECT follower_user_id FROM anon_blocks)"),
    ("user_settings", "user_id",
     "SELECT count(*) FROM star_transactions t WHERE t.user_id IN (SELECT user_id FROM user_settings)"),
    ("donation_prompts", "user_id",
     "SELECT count(*) FROM star_transactions t WHERE t.user_id IN (SELECT user_id FROM donation_prompts)"),
    ("promo_seen", "user_id",
     "SELECT count(*) FROM star_transactions t WHERE t.user_id IN (SELECT user_id FROM promo_seen)"),
    ("activity_events", "user_id",
     "SELECT count(*) FROM star_transactions t WHERE t.user_id IN (SELECT user_id FROM activity_events)"),
)


def _opens(opener, *args, **kwargs) -> bool:
    try:
        opener(*args, **kwargs)
        return True
    except (vault.VaultError, TypeError, ValueError):
        return False


def verify_personal_data_v1(conn, schema: str) -> dict:
    counts: dict = {}
    problems: list = []
    ceiling = vault.PSEUDONYM_CEILING
    with _in_schema(conn, schema):
        for table, column, statement in _IDENTITY_CHECKS:
            if not _ready(conn, schema, counts, table, column):
                continue
            total, plain = conn.execute(statement, (ceiling,)).fetchone()
            counts[table] = total
            if plain:
                problems.append(f"{table}.{column}: {plain} value(s) are not pseudonyms")

        def sealed_column(table: str, column: str) -> bool:
            if _has_column(conn, schema, table, column):
                return True
            if counts.get(table):
                problems.append(f"{table}.{column}: the column is missing")
            return False

        def tally(table: str, column: str, opened: int, total: int) -> None:
            counts[f"{table}.{column}"] = opened
            if opened != total:
                problems.append(f"{table}.{column}: {total - opened} of {total} sealed value(s) "
                                f"are missing, do not open, or do not belong to their row")

        if _ready(conn, schema, counts, "anon_links", "owner_user_id") \
            and sealed_column("anon_links", "owner_sealed"):
            rows = conn.execute("SELECT owner_user_id, owner_sealed FROM anon_links").fetchall()
            tally("anon_links", "owner_sealed",
                  sum(_opens(vault.open_id, sealed, "anon.link.owner", expected=owner)
                      for owner, sealed in rows), len(rows))

        if _ready(conn, schema, counts, "anon_conversations", "owner_user_id", "follower_user_id"):
            if sealed_column("anon_conversations", "owner_sealed") \
                and sealed_column("anon_conversations", "follower_sealed"):
                rows = conn.execute(
                    "SELECT owner_user_id, follower_user_id, owner_sealed, follower_sealed "
                    "FROM anon_conversations").fetchall()
                tally("anon_conversations", "owner_sealed",
                      sum(_opens(vault.open_id, o_sealed, "anon.conversation.owner", expected=owner)
                          for owner, _f, o_sealed, _fs in rows), len(rows))
                tally("anon_conversations", "follower_sealed",
                      sum(_opens(vault.open_id, f_sealed, "anon.conversation.follower", expected=follower)
                          for _o, follower, _os, f_sealed in rows), len(rows))

        if _ready(conn, schema, counts, "user_settings", "user_id") \
            and sealed_column("user_settings", "user_sealed"):
            rows = conn.execute("SELECT user_id, user_sealed FROM user_settings").fetchall()
            tally("user_settings", "user_sealed",
                  sum(_opens(vault.open_id, sealed, "anon.user", expected=user)
                      for user, sealed in rows), len(rows))

        if _ready(conn, schema, counts, "anon_relay", "chat_id"):
            for column in ("sent_at", "from_bot"):
                if _has_column(conn, schema, "anon_relay", column):
                    problems.append(f"anon_relay.{column}: the column is still there")
        if _has_table(conn, schema, "promo_seen") and _has_column(conn, schema, "promo_seen", "seen_at"):
            problems.append("promo_seen.seen_at: the column is still there")

        if _ready(conn, schema, counts, "activity_events", "user_id")                 and _ready(conn, schema, counts, "user_settings", "user_id"):
            (unreachable,) = conn.execute(
                "SELECT count(*) FROM (SELECT DISTINCT user_id FROM activity_events) a "
                "WHERE NOT EXISTS (SELECT 1 FROM user_settings s WHERE s.user_id = a.user_id)"
            ).fetchone()
            counts["activity_events.without_settings"] = unreachable
            if unreachable:
                problems.append(f"activity_events: {unreachable} person(s) have no user_settings "
                                f"row, so a broadcast cannot reach them")

        if _ready(conn, schema, counts, "star_transactions", "user_id"):
            clashes = 0
            for table, column, statement in _PAYER_CLASH_CHECKS:
                if _has_table(conn, schema, table) and _has_column(conn, schema, table, column):
                    clashes += conn.execute(statement).fetchone()[0]
            counts["star_transactions.payer_clashes"] = clashes
            if clashes:
                problems.append(f"star_transactions.user_id: {clashes} payment(s) are stored under a "
                                f"person's pseudonym(), so they can be lined up with that person's rows")
        if _has_table(conn, schema, "star_transactions"):
            if _has_column(conn, schema, "star_transactions", "username_sealed"):
                rows = conn.execute(
                    "SELECT username_sealed FROM star_transactions WHERE username_sealed IS NOT NULL"
                ).fetchall()
                tally("star_transactions", "username_sealed",
                      sum(_opens(vault.open_text, sealed, "stars.username") for (sealed,) in rows),
                      len(rows))
            if _has_column(conn, schema, "star_transactions", "username"):
                problems.append("star_transactions.username: the plain column is still there")
    if problems:
        raise PersonalDataError(f"schema {schema} is not fully sealed: " + "; ".join(problems))
    return counts


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
