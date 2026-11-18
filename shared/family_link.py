from __future__ import annotations

import asyncio
import importlib
import json
import logging
import os
import re
import socket
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg
from psycopg import sql as pgsql

import db
import lifecycle
import redact
import vault

logger = logging.getLogger(__name__)

FAMILY_SCHEMA = "family"

VERSION = os.environ.get("FAMILY_VERSION", "3.0.0")

RELEASE = os.environ.get("FAMILY_RELEASE", "3.0.0")
BUILD = (os.environ.get("FAMILY_BUILD") or os.environ.get("RAILWAY_GIT_COMMIT_SHA") or "")[:7]
LABEL = f"{RELEASE} ({BUILD})" if BUILD else RELEASE

_REPORTS_BECAME_AUTOMATIC = datetime(2026, 9, 13, tzinfo=timezone.utc)

HEARTBEAT_SECONDS = int(os.environ.get("FAMILY_HEARTBEAT_SECONDS", "30"))

BUS_POLL_ACTIVE_SECONDS = float(os.environ.get("FAMILY_BUS_POLL_ACTIVE_SECONDS", "1"))
BUS_POLL_IDLE_SECONDS = float(os.environ.get("FAMILY_BUS_POLL_IDLE_SECONDS", "10"))
BUS_ACTIVE_WINDOW_SECONDS = float(os.environ.get("FAMILY_BUS_ACTIVE_WINDOW_SECONDS", "20"))

RUN_LATE = {"misfire_grace_time": None}

_bot_id: str | None = None
_display_name: str | None = None
_start_time: datetime | None = None
_enabled = False


HOSTNAME = socket.gethostname()

COMMAND_RETENTION_HOURS = int(os.environ.get("FAMILY_COMMAND_RETENTION_HOURS", "24"))
EVENT_RETENTION_DAYS = int(os.environ.get("FAMILY_EVENT_RETENTION_DAYS", "30"))
HOUSEKEEPING_SECONDS = int(os.environ.get("FAMILY_HOUSEKEEPING_SECONDS", "21600"))


def _connect():
    return db.pooled()


def _column_missing(conn, table: str, column: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_schema = %s AND table_name = %s AND column_name = %s",
        (FAMILY_SCHEMA, table, column),
    ).fetchone() is None


