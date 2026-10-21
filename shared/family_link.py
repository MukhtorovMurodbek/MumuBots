"""The family bus: how one bot talks to ManagerBot, and how ManagerBot talks
back.

Every bot in the family (the four public ones and ManagerBot itself) keeps
this file, byte-identical, exactly like shared_features.py -- the bots stay
independent processes with independent repos, so nothing is imported across
folders. What they *do* share is one Postgres database, and this module is
the only thing that touches the parts of it that aren't a single bot's own.

Layout of that shared database:

    family.*        this file's tables -- heartbeats, events, command queue
    sticker_bot.*   StickerBot's own tables (its db.py, unchanged)
    convert_bot.*   ConvertBot's own tables
    downloader_bot.*
    anon_bot.*
    manager_bot.*    ManagerBot's own tables

One Postgres schema per bot means the four bots' identically-named tables
(user_settings, star_transactions, activity_events, ...) never collide, and
no bot's SQL had to change -- each connects with its own search_path (see
db.py's DB_SCHEMA). ManagerBot is the only process that reads across schemas.

Three things flow over this bus:

  1. **Heartbeats** -- every HEARTBEAT_SECONDS each bot stamps
     family.heartbeats with "still alive, started at X, N errors so far".
     ManagerBot decides a bot is down when that stamp goes stale, which
     works whether the bot crashed, was OOM-killed, lost its network, or
     was never started at all. No open ports, no HTTP between services.

  2. **Events** -- anything ManagerBot should tell the owner about lands in
     family.events (an unhandled exception, a startup, a donation).
     ManagerBot polls for undelivered ones and forwards them as a DM.

  3. **Commands** -- ManagerBot inserts a row in family.commands aimed at
     one bot; that bot polls for it (fast while the bus is busy, every
     FAMILY_BUS_POLL_IDLE_SECONDS otherwise), runs it, and writes the answer
     back into the same row. This is how ManagerBot
     runs another bot's owner-only commands (/status, /dbdump, /whois,
     /messageas, ...) without either process needing to reach the other
     over the network. A command aimed at a bot that is down simply stays
     pending until ManagerBot times it out and says so.

Every bot also tidies up after itself here, on a slow timer: its own
finished rows in family.commands (which carry file payloads -- a delivered
/dbdump zip is megabytes of BYTEA nobody will read again), its own delivered
events, and its own activity log. Each bot only ever deletes rows keyed to
itself, so the five processes need no coordination to do it.

Everything here is best-effort by design: if the shared database is
unreachable, the family bus goes quiet but the bot itself keeps serving its
users normally. A monitoring layer must never be able to take down the
thing it monitors.
"""
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

import db
import lifecycle

logger = logging.getLogger(__name__)

FAMILY_SCHEMA = "family"

# Bumped with the family's version (see CHANGELOG.md) -- reported in
# heartbeats so /status can show which bots are running stale code after a
# partial deploy.
VERSION = os.environ.get("FAMILY_VERSION", "2.2.0")

# Before 1.7.0 a row in family.problem_reports only existed because somebody
# tapped Report, so every row older than this is one of those and is marked
# as shared. After it, most rows are automatic and carry nothing personal.
_REPORTS_BECAME_AUTOMATIC = datetime(2026, 9, 13, tzinfo=timezone.utc)

HEARTBEAT_SECONDS = int(os.environ.get("FAMILY_HEARTBEAT_SECONDS", "30"))

# ---------------------------------------------------------------------------
# How a bot finds out there is a command waiting for it
# ---------------------------------------------------------------------------
# It polls family.commands for its own pending rows. There is no push.
#
# v1.0.1 added a LISTEN/NOTIFY layer on top of the poll: a dedicated
# psycopg.AsyncConnection per channel, an asyncio task holding it open, its
# own reconnect/backoff. It was meant to turn "picked up within the poll
# interval" into "picked up in milliseconds". In practice it earned its
# reputation as the most fragile code in the family: the job that started it
# was silently misfire-dropped and never ran on any bot for two months
# (v1.1.2), and psycopg's async connection cannot run on Windows' default
# event loop at all, so it never worked on the laptop the bots are developed
# on. Both times the poll underneath carried the whole bus and nobody
# noticed. For a bus with one human operator typing /ping now and then, a
# held connection and 200 lines of reconnect logic bought a few seconds on a
# manual command. v1.1.4 deleted it.
#
# What is left is one poll, made adaptive: fast right after the bus was last
# busy, slow when it has been quiet. mark_bus_active() opens a short fast
# window; anything that expects bus traffic (a command just queued, claimed
# or finished) calls it. The result: a warm bus answers in about a second,
# and a cold one within FAMILY_BUS_POLL_IDLE_SECONDS. It behaves identically
# on a Windows laptop and on Railway, and it has no failure mode short of the
# database itself being unreachable -- which /status shows anyway.
BUS_POLL_ACTIVE_SECONDS = float(os.environ.get("FAMILY_BUS_POLL_ACTIVE_SECONDS", "1"))
BUS_POLL_IDLE_SECONDS = float(os.environ.get("FAMILY_BUS_POLL_IDLE_SECONDS", "10"))
# How long the fast cadence lasts after the last time the bus did something.
BUS_ACTIVE_WINDOW_SECONDS = float(os.environ.get("FAMILY_BUS_ACTIVE_WINDOW_SECONDS", "20"))

# Pass to every job_queue.run_once(..., when=0) in the family.
#
# APScheduler drops a job whose run time has already passed by more than
# misfire_grace_time, which defaults to one second, and python-telegram-bot
# does not override it. A bot's main() stamps "now" before run_polling()
# starts the scheduler -- and between those two moments sits post_init: an
# advisory lock, the waitlist table, the maintenance flag and set_my_commands,
# each a round trip to a database on another continent. Several seconds, every
# time. So a run_once(when=0) job would be scheduled, silently discarded as a
# misfire, and never run (this is exactly how the old LISTEN listener stayed
# dead for two months -- see v1.1.2). `None` means "run it however late it
# is", which for a job that only ever means "do this as soon as the loop is
# up" is the only correct setting.
RUN_LATE = {"misfire_grace_time": None}

# Set by attach(); everything below no-ops until then.
_bot_id: str | None = None
_display_name: str | None = None
_start_time: datetime | None = None
_enabled = False


# The host name never changes while the process runs, and gethostname() is a
# syscall -- worth doing once rather than on every heartbeat.
HOSTNAME = socket.gethostname()

# How long finished command rows and delivered events are kept before this bot
# tidies up after itself. The command queue carries BYTEA payloads (a /dbdump
# zip on its way to ManagerBot), so letting it grow forever means paying to
# store megabytes of files that were already delivered.
COMMAND_RETENTION_HOURS = int(os.environ.get("FAMILY_COMMAND_RETENTION_HOURS", "24"))
EVENT_RETENTION_DAYS = int(os.environ.get("FAMILY_EVENT_RETENTION_DAYS", "30"))
HOUSEKEEPING_SECONDS = int(os.environ.get("FAMILY_HOUSEKEEPING_SECONDS", "21600"))  # 6h


# ---------------------------------------------------------------------------
# Connection helper -- the family schema is shared, so it is always addressed
# by its fully-qualified name and never relies on this bot's search_path.
# ---------------------------------------------------------------------------
# This borrows db.py's connection pool rather than opening its own connection
# per heartbeat/poll. At a 3-second command poll that was 1,200 connect-
# authenticate-fork-disconnect cycles an hour, per bot, to almost always find
# an empty queue.

def _connect():
    return db.pooled()


def _column_missing(conn, table: str, column: str) -> bool:
    """True when family.<table> has no `column` yet.

    init_family_schema() asks this before each ALTER TABLE and issues the ALTER
    only when the column is missing. Every ALTER TABLE takes an ACCESS EXCLUSIVE
    lock on its table and keeps it until the transaction ends -- even one that
    turns out to change nothing, IF NOT EXISTS or not (measured on Postgres 17,
    2026-10-09). All five bots run init_family_schema() at every start, on
    tables the heartbeats and the command bus write to every few seconds, in one
    transaction that commits at the end: so a start that only wanted to check
    queued every other reader and writer of those tables behind it. A read of
    the catalog takes no lock that blocks anybody, so a normal start now takes
    none. IF NOT EXISTS stays on the statements because two bots starting
    together can both see the column missing; the second then waits for the
    first and finds nothing left to do (the same reason as in each bot's
    db.py). The statements stay literals, one per column."""
    return conn.execute(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_schema = %s AND table_name = %s AND column_name = %s",
        (FAMILY_SCHEMA, table, column),
    ).fetchone() is None


