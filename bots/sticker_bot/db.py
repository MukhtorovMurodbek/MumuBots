"""Postgres layer for StickerBot -- these tables are this bot's
alone. The family shares one Postgres database now, but each bot gets its
own schema in it (DB_SCHEMA below), and no bot reads or writes another's
tables; the only shared tables are `family.*`, which family_link.py owns.

Owns:
- per-user setting: chosen UI language (English/Uzbek/Russian)
- which sticker sets belong to which user (for /mypacks, /addsticker picker)
- co-editing: a share-link token per pack (with how many editors it lets in
  and until when), who has been granted add access to a pack, and the
  @usernames its owner invited who have not used the bot yet
- who created each pack (display name snapshot, for /whomade)
- a Telegram Stars ledger + donation-reminder cooldown, for this bot's own
  /donate flow (see shared_features.py)
- group chats (2.2.0, group_mode.py): whether each pack is public (shown and
  open to add to in the groups its owner or a co-editor uses the bot in) or
  private (the default), and which people have used the bot in which group,
  for ACTIVITY_RETENTION_DAYS

Postgres was chosen (over SQLite) so this survives an ephemeral cloud
container redeploy -- see DEPLOY.md.
"""
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


# --- one shared database, one schema per bot ---------------------------------
# The whole family now lives in ONE Postgres database, with a schema per bot
# (family_link.py has the full layout). DB_SCHEMA is the one this bot owns.
# Nothing else in this file had to change: every statement below is still
# written against bare table names, and search_path resolves them into this
# bot's own schema, so the four bots' identically-named tables
# (user_settings, star_transactions, activity_events, ...) never collide.
# Leaving DB_SCHEMA unset keeps the old behaviour -- "public" in a database
# this bot has entirely to itself.
DB_SCHEMA = os.environ.get("DB_SCHEMA", "public")

# ---------------------------------------------------------------------------
# Connection-string sanity check
# ---------------------------------------------------------------------------
# Two ways of pointing a bot at a cloud Postgres fail *quietly* rather than
# loudly, so both are worth catching at startup instead of in the data:
#
#   Transaction pooling -- Supabase/Supavisor on port 6543, or PgBouncer in
#   transaction mode -- multiplexes many clients over a few server
#   connections, so per-connection startup options do not survive from one
#   transaction to the next. The pool below passes search_path as exactly
#   such an option, which means the bot would read and write "public"
#   instead of its own schema, while still heartbeating perfectly. That
#   surfaces as wrong data rather than as a broken bot, which is the worst
#   way to find out. The session pooler (port 5432) keeps one server
#   connection per client and is the right one here.
#
#   An unencoded "@" or ":" in the password splits the URL in the wrong
#   place, so libpq ends up resolving a hostname that is really the tail of
#   the password -- a DNS error that says nothing about the real cause.
#
# These warn rather than refuse: an unusual setup is the owner's business,
# and a bot that will not start is worse than one that says why it might
# misbehave.
TRANSACTION_POOLER_PORTS = {6543}


def check_database_url(dsn: str = DATABASE_URL) -> list[str]:
    """Human-readable warnings about `dsn`; empty when it looks sane."""
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


# ---------------------------------------------------------------------------
# One pooled connection per process
# ---------------------------------------------------------------------------
# Every function below used to open -- and immediately throw away -- its own
# Postgres connection. On a small shared cloud database that is by far the
# most expensive thing this bot does: a TCP round trip, a TLS handshake and a
# freshly forked backend process on the server, all to run one INSERT that
# takes microseconds. At one connection per Telegram update (plus one per
# heartbeat, per command poll, per donation check) it is also what decides
# how big the database instance has to be.
#
# A pool keeps a warm connection open instead and hands it out. Sized for
# cheap: one connection held, a couple more only while several things happen
# at once, and any extra handed back to the server after DB_POOL_MAX_IDLE
# seconds -- so an idle bot costs the database exactly one backend.
POOL_MIN = int(os.environ.get("DB_POOL_MIN", "1"))
POOL_MAX = int(os.environ.get("DB_POOL_MAX", "3"))
POOL_MAX_IDLE = float(os.environ.get("DB_POOL_MAX_IDLE", "120"))
POOL_TIMEOUT = float(os.environ.get("DB_POOL_TIMEOUT", "15"))