def init_family_schema() -> None:
    with _connect() as conn:
        conn.execute(f"CREATE SCHEMA IF NOT EXISTS {FAMILY_SCHEMA}")
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {FAMILY_SCHEMA}.heartbeats (
                bot_id TEXT PRIMARY KEY,
                display_name TEXT,
                host TEXT,
                version TEXT,
                pid INTEGER,
                db_schema TEXT,
                started_at TIMESTAMPTZ NOT NULL,
                last_seen TIMESTAMPTZ NOT NULL,
                error_count INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        if _column_missing(conn, "heartbeats", "commands"):
            conn.execute(f"ALTER TABLE {FAMILY_SCHEMA}.heartbeats "
                         f"ADD COLUMN IF NOT EXISTS commands TEXT")
        if _column_missing(conn, "heartbeats", "release"):
            conn.execute(f"ALTER TABLE {FAMILY_SCHEMA}.heartbeats "
                         f"ADD COLUMN IF NOT EXISTS release TEXT")
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {FAMILY_SCHEMA}.events (
                id BIGSERIAL PRIMARY KEY,
                bot_id TEXT NOT NULL,
                level TEXT NOT NULL,
                kind TEXT NOT NULL,
                message TEXT NOT NULL,
                details TEXT,
                occurred_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                notified BOOLEAN NOT NULL DEFAULT FALSE
            )
            """
        )
        conn.execute(
            f"CREATE INDEX IF NOT EXISTS idx_family_events_pending "
            f"ON {FAMILY_SCHEMA}.events (notified, id)"
        )
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {FAMILY_SCHEMA}.commands (
                id BIGSERIAL PRIMARY KEY,
                target_bot TEXT NOT NULL,
                command TEXT NOT NULL,
                args TEXT NOT NULL DEFAULT '',
                requested_by BIGINT,
                reply_chat_id BIGINT,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                claimed_at TIMESTAMPTZ,
                finished_at TIMESTAMPTZ,
                ok BOOLEAN,
                output TEXT,
                file_name TEXT,
                file_bytes BYTEA,
                delivered BOOLEAN NOT NULL DEFAULT FALSE
            )
            """
        )
        conn.execute(
            f"CREATE INDEX IF NOT EXISTS idx_family_commands_queue "
            f"ON {FAMILY_SCHEMA}.commands (target_bot, status, id)"
        )
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {FAMILY_SCHEMA}.usage_samples (
                id BIGSERIAL PRIMARY KEY,
                bot_id TEXT NOT NULL,
                sampled_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                window_minutes INTEGER NOT NULL,
                rss_mb INTEGER,
                peak_rss_mb INTEGER,
                ceiling_mb INTEGER,
                cpu_seconds INTEGER,
                updates INTEGER NOT NULL DEFAULT 0,
                users INTEGER NOT NULL DEFAULT 0,
                sleepable_seconds INTEGER NOT NULL DEFAULT 0,
                max_gap_seconds INTEGER NOT NULL DEFAULT 0,
                jobs_ok INTEGER NOT NULL DEFAULT 0,
                jobs_failed INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        for column in ("sleepable_seconds", "max_gap_seconds", "jobs_ok", "jobs_failed"):
            if _column_missing(conn, "usage_samples", column):
                conn.execute(
                    f"ALTER TABLE {FAMILY_SCHEMA}.usage_samples "
                    f"ADD COLUMN IF NOT EXISTS {column} INTEGER NOT NULL DEFAULT 0"
                )
        conn.execute(
            f"CREATE INDEX IF NOT EXISTS idx_family_usage_recent "
            f"ON {FAMILY_SCHEMA}.usage_samples (bot_id, sampled_at DESC)"
        )
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {FAMILY_SCHEMA}.button_taps (
                bot_id TEXT NOT NULL,
                day DATE NOT NULL,
                button TEXT NOT NULL,
                taps INTEGER NOT NULL,
                PRIMARY KEY (bot_id, day, button)
            )
            """
        )
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {FAMILY_SCHEMA}.daily_stats (
                bot_id TEXT NOT NULL,
                day DATE NOT NULL,
                metric TEXT NOT NULL,
                key TEXT NOT NULL,
                n INTEGER NOT NULL,
                PRIMARY KEY (bot_id, day, metric, key)
            )
            """
        )
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {FAMILY_SCHEMA}.bot_state (
                bot_id TEXT PRIMARY KEY,
                is_up BOOLEAN NOT NULL,
                changed_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {FAMILY_SCHEMA}.settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
            """
        )
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {FAMILY_SCHEMA}.star_balances (
                user_id BIGINT PRIMARY KEY,
                balance BIGINT NOT NULL DEFAULT 0,
                lifetime_topped_up BIGINT NOT NULL DEFAULT 0,
                lifetime_spent BIGINT NOT NULL DEFAULT 0,
                lifetime_stars_paid BIGINT NOT NULL DEFAULT 0,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {FAMILY_SCHEMA}.star_ledger (
                id BIGSERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                bot_id TEXT NOT NULL,
                delta BIGINT NOT NULL,
                reason TEXT NOT NULL,
                detail TEXT,
                stars_paid BIGINT NOT NULL DEFAULT 0,
                balance_after BIGINT NOT NULL,
                occurred_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        conn.execute(
            f"CREATE INDEX IF NOT EXISTS idx_family_star_ledger_user "
            f"ON {FAMILY_SCHEMA}.star_ledger (user_id, id DESC)"
        )
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {FAMILY_SCHEMA}.star_bonus_lots (
                id BIGSERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                amount BIGINT NOT NULL,
                remaining BIGINT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                expires_at TIMESTAMPTZ NOT NULL
            )
            """
        )
        conn.execute(
            f"CREATE INDEX IF NOT EXISTS idx_family_bonus_lots_user "
            f"ON {FAMILY_SCHEMA}.star_bonus_lots (user_id, expires_at)"
        )
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {FAMILY_SCHEMA}.problem_reports (
                id BIGSERIAL PRIMARY KEY,
                bot_id TEXT NOT NULL,
                code TEXT NOT NULL,
                incident TEXT NOT NULL,
                occurred_at TIMESTAMPTZ,
                reported_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                version TEXT,
                UNIQUE (bot_id, incident)
            )
            """
        )
        if _column_missing(conn, "problem_reports", "shared_at"):
            conn.execute(f"ALTER TABLE {FAMILY_SCHEMA}.problem_reports "
                         f"ADD COLUMN IF NOT EXISTS shared_at TIMESTAMPTZ")
        if _column_missing(conn, "problem_reports", "user_id"):
            conn.execute(f"ALTER TABLE {FAMILY_SCHEMA}.problem_reports "
                         f"ADD COLUMN IF NOT EXISTS user_id BIGINT")
        if _column_missing(conn, "problem_reports", "username"):
            conn.execute(f"ALTER TABLE {FAMILY_SCHEMA}.problem_reports "
                         f"ADD COLUMN IF NOT EXISTS username TEXT")
        if _column_missing(conn, "problem_reports", "user_lang"):
            conn.execute(f"ALTER TABLE {FAMILY_SCHEMA}.problem_reports "
                         f"ADD COLUMN IF NOT EXISTS user_lang TEXT")
        if _column_missing(conn, "problem_reports", "chat_kind"):
            conn.execute(f"ALTER TABLE {FAMILY_SCHEMA}.problem_reports "
                         f"ADD COLUMN IF NOT EXISTS chat_kind TEXT")
        if _column_missing(conn, "problem_reports", "seen"):
            conn.execute(f"ALTER TABLE {FAMILY_SCHEMA}.problem_reports "
                         f"ADD COLUMN IF NOT EXISTS seen INTEGER NOT NULL DEFAULT 1")
        if _column_missing(conn, "problem_reports", "level"):
            conn.execute(f"ALTER TABLE {FAMILY_SCHEMA}.problem_reports "
                         f"ADD COLUMN IF NOT EXISTS level INTEGER NOT NULL DEFAULT 2")
        conn.execute(f"UPDATE {FAMILY_SCHEMA}.problem_reports "
                     f"SET shared_at = reported_at "
                     f"WHERE shared_at IS NULL AND reported_at < %s",
                     (_REPORTS_BECAME_AUTOMATIC,))
        conn.execute(
            f"CREATE INDEX IF NOT EXISTS idx_family_problem_reports_recent "
            f"ON {FAMILY_SCHEMA}.problem_reports (reported_at DESC)"
        )
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {FAMILY_SCHEMA}.feedback (
                id BIGSERIAL PRIMARY KEY,
                bot_id TEXT NOT NULL,
                lang TEXT,
                body TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                delivered_at TIMESTAMPTZ
            )
            """
        )
        conn.execute(
            f"CREATE INDEX IF NOT EXISTS idx_family_feedback_pending "
            f"ON {FAMILY_SCHEMA}.feedback (delivered_at, id)"
        )
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {FAMILY_SCHEMA}.erasures (
                id BIGSERIAL PRIMARY KEY,
                bot TEXT NOT NULL,
                db_schema TEXT NOT NULL,
                sealed_user BYTEA NOT NULL,
                erased_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        conn.commit()


DATA_KEY_SETTING = "data_key:v1"

SEAL_STAGES = ("anon", "names")


def sealed_setting(stage: str) -> str:
    return f"sealed:{stage}:v1"


STAGE_MARK = {"anon": "anon_conversations", "names": "star_transactions"}
STAGE_TABLES = {
    "anon": frozenset((
        "anon_links", "anon_conversations", "anon_follower_state", "anon_relay", "anon_blocks",
        "user_settings", "donation_prompts", "promo_seen", "activity_events", "star_transactions")),
    "names": frozenset(("star_transactions", "packs")),
}
NAME_PURPOSE = "stars.username"
OWNER_PURPOSE = "sticker.pack.owner"
STATE_TABLES = (lifecycle.STATE_TABLE, lifecycle.WAITLIST_TABLE)

_TEST_PREFIX = "testbot_"


def belongs_to_family(schema: str, family_schema: "str | None" = None) -> bool:
    family_schema = family_schema or FAMILY_SCHEMA
    prefix = family_schema[:-len("family")] if family_schema.endswith("family") else ""
    return schema.startswith(prefix) if prefix else not schema.startswith(_TEST_PREFIX)


def _tables_of_family(conn, family_schema: str) -> dict:
    found: dict = {}
    for schema, table in conn.execute(
        "SELECT schemaname, tablename FROM pg_catalog.pg_tables "
        "WHERE schemaname <> 'information_schema' AND schemaname NOT LIKE 'pg\\_%'"
    ).fetchall():
        if belongs_to_family(schema, family_schema):
            found.setdefault(schema, set()).add(table)
    return found


def stage_schemas(conn, stage: str, family_schema: "str | None" = None) -> "list[str]":
    family_schema = family_schema or FAMILY_SCHEMA
    return sorted(schema for schema, tables in _tables_of_family(conn, family_schema).items()
                  if STAGE_MARK[stage] in tables)


def personal_data_tables(conn, stages=SEAL_STAGES, family_schema: "str | None" = None) -> "list[tuple[str, str]]":
    family_schema = family_schema or FAMILY_SCHEMA
    out: set = set()
    for schema, tables in _tables_of_family(conn, family_schema).items():
        for stage in stages:
            if STAGE_MARK[stage] in tables:
                out |= {(schema, table) for table in tables & STAGE_TABLES[stage]}
        out |= {(schema, table) for table in tables & set(STATE_TABLES)}
    return sorted(out)


def _has_rows(conn, schema: str, table: str) -> bool:
    return bool(conn.execute(
        pgsql.SQL("SELECT EXISTS (SELECT 1 FROM {}.{})").format(
            pgsql.Identifier(schema), pgsql.Identifier(table))
    ).fetchone()[0])


def read_stamp(conn, key: str, family_schema: "str | None" = None) -> "str | None":
    family_schema = family_schema or FAMILY_SCHEMA
    if conn.execute("SELECT to_regclass(%s)", (f"{family_schema}.settings",)).fetchone()[0] is None:
        return None
    row = conn.execute(
        pgsql.SQL("SELECT value FROM {}.settings WHERE key = %s").format(pgsql.Identifier(family_schema)),
        (key,)).fetchone()
    return row[0] if row else None


def ensure_settings_table(conn, family_schema: str) -> bool:
    if conn.execute("SELECT to_regclass(%s)", (f"{family_schema}.settings",)).fetchone()[0] is not None:
        return False
    conn.execute(pgsql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(pgsql.Identifier(family_schema)))
    conn.execute(pgsql.SQL("CREATE TABLE IF NOT EXISTS {}.settings (key TEXT PRIMARY KEY, value TEXT)")
                 .format(pgsql.Identifier(family_schema)))
    return True


def write_stamps(conn, fingerprint: str, stages=SEAL_STAGES, family_schema: "str | None" = None) -> None:
    family_schema = family_schema or FAMILY_SCHEMA
    ensure_settings_table(conn, family_schema)
    insert = pgsql.SQL("INSERT INTO {}.settings (key, value) VALUES (%s, %s) "
                       "ON CONFLICT (key) DO NOTHING").format(pgsql.Identifier(family_schema))
    conn.execute(insert, (DATA_KEY_SETTING, fingerprint))
    for stage in stages:
        conn.execute(insert, (sealed_setting(stage), "1"))


def fresh_heartbeats(conn, within_seconds: int = 180, family_schema: "str | None" = None) -> "list[str]":
    family_schema = family_schema or FAMILY_SCHEMA
    if conn.execute("SELECT to_regclass(%s)", (f"{family_schema}.heartbeats",)).fetchone()[0] is None:
        return []
    rows = conn.execute(
        pgsql.SQL("SELECT bot_id FROM {}.heartbeats "
                  "WHERE last_seen > now() - make_interval(secs => %s) ORDER BY bot_id")
        .format(pgsql.Identifier(family_schema)), (within_seconds,)).fetchall()
    return [row[0] for row in rows]


_NOT_SEALED = ("Personal data in this database is not sealed yet: stop the bots and run "
               "scripts/security/encrypt_personal_data.py")
_WRONG_KEY = "The data key file is not the key this database was sealed with"


class DataKeyRefused(SystemExit):
    data_key = True


def _refuse(message: str):
    logger.critical("%s", message)
    raise DataKeyRefused(message)


def inspect_data_key(conn) -> dict:
    try:
        fingerprint = vault.require()
    except vault.VaultError as exc:
        return {"usable": False, "sealed": frozenset(), "problem": f"The data key is not usable: {exc}"}
    if vault.init_note:
        logger.warning("%s", vault.init_note)

    stamped = read_stamp(conn, DATA_KEY_SETTING)
    if stamped is None:
        holding = [f"{schema}.{table}" for schema, table in personal_data_tables(conn)
                   if _has_rows(conn, schema, table)]
        if holding:
            stamped = read_stamp(conn, DATA_KEY_SETTING)
            if stamped is None:
                logger.warning("Rows that are not sealed may be in: %s", ", ".join(holding))
                return {"usable": False, "sealed": frozenset(), "problem": _NOT_SEALED}
        else:
            write_stamps(conn, fingerprint)
            conn.commit()
            stamped = read_stamp(conn, DATA_KEY_SETTING)
    if stamped != fingerprint:
        return {"usable": False, "sealed": frozenset(), "problem": _WRONG_KEY}
    sealed = frozenset(stage for stage in SEAL_STAGES if read_stamp(conn, sealed_setting(stage)) == "1")
    return {"usable": True, "sealed": sealed, "problem": None}


def key_problem(state: dict, stages=()) -> "str | None":
    if not state["usable"]:
        return state["problem"]
    unsealed = [stage for stage in stages if stage not in state["sealed"]]
    if unsealed:
        logger.warning("Not sealed yet, by stage: %s", ", ".join(unsealed))
        return _NOT_SEALED
    return None


def ensure_data_key(conn, stages=SEAL_STAGES) -> None:
    problem = key_problem(inspect_data_key(conn), stages)
    if problem:
        _refuse(problem)
    logger.info("Data key checked: it is the key this database is sealed with.")


def _require_transaction(conn) -> None:
    if getattr(conn, "autocommit", False):
        raise ValueError("The conversion has to run in one transaction: give it a connection "
                         "with autocommit off")


def _schemas_with(conn, table: str, family_schema: str) -> "list[str]":
    return sorted(schema for schema, tables in _tables_of_family(conn, family_schema).items()
                  if table in tables)


def _column_exists(conn, schema: str, table: str, column: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_schema = %s AND table_name = %s AND column_name = %s",
        (schema, table, column)).fetchone() is not None


def _waitlist_row_opens(user_id, chat_id, sealed) -> bool:
    if sealed is None or not (vault.is_pseudonym(user_id) and vault.is_pseudonym(chat_id)):
        return False
    try:
        who = vault.open_json(sealed, lifecycle.WAITLIST_PURPOSE)
        return (vault.pseudonym(int(who["user"])) == int(user_id)
                and vault.pseudonym(int(who["chat"])) == int(chat_id))
    except (vault.VaultError, KeyError, TypeError, ValueError, AttributeError):
        return False


def convert_personal_data_v1(conn, family_schema: "str | None" = None) -> dict:
    _require_transaction(conn)
    family_schema = family_schema or FAMILY_SCHEMA
    changed: dict = {}
    skipped: list = []

    for schema in _schemas_with(conn, lifecycle.WAITLIST_TABLE, family_schema):
        label = f"{schema}.{lifecycle.WAITLIST_TABLE}"
        if not _column_exists(conn, schema, lifecycle.WAITLIST_TABLE, "who_sealed"):
            conn.execute(pgsql.SQL("ALTER TABLE {}.{} ADD COLUMN IF NOT EXISTS who_sealed BYTEA").format(
                pgsql.Identifier(schema), pgsql.Identifier(lifecycle.WAITLIST_TABLE)))
        rows = conn.execute(pgsql.SQL("SELECT bot_id, user_id, chat_id, who_sealed FROM {}.{}").format(
            pgsql.Identifier(schema), pgsql.Identifier(lifecycle.WAITLIST_TABLE))).fetchall()
        gone = 0
        for bot_id, user_id, chat_id, sealed in rows:
            if _waitlist_row_opens(user_id, chat_id, sealed):
                continue
            gone += conn.execute(
                pgsql.SQL("DELETE FROM {}.{} WHERE bot_id = %s AND user_id = %s").format(
                    pgsql.Identifier(schema), pgsql.Identifier(lifecycle.WAITLIST_TABLE)),
                (bot_id, user_id)).rowcount or 0
        changed[label] = gone
    if not changed:
        skipped.append(f"{family_schema}: no {lifecycle.WAITLIST_TABLE} table")

    states = _schemas_with(conn, lifecycle.STATE_TABLE, family_schema)
    for schema in states:
        rows = conn.execute(pgsql.SQL("SELECT kind, key, value FROM {}.{}").format(
            pgsql.Identifier(schema), pgsql.Identifier(lifecycle.STATE_TABLE))).fetchall()
        gone = 0
        for kind, key, value in rows:
            try:
                lifecycle.unseal_state(kind, key, value)
                continue
            except vault.VaultError:
                pass
            gone += conn.execute(
                pgsql.SQL("DELETE FROM {}.{} WHERE kind = %s AND key = %s").format(
                    pgsql.Identifier(schema), pgsql.Identifier(lifecycle.STATE_TABLE)),
                (kind, key)).rowcount or 0
        changed[f"{schema}.{lifecycle.STATE_TABLE}"] = gone
    if not states:
        skipped.append(f"{family_schema}: no {lifecycle.STATE_TABLE} table in any schema")
    return {"changed": changed, "skipped": skipped}


def verify_personal_data_v1(conn, family_schema: "str | None" = None) -> dict:
    family_schema = family_schema or FAMILY_SCHEMA
    checked: dict = {}
    skipped: list = []
    problems: list = []

    for schema in _schemas_with(conn, lifecycle.WAITLIST_TABLE, family_schema):
        label = f"{schema}.{lifecycle.WAITLIST_TABLE}"
        if not _column_exists(conn, schema, lifecycle.WAITLIST_TABLE, "who_sealed"):
            problems.append(f"{label} has no who_sealed column")
            continue
        rows = conn.execute(pgsql.SQL("SELECT bot_id, user_id, chat_id, who_sealed FROM {}.{}").format(
            pgsql.Identifier(schema), pgsql.Identifier(lifecycle.WAITLIST_TABLE))).fetchall()
        for index, (bot_id, user_id, chat_id, sealed) in enumerate(rows, 1):
            if not _waitlist_row_opens(user_id, chat_id, sealed):
                problems.append(f"{label} row {index} ({bot_id}): not sealed, or does not open")
        checked[label] = len(rows)
    if not checked:
        skipped.append(f"{family_schema}: no {lifecycle.WAITLIST_TABLE} table")

    states = _schemas_with(conn, lifecycle.STATE_TABLE, family_schema)
    for schema in states:
        label = f"{schema}.{lifecycle.STATE_TABLE}"
        rows = conn.execute(pgsql.SQL("SELECT kind, key, value FROM {}.{}").format(
            pgsql.Identifier(schema), pgsql.Identifier(lifecycle.STATE_TABLE))).fetchall()
        for index, (kind, key, value) in enumerate(rows, 1):
            try:
                lifecycle.unseal_state(kind, key, value)
            except vault.VaultError:
                problems.append(f"{label} row {index} ({kind}): not sealed, or does not open")
        checked[label] = len(rows)
    if not states:
        skipped.append(f"{family_schema}: no {lifecycle.STATE_TABLE} table in any schema")

    if problems:
        raise RuntimeError("Saved state in the family is not sealed: " + "; ".join(problems[:20])
                           + (f" (and {len(problems) - 20} more)" if len(problems) > 20 else ""))
    return {"checked": checked, "skipped": skipped}


def _changed_one(cur, what: str) -> None:
    if cur.rowcount != 1:
        raise RuntimeError(f"the names stage expected to change one row of {what}, changed {cur.rowcount}")


def _table_exists(conn, schema: str, table: str) -> bool:
    return conn.execute("SELECT to_regclass(%s)", (f'"{schema}"."{table}"',)).fetchone()[0] is not None


def convert_names_v1(conn, schema: str) -> dict:
    _require_transaction(conn)
    counts: dict = {}
    ident = pgsql.Identifier
    if _table_exists(conn, schema, "star_transactions"):
        table = pgsql.SQL("{}.star_transactions").format(ident(schema))
        if not _column_exists(conn, schema, "star_transactions", "username_sealed"):
            conn.execute(pgsql.SQL("ALTER TABLE {} ADD COLUMN IF NOT EXISTS username_sealed BYTEA").format(table))
        sealed = 0
        if _column_exists(conn, schema, "star_transactions", "username"):
            rows = conn.execute(pgsql.SQL(
                "SELECT id, username FROM {} WHERE username IS NOT NULL AND username_sealed IS NULL"
            ).format(table)).fetchall()
            for row_id, username in rows:
                _changed_one(conn.execute(
                    pgsql.SQL("UPDATE {} SET username_sealed = %s WHERE id = %s").format(table),
                    (vault.seal_text(username, NAME_PURPOSE), row_id)), f"{schema}.star_transactions")
            sealed = len(rows)
            conn.execute(pgsql.SQL("ALTER TABLE {} DROP COLUMN IF EXISTS username").format(table))
        counts["star_transactions.username_sealed"] = sealed
    if _table_exists(conn, schema, "packs"):
        table = pgsql.SQL("{}.packs").format(ident(schema))
        if not _column_exists(conn, schema, "packs", "owner_sealed"):
            conn.execute(pgsql.SQL("ALTER TABLE {} ADD COLUMN IF NOT EXISTS owner_sealed BYTEA").format(table))
        sealed = 0
        if _column_exists(conn, schema, "packs", "owner_username"):
            rows = conn.execute(pgsql.SQL(
                "SELECT id, owner_username, owner_name FROM {} "
                "WHERE (owner_username IS NOT NULL OR owner_name IS NOT NULL) AND owner_sealed IS NULL"
            ).format(table)).fetchall()
            for row_id, username, name in rows:
                _changed_one(conn.execute(
                    pgsql.SQL("UPDATE {} SET owner_sealed = %s WHERE id = %s").format(table),
                    (vault.seal_json({"username": username, "name": name}, OWNER_PURPOSE), row_id)),
                    f"{schema}.packs")
            sealed = len(rows)
            conn.execute(pgsql.SQL("ALTER TABLE {} DROP COLUMN IF EXISTS owner_username, "
                                   "DROP COLUMN IF EXISTS owner_name").format(table))
        counts["packs.owner_sealed"] = sealed
    return counts


def verify_names_v1(conn, schema: str) -> dict:
    counts: dict = {}
    problems: list = []
    for table, column, plain, purpose, as_json in (
            ("star_transactions", "username_sealed", ("username",), NAME_PURPOSE, False),
            ("packs", "owner_sealed", ("owner_username", "owner_name"), OWNER_PURPOSE, True)):
        if not _table_exists(conn, schema, table):
            continue
        left = [name for name in plain if _column_exists(conn, schema, table, name)]
        if left:
            problems.append(f"{schema}.{table}: the plain column(s) {', '.join(left)} are still there")
        if not _column_exists(conn, schema, table, column):
            problems.append(f"{schema}.{table}.{column}: the column is missing")
            continue
        rows = conn.execute(pgsql.SQL("SELECT {} FROM {}.{} WHERE {} IS NOT NULL").format(
            pgsql.Identifier(column), pgsql.Identifier(schema), pgsql.Identifier(table),
            pgsql.Identifier(column))).fetchall()
        opened = 0
        for (blob,) in rows:
            try:
                (vault.open_json if as_json else vault.open_text)(blob, purpose)
                opened += 1
            except (vault.VaultError, ValueError, UnicodeDecodeError):
                pass
        counts[f"{table}.{column}"] = opened
        if opened != len(rows):
            problems.append(f"{schema}.{table}.{column}: {len(rows) - opened} of {len(rows)} sealed "
                            f"value(s) do not open")
    if problems:
        raise RuntimeError("Names are not sealed: " + "; ".join(problems))
    return counts


TOPUP_MULTIPLIER = float(os.environ.get("FAMILY_TOPUP_MULTIPLIER", "2"))


def _parse_ladder(spec: str) -> list:
    steps = []
    for part in (spec or "").split(","):
        stars, _, multiplier = part.strip().partition(":")
        try:
            steps.append((int(stars), float(multiplier)))
        except ValueError:
            continue
    return [(stars, multiplier) for stars, multiplier in steps if stars > 0 and multiplier >= 1]


TOPUP_LADDER = _parse_ladder(os.environ.get("FAMILY_TOPUP_LADDER", "500:3,500:2"))
BONUS_EXPIRY_DAYS = int(os.environ.get("FAMILY_BONUS_EXPIRY_DAYS", "90"))

LEDGER_REASONS = ("topup", "bonus", "grant", "spend", "refund", "adjustment", "expired")


def credit_for_stars(stars: int) -> int:
    return int(round(stars * TOPUP_MULTIPLIER, 6))


def _ladder_ranges():
    start = 0
    for size, multiplier in TOPUP_LADDER:
        yield start, start + size, multiplier
        start += size
    yield start, None, 1.0


def quote_credit(lifetime_paid: int, stars: int) -> dict:
    low, high = max(0, lifetime_paid), max(0, lifetime_paid) + max(0, stars)
    total = 0.0
    for start, end, multiplier in _ladder_ranges():
        overlap_low = max(start, low)
        overlap_high = high if end is None else min(end, high)
        if overlap_high > overlap_low:
            total += (overlap_high - overlap_low) * TOPUP_MULTIPLIER * multiplier
    base = credit_for_stars(stars)
    total_credit = max(int(round(total, 6)), base)
    return {"stars": stars, "base": base, "bonus": total_credit - base, "total": total_credit}


def ladder_position(lifetime_paid: int) -> tuple:
    for start, end, multiplier in _ladder_ranges():
        if end is None or lifetime_paid < end:
            return multiplier, (None if end is None else end - max(lifetime_paid, start))
    return 1.0, None


def _ledger_bot() -> str:
    return _bot_id or "unattached"


def _ledger_in(conn, user_id, delta, reason, detail, stars_paid, balance_after) -> None:
    conn.execute(
        f"INSERT INTO {FAMILY_SCHEMA}.star_ledger "
        f"(user_id, bot_id, delta, reason, detail, stars_paid, balance_after) "
        f"VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (user_id, _ledger_bot(), delta, reason, detail, stars_paid, balance_after),
    )


def _lock_wallet_in(conn, user_id) -> int:
    conn.execute(
        f"INSERT INTO {FAMILY_SCHEMA}.star_balances (user_id) VALUES (%s) ON CONFLICT (user_id) DO NOTHING",
        (user_id,),
    )
    return int(conn.execute(
        f"SELECT balance FROM {FAMILY_SCHEMA}.star_balances WHERE user_id = %s FOR UPDATE",
        (user_id,),
    ).fetchone()[0])


def _expire_in(conn, user_id) -> int:
    due = conn.execute(
        f"SELECT id, remaining FROM {FAMILY_SCHEMA}.star_bonus_lots "
        f"WHERE user_id = %s AND remaining > 0 AND expires_at <= now() FOR UPDATE",
        (user_id,),
    ).fetchall()
    total = sum(int(row[1]) for row in due)
    if not total:
        return 0
    conn.execute(
        f"UPDATE {FAMILY_SCHEMA}.star_bonus_lots SET remaining = 0 WHERE id = ANY(%s)",
        ([row[0] for row in due],),
    )
    after = conn.execute(
        f"UPDATE {FAMILY_SCHEMA}.star_balances SET balance = balance - %s, updated_at = now() "
        f"WHERE user_id = %s RETURNING balance",
        (total, user_id),
    ).fetchone()[0]
    _ledger_in(conn, user_id, -total, "expired", "bonus credit expired", 0, int(after))
    return total


def _take_bonus_in(conn, user_id, amount) -> None:
    if amount <= 0:
        return
    lots = conn.execute(
        f"SELECT id, remaining FROM {FAMILY_SCHEMA}.star_bonus_lots "
        f"WHERE user_id = %s AND remaining > 0 AND expires_at > now() "
        f"ORDER BY expires_at, id FOR UPDATE",
        (user_id,),
    ).fetchall()
    for lot_id, remaining in lots:
        if amount <= 0:
            break
        taken = min(int(remaining), amount)
        conn.execute(
            f"UPDATE {FAMILY_SCHEMA}.star_bonus_lots SET remaining = remaining - %s WHERE id = %s",
            (taken, lot_id),
        )
        amount -= taken


def _bonus_in(conn, user_id) -> int:
    return int(conn.execute(
        f"SELECT coalesce(sum(remaining), 0) FROM {FAMILY_SCHEMA}.star_bonus_lots "
        f"WHERE user_id = %s AND remaining > 0 AND expires_at > now()",
        (user_id,),
    ).fetchone()[0])


def _cap_bonus_in(conn, user_id, balance) -> None:
    excess = _bonus_in(conn, user_id) - max(balance, 0)
    _take_bonus_in(conn, user_id, excess)


def _move_in(conn, user_id, delta, reason, detail, stars_paid=0) -> int:
    earned = delta if reason in ("topup", "bonus", "grant") and delta > 0 else 0
    after = int(conn.execute(
        f"UPDATE {FAMILY_SCHEMA}.star_balances "
        f"SET balance = balance + %s, lifetime_topped_up = lifetime_topped_up + %s, "
        f"lifetime_stars_paid = lifetime_stars_paid + %s, updated_at = now() "
        f"WHERE user_id = %s RETURNING balance",
        (delta, earned, stars_paid, user_id),
    ).fetchone()[0])
    _ledger_in(conn, user_id, delta, reason, detail, stars_paid, after)
    return after


def star_balance(user_id: int) -> int:
    with _connect() as conn:
        exists = conn.execute(
            f"SELECT 1 FROM {FAMILY_SCHEMA}.star_balances WHERE user_id = %s", (user_id,)).fetchone()
        if not exists:
            return 0
        _lock_wallet_in(conn, user_id)
        _expire_in(conn, user_id)
        balance = int(conn.execute(
            f"SELECT balance FROM {FAMILY_SCHEMA}.star_balances WHERE user_id = %s", (user_id,)
        ).fetchone()[0])
        conn.commit()
    return balance


def star_totals(user_id: int) -> dict:
    empty = {"balance": 0, "topped_up": 0, "spent": 0, "stars_paid": 0,
             "bonus": 0, "bonus_next_amount": 0, "bonus_next_expiry": None}
    with _connect() as conn:
        exists = conn.execute(
            f"SELECT 1 FROM {FAMILY_SCHEMA}.star_balances WHERE user_id = %s", (user_id,)).fetchone()
        if not exists:
            return empty
        _lock_wallet_in(conn, user_id)
        _expire_in(conn, user_id)
        row = conn.execute(
            f"SELECT balance, lifetime_topped_up, lifetime_spent, lifetime_stars_paid "
            f"FROM {FAMILY_SCHEMA}.star_balances WHERE user_id = %s", (user_id,)
        ).fetchone()
        nxt = conn.execute(
            f"SELECT expires_at, sum(remaining) FROM {FAMILY_SCHEMA}.star_bonus_lots "
            f"WHERE user_id = %s AND remaining > 0 AND expires_at > now() "
            f"GROUP BY expires_at ORDER BY expires_at LIMIT 1", (user_id,)
        ).fetchone()
        bonus = _bonus_in(conn, user_id)
        conn.commit()
    return {"balance": int(row[0]), "topped_up": int(row[1]), "spent": int(row[2]),
            "stars_paid": int(row[3]), "bonus": bonus,
            "bonus_next_amount": int(nxt[1]) if nxt else 0,
            "bonus_next_expiry": nxt[0] if nxt else None}


def quote_topup(user_id: int, stars: int) -> dict:
    with _connect() as conn:
        row = conn.execute(
            f"SELECT lifetime_stars_paid FROM {FAMILY_SCHEMA}.star_balances WHERE user_id = %s",
            (user_id,),
        ).fetchone()
    return quote_credit(int(row[0]) if row else 0, stars)


def move_stars(user_id: int, delta: int, reason: str, detail: str | None = None,
               stars_paid: int = 0) -> int:
    if reason not in LEDGER_REASONS:
        raise ValueError(f"unknown ledger reason {reason!r}; add it to LEDGER_REASONS")
    if delta == 0 and not stars_paid:
        return star_balance(user_id)
    with _connect() as conn:
        _lock_wallet_in(conn, user_id)
        _expire_in(conn, user_id)
        after = _move_in(conn, user_id, delta, reason, detail, stars_paid)
        if delta < 0:
            _cap_bonus_in(conn, user_id, after)
        conn.commit()
    return after


def spend_stars(user_id: int, amount: int, reason: str = "spend",
                detail: str | None = None) -> "int | None":
    if amount <= 0:
        return star_balance(user_id)
    with _connect() as conn:
        balance = _lock_wallet_in(conn, user_id)
        balance -= _expire_in(conn, user_id)
        if balance < amount:
            conn.commit()
            return None
        after = int(conn.execute(
            f"UPDATE {FAMILY_SCHEMA}.star_balances SET balance = balance - %s, "
            f"lifetime_spent = lifetime_spent + %s, updated_at = now() "
            f"WHERE user_id = %s RETURNING balance",
            (amount, amount, user_id),
        ).fetchone()[0])
        _ledger_in(conn, user_id, -amount, reason, detail, 0, after)
        _take_bonus_in(conn, user_id, amount)
        conn.commit()
    return after


def _credited_claim(text: "str | None") -> "dict | None":
    if not text or not text.startswith("{"):
        return None
    try:
        found = json.loads(text)
    except ValueError:
        return None
    return found if isinstance(found, dict) and found.get("attempt") else None


def _recovered_topup(user_id: int, stars_paid: int, claim: dict) -> dict:
    expires = _moment(claim.get("expires"))
    try:
        balance = star_balance(user_id)
    except psycopg.OperationalError:
        logger.warning("topup: the balance after a recovered credit could not be read")
        balance = "?"
    return {"stars": stars_paid, "credited": int(claim.get("credited") or 0),
            "bonus": int(claim.get("bonus") or 0), "balance": balance,
            "bonus_expires": expires, "lifetime_before": int(claim.get("lifetime_before") or 0),
            "recovered": True}


def charge_claim(charge_id: str) -> "str | None":
    with _connect() as conn:
        row = conn.execute(f"SELECT value FROM {FAMILY_SCHEMA}.settings WHERE key = %s",
                           (f"stars:paid:{charge_id}",)).fetchone()
    return row[0] if row else None


def topup(user_id: int, stars_paid: int, detail: str | None = None,
          charge_id: "str | None" = None) -> "dict | None":
    attempt_id = os.urandom(8).hex()
    claim_key = f"stars:paid:{charge_id}"
    recovered = None
    for attempt in range(1, _TOPUP_ATTEMPTS + 1):
        try:
            with _connect() as conn:
                _lock_wallet_in(conn, user_id)
                if charge_id:
                    won = conn.execute(
                        f"INSERT INTO {FAMILY_SCHEMA}.settings (key, value) VALUES (%s, %s) "
                        f"ON CONFLICT (key) DO NOTHING RETURNING key",
                        (claim_key, datetime.now(timezone.utc).isoformat()),
                    ).fetchone()
                    if not won:
                        held = conn.execute(
                            f"SELECT value FROM {FAMILY_SCHEMA}.settings WHERE key = %s", (claim_key,),
                        ).fetchone()
                        conn.rollback()
                        claim = _credited_claim(held[0] if held else None)
                        if claim is None or claim.get("attempt") != attempt_id:
                            return None
                        recovered = claim
                        break
                _expire_in(conn, user_id)
                lifetime = int(conn.execute(
                    f"SELECT lifetime_stars_paid FROM {FAMILY_SCHEMA}.star_balances WHERE user_id = %s",
                    (user_id,),
                ).fetchone()[0])
                quote = quote_credit(lifetime, stars_paid)
                balance = _move_in(conn, user_id, quote["base"], "topup", detail, stars_paid)
                expires = None
                if quote["bonus"] > 0:
                    balance = _move_in(conn, user_id, quote["bonus"], "bonus",
                                       f"ladder bonus on {stars_paid} stars, expires in {BONUS_EXPIRY_DAYS} days")
                    expires = conn.execute(
                        f"INSERT INTO {FAMILY_SCHEMA}.star_bonus_lots (user_id, amount, remaining, expires_at) "
                        f"VALUES (%s, %s, %s, now() + make_interval(days => %s)) RETURNING expires_at",
                        (user_id, quote["bonus"], quote["bonus"], BONUS_EXPIRY_DAYS),
                    ).fetchone()[0]
                if charge_id:
                    conn.execute(
                        f"UPDATE {FAMILY_SCHEMA}.settings SET value = %s WHERE key = %s",
                        (json.dumps({"at": datetime.now(timezone.utc).isoformat(), "attempt": attempt_id,
                                     "credited": quote["base"], "bonus": quote["bonus"],
                                     "expires": expires.isoformat() if expires else None,
                                     "lifetime_before": lifetime}, separators=(",", ":")),
                         claim_key),
                    )
                conn.commit()
            break
        except psycopg.OperationalError as exc:
            if attempt == _TOPUP_ATTEMPTS:
                raise
            logger.warning("topup: database error on attempt %d of %d (%s); trying again",
                           attempt, _TOPUP_ATTEMPTS, exc)
            time.sleep(_TOPUP_BACKOFF_SECONDS[attempt - 1])
    if recovered is not None:
        logger.warning("topup: charge %s was credited by an earlier attempt whose answer was lost; "
                       "carrying on with that credit", charge_id)
        return _recovered_topup(user_id, stars_paid, recovered)
    return {"stars": stars_paid, "credited": quote["base"], "bonus": quote["bonus"],
            "balance": balance, "bonus_expires": expires, "lifetime_before": lifetime}


_TOPUP_ATTEMPTS = 3
_TOPUP_BACKOFF_SECONDS = (1.0, 3.0)

UNCREDITED_CLAIM = "uncredited:"


def claim_uncredited(charge_id: str) -> "str | None":
    key = f"stars:paid:{charge_id}"
    mine = f"{UNCREDITED_CLAIM}{datetime.now(timezone.utc).isoformat()} {os.urandom(6).hex()}"
    for attempt in range(1, _TOPUP_ATTEMPTS + 1):
        try:
            with _connect() as conn:
                won = conn.execute(
                    f"INSERT INTO {FAMILY_SCHEMA}.settings (key, value) VALUES (%s, %s) "
                    f"ON CONFLICT (key) DO NOTHING RETURNING key",
                    (key, mine),
                ).fetchone()
                if won:
                    conn.commit()
                    return None
                held = conn.execute(
                    f"SELECT value FROM {FAMILY_SCHEMA}.settings WHERE key = %s", (key,),
                ).fetchone()
                conn.rollback()
            if held is None:
                raise RuntimeError(f"the claim for charge {charge_id} was there, and then was not")
            return None if held[0] == mine else held[0]
        except psycopg.OperationalError as exc:
            if attempt == _TOPUP_ATTEMPTS:
                raise
            logger.warning("claim_uncredited: database error on attempt %d of %d (%s); trying again",
                           attempt, _TOPUP_ATTEMPTS, exc)
            time.sleep(_TOPUP_BACKOFF_SECONDS[attempt - 1])


def set_star_balance(user_id: int, target: int, reason: str = "adjustment",
                     detail: str | None = None) -> int:
    with _connect() as conn:
        before = _lock_wallet_in(conn, user_id)
        before -= _expire_in(conn, user_id)
        delta = target - before
        if delta:
            conn.execute(
                f"UPDATE {FAMILY_SCHEMA}.star_balances SET balance = %s, updated_at = now() WHERE user_id = %s",
                (target, user_id),
            )
            _ledger_in(conn, user_id, delta, reason, detail, 0, target)
            _cap_bonus_in(conn, user_id, target)
        conn.commit()
    return target


def grant_stars_once(user_id: int, amount: int, key: str, detail: str | None = None,
                     reason: str = "grant") -> "int | None":
    claim = f"stars:granted:{key}:{user_id}"
    with _connect() as conn:
        won = conn.execute(
            f"INSERT INTO {FAMILY_SCHEMA}.settings (key, value) VALUES (%s, %s) "
            f"ON CONFLICT (key) DO NOTHING RETURNING key",
            (claim, datetime.now(timezone.utc).isoformat()),
        ).fetchone()
        conn.commit()
    if not won:
        return None
    try:
        return move_stars(user_id, amount, reason, detail or key)
    except Exception:
        try:
            with _connect() as conn:
                conn.execute(f"DELETE FROM {FAMILY_SCHEMA}.settings WHERE key = %s", (claim,))
                conn.commit()
        except Exception:
            logger.exception("Could not release the grant claim %s", claim)
        raise


GIFT_MAX_CREDIT = 1000


def grant_bonus_once(user_id: int, amount: int, key: str, expires_at: datetime,
                     detail: str | None = None) -> "int | None":
    if not 0 < amount <= GIFT_MAX_CREDIT:
        raise ValueError(f"a gift is 1..{GIFT_MAX_CREDIT} credits, not {amount}")
    if expires_at.tzinfo is None:
        raise ValueError("a gift's expiry must carry its time zone")
    if expires_at <= datetime.now(timezone.utc):
        raise ValueError("a gift that has already expired gives nothing")
    claim = f"stars:granted:{key}:{user_id}"
    with _connect() as conn:
        _lock_wallet_in(conn, user_id)
        won = conn.execute(
            f"INSERT INTO {FAMILY_SCHEMA}.settings (key, value) VALUES (%s, %s) "
            f"ON CONFLICT (key) DO NOTHING RETURNING key",
            (claim, datetime.now(timezone.utc).isoformat()),
        ).fetchone()
        if not won:
            conn.rollback()
            return None
        _expire_in(conn, user_id)
        last_day = expires_at - timedelta(microseconds=1)
        balance = _move_in(conn, user_id, amount, "grant",
                           detail or f"gift {key}, bonus through {last_day:%Y-%m-%d}")
        conn.execute(
            f"INSERT INTO {FAMILY_SCHEMA}.star_bonus_lots (user_id, amount, remaining, expires_at) "
            f"VALUES (%s, %s, %s, %s)",
            (user_id, amount, amount, expires_at),
        )
        conn.commit()
    return balance


def expire_bonus_credit() -> int:
    with _connect() as conn:
        users = [row[0] for row in conn.execute(
            f"SELECT DISTINCT user_id FROM {FAMILY_SCHEMA}.star_bonus_lots "
            f"WHERE remaining > 0 AND expires_at <= now()"
        ).fetchall()]
    total = 0
    for user_id in users:
        with _connect() as conn:
            _lock_wallet_in(conn, user_id)
            total += _expire_in(conn, user_id)
            conn.commit()
    return total


def star_ledger_for(user_id: int, limit: int = 10) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT occurred_at, bot_id, delta, reason, detail, stars_paid, balance_after "
            f"FROM {FAMILY_SCHEMA}.star_ledger WHERE user_id = %s "
            f"ORDER BY id DESC LIMIT %s",
            (user_id, max(1, min(limit, 100))),
        ).fetchall()
    return [{"occurred_at": r[0], "bot_id": r[1], "delta": int(r[2]),
             "reason": r[3], "detail": r[4], "stars_paid": int(r[5] or 0),
             "balance_after": int(r[6])}
            for r in rows]


