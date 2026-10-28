"""Postgres layer for ManagerBot.

ManagerBot is the one process in the family that reads *across* schemas. It
owns `manager_bot.*` (just its own activity log, so the same /status
machinery as every other bot works here too), it reads and writes
`family.*` (heartbeats, events, the command queue -- created by
family_link.py, which every bot in the family carries), and it reads the
four public bots' schemas to answer "how many users has ConvertBot had this
week" without going anywhere near their processes.

Read-only across other bots' schemas, deliberately -- ManagerBot never
writes into a bot's own tables. When something needs changing inside a bot,
it goes through the family command queue so that bot does it itself, with
its own code and its own invariants.
"""
import logging
import os
import time
from contextlib import closing, contextmanager
from datetime import datetime, timedelta, timezone

from urllib.parse import urlsplit

import psycopg
from psycopg_pool import ConnectionPool

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/botfamily"
)

# ManagerBot's own schema in the shared database -- same convention as every
# other bot (see family_link.py's layout diagram).
DB_SCHEMA = os.environ.get("DB_SCHEMA", "manager_bot")

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


FAMILY_SCHEMA = "family"


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
                # (idle_in_transaction_session_timeout). /sql sets its own,
                # shorter statement_timeout for the length of its transaction
                # (run_readonly_query), which overrides this one.
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
    with closing(psycopg.connect(dsn)) as conn:
        conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{DB_SCHEMA}"')
        conn.commit()


def init_db(dsn: str = DATABASE_URL) -> None:
    """Only ManagerBot's own tables. The family.* tables are created by
    family_link.init_family_schema(), which runs in every bot including
    this one, so whichever process starts first sets them up."""
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
        conn.commit()


# ---------- this bot's own activity (mirrors every other bot's db.py) ----------

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
            "SELECT COUNT(DISTINCT user_id) FROM activity_events WHERE occurred_at >= %s", (since,)
        )
        return cur.fetchone()[0]


# ---------- family.heartbeats ----------


def active_user_ids_since(since) -> list[int]:
    """Everyone with activity since `since`. Used by the family bus for an
    aimed broadcast -- see BROADCAST_ACTIVE_DAYS in family_link.py."""
    with pooled_read() as conn:
        cur = conn.execute(
            "SELECT DISTINCT user_id FROM activity_events WHERE occurred_at >= %s",
            (since,),
        )
        return [row[0] for row in cur.fetchall()]


