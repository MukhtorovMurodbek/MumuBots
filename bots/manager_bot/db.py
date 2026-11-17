import logging
import os
import time
from contextlib import closing, contextmanager
from datetime import datetime, timedelta, timezone

from urllib.parse import urlsplit

import psycopg

import redact
from psycopg_pool import ConnectionPool

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/botfamily"
)

DB_SCHEMA = os.environ.get("DB_SCHEMA", "manager_bot")

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


FAMILY_SCHEMA = "family"


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


def init_db(dsn: str = DATABASE_URL) -> None:
    for _problem in check_database_url(dsn):
        logging.getLogger(__name__).warning("%s", _problem)
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
            "SELECT COUNT(DISTINCT user_id) FROM activity_events WHERE occurred_at >= %s", (since,)
        )
        return cur.fetchone()[0]


def active_user_ids_since(since) -> list[int]:
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


def take_unnotified_events(limit: int = 20) -> list[dict]:
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
    with pooled() as conn:
        conn.execute(
            f"INSERT INTO {FAMILY_SCHEMA}.events (bot_id, level, kind, message, notified) "
            f"VALUES (%s, %s, %s, %s, TRUE)",
            (bot_id, level, kind, redact.redact(message)[:4000]),
        )
        conn.commit()


def queue_command(target_bot: str, command: str, args: str, requested_by: int,
                  reply_chat_id: int) -> int:
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


def _existing_tables(conn, schemas: list[str], table: str) -> set[str]:
    cur = conn.execute(
        "SELECT table_schema FROM information_schema.tables "
        "WHERE table_schema = ANY(%s) AND table_name = %s",
        (schemas, table),
    )
    return {row[0] for row in cur.fetchall()}


def active_users_by_schema(schemas: list[str], since, include_known: bool = False) -> dict[str, tuple]:
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


PEOPLE_TABLES = ("activity_events", "user_settings", "settings")


def unique_people(schemas: list[str]) -> int:
    if not schemas:
        return 0
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
        with_user_id = {tuple(row) for row in conn.execute(
            "SELECT table_schema, table_name FROM information_schema.columns "
            "WHERE table_schema = ANY(%s) AND table_name = ANY(%s) AND column_name = 'user_id'",
            (schemas, list(PEOPLE_TABLES)),
        ).fetchall()}
        parts = [f'SELECT user_id FROM "{schema}".{table}'
                 for schema, table in found if (schema, table) in with_user_id]
        people: set = set()
        if parts:
            people = {row[0] for row in conn.execute(
                "SELECT user_id FROM (" + " UNION ".join(parts) + ") AS everyone "
                "WHERE user_id > 0 AND user_id <> ALL(%s)",
                (sorted(not_people),),
            ).fetchall()}
        for schema in sorted(_existing_tables(conn, schemas, "anon_conversations")):
            if conn.execute(
                "SELECT 1 FROM information_schema.columns WHERE table_schema = %s "
                "AND table_name = 'user_settings' AND column_name = 'user_sealed'",
                (schema,),
            ).fetchone() is None:
                continue
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


PEOPLE_COUNTS = f"{FAMILY_SCHEMA}.people_counts"
EVERYONE = "*"
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
    conn.execute(f"DROP TABLE IF EXISTS {FAMILY_SCHEMA}.people_by_day")


def first_exact_day(now=None):
    now = now or datetime.now(timezone.utc)
    return (now - timedelta(days=ACTIVITY_RETENTION_DAYS)).date() + timedelta(days=1)


def _midnight(day) -> datetime:
    return datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc)


def _next_month(day):
    return (day.replace(day=28) + timedelta(days=4)).replace(day=1)


def _real_ids_of(conn, schema: str, pseudonyms: set) -> dict:
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


def people_since(schemas: "list[str]", since) -> "tuple[int, bool]":
    import family_link
    import vault
    if not schemas:
        return 0, True
    with_key = family_link.vault_usable()
    not_people = set(family_link._GIFT_NOT_PEOPLE)
    people: set = set()
    complete = True
    with pooled_read() as conn:
        sealed = {row[0] for row in conn.execute(
            "SELECT table_schema FROM information_schema.columns WHERE table_schema = ANY(%s) "
            "AND table_name = 'user_settings' AND column_name = 'user_sealed'", (schemas,),
        ).fetchall()}
        for schema in sorted(_existing_tables(conn, schemas, "activity_events")):
            seen = {row[0] for row in conn.execute(
                f'SELECT DISTINCT user_id FROM "{schema}".activity_events WHERE occurred_at >= %s',
                (since,),
            ).fetchall()}
            pseudonyms = {u for u in seen if vault.is_pseudonym(u)}
            real = {}
            if pseudonyms and schema in sealed:
                if with_key:
                    real = _real_ids_of(conn, schema, pseudonyms)
                else:
                    complete = False
            for user_id in seen:
                person = real.get(user_id, user_id)
                if person > 0 and person not in not_people:
                    people.add(person)
    return len(people), complete


def _counts(by_day: dict, days) -> dict:
    union: dict = {}
    for day in days:
        for bot_id, people in by_day.get(day, {}).items():
            union.setdefault(bot_id, set()).update(people)
    return {bot_id: len(people) for bot_id, people in union.items()}


def people_live(bots: "list[tuple[str, str]]", everyone: "list[str]", first_day, last_day) -> dict:
    with pooled_read() as conn:
        by_day = _active_by_day(conn, bots, everyone, first_day, last_day)
    return _counts(by_day, [first_day + timedelta(days=n) for n in range((last_day - first_day).days + 1)])


def record_people_counts(bots: "list[tuple[str, str]]", everyone: "list[str]", now=None) -> int:
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
    with pooled_read() as conn:
        if schema not in _existing_tables(conn, [schema], "promo_seen"):
            return None
        return conn.execute(
            f'SELECT COUNT(*) FROM "{schema}".promo_seen WHERE promo = %s', (promo,)
        ).fetchone()[0]


SQL_TIMEOUT_SECONDS = 10
SQL_MAX_ROWS = 50


def run_readonly_query(sql: str, limit: int = SQL_MAX_ROWS):
    with pooled() as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        conn.execute(f"SET LOCAL statement_timeout = '{SQL_TIMEOUT_SECONDS}s'")
        cur = conn.execute(sql, prepare=True)
        cols = [d.name for d in cur.description] if cur.description else []
        rows = cur.fetchmany(limit) if cur.description else []
        conn.rollback()
        return cols, rows


ACTIVITY_RETENTION_DAYS = int(os.environ.get("ACTIVITY_RETENTION_DAYS", "90"))


def prune_old_data() -> int:
    with pooled() as conn:
        cur = conn.execute(
            "DELETE FROM activity_events WHERE occurred_at < now() - make_interval(days => %s)",
            (ACTIVITY_RETENTION_DAYS,),
        )
        removed = cur.rowcount
        conn.commit()
    return removed


def dump_database_csv_zip() -> bytes:
    return _dump_schemas([DB_SCHEMA])


def dump_family_csv_zip(schemas: list[str]) -> bytes:
    return _dump_schemas(schemas)


def _csv_safe(row: tuple) -> list:
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