def star_balance_overview(limit: int = 20) -> tuple[dict, list[dict]]:
    with _connect() as conn:
        totals = conn.execute(
            f"SELECT coalesce(sum(balance), 0), coalesce(sum(lifetime_topped_up), 0), "
            f"coalesce(sum(lifetime_spent), 0), coalesce(sum(lifetime_stars_paid), 0), "
            f"count(*) FROM {FAMILY_SCHEMA}.star_balances"
        ).fetchone()
        bonus = conn.execute(
            f"SELECT coalesce(sum(remaining), 0) FROM {FAMILY_SCHEMA}.star_bonus_lots "
            f"WHERE remaining > 0 AND expires_at > now()"
        ).fetchone()[0]
        rows = conn.execute(
            f"SELECT user_id, balance, lifetime_topped_up, lifetime_spent, lifetime_stars_paid "
            f"FROM {FAMILY_SCHEMA}.star_balances "
            f"WHERE balance <> 0 ORDER BY balance DESC LIMIT %s",
            (max(1, min(limit, 100)),),
        ).fetchall()
    return (
        {"outstanding": int(totals[0]), "topped_up": int(totals[1]),
         "spent": int(totals[2]), "stars_paid": int(totals[3]),
         "wallets": int(totals[4]), "bonus": int(bonus)},
        [{"user_id": int(r[0]), "balance": int(r[1]), "topped_up": int(r[2]),
          "spent": int(r[3]), "stars_paid": int(r[4])} for r in rows],
    )