# How long a pooled connection may sit unused before it is worth spending a
# round trip proving it is still alive. See _check_if_idle: the check was
# unconditional, and against a database on the other side of the world an
# unconditional check is the single most expensive thing about a small query.
POOL_CHECK_AFTER_IDLE = float(os.environ.get("DB_POOL_CHECK_AFTER_IDLE", "45"))

_pool: "ConnectionPool | None" = None
# id(connection) -> when it was last known good. Bounded by max_size. An id
# can be reused after a connection is closed, and the worst that costs is a
# skipped check on a connection that was only just opened -- which is alive
# by construction.
_last_known_good: "dict[int, float]" = {}


def _check_if_idle(conn) -> None:
    """The pool's checkout check, but only for connections that have actually
    been sitting there.

    A connection idle across a cloud provider's own network timeout comes back
    dead, and `ConnectionPool.check_connection` is the guard against handing
    one out. It is also a full round trip, and it was being paid on every
    checkout -- including the checkout half a second after the last one, on a
    connection that could not possibly have gone stale in between.

    That is most of them. It cost a quarter of a second each back when this
    bot's database was in ap-northeast-2 and the container in EU West -- a
    third of the cost of every read. Since v1.2.0 the database is in
    eu-central-1, beside the containers, which cuts the absolute cost by an
    order of magnitude but leaves the ratio alone: the check is still a whole
    extra round trip per read. A connection used within the last
    POOL_CHECK_AFTER_IDLE seconds is taken as alive, and everything quieter
    than that is still proved before use.
    """
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
    """Created on first use, never at import time -- init_db() has to be able
    to create the schema before anything connects into it."""
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            DATABASE_URL,
            min_size=POOL_MIN,
            max_size=POOL_MAX,
            max_idle=POOL_MAX_IDLE,
            timeout=POOL_TIMEOUT,
            kwargs={
                # Nothing here may wait on the network for ever. The five bots
                # share one process and eight threads, and a query whose
                # packets go unacknowledged (a dead network path) otherwise
                # waits out TCP retransmission -- minutes -- holding one of
                # them: measured in the incident of 2026-10-09 08:40 UTC. So a
                # connection attempt gives up after 10 s (connect_timeout),
                # data nobody acknowledges after 30 s (tcp_user_timeout, in
                # ms), a statement after 60 s (statement_timeout) and a
                # transaction left open and idle after 120 s
                # (idle_in_transaction_session_timeout).
                "options": f"-c search_path={DB_SCHEMA},public "
                           f"-c statement_timeout=60s -c idle_in_transaction_session_timeout=120s",
                "connect_timeout": 10, "tcp_user_timeout": 30000,
                # Keep an idle connection alive at the TCP level rather than
                # discovering it is dead on the next checkout. Cheaper than
                # the check it saves, and it happens while nobody is waiting.
                "keepalives": 1, "keepalives_idle": 30,
                "keepalives_interval": 10, "keepalives_count": 5,
            },
            check=_check_if_idle,
            name=f"{DB_SCHEMA}",
            open=True,
        )
    return _pool


def pooled():
    """A connection from the pool, as a context manager. The transaction is
    committed on a clean exit and rolled back on an exception; the connection
    itself goes back to the pool either way rather than being closed.

    For anything that writes. Reads should use pooled_read(), which is the
    same connection without the transaction around it."""
    return _get_pool().connection()


@contextmanager
def pooled_read():
    """A pooled connection in autocommit, for statements that only read.

    A read through pooled() costs three round trips to the database: the
    implicit BEGIN that psycopg opens with the first statement, the statement
    itself, and the COMMIT the context manager sends on the way out. Two of
    those exist to make a transaction nobody needed -- a single SELECT is
    atomic on its own.

    Measured against the family's actual database, one read: 28 ms through
    pooled(), 9 ms through this. The same shape holds wherever the database
    is; it is round trips, so it scales with the distance rather than washing
    out. Everything that writes -- and anything reading several statements
    that have to agree with each other -- still goes through pooled().
    """
    with _get_pool().connection() as conn:
        conn.set_autocommit(True)
        try:
            yield conn
        finally:
            # Back to the pool as it was found, so pooled() still gets a
            # connection that opens a transaction.
            try:
                conn.set_autocommit(False)
            except Exception:
                logging.getLogger(__name__).debug("Could not restore transaction mode", exc_info=True)


