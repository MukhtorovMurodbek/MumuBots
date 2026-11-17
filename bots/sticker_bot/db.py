import logging
import os
import time
import uuid
from contextlib import closing, contextmanager
from datetime import datetime, timezone

from urllib.parse import urlsplit

import psycopg
from psycopg_pool import ConnectionPool

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/stickerbot"
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
            CREATE TABLE IF NOT EXISTS packs (
                id BIGSERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                name TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL,
                created_at TEXT NOT NULL,
                owner_sealed BYTEA
            )
            """
        )
        if _column(conn, "packs", "owner_sealed") is None:
            conn.execute("ALTER TABLE packs ADD COLUMN IF NOT EXISTS owner_sealed BYTEA")
        if _column(conn, "packs", "public") is None:
            conn.execute("ALTER TABLE packs ADD COLUMN IF NOT EXISTS public BOOLEAN NOT NULL DEFAULT FALSE")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS group_members (
                chat_id BIGINT NOT NULL,
                user_id BIGINT NOT NULL,
                seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                PRIMARY KEY (chat_id, user_id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pack_editors (
                pack_name TEXT NOT NULL,
                user_id BIGINT NOT NULL,
                added_at TEXT NOT NULL,
                PRIMARY KEY (pack_name, user_id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pack_share_tokens (
                pack_name TEXT PRIMARY KEY,
                token TEXT NOT NULL UNIQUE
            )
            """
        )
        if _column(conn, "pack_share_tokens", "max_editors") is None:
            conn.execute("ALTER TABLE pack_share_tokens ADD COLUMN IF NOT EXISTS max_editors INTEGER "
                         f"NOT NULL DEFAULT {DEFAULT_MAX_EDITORS}")
        if _column(conn, "pack_share_tokens", "valid_days") is None:
            conn.execute("ALTER TABLE pack_share_tokens ADD COLUMN IF NOT EXISTS valid_days INTEGER "
                         f"DEFAULT {DEFAULT_VALID_DAYS}")
        if _column(conn, "pack_share_tokens", "expires_at") is None:
            conn.execute("ALTER TABLE pack_share_tokens ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ "
                         f"DEFAULT (now() + interval '{DEFAULT_VALID_DAYS} days')")
        if _column(conn, "pack_share_tokens", "editors_delete") is None:
            conn.execute("ALTER TABLE pack_share_tokens ADD COLUMN IF NOT EXISTS editors_delete BOOLEAN "
                         "NOT NULL DEFAULT FALSE")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pack_invites (
                pack_name TEXT NOT NULL,
                username_mark TEXT NOT NULL,
                username_sealed BYTEA,
                added_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                PRIMARY KEY (pack_name, username_mark)
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
            CREATE TABLE IF NOT EXISTS user_settings (
                user_id BIGINT PRIMARY KEY,
                language TEXT
            )
            """
        )
        _note_plain_names(conn)
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


def add_pack(
    user_id: int,
    name: str,
    title: str,
    owner_username: str | None = None,
    owner_name: str | None = None,
) -> None:
    with pooled() as conn:
        owner = {"username": owner_username, "name": owner_name} if owner_username or owner_name else None
        conn.execute(
            """
            INSERT INTO packs (user_id, name, title, created_at, owner_sealed)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (user_id, name, title, datetime.now(timezone.utc).isoformat(), seal_name(owner, OWNER_PURPOSE)),
        )
        conn.commit()


def get_user_packs(user_id: int) -> list[tuple[str, str]]:
    with pooled_read() as conn:
        cur = conn.execute(
            "SELECT name, title FROM packs WHERE user_id = %s ORDER BY created_at DESC",
            (user_id,),
        )
        return cur.fetchall()


def get_pack_owner(name: str) -> int | None:
    with pooled_read() as conn:
        cur = conn.execute("SELECT user_id FROM packs WHERE name = %s", (name,))
        row = cur.fetchone()
        return row[0] if row else None