def record_problem_occurrence(code: str, incident: str, occurred_at, level: int = 2) -> bool:
    with _connect() as conn:
        row = conn.execute(
            f"INSERT INTO {FAMILY_SCHEMA}.problem_reports "
            f"(bot_id, code, incident, occurred_at, version, level) "
            f"VALUES (%s, %s, %s, %s, %s, %s) "
            f"ON CONFLICT (bot_id, incident) DO UPDATE "
            f"SET seen = {FAMILY_SCHEMA}.problem_reports.seen + 1 "
            f"RETURNING (xmax = 0)",
            (_ledger_bot(), code, incident, occurred_at, LABEL, int(level)),
        ).fetchone()
        conn.commit()
    return bool(row and row[0])


def attach_problem_reporter(code: str, incident: str, occurred_at,
                            user_id: int, username: "str | None",
                            user_lang: "str | None", chat_kind: "str | None",
                            level: int = 2) -> bool:
    with _connect() as conn:
        row = conn.execute(
            f"INSERT INTO {FAMILY_SCHEMA}.problem_reports "
            f"(bot_id, code, incident, occurred_at, version, level, shared_at, "
            f" user_id, username, user_lang, chat_kind) "
            f"VALUES (%s, %s, %s, %s, %s, %s, now(), %s, %s, %s, %s) "
            f"ON CONFLICT (bot_id, incident) DO UPDATE "
            f"SET shared_at = now(), user_id = excluded.user_id, "
            f"    username = excluded.username, user_lang = excluded.user_lang, "
            f"    chat_kind = excluded.chat_kind "
            f"WHERE {FAMILY_SCHEMA}.problem_reports.shared_at IS NULL "
            f"RETURNING id",
            (_ledger_bot(), code, incident, occurred_at, LABEL, int(level),
             user_id, username, user_lang, chat_kind),
        ).fetchone()
        conn.commit()
    return row is not None


def recent_problem_reports(limit: int = 15, shared_only: bool = False) -> list[dict]:
    where = " WHERE shared_at IS NOT NULL" if shared_only else ""
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT reported_at, bot_id, code, incident, occurred_at, version, "
            f"seen, shared_at, user_id, username, user_lang, chat_kind, level "
            f"FROM {FAMILY_SCHEMA}.problem_reports{where} ORDER BY id DESC LIMIT %s",
            (max(1, min(limit, 100)),),
        ).fetchall()
    return [_problem_row(r) for r in rows]


def problem_report_by_incident(incident: str) -> "dict | None":
    with _connect() as conn:
        row = conn.execute(
            f"SELECT reported_at, bot_id, code, incident, occurred_at, version, "
            f"seen, shared_at, user_id, username, user_lang, chat_kind, level "
            f"FROM {FAMILY_SCHEMA}.problem_reports WHERE incident = %s "
            f"ORDER BY id DESC LIMIT 1",
            (incident,),
        ).fetchone()
    return _problem_row(row) if row is not None else None


def _problem_row(r) -> dict:
    return {"reported_at": r[0], "bot_id": r[1], "code": r[2], "incident": r[3],
            "occurred_at": r[4], "version": r[5], "seen": r[6], "shared_at": r[7],
            "user_id": r[8], "username": r[9], "user_lang": r[10], "chat_kind": r[11],
            "level": r[12]}


def recent_problem_reports_since(hours: int = 24, limit: int = 400) -> list:
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT reported_at, bot_id, code, incident, occurred_at, version, "
            f"seen, shared_at, user_id, username, user_lang, chat_kind, level "
            f"FROM {FAMILY_SCHEMA}.problem_reports "
            f"WHERE reported_at > now() - make_interval(hours => %s) "
            f"ORDER BY id DESC LIMIT %s",
            (max(1, min(hours, 168)), max(1, min(limit, 1000))),
        ).fetchall()
    return [_problem_row(r) for r in rows]


def problem_counts_since(hours: int = 24) -> dict:
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT bot_id, level, count(*), coalesce(sum(seen), 0) "
            f"FROM {FAMILY_SCHEMA}.problem_reports "
            f"WHERE reported_at > now() - make_interval(hours => %s) "
            f"GROUP BY bot_id, level",
            (max(1, min(hours, 168)),),
        ).fetchall()
    out: dict = {}
    for bot_id, level, incidents, seen in rows:
        out.setdefault(bot_id, {})[int(level)] = {"incidents": int(incidents), "seen": int(seen)}
    return out


PROBLEM_DETAIL_RETENTION_DAYS = int(os.environ.get("PROBLEM_DETAIL_RETENTION_DAYS", "30"))
PROBLEM_RETENTION_DAYS = int(os.environ.get("PROBLEM_RETENTION_DAYS", "180"))


def prune_problem_reports() -> int:
    with _connect() as conn:
        cleared = conn.execute(
            f"UPDATE {FAMILY_SCHEMA}.problem_reports "
            f"SET user_id = NULL, username = NULL, user_lang = NULL, chat_kind = NULL "
            f"WHERE shared_at IS NOT NULL AND user_id IS NOT NULL "
            f"AND shared_at < now() - make_interval(days => %s)",
            (PROBLEM_DETAIL_RETENTION_DAYS,),
        ).rowcount or 0
        removed = conn.execute(
            f"DELETE FROM {FAMILY_SCHEMA}.problem_reports "
            f"WHERE reported_at < now() - make_interval(days => %s)",
            (PROBLEM_RETENTION_DAYS,),
        ).rowcount or 0
        conn.commit()
    return cleared + removed