def init_family_schema() -> None:
    """Idempotent; every bot calls it at startup, whoever gets there first
    wins. Kept here rather than in ManagerBot alone so a bot started on its
    own (no manager running yet) still has somewhere to write."""
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
        # Which bus commands this bot answers, comma-separated. Written from
        # COMMANDS itself, so a bot that registers one of its own (ConvertBot's
        # `stars`, DownloaderBot's `probe`) advertises it without anybody
        # maintaining a list. NULL for a bot older than 1.7.0, which is why
        # every reader falls back to the shared set.
        if _column_missing(conn, "heartbeats", "commands"):
            conn.execute(f"ALTER TABLE {FAMILY_SCHEMA}.heartbeats "
                         f"ADD COLUMN IF NOT EXISTS commands TEXT")
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
        # What each bot has been costing, one row per sampling window.
        #
        # A heartbeat says a bot is alive; this says what being alive costs.
        # On a host that bills resident memory by the second, "is it up" and
        # "is it about to be too expensive to keep up" are different
        # questions, and until this existed only the first had an answer --
        # /status could say what the footprint is *right now*, which tells
        # nobody whether that is normal.
        #
        # Deliberately narrow: no user ids, no message text, nothing about
        # what anybody did. A count of updates, a count of distinct people,
        # and four numbers read from the kernel.
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
        # Added after the table existed on a running family, so they arrive as
        # ALTERs rather than as part of the CREATE. IF NOT EXISTS makes both
        # paths idempotent, which is what lets every bot run this on startup
        # without coordinating.
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
        # How often each button is tapped: a count per bot, per day (UTC), per
        # button, written with the usage sample. Counts only -- the key is the
        # button's name (and, for a few, its choice from a short fixed list), never
        # an id, a link or a name; shared_features.tap_key decides what a key may be.
        # Created like usage_samples above: IF NOT EXISTS, no ALTER, no lock on any
        # table that already exists.
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
        # ManagerBot's memory of who was up last time it looked, so it can
        # alert on the *transition* (down -> up, up -> down) instead of
        # repeating "still down" every minute.
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
        # One Stars balance per person, for the whole family.
        #
        # It lives here rather than in a bot's own schema because that is the
        # whole point of it: stars put in through StickerBot are spent in
        # ConvertBot, and neither bot owns them. A per-bot balance would be
        # five wallets somebody has to top up separately, which is worse than
        # the per-job invoice it replaces.
        #
        # `balance` is in CREDITS, not Stars, and the two are not the same
        # unit: paying Stars buys a multiple of them (TOPUP_MULTIPLIER), so a
        # balance is always larger than the money behind it and can never be
        # paid back out. That is why `lifetime_stars_paid` is a column of its
        # own -- it is the only figure here denominated in real money, and it
        # is the one to ask "has this person ever paid". Credits can arrive as
        # a grant or a welcome bonus, and counting those as a payment is how a
        # gift turns into a claim that somebody donated.
        #
        # The lifetime columns exist so a balance of 40 can be explained --
        # topped up 100, spent 60 -- without reading the whole ledger, and so
        # that an adjustment cannot inflate what somebody appears to have
        # paid: they grow on real top-ups, bonuses and spends, never on an
        # owner's correction.
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
        # Every movement, and why. A balance with no ledger behind it is a
        # number nobody can defend: the first question anybody asks about a
        # wallet is "where did the other sixty go", and the answer has to be
        # a list of dated lines rather than an assurance.
        #
        # `balance_after` is redundant on purpose. It makes the ledger
        # self-checking -- the newest row's balance_after must equal the
        # balance, and each row's must equal the one before it plus the delta
        # -- so a bug that debits without recording, or records without
        # debiting, is visible instead of merely suspected.
        #
        # `stars_paid` on a row is the real money behind that movement, so
        # the ledger can answer "what was actually charged" separately from
        # "what was credited" -- which is what a refund has to be reasoned
        # about in, and what a promotion rate makes different numbers.
        #
        # This is a payment record and is **not pruned and not erased**, for
        # the same reason star_transactions is not: it is what a refund is
        # issued against and what the totals are counted from. It holds a
        # numeric id, an amount and a reason, and nothing anybody wrote.
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
        # Bonus credit, one row per payment that earned some. The balance
        # above is the only figure anything spends from; this is what says how
        # much of it is bonus and when that part runs out. Spending takes from
        # the soonest-expiring lot first, so what a person loses to expiry is
        # only ever bonus they had not got round to using.
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
        # Every problem any bot shows anybody (see problems.py). One row per
        # incident, so a redraw or a double tap stores once.
        #
        # The row is written the moment the problem is *shown*, not when
        # somebody decides to report it -- the owner: "It should include the
        # error without user pressing the report button, but should only store
        # generic technical data with no user specific info". So the columns
        # in the CREATE are all a row has unless its person volunteered more:
        # which bot, the code, the incident, when it happened, the version.
        # None of them is about anybody.
        #
        # The columns added below are filled in only by somebody tapping
        # Report and then Send, having read exactly what those columns are. A
        # row with `shared_at` NULL has never carried anything about a person.
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
        # Added in 1.7.0, to a table that held reports only before it.
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
        # How many times this exact problem was shown. An incident is one
        # occurrence, so this only moves when the same message is re-shown --
        # a status line redrawn with the failure still on it.
        if _column_missing(conn, "problem_reports", "seen"):
            conn.execute(f"ALTER TABLE {FAMILY_SCHEMA}.problem_reports "
                         f"ADD COLUMN IF NOT EXISTS seen INTEGER NOT NULL DEFAULT 1")
        # 1 urgent, 2 fault, 3 refused -- see problems.level(). Stored rather
        # than derived on read, so that a row keeps the seriousness it had
        # when it happened: re-classifying a code later must not rewrite what
        # last month's nightly reports said.
        if _column_missing(conn, "problem_reports", "level"):
            conn.execute(f"ALTER TABLE {FAMILY_SCHEMA}.problem_reports "
                         f"ADD COLUMN IF NOT EXISTS level INTEGER NOT NULL DEFAULT 2")
        # Everything in the table before 1.7.0 arrived through the Report
        # button, which is what `shared_at` now means. Stamped with the time
        # the report came in, which is the only time those rows carry.
        conn.execute(f"UPDATE {FAMILY_SCHEMA}.problem_reports "
                     f"SET shared_at = reported_at "
                     f"WHERE shared_at IS NULL AND reported_at < %s",
                     (_REPORTS_BECAME_AUTOMATIC,))
        # /reports reads newest first over a table that now grows with every
        # problem rather than with every report.
        conn.execute(
            f"CREATE INDEX IF NOT EXISTS idx_family_problem_reports_recent "
            f"ON {FAMILY_SCHEMA}.problem_reports (reported_at DESC)"
        )
        # What people send with /feedback (shared_features.feedback_command),
        # added in 2.1.0.
        #
        # The words are what this table is for, which makes it the one place
        # in the family schema that keeps something a person wrote -- so it
        # keeps nothing else about them: which bot, which language, when, and
        # when the owner was sent it. No user id and no username, on the
        # owner's standing rule that nothing pushed at them identifies anybody.
        # Deleted after FEEDBACK_RETENTION_DAYS whether or not it was ever read.
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
        conn.commit()


# ---------------------------------------------------------------------------
# The credit balance
# ---------------------------------------------------------------------------
# Everything anybody pays goes into a balance, and everything the family
# charges for comes out of one. There is exactly one rail, and it is the same
# rail for the owner as for a stranger.
#
# **A balance is credit, not Stars.** Paying Stars buys credit at a rate above
# one-for-one, so a balance shown as "⭐" would claim a person holds Stars they
# could take back out. Credit is "⚡" and Stars stay "⭐", everywhere.
#
# **Payments are final.** The owner decided there are no refunds of Stars
# payments, and every place a person pays says so before they do. The only
# credit that ever comes back is a conversion's own charge, returned to the
# balance when the bot fails it -- that is not a refund of money.
#
# How much a payment buys is a lifetime ladder, in the owner's words:
# "however many times the user recharges, the first 500 stars will give 3x and
# the next 500 gives 2x usage." Read as a multiplier on the ordinary rate, so
# every clause means something: somebody's first 500 Stars ever buy 6 ⚡ each,
# the next 500 buy 4 ⚡, and after that 2 ⚡. Across all their payments, not per
# payment -- paying 100 five times earns exactly what paying 500 once does.
#
# The part of a payment above the ordinary rate is **bonus credit** and it
# expires (FAMILY_BONUS_EXPIRY_DAYS, 90 by default); the ordinary part never
# does. "Only the bonus credit should expire" was the owner's instruction.

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


# "stars:multiplier" segments of a person's lifetime Stars, in order; after the
# last one the multiplier is 1. Empty turns bonuses off.
TOPUP_LADDER = _parse_ladder(os.environ.get("FAMILY_TOPUP_LADDER", "500:3,500:2"))
BONUS_EXPIRY_DAYS = int(os.environ.get("FAMILY_BONUS_EXPIRY_DAYS", "90"))

# Every reason a balance can move, so that a typo becomes a failure here
# rather than a row nobody can group by later.
LEDGER_REASONS = ("topup", "bonus", "grant", "spend", "refund", "adjustment", "expired")


def credit_for_stars(stars: int) -> int:
    """The ordinary credit `stars` buy, with no bonus -- the part that never
    expires."""
    return int(round(stars * TOPUP_MULTIPLIER, 6))


def _ladder_ranges():
    start = 0
    for size, multiplier in TOPUP_LADDER:
        yield start, start + size, multiplier
        start += size
    yield start, None, 1.0