def all_heartbeats() -> list[dict]:
    with pooled_read() as conn:
        cur = conn.execute(
            f"""
            SELECT bot_id, display_name, host, version, pid, db_schema,
                   started_at, last_seen, error_count,
                   to_jsonb(h) ->> 'commands' AS commands,
                   to_jsonb(h) ->> 'release' AS release,
                   EXTRACT(EPOCH FROM (now() - last_seen))::int AS seconds_ago
            FROM {FAMILY_SCHEMA}.heartbeats h ORDER BY bot_id
            """
        )
        cols = [d.name for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def heartbeat_of(bot_id: str) -> dict | None:
    """One bot's row, or None if it has never been seen.

    Separate from all_heartbeats() because it is asked on the way into every
    /run: the version check in bot.py wants one bot, not five, and this runs
    in front of a command the owner is waiting on.
    """
    with pooled_read() as conn:
        cur = conn.execute(
            f"""
            SELECT bot_id, display_name, host, version, pid, db_schema,
                   started_at, last_seen, error_count,
                   to_jsonb(h) ->> 'commands' AS commands,
                   to_jsonb(h) ->> 'release' AS release,
                   EXTRACT(EPOCH FROM (now() - last_seen))::int AS seconds_ago
            FROM {FAMILY_SCHEMA}.heartbeats h WHERE bot_id = %s
            """,
            (bot_id,),
        )
        row = cur.fetchone()
        if not row:
            return None
        return dict(zip([d.name for d in cur.description], row))


# ---------- family.bot_state (edge-triggered up/down alerting) ----------

def get_known_state() -> dict[str, bool]:
    with pooled_read() as conn:
        cur = conn.execute(f"SELECT bot_id, is_up FROM {FAMILY_SCHEMA}.bot_state")
        return {row[0]: row[1] for row in cur.fetchall()}


def set_known_state(bot_id: str, is_up: bool) -> None:
    with pooled() as conn:
        conn.execute(
            f"""
            INSERT INTO {FAMILY_SCHEMA}.bot_state (bot_id, is_up, changed_at)
            VALUES (%s, %s, now())
            ON CONFLICT (bot_id) DO UPDATE SET is_up = excluded.is_up, changed_at = now()
            """,
            (bot_id, is_up),
        )
        conn.commit()


# ---------- family.events ----------

def take_unnotified_events(limit: int = 20) -> list[dict]:
    """Claims and returns them in one statement, so a ManagerBot that gets
    restarted mid-DM doesn't re-send the whole backlog."""
    with pooled() as conn:
        cur = conn.execute(
            f"""
            UPDATE {FAMILY_SCHEMA}.events SET notified = TRUE
            WHERE id IN (
                SELECT id FROM {FAMILY_SCHEMA}.events WHERE notified = FALSE
                ORDER BY id LIMIT %s FOR UPDATE SKIP LOCKED
            )
            RETURNING id, bot_id, level, kind, message, details, occurred_at
            """,
            (limit,),
        )
        cols = [d.name for d in cur.description]
        rows = [dict(zip(cols, row)) for row in cur.fetchall()]
        conn.commit()
        return rows


def recent_events(limit: int = 15, bot_id: str | None = None) -> list[dict]:
    sql = (
        f"SELECT bot_id, level, kind, message, occurred_at FROM {FAMILY_SCHEMA}.events "
        + ("WHERE bot_id = %s " if bot_id else "")
        + "ORDER BY id DESC LIMIT %s"
    )
    params = (bot_id, limit) if bot_id else (limit,)
    with pooled_read() as conn:
        cur = conn.execute(sql, params)
        cols = [d.name for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def log_event(bot_id: str, level: str, kind: str, message: str) -> None:
    """ManagerBot's own events go in already marked as notified -- it is the
    one doing the notifying, so forwarding them to itself would be a loop."""
    with pooled() as conn:
        conn.execute(
            f"INSERT INTO {FAMILY_SCHEMA}.events (bot_id, level, kind, message, notified) "
            f"VALUES (%s, %s, %s, %s, TRUE)",
            (bot_id, level, kind, message[:4000]),
        )
        conn.commit()


# ---------- family.commands ----------

def queue_command(target_bot: str, command: str, args: str, requested_by: int,
                  reply_chat_id: int) -> int:
    """Queue one command for a bot to pick up.

    The target finds it on its next bus poll -- about a second if the bus is
    already busy, at most FAMILY_BUS_POLL_IDLE_SECONDS if it has been quiet
    (see family_link._bus_tick). The caller should call
    family_link.mark_bus_active() right after this so ManagerBot's own result
    pump goes to the fast cadence while the answer is on its way back.
    """
    with pooled() as conn:
        cur = conn.execute(
            f"""
            INSERT INTO {FAMILY_SCHEMA}.commands (target_bot, command, args, requested_by, reply_chat_id)
            VALUES (%s, %s, %s, %s, %s) RETURNING id
            """,
            (target_bot, command, args, requested_by, reply_chat_id),
        )
        command_id = cur.fetchone()[0]
        conn.commit()
    # One choke point for every /ping, /run, /pause, ... so ManagerBot's result
    # pump goes to its fast cadence the instant a command is outstanding,
    # without each call site having to remember to say so. Imported here
    # rather than at module scope because family_link imports this module.
    try:
        import family_link
        family_link.mark_bus_active()
    except Exception:
        logging.getLogger(__name__).debug("Could not mark the family bus active", exc_info=True)
    return command_id


def take_finished_commands(limit: int = 5) -> list[dict]:
    with pooled() as conn:
        cur = conn.execute(
            f"""
            UPDATE {FAMILY_SCHEMA}.commands SET delivered = TRUE
            WHERE id IN (
                SELECT id FROM {FAMILY_SCHEMA}.commands
                WHERE delivered = FALSE AND status IN ('done', 'failed', 'timeout')
                ORDER BY id LIMIT %s FOR UPDATE SKIP LOCKED
            )
            RETURNING id, target_bot, command, args, reply_chat_id, status, ok, output,
                      file_name, file_bytes, created_at, claimed_at, finished_at,
                      clock_timestamp() AS taken_at
            """,
            (limit,),
        )
        cols = [d.name for d in cur.description]
        rows = [dict(zip(cols, row)) for row in cur.fetchall()]
        conn.commit()
        return rows


def expire_stale_commands(after_seconds: int) -> list[dict]:
    """A command aimed at a bot that is down never gets claimed. Rather than
    leave it pending forever, mark it timed out so the owner gets told
    instead of silently waiting."""
    with pooled() as conn:
        cur = conn.execute(
            f"""
            UPDATE {FAMILY_SCHEMA}.commands
            SET status = 'timeout', ok = FALSE, finished_at = now(),
                output = 'No answer -- that bot did not pick the command up. It is probably down.'
            WHERE status IN ('pending', 'running')
              AND created_at < now() - make_interval(secs => %s)
            RETURNING id, target_bot, command
            """,
            (after_seconds,),
        )
        cols = [d.name for d in cur.description]
        rows = [dict(zip(cols, row)) for row in cur.fetchall()]
        conn.commit()
        return rows


# ---------- family.settings (ManagerBot's own toggles) ----------

def get_setting(key: str, default: str | None = None) -> str | None:
    with pooled_read() as conn:
        cur = conn.execute(f"SELECT value FROM {FAMILY_SCHEMA}.settings WHERE key = %s", (key,))
        row = cur.fetchone()
        return row[0] if row else default


def set_setting(key: str, value: str) -> None:
    with pooled() as conn:
        conn.execute(
            f"INSERT INTO {FAMILY_SCHEMA}.settings (key, value) VALUES (%s, %s) "
            f"ON CONFLICT (key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        conn.commit()


# ---------- cross-schema reads (the whole point of one shared database) ----------


def _existing_tables(conn, schemas: list[str], table: str) -> set[str]:
    """Which of these schemas actually have this table yet -- one lookup for
    the whole family rather than one per bot."""
    cur = conn.execute(
        "SELECT table_schema FROM information_schema.tables "
        "WHERE table_schema = ANY(%s) AND table_name = %s",
        (schemas, table),
    )
    return {row[0] for row in cur.fetchall()}


def active_users_by_schema(schemas: list[str], since, include_known: bool = False) -> dict[str, tuple]:
    """{schema: (active since `since`, all-time known)} for every schema that
    has an activity_events table. A schema missing from the result has never
    been created -- worth showing differently from a real zero.

    One connection and one UNION ALL for the whole family. /status ran this
    query once per bot, each on its own connection; ManagerBot's own status
    screen was eight separate connections to the same database."""
    if not schemas:
        return {}
    with pooled_read() as conn:
        present = sorted(_existing_tables(conn, schemas, "activity_events"))
        if not present:
            return {}
        known_expr = "COUNT(DISTINCT user_id)" if include_known else "0"
        union = " UNION ALL ".join(
            f'''SELECT %s AS schema_name,
                       COUNT(DISTINCT user_id) FILTER (WHERE occurred_at >= %s) AS active,
                       {known_expr} AS known
                FROM "{schema}".activity_events'''
            for schema in present
        )
        params: list = []
        for schema in present:
            params += [schema, since]
        cur = conn.execute(union, params)
        return {row[0]: (row[1], row[2]) for row in cur.fetchall()}


def donations_by_schema(schemas: list[str]) -> dict[str, list[tuple[str, int, int]]]:
    """{schema: [(currency, paid transactions, total amount), ...]}. Amounts
    are in each currency's own unit -- Stars for XTR, minor units for fiat --
    exactly as star_transactions stores them. One trip for the whole family."""
    if not schemas:
        return {}
    with pooled_read() as conn:
        present = sorted(_existing_tables(conn, schemas, "star_transactions"))
        if not present:
            return {}
        union = " UNION ALL ".join(
            f'''SELECT %s AS schema_name, currency, COUNT(*), COALESCE(SUM(amount_stars), 0)
                FROM "{schema}".star_transactions WHERE status = 'paid'
                GROUP BY currency'''
            for schema in present
        )
        cur = conn.execute(union, list(present))
        out: dict[str, list[tuple[str, int, int]]] = {}
        for schema_name, currency, count, total in cur.fetchall():
            out.setdefault(schema_name, []).append((currency, count, total))
        for rows in out.values():
            rows.sort()
        return out


# Where each bot keeps a row per person, besides activity_events: AnonBot, ConvertBot
# and StickerBot call it user_settings, DownloaderBot's per-person table is settings.
PEOPLE_TABLES = ("activity_events", "user_settings", "settings")


def unique_people(schemas: list[str]) -> int:
    """How many different people have used any of these bots, each counted once
    however many bots they used: the distinct user_id over every schema's
    activity_events and whichever of user_settings and settings it has, leaving
    out ids that are not people (zero and below, and family_link's
    _GIFT_NOT_PEOPLE: Telegram, anonymous admins, channels). One connection. The
    220 gift's count (family_link.PROMO_USERS).

    It is built from tables that exist and not from a list of its own, so it can
    only under-count: an activity_events row older than ACTIVITY_RETENTION_DAYS is
    pruned, and somebody with no settings row then drops out. It can never
    over-count, which is what "the bots have passed 220 users" needs to stay
    true. Schema and table names come from the registry and the catalog, never
    from a person.

    AnonBot stores its people as pseudonyms (negative numbers, which the
    `user_id > 0` test below leaves out) and keeps each real id sealed beside
    its pseudonym in user_settings.user_sealed. So the count is made in two
    steps: the ids the other tables hold in the clear, and the ids opened from
    those sealed values (shared/vault.py, purpose "anon.user", checked against
    the row's pseudonym), joined in memory. A row that does not open is skipped
    and counted in one warning that names the table only. Since 2.1.2 an
    AnonBot user_settings row exists for everyone with activity, so its
    activity_events need not be opened. Before the conversion nothing is
    sealed, the plain ids are all there is, and the answer is the same as it
    always was. Nothing is stored and no id is logged."""
    if not schemas:
        return 0
    # Inside the function: family_link imports `db` (this module) at its top, so
    # importing it at the top here would be circular.
    import family_link
    import vault
    from psycopg import sql as pgsql
    not_people = set(family_link._GIFT_NOT_PEOPLE)
    with pooled_read() as conn:
        found = []
        for table in PEOPLE_TABLES:
            found += [(schema, table) for schema in sorted(_existing_tables(conn, schemas, table))]
        if not found:
            return 0
        # A table that goes by one of these names is only a list of people if it
        # has a user_id column (a bot's settings could be a key/value table).
        with_user_id = {tuple(row) for row in conn.execute(
            "SELECT table_schema, table_name FROM information_schema.columns "
            "WHERE table_schema = ANY(%s) AND table_name = ANY(%s) AND column_name = 'user_id'",
            (schemas, list(PEOPLE_TABLES)),
        ).fetchall()}
        parts = [f'SELECT user_id FROM "{schema}".{table}'
                 for schema, table in found if (schema, table) in with_user_id]
        people: set = set()
        if parts:
            # UNION, not UNION ALL: a person in two bots comes back once (and
            # the set below would hold them once anyway).
            people = {row[0] for row in conn.execute(
                "SELECT user_id FROM (" + " UNION ".join(parts) + ") AS everyone "
                "WHERE user_id > 0 AND user_id <> ALL(%s)",
                (sorted(not_people),),
            ).fetchall()}
        # AnonBot's people: any schema with anon_conversations is AnonBot's
        # (the same mark the key guard uses), and its settings hold the sealed ids.
        for schema in sorted(_existing_tables(conn, schemas, "anon_conversations")):
            if conn.execute(
                "SELECT 1 FROM information_schema.columns WHERE table_schema = %s "
                "AND table_name = 'user_settings' AND column_name = 'user_sealed'",
                (schema,),
            ).fetchone() is None:
                continue
            # Only the rows that hold a pseudonym: a plain id (unconverted data)
            # was counted above, and has nothing to open.
            rows = conn.execute(
                pgsql.SQL("SELECT user_id, user_sealed FROM {}.user_settings "
                          "WHERE user_id < %s").format(pgsql.Identifier(schema)),
                (vault.PSEUDONYM_CEILING,),
            ).fetchall()
            skipped = 0
            for pseudonym, sealed in rows:
                try:
                    if sealed is None:
                        raise ValueError("no sealed id")
                    real = vault.open_id(sealed, "anon.user", expected=pseudonym)
                except (vault.VaultError, TypeError, ValueError):
                    skipped += 1
                    continue
                if real > 0 and real not in not_people:
                    people.add(real)
            if skipped:
                logging.getLogger(__name__).warning(
                    "%d sealed value(s) in %s.user_settings did not open and were not counted",
                    skipped, schema)
        return len(people)


# Unique people per bot. activity_events keeps who used a bot for ACTIVITY_RETENTION_DAYS, so
# any stretch inside that window is counted exactly, live, and nothing is stored for it. What
# outlives the window is only numbers: each UTC day's and each calendar month's count per bot
# (and "*", everyone across the bots), written once the day or month is over. They hold no ids,
# marks or hashes -- nothing that says who -- so there is nothing in them to erase for anybody.
PEOPLE_COUNTS = f"{FAMILY_SCHEMA}.people_counts"
EVERYONE = "*"
# A day (or month) is counted this long after it ends, when every bot has flushed its activity.
FINAL_AFTER = timedelta(hours=1)
DAY, MONTH = "day", "month"


def _ensure_people_counts(conn) -> None:
    conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{FAMILY_SCHEMA}"')
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {PEOPLE_COUNTS} (
            period TEXT NOT NULL CHECK (period IN ('day', 'month')),
            start DATE NOT NULL,
            bot TEXT NOT NULL,
            people INTEGER NOT NULL,
            PRIMARY KEY (period, start, bot)
        )
        """
    )
    # Before release, 2.2.0 kept a keyed mark per person per day here, for good. With the
    # data key those marks could tell whether somebody had been active, so they are gone.
    conn.execute(f"DROP TABLE IF EXISTS {FAMILY_SCHEMA}.people_by_day")


def first_exact_day(now=None):
    """The oldest UTC day activity_events still holds whole."""
    now = now or datetime.now(timezone.utc)
    return (now - timedelta(days=ACTIVITY_RETENTION_DAYS)).date() + timedelta(days=1)


def _midnight(day) -> datetime:
    return datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc)


def _next_month(day):
    return (day.replace(day=28) + timedelta(days=4)).replace(day=1)


def _real_ids_of(conn, schema: str, pseudonyms: set) -> dict:
    """AnonBot's pseudonyms -> the real ids sealed beside them, so "*" counts a person once."""
    import vault
    from psycopg import sql as pgsql
    if not pseudonyms:
        return {}
    rows = conn.execute(
        pgsql.SQL("SELECT user_id, user_sealed FROM {}.user_settings WHERE user_id = ANY(%s)")
        .format(pgsql.Identifier(schema)),
        (sorted(pseudonyms),),
    ).fetchall()
    real = {}
    for pseudonym, sealed in rows:
        try:
            real[pseudonym] = vault.open_id(sealed, "anon.user", expected=pseudonym)
        except (vault.VaultError, TypeError, ValueError):
            continue
    return real


def _active_by_day(conn, bots: "list[tuple[str, str]]", everyone: "list[str]", first_day, last_day) -> dict:
    """{day: {bot id or "*": set of people}} from first_day to last_day, both included, in
    memory only. "*" is everyone across the `everyone` schemas, each real person once (AnonBot's
    pseudonyms opened), and only while the data key opens; without it "*" is left out."""
    import family_link
    import vault
    schemas = sorted({schema for _, schema in bots} | set(everyone))
    present = _existing_tables(conn, schemas, "activity_events")
    sealed = {row[0] for row in conn.execute(
        "SELECT table_schema FROM information_schema.columns WHERE table_schema = ANY(%s) "
        "AND table_name = 'user_settings' AND column_name = 'user_sealed'", (schemas,),
    ).fetchall()}
    with_everyone = family_link.vault_usable()
    not_people = set(family_link._GIFT_NOT_PEOPLE)
    found: dict = {}
    for schema in sorted(present):
        seen = conn.execute(
            "SELECT DISTINCT (occurred_at AT TIME ZONE 'UTC')::date, user_id "
            f'FROM "{schema}".activity_events WHERE occurred_at >= %s AND occurred_at < %s',
            (_midnight(first_day), _midnight(last_day + timedelta(days=1))),
        ).fetchall()
        real = (_real_ids_of(conn, schema, {u for _, u in seen if vault.is_pseudonym(u)})
                if with_everyone and schema in sealed else {})
        owners = [bot_id for bot_id, bot_schema in bots if bot_schema == schema]
        for day, user_id in seen:
            on_day = found.setdefault(day, {})
            for bot_id in owners:
                on_day.setdefault(bot_id, set()).add(user_id)
            person = real.get(user_id, user_id)
            if with_everyone and schema in everyone and person > 0 and person not in not_people:
                on_day.setdefault(EVERYONE, set()).add(person)
    return found


def _counts(by_day: dict, days) -> dict:
    union: dict = {}
    for day in days:
        for bot_id, people in by_day.get(day, {}).items():
            union.setdefault(bot_id, set()).update(people)
    return {bot_id: len(people) for bot_id, people in union.items()}


def people_live(bots: "list[tuple[str, str]]", everyone: "list[str]", first_day, last_day) -> dict:
    """Exact unique people per bot (and "*") from first_day to last_day, both included. Only
    days from first_exact_day() on are complete; the caller keeps to them."""
    with pooled_read() as conn:
        by_day = _active_by_day(conn, bots, everyone, first_day, last_day)
    return _counts(by_day, [first_day + timedelta(days=n) for n in range((last_day - first_day).days + 1)])


def record_people_counts(bots: "list[tuple[str, str]]", everyone: "list[str]", now=None) -> int:
    """Write the count of every day and month that is over and still wholly inside the
    activity window but not written yet. Only while the data key opens, so "*" is never
    stored short. Returns how many periods were written."""
    import family_link
    if not family_link.vault_usable():
        return 0
    now = now or datetime.now(timezone.utc)
    first = first_exact_day(now)
    wanted = []
    day = first
    while _midnight(day + timedelta(days=1)) + FINAL_AFTER <= now:
        wanted.append((DAY, day, day))
        day += timedelta(days=1)
    month = first if first.day == 1 else _next_month(first)
    while _midnight(_next_month(month)) + FINAL_AFTER <= now:
        wanted.append((MONTH, month, _next_month(month) - timedelta(days=1)))
        month = _next_month(month)
    if not wanted:
        return 0
    with pooled() as conn:
        _ensure_people_counts(conn)
        conn.commit()
        done = {(period, start) for period, start in conn.execute(
            f"SELECT DISTINCT period, start FROM {PEOPLE_COUNTS} WHERE start >= %s", (first,)).fetchall()}
        wanted = [w for w in wanted if (w[0], w[1]) not in done]
        if not wanted:
            return 0
        by_day = _active_by_day(conn, bots, everyone, min(w[1] for w in wanted), max(w[2] for w in wanted))
        rows = []
        for period, start, last in wanted:
            counts = _counts(by_day, [start + timedelta(days=n) for n in range((last - start).days + 1)])
            for bot_id in [bot_id for bot_id, _ in bots] + [EVERYONE]:
                rows.append((period, start, bot_id, counts.get(bot_id, 0)))
        with conn.cursor() as cur:
            cur.executemany(
                f"INSERT INTO {PEOPLE_COUNTS} (period, start, bot, people) VALUES (%s, %s, %s, %s) "
                f"ON CONFLICT (period, start, bot) DO NOTHING",
                rows,
            )
        conn.commit()
        return len(wanted)


def people_stored(period: str, first_day, last_day) -> "dict":
    """{start: {bot id or "*": people}} of the stored counts of `period` starting from
    first_day to last_day."""
    found: dict = {}
    with pooled_read() as conn:
        if conn.execute("SELECT to_regclass(%s)", (PEOPLE_COUNTS,)).fetchone()[0] is None:
            return found
        for start, bot_id, people in conn.execute(
            f"SELECT start, bot, people FROM {PEOPLE_COUNTS} WHERE period = %s AND start BETWEEN %s AND %s",
            (period, first_day, last_day),
        ):
            found.setdefault(start, {})[bot_id] = people
    return found


def promo_seen_count(schema: str, promo: str) -> "int | None":
    """How many people that bot's schema says were shown this announcement, or
    None when the schema has no promo_seen table (a bot that never had one, or
    not created yet)."""
    with pooled_read() as conn:
        if schema not in _existing_tables(conn, [schema], "promo_seen"):
            return None
        return conn.execute(
            f'SELECT COUNT(*) FROM "{schema}".promo_seen WHERE promo = %s', (promo,)
        ).fetchone()[0]


# What /sql allows itself, named so ManagerBot's sql help page reads the same numbers:
# a statement is stopped after this many seconds, and at most this many rows come back.
SQL_TIMEOUT_SECONDS = 10
SQL_MAX_ROWS = 50


def run_readonly_query(sql: str, limit: int = SQL_MAX_ROWS):
    """Backs ManagerBot's /sql. The safety here is the connection being
    genuinely read-only at the transaction level -- Postgres itself rejects
    any write, so this does not depend on parsing the statement correctly.
    The keyword check in bot.py is only there to give a friendlier error.

    Both SETs are LOCAL, i.e. scoped to this transaction. The connection is
    pooled and gets handed to the next caller afterwards; a plain SET would
    leave a ten-second statement timeout stuck on it for the rest of the
    process's life.

    READ ONLY lasts as long as the transaction does, and one string may hold
    several statements: psycopg sends a query without parameters over the
    simple protocol, which runs them all, so `SELECT 1; COMMIT; DROP TABLE x`
    ended the read-only transaction on its second statement and ran the third
    in a new one that was not. `prepare=True` sends it over the extended
    protocol instead, in which Postgres itself refuses more than one statement
    ("cannot insert multiple commands into a prepared statement") -- so the
    guarantee is Postgres's, not a parser's."""
    with pooled() as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        conn.execute(f"SET LOCAL statement_timeout = '{SQL_TIMEOUT_SECONDS}s'")
        cur = conn.execute(sql, prepare=True)
        cols = [d.name for d in cur.description] if cur.description else []
        rows = cur.fetchmany(limit) if cur.description else []
        conn.rollback()
        return cols, rows


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
        conn.commit()
    return removed

# ---------- exports ----------


def dump_database_csv_zip() -> bytes:
    """ManagerBot's own schema only -- same contract as every other bot's
    db.py, so family_link's `dbdump` command works here unchanged. For the
    whole family in one file, see dump_family_csv_zip below."""
    return _dump_schemas([DB_SCHEMA])


def dump_family_csv_zip(schemas: list[str]) -> bytes:
    """Every schema in the shared database, one folder per schema, one CSV
    per table. This is the "download the whole database" button: it needs
    nothing but the psycopg connection already in use here, so it works
    from anywhere without pg_dump being installed."""
    return _dump_schemas(schemas)


def _csv_safe(row: tuple) -> list:
    """family.commands carries a BYTEA column (a finished /dbdump waiting to
    be delivered). Writing raw bytes into a CSV would produce megabytes of
    unreadable escaping, so they are summarised instead -- the real payload
    was already sent to Telegram when the command finished."""
    return [f"<{len(v)} bytes>" if isinstance(v, (bytes, bytearray, memoryview)) else v for v in row]


def _dump_schemas(schemas: list[str]) -> bytes:
    import csv
    import io
    import zipfile

    buf = io.BytesIO()
    with pooled() as conn, zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        stamp = datetime.now(timezone.utc).isoformat()
        manifest = [f"# botfamily database export, {stamp}", ""]
        for schema in schemas:
            cur = conn.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = %s AND table_type = 'BASE TABLE' ORDER BY table_name",
                (schema,),
            )
            tables = [row[0] for row in cur.fetchall()]
            if not tables:
                manifest.append(f"{schema}/ -- no tables")
                continue
            for table in tables:
                cur = conn.execute(f'SELECT * FROM "{schema}"."{table}"')
                columns = [d.name for d in cur.description]
                rows = cur.fetchall()
                out = io.StringIO()
                writer = csv.writer(out)
                writer.writerow(columns)
                writer.writerows(_csv_safe(row) for row in rows)
                zf.writestr(f"{schema}/{table}.csv", out.getvalue())
                manifest.append(f"{schema}/{table}.csv -- {len(rows)} row(s)")
        zf.writestr("MANIFEST.txt", "\n".join(manifest) + "\n")
    return buf.getvalue()


def status_snapshot(schemas: list[str], since) -> dict:
    """Everything ManagerBot's /status prints, on one connection: heartbeats,
    per-bot active users, the alerts toggle and the database's own size.

    Drawing that board used to mean eight connections -- one for the
    heartbeats, one per bot for its user count, one for the setting and one
    for the database size -- every time the owner typed four characters."""
    with pooled_read() as conn:
        cur = conn.execute(
            f"""
            SELECT bot_id, display_name, host, version, pid, db_schema,
                   started_at, last_seen, error_count, commands,
                   to_jsonb(h) ->> 'release' AS release,
                   EXTRACT(EPOCH FROM (now() - last_seen))::int AS seconds_ago
            FROM {FAMILY_SCHEMA}.heartbeats h ORDER BY bot_id
            """
        )
        cols = [d.name for d in cur.description]
        beats = [dict(zip(cols, row)) for row in cur.fetchall()]

        cur = conn.execute(f"SELECT value FROM {FAMILY_SCHEMA}.settings WHERE key = 'alerts'")
        row = cur.fetchone()
        alerts = row[0] if row else "on"

        cur = conn.execute(
            "SELECT current_database(), pg_size_pretty(pg_database_size(current_database())), "
            "split_part(version(), ' ', 2)"
        )
        name, size, version = cur.fetchone()

        present = sorted(_existing_tables(conn, schemas, "activity_events"))
        active: dict[str, int] = {}
        if present:
            union = " UNION ALL ".join(
                f'''SELECT %s, COUNT(DISTINCT user_id)
                    FROM "{schema}".activity_events WHERE occurred_at >= %s'''
                for schema in present
            )
            params: list = []
            for schema in present:
                params += [schema, since]
            cur = conn.execute(union, params)
            active = {r[0]: r[1] for r in cur.fetchall()}

    return {
        "beats": beats,
        "alerts": alerts,
        "active": active,
        "database": {"name": name, "size": size, "version": version},
    }