def forget_problem_reporter(user_id: int) -> int:
    with _connect() as conn:
        changed = conn.execute(
            f"UPDATE {FAMILY_SCHEMA}.problem_reports "
            f"SET user_id = NULL, username = NULL, user_lang = NULL, chat_kind = NULL "
            f"WHERE user_id = %s",
            (user_id,),
        ).rowcount or 0
        conn.commit()
    return changed


FEEDBACK_RETENTION_DAYS = int(os.environ.get("FEEDBACK_RETENTION_DAYS", "30"))
FEEDBACK_DELIVERY_KEY = "feedback_delivery"
FEEDBACK_INSTANT, FEEDBACK_NIGHTLY = "instant", "nightly"


def save_feedback(lang: "str | None", body: str) -> int:
    with _connect() as conn:
        row = conn.execute(
            f"INSERT INTO {FAMILY_SCHEMA}.feedback (bot_id, lang, body) "
            f"VALUES (%s, %s, %s) RETURNING id",
            (_ledger_bot(), lang, body),
        ).fetchone()
        conn.commit()
    return int(row[0])


def _feedback_row(r) -> dict:
    return {"id": r[0], "bot_id": r[1], "lang": r[2], "body": r[3],
            "created_at": r[4], "delivered_at": r[5]}


def take_undelivered_feedback(limit: int = 20) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            f"UPDATE {FAMILY_SCHEMA}.feedback SET delivered_at = now() "
            f"WHERE id IN (SELECT id FROM {FAMILY_SCHEMA}.feedback "
            f"             WHERE delivered_at IS NULL ORDER BY id LIMIT %s "
            f"             FOR UPDATE SKIP LOCKED) "
            f"RETURNING id, bot_id, lang, body, created_at, delivered_at",
            (max(1, min(limit, 200)),),
        ).fetchall()
        conn.commit()
    return sorted((_feedback_row(r) for r in rows), key=lambda row: row["id"])


def recent_feedback(limit: int = 10) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT id, bot_id, lang, body, created_at, delivered_at "
            f"FROM {FAMILY_SCHEMA}.feedback ORDER BY id DESC LIMIT %s",
            (max(1, min(limit, 50)),),
        ).fetchall()
    return [_feedback_row(r) for r in rows]


def feedback_count_since(hours: int = 24) -> int:
    with _connect() as conn:
        row = conn.execute(
            f"SELECT count(*) FROM {FAMILY_SCHEMA}.feedback "
            f"WHERE created_at > now() - make_interval(hours => %s)",
            (max(1, min(hours, 168)),),
        ).fetchone()
    return int(row[0])


def prune_feedback() -> int:
    with _connect() as conn:
        removed = conn.execute(
            f"DELETE FROM {FAMILY_SCHEMA}.feedback "
            f"WHERE created_at < now() - make_interval(days => %s)",
            (FEEDBACK_RETENTION_DAYS,),
        ).rowcount or 0
        conn.commit()
    return removed


_local_settings: dict = {}


def get_setting(key: str, default: "str | None" = None) -> "str | None":
    if not _enabled:
        return _local_settings.get(key, default)
    try:
        with _connect() as conn:
            row = conn.execute(
                f"SELECT value FROM {FAMILY_SCHEMA}.settings WHERE key = %s", (key,)).fetchone()
        return row[0] if row and row[0] is not None else default
    except Exception:
        logger.debug("Could not read family setting %s", key, exc_info=True)
        return default


def settings_with_prefix(prefix: str) -> dict:
    if not _enabled:
        return {key: value for key, value in _local_settings.items() if key.startswith(prefix)}
    escaped = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    try:
        with _connect() as conn:
            rows = conn.execute(
                f"SELECT key, value FROM {FAMILY_SCHEMA}.settings WHERE key LIKE %s",
                (escaped + "%",)).fetchall()
        return {key: value for key, value in rows}
    except Exception:
        logger.debug("Could not read family settings %s*", prefix, exc_info=True)
        return {}


def set_setting(key: str, value: str) -> None:
    if not _enabled:
        _local_settings[key] = value
        return
    try:
        with _connect() as conn:
            conn.execute(
                f"INSERT INTO {FAMILY_SCHEMA}.settings (key, value) VALUES (%s, %s) "
                f"ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value", (key, value))
            conn.commit()
    except Exception:
        logger.debug("Could not write family setting %s", key, exc_info=True)


_bus_active_until = 0.0


def mark_bus_active() -> None:
    global _bus_active_until
    _bus_active_until = time.monotonic() + BUS_ACTIVE_WINDOW_SECONDS


def bus_is_active() -> bool:
    return time.monotonic() < _bus_active_until


def _monitoring():
    for name in ("monitoring", "shared_features"):
        try:
            return importlib.import_module(name)
        except ImportError:
            continue
        except Exception:
            return None
    return None


def write_heartbeat() -> None:
    sf = _monitoring()
    errors = getattr(sf, "_error_count", 0) if sf else 0
    with _connect() as conn:
        conn.execute(
            f"""
            INSERT INTO {FAMILY_SCHEMA}.heartbeats
                (bot_id, display_name, host, version, pid, db_schema, started_at, last_seen,
                 error_count, commands, release)
            VALUES (%s, %s, %s, %s, %s, %s, %s, now(), %s, %s, %s)
            ON CONFLICT (bot_id) DO UPDATE SET
                display_name = excluded.display_name,
                host = excluded.host,
                version = excluded.version,
                release = excluded.release,
                pid = excluded.pid,
                db_schema = excluded.db_schema,
                started_at = excluded.started_at,
                last_seen = excluded.last_seen,
                error_count = excluded.error_count,
                commands = excluded.commands
            """,
            (_bot_id, _display_name, HOSTNAME, VERSION, os.getpid(),
             getattr(db, "DB_SCHEMA", "public"), _start_time, errors,
             ",".join(sorted(COMMANDS)), LABEL),
        )
        conn.commit()


USAGE_RETENTION_DAYS = int(os.environ.get("FAMILY_USAGE_RETENTION_DAYS") or 45)


def report_event(level: str, kind: str, message: str, details: str | None = None,
                 occurred_at: "datetime | None" = None) -> None:
    if not _enabled:
        return
    message, details = redact.redact(message), redact.redact(details)
    with _connect() as conn:
        conn.execute(
            f"INSERT INTO {FAMILY_SCHEMA}.events (bot_id, level, kind, message, details, occurred_at) "
            f"VALUES (%s, %s, %s, %s, %s, coalesce(%s, now()))",
            (_bot_id, level, kind, message[:4000], (details or "")[:8000] or None, occurred_at),
        )
        conn.commit()


OUTBOX_MAX = 200
_outbox: "deque[tuple]" = deque(maxlen=OUTBOX_MAX)


def keep_for_later(level: str, kind: str, message: str, details: "str | None") -> None:
    if len(_outbox) == _outbox.maxlen:
        logger.error("The event outbox is full; the oldest held event is dropped: %s", _outbox[0][2][:200])
    _outbox.append((level, kind, message, details, datetime.now(timezone.utc)))


def flush_outbox() -> int:
    sent = 0
    while _outbox:
        level, kind, message, details, occurred_at = _outbox[0]
        report_event(level, kind, message + " (held while the database was unreachable)", details,
                     occurred_at)
        _outbox.popleft()
        sent += 1
    return sent


def record_usage(window_minutes: int, rss_mb, peak_rss_mb, ceiling_mb,
                 cpu_seconds, updates: int, users: int, sleepable_seconds: int = 0,
                 max_gap_seconds: int = 0, jobs_ok: int = 0, jobs_failed: int = 0) -> None:
    if not _enabled:
        return
    with _connect() as conn:
        conn.execute(
            f"INSERT INTO {FAMILY_SCHEMA}.usage_samples "
            f"(bot_id, window_minutes, rss_mb, peak_rss_mb, ceiling_mb, cpu_seconds, "
            f"updates, users, sleepable_seconds, max_gap_seconds, jobs_ok, jobs_failed) "
            f"VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (_bot_id, window_minutes, rss_mb, peak_rss_mb, ceiling_mb, cpu_seconds,
             updates, users, sleepable_seconds, max_gap_seconds, jobs_ok, jobs_failed),
        )
        conn.commit()


def record_taps(taps: dict) -> None:
    if not _enabled or not taps:
        return
    rows = [(_bot_id, key, count) for key, count in taps.items()
            if isinstance(key, str) and isinstance(count, int) and not isinstance(count, bool) and count > 0]
    if not rows:
        return
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.executemany(
                f"INSERT INTO {FAMILY_SCHEMA}.button_taps AS t (bot_id, day, button, taps) "
                f"VALUES (%s, (now() AT TIME ZONE 'UTC')::date, %s, %s) "
                f"ON CONFLICT (bot_id, day, button) DO UPDATE SET taps = t.taps + EXCLUDED.taps",
                rows,
            )
        conn.commit()


def button_taps_history(days: int = 7) -> list[dict]:
    if not _enabled:
        return []
    with _connect() as conn:
        cur = conn.execute(
            f"SELECT bot_id, button, SUM(taps)::bigint AS taps FROM {FAMILY_SCHEMA}.button_taps "
            f"WHERE day > (now() AT TIME ZONE 'UTC')::date - %s "
            f"GROUP BY bot_id, button ORDER BY bot_id, SUM(taps) DESC, button",
            (days,),
        )
        names = [column.name for column in cur.description]
        return [dict(zip(names, row)) for row in cur.fetchall()]


STATS_RETENTION_DAYS = int(os.environ.get("FAMILY_STATS_RETENTION_DAYS") or 400)


def record_stats(stats: dict) -> None:
    if not _enabled or not stats:
        return
    rows = [(_bot_id, metric[:40], key[:60], n) for (metric, key), n in stats.items()
            if isinstance(metric, str) and isinstance(key, str) and isinstance(n, int)
            and not isinstance(n, bool) and n > 0]
    if not rows:
        return
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.executemany(
                f"INSERT INTO {FAMILY_SCHEMA}.daily_stats AS t (bot_id, day, metric, key, n) "
                f"VALUES (%s, (now() AT TIME ZONE 'UTC')::date, %s, %s, %s) "
                f"ON CONFLICT (bot_id, day, metric, key) DO UPDATE SET n = t.n + EXCLUDED.n",
                rows,
            )
        conn.commit()


def stats_history(days: int = 30) -> list[dict]:
    if not _enabled:
        return []
    with _connect() as conn:
        cur = conn.execute(
            f"SELECT bot_id, metric, key, SUM(n)::bigint AS n FROM {FAMILY_SCHEMA}.daily_stats "
            f"WHERE day > (now() AT TIME ZONE 'UTC')::date - %s "
            f"GROUP BY bot_id, metric, key ORDER BY bot_id, metric, SUM(n) DESC, key",
            (days,),
        )
        names = [column.name for column in cur.description]
        return [dict(zip(names, row)) for row in cur.fetchall()]


def usage_history(bot_id: str | None = None, hours: int = 24) -> list[dict]:
    if not _enabled:
        return []
    where = "sampled_at > now() - make_interval(hours => %s)"
    params: tuple = (hours,)
    if bot_id:
        where += " AND bot_id = %s"
        params += (bot_id,)
    with _connect() as conn:
        cur = conn.execute(
            f"SELECT bot_id, sampled_at, window_minutes, rss_mb, peak_rss_mb, "
            f"ceiling_mb, cpu_seconds, updates, users, sleepable_seconds, "
            f"max_gap_seconds, jobs_ok, jobs_failed "
            f"FROM {FAMILY_SCHEMA}.usage_samples WHERE {where} "
            f"ORDER BY sampled_at DESC LIMIT 5000",
            params,
        )
        names = [column.name for column in cur.description]
        return [dict(zip(names, row)) for row in cur.fetchall()]


def report_event_soon(level: str, kind: str, message: str, details: str | None = None,
                      keep: bool = False) -> None:
    def _run():
        try:
            report_event(level, kind, message, details)
        except Exception:
            if keep:
                logger.warning("Could not report a family event now; it is held and sent once the "
                               "database answers", exc_info=True)
                keep_for_later(level, kind, message, details)
            else:
                logger.debug("Could not report a family event", exc_info=True)

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        _run()
        return
    loop.run_in_executor(None, _run)


def _where_am_i() -> str:
    sf = _monitoring()
    detect = getattr(sf, "detect_host_environment", None) if sf else None
    if detect is not None:
        try:
            return detect()
        except Exception:
            pass
    return HOSTNAME


def ping_probe() -> dict:
    local_before = datetime.now(timezone.utc)
    started = time.perf_counter()
    with db.pooled_read() as conn:
        server_now = conn.execute("SELECT clock_timestamp()").fetchone()[0]
    elapsed_ms = (time.perf_counter() - started) * 1000
    local_after = datetime.now(timezone.utc)
    midpoint = local_before + (local_after - local_before) / 2
    return {
        "db_ms": round(elapsed_ms, 2),
        "skew_ms": round((server_now - midpoint).total_seconds() * 1000, 1),
        "host": HOSTNAME,
        "where": _where_am_i(),
        "version": LABEL,
        "commands_as_of": VERSION,
        "pid": os.getpid(),
    }


PROBE_USER_ID = 0


def user_path_probe() -> dict:
    read = getattr(db, "get_user_language", None)
    started = time.perf_counter()
    if read is not None:
        read(PROBE_USER_ID)
        what = "user_settings"
    else:
        with db.pooled_read() as conn:
            conn.execute("SELECT 1").fetchone()
        what = "SELECT 1"
    return {"info_ms": round((time.perf_counter() - started) * 1000, 2), "info_query": what}


async def _telegram_round_trip(context) -> float | None:
    bot = getattr(context, "bot", None)
    if bot is None or not hasattr(bot, "get_webhook_info"):
        return None
    started = time.perf_counter()
    await bot.get_webhook_info()
    return round((time.perf_counter() - started) * 1000, 2)