def quote_credit(lifetime_paid: int, stars: int) -> dict:
    """What paying `stars` earns for somebody who has already paid
    `lifetime_paid` Stars, split into the ordinary part and the bonus.

    A payment that straddles a step is priced piece by piece: 400 Stars from
    somebody at 300 buys 200 at 3x and 200 at 2x."""
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
    """(multiplier the next Star earns, Stars left at that multiplier). The
    second is None once past the ladder."""
    for start, end, multiplier in _ladder_ranges():
        if end is None or lifetime_paid < end:
            return multiplier, (None if end is None else end - max(lifetime_paid, start))
    return 1.0, None


def _ledger_bot() -> str:
    """Which bot a movement is recorded against. `attach()` has run by the
    time anybody spends anything; a movement before that is still worth
    recording, under a name that says it could not be attributed."""
    return _bot_id or "unattached"


def _ledger_in(conn, user_id, delta, reason, detail, stars_paid, balance_after) -> None:
    conn.execute(
        f"INSERT INTO {FAMILY_SCHEMA}.star_ledger "
        f"(user_id, bot_id, delta, reason, detail, stars_paid, balance_after) "
        f"VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (user_id, _ledger_bot(), delta, reason, detail, stars_paid, balance_after),
    )


def _lock_wallet_in(conn, user_id) -> int:
    """Make sure the wallet exists and hold it for this transaction. Every
    change to a balance goes through here first, so two bots touching one
    wallet at the same moment take turns instead of racing."""
    conn.execute(
        f"INSERT INTO {FAMILY_SCHEMA}.star_balances (user_id) VALUES (%s) ON CONFLICT (user_id) DO NOTHING",
        (user_id,),
    )
    return int(conn.execute(
        f"SELECT balance FROM {FAMILY_SCHEMA}.star_balances WHERE user_id = %s FOR UPDATE",
        (user_id,),
    ).fetchone()[0])


def _expire_in(conn, user_id) -> int:
    """Take away this person's bonus credit that has run out. Returns how
    much. Called at the start of anything that reads or moves a balance, so
    an expired bonus is never spendable even between housekeeping passes."""
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
    """Reduce this person's unexpired bonus by `amount`, soonest to expire
    first. What is spent is bonus before it is ordinary credit, so what
    expiry takes is only bonus nobody got round to using."""
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
    """After a balance goes down for any reason but a spend, bonus cannot be
    more than what is left -- or its expiry would later take away credit
    that is not there."""
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
    """What this person has, in credits, after any expired bonus is gone."""
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
    """Balance, lifetime figures, and how much of the balance is bonus with
    when the next of it expires. `stars_paid` is the only figure in real
    Stars, and it is what the ladder is counted from."""
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
    """quote_credit for this person, from what they have paid so far. For
    the sentence before a payment; topup() prices it again under a lock."""
    with _connect() as conn:
        row = conn.execute(
            f"SELECT lifetime_stars_paid FROM {FAMILY_SCHEMA}.star_balances WHERE user_id = %s",
            (user_id,),
        ).fetchone()
    return quote_credit(int(row[0]) if row else 0, stars)


def move_stars(user_id: int, delta: int, reason: str, detail: str | None = None,
               stars_paid: int = 0) -> int:
    """Add `delta` credits to a balance and record why. Returns the new
    balance. Negative deltas are allowed and may take a balance below zero,
    which simply means no paid work until it is positive again. What arrives
    this way is ordinary credit and never expires; only topup() creates
    bonus."""
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
    """Take `amount` credits off a balance if it covers it. Returns the new
    balance, or None if it does not. The check happens under the wallet's
    lock, so two jobs racing for the last of a balance cannot both win.
    Bonus is used before ordinary credit."""
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


def topup(user_id: int, stars_paid: int, detail: str | None = None,
          charge_id: "str | None" = None) -> "dict | None":
    """The whole of the money-in path. Prices the payment on the ladder from
    what this person has paid before -- under the wallet's lock, so two
    payments at once cannot both be priced as somebody's first 500 -- and
    records the ordinary credit and the bonus as separate ledger rows, with
    the bonus in a lot of its own that expires.

    `charge_id` is Telegram's own id for the payment (`telegram_payment_charge_id`),
    which is unique to it. Given, the payment is credited ONCE however many times
    Telegram delivers it: a claim `stars:paid:<charge_id>` is staked in
    family.settings in the same transaction as the credit, so the two stand or
    fall together -- a crash between them cannot leave a claim with no credit
    behind it (the failure that grant_stars_once has to undo by hand), and two
    deliveries at once cannot both win (the second INSERT waits for the first to
    commit, then finds the row). Returns None for a charge that was already
    credited; the caller then does nothing -- above all, does not start the
    conversion a second time. Without a charge id (the sandbox, which charges
    nobody) every call credits, as before.

    A database that drops the connection mid-payment (`psycopg.OperationalError`,
    which a pool timeout, a deadlock and a serialisation failure also are) is tried
    again, _TOPUP_ATTEMPTS times in all: the payment has been taken by then, and the
    handler that called this is Telegram's only delivery of it -- an error here was
    a person who paid and got nothing. Retrying is safe only because of the claim:
    a failed attempt rolled back whole, and an attempt whose COMMIT went through
    but whose answer was lost finds its own claim on the next try and returns None
    -- credited once, though the caller then stays silent (logged below). Any other
    error is a bug, not weather, and propagates at once.

    Returns stars, credited (ordinary), bonus, balance and bonus_expires."""
    for attempt in range(1, _TOPUP_ATTEMPTS + 1):
        try:
            with _connect() as conn:
                _lock_wallet_in(conn, user_id)
                if charge_id:
                    won = conn.execute(
                        f"INSERT INTO {FAMILY_SCHEMA}.settings (key, value) VALUES (%s, %s) "
                        f"ON CONFLICT (key) DO NOTHING RETURNING key",
                        (f"stars:paid:{charge_id}", datetime.now(timezone.utc).isoformat()),
                    ).fetchone()
                    if not won:
                        conn.rollback()
                        if attempt > 1:
                            logger.error("topup: charge %s was credited by an earlier attempt whose "
                                         "answer was lost; the payer was credited but not told", charge_id)
                        return None
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
                conn.commit()
            break
        except psycopg.OperationalError as exc:
            if attempt == _TOPUP_ATTEMPTS:
                raise
            logger.warning("topup: database error on attempt %d of %d (%s); trying again",
                           attempt, _TOPUP_ATTEMPTS, exc)
            time.sleep(_TOPUP_BACKOFF_SECONDS[attempt - 1])
    return {"stars": stars_paid, "credited": quote["base"], "bonus": quote["bonus"],
            "balance": balance, "bonus_expires": expires, "lifetime_before": lifetime}


# A payment's credit is retried through a database blip (see topup). Three tries
# over about four seconds: long enough for a pool to replace a dead connection or a
# managed Postgres to fail over its proxy, short enough that the person who paid
# is not left looking at nothing. Callers run topup in a thread, so the sleep
# never holds the event loop.
_TOPUP_ATTEMPTS = 3
_TOPUP_BACKOFF_SECONDS = (1.0, 3.0)


def set_star_balance(user_id: int, target: int, reason: str = "adjustment",
                     detail: str | None = None) -> int:
    """Put a balance at exactly `target` credits, recording the movement."""
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
    """Credit `amount` the first time this (key, user) is ever asked for, and
    never again. Returns the new balance, or None if it had already been
    done. The claim is staked atomically in family.settings, and released
    again if the credit that follows fails."""
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


# The most one gift may give one person. A gift goes to everybody at once, so a
# slipped digit (1000 for 100) would be multiplied by every account there is;
# past this the owner has to change the code, not just the command.
GIFT_MAX_CREDIT = 1000


def grant_bonus_once(user_id: int, amount: int, key: str, expires_at: datetime,
                     detail: str | None = None) -> "int | None":
    """A gift: `amount` credits of bonus that runs out at `expires_at`, given
    the first time this (key, user) is ever asked for and never again. Returns
    the new balance, or None if it had already been given.

    The same claim as grant_stars_once (`stars:granted:<key>:<user>`), so one
    key can never pay twice through the two of them. Unlike it, the claim and
    the credit are one transaction, staked the way topup() stakes a charge id:
    they stand or fall together, and two bots granting the same person at once
    take turns on the wallet's lock, the second finding the claim taken.

    It is bonus, not ordinary credit: a lot of its own in star_bonus_lots, spent
    before ordinary credit and taken back by _expire_in when it runs out."""
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
        # The last day it can be spent, as the owner typed it: expires_at is the
        # moment after it (midnight UTC that ends the day).
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
    """Housekeeping: expire every person's run-out bonus. Returns how much
    credit it took in total. Safe from five bots at once -- each wallet is
    locked, and a lot already cleared is not cleared again."""
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
    """This person's movements, newest first."""
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
    """Family-wide totals, and the largest balances. `outstanding` is credit
    people hold and have not spent -- work the family still owes -- and
    `bonus` is the part of it that will expire if unused."""
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
    """Write down that this problem was shown. True the first time an incident
    is seen, False for a re-show of the same one.

    Called for every coded message any bot sends, whether or not anybody ever
    taps Report. Nothing here is about a person: the bot, the code, the
    incident, when, and the version. `seen` counts re-shows, so a status line
    redrawn with the same failure on it does not become a second problem.
    """
    with _connect() as conn:
        row = conn.execute(
            f"INSERT INTO {FAMILY_SCHEMA}.problem_reports "
            f"(bot_id, code, incident, occurred_at, version, level) "
            f"VALUES (%s, %s, %s, %s, %s, %s) "
            f"ON CONFLICT (bot_id, incident) DO UPDATE "
            f"SET seen = {FAMILY_SCHEMA}.problem_reports.seen + 1 "
            f"RETURNING (xmax = 0)",
            (_ledger_bot(), code, incident, occurred_at, VERSION, int(level)),
        ).fetchone()
        conn.commit()
    return bool(row and row[0])