def close_pool() -> None:
    """Shutdown hook -- lets the process exit without waiting on the pool's
    own worker threads."""
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


def connect(dsn: str = DATABASE_URL):
    """A brand-new, unpooled connection to an arbitrary database. Only the
    offline tools (db_merge.py) need this, because they hold two databases
    open at once and drive the transaction by hand. Everything in this module
    goes through pooled() instead."""
    return psycopg.connect(dsn, options=f"-c search_path={DB_SCHEMA},public")


def ensure_schema(dsn: str = DATABASE_URL) -> None:
    """Deliberately connects *without* the search_path option -- the schema
    it is about to create may not exist yet, and libpq would not complain
    but every later CREATE TABLE would land in public instead."""
    with closing(psycopg.connect(dsn)) as conn:
        conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{DB_SCHEMA}"')
        conn.commit()


def _column(conn, table: str, column: str):
    """(data_type, column_default) of `column` in this bot's schema, or None
    when the table has no such column.

    init_db() asks this before every ALTER TABLE and issues the ALTER only when
    the answer says something is missing or different. Every ALTER TABLE takes
    an ACCESS EXCLUSIVE lock on its table and keeps it until the transaction
    ends -- even one that turns out to change nothing, IF NOT EXISTS or not
    (measured on Postgres 17, 2026-10-09) -- and init_db() is one transaction
    that commits at the very end. So a start that only wanted to check used to
    queue every other reader and writer of those tables behind it for as long
    as init_db() took, and during a deploy overlap those are the old
    instance's, still serving. This is a read of the catalog, which takes no
    lock that blocks anybody; a normal start now takes none of the table locks.
    IF NOT EXISTS stays on the statements, because two instances starting
    together can both see the column missing; the second then waits for the
    first and finds nothing left to do. The statements are literals, one per
    column, on purpose: tests/test_sql_safety.py allows no SQL built from
    names."""
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

    # Deliberately on a plain connection rather than the pool: the offline
    # tools (db_merge.py, migrate_to_shared_db.py) call this against a
    # *different* database than the one this process serves, and the pool
    # is bound to DATABASE_URL. It runs once, so there is nothing to save.
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
        # 2.2.0: private (the default, and every pack made before) or public in groups.
        if _column(conn, "packs", "public") is None:
            conn.execute("ALTER TABLE packs ADD COLUMN IF NOT EXISTS public BOOLEAN NOT NULL DEFAULT FALSE")
        # Who has used the bot in which group chat, so a group's /packs can list
        # the public packs of its own members (the Bot API cannot list a
        # group's members). Pruned with activity_events; erased with the person.
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
        # 2.2.0 (owner, 2026-10-28: "The link might be shared/leaked to a group too
        # large"): a link lets in at most max_editors co-editors in all, and works for
        # valid_days from when it was made (NULL: no limit). A link made before this
        # gets the defaults from the moment this runs: 10 editors, a week.
        if _column(conn, "pack_share_tokens", "max_editors") is None:
            conn.execute("ALTER TABLE pack_share_tokens ADD COLUMN IF NOT EXISTS max_editors INTEGER "
                         f"NOT NULL DEFAULT {DEFAULT_MAX_EDITORS}")
        if _column(conn, "pack_share_tokens", "valid_days") is None:
            conn.execute("ALTER TABLE pack_share_tokens ADD COLUMN IF NOT EXISTS valid_days INTEGER "
                         f"DEFAULT {DEFAULT_VALID_DAYS}")
        if _column(conn, "pack_share_tokens", "expires_at") is None:
            conn.execute("ALTER TABLE pack_share_tokens ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ "
                         f"DEFAULT (now() + interval '{DEFAULT_VALID_DAYS} days')")
        # Whether the pack's co-editors may delete stickers (owner, 2026-10-28: "let the owner
        # pick if they want to grant delete rights to the link"). Off for every link, old ones
        # included: until the owner says otherwise, only the owner deletes.
        if _column(conn, "pack_share_tokens", "editors_delete") is None:
            conn.execute("ALTER TABLE pack_share_tokens ADD COLUMN IF NOT EXISTS editors_delete BOOLEAN "
                         "NOT NULL DEFAULT FALSE")
        # People the owner invited by @username who have not used the bot since:
        # a keyed hash to find them by, and the name sealed for the owner's list.
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
        # Each safe to re-run on an existing table, and each runs only when it is
        # needed (see _column): username_sealed for tables from before 2.2.0 sealed
        # the names; amount_stars widened, since some fiat currencies' minor-unit
        # amounts can exceed a plain INTEGER's range; currency for bots migrating
        # from Stars-only donations (see shared_features.py's FIAT_CURRENCIES).
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
        # How many times this person has ever been shown the nudge. The
        # cadence is a schedule that runs out rather than a loop (see
        # DONATION_STEPS in shared_features.py), and this is the step counter
        # it reads. Safe to re-run on an existing table.
        if _column(conn, "donation_prompts", "times_shown") is None:
            conn.execute("ALTER TABLE donation_prompts ADD COLUMN IF NOT EXISTS times_shown INTEGER NOT NULL DEFAULT 0")
        # Set only when DONATION_PIN is on: the message this bot pinned, so
        # it can be unpinned again when they donate or when a newer nudge
        # replaces it.
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
        # Nullable, no default -- NULL means "hasn't picked a language yet",
        # which is what gates the first-run picker in bot.py's /start.
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