async def _cmd_ping(context, args):
    up = datetime.now(timezone.utc) - _start_time
    if not args or args[0] != "trace":
        return f"pong -- up {_format_delta(up)}", None, None
    try:
        probe = await asyncio.to_thread(ping_probe)
    except Exception as exc:
        probe = {"error": f"{type(exc).__name__}: {exc}"}
    probe["up"] = _format_delta(up)
    user: dict = {}
    try:
        user.update(await asyncio.to_thread(user_path_probe))
    except Exception as exc:
        user["info_error"] = f"{type(exc).__name__}: {exc}"
    try:
        telegram_ms = await _telegram_round_trip(context)
        if telegram_ms is not None:
            user["telegram_ms"] = telegram_ms
    except Exception as exc:
        user["telegram_error"] = f"{type(exc).__name__}: {exc}"
    if "info_ms" in user and "telegram_ms" in user:
        user["total_ms"] = round(user["info_ms"] + user["telegram_ms"], 2)
    probe["user"] = user
    return json.dumps(probe, separators=(",", ":")), None, None


async def _cmd_status(context, args):
    sf = _monitoring()
    now = datetime.now(timezone.utc)
    hour = await _active_users_since(now - timedelta(hours=1))
    since_start = await _active_users_since(_start_time)
    if sf and hasattr(sf, "build_status_text"):
        return sf.build_status_text(_start_time, hour, since_start), None, None
    return (
        f"Started: {_start_time:%Y-%m-%d %H:%M:%S UTC}\n"
        f"Active users (last hour): {hour}\n"
        f"Active users (since start): {since_start}"
    ), None, None


async def _cmd_errors(context, args):
    sf = _monitoring()
    if sf and hasattr(sf, "error_summary"):
        return sf.error_summary(), None, None
    return "No error tracking in this bot.", None, None


async def _cmd_users(context, args):
    hours = int(args[0]) if args and args[0].isdigit() else 24
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    return f"{await _active_users_since(since)} active user(s) in the last {hours}h.", None, None


async def _cmd_dbdump(context, args):
    dump = getattr(db, "dump_database_csv_zip", None)
    if dump is None:
        return "This bot has no database export.", None, None
    data = await asyncio.to_thread(dump)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M")
    return f"{len(data)/1024:.0f} KB", f"{_bot_id}_db_{stamp}.zip", data


def installed_versions() -> list[str]:
    from importlib import metadata
    seen: dict[str, str] = {}
    for dist in metadata.distributions():
        name = (dist.metadata["Name"] or "").strip()
        if name and name.lower() not in seen:
            seen[name.lower()] = f"{name}=={dist.version}"
    return [seen[key] for key in sorted(seen)]


async def _cmd_versions(context, args):
    import platform
    lines = await asyncio.to_thread(installed_versions)
    head = f"{_display_name or _bot_id} {LABEL} (commands as of {VERSION}), Python {platform.python_version()}, {len(lines)} packages"
    if args:
        wanted = args[0].lower()
        found = [line for line in lines if wanted in line.lower()]
        return f"{head}; matching '{args[0]}':\n" + ("\n".join(found) or "(none)"), None, None
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M")
    data = (f"# {head}, read {stamp} UTC\n" + "\n".join(lines) + "\n").encode()
    return head, f"{_bot_id}_versions_{stamp}.txt", data


async def _cmd_whois(context, args):
    if not args or not args[0].lstrip("-").isdigit():
        return "Usage: whois <user_id>", None, None
    user_id = int(args[0])
    lines = [f"{user_id}"]
    try:
        chat = await context.bot.get_chat(user_id)
        name = " ".join(p for p in (chat.first_name, chat.last_name) if p)
        if name:
            lines.append(f"Name: {name}")
        if chat.username:
            lines.append(f"Username: @{chat.username}")
        if chat.bio:
            lines.append(f"Bio: {chat.bio}")
    except Exception as exc:
        lines.append(f"Couldn't fetch their profile from this bot: {exc}")
        lines.append("(They may have never messaged this bot, or blocked it.)")
    return "\n".join(lines), None, None


async def _cmd_tellcredit(context, args):
    if len(args) != 3 or not all(arg.lstrip("-").isdigit() for arg in args):
        return "Usage: tellcredit <user_id> <before> <after>", None, None
    user_id, before, after = (int(arg) for arg in args)
    lang = await asyncio.to_thread(_language_of, user_id)
    try:
        await context.bot.send_message(chat_id=user_id,
                                       text=phrase("credit_updated", lang, before=before, after=after))
    except Exception as exc:
        return f"not reached ({type(exc).__name__}: {exc})", None, None
    return "told", None, None


async def _cmd_message(context, args):
    if len(args) < 2 or not args[0].lstrip("-").isdigit():
        return "Usage: message <user_id> <text>", None, None
    await context.bot.send_message(chat_id=int(args[0]), text=" ".join(args[1:]))
    return "Sent.", None, None


class _RecentLines(logging.Handler):
    def __init__(self, capacity: int, level: int, only: str | None = None):
        super().__init__(level)
        self.lines: deque = deque(maxlen=capacity)
        self.only = only

    def emit(self, record) -> None:
        if self.only and record.name != self.only:
            return
        try:
            self.lines.extend(self.format(record).splitlines())
        except Exception:
            pass

    def tail(self, wanted: int) -> list[str]:
        return list(self.lines)[-wanted:]


_RECENT_LOGS = {
    "bot.log": _RecentLines(1500, logging.INFO),
    "errors.log": _RecentLines(800, logging.WARNING),
    "problems.log": _RecentLines(500, logging.INFO, only="problems"),
}


def keep_recent_log_lines() -> None:
    root = logging.getLogger()
    fmt = next((h.formatter for h in root.handlers if h.formatter is not None), None) \
        or logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s")
    for handler in _RECENT_LOGS.values():
        if handler not in root.handlers:
            handler.setFormatter(fmt)
            root.addHandler(handler)


def _tail_lines(path: Path, wanted: int) -> list[str]:
    block = 8192
    with path.open("rb") as f:
        f.seek(0, os.SEEK_END)
        end = f.tell()
        data = b""
        while end > 0 and data.count(b"\n") <= wanted:
            step = min(block, end)
            end -= step
            f.seek(end)
            data = f.read(step) + data
    return data.decode("utf-8", errors="replace").splitlines()[-wanted:]


LOG_TAIL_DEFAULT = 40
LOG_TAIL_MAX = 500


async def _cmd_logs(context, args):
    lines_wanted = min(int(args[0]), LOG_TAIL_MAX) if args and args[0].isdigit() else LOG_TAIL_DEFAULT
    which = "errors.log"
    if args and args[-1] in ("bot", "all"):
        which = "bot.log"
    elif args and args[-1] in ("problems", "problem"):
        which = "problems.log"
    path = Path(__file__).resolve().parent / "logs" / which
    if path.exists():
        tail = await asyncio.to_thread(_tail_lines, path, lines_wanted)
        where = which
    else:
        tail = _RECENT_LOGS[which].tail(lines_wanted)
        where = f"{which} (kept in memory since this process started; no log files on this host)"
    if not tail:
        return f"{where}: nothing yet.", None, None
    return f"--- {where}, last {len(tail)} line(s) ---\n" + "\n".join(tail), None, None


async def _cmd_restart(context, args):
    if "force" not in [arg.lower() for arg in args]:
        issued = next((arg for arg in args if arg.replace(".", "", 1).isdigit()), None)
        return await lifecycle.soft_stop(1, issued), None, None

    async def _bye():
        await asyncio.sleep(2)
        logger.warning("Restarting: asked to by ManagerBot.")
        os._exit(1)

    asyncio.create_task(_bye())
    return "Restarting now (a supervisor brings it back; a hand-started process just stops).", None, None


async def _cmd_softstop(context, args):
    issued = args[0] if args else None
    return await lifecycle.soft_stop(0, issued), None, None


async def _cmd_softstopbot(context, args):
    issued = args[0] if args else None
    return await lifecycle.soft_stop_bot(issued), None, None


async def _cmd_crashtest(context, args):
    app = getattr(context, "application", None)
    if app is None:
        raise RuntimeError("Manual crashtest via ManagerBot -- error tracking works.")

    async def _boom():
        raise RuntimeError(
            f"Manual crashtest for {_bot_id} via ManagerBot -- error tracking works.")

    app.create_task(_boom())
    return ("Raised. The alert should arrive in a moment; if it does not, "
            "the crash reporting is what is broken."), None, None


def _i18n():
    try:
        return importlib.import_module("i18n")
    except ImportError:
        return None
    except Exception:
        return None


_PLAIN = {
    "update_soon_try_later": "\U0001f527 The bot is about to be updated — please try again in about {minutes} min. A message will follow once it's back.",
    "update_soon_try_later_soon": "\U0001f527 The bot is being updated — please try again shortly. A message will follow once it's back.",
    "update_will_reset": "\U0001f527 The bot is about to be updated, and the current step will be reset. It can be started again in a few minutes.",
    "update_done_try_now": "✅ Update done — try again now.",
    "gift_granted": "\U0001f381 Mumu bots passed 100 users — thanks! {amount} ⚡ of free credit has been added, "
                    "good until {date}: about {conversions} paid conversions in {convert} or big downloads "
                    "in {download}. /balance",
    "credit_updated": "\U0001f381 The balance has been updated from {before} ⚡ to {after} ⚡!",
    "gift_claimed": "\U0001f381 Gift: {amount} ⚡ of free credit — Mumu bots passed {users} users, thanks!\n"
                    "Good until {date} (UTC): about {conversions} paid conversions in {convert} or big "
                    "downloads in {download}.\n/balance",
    "gift_ad": "\U0001f389 Mumu bots have passed {users} unique users, so {amount} ⚡ of free credit is being "
               "given out! It can be claimed and used in {download} and {convert}.\n\n<i>To claim it, send /gift "
               "to either bot, or tap the button it shows. Claim it by {claim_by}; it can be spent until "
               "{spend_by}.</i>",
    "gift_offer": "\U0001f381 Mumu bots passed {users} users: {amount} ⚡ of free credit for everyone, once.\n"
                  "Claim it by {claim_by} (UTC) with the button below or /gift. It can be spent until {spend_by}.",
    "gift_claim_button": "\U0001f381 Claim {amount} ⚡",
    "gift_already_had": "\U0001f381 This gift has already been claimed: one per person. /balance",
    "gift_not_open": "\U0001f381 No gift can be claimed right now.",
}


def phrase(key: str, lang: str | None = None, **kwargs) -> str:
    i18n = _i18n()
    if i18n is not None:
        try:
            text = i18n.t(lang or "en", key, **kwargs)
            if text and text != key:
                return text
        except Exception:
            pass
    return _PLAIN.get(key, key).format(**kwargs)


def _language_of(user_id: int) -> str | None:
    fn = getattr(db, "get_user_language", None)
    if fn is None:
        return None
    try:
        return fn(user_id)
    except Exception:
        return None


BROADCAST_ACTIVE_DAYS = int(os.environ.get("FAMILY_BROADCAST_ACTIVE_DAYS", "7"))


def _everyone() -> list[int]:
    fn = getattr(db, "list_all_users", None)
    if fn is None:
        return []
    try:
        return fn()
    except Exception:
        logger.exception("Could not read this bot's user list")
        return []


def _recently_active(days: int) -> list[int] | None:
    fn = getattr(db, "active_user_ids_since", None)
    if fn is None:
        return None
    try:
        return fn(datetime.now(timezone.utc) - timedelta(days=days))
    except Exception:
        logger.exception("Could not read this bot's active users")
        return None


BROADCAST_PER_SECOND = float(os.environ.get("FAMILY_BROADCAST_PER_SECOND", "20"))


async def _send_to_each(context, targets, text_for) -> tuple[int, int]:
    delivered = skipped = 0
    delay = 1.0 / BROADCAST_PER_SECOND if BROADCAST_PER_SECOND > 0 else 0
    for user_id, chat_id in targets:
        text = text_for(user_id)
        if not text:
            skipped += 1
            continue
        try:
            await context.bot.send_message(chat_id=chat_id, text=text)
            delivered += 1
        except Exception as exc:
            skipped += 1
            logger.debug("Broadcast skipped %s: %s", chat_id, exc)
        if delay:
            await asyncio.sleep(delay)
    return delivered, skipped


async def _cmd_broadcast(context, args):
    aimed = bool(args) and args[0] == "--active"
    if aimed:
        args = args[1:]
    text = " ".join(args).strip()
    if not text:
        return "Usage: broadcast [--active] <text>", None, None

    if aimed:
        users = await asyncio.to_thread(_recently_active, BROADCAST_ACTIVE_DAYS)
        if users is None:
            return ("This bot cannot tell who is active, so --active would have "
                    "reached nobody. Nothing was sent."), None, None
        who = f"active in the last {BROADCAST_ACTIVE_DAYS} day(s)"
    else:
        users = await asyncio.to_thread(_everyone)
        who = "everyone this bot knows"

    if not users:
        return f"Nobody to broadcast to ({who}).", None, None
    delivered, skipped = await _send_to_each(
        context, ((uid, uid) for uid in users), lambda _uid: text,
    )
    return (
        f"Broadcast to {delivered} of {len(users)} user(s), {who}"
        + (f"; {skipped} unreachable." if skipped else "."),
        None, None,
    )


GIFT_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,39}$")
GIFT_KEY_RULE = "The key is 3-40 lowercase letters, digits or dashes, starting with a letter or digit."
GIFT_MAX_DAYS = 400
_GIFT_NOT_PEOPLE = {1087968824, 136817688, 777000}
GIFT_CREDITS_PER_CONVERSION = 10


def _gift_last_day(text: str) -> "datetime | None":
    try:
        day = datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return day + timedelta(days=1)