def attach_problem_reporter(code: str, incident: str, occurred_at,
                            user_id: int, username: "str | None",
                            user_lang: "str | None", chat_kind: "str | None",
                            level: int = 2) -> bool:
    """Add the details somebody volunteered to their incident's row. True if
    this is the first time anybody has attached themselves to it.

    The row usually exists already -- record_problem_occurrence wrote it when
    the problem was shown. The INSERT covers the one case where it does not: a
    bot that could not reach the database at the moment of the failure, which
    is exactly the kind of failure most worth having reported.

    Everything written here was named to the person, on screen, before they
    tapped Send. Nothing reaches this function any other way.
    """
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
            (_ledger_bot(), code, incident, occurred_at, VERSION, int(level),
             user_id, username, user_lang, chat_kind),
        ).fetchone()
        conn.commit()
    return row is not None


def recent_problem_reports(limit: int = 15, shared_only: bool = False) -> list[dict]:
    """The latest problems, newest first. `shared_only` narrows it to the ones
    somebody attached their own details to."""
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
    """One incident, whichever bot it came from.

    This is where details somebody volunteered are read: deliberately, by the
    owner asking for that incident, rather than pushed at them in an alert.
    The alert says a report arrived and nothing about who sent it.
    """
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
    """Everything recorded in the last `hours`, newest first. What the nightly
    report is built from -- one read rather than one per bot, because the
    report is about the family and the table already knows which bot each row
    came from."""
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
    """{bot_id: {level: count}} for the last `hours`. The cheap version of the
    line above, for a summary that only needs numbers."""
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


# ---------------------------------------------------------------------------
# Getting rid of them again
# ---------------------------------------------------------------------------
# Two different clocks, because the two halves of a row are two different
# things. The generic half -- code, incident, time, version -- is about the
# software and is not about anybody, so it can be kept long enough to tell
# whether a fault is new or has been happening since June. The half somebody
# volunteered *is* about them, and the reason it was collected (finding their
# case in the logs) stops applying within days of the fault being fixed.
#
# So the details are cleared first and the row survives without them, which
# keeps the count honest -- the fault still happened -- while holding nothing
# about who hit it. `/deletemydata` does the same thing immediately, for one
# person, whether or not the clock has run out.
PROBLEM_DETAIL_RETENTION_DAYS = int(os.environ.get("PROBLEM_DETAIL_RETENTION_DAYS", "30"))
PROBLEM_RETENTION_DAYS = int(os.environ.get("PROBLEM_RETENTION_DAYS", "180"))


def prune_problem_reports() -> int:
    """Clear old volunteered details, then drop old rows entirely. Returns how
    many rows were changed or removed."""
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
    """Take one person off every problem they ever volunteered for, now.

    The rows stay, without them: a fault that happened still happened, and the
    count of it is not personal data. Called by /deletemydata in every bot, so
    that "erase what you have on me" covers the family table as well as the
    bot's own."""
    with _connect() as conn:
        changed = conn.execute(
            f"UPDATE {FAMILY_SCHEMA}.problem_reports "
            f"SET user_id = NULL, username = NULL, user_lang = NULL, chat_kind = NULL "
            f"WHERE user_id = %s",
            (user_id,),
        ).rowcount or 0
        conn.commit()
    return changed


# ---------------------------------------------------------------------------
# Feedback
# ---------------------------------------------------------------------------
# A public bot writes what somebody sent with /feedback; ManagerBot reads it
# and sends it to the owner. The owner, 2026-10-03: "I don't have many users
# so I think notifications for each feedback won't hurt for now. Then add a
# switch for me to decide if i want each feedback to arrive to me or only
# grouped in a nightly report." The switch is a row in family.settings, so
# changing it needs no redeploy and every process reads the same answer.
FEEDBACK_RETENTION_DAYS = int(os.environ.get("FEEDBACK_RETENTION_DAYS", "30"))
FEEDBACK_DELIVERY_KEY = "feedback_delivery"
FEEDBACK_INSTANT, FEEDBACK_NIGHTLY = "instant", "nightly"


def save_feedback(lang: "str | None", body: str) -> int:
    """Keep one piece of feedback for ManagerBot to deliver. Returns its id."""
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
    """The oldest feedback nobody has been sent yet, marked as sent in the same
    statement -- so two ManagerBots overlapping in a deploy cannot both send
    the same one. Oldest first, the order it was written in."""
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
    """The latest feedback, newest first, delivered or not -- for ManagerBot's
    /feedback, which is where it can be read again after the message is gone."""
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
    """Drop feedback older than FEEDBACK_RETENTION_DAYS, sent or not. Run by
    every bot over the whole table, for the reason problem reports are: a
    family table that is tidied only while one particular bot is up grows
    whenever that bot is the one that is down."""
    with _connect() as conn:
        removed = conn.execute(
            f"DELETE FROM {FAMILY_SCHEMA}.feedback "
            f"WHERE created_at < now() - make_interval(days => %s)",
            (FEEDBACK_RETENTION_DAYS,),
        ).rowcount or 0
        conn.commit()
    return removed


# ---------------------------------------------------------------------------
# Small shared memory: family.settings
# ---------------------------------------------------------------------------
# A key and a short text, for a fact that has to survive a redeploy and be the
# same in every process -- "when did the route check last run", "which state
# was Instagram in". Not for anything about a person. Without a database
# (tests, a bot run on a laptop with persistence off) it is a dict in this
# process, which is the right amount of memory for those.
_local_settings: dict = {}


def get_setting(key: str, default: "str | None" = None) -> "str | None":
    """Blocking. Never raises: a missing database reads as "not set"."""
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
    """Every setting whose key starts with `prefix`, as {key: value}. Blocking.
    Never raises, for the same reason as get_setting: a missing database reads
    as nothing set. (ManagerBot reads DownloaderBot's route check this way.)"""
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
    """Blocking. Never raises, for the same reason."""
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


# ---------------------------------------------------------------------------
# The adaptive poll
# ---------------------------------------------------------------------------
# One timer, ticking every BUS_POLL_ACTIVE_SECONDS, that decides on each tick
# whether this is also a database poll: always while the bus is "active",
# otherwise once per BUS_POLL_IDLE_SECONDS. mark_bus_active() opens a fresh
# active window and is called by anything that has reason to expect bus
# traffic in the next few seconds -- a command just queued, claimed, or
# finished. So a bus that is doing something polls at ~1s and one that has
# been quiet for a while polls at ~10s, with no held connection and no state
# machine to get wrong.

_bus_active_until = 0.0   # monotonic; poll fast while time.monotonic() < this


def mark_bus_active() -> None:
    """Poll at the fast cadence for the next BUS_ACTIVE_WINDOW_SECONDS. Cheap
    and idempotent -- call it whenever bus traffic is likely (a command was
    just written, picked up, or answered)."""
    global _bus_active_until
    _bus_active_until = time.monotonic() + BUS_ACTIVE_WINDOW_SECONDS


def bus_is_active() -> bool:
    return time.monotonic() < _bus_active_until


# ---------------------------------------------------------------------------
# Outbound: heartbeats and events
# ---------------------------------------------------------------------------