# ---------- /status active-user tracking ----------

def record_activity_batch(user_ids) -> None:
    """One row per user per flush window -- see shared_features.py's
    track_activity, which buffers them. Sent as a single statement whatever
    the batch size: both readers of this table are COUNT(DISTINCT user_id)
    over a time window, so nothing depends on a row per update."""
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
    """Everyone with activity since `since`. Used by the family bus for an
    aimed broadcast -- see BROADCAST_ACTIVE_DAYS in family_link.py."""
    with pooled_read() as conn:
        cur = conn.execute(
            "SELECT DISTINCT user_id FROM activity_events WHERE occurred_at >= %s",
            (since,),
        )
        return [row[0] for row in cur.fetchall()]


def list_all_users() -> list[int]:
    """Everyone this bot could send an unprompted message to.

    The union of two tables because neither is the whole answer on its own:
    user_settings has a row per person who has ever picked a setting and is never
    pruned, while activity_events reaches people who only ever used the bot
    without changing anything -- but is pruned at ACTIVITY_RETENTION_DAYS.
    Together they are "everyone we still know about", which is the honest
    scope of a broadcast.
    """
    with pooled_read() as conn:
        cur = conn.execute(
            "SELECT user_id FROM user_settings "
            "UNION "
            "SELECT DISTINCT user_id FROM activity_events"
        )
        return [int(r[0]) for r in cur.fetchall()]


def get_user_language(user_id: int) -> str | None:
    """None means the user hasn't picked a language yet (no row, or a row
    with no language set)."""
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
    """Returns list of (name, title) for a user's own packs, newest first.

    Only returns packs this user_id *owns* -- packs they can co-edit via a
    share link don't show up here, since editing there happens entirely
    through the /start deep link instead of this picker.
    """
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
    """Several at once, in one round trip: the titles /mypacks found changed on
    Telegram (see bot._refresh_pack_titles)."""
    with pooled() as conn:
        with conn.cursor() as cur:
            cur.executemany("UPDATE packs SET title = %s WHERE name = %s",
                            [(title, name) for name, title in titles.items()])
        conn.commit()


# ---------- co-editing ----------