def _gift_args(args: list[str]) -> "tuple[str, int, datetime, str] | str":
    if len(args) != 3:
        return "Usage: gift <key> <credits> <last day YYYY-MM-DD> [--go]"
    key, raw_amount, raw_day = args
    if not GIFT_KEY_RE.fullmatch(key):
        return GIFT_KEY_RULE
    if key == PROMO_KEY:
        return "That key belongs to the 220 ⚡ gift (/promo). Choose another."
    amount = int(raw_amount) if re.fullmatch(r"[0-9]{1,6}", raw_amount) else 0
    if not 0 < amount <= GIFT_MAX_CREDIT:
        return f"The amount is a whole number of credits, 1 to {GIFT_MAX_CREDIT}."
    expires = _gift_last_day(raw_day)
    now = datetime.now(timezone.utc)
    if expires is None or not now + timedelta(days=1) <= expires <= now + timedelta(days=GIFT_MAX_DAYS + 1):
        return f"The last day is a date, YYYY-MM-DD, from tomorrow (UTC) to {GIFT_MAX_DAYS} days ahead."
    return key, amount, expires, raw_day


def _gift_already_given(key: str) -> set:
    prefix = f"stars:granted:{key}:"
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT key FROM {FAMILY_SCHEMA}.settings WHERE key LIKE %s", (prefix + "%",)
        ).fetchall()
    given = set()
    for (claim,) in rows:
        tail = claim[len(prefix):]
        if tail.lstrip("-").isdigit():
            given.add(int(tail))
    return given


def _bot_handle(bot_id: str, fallback: str) -> str:
    for entry in os.environ.get("SIBLING_BOTS", "").split(","):
        parts = entry.strip().split(":")
        if len(parts) == 3 and parts[0] == bot_id and parts[2].strip().lstrip("@"):
            return "@" + parts[2].strip().lstrip("@")
    return fallback


def gift_notice(lang: "str | None", amount: int, expires_at: datetime) -> str:
    last_day = expires_at - timedelta(days=1)
    return phrase(
        "gift_granted", lang, amount=amount, date=last_day.strftime("%d.%m.%Y"),
        conversions=max(1, amount // GIFT_CREDITS_PER_CONVERSION),
        convert=_bot_handle("convertbot", "ConvertBot"),
        download=_bot_handle("downloaderbot", "DownloaderBot"),
    )


async def _cmd_gift(context, args):
    go = "--go" in args
    parsed = _gift_args([a for a in args if a != "--go"])
    if isinstance(parsed, str):
        return parsed, None, None
    key, amount, expires, raw_day = parsed
    fn = getattr(db, "list_all_users", None)
    if fn is None:
        return "This bot keeps no list of its users, so it has nobody to give to.", None, None
    try:
        users = [uid for uid in await asyncio.to_thread(fn)
                 if isinstance(uid, int) and uid > 0 and uid not in _GIFT_NOT_PEOPLE]
    except Exception as exc:
        return f"Could not read this bot's users ({type(exc).__name__}); nothing was given.", None, None
    users = list(dict.fromkeys(users))
    given = await asyncio.to_thread(_gift_already_given, key)
    due = [uid for uid in users if uid not in given]
    head = (f"Gift '{key}': {amount} ⚡ of bonus credit each, good until {raw_day} "
            f"(the end of that day, UTC).")

    if not go:
        return (
            f"{head}\nThis bot knows {len(users)} people; {len(due)} would get it now "
            f"({len(users) - len(due)} already have it, from this bot or another), "
            f"{len(due) * amount} ⚡ in all.\nNothing was given: this was only the count.\n\n"
            f"What each of them is told (in their own language; English here):\n"
            f"{gift_notice('en', amount, expires)}",
            None, None,
        )

    granted = already = failed = told = unreachable = 0
    delay = 1.0 / BROADCAST_PER_SECOND if BROADCAST_PER_SECOND > 0 else 0
    for uid in due:
        try:
            balance = await asyncio.to_thread(grant_bonus_once, uid, amount, key, expires)
        except Exception as exc:
            failed += 1
            logger.warning("Gift %s: could not give it to one person (%s)", key, type(exc).__name__)
            continue
        if balance is None:
            already += 1
            continue
        granted += 1
        try:
            lang = await asyncio.to_thread(_language_of, uid)
            await context.bot.send_message(chat_id=uid, text=gift_notice(lang, amount, expires))
            told += 1
        except Exception as exc:
            unreachable += 1
            logger.debug("Gift %s: a notice could not be delivered (%s)", key, type(exc).__name__)
        if delay:
            await asyncio.sleep(delay)
    out = (f"{head}\nGave it to {granted} people ({granted * amount} ⚡) and told {told} of them"
           + (f"; {unreachable} could not be reached but have the credit" if unreachable else "")
           + f". {len(users) - len(due) + already} already had it.")
    if failed:
        out += f"\n{failed} failed (database errors, see the log); run the same command again to finish them."
    return out, None, None


PROMO_KEY = "gift-220-users"
PROMO_USERS = 200
PROMO_CREDITS = 220
PROMO_CLAIM_DAYS = 3
PROMO_SPEND_DAYS = 28
PROMO_STARTED = f"promo:{PROMO_KEY}:started"
PROMO_STOPPED = f"promo:{PROMO_KEY}:stopped"
PROMO_MILESTONE = f"promo:{PROMO_KEY}:milestone"
PROMO_REFRESH_SECONDS = 60
PROMO_GIVEN, PROMO_HAD, PROMO_CLOSED = "given", "had", "closed"

_promo_cache: dict = {"at": None, "started": None, "stopped": None}


def _end_of_day(day) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=timezone.utc) + timedelta(days=1)


def _moment(text: "str | None") -> "datetime | None":
    if not text:
        return None
    try:
        when = datetime.fromisoformat(text)
    except ValueError:
        return None
    return when.astimezone(timezone.utc) if when.tzinfo is not None else None


def _claim_setting(key: str, value: str) -> bool:
    with _connect() as conn:
        won = conn.execute(
            f"INSERT INTO {FAMILY_SCHEMA}.settings (key, value) VALUES (%s, %s) "
            f"ON CONFLICT (key) DO NOTHING RETURNING key",
            (key, value),
        ).fetchone()
        conn.commit()
    return bool(won)


def _read_promo(fresh: bool = False) -> tuple:
    at = _promo_cache["at"]
    if not fresh and at is not None and time.monotonic() - at < PROMO_REFRESH_SECONDS:
        return _promo_cache["started"], _promo_cache["stopped"]
    try:
        with _connect() as conn:
            values = dict(conn.execute(
                f"SELECT key, value FROM {FAMILY_SCHEMA}.settings WHERE key IN (%s, %s)",
                (PROMO_STARTED, PROMO_STOPPED),
            ).fetchall())
    except Exception:
        if fresh:
            raise
        logger.debug("Could not read the gift's state; closed until the next try", exc_info=True)
        _promo_cache.update(at=time.monotonic(), started=None, stopped=None)
        return None, None
    started, stopped = _moment(values.get(PROMO_STARTED)), _moment(values.get(PROMO_STOPPED))
    _promo_cache.update(at=time.monotonic(), started=started, stopped=stopped)
    return started, stopped


def promo_closes_for(started: datetime) -> datetime:
    return _end_of_day((started.astimezone(timezone.utc) + timedelta(days=PROMO_CLAIM_DAYS)).date())


def promo_window(fresh: bool = False) -> "tuple[datetime, datetime] | None":
    started, stopped = _read_promo(fresh)
    if started is None:
        return None
    closes = promo_closes_for(started)
    if stopped is not None and stopped < closes:
        closes = stopped
    return started, closes


def promo_open(now: "datetime | None" = None, fresh: bool = False) -> bool:
    window = promo_window(fresh)
    if window is None:
        return False
    now = now or datetime.now(timezone.utc)
    return window[0] <= now < window[1]


PROMO_SPEND_UNTIL = datetime(2026, 12, 2, tzinfo=timezone.utc)


def promo_expiry(claimed_at: datetime) -> datetime:
    return PROMO_SPEND_UNTIL


def promo_spend_by() -> str:
    return (PROMO_SPEND_UNTIL - timedelta(microseconds=1)).strftime("%d.%m.%Y")


def claim_promo_gift(user_id: int) -> "tuple[str, datetime | None]":
    if not (isinstance(user_id, int) and user_id > 0) or user_id in _GIFT_NOT_PEOPLE:
        return PROMO_CLOSED, None
    now = datetime.now(timezone.utc)
    if not promo_open(now, fresh=True):
        return PROMO_CLOSED, None
    expires = promo_expiry(now)
    if grant_bonus_once(user_id, PROMO_CREDITS, PROMO_KEY, expires) is None:
        return PROMO_HAD, None
    return PROMO_GIVEN, expires


def promo_given_to(user_id: int) -> bool:
    claim = f"stars:granted:{PROMO_KEY}:{user_id}"
    with _connect() as conn:
        return conn.execute(
            f"SELECT 1 FROM {FAMILY_SCHEMA}.settings WHERE key = %s", (claim,)
        ).fetchone() is not None


def promo_claim_count() -> int:
    return len(_gift_already_given(PROMO_KEY))


def _refresh_promo_after_write() -> None:
    try:
        _read_promo(fresh=True)
    except Exception:
        _promo_cache["at"] = None
        logger.debug("The gift's state changed; reading it back failed", exc_info=True)


def start_promo() -> "tuple[datetime, bool]":
    now = datetime.now(timezone.utc)
    if _claim_setting(PROMO_STARTED, now.isoformat()):
        _refresh_promo_after_write()
        return now, True
    started, _ = _read_promo(fresh=True)
    return started, False


def stop_promo() -> bool:
    if not promo_open(fresh=True):
        return False
    won = _claim_setting(PROMO_STOPPED, datetime.now(timezone.utc).isoformat())
    _refresh_promo_after_write()
    return won


def note_promo_milestone() -> bool:
    return _claim_setting(PROMO_MILESTONE, datetime.now(timezone.utc).isoformat())


def promo_milestone_at() -> "datetime | None":
    with _connect() as conn:
        row = conn.execute(
            f"SELECT value FROM {FAMILY_SCHEMA}.settings WHERE key = %s", (PROMO_MILESTONE,)
        ).fetchone()
    return _moment(row[0]) if row else None


def _promo_bots() -> dict:
    return {"convert": _bot_handle("convertbot", "ConvertBot"),
            "download": _bot_handle("downloaderbot", "DownloaderBot")}