def _monitoring():
    """The module holding this bot's error counter / status text. Called
    `shared_features` in the four public bots and `monitoring` in ManagerBot
    (which has no donations or sibling cross-promotion to share) -- looked
    up lazily, because this one file serves all five.

    `monitoring` is asked for first. Since 2.0.0 shared_features.py lives in
    shared/, which is on ManagerBot's path as well, so asking for it first
    would hand ManagerBot the public bots' module instead of its own."""
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
                 error_count, commands)
            VALUES (%s, %s, %s, %s, %s, %s, %s, now(), %s, %s)
            ON CONFLICT (bot_id) DO UPDATE SET
                display_name = excluded.display_name,
                host = excluded.host,
                version = excluded.version,
                pid = excluded.pid,
                db_schema = excluded.db_schema,
                started_at = excluded.started_at,
                last_seen = excluded.last_seen,
                error_count = excluded.error_count,
                commands = excluded.commands
            """,
            (_bot_id, _display_name, HOSTNAME, VERSION, os.getpid(),
             getattr(db, "DB_SCHEMA", "public"), _start_time, errors,
             ",".join(sorted(COMMANDS))),
        )
        conn.commit()


# Usage samples are one small row per bot per window and are the only thing
# here anybody will want to look *back* through, so they outlive the rest.
# 45 days is a month and a half: long enough to compare this month with last.
USAGE_RETENTION_DAYS = int(os.environ.get("FAMILY_USAGE_RETENTION_DAYS") or 45)


def report_event(level: str, kind: str, message: str, details: str | None = None) -> None:
    """Blocking -- call it through asyncio.to_thread from async code, or
    just let report_event_soon() below do that for you.

    level is one of info / warning / error / critical; ManagerBot decides
    which levels are worth a DM at 3am (see its ALERT_LEVELS)."""
    if not _enabled:
        return
    with _connect() as conn:
        conn.execute(
            f"INSERT INTO {FAMILY_SCHEMA}.events (bot_id, level, kind, message, details) "
            f"VALUES (%s, %s, %s, %s, %s)",
            (_bot_id, level, kind, message[:4000], (details or "")[:8000] or None),
        )
        conn.commit()
    # ManagerBot picks this up on its event pump's next pass -- fast, because a
    # bot reporting an event is usually in the middle of something the bus
    # cares about, and the pump runs its active cadence whenever ManagerBot has
    # recently been busy.


def record_usage(window_minutes: int, rss_mb, peak_rss_mb, ceiling_mb,
                 cpu_seconds, updates: int, users: int, sleepable_seconds: int = 0,
                 max_gap_seconds: int = 0, jobs_ok: int = 0, jobs_failed: int = 0) -> None:
    """One sampling window's worth of what this process cost. Blocking.

    Never raises: the whole point of this table is to notice a problem, and a
    monitoring write that took the bot down with it would be the problem."""
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
    """Add a window's button counts to today's (UTC) row of each button. Blocking.
    One transaction; `taps` is {key: count}, nothing else is stored."""
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
    """Taps per bot and button over the last `days` UTC days (today and the days-1
    before it), most tapped first within a bot. Every bot's rows: this is what
    ManagerBot asks for."""
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


def usage_history(bot_id: str | None = None, hours: int = 24) -> list[dict]:
    """Recent samples, newest first. `bot_id=None` means every bot, which is
    what ManagerBot asks for."""
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


def report_event_soon(level: str, kind: str, message: str, details: str | None = None) -> None:
    """Fire-and-forget version, safe to call from a running event loop or
    from plain sync code. Swallows everything -- a monitoring write must
    never propagate into the bot's own error path (which is often exactly
    where it is being called from)."""
    def _run():
        try:
            report_event(level, kind, message, details)
        except Exception:
            logger.debug("Could not report a family event", exc_info=True)

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        _run()
        return
    loop.run_in_executor(None, _run)


# ---------------------------------------------------------------------------
# Inbound: the command queue
# ---------------------------------------------------------------------------
# Each entry returns (text, file_name, file_bytes). Only ManagerBot ever puts
# rows in the queue, and only the owner can drive ManagerBot, so these are the
# same trust level as each bot's own owner-only commands.

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
    """Blocking -- call through asyncio.to_thread.

    Two numbers, and the second is the one that makes the whole report
    trustworthy. `db_ms` is how far this process is from Postgres. `skew_ms`
    is how far this machine's clock is from Postgres's: without it, any
    "sent at X, arrived at Y" figure computed across two hosts is that skew
    plus the real latency, with no way to tell which is which. Postgres's
    clock is the one both ends can see, so it is the referee.
    """
    local_before = datetime.now(timezone.utc)
    started = time.perf_counter()
    # Through pooled_read, so this is ONE round trip and reports the distance
    # to the database rather than the distance times however many statements
    # the pool wraps around it. Measured through pooled() it was the query
    # plus an implicit BEGIN plus a COMMIT plus a liveness check -- four trips
    # to the database reported as though they were one, which is how
    # "Supabase 976 ms" ended up on screen for a link that was nearer 250.
    with db.pooled_read() as conn:
        server_now = conn.execute("SELECT clock_timestamp()").fetchone()[0]
    elapsed_ms = (time.perf_counter() - started) * 1000
    local_after = datetime.now(timezone.utc)
    # The query result was produced somewhere inside the round trip; the
    # midpoint of it is the least wrong instant to compare against.
    midpoint = local_before + (local_after - local_before) / 2
    return {
        "db_ms": round(elapsed_ms, 2),
        "skew_ms": round((server_now - midpoint).total_seconds() * 1000, 1),
        "host": HOSTNAME,
        "where": _where_am_i(),
        "version": VERSION,
        "pid": os.getpid(),
    }


# The id the user-path probe reads with. Not anybody's: it is below every id
# Telegram issues, so the SELECT walks the same index to the same depth as a
# real lookup and finds nothing, and no real person's row is read to time a
# ping.
PROBE_USER_ID = 0


def user_path_probe() -> dict:
    """Blocking -- call through asyncio.to_thread. The database half of what a
    person actually waits for.

    /ping measures the **admin bus**: ManagerBot writing a row in Postgres,
    this bot noticing it, answering into another row, ManagerBot collecting
    it. Nobody using the bot ever takes that path, which is why the report
    says so -- but saying "this is not what a user waits for" and then not
    saying what a user *does* wait for leaves the useful question unanswered.

    An info command is the cheapest real thing a person can ask for -- /start
    with a language already chosen is one indexed read of `user_settings` and
    one reply -- and it is the floor under every other answer this bot gives.
    So the read is done here, for real, through the same pool every handler
    uses, and reported next to the trip to Telegram that carries the answer
    back.
    """
    read = getattr(db, "get_user_language", None)
    started = time.perf_counter()
    if read is not None:
        read(PROBE_USER_ID)
        what = "user_settings"
    else:
        # ManagerBot has no user_settings. Time the pool itself rather than
        # report nothing -- it is the same round trip minus the index walk.
        with db.pooled_read() as conn:
            conn.execute("SELECT 1").fetchone()
        what = "SELECT 1"
    return {"info_ms": round((time.perf_counter() - started) * 1000, 2), "info_query": what}


async def _telegram_round_trip(context) -> float | None:
    """One call to the Bot API and back, in milliseconds.

    getWebhookInfo, because it is the cheapest method that always goes to
    Telegram: getMe is cached by python-telegram-bot after startup and would
    report nought, which is the one wrong answer that looks like a fast one.
    """
    bot = getattr(context, "bot", None)
    if bot is None or not hasattr(bot, "get_webhook_info"):
        return None
    started = time.perf_counter()
    await bot.get_webhook_info()
    return round((time.perf_counter() - started) * 1000, 2)


async def _cmd_ping(context, args):
    """Plain `ping` answers a sentence. `ping trace` answers the numbers
    ManagerBot needs to draw the full round trip -- see its /ping."""
    up = datetime.now(timezone.utc) - _start_time
    if not args or args[0] != "trace":
        return f"pong -- up {_format_delta(up)}", None, None
    try:
        probe = await asyncio.to_thread(ping_probe)
    except Exception as exc:
        probe = {"error": f"{type(exc).__name__}: {exc}"}
    probe["up"] = _format_delta(up)
    # What a person waits for, measured rather than guessed. Both halves are
    # optional: a failure here must not cost the round trip its report, which
    # is the number the command was written for.
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
        # The answer's own cost: read the row, hand the reply to Telegram.
        # Their trip to Telegram and back is on top and is not measurable
        # from here, which is what the report says rather than folding an
        # invented number in.
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
    """Every distribution this process can import, as `name==version`, sorted
    -- what `pip freeze` would print, without starting pip.

    The requirements files name lower bounds (`yt-dlp[default]>=2025.11.12`),
    so what production actually runs is whatever the last build happened to
    resolve. This is how that list is read back, to be pinned in the next
    release, from the process itself rather than from a guess about the image."""
    from importlib import metadata
    seen: dict[str, str] = {}
    for dist in metadata.distributions():
        name = (dist.metadata["Name"] or "").strip()
        if name and name.lower() not in seen:
            seen[name.lower()] = f"{name}=={dist.version}"
    return [seen[key] for key in sorted(seen)]


async def _cmd_versions(context, args):
    """`versions` sends the whole list as a requirements-style file;
    `versions <name>` answers the matching lines as text."""
    import platform
    lines = await asyncio.to_thread(installed_versions)
    head = f"Python {platform.python_version()}, {len(lines)} packages"
    if args:
        wanted = args[0].lower()
        found = [line for line in lines if wanted in line.lower()]
        return f"{head}; matching '{args[0]}':\n" + ("\n".join(found) or "(none)"), None, None
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M")
    data = (f"# {_bot_id} {VERSION}, {head}, read {stamp} UTC\n" + "\n".join(lines) + "\n").encode()
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


async def _cmd_message(context, args):
    """Sends as THIS bot -- that is the whole point of routing it here
    rather than having ManagerBot send it: the user only ever sees the bot
    they actually talked to."""
    if len(args) < 2 or not args[0].lstrip("-").isdigit():
        return "Usage: message <user_id> <text>", None, None
    await context.bot.send_message(chat_id=int(args[0]), text=" ".join(args[1:]))
    return "Sent.", None, None


class _RecentLines(logging.Handler):
    """The last lines one of the log files would hold, kept in memory. A host
    that writes no log files -- Railway, unless LOG_TO_FILES=1 -- would
    otherwise give /logs nothing to show: its console output goes to the
    platform's own viewer, which is not somewhere ManagerBot can reach."""

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
    """Keep the latest lines of each log in memory, for /logs on a host with
    no log files. Idempotent; attach() calls it."""
    root = logging.getLogger()
    fmt = next((h.formatter for h in root.handlers if h.formatter is not None), None) \
        or logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s")
    for handler in _RECENT_LOGS.values():
        if handler not in root.handlers:
            handler.setFormatter(fmt)
            root.addHandler(handler)