def get_pack_creator_info(name: str) -> dict | None:
    plain = ", owner_username, owner_name" if _plain_names.get("packs") else ", NULL, NULL"
    with pooled_read() as conn:
        cur = conn.execute(
            f"SELECT user_id, title, created_at, owner_sealed{plain} FROM packs WHERE name = %s",
            (name,),
        )
        row = cur.fetchone()
        if not row:
            return None
        owner = open_name(row[3], OWNER_PURPOSE, as_json=True)
        if not isinstance(owner, dict):
            owner = {"username": row[4], "name": row[5]}
        return {
            "owner_id": row[0],
            "title": row[1],
            "created_at": row[2],
            "owner_username": owner.get("username"),
            "owner_name": owner.get("name"),
        }


def get_pack_title(name: str) -> str | None:
    with pooled_read() as conn:
        cur = conn.execute("SELECT title FROM packs WHERE name = %s", (name,))
        row = cur.fetchone()
        return row[0] if row else None


def set_pack_title(name: str, title: str) -> None:
    with pooled() as conn:
        conn.execute("UPDATE packs SET title = %s WHERE name = %s", (title, name))
        conn.commit()


def set_pack_titles(titles: "dict[str, str]") -> None:
    with pooled() as conn:
        with conn.cursor() as cur:
            cur.executemany("UPDATE packs SET title = %s WHERE name = %s",
                            [(title, name) for name, title in titles.items()])
        conn.commit()