def promo_notice(lang: "str | None", expires_at: datetime) -> str:
    return phrase(
        "gift_claimed", lang, amount=PROMO_CREDITS, users=PROMO_USERS,
        date=(expires_at - timedelta(days=1)).strftime("%d.%m.%Y"),
        conversions=max(1, PROMO_CREDITS // GIFT_CREDITS_PER_CONVERSION), **_promo_bots(),
    )


def promo_offer(lang: "str | None", closes: datetime) -> str:
    return phrase(
        "gift_offer", lang, amount=PROMO_CREDITS, users=PROMO_USERS, spend_by=promo_spend_by(),
        claim_by=(closes - timedelta(microseconds=1)).strftime("%d.%m.%Y"),
    )


def promo_ad(lang: "str | None", closes: datetime) -> str:
    return phrase(
        "gift_ad", lang, amount=PROMO_CREDITS, users=PROMO_USERS, spend_by=promo_spend_by(),
        claim_by=(closes - timedelta(microseconds=1)).strftime("%d.%m.%Y"), **_promo_bots(),
    )


async def _cmd_pause(context, args):
    minutes = int(args[0]) if args and args[0].isdigit() else lifecycle.DEFAULT_MAINTENANCE_MINUTES
    until = await asyncio.to_thread(lifecycle.begin_maintenance, _bot_id, minutes)
    return (
        f"Paused. New long work is declined until `resume`; "
        f"users are being told to come back in about {minutes} minute(s) "
        f"(around {until.strftime('%H:%M')} UTC).",
        None, None,
    )


async def _cmd_warnbusy(context, args):
    waiting = lifecycle.in_flight()
    if not waiting:
        return "Nobody is mid-anything right now -- nothing to warn about.", None, None
    seen = set()
    targets = []
    for chat_id, _ in waiting:
        if chat_id not in seen:
            seen.add(chat_id)
            targets.append((chat_id, chat_id))
    delivered, skipped = await _send_to_each(
        context, targets,
        lambda uid: phrase("update_will_reset", _language_of(uid)),
    )
    return (
        f"Warned {delivered} user(s) with work in flight"
        + (f"; {skipped} unreachable." if skipped else "."),
        None, None,
    )


async def _cmd_resume(context, args):
    await asyncio.to_thread(lifecycle.end_maintenance, _bot_id)
    held = await asyncio.to_thread(lifecycle.take_held, _bot_id)
    if not held:
        return "Open again. Nobody had been turned away.", None, None
    delivered, skipped = await _send_to_each(
        context, held,
        lambda uid: phrase("update_done_try_now", _language_of(uid)),
    )
    return (
        f"Open again. Told {delivered} of {len(held)} user(s) who had been "
        f"turned away" + (f"; {skipped} unreachable." if skipped else "."),
        None, None,
    )


COMMANDS = {
    "ping": _cmd_ping,
    "status": _cmd_status,
    "errors": _cmd_errors,
    "users": _cmd_users,
    "dbdump": _cmd_dbdump,
    "versions": _cmd_versions,
    "whois": _cmd_whois,
    "message": _cmd_message,
    "logs": _cmd_logs,
    "restart": _cmd_restart,
    "softstop": _cmd_softstop,
    "softstopbot": _cmd_softstopbot,
    "crashtest": _cmd_crashtest,
    "broadcast": _cmd_broadcast,
    "pause": _cmd_pause,
    "warnbusy": _cmd_warnbusy,
    "resume": _cmd_resume,
    "gift": _cmd_gift,
    "tellcredit": _cmd_tellcredit,
}

COMMAND_HELP = {
    "ping": "is it alive, and for how long",
    "status": "uptime, host, crash count, active users",
    "errors": "errors since that bot last started",
    "users": "active users -- users [hours], default 24",
    "dbdump": "that bot's own tables as a zip of CSVs",
    "versions": "versions [name] -- the installed packages as a requirements file, or the lines matching name",
    "whois": "whois <user_id> -- look a user up through that bot",
    "message": "message <user_id> <text> -- DM someone as that bot",
    "logs": "logs [n] [bot|problems] -- tail errors.log, bot.log with 'bot', problems.log with 'problems'",
    "restart": "restart that bot's process (its whole service) once nobody is using it; 'restart force' restarts that bot alone, at once",
    "softstop": "stop that bot's process (its whole service) and stay down, only if nobody is using it",
    "softstopbot": "stop only that bot, if nobody is using it; the rest of its service carries on",
    "crashtest": "raise on purpose, to check the crash alert still works",
    "broadcast": "broadcast [--active] <text> -- one message as that bot; --active aims it at recent users only",
    "pause": "pause [minutes] -- decline new long work and say why",
    "warnbusy": "tell whoever is mid-something that it is about to be reset",
    "resume": "reopen, and tell everyone who was turned away",
    "gift": "gift <key> <credits> <last day YYYY-MM-DD> [--go] -- bonus credit for everyone, once each; counts unless --go",
    "tellcredit": "tellcredit <user_id> <before> <after> -- tell one person, as that bot, that the balance changed",
}


async def _active_users_since(since) -> int:
    fn = getattr(db, "count_active_users_since", None)
    if fn is None:
        return 0
    try:
        return await asyncio.to_thread(fn, since)
    except Exception:
        return 0


def _format_delta(delta: timedelta) -> str:
    days, rem = divmod(int(delta.total_seconds()), 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)
    if days:
        return f"{days}d {hours}h {minutes}m"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def _claim_next_command() -> tuple[int, str, str] | None:
    with _connect() as conn:
        cur = conn.execute(
            f"""
            UPDATE {FAMILY_SCHEMA}.commands SET status = 'running', claimed_at = now()
            WHERE id = (
                SELECT id FROM {FAMILY_SCHEMA}.commands
                WHERE target_bot = %s AND status = 'pending'
                ORDER BY id LIMIT 1 FOR UPDATE SKIP LOCKED
            )
            RETURNING id, command, args
            """,
            (_bot_id,),
        )
        row = cur.fetchone()
        conn.commit()
        return row


def _finish_command(command_id: int, ok: bool, output: str, file_name, file_bytes) -> None:
    with _connect() as conn:
        conn.execute(
            f"""
            UPDATE {FAMILY_SCHEMA}.commands
            SET status = %s, ok = %s, output = %s, file_name = %s, file_bytes = %s, finished_at = now()
            WHERE id = %s
            """,
            ("done" if ok else "failed", ok, output[:60000], file_name, file_bytes, command_id),
        )
        conn.commit()


_STOP_COMMANDS = frozenset({"softstop", "softstopbot", "restart"})
_running_here: dict = {}


def running_here() -> int:
    return sum(1 for command in _running_here.values() if command not in _STOP_COMMANDS)


async def _run_one_command(context, row) -> None:
    command_id, command, raw_args = row
    handler = COMMANDS.get(command)
    logger.info("ManagerBot asked for: %s %s", command, raw_args)
    _running_here[command_id] = command
    try:
        if handler is None:
            ok, output, name, data = False, f"Unknown command '{command}'.", None, None
        else:
            output, name, data = await handler(context, raw_args.split())
            ok = True
    except Exception as exc:
        logger.exception("Family command %r failed", command)
        ok, output, name, data = False, f"{type(exc).__name__}: {exc}", None, None
    finally:
        _running_here.pop(command_id, None)

    try:
        await asyncio.to_thread(_finish_command, command_id, ok, output, name, data)
    except Exception:
        logger.exception("Could not write the result of family command %s back", command_id)


MAX_COMMANDS_PER_PASS = int(os.environ.get("FAMILY_MAX_COMMANDS_PER_PASS", "10"))


async def _poll_commands(context) -> None:
    ran = 0
    for _ in range(MAX_COMMANDS_PER_PASS):
        try:
            row = await asyncio.to_thread(_claim_next_command)
        except Exception:
            logger.debug("Family command poll failed (database unreachable?)", exc_info=True)
            break
        if not row:
            break
        await _run_one_command(context, row)
        ran += 1
    if ran:
        mark_bus_active()


_last_bus_poll_at = 0.0


async def _bus_tick(context) -> None:
    global _last_bus_poll_at
    due = BUS_POLL_ACTIVE_SECONDS if bus_is_active() else BUS_POLL_IDLE_SECONDS
    now = time.monotonic()
    if now - _last_bus_poll_at < due:
        return
    _last_bus_poll_at = now
    await _poll_commands(context)


async def _send_heartbeat(context) -> None:
    try:
        importlib.import_module("resources").note_memory()
    except Exception:
        logger.debug("Could not read memory", exc_info=True)
    try:
        await asyncio.to_thread(write_heartbeat)
    except Exception:
        logger.debug("Heartbeat failed (database unreachable?)", exc_info=True)
        return
    if _outbox:
        try:
            sent = await asyncio.to_thread(flush_outbox)
            logger.info("Sent %d event(s) held while the database was unreachable.", sent)
        except Exception:
            logger.debug("Held events could not be sent yet", exc_info=True)


def _prune_family_rows() -> tuple[int, int, int, int]:
    with _connect() as conn:
        cur = conn.execute(
            f"DELETE FROM {FAMILY_SCHEMA}.commands "
            f"WHERE target_bot = %s AND delivered = TRUE "
            f"AND finished_at < now() - make_interval(hours => %s)",
            (_bot_id, COMMAND_RETENTION_HOURS),
        )
        commands = cur.rowcount
        cur = conn.execute(
            f"DELETE FROM {FAMILY_SCHEMA}.events "
            f"WHERE bot_id = %s AND notified = TRUE "
            f"AND occurred_at < now() - make_interval(days => %s)",
            (_bot_id, EVENT_RETENTION_DAYS),
        )
        events = cur.rowcount
        cur = conn.execute(
            f"DELETE FROM {FAMILY_SCHEMA}.usage_samples "
            f"WHERE bot_id = %s AND sampled_at < now() - make_interval(days => %s)",
            (_bot_id, USAGE_RETENTION_DAYS),
        )
        samples = cur.rowcount
        cur = conn.execute(
            f"DELETE FROM {FAMILY_SCHEMA}.button_taps "
            f"WHERE bot_id = %s AND day < (now() AT TIME ZONE 'UTC')::date - %s",
            (_bot_id, USAGE_RETENTION_DAYS),
        )
        taps = cur.rowcount
        conn.execute(
            f"DELETE FROM {FAMILY_SCHEMA}.daily_stats "
            f"WHERE bot_id = %s AND day < (now() AT TIME ZONE 'UTC')::date - %s",
            (_bot_id, STATS_RETENTION_DAYS),
        )
        conn.commit()
    return commands, events, samples, taps


def _prune() -> str:
    commands, events, samples, taps = _prune_family_rows()
    parts = [f"{commands} command(s)", f"{events} event(s)", f"{samples} usage sample(s)",
             f"{taps} button tap row(s)"]
    try:
        parts.append(f"{expire_bonus_credit()} expired bonus credit")
    except Exception:
        logger.debug("Could not expire bonus credit", exc_info=True)
    try:
        parts.append(f"{prune_problem_reports()} problem row(s)")
    except Exception:
        logger.debug("Could not prune problem reports", exc_info=True)
    try:
        parts.append(f"{prune_feedback()} feedback row(s)")
    except Exception:
        logger.debug("Could not prune feedback", exc_info=True)
    try:
        parts.append(f"{lifecycle.prune_waitlist()} stale waitlist row(s)")
    except Exception:
        logger.debug("Could not prune the update waitlist", exc_info=True)
    own = getattr(db, "prune_old_data", None)
    if own is not None:
        parts.append(f"{own()} activity row(s)")
    return ", ".join(parts)


async def _housekeeping(context) -> None:
    try:
        removed = await asyncio.to_thread(_prune)
    except Exception:
        logger.debug("Housekeeping pass failed (database unreachable?)", exc_info=True)
        return
    logger.info("Housekeeping: pruned %s.", removed)


_GUARD_RETRY_SLEEPS = (2, 4, 8, 16, 30, 30)
_sleep = time.sleep

_key_state: "dict | None" = None
KEY_RECHECK_SECONDS = int(os.environ.get("FAMILY_KEY_RECHECK_SECONDS") or 60)


def _unchecked(problem: str) -> dict:
    return {"usable": False, "sealed": frozenset(), "problem": problem}


def check_data_key(fresh: bool = False) -> dict:
    global _key_state
    if _key_state is not None and not fresh:
        return _key_state
    attempts = len(_GUARD_RETRY_SLEEPS) + 1
    state = None
    for attempt in range(1, attempts + 1):
        try:
            init_family_schema()
            with _connect() as conn:
                state = inspect_data_key(conn)
            break
        except psycopg.OperationalError as exc:
            if attempt == attempts or fresh:
                state = _unchecked("The data key could not be checked: the database is not answering")
                break
            wait = _GUARD_RETRY_SLEEPS[attempt - 1]
            logger.warning("The data key could not be checked yet (%s); trying again in %ss "
                           "(attempt %s of %s).", type(exc).__name__, wait, attempt, attempts)
            _sleep(wait)
        except Exception as exc:
            state = _unchecked(f"The data key could not be checked against the database "
                               f"({type(exc).__name__})")
            break
    if state["usable"]:
        logger.info("Data key checked: it is the key this database is sealed with.")
        unsealed = [stage for stage in SEAL_STAGES if stage not in state["sealed"]]
        if unsealed and (_key_state is None or _key_state.get("sealed") != state["sealed"]):
            logger.warning("Personal data is not sealed yet for stage(s) %s: run "
                           "scripts/security/encrypt_personal_data.py when the bots can be stopped. "
                           "New rows are sealed already.", ", ".join(unsealed))
    elif _key_state is None or _key_state.get("problem") != state["problem"]:
        logger.critical("%s. What needs the data key is switched off until it is put right.",
                        state["problem"])
    _key_state = state
    return state


def vault_usable() -> bool:
    return check_data_key()["usable"]


def guard_data_key(stages=SEAL_STAGES) -> None:
    problem = key_problem(check_data_key(), stages)
    if problem:
        _refuse(problem)


async def _recheck_key(context) -> None:
    state = await asyncio.to_thread(check_data_key, True)
    if state["usable"] and not key_problem(state, context.job.data or ()):
        if lifecycle.in_flight():
            logger.info("The data key checks out now; restarting once the work in flight is done.")
            return
        logger.warning("The data key checks out now; restarting to switch on what needs it.")
        context.job.schedule_removal()
        os._exit(1)


def watch_data_key(app, stages=()) -> None:
    if app.job_queue is not None and key_problem(check_data_key(), stages):
        app.job_queue.run_repeating(_recheck_key, interval=KEY_RECHECK_SECONDS, first=KEY_RECHECK_SECONDS,
                                    data=tuple(stages))


def attach(app, bot_id: str, display_name: str, start_time: datetime, needs=()) -> None:
    global _bot_id, _display_name, _start_time, _enabled

    keep_recent_log_lines()

    state = check_data_key()
    problem = key_problem(state, needs)
    watch_data_key(app, needs)

    if os.environ.get("FAMILY_BUS", "on").lower() in ("off", "0", "false", "no"):
        logger.info("Family bus disabled (FAMILY_BUS=off) -- running standalone.")
        return

    bot_id = os.environ.get("FAMILY_BOT_ID") or bot_id
    display_name = os.environ.get("FAMILY_LABEL") or display_name
    _bot_id, _display_name, _start_time = bot_id, display_name, start_time

    try:
        write_heartbeat()
    except Exception as exc:
        logger.warning(
            "Family bus unavailable for now (%s) -- this bot runs fine without it. The heartbeat "
            "keeps trying; ManagerBot reports it as down until the shared database answers.", exc,
        )

    _enabled = True
    lifecycle.add_work_source("family command(s)", running_here)
    if problem:
        report_event_soon("critical", "data_key", f"{display_name}: {problem}. It runs, with what "
                          f"needs the key switched off.", keep=True)

    sf = _monitoring()
    if sf and hasattr(sf, "set_event_hook"):
        sf.set_event_hook(report_event_soon)

    report_event_soon("info", "startup", f"{display_name} started on {HOSTNAME}.")

    if app.job_queue is None:
        logger.warning("No job queue -- install python-telegram-bot[job-queue]. Family bus is off.")
        _enabled = False
        return

    app.job_queue.run_repeating(_send_heartbeat, interval=HEARTBEAT_SECONDS, first=HEARTBEAT_SECONDS)

    app.job_queue.run_repeating(
        _bus_tick, interval=BUS_POLL_ACTIVE_SECONDS, first=BUS_POLL_ACTIVE_SECONDS
    )
    mark_bus_active()

    app.job_queue.run_repeating(_housekeeping, interval=HOUSEKEEPING_SECONDS, first=60)
    logger.info(
        "Family bus on: heartbeat every %ss, commands polled (%ss busy / %ss idle), "
        "tidy-up every %ss.",
        HEARTBEAT_SECONDS, BUS_POLL_ACTIVE_SECONDS, BUS_POLL_IDLE_SECONDS, HOUSEKEEPING_SECONDS,
    )