def _tail_lines(path: Path, wanted: int) -> list[str]:
    """Reads the last `wanted` lines by seeking backwards from the end of the
    file. bot.log rotates at 2 MB, and pulling all of it into memory to throw
    away everything but the last forty lines is exactly the kind of spike this
    bot cannot afford on a small container."""
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


# How much of a log /logs prints: this many lines when none are asked for, and
# never more than the maximum. Named so ManagerBot's logs page under /help reads the same
# numbers the command uses.
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
        # No file on this host (the cloud writes none unless LOG_TO_FILES=1),
        # so what this process has logged since it started, from memory.
        tail = _RECENT_LOGS[which].tail(lines_wanted)
        where = f"{which} (kept in memory since this process started; no log files on this host)"
    if not tail:
        return f"{where}: nothing yet.", None, None
    return f"--- {where}, last {len(tail)} line(s) ---\n" + "\n".join(tail), None, None


async def _cmd_restart(context, args):
    """Exits with a non-zero code so a supervisor restarts the process --
    Railway's restart policy, Docker's restart: unless-stopped, and so on.
    Run against a bot started by hand on a laptop it just stops it."""
    async def _bye():
        await asyncio.sleep(2)
        logger.warning("Restarting: asked to by ManagerBot.")
        os._exit(1)

    asyncio.create_task(_bye())
    return "Restarting now (a supervisor brings it back; a hand-started process just stops).", None, None


async def _cmd_crashtest(context, args):
    """Deliberately raise inside this bot, so the whole crash path can be
    checked end to end from ManagerBot without waiting for a real bug.

    StickerBot has had a local `/crashtest` for this since v0.4, and it was
    the one owner-only command with no way to reach it from ManagerBot. Now
    every bot has it, which is the more useful shape: what you actually want
    to know is whether the reporting works for the bot that just went quiet,
    and that is never the bot whose chat you happen to be in.

    The raise goes through `application.create_task` rather than being raised
    here. A bus handler that raises is caught by `_run_one_command`, which
    turns it into a failed command result -- informative, but it never
    reaches the error handler, so it would test nothing. `create_task` routes
    the exception to exactly where a real handler's would go:
    shared_features.error_handler -> record_error -> emit_event -> the alert
    ManagerBot forwards. If that alert does not arrive within a few seconds,
    the crash reporting is broken and this is how you found out.
    """
    app = getattr(context, "application", None)
    if app is None:
        raise RuntimeError("Manual crashtest via ManagerBot -- error tracking works.")

    async def _boom():
        raise RuntimeError(
            f"Manual crashtest for {_bot_id} via ManagerBot -- error tracking works.")

    app.create_task(_boom())
    return ("Raised. The alert should arrive in a moment; if it does not, "
            "the crash reporting is what is broken."), None, None


# ---------------------------------------------------------------------------
# Talking to everybody, and announcing an update before it lands
# ---------------------------------------------------------------------------
# Four commands that all share one shape: ManagerBot decides, the bot the user
# actually talks to does the speaking. That is the whole reason these live
# here rather than in ManagerBot -- a person who has only ever met StickerBot
# should hear about StickerBot's update from StickerBot, in their own
# language, and not from a private bot they have never seen.

def _i18n():
    """This bot's translations, or None. ManagerBot has no i18n.py -- it has
    exactly one reader -- so everything below degrades to plain English
    rather than requiring one."""
    try:
        return importlib.import_module("i18n")
    except ImportError:
        return None
    except Exception:
        return None


# Fallbacks for ManagerBot, and for any key a translation file has not caught
# up with yet. Never the normal path in the four public bots.
_PLAIN = {
    "update_soon_try_later": "\U0001f527 I'm about to be updated, so I can't start anything new right now. Please try again in about {minutes} minutes.",
    "update_soon_try_later_soon": "\U0001f527 I'm about to be updated, so I can't start anything new right now. Please try again shortly.",
    "update_will_reset": "\U0001f527 Heads up: I'm about to be updated, and what you have going right now will be reset. You'll be able to start it again in a moment.",
    "update_done_try_now": "✅ The update is done. You can go ahead and try again now.",
    "gift_granted": "\U0001f381 We have passed 100 users, thank you! You get {amount} ⚡ of free credit, "
                    "good until {date}. It pays for about {conversions} paid conversions in {convert} "
                    "or big-file downloads in {download}. Your balance: /balance",
    "gift_claimed": "\U0001f381 Gift received: {amount} ⚡ of free credit! Mumu bots have passed {users} users, "
                    "and this is our thank-you.\nIt is good until {date} (UTC) and pays for about {conversions} "
                    "paid conversions in {convert} or big-file downloads in {download}.\nYour balance: /balance",
    "gift_ad": "\U0001f389 Mumu bots have passed {users} unique users, so we decided to give out {amount} ⚡ of "
               "free credit! You can claim it and use it in {download} and {convert}.\n\n<i>To claim it, send any "
               "message to either bot: the gift is added automatically. Claim it by {claim_by}; once claimed, "
               "it lasts {days} days.</i>",
}


def phrase(key: str, lang: str | None = None, **kwargs) -> str:
    """One of the sentences above, translated if this bot can."""
    i18n = _i18n()
    if i18n is not None:
        try:
            text = i18n.t(lang or "en", key, **kwargs)
            # i18n.t returns the key itself when it has no such string, which
            # is the signal to fall back rather than send someone a key.
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


# Who counts as "active" when a broadcast is aimed rather than sent to
# everybody. Seven days, and the number is a judgement about what an
# announcement is for rather than about the data.
#
# An advance notice of an update is only worth sending to somebody it might
# actually affect. Sending it to every account that ever typed /start is a
# push notification to people who used the bot once in March, which reads as
# spam and is the fastest way to teach them to mute it -- so the next notice,
# the one that matters, is not seen either. Seven days covers everyone with a
# habit, including the weekly user, and excludes the long tail.
#
# It is deliberately not the same window as /users' default (24 hours). That
# one answers "is anyone around right now", which is an operational question
# asked at the moment of deploying. This one answers "who would want to
# know", which is asked the day before.
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
    """Everyone seen in the last `days`, or None if this bot cannot say.

    None rather than an empty list, because the two mean opposite things: a
    bot with no activity table cannot answer the question, and answering it
    with "nobody" would silently turn an aimed broadcast into no broadcast at
    all.
    """
    fn = getattr(db, "active_user_ids_since", None)
    if fn is None:
        return None
    try:
        return fn(datetime.now(timezone.utc) - timedelta(days=days))
    except Exception:
        logger.exception("Could not read this bot's active users")
        return None


# Telegram's documented ceiling for bulk sends is about 30 messages a second,
# and a bot that trips it gets a 429 with a retry_after rather than a queue.
# A broadcast is never urgent to the second, so it is paced under the limit
# and simply keeps going past anyone who cannot be reached.
BROADCAST_PER_SECOND = float(os.environ.get("FAMILY_BROADCAST_PER_SECOND", "20"))


async def _send_to_each(context, targets, text_for) -> tuple[int, int]:
    """(delivered, skipped). `targets` is an iterable of (user_id, chat_id);
    `text_for(user_id)` returns that person's text, or None to skip them.

    Nothing here raises. Someone who blocked the bot, deleted their account
    or never really existed is a skip, not a failure -- the alternative is a
    broadcast that stops halfway through the alphabet.
    """
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
    """Sends one message to everyone this bot knows, as this bot.

    Verbatim, and deliberately not translated: the owner wrote these words
    and this file has no way to write them again in Uzbek. The four sentences
    the *bots* say about updates are the translated ones.
    """
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


# ---- the gift: bonus credit for everyone, once -----------------------------
# One promotion at a time: `amount` of bonus credit for everyone this bot knows,
# once per person across the family (grant_bonus_once's claim), and a notice in
# their own language from the bot that won the claim -- so somebody who uses two
# bots is given it once and told once. Without --go it only counts.
#
# Run --go once per bot, at the moment the promotion is announced: a later run
# also reaches whoever arrived in between, and a fixed moment is what stops the
# gift being farmed with fresh accounts.

GIFT_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,39}$")
# What _gift_args says of a key that does not fit GIFT_KEY_RE; ManagerBot's
# gift page under /help prints the same sentence.
GIFT_KEY_RULE = "The key is 3-40 lowercase letters, digits or dashes, starting with a letter or digit."
GIFT_MAX_DAYS = 400
# Never real people: anonymous group admins, channels posting as themselves, and
# Telegram (the ids delivery_cache.SHARED_SENDER_IDS also refuses).
_GIFT_NOT_PEOPLE = {1087968824, 136817688, 777000}
# What a paid conversion costs, roughly, for the notice's "about N conversions":
# 6-7 ⚡ for a small one, 13-33 for a big video. Ten is the honest middle.
GIFT_CREDITS_PER_CONVERSION = 10