def add_editor(pack_name: str, user_id: int) -> None:
    with pooled() as conn:
        conn.execute(
            "INSERT INTO pack_editors (pack_name, user_id, added_at) VALUES (%s, %s, %s) "
            "ON CONFLICT (pack_name, user_id) DO NOTHING",
            (pack_name, user_id, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()


DEFAULT_MAX_EDITORS = 10
DEFAULT_VALID_DAYS = 7
MAX_EDITORS_CHOICES = (3, 5, 10, 20, 50)
VALID_DAYS_CHOICES = (1, 7, 30, None)
INVITE_KEEP_DAYS = 30
INVITE_PURPOSE = "sticker.invite.username"


def get_coedit_view(pack_name: str) -> dict:
    with pooled() as conn:
        conn.execute(
            "INSERT INTO pack_share_tokens (pack_name, token, max_editors, valid_days, expires_at) "
            "VALUES (%s, %s, %s, %s, now() + make_interval(days => %s)) ON CONFLICT (pack_name) DO NOTHING",
            (pack_name, uuid.uuid4().hex, DEFAULT_MAX_EDITORS, DEFAULT_VALID_DAYS, DEFAULT_VALID_DAYS),
        )
        row = conn.execute(
            """
            SELECT t.token, p.title, p.id,
                   (SELECT COUNT(*) FROM pack_editors e WHERE e.pack_name = t.pack_name),
                   (SELECT COUNT(*) FROM pack_invites i WHERE i.pack_name = t.pack_name),
                   t.max_editors, t.valid_days, t.expires_at, t.editors_delete
              FROM pack_share_tokens t LEFT JOIN packs p ON p.name = t.pack_name
             WHERE t.pack_name = %s
            """,
            (pack_name,),
        ).fetchone()
        conn.commit()
    keys = ("token", "title", "pack_id", "editors", "invited", "max_editors", "valid_days", "expires_at",
            "editors_delete")
    return dict(zip(keys, row))


def reset_share_token(pack_name: str) -> str:
    token = uuid.uuid4().hex
    with pooled() as conn:
        conn.execute(
            """
            INSERT INTO pack_share_tokens (pack_name, token, max_editors, valid_days, expires_at)
            VALUES (%(name)s, %(token)s, %(most)s, %(days)s, now() + make_interval(days => %(days)s))
            ON CONFLICT (pack_name) DO UPDATE SET
                token = excluded.token,
                expires_at = CASE WHEN pack_share_tokens.valid_days IS NULL THEN NULL
                                  ELSE now() + make_interval(days => pack_share_tokens.valid_days) END
            """,
            {"name": pack_name, "token": token, "most": DEFAULT_MAX_EDITORS, "days": DEFAULT_VALID_DAYS},
        )
        conn.commit()
    return token


def set_link_valid_days(pack_name: str, days: "int | None") -> None:
    get_coedit_view(pack_name)
    with pooled() as conn:
        conn.execute(
            "UPDATE pack_share_tokens SET valid_days = %(days)s, expires_at = CASE WHEN %(days)s::int IS NULL "
            "THEN NULL ELSE now() + make_interval(days => %(days)s::int) END WHERE pack_name = %(name)s",
            {"days": days, "name": pack_name},
        )
        conn.commit()


def set_max_editors(pack_name: str, most: int) -> None:
    get_coedit_view(pack_name)
    with pooled() as conn:
        conn.execute("UPDATE pack_share_tokens SET max_editors = %s WHERE pack_name = %s", (most, pack_name))
        conn.commit()


def set_editors_delete(pack_name: str, allowed: bool) -> None:
    get_coedit_view(pack_name)
    with pooled() as conn:
        conn.execute("UPDATE pack_share_tokens SET editors_delete = %s WHERE pack_name = %s",
                     (allowed, pack_name))
        conn.commit()


def join_by_token(token: str, user_id: int) -> "tuple[str, str | None]":
    with pooled() as conn:
        row = conn.execute(
            "SELECT t.pack_name, p.user_id, t.max_editors, t.expires_at IS NOT NULL AND t.expires_at <= now() "
            "FROM pack_share_tokens t LEFT JOIN packs p ON p.name = t.pack_name "
            "WHERE t.token = %s FOR UPDATE OF t",
            (token,),
        ).fetchone()
        if row is None:
            conn.rollback()
            return "invalid", None
        pack_name, owner_id, most, expired = row
        if owner_id is None:
            conn.rollback()
            return "gone", pack_name
        if owner_id == user_id:
            conn.rollback()
            return "own", pack_name
        if conn.execute("SELECT 1 FROM pack_editors WHERE pack_name = %s AND user_id = %s",
                        (pack_name, user_id)).fetchone():
            conn.rollback()
            return "already", pack_name
        if expired:
            conn.rollback()
            return "expired", pack_name
        taken = conn.execute(
            "SELECT (SELECT COUNT(*) FROM pack_editors WHERE pack_name = %(n)s) "
            "     + (SELECT COUNT(*) FROM pack_invites WHERE pack_name = %(n)s)",
            {"n": pack_name},
        ).fetchone()[0]
        if taken >= most:
            conn.rollback()
            return "full", pack_name
        conn.execute(
            "INSERT INTO pack_editors (pack_name, user_id, added_at) VALUES (%s, %s, %s) "
            "ON CONFLICT (pack_name, user_id) DO NOTHING",
            (pack_name, user_id, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        return "joined", pack_name


def pack_name_by_id(pack_id: int) -> "str | None":
    with pooled_read() as conn:
        row = conn.execute("SELECT name FROM packs WHERE id = %s", (pack_id,)).fetchone()
        return row[0] if row else None


def pack_editors(pack_name: str) -> "list[int]":
    with pooled_read() as conn:
        return [row[0] for row in conn.execute(
            "SELECT user_id FROM pack_editors WHERE pack_name = %s ORDER BY added_at, user_id",
            (pack_name,)).fetchall()]


def remove_editor(pack_name: str, user_id: int) -> None:
    with pooled() as conn:
        conn.execute("DELETE FROM pack_editors WHERE pack_name = %s AND user_id = %s", (pack_name, user_id))
        conn.commit()


def shared_packs(user_id: int) -> "list[tuple[str, str]]":
    with pooled_read() as conn:
        return conn.execute(
            "SELECT p.name, p.title FROM pack_editors e JOIN packs p ON p.name = e.pack_name "
            "WHERE e.user_id = %s ORDER BY lower(p.title), p.name",
            (user_id,),
        ).fetchall()


def _username_mark(username: str) -> str:
    import vault
    return vault.text_mark(username.lstrip("@").lower(), "sticker.invite")


def invites_usable() -> bool:
    return _names_sealable()


def add_invites(pack_name: str, usernames: "list[str]") -> "tuple[list[str], list[str]]":
    if not invites_usable():
        raise RuntimeError("invitations need the data key")
    view = get_coedit_view(pack_name)
    room = view["max_editors"] - view["editors"] - view["invited"]
    invited, full = [], []
    with pooled() as conn:
        for name in usernames:
            mark = _username_mark(name)
            if conn.execute("SELECT 1 FROM pack_invites WHERE pack_name = %s AND username_mark = %s",
                            (pack_name, mark)).fetchone():
                invited.append(name)
                continue
            if room <= 0:
                full.append(name)
                continue
            conn.execute(
                "INSERT INTO pack_invites (pack_name, username_mark, username_sealed, added_at) "
                "VALUES (%s, %s, %s, clock_timestamp())",
                (pack_name, mark, seal_name(name.lstrip("@"), INVITE_PURPOSE)),
            )
            room -= 1
            invited.append(name)
        conn.commit()
    return invited, full


def pack_invites(pack_name: str) -> "list[tuple[str, str]]":
    with pooled_read() as conn:
        rows = conn.execute(
            "SELECT username_mark, username_sealed FROM pack_invites WHERE pack_name = %s "
            "ORDER BY added_at, username_mark", (pack_name,)).fetchall()
    return [(mark, open_name(sealed, INVITE_PURPOSE) or "?") for mark, sealed in rows]


def remove_invite(pack_name: str, mark: str) -> None:
    with pooled() as conn:
        conn.execute("DELETE FROM pack_invites WHERE pack_name = %s AND username_mark = %s", (pack_name, mark))
        conn.commit()


def claim_invites(user_id: int, username: "str | None") -> "list[tuple[str, str]]":
    if not username or not invites_usable():
        return []
    mark = _username_mark(username)
    with pooled() as conn:
        rows = conn.execute(
            "DELETE FROM pack_invites i USING packs p WHERE i.username_mark = %s AND p.name = i.pack_name "
            "RETURNING p.name, p.title, p.user_id",
            (mark,),
        ).fetchall()
        joined = []
        for name, title, owner_id in rows:
            if owner_id == user_id:
                continue
            conn.execute(
                "INSERT INTO pack_editors (pack_name, user_id, added_at) VALUES (%s, %s, %s) "
                "ON CONFLICT (pack_name, user_id) DO NOTHING",
                (name, user_id, datetime.now(timezone.utc).isoformat()),
            )
            joined.append((name, title))
        conn.commit()
    return joined


def delete_pack_records(name: str) -> None:
    with pooled() as conn:
        conn.execute("DELETE FROM packs WHERE name = %s", (name,))
        conn.execute("DELETE FROM pack_editors WHERE pack_name = %s", (name,))
        conn.execute("DELETE FROM pack_share_tokens WHERE pack_name = %s", (name,))
        conn.execute("DELETE FROM pack_invites WHERE pack_name = %s", (name,))
        conn.commit()


def set_pack_public(name: str, public: bool) -> None:
    with pooled() as conn:
        conn.execute("UPDATE packs SET public = %s WHERE name = %s", (public, name))
        conn.commit()


def is_pack_public(name: str) -> bool:
    with pooled_read() as conn:
        row = conn.execute("SELECT public FROM packs WHERE name = %s", (name,)).fetchone()
        return bool(row and row[0])


def pack_access(name: str, user_id: int, chat_id: "int | None" = None) -> "dict | None":
    with pooled_read() as conn:
        row = conn.execute(
            """
            SELECT p.user_id, p.title,
                   p.user_id = %(user)s
                   OR EXISTS (SELECT 1 FROM pack_editors e
                               WHERE e.pack_name = p.name AND e.user_id = %(user)s)
                   OR (p.public AND %(chat)s::bigint IS NOT NULL AND EXISTS (
                         SELECT 1 FROM group_members g
                          WHERE g.chat_id = %(chat)s::bigint
                            AND (g.user_id = p.user_id OR g.user_id IN (
                                 SELECT e.user_id FROM pack_editors e WHERE e.pack_name = p.name)))),
                   p.user_id = %(user)s
                   OR (COALESCE(t.editors_delete, FALSE) AND EXISTS (
                         SELECT 1 FROM pack_editors e WHERE e.pack_name = p.name AND e.user_id = %(user)s))
            FROM packs p LEFT JOIN pack_share_tokens t ON t.pack_name = p.name
            WHERE p.name = %(name)s
            """,
            {"user": user_id, "chat": chat_id, "name": name},
        ).fetchone()
    if row is None:
        return None
    return {"owner_id": row[0], "title": row[1], "allowed": bool(row[2]), "can_delete": bool(row[3])}


GROUP_PACKS_MAX = 50


def group_packs(chat_id: int) -> "list[tuple[str, str]]":
    with pooled_read() as conn:
        return conn.execute(
            """
            SELECT p.name, p.title FROM packs p
             WHERE p.public AND (
                   p.user_id IN (SELECT user_id FROM group_members WHERE chat_id = %(chat)s)
                   OR EXISTS (SELECT 1 FROM pack_editors e
                                JOIN group_members g ON g.user_id = e.user_id AND g.chat_id = %(chat)s
                               WHERE e.pack_name = p.name))
             ORDER BY lower(p.title), p.name
             LIMIT %(most)s
            """,
            {"chat": chat_id, "most": GROUP_PACKS_MAX},
        ).fetchall()


def note_group_member(chat_id: int, user_id: int) -> None:
    with pooled() as conn:
        conn.execute(
            "INSERT INTO group_members (chat_id, user_id, seen_at) VALUES (%s, %s, now()) "
            "ON CONFLICT (chat_id, user_id) DO UPDATE SET seen_at = now()",
            (chat_id, user_id),
        )
        conn.commit()


def forget_group_member(chat_id: int, user_id: int) -> None:
    with pooled() as conn:
        conn.execute("DELETE FROM group_members WHERE chat_id = %s AND user_id = %s", (chat_id, user_id))
        conn.commit()


def forget_group(chat_id: int) -> None:
    with pooled() as conn:
        conn.execute("DELETE FROM group_members WHERE chat_id = %s", (chat_id,))
        conn.commit()


def move_group(old_chat_id: int, new_chat_id: int) -> None:
    with pooled() as conn:
        conn.execute(
            "INSERT INTO group_members (chat_id, user_id, seen_at) "
            "SELECT %s, user_id, seen_at FROM group_members WHERE chat_id = %s "
            "ON CONFLICT (chat_id, user_id) DO NOTHING",
            (new_chat_id, old_chat_id),
        )
        conn.execute("DELETE FROM group_members WHERE chat_id = %s", (old_chat_id,))
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


ACTIVITY_RETENTION_DAYS = int(os.environ.get("ACTIVITY_RETENTION_DAYS", "90"))


def prune_old_data() -> int:
    with pooled() as conn:
        cur = conn.execute(
            "DELETE FROM activity_events WHERE occurred_at < now() - make_interval(days => %s)",
            (ACTIVITY_RETENTION_DAYS,),
        )
        removed = cur.rowcount
        cur = conn.execute(
            "DELETE FROM group_members WHERE seen_at < now() - make_interval(days => %s)",
            (ACTIVITY_RETENTION_DAYS,),
        )
        removed += cur.rowcount or 0
        cur = conn.execute(
            "DELETE FROM pack_invites WHERE added_at < now() - make_interval(days => %s)",
            (INVITE_KEEP_DAYS,),
        )
        removed += cur.rowcount or 0
        conn.commit()
    return removed


def erase_user(user_id: int) -> int:
    removed = 0
    with pooled() as conn:
        cur = conn.execute("SELECT name FROM packs WHERE user_id = %s", (user_id,))
        owned = [row[0] for row in cur.fetchall()]
        if owned:
            for table in ("pack_share_tokens", "pack_editors", "pack_invites"):
                cur = conn.execute(f"DELETE FROM {table} WHERE pack_name = ANY(%s)", (owned,))
                removed += cur.rowcount or 0
            cur = conn.execute("DELETE FROM packs WHERE user_id = %s", (user_id,))
            removed += cur.rowcount or 0
        cur = conn.execute("DELETE FROM pack_editors WHERE user_id = %s", (user_id,))
        removed += cur.rowcount or 0
        for table in ("user_settings", "donation_prompts", "activity_events", "group_members"):
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