def add_editor(pack_name: str, user_id: int) -> None:
    """Records that user_id has been granted add-only access to pack_name
    (they opened a valid co-edit link). Idempotent."""
    with pooled() as conn:
        conn.execute(
            "INSERT INTO pack_editors (pack_name, user_id, added_at) VALUES (%s, %s, %s) "
            "ON CONFLICT (pack_name, user_id) DO NOTHING",
            (pack_name, user_id, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()


DEFAULT_MAX_EDITORS = 10
DEFAULT_VALID_DAYS = 7
# The choices the co-edit screen cycles through.
MAX_EDITORS_CHOICES = (3, 5, 10, 20, 50)
VALID_DAYS_CHOICES = (1, 7, 30, None)
# An invitation nobody has used is dropped after this many days.
INVITE_KEEP_DAYS = 30
INVITE_PURPOSE = "sticker.invite.username"


def get_coedit_view(pack_name: str) -> dict:
    """Everything the co-edit screen renders, on one connection, making the
    pack's link (with the defaults) if it has none: {"token", "title",
    "pack_id", "editors", "invited", "max_editors", "valid_days", "expires_at",
    "editors_delete"}."""
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
    """Generates a fresh token for the pack, invalidating the old link, valid
    for the pack's period again from now. Does NOT remove already-granted
    editors -- it only stops the *old* link from granting new access."""
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
    """The link works for `days` from now (None: no limit)."""
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
    """Whether the pack's co-editors -- through the link or invited -- may delete stickers."""
    get_coedit_view(pack_name)
    with pooled() as conn:
        conn.execute("UPDATE pack_share_tokens SET editors_delete = %s WHERE pack_name = %s",
                     (allowed, pack_name))
        conn.commit()


def join_by_token(token: str, user_id: int) -> "tuple[str, str | None]":
    """Someone opened a co-edit link: (outcome, pack name). Outcomes: "invalid" (no such
    link), "gone" (its pack is not known any more), "own" (the owner's own link), "already"
    (an editor already), "expired", "full" (editors and pending invitations at the link's
    limit), "joined". One transaction, the link's row locked, so two people opening it at
    once cannot both take its last place."""
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
    """The co-edit screen's buttons carry the pack's number, not its name: a name can be
    48 characters, and Telegram allows a button 64."""
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
    """(name, title) of the packs this person co-edits, by title."""
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
    """Invitations hold a name, so they need the data key."""
    return _names_sealable()


def add_invites(pack_name: str, usernames: "list[str]") -> "tuple[list[str], list[str]]":
    """Invite these @usernames (already checked to look like usernames) to co-edit:
    (invited, refused for the pack being at its limit). Editors and pending invitations
    together stay within the link's max_editors. Refuses without the data key: the
    names are kept sealed, or not at all."""
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
                # clock_timestamp(), not now(): every row of one message would otherwise
                # carry the same time, and the owner's list is in the order they were typed.
                "INSERT INTO pack_invites (pack_name, username_mark, username_sealed, added_at) "
                "VALUES (%s, %s, %s, clock_timestamp())",
                (pack_name, mark, seal_name(name.lstrip("@"), INVITE_PURPOSE)),
            )
            room -= 1
            invited.append(name)
        conn.commit()
    return invited, full


def pack_invites(pack_name: str) -> "list[tuple[str, str]]":
    """(mark, @username) of the pack's pending invitations, oldest first."""
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
    """This person has used the bot: every pack that invited their @username makes them a
    co-editor now, and the invitation goes. Returns (name, title) of those packs. A pack
    they own is not joined, only its invitation dropped."""
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
    """Wipes all local traces of a pack (owner record, co-editors, share
    token). Used when the pack itself gets deleted on Telegram's side --
    e.g. because its last sticker was removed and Telegram auto-deletes
    sets that reach zero stickers -- so it doesn't linger as a dead entry
    in /mypacks."""
    with pooled() as conn:
        conn.execute("DELETE FROM packs WHERE name = %s", (name,))
        conn.execute("DELETE FROM pack_editors WHERE pack_name = %s", (name,))
        conn.execute("DELETE FROM pack_share_tokens WHERE pack_name = %s", (name,))
        conn.execute("DELETE FROM pack_invites WHERE pack_name = %s", (name,))
        conn.commit()


# ---------- public packs and group chats (group_mode.py) ----------

def set_pack_public(name: str, public: bool) -> None:
    with pooled() as conn:
        conn.execute("UPDATE packs SET public = %s WHERE name = %s", (public, name))
        conn.commit()


def is_pack_public(name: str) -> bool:
    with pooled_read() as conn:
        row = conn.execute("SELECT public FROM packs WHERE name = %s", (name,)).fetchone()
        return bool(row and row[0])


def pack_access(name: str, user_id: int, chat_id: "int | None" = None) -> "dict | None":
    """Whether user_id may add to (or retag stickers in) pack `name`, and delete
    stickers from it, as {"owner_id", "title", "allowed", "can_delete"}; None for
    a pack this bot does not know.

    Allowed: its owner and its co-editors, anywhere. In a group chat
    (`chat_id`), also anybody, when the pack is public and its owner or one of
    its co-editors has used the bot in that group. Deleting: its owner, and its
    co-editors when the owner allows it (set_editors_delete) -- never somebody
    let in by a public pack, and in a group nobody at all (group_mode). One
    round trip."""
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
    """(name, title) of the public packs whose owner or a co-editor has used
    the bot in this group, by title."""
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
    """The bot left the group, or was removed from it."""
    with pooled() as conn:
        conn.execute("DELETE FROM group_members WHERE chat_id = %s", (chat_id,))
        conn.commit()


def move_group(old_chat_id: int, new_chat_id: int) -> None:
    """The group became a supergroup, under a new id: its members are noted there."""
    with pooled() as conn:
        conn.execute(
            "INSERT INTO group_members (chat_id, user_id, seen_at) "
            "SELECT %s, user_id, seen_at FROM group_members WHERE chat_id = %s "
            "ON CONFLICT (chat_id, user_id) DO NOTHING",
            (new_chat_id, old_chat_id),
        )
        conn.execute("DELETE FROM group_members WHERE chat_id = %s", (old_chat_id,))
        conn.commit()


# ---------- Telegram Stars ledger (this bot's /donate only) ----------

def record_star_invoice(
    user_id: int,
    username: str | None,
    amount_stars: int,
    item: str,
    payload: str,
    status: str = "invoiced",
    currency: str = "XTR",
) -> None:
    """amount_stars is in the currency's smallest unit for fiat currencies
    (see shared_features.py's FIAT_CURRENCIES), or a plain Stars count for
    the default currency="XTR"."""
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


# ---------- donation-reminder cooldown ----------

def bump_donation_action(user_id: int) -> tuple[int, str | None, int, bool]:
    """Increments this user's action counter and returns everything the nudge
    decision needs: (actions since the last nudge, when it was last shown,
    how many times it has ever been shown, has this person ever donated).

    One statement. This runs on the success path of every completed action --
    every finished pack, every conversion, every download -- so it is one of
    the hottest writes in the family, and the database is on another
    continent. The donation check used to be two round trips and the "have
    they already given" question would have made it three.

    `times_shown` is what turns the cadence from "every N actions forever"
    into a schedule that runs out: see DONATION_STEPS in shared_features.py.
    The paid check is what stops the bot thanking somebody by asking them
    again.
    """
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
    """Zeroes the action counter, stamps 'last_shown_at' and counts the
    showing -- call right after actually showing the nudge, not on every
    check."""
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
    """The id of the nudge this bot pinned in that person's chat, if any."""
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


# ---------- housekeeping ----------
# activity_events is append-only and powers nothing older than the retention
# window below (/status counts the last hour and since-start, ManagerBot's
# /users the last N hours). Left alone it is the one table in this schema that
# grows without limit, which on a metered database is a bill that only ever
# goes up. family_link.py's housekeeping job calls this.
ACTIVITY_RETENTION_DAYS = int(os.environ.get("ACTIVITY_RETENTION_DAYS", "90"))


def prune_old_data() -> int:
    """Returns how many rows were removed. Safe to run at any time."""
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

# ---------- erasing one person, on request ----------
# What /deletemydata reaches -- see shared_features.py, which owns the
# command and the confirmation. Everything in this schema that is about one
# person goes, in a single transaction, with one deliberate exception.
#
# The Stars ledger keeps its rows. A donation is a payment, and a payment
# record has to outlive the payer asking to be forgotten: it is what a refund
# is issued against and what the totals are counted from. The username is
# cleared, since it is the one free-text identifier on the row; the numeric
# id stays, because without it a refund cannot be sent to anybody. The
# privacy notice says so rather than implying the erase is total.


def erase_user(user_id: int) -> int:
    """Returns how many rows were removed. Blocking; call through
    asyncio.to_thread.

    The sticker packs themselves are not ours to delete. They live on
    Telegram's servers under this bot's name, and dropping the rows here
    makes the bot forget them -- it stops listing them and can no longer add
    to them -- while every copy anyone installed keeps working. Removing a
    pack for real is done from Telegram, by its owner.
    """
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
        # Their seat on somebody else's pack, which is theirs to give up and
        # takes nothing of the owner's with it.
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


# ---------- admin: full database export ----------

def dump_database_csv_zip() -> bytes:
    """Exports every table in this bot's own schema to one CSV per table,
    zipped together -- this bot's data only, never a sibling's. Deliberately not pg_dump-based -- that binary
    isn't guaranteed to exist wherever this bot ends up hosted, while this
    only needs the psycopg connection already used everywhere else here."""
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