def _gift_last_day(text: str) -> "datetime | None":
    """`YYYY-MM-DD` -> the moment the gift runs out: the end of that day, UTC."""
    try:
        day = datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return day + timedelta(days=1)


def _gift_args(args: list[str]) -> "tuple[str, int, datetime, str] | str":
    """(key, amount, expires_at, last day as written), or the sentence saying
    what is wrong. ManagerBot checks the same rules before it queues."""
    if len(args) != 3:
        return "Usage: gift <key> <credits> <last day YYYY-MM-DD> [--go]"
    key, raw_amount, raw_day = args
    if not GIFT_KEY_RE.fullmatch(key):
        return GIFT_KEY_RULE
    if key == PROMO_KEY:
        # Its claims are the 220 gift's: /gift under it would count as claims
        # in /promo and turn the people it reached into "already had it".
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
    """A sibling as somebody can tap it, from SIBLING_BOTS (the format
    shared_features documents), or its plain name."""
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


# ---------------------------------------------------------------------------
# The 220 ⚡ gift: claimed by writing to ConvertBot or DownloaderBot (2.2.0)
# ---------------------------------------------------------------------------
# One promotion, not a tool (the owner, 2026-10-18: "Make this one gift not
# general, so it dies after first usage"). ManagerBot counts the family's unique
# users and tells the owner once they reach PROMO_USERS; the owner's /promo then
# starts it. The start is a row in family.settings that is written once and
# never changed, so this gift can never run a second time.
#
# From the start, for PROMO_CLAIM_DAYS, the first message somebody sends to
# ConvertBot or DownloaderBot gives them PROMO_CREDITS ⚡ of bonus credit, once
# (grant_bonus_once: the claim and the credit in one transaction), to spend
# within PROMO_SPEND_DAYS of that day. AnonBot shows its ad to each person once
# meanwhile. /promo stop ends the claiming early; credit already given stays.
#
# Every day here ends at midnight UTC, the rule gift dates already follow (TERMS:
# "expires at the end of the last day stated"), so the dates people are shown
# are whole days.

PROMO_KEY = "gift-220-users"
PROMO_USERS = 220
PROMO_CREDITS = 220
PROMO_CLAIM_DAYS = 14
PROMO_SPEND_DAYS = 28
PROMO_STARTED = f"promo:{PROMO_KEY}:started"
PROMO_STOPPED = f"promo:{PROMO_KEY}:stopped"
PROMO_MILESTONE = f"promo:{PROMO_KEY}:milestone"
# How stale a bot's idea of "has it started" may be. Only the start can lag: the
# end is read off the clock, and a claim re-reads both before it gives anything.
PROMO_REFRESH_SECONDS = 60
# What claim_promo_gift says happened.
PROMO_GIVEN, PROMO_HAD, PROMO_CLOSED = "given", "had", "closed"

_promo_cache: dict = {"at": None, "started": None, "stopped": None}


def _end_of_day(day) -> datetime:
    """The moment a date ends: the midnight UTC after it."""
    return datetime(day.year, day.month, day.day, tzinfo=timezone.utc) + timedelta(days=1)


def _moment(text: "str | None") -> "datetime | None":
    if not text:
        return None
    try:
        when = datetime.fromisoformat(text)
    except ValueError:
        return None
    return when.astimezone(timezone.utc) if when.tzinfo is not None else None


# Everything below reads and writes the database itself, never get_setting's
# in-process fallback: that fallback is what a process gets when its family bus
# did not come up, and a gift started in one process's memory would be "started"
# for ManagerBot's card and for nobody else.

def _claim_setting(key: str, value: str) -> bool:
    """Write a setting only if it is not there yet. True if this call wrote it.
    Blocking; raises on a database error (unlike set_setting: a caller that
    starts something has to know whether it did)."""
    with _connect() as conn:
        won = conn.execute(
            f"INSERT INTO {FAMILY_SCHEMA}.settings (key, value) VALUES (%s, %s) "
            f"ON CONFLICT (key) DO NOTHING RETURNING key",
            (key, value),
        ).fetchone()
        conn.commit()
    return bool(won)


def _read_promo(fresh: bool = False) -> tuple:
    """(started, stopped), each a UTC datetime or None. Blocking.

    Cached for PROMO_REFRESH_SECONDS. A `fresh` read raises on a database error,
    so that a claim never takes a failed read for "not stopped". A cached-path
    read that fails answers "not started" until the next refresh: closed, which
    gives nothing away, and one connection attempt a minute rather than one per
    message while the database is down."""
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
    """When claiming ends for a gift started at `started`, unless it is
    stopped first: the end of the PROMO_CLAIM_DAYS-th day after the start (UTC).
    ManagerBot's card uses it to say what a start now would mean."""
    return _end_of_day((started.astimezone(timezone.utc) + timedelta(days=PROMO_CLAIM_DAYS)).date())


def promo_window(fresh: bool = False) -> "tuple[datetime, datetime] | None":
    """(started, closes) once the owner has started the gift, else None.
    `closes` is the end of the PROMO_CLAIM_DAYS-th day after the start, or the
    moment it was stopped if that came first. Blocking; raises on a database
    error."""
    started, stopped = _read_promo(fresh)
    if started is None:
        return None
    closes = promo_closes_for(started)
    if stopped is not None and stopped < closes:
        closes = stopped
    return started, closes


def promo_open(now: "datetime | None" = None, fresh: bool = False) -> bool:
    """Can the gift be claimed at `now`? Blocking. Only a `fresh` read raises
    on a database error; the cached one answers False (see _read_promo)."""
    window = promo_window(fresh)
    if window is None:
        return False
    now = now or datetime.now(timezone.utc)
    return window[0] <= now < window[1]


def promo_expiry(claimed_at: datetime) -> datetime:
    """When a gift claimed at `claimed_at` runs out: the end of the
    PROMO_SPEND_DAYS-th day after the day it was claimed (UTC)."""
    return _end_of_day((claimed_at.astimezone(timezone.utc) + timedelta(days=PROMO_SPEND_DAYS)).date())


def claim_promo_gift(user_id: int) -> "tuple[str, datetime | None]":
    """Give this person the gift if it is open and they never had it:
    (PROMO_GIVEN, when it runs out), (PROMO_HAD, None) or (PROMO_CLOSED, None).
    Re-reads the start and the stop first, so a cached "open" does not outlive
    /promo stop. (A claim that read "open" in the moment before the stop was
    committed still pays: a handful at most, accepted.) Blocking; raises on a
    database error, and then nothing was given."""
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
    """Has this person had the gift? Blocking; raises on a database error."""
    claim = f"stars:granted:{PROMO_KEY}:{user_id}"
    with _connect() as conn:
        return conn.execute(
            f"SELECT 1 FROM {FAMILY_SCHEMA}.settings WHERE key = %s", (claim,)
        ).fetchone() is not None


def promo_claim_count() -> int:
    """How many people have had the gift. Blocking; raises on a database error."""
    return len(_gift_already_given(PROMO_KEY))


def _refresh_promo_after_write() -> None:
    """Re-read the state after this process changed it. Never raises: the
    write is already committed, and a failed read-back must not make the
    owner's card say that nothing happened."""
    try:
        _read_promo(fresh=True)
    except Exception:
        _promo_cache["at"] = None
        logger.debug("The gift's state changed; reading it back failed", exc_info=True)


def start_promo() -> "tuple[datetime, bool]":
    """Start the gift -- possible once, ever. (when it started, True if this
    call started it). Blocking; raises on a database error, but never after
    this call's start is written."""
    now = datetime.now(timezone.utc)
    if _claim_setting(PROMO_STARTED, now.isoformat()):
        _refresh_promo_after_write()
        return now, True
    started, _ = _read_promo(fresh=True)
    return started, False


def stop_promo() -> bool:
    """End the claiming now, if the gift is open. True if this call ended it.
    Credit already given is kept. Blocking; raises on a database error."""
    if not promo_open(fresh=True):
        return False
    won = _claim_setting(PROMO_STOPPED, datetime.now(timezone.utc).isoformat())
    _refresh_promo_after_write()
    return won


def note_promo_milestone() -> bool:
    """True the first time ever this is called: ManagerBot's one notice to the
    owner that the users have reached PROMO_USERS. Blocking; raises on a
    database error."""
    return _claim_setting(PROMO_MILESTONE, datetime.now(timezone.utc).isoformat())


def promo_milestone_at() -> "datetime | None":
    """When the users first reached PROMO_USERS, if they have. Blocking; raises
    on a database error."""
    with _connect() as conn:
        row = conn.execute(
            f"SELECT value FROM {FAMILY_SCHEMA}.settings WHERE key = %s", (PROMO_MILESTONE,)
        ).fetchone()
    return _moment(row[0]) if row else None


def _promo_bots() -> dict:
    return {"convert": _bot_handle("convertbot", "ConvertBot"),
            "download": _bot_handle("downloaderbot", "DownloaderBot")}


def promo_notice(lang: "str | None", expires_at: datetime) -> str:
    """What somebody reads in ConvertBot or DownloaderBot when the gift was
    just given to them. Plain text."""
    return phrase(
        "gift_claimed", lang, amount=PROMO_CREDITS, users=PROMO_USERS,
        date=(expires_at - timedelta(days=1)).strftime("%d.%m.%Y"),
        conversions=max(1, PROMO_CREDITS // GIFT_CREDITS_PER_CONVERSION), **_promo_bots(),
    )


def promo_ad(lang: "str | None", closes: datetime) -> str:
    """AnonBot's ad for the gift, in HTML (the last paragraph is in italics)."""
    return phrase(
        "gift_ad", lang, amount=PROMO_CREDITS, users=PROMO_USERS, days=PROMO_SPEND_DAYS,
        claim_by=(closes - timedelta(microseconds=1)).strftime("%d.%m.%Y"), **_promo_bots(),
    )


async def _cmd_pause(context, args):
    """Stop starting work a redeploy would throw away, and say so.

    Anyone turned away is written down, so `resume` can tell them it is over.
    """
    minutes = int(args[0]) if args and args[0].isdigit() else lifecycle.DEFAULT_MAINTENANCE_MINUTES
    until = await asyncio.to_thread(lifecycle.begin_maintenance, _bot_id, minutes)
    return (
        f"Paused. New long work is declined until you say otherwise; "
        f"users are being told to come back in about {minutes} minute(s) "
        f"(around {until.strftime('%H:%M')} UTC).",
        None, None,
    )


async def _cmd_warnbusy(context, args):
    """Tell everyone mid-something that it is about to be lost.

    Only the people actually inside a slow stretch of work: with state now
    persisted, an open conversation survives a redeploy and warning about it
    would be a false alarm. What does not survive is an encode or a download
    already running, which is exactly what busy() tracks.
    """
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
    """The other half of pause: reopen, and go back to everyone who was
    turned away while it was closed."""
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
    "crashtest": _cmd_crashtest,
    "broadcast": _cmd_broadcast,
    "pause": _cmd_pause,
    "warnbusy": _cmd_warnbusy,
    "resume": _cmd_resume,
    "gift": _cmd_gift,
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
    "restart": "restart that bot's process",
    "crashtest": "raise on purpose, to check the crash alert still works",
    "broadcast": "broadcast [--active] <text> -- one message as that bot; --active aims it at recent users only",
    "pause": "pause [minutes] -- decline new long work and say why",
    "warnbusy": "tell whoever is mid-something that it is about to be reset",
    "resume": "reopen, and tell everyone who was turned away",
    "gift": "gift <key> <credits> <last day YYYY-MM-DD> [--go] -- bonus credit for everyone, once each; counts unless --go",
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
    """FOR UPDATE SKIP LOCKED so two copies of the same bot (a laptop one
    and a deployed one both pointed at the same database) can't run the
    same command twice."""
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
    # ManagerBot's result pump collects this on its next pass. That pass is at
    # the fast cadence: ManagerBot marked the bus active when it queued the
    # command, so the whole time an answer could be coming back it is looking
    # about once a second.


async def _run_one_command(context, row) -> None:
    command_id, command, raw_args = row
    handler = COMMANDS.get(command)
    logger.info("ManagerBot asked for: %s %s", command, raw_args)
    try:
        if handler is None:
            ok, output, name, data = False, f"Unknown command '{command}'.", None, None
        else:
            output, name, data = await handler(context, raw_args.split())
            ok = True
    except Exception as exc:
        logger.exception("Family command %r failed", command)
        ok, output, name, data = False, f"{type(exc).__name__}: {exc}", None, None

    try:
        await asyncio.to_thread(_finish_command, command_id, ok, output, name, data)
    except Exception:
        logger.exception("Could not write the result of family command %s back", command_id)


# One pass does not mean one command: several can be queued between two ticks
# (a /ping to everything, a bot that was down catching up), so drain rather
# than leave the rest for "next time". The ceiling is there so that a queue
# somebody filled by accident cannot monopolise the event loop -- what is left
# waits for the next tick, which is immediate while the bus is active.
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
        # Commands cluster -- one arriving means another probably is too.
        # Keep looking at the fast cadence for a while.
        mark_bus_active()


# _bus_tick fires every BUS_POLL_ACTIVE_SECONDS and decides whether this tick
# is also a database poll: always while the bus is active, otherwise once per
# BUS_POLL_IDLE_SECONDS. This is the whole delivery mechanism -- there is no
# push behind it any more -- so it is deliberately simple and has nothing that
# can be "down".
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
        await asyncio.to_thread(write_heartbeat)
    except Exception:
        logger.debug("Heartbeat failed (database unreachable?)", exc_info=True)


# ---------------------------------------------------------------------------
# Housekeeping -- every bot tidies up after itself
# ---------------------------------------------------------------------------
# Three tables grow forever if nobody deletes from them: family.commands (with
# a BYTEA column, so a handful of /dbdump zips is megabytes), family.events,
# and each bot's own activity_events. On a metered database that is a bill
# that only goes up, for rows nothing will ever read again.
#
# Each bot prunes only its *own* rows, so the five processes never collide and
# no coordination is needed -- target_bot / bot_id already partition the shared
# tables perfectly, and activity_events lives in the bot's own schema.

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
        # Button-tap counts live as long as the usage samples do.
        cur = conn.execute(
            f"DELETE FROM {FAMILY_SCHEMA}.button_taps "
            f"WHERE bot_id = %s AND day < (now() AT TIME ZONE 'UTC')::date - %s",
            (_bot_id, USAGE_RETENTION_DAYS),
        )
        taps = cur.rowcount
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
    # The volunteered halves of old problem reports, and then the old rows.
    # Every bot runs this over the shared table rather than only its own rows:
    # it is idempotent, it is a cheap indexed delete, and a family table that
    # only gets tidied when one particular bot happens to be up is a family
    # table that grows whenever that bot is the one that is down.
    try:
        parts.append(f"{prune_problem_reports()} problem row(s)")
    except Exception:
        logger.debug("Could not prune problem reports", exc_info=True)
    try:
        parts.append(f"{prune_feedback()} feedback row(s)")
    except Exception:
        logger.debug("Could not prune feedback", exc_info=True)
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


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------

def attach(app, bot_id: str, display_name: str, start_time: datetime) -> None:
    """One line in each bot's main(), just before run_polling().

    Never raises: a bot whose shared database is unreachable logs a warning
    and carries on serving its users with no family bus at all.

    FAMILY_BOT_ID and FAMILY_LABEL override what this process calls itself on
    the bus. They exist for the test bot (see `testbot/`), which runs one of
    the four bots' code on a spare token: without them it would write its
    heartbeat under the real bot's id, and ManagerBot would show a laptop
    process as the live one. `testbot/run.ps1` points at a local database as
    well, so this is the second of two locks on the same door rather than the
    only one.
    """
    global _bot_id, _display_name, _start_time, _enabled

    keep_recent_log_lines()

    if os.environ.get("FAMILY_BUS", "on").lower() in ("off", "0", "false", "no"):
        logger.info("Family bus disabled (FAMILY_BUS=off) -- running standalone.")
        return

    bot_id = os.environ.get("FAMILY_BOT_ID") or bot_id
    display_name = os.environ.get("FAMILY_LABEL") or display_name
    _bot_id, _display_name, _start_time = bot_id, display_name, start_time

    try:
        init_family_schema()
        write_heartbeat()
    except Exception as exc:
        logger.warning(
            "Family bus unavailable (%s) -- this bot runs fine without it, but "
            "ManagerBot will report it as down until the shared database is reachable.", exc,
        )
        return

    _enabled = True

    sf = _monitoring()
    if sf and hasattr(sf, "set_event_hook"):
        sf.set_event_hook(report_event_soon)

    report_event_soon("info", "startup", f"{display_name} started on {HOSTNAME}.")

    if app.job_queue is None:
        logger.warning("No job queue -- install python-telegram-bot[job-queue]. Family bus is off.")
        _enabled = False
        return

    app.job_queue.run_repeating(_send_heartbeat, interval=HEARTBEAT_SECONDS, first=HEARTBEAT_SECONDS)

    # The command bus. One repeating tick at the fast cadence; _bus_tick
    # decides on each one whether to actually poll (always while the bus is
    # active, otherwise every BUS_POLL_IDLE_SECONDS).
    app.job_queue.run_repeating(
        _bus_tick, interval=BUS_POLL_ACTIVE_SECONDS, first=BUS_POLL_ACTIVE_SECONDS
    )
    # A bot that has just started is usually about to be pinged by ManagerBot's
    # roll-call, so open with the fast cadence rather than making that first
    # command wait a full idle interval.
    mark_bus_active()

    # First pass a minute in rather than at startup, so a restart loop cannot
    # turn into a delete storm.
    app.job_queue.run_repeating(_housekeeping, interval=HOUSEKEEPING_SECONDS, first=60)
    logger.info(
        "Family bus on: heartbeat every %ss, commands polled (%ss busy / %ss idle), "
        "tidy-up every %ss.",
        HEARTBEAT_SECONDS, BUS_POLL_ACTIVE_SECONDS, BUS_POLL_IDLE_SECONDS, HOUSEKEEPING_SECONDS,
    )
