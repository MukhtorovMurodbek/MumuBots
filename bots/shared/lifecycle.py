"""Surviving a redeploy without anybody noticing.

Pushing an update to a bot on a platform like Railway means the running
container is replaced. Four separate things go wrong in that gap, and only
one of them is the obvious one:

  1. **Two bots, one token.** If the new container starts polling before the
     old one has stopped, Telegram answers one of them with 409 Conflict and
     both flap. `hold_the_lease()` below settles it with a lease row in the
     shared database: exactly one process at a time holds the right to poll,
     and the new one waits for the old one to hand it back (or for it to
     expire) rather than fighting it.

  2. **Everything the bot was in the middle of is forgotten.** user_data and
     every ConversationHandler state live in that process's memory and go
     with it. Someone who was three stickers into a pack sends the fourth
     and gets "I don't recognize that" from a bot that, thirty seconds ago,
     knew exactly what they were doing. `PostgresPersistence` writes both
     into the bot's own schema, so the new process picks the conversation up
     mid-sentence.

  3. **Work in flight is killed.** An ffmpeg encode or a download that is
     running when the platform sends SIGTERM does not come back, and the
     user is left looking at "converting..." forever. `busy()` marks those
     stretches; when a stop signal arrives, everyone inside one is told, in
     their own chat, that an update interrupted them -- once, immediately,
     instead of never.

  4. **The owner gets paged for a deploy they did on purpose.** ManagerBot
     decides a bot is down from a stale heartbeat, and a redeploy makes
     every heartbeat stale. `mark_expected_restart()` leaves a note in the
     shared database saying this one was deliberate.

What is *not* a problem, and does not need solving: updates sent while the
bot is between containers. Telegram queues undelivered updates for 24 hours
and hands them over on the next getUpdates, so as long as nothing calls
run_polling(drop_pending_updates=True), the gap costs those users latency
and nothing else.

Copied byte-identically into every bot, like shared_features.py,
family_link.py and live_message.py.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import logging
import os
import signal
import secrets
import socket
import sys
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import db
import vault

logger = logging.getLogger(__name__)

# Everything here is opt-out, one env var, and every piece of it degrades to
# "the way it worked before" rather than to a bot that will not start.
ENABLED = os.environ.get("DEPLOY_SAFETY", "on").lower() not in ("off", "0", "false", "no")

# How long a starting process waits for a previous one to let go of the poll
# lease before it starts anyway.
#
# Raised from 45s to 240s in v1.2.2R, when railway.json's overlapSeconds went
# from 0 to 30. Those two numbers are one decision, not two.
#
# overlapSeconds: 0 meant Railway stopped the old container before starting
# the new one, so the bot was away for the whole of the new container's build
# and boot -- a minute or three during which every message waited. Telegram
# holds those updates and hands them over afterwards, so nothing was ever
# lost; what it cost was a user typing into what looked like a dead bot.
#
# With this lease in place, the old container can keep serving while the new
# one builds and boots. The new one blocks HERE, in post_init -- before its
# first getUpdates, before its first heartbeat -- until Railway stops the old
# one and the old one hands the lease back. The gap becomes the drain alone,
# which is seconds.
#
# The wait therefore has to outlast the whole handover: the overlap window
# (30s) plus a worst-case drain (drainingSeconds, 120s), with room to spare.
# If it does not, the new container gives up and starts polling while the old
# one is still going, and Telegram answers 409 Conflict and splits the
# updates between them -- which is the exact failure overlapSeconds: 0
# existed to prevent. Keep DEPLOY_LEASE_WAIT_SECONDS comfortably above
# overlapSeconds + drainingSeconds if either is ever changed.
LEASE_WAIT_SECONDS = float(os.environ.get("DEPLOY_LEASE_WAIT_SECONDS", "240"))

# State older than this is not loaded at startup, and is deleted when it is
# noticed. Matches shared_features' USER_DATA_TTL_HOURS, which drops the same
# state from memory for the same reason -- a conversation nobody has touched
# for half a day is over, whatever the state machine still thinks.
STATE_TTL_HOURS = int(os.environ.get("DEPLOY_STATE_TTL_HOURS", "12"))

# How often in-memory state is written out *besides* at shutdown. A clean
# redeploy flushes on the way out and needs none of these; this is purely
# insurance against the ways a process dies without warning (an OOM kill, a
# host failure), where the alternative is losing everything since startup.
# Every pass is one small upsert per user who has changed, so the cost of
# making it more frequent is real but modest.
PERSIST_SECONDS = float(os.environ.get("DEPLOY_PERSIST_SECONDS", "180"))

_app = None
_draining = False
_drain_started: float | None = None
_lease_task: "asyncio.Task | None" = None
_lease_table_ready = False
_lease_lock = threading.Lock()
_lease_handed_back = False
_bot_id: str | None = None


# ---------------------------------------------------------------------------
# 1. One poller at a time
# ---------------------------------------------------------------------------
# Telegram allows exactly one getUpdates consumer per token. Two is not a
# degraded mode, it is a coin flip per update: 409 Conflict to whichever
# asked second, and updates split unpredictably between the two.
#
# The lease is a row in the family schema saying which process may poll for
# a bot, and until when. Its holder renews it every LEASE_RENEW_SECONDS and
# deletes it on the way out; a holder that dies without deleting it (an OOM
# kill, or SIGKILL at the end of the drain) loses it when it expires.
#
# Up to 2.1.4 the lease was a session-level advisory lock, which the server
# drops when the connection that took it closes. Behind a connection pooler
# that connection is the pooler's, not the bot's: it outlives the bot, keeps
# the lock, and is handed to whoever connects next, the new container
# included. Production showed exactly that on 2026-11-10 (Supabase's
# Supavisor): the five locks sat on pooler connections opened on 2026-10-23,
# and each new container "got" its lease within two seconds, while the old
# one was still polling. A row with an expiry works through any pooler,
# because every statement stands on its own.

LEASE_TABLE = "poll_lease"

# How long a lease lasts unless it is renewed. This is what a holder that
# died without handing the lease back costs whoever comes next: up to this
# long before it may poll. Renewed three times a period, so one slow round
# trip to the database does not lose it.
LEASE_TTL_SECONDS = float(os.environ.get("DEPLOY_LEASE_TTL_SECONDS", "30"))
LEASE_RENEW_SECONDS = LEASE_TTL_SECONDS / 3

# Who holds a lease: this machine, this process, and a token of its own. The
# token tells two lives of one process id apart -- the first process in a
# container usually gets the same number every time it starts.
_HOST = socket.gethostname()
_HOLDER = f"{_HOST}:{os.getpid()}:{secrets.token_hex(4)}"


def _lock_key(name: str) -> int:
    """A stable signed 64-bit key, which is what pg_advisory_xact_lock takes."""
    digest = hashlib.blake2b(name.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big", signed=True)


def init_lease_table() -> None:
    """Blocking. Every bot starts at once, so on the first start they would all
    create the table together, and two CREATE TABLE IF NOT EXISTS that meet can
    still fail. A transaction-level advisory lock lines them up: unlike the
    session-level kind it ends with its transaction, and a pooler keeps a
    transaction on one connection from start to finish."""
    global _lease_table_ready
    schema = _family_schema()
    with db.pooled() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (_lock_key(f"{schema}.{LEASE_TABLE}"),))
        conn.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {schema}.{LEASE_TABLE} (
                bot_id     TEXT PRIMARY KEY,
                holder     TEXT NOT NULL,
                taken_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
                expires_at TIMESTAMPTZ NOT NULL
            )
            """
        )
    _lease_table_ready = True


def _take(bot_id: str) -> bool:
    """Take the lease if it is free or has expired, or renew it if it is ours.
    True if it is ours now. Blocking."""
    schema = _family_schema()
    with db.pooled() as conn:
        row = conn.execute(
            f"""
            INSERT INTO {schema}.{LEASE_TABLE} AS lease (bot_id, holder, taken_at, expires_at)
            VALUES (%(bot)s, %(me)s, now(), now() + make_interval(secs => %(ttl)s))
            ON CONFLICT (bot_id) DO UPDATE
               SET holder = EXCLUDED.holder,
                   taken_at = CASE WHEN lease.holder = EXCLUDED.holder
                                   THEN lease.taken_at ELSE now() END,
                   expires_at = EXCLUDED.expires_at
             WHERE lease.holder = EXCLUDED.holder OR lease.expires_at <= now()
            RETURNING holder
            """,
            {"bot": bot_id, "me": _HOLDER, "ttl": LEASE_TTL_SECONDS},
        ).fetchone()
    return row is not None


def _gone(holder: str) -> bool:
    """A holder on this machine that is no longer running: a process that
    crashed and was started again in the same container. Whoever comes next
    need not wait out its expiry."""
    host, _, rest = holder.partition(":")
    pid, _, _token = rest.partition(":")
    if host != _HOST or holder == _HOLDER or not pid.isdigit():
        return False
    if int(pid) == os.getpid():
        # The same number with another token: an earlier life of this process.
        return True
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return True
    except OSError:
        # There, just not ours to signal: running.
        return False
    return False


def _take_from_the_gone(bot_id: str) -> bool:
    schema = _family_schema()
    with db.pooled() as conn:
        row = conn.execute(
            f"SELECT holder FROM {schema}.{LEASE_TABLE} WHERE bot_id = %s AND expires_at > now()",
            (bot_id,),
        ).fetchone()
        if row is None or not _gone(row[0]):
            return False
        row = conn.execute(
            f"""
            UPDATE {schema}.{LEASE_TABLE}
               SET holder = %(me)s, taken_at = now(),
                   expires_at = now() + make_interval(secs => %(ttl)s)
             WHERE bot_id = %(bot)s AND holder = %(gone)s
            RETURNING holder
            """,
            {"bot": bot_id, "me": _HOLDER, "ttl": LEASE_TTL_SECONDS, "gone": row[0]},
        ).fetchone()
    return row is not None


def _try_lease(bot_id: str) -> bool:
    """One attempt at taking (or renewing) the lease. Blocking.

    Under a lock, so that a renewal already on its way cannot put the row back
    after release_lease() has deleted it."""
    with _lease_lock:
        if _lease_handed_back:
            return False
        if not _lease_table_ready:
            init_lease_table()
        return _take(bot_id) or _take_from_the_gone(bot_id)


async def hold_the_lease(bot_id: str) -> bool:
    """Block until this process is the only one polling for `bot_id`.

    Returns True when the lease is ours. Returns False -- and lets the bot
    start anyway -- if it could not be had: an unreachable database must not
    be able to stop a bot from serving its users, and a lease nobody can
    check is no worse than the no-lease-at-all this family ran with before.
    Either way it then minds the lease in the background, renewing it or
    taking it once it is free, so that the next container waits for this one.
    """
    global _lease_task, _lease_handed_back
    if not ENABLED:
        return False
    _lease_handed_back = False
    deadline = time.monotonic() + LEASE_WAIT_SECONDS
    complained = False
    ours = False
    while True:
        try:
            if await asyncio.to_thread(_try_lease, bot_id):
                if complained:
                    logger.info("Poll lease acquired -- the previous instance has stopped.")
                ours = True
                break
        except Exception as exc:
            logger.warning("Could not check the poll lease (%s) -- starting without it.", exc)
            break
        if time.monotonic() >= deadline:
            logger.warning(
                "Another instance of %s still holds the poll lease after %.0fs. Starting "
                "anyway -- expect 409 Conflict from Telegram until it stops.",
                bot_id, LEASE_WAIT_SECONDS,
            )
            break
        if not complained:
            logger.info("Waiting for the previous instance of %s to let go of the poll lease...", bot_id)
            complained = True
        await asyncio.sleep(1.0)
    if _lease_task is None or _lease_task.done():
        _lease_task = asyncio.get_running_loop().create_task(_mind_the_lease(bot_id, ours))
    return ours


async def _mind_the_lease(bot_id: str, ours: bool) -> None:
    """For as long as this process runs: renew the lease, or take it once it
    is free."""
    failing = False
    while True:
        await asyncio.sleep(LEASE_RENEW_SECONDS)
        try:
            now_ours = await asyncio.to_thread(_try_lease, bot_id)
        except Exception as exc:
            if not failing:
                logger.warning("Could not renew the poll lease (%s); trying again.", exc)
            failing = True
            continue
        failing = False
        if ours and not now_ours:
            logger.warning(
                "Another instance of %s has taken the poll lease: this one went %.0fs without "
                "renewing it. Expect 409 Conflict from Telegram until one of them stops.",
                bot_id, LEASE_TTL_SECONDS,
            )
        elif now_ours and not ours:
            logger.info("Poll lease for %s taken -- the instance that held it has stopped.", bot_id)
        ours = now_ours


def release_lease() -> None:
    """Hand the lease back on the way out, once polling has stopped, so the
    next container need not wait for it to expire. Blocking."""
    global _lease_task, _lease_handed_back
    if _lease_task is not None:
        _lease_task.cancel()
        _lease_task = None
    with _lease_lock:
        _lease_handed_back = True
        if not ENABLED or _bot_id is None or not _lease_table_ready:
            return
        try:
            with db.pooled() as conn:
                conn.execute(
                    f"DELETE FROM {_family_schema()}.{LEASE_TABLE} WHERE bot_id = %s AND holder = %s",
                    (_bot_id, _HOLDER),
                )
        except Exception:
            logger.debug("Could not hand the poll lease back; it expires on its own", exc_info=True)


# ---------------------------------------------------------------------------
# 2. State that outlives the process
# ---------------------------------------------------------------------------
# One table in the bot's own schema (search_path is already pointed there by
# db.py, so this needs no qualification and cannot collide with a sibling's).
#
# JSONB rather than pickle, on purpose. It is readable from ManagerBot's /sql,
# it can be pruned by age with plain SQL, and it cannot execute anything on
# the way back in. The price is that values which are not JSON -- notably
# whole telegram.Message objects -- are dropped instead of stored; every
# caller in this family already treats those as in-memory-only, and the one
# place that keeps one (AnonBot's held message) has said "expired" when it is
# missing since the day it was written.

STATE_TABLE = "runtime_state"

# What the table's rows are sealed under (shared/vault.py).
STATE_PURPOSE = "lifecycle.state"
WAITLIST_PURPOSE = "lifecycle.waitlist"

# Sealed since 2.1.2CHL. user_data holds a person's language, a half-built
# pack's name, an AnonBot inbox token; a conversation's key says who is in
# which chat. So a row keeps neither in the clear:
#   key    the same shape as before, each id replaced by its pseudonym, so the
#          primary key still finds a person's row and /deletemydata can still
#          delete it (forget_user);
#   value  {"sealed": <base64>}, which opens to {"key": <the original key>,
#          "data": <the original value>}.
# Reading opens every row and hands back the original key and value, so
# nothing above this block ever sees a pseudonym. A row that does not open,
# or whose sealed key is not the one it sits under, is skipped with a warning
# (the table and the kind, never a key or a value).


def _pseudo_key(kind: str, key: str) -> str:
    """The key a row is stored under. ValueError when `key` is not one this
    table knows how to hide (a part that is not a number, a part that already
    is a pseudonym, a kind we do not store) -- never a plain key by default."""
    if kind == "user":
        return str(vault.pseudonym(int(key)))
    if kind == "conversation":
        name, bar, pair = key.partition("|")
        if not bar:
            raise ValueError("a conversation key has the form name|chat:user")
        return f"{name}|" + ":".join(str(vault.pseudonym(int(part))) for part in pair.split(":"))
    raise ValueError(f"unknown kind of saved state: {kind!r}")


def _seal_state(kind: str, key: str, value) -> "tuple[str, dict]":
    """(the key to store, the value to store) for one row."""
    stored_key = _pseudo_key(kind, key)
    blob = vault.seal_json({"key": key, "data": value}, STATE_PURPOSE)
    return stored_key, {"sealed": base64.b64encode(blob).decode("ascii")}


def unseal_state(kind: str, stored_key: str, stored_value) -> "tuple[str, object]":
    """(the original key, the original value) of a stored row. VaultError when
    the row is not sealed, does not open, or has been moved to another key."""
    if not isinstance(stored_value, dict) or not isinstance(stored_value.get("sealed"), str):
        raise vault.VaultError("A saved state row is not sealed")
    try:
        blob = base64.b64decode(stored_value["sealed"], validate=True)
    except (ValueError, binascii.Error):
        raise vault.VaultError("A saved state row is not sealed") from None
    try:
        payload = vault.open_json(blob, STATE_PURPOSE)
    except ValueError:                  # it opened, but is not the JSON we sealed
        raise vault.VaultError("A saved state row is malformed") from None
    try:
        key, data = payload["key"], payload["data"]
        belongs = _pseudo_key(kind, key) == stored_key
    except (KeyError, TypeError, ValueError, AttributeError):
        raise vault.VaultError("A saved state row is malformed") from None
    if not belongs:
        raise vault.VaultError("A saved state row does not belong to its key")
    return key, data


def init_state_table() -> None:
    with db.pooled() as conn:
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {STATE_TABLE} (
                kind       TEXT NOT NULL,
                key        TEXT NOT NULL,
                value      JSONB NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                PRIMARY KEY (kind, key)
            )
            """
        )
        conn.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{STATE_TABLE}_age "
            f"ON {STATE_TABLE} (updated_at)"
        )
        conn.commit()


def _json_safe(value):
    """`value` if it survives a JSON round trip unchanged, else None.

    Tuples are the one thing deliberately *not* accommodated: JSON has no
    tuple, so one would come back as a list and quietly change type under
    code that unpacked it. Nothing in this family keeps a tuple in user_data
    that has to survive a restart, so refusing them is safer than converting
    them.
    """
    import json

    try:
        encoded = json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return None
    if json.loads(encoded) != value:
        return None
    return value


def _clean(data: dict) -> dict:
    out = {}
    for key, value in data.items():
        if not isinstance(key, str):
            continue
        kept = _json_safe(value)
        if kept is None and value is not None:
            logger.debug("Not persisting user_data[%r]: not JSON", key)
            continue
        out[key] = value
    return out


def _write_state(rows: list[tuple[str, str, dict]]) -> None:
    if not rows:
        return
    from psycopg.types.json import Jsonb

    sealed = []
    for kind, key, value in rows:
        try:
            stored_key, stored_value = _seal_state(kind, key, value)
        except ValueError:
            logger.warning("%s: not saving a %s row whose key cannot be hidden.", STATE_TABLE, kind)
            continue
        sealed.append((kind, stored_key, Jsonb(stored_value)))
    if not sealed:
        return
    with db.pooled() as conn:
        with conn.cursor() as cur:
            cur.executemany(
                f"""
                INSERT INTO {STATE_TABLE} (kind, key, value, updated_at)
                VALUES (%s, %s, %s, now())
                ON CONFLICT (kind, key) DO UPDATE
                SET value = excluded.value, updated_at = excluded.updated_at
                """,
                sealed,
            )
        conn.commit()


def _delete_state(rows: list[tuple[str, str]]) -> None:
    stored = []
    for kind, key in rows:
        try:
            stored.append((kind, _pseudo_key(kind, key)))
        except ValueError:
            continue        # never saved under a key we could hide, so there is nothing to delete
    if not stored:
        return
    with db.pooled() as conn:
        with conn.cursor() as cur:
            cur.executemany(f"DELETE FROM {STATE_TABLE} WHERE kind = %s AND key = %s", stored)
        conn.commit()


def _read_state(kind: str) -> list[tuple[str, dict]]:
    """Everything of one kind that is still young enough to mean something,
    deleting the rest on the way past -- the only pruning this table needs,
    and it happens once per process start. (key, value) as they were saved,
    the ids real again; a row that does not open is skipped."""
    with db.pooled() as conn:
        conn.execute(
            f"DELETE FROM {STATE_TABLE} WHERE updated_at < now() - make_interval(hours => %s)",
            (STATE_TTL_HOURS,),
        )
        cur = conn.execute(f"SELECT key, value FROM {STATE_TABLE} WHERE kind = %s", (kind,))
        rows = cur.fetchall()
        conn.commit()
    opened = []
    for stored_key, stored_value in rows:
        try:
            opened.append(unseal_state(kind, stored_key, stored_value))
        except vault.VaultError:
            logger.warning("%s: a saved %s row did not open -- skipping it.", STATE_TABLE, kind)
    return opened


def _key_usable() -> bool:
    import family_link

    return family_link.vault_usable()


def forget_user(user_id: int) -> int:
    if not _key_usable():
        return 0
    who = vault.pseudonym(user_id)
    removed = 0
    with db.pooled() as conn:
        if conn.execute("SELECT to_regclass(%s)", (STATE_TABLE,)).fetchone()[0] is not None:
            cur = conn.execute(
                f"DELETE FROM {STATE_TABLE} WHERE (kind = 'user' AND key = %s) "
                f"OR (kind = 'conversation' AND (key LIKE %s OR key LIKE %s))",
                (str(who), f"%:{who}", f"%|{who}"),
            )
            removed += cur.rowcount or 0
        waitlist = f"{_family_schema()}.{WAITLIST_TABLE}"
        if conn.execute("SELECT to_regclass(%s)", (waitlist,)).fetchone()[0] is not None:
            cur = conn.execute(f"DELETE FROM {waitlist} WHERE user_id = %s", (who,))
            removed += cur.rowcount or 0
        conn.commit()
    return removed


try:
    from telegram.ext import BasePersistence, PersistenceInput
except ImportError:  # pragma: no cover -- only if PTB is missing entirely
    BasePersistence = object
    PersistenceInput = None


class PostgresPersistence(BasePersistence):
    """user_data and conversation states, in the bot's own Postgres schema.

    Written at shutdown (python-telegram-bot calls flush() there) and every
    PERSIST_SECONDS in between, not on every update: the point is to survive
    a restart, and a restart gives ample warning. Conversation states are the
    exception -- they are single small rows, they change rarely, and being
    one state behind is the difference between picking a conversation up and
    dropping it, so those are written as they happen.
    """

    def __init__(self):
        super().__init__(
            store_data=PersistenceInput(
                bot_data=False, chat_data=False, user_data=True, callback_data=False
            ),
            # python-telegram-bot's own timer only *collects* changes (it
            # calls update_user_data and stops there); the writing is driven
            # from install()'s job below, so PTB's pass is pushed far out
            # rather than doing the same deep copies twice a period.
            update_interval=max(PERSIST_SECONDS * 10, 600),
        )
        self._user_data: dict[int, dict] = {}
        self._dirty_users: set[int] = set()
        self._dropped_users: set[int] = set()
        self._conversations: dict[str, dict] = {}
        self._conversations_loaded = False

    # ---- loading, once, at startup ----

    async def get_user_data(self) -> dict[int, dict]:
        try:
            rows = await asyncio.to_thread(_read_state, "user")
        except Exception:
            logger.warning("Could not read saved user state; starting with none.", exc_info=True)
            return {}
        loaded = {}
        for key, value in rows:
            try:
                loaded[int(key)] = dict(value)
            except (TypeError, ValueError):
                continue
        self._user_data = loaded
        if loaded:
            logger.info("Restored in-progress state for %d user(s).", len(loaded))
        return loaded

    async def get_conversations(self, name: str) -> dict:
        if not self._conversations_loaded:
            self._conversations_loaded = True
            try:
                rows = await asyncio.to_thread(_read_state, "conversation")
            except Exception:
                logger.warning("Could not read saved conversations; starting with none.", exc_info=True)
                rows = []
            for key, value in rows:
                handler, _, pair = key.partition("|")
                try:
                    # Rebuilt with the same arity it was stored with: a
                    # ConversationHandler's key is (chat, user) by default but
                    # (user,) with per_chat=False, and handing back the wrong
                    # shape would silently match nothing.
                    conv_key = tuple(int(part) for part in pair.split(":"))
                except ValueError:
                    continue
                self._conversations.setdefault(handler, {})[conv_key] = value.get("state")
        restored = self._conversations.get(name, {})
        if restored:
            logger.info("Restored %d open %s conversation(s).", len(restored), name)
        return dict(restored)

    async def get_bot_data(self):
        return {}

    async def get_chat_data(self):
        return {}

    async def get_callback_data(self):
        return None

    # ---- collecting changes ----

    async def update_user_data(self, user_id: int, data: dict) -> None:
        self._user_data[user_id] = data
        self._dirty_users.add(user_id)
        self._dropped_users.discard(user_id)

    async def update_conversation(self, name: str, key: tuple, new_state) -> None:
        conversations = self._conversations.setdefault(name, {})
        if new_state is None:
            conversations.pop(key, None)
        else:
            conversations[key] = new_state
        row_key = f"{name}|" + ":".join(str(part) for part in key)
        try:
            if new_state is None:
                await asyncio.to_thread(_delete_state, [("conversation", row_key)])
            else:
                await asyncio.to_thread(_write_state, [("conversation", row_key, {"state": new_state})])
        except Exception:
            logger.debug("Could not persist a conversation state", exc_info=True)

    async def drop_user_data(self, user_id: int) -> None:
        self._user_data.pop(user_id, None)
        self._dirty_users.discard(user_id)
        self._dropped_users.add(user_id)

    async def update_bot_data(self, data) -> None:
        return None

    async def update_chat_data(self, chat_id: int, data) -> None:
        return None

    async def update_callback_data(self, data) -> None:
        return None

    async def drop_chat_data(self, chat_id: int) -> None:
        return None

    async def refresh_user_data(self, user_id: int, user_data: dict) -> None:
        return None

    async def refresh_chat_data(self, chat_id: int, chat_data) -> None:
        return None

    async def refresh_bot_data(self, bot_data) -> None:
        return None

    # ---- writing them out ----

    async def flush(self) -> None:
        dirty, dropped = set(self._dirty_users), set(self._dropped_users)
        writes, deletes = [], [("user", str(u)) for u in dropped]
        for user_id in dirty:
            kept = _clean(self._user_data.get(user_id) or {})
            if kept:
                writes.append(("user", str(user_id), kept))
            else:
                # Their state emptied out -- a /cancel, a /done, or nothing
                # worth keeping in the first place. Without this the row
                # would be restored as an empty dict forever.
                deletes.append(("user", str(user_id)))
        self._dirty_users.clear()
        self._dropped_users.clear()
        if not writes and not deletes:
            return
        try:
            await asyncio.to_thread(_write_state, writes)
            await asyncio.to_thread(_delete_state, deletes)
        except Exception:
            logger.warning("Could not save in-progress state.", exc_info=True)
            # Nothing that was in this pass is known to be saved, so it is all
            # still to do: kept for the next pass rather than forgotten until
            # the person next does something. Anyone who changed, or was
            # dropped, while the write was failing is as they now are, not as
            # they were.
            self._dirty_users |= dirty - self._dropped_users
            self._dropped_users |= dropped - self._dirty_users


# ---------------------------------------------------------------------------
# 3. Work that is in flight when the platform says stop
# ---------------------------------------------------------------------------
# python-telegram-bot already waits for running handlers before it shuts down,
# so short work finishes on its own. The problem is the long kind: a video
# encode or a download can outlast whatever grace period the platform allows
# before SIGKILL, and the user is left watching a "converting..." that will
# never change.
#
# There is no way to make that work survive. There is a way to make sure the
# user hears about it, which is all busy() does: it keeps a note of who is
# waiting on what, and the stop handler answers all of them at once.

# chat id -> the message to send there if this work does not survive. The
# message rather than a label, because this file is byte-identical in five
# bots and four of them are trilingual: the caller has the user's language in
# hand and this file has no business importing i18n to go and find it.
_in_flight: dict[int, tuple[int, str]] = {}
# When each stretch began, so a soft stop that finds one can say how long it has run.
_in_flight_since: dict[int, float] = {}
_next_ticket = 0


def is_draining() -> bool:
    """True once a stop signal has arrived. Check it before starting
    anything slow -- see the guard in each bot's media handlers."""
    return _draining


@asynccontextmanager
async def busy(chat_id: int, if_interrupted: str):
    """Mark a stretch of slow work, so a redeploy in the middle of it ends
    with an explanation rather than with silence.

        async with lifecycle.busy(chat_id, i18n.t(lang, "restarting_send_again")):
            ...

    `if_interrupted` is the finished, translated message to send if a stop
    signal arrives while this is running -- and nothing at all otherwise.
    """
    global _next_ticket
    _next_ticket += 1
    ticket = _next_ticket
    _in_flight[ticket] = (chat_id, if_interrupted)
    _in_flight_since[ticket] = time.monotonic()
    try:
        yield
    finally:
        _in_flight.pop(ticket, None)
        _in_flight_since.pop(ticket, None)


async def _tell_the_interrupted(bot) -> None:
    """One message each, to everyone who was mid-something. Best-effort and
    strictly time-boxed: this runs inside the platform's shutdown grace
    period, and holding that up to retry a send helps nobody."""
    waiting = list(_in_flight.values())
    if not waiting:
        return
    logger.info("Telling %d user(s) that an update interrupted them.", len(waiting))
    for chat_id, message in waiting:
        try:
            await asyncio.wait_for(
                bot.send_message(chat_id=chat_id, text=message), timeout=5,
            )
        except Exception:
            logger.debug("Could not warn %s about the restart", chat_id, exc_info=True)


# ---------------------------------------------------------------------------
# 4. A deploy is not a crash
# ---------------------------------------------------------------------------
# ManagerBot calls a bot down when its heartbeat goes stale, which is exactly
# what a redeploy does to it. Leaving a note in the shared settings table
# lets the watchdog tell "I did this on purpose" from "something is wrong"
# -- see ManagerBot's watchdog.

RESTART_NOTE_PREFIX = "restarting:"


def mark_expected_restart(bot_id: str) -> None:
    try:
        import family_link

        with db.pooled() as conn:
            conn.execute(
                f"""
                INSERT INTO {family_link.FAMILY_SCHEMA}.settings (key, value)
                VALUES (%s, %s)
                ON CONFLICT (key) DO UPDATE SET value = excluded.value
                """,
                (RESTART_NOTE_PREFIX + bot_id, datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()
    except Exception:
        logger.debug("Could not leave a redeploy note", exc_info=True)


# ---------------------------------------------------------------------------
# 5. An update the owner announces before it happens
# ---------------------------------------------------------------------------
# Sections 1-4 are about a redeploy nobody was warned about. This one is for
# the deploy you know is coming: the owner puts a bot into maintenance from
# ManagerBot, and until they say otherwise it declines to *start* anything a
# restart would throw away, says roughly how long it expects to be, and
# writes down who it turned away so they can be told when it is over.
#
# Three properties, none of which a module-level flag would have:
#
#   It has to survive the restart it is announcing. One update is often
#   several deploys, so the flag lives in family.settings and is read back at
#   startup rather than being held in the process that is about to end.
#
#   It has to be free to check. Every incoming message asks "are we paused?",
#   so the answer is a cached flag -- set at startup and whenever ManagerBot
#   says so -- and not a query per update.
#
#   The people turned away have to outlive the process too. That is what
#   family.update_waitlist is: one row per person told to come back, removed
#   when they are told they can.
#
# The announced time is an estimate and nothing more. Maintenance ends when
# the owner ends it, never on a timer -- because the reason they wanted a
# manual end is exactly that one update can take several deploys, and a timer
# would let the bot reopen between two of them. Once the estimate has passed
# the bots stop quoting a number rather than start quoting a wrong one.

MAINTENANCE_KEY_PREFIX = "maintenance:"
WAITLIST_TABLE = "update_waitlist"

# What ManagerBot's /pause promises when it is not told a number.
DEFAULT_MAINTENANCE_MINUTES = int(os.environ.get("DEPLOY_MAINTENANCE_MINUTES", "10"))

_maintenance_on = False
_maintenance_until: "datetime | None" = None


def _family_schema() -> str:
    import family_link

    return family_link.FAMILY_SCHEMA


def init_waitlist_table() -> None:
    schema = _family_schema()
    with db.pooled() as conn:
        conn.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {schema}.{WAITLIST_TABLE} (
                bot_id  TEXT   NOT NULL,
                user_id BIGINT NOT NULL,
                chat_id BIGINT NOT NULL,
                held_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                who_sealed BYTEA,
                PRIMARY KEY (bot_id, user_id)
            )
            """
        )
        # user_id and chat_id hold pseudonyms since 2.1.2CHL; the real ids are
        # in who_sealed, which is how take_held() knows whom to tell. A table
        # made by an older version gets the column here and nothing else is
        # rewritten (scripts/encrypt_personal_data.py empties the old rows).
        import family_link

        if family_link._column_missing(conn, WAITLIST_TABLE, "who_sealed"):
            conn.execute(f"ALTER TABLE {schema}.{WAITLIST_TABLE} ADD COLUMN IF NOT EXISTS who_sealed BYTEA")
        conn.commit()


def _read_maintenance(bot_id: str) -> "tuple[bool, datetime | None]":
    """(is it on, what was promised). Blocking."""
    schema = _family_schema()
    with db.pooled() as conn:
        cur = conn.execute(
            f"SELECT value FROM {schema}.settings WHERE key = %s",
            (MAINTENANCE_KEY_PREFIX + bot_id,),
        )
        row = cur.fetchone()
    if row is None or not row[0]:
        return False, None
    try:
        return True, datetime.fromisoformat(row[0])
    except ValueError:
        # The row is the flag; a value that will not parse costs the estimate
        # and nothing else.
        return True, None


def refresh_maintenance(bot_id: str) -> bool:
    """Read the flag out of the shared database into this process. Blocking.

    Called at startup and after ManagerBot changes it -- the only two moments
    it can have changed, which is why nothing else has to ask.
    """
    global _maintenance_on, _maintenance_until
    try:
        _maintenance_on, _maintenance_until = _read_maintenance(bot_id)
    except Exception:
        logger.debug("Could not read the maintenance flag", exc_info=True)
        return _maintenance_on
    return _maintenance_on


def begin_maintenance(bot_id: str, minutes: int) -> "datetime":
    """Blocking. Returns the moment being promised, so the caller can say it
    back to the owner in the same words the users will get."""
    global _maintenance_on, _maintenance_until
    until = datetime.now(timezone.utc) + timedelta(minutes=max(1, minutes))
    schema = _family_schema()
    with db.pooled() as conn:
        conn.execute(
            f"""
            INSERT INTO {schema}.settings (key, value) VALUES (%s, %s)
            ON CONFLICT (key) DO UPDATE SET value = excluded.value
            """,
            (MAINTENANCE_KEY_PREFIX + bot_id, until.isoformat()),
        )
        conn.commit()
    _maintenance_on, _maintenance_until = True, until
    return until


def end_maintenance(bot_id: str) -> None:
    """Blocking. Deletes the flag rather than blanking it, so "paused" is the
    presence of a row and there is no second way to spell "off"."""
    global _maintenance_on, _maintenance_until
    schema = _family_schema()
    with db.pooled() as conn:
        conn.execute(
            f"DELETE FROM {schema}.settings WHERE key = %s",
            (MAINTENANCE_KEY_PREFIX + bot_id,),
        )
        conn.commit()
    _maintenance_on, _maintenance_until = False, None


def in_maintenance() -> bool:
    return _maintenance_on


def maintenance_minutes_left() -> "int | None":
    """Whole minutes still promised, or None once the estimate has run out.

    None does not mean the pause is over -- only the owner ends that. It
    means there is no honest number to quote any more, and the bots say
    "shortly" instead of a figure they have already missed.
    """
    if not _maintenance_on or _maintenance_until is None:
        return None
    left = (_maintenance_until - datetime.now(timezone.utc)).total_seconds()
    if left <= 0:
        return None
    # Rounded up, so 90 seconds left is "2 minutes" rather than "1" -- but a
    # real ceiling, not seconds//60 + 1, which turns a flat ten minutes into
    # eleven the instant it is announced.
    return max(1, -(-int(left) // 60))


def is_paused() -> bool:
    """True when nothing slow should be *started*: a stop signal has already
    arrived, the owner has announced an update, or a soft stop is checking
    whether anybody is using the bot (see "Soft stop" at the end of this file).

    The check every long handler makes. is_draining() is still the narrower
    question -- "is this process going away right now" -- and is what the
    shutdown path itself uses.
    """
    return _draining or _maintenance_on or _closing


WAITLIST_KEEP_DAYS = int(os.environ.get("DEPLOY_WAITLIST_KEEP_DAYS") or 7)


def hold_for_update(user_id: int, chat_id: int, bot_id: "str | None" = None) -> None:
    """Remember that this person was turned away, so /finishupdates can tell
    them it is over. Blocking; best-effort.

    One row per person, not per attempt: someone who tries four times during
    an update is one person to apologise to, not four.
    """
    bot_id = bot_id or _bot_id
    if not bot_id or not _key_usable():
        return
    schema = _family_schema()
    try:
        who = vault.seal_json({"user": user_id, "chat": chat_id}, WAITLIST_PURPOSE)
        with db.pooled() as conn:
            conn.execute(
                f"""
                INSERT INTO {schema}.{WAITLIST_TABLE} (bot_id, user_id, chat_id, who_sealed)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (bot_id, user_id) DO NOTHING
                """,
                (bot_id, vault.pseudonym(user_id), vault.pseudonym(chat_id), who),
            )
            conn.commit()
    except Exception:
        logger.debug("Could not add somebody to the update waitlist", exc_info=True)


def take_held(bot_id: str) -> "list[tuple[int, int]]":
    if not _key_usable():
        logger.warning("family.%s: the data key is not usable, so the people held for %s stay "
                       "held until it is.", WAITLIST_TABLE, bot_id)
        return []
    schema = _family_schema()
    with db.pooled() as conn:
        cur = conn.execute(
            f"DELETE FROM {schema}.{WAITLIST_TABLE} WHERE bot_id = %s "
            f"AND held_at > now() - make_interval(days => %s) "
            f"RETURNING user_id, chat_id, who_sealed",
            (bot_id, WAITLIST_KEEP_DAYS),
        )
        rows = cur.fetchall()
        conn.commit()
    held = []
    for user_pseudonym, chat_pseudonym, sealed in rows:
        try:
            who = vault.open_json(sealed, WAITLIST_PURPOSE)
            user_id, chat_id = int(who["user"]), int(who["chat"])
            if vault.pseudonym(user_id) != user_pseudonym or vault.pseudonym(chat_id) != chat_pseudonym:
                raise vault.VaultError("A sealed waitlist entry does not belong to its row")
        except (vault.VaultError, KeyError, TypeError, ValueError, AttributeError):
            logger.warning("family.%s: a held row for %s did not open -- skipping it.", WAITLIST_TABLE, bot_id)
            continue
        held.append((user_id, chat_id))
    return held


def prune_waitlist() -> int:
    schema = _family_schema()
    with db.pooled() as conn:
        if conn.execute("SELECT to_regclass(%s)", (f"{schema}.{WAITLIST_TABLE}",)).fetchone()[0] is None:
            return 0
        removed = conn.execute(
            f"DELETE FROM {schema}.{WAITLIST_TABLE} WHERE held_at < now() - make_interval(days => %s)",
            (WAITLIST_KEEP_DAYS,),
        ).rowcount or 0
        conn.commit()
    return removed


def in_flight() -> "list[tuple[int, str]]":
    """Who is mid-something right now, as (chat_id, the message they would
    get if it were interrupted) -- one entry per stretch of slow work.

    The same registry the shutdown path reads, exposed so the owner can warn
    these people *before* pressing deploy rather than as the door closes.
    """
    return list(_in_flight.values())


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------

def persistence():
    """Pass to ApplicationBuilder().persistence(). None when deploy safety is
    off, in which case every ConversationHandler must also drop
    persistent=True -- which is what persistent() below is for."""
    if not ENABLED:
        return None
    if not _key_usable():
        logger.warning("No saved state: the data key is not usable, so conversations will not "
                       "survive a restart until it is.")
        return None
    try:
        init_state_table()
    except Exception as exc:
        logger.warning(
            "No state table (%s) -- conversations will not survive a restart, "
            "which is how this bot behaved before.", exc,
        )
        return None
    return PostgresPersistence()


_persistence_on = False


def persistent() -> bool:
    """For ConversationHandler(persistent=..., name=...). Adding a persistent
    handler to an Application that has no persistence is an error, so the two
    have to agree."""
    return _persistence_on


def _on_stop_signal(sig: int) -> None:
    global _draining, _drain_started
    if _draining:
        return
    _draining = True
    _drain_started = time.monotonic()
    logger.info("Got %s -- draining. Finishing what's in flight, then stopping.",
                signal.Signals(sig).name if hasattr(signal, "Signals") else sig)
    app = _app
    if app is None:
        return
    asyncio.get_running_loop().create_task(_drain_and_stop(app))


async def _drain_and_stop(app) -> None:
    # Off the event loop: this is a database write, and the signal handler
    # that got us here runs *in* the loop -- blocking it would hold up the
    # very shutdown it is announcing.
    if _bot_id:
        try:
            await asyncio.to_thread(mark_expected_restart, _bot_id)
        except Exception:
            logger.debug("Could not leave a redeploy note", exc_info=True)
    try:
        await _wait_for_work(SIGTERM_WAIT_SECONDS)
    except Exception:
        logger.debug("Could not wait for work in progress", exc_info=True)
    try:
        await _tell_the_interrupted(app.bot)
    except Exception:
        logger.debug("Drain announcement failed", exc_info=True)
    try:
        app.stop_running()
    except RuntimeError:
        # A stop signal that arrived before run_polling() had finished
        # starting. Nothing is serving anyone yet, so there is nothing to
        # drain gracefully -- just go.
        logger.info("Stopped before startup finished; exiting.")
        os._exit(0)


async def optional(name: str, step) -> None:
    try:
        result = step() if callable(step) else step
        if asyncio.iscoroutine(result):
            await result
    except Exception:
        logger.warning("Startup step %r failed; the bot starts without it.", name, exc_info=True)


def optional_now(name: str, step, *args):
    try:
        return step(*args)
    except Exception:
        logger.warning("Startup step %r failed; the bot starts without it.", name, exc_info=True)
        return None


def can_handle_signals() -> bool:
    return sys.platform != "win32" and hasattr(signal, "SIGTERM")


def polling_kwargs(**extra) -> dict:
    """Wrap each bot's run_polling() arguments.

    Where we can take the stop signals ourselves we do, because
    python-telegram-bot's own handler goes straight to shutdown and there is
    no way to get a word in before it -- and the word is the whole point:
    "an update interrupted you, send it again". Where we cannot (Windows has
    no loop.add_signal_handler), the key is simply left out and PTB's own
    default handling stands.
    """
    if ENABLED and can_handle_signals():
        extra["stop_signals"] = ()
    return extra


async def _persist_job(context) -> None:
    """Collect what changed and write it out. Both halves are needed: PTB's
    update_persistence() is what copies live user_data into the persistence
    object, and flush() is what puts it in Postgres."""
    app = _app
    if app is None or app.persistence is None:
        return
    try:
        await app.update_persistence()
        await app.persistence.flush()
    except Exception:
        logger.debug("Periodic state save failed; the next pass retries", exc_info=True)


def install(app, bot_id: str, admin_ids=()) -> None:
    """One line in each bot's main(), next to family_link.attach().

    Everything it wires is best-effort: a bot whose database is unreachable
    keeps running exactly as it did before any of this existed. `admin_ids`
    are the owners, whose own messages do not count as somebody using the bot
    when a soft stop asks.
    """
    global _app, _bot_id, _persistence_on, _admins
    _app, _bot_id = app, bot_id
    _admins = frozenset(int(x) for x in admin_ids or ())
    _persistence_on = app.persistence is not None
    if not ENABLED:
        logger.info("Deploy safety off (DEPLOY_SAFETY=off).")
        return
    if not _persistence_on:
        logger.warning("Running without persistence -- open conversations will not survive a restart.")
    elif app.job_queue is not None:
        app.job_queue.run_repeating(_persist_job, interval=PERSIST_SECONDS, first=PERSIST_SECONDS)
    logger.info(
        "Deploy safety on: single-poller lease, state in %s, in-flight work announced on SIGTERM.",
        STATE_TABLE,
    )


async def on_start(bot_id: str) -> None:
    """Register as (part of) each bot's post_init, before it starts polling.

    Two jobs: make sure no previous container is still polling this token,
    and take the stop signals so a redeploy can say goodbye properly.
    """
    global _bot_id
    _bot_id = bot_id
    if not ENABLED:
        return
    await hold_the_lease(bot_id)

    # Running again, so whatever stop the last /shutdown recorded is over.
    try:
        await asyncio.to_thread(_set_note, STOPPED_NOTE_PREFIX + bot_id, "")
    except Exception:
        logger.debug("Could not clear the stopped-on-purpose note", exc_info=True)

    # An update is usually several deploys, so a container that has just come
    # up has to find out whether it is still inside one before it starts
    # taking work it would only have to throw away again.
    try:
        await asyncio.to_thread(init_waitlist_table)
        if await asyncio.to_thread(refresh_maintenance, bot_id):
            logger.info("Still in maintenance -- not starting new long work yet.")
    except Exception:
        logger.debug("Could not read the maintenance flag at startup", exc_info=True)

    if not can_handle_signals():
        return

    # polling_kwargs() has already told python-telegram-bot not to install its
    # own handlers, so if none of these land the process would be deaf to
    # SIGTERM and the platform would go straight to SIGKILL. The plain
    # signal.signal() fallback exists for that case alone.
    loop = asyncio.get_running_loop()
    wanted = (signal.SIGTERM, signal.SIGINT)
    installed = 0
    for sig in wanted:
        try:
            loop.add_signal_handler(sig, _on_stop_signal, sig)
            installed += 1
        except (NotImplementedError, RuntimeError):
            logger.debug("Could not take signal %s through the loop.", sig)
    if installed:
        return
    for sig in wanted:
        try:
            signal.signal(sig, lambda number, frame: loop.call_soon_threadsafe(_on_stop_signal, number))
        except (ValueError, OSError):
            logger.warning("Nothing could take signal %s -- shutdown will not be graceful.", sig)


async def on_stop(application) -> None:
    """Register as the *first* thing in each bot's post_stop -- before
    flush_on_shutdown(), which closes the connection pool these writes go
    through.

    python-telegram-bot flushes persistence itself a moment later, during
    shutdown(). Doing it here as well is what makes the ordering explicit
    instead of load-bearing: by the time the pool closes, the state is
    already written.
    """
    if application.persistence is not None:
        try:
            await application.update_persistence()
            await application.persistence.flush()
            logger.info("In-progress state saved -- the next start picks it up.")
        except Exception:
            logger.warning("Could not save in-progress state on the way out.", exc_info=True)
    release_lease()


# --- Soft stop -------------------------------------------------------------------------------------
# The only way a service is meant to go down: ManagerBot's /shutdown (exit 0, it stays down) or
# /restart (exit 1, the host starts it again). It first closes the door -- every bot in the process
# declines new slow work, with no await in between -- then looks for anybody using any of them, and
# reopens and refuses if it finds one. "In use", for one bot (usage_here):
#   - slow work in progress: a busy() stretch (StickerBot, DownloaderBot) or a registered work source
#     (ConvertBot's conversions, queued or running);
#   - updates received and not yet handled;
#   - somebody other than the owners sent it anything -- a message, a button, a payment -- in the
#     last QUIET_MINUTES;
#   - somebody other than the owners is part-way through a conversation (a pack being built, a reply
#     being written) and took a step in the last CONVERSATION_MINUTES;
# and, for the process, a bus command still running on any of its bots (a broadcast, a gift, a dump).
# A stop signal from the host (a deploy, Railway's Restart/Remove) cannot be refused; it waits up to
# SIGTERM_WAIT_SECONDS for work in progress instead, inside the host's draining window.

QUIET_MINUTES = float(os.environ.get("SOFTSTOP_QUIET_MINUTES", "5"))
CONVERSATION_MINUTES = float(os.environ.get("SOFTSTOP_CONVERSATION_MINUTES", "20"))
STOP_REQUEST_MAX_AGE_SECONDS = 300
STOP_DELAY_SECONDS = 3.0
SIGTERM_WAIT_SECONDS = float(os.environ.get("DEPLOY_SIGTERM_WAIT_SECONDS", "60"))
STOPPED_NOTE_PREFIX = "stopped:"

_closing = False
_admins: frozenset = frozenset()
_work_sources: dict = {}
_stop_requested = False


def add_work_source(name: str, count) -> None:
    """`count()` -> how many pieces of slow work of this kind are queued or running now."""
    _work_sources[name] = count


def _work_counts() -> "dict[str, int]":
    counts = {}
    if _in_flight:
        counts["slow work"] = len(_in_flight)
    for name, count in _work_sources.items():
        try:
            n = int(count() or 0)
        except Exception:
            logger.debug("Work source %s could not be counted", name, exc_info=True)
            n = 0
        if n:
            counts[name] = n
    return counts


async def _wait_for_work(limit: float) -> None:
    """Until nothing slow is in progress here, or `limit` seconds."""
    deadline = time.monotonic() + max(0.0, limit)
    announced = False
    while _work_counts() and time.monotonic() < deadline:
        if not announced:
            announced = True
            logger.info("Waiting up to %.0fs for work in progress: %s", limit, _work_counts())
        await asyncio.sleep(1.0)


def _minutes(seconds: float) -> str:
    seconds = max(0.0, seconds)
    return f"{seconds:.0f}s" if seconds < 90 else f"{seconds / 60:.0f}m"


def _conversation_users(app) -> "list[tuple]":
    """The keys of every open conversation of every ConversationHandler in the app."""
    from telegram.ext import ConversationHandler
    keys = []
    for handlers in getattr(app, "handlers", {}).values():
        for handler in handlers:
            if isinstance(handler, ConversationHandler):
                keys.extend(tuple(key) if isinstance(key, (tuple, list)) else (key,)
                            for key in getattr(handler, "_conversations", {}) or {})
    return keys


def usage_here() -> "list[str]":
    """Why this bot counts as in use right now, as short phrases about how many and how long ago, never who; empty when it does not."""
    reasons = []
    now = time.monotonic()
    for name, n in _work_counts().items():
        reasons.append(f"{n} {name} in progress")
    if _in_flight_since:
        reasons.append(f"the oldest started {_minutes(now - min(_in_flight_since.values()))} ago")
    app = _app
    if app is None:
        return reasons
    queue = getattr(app, "update_queue", None)
    if queue is not None and queue.qsize():
        reasons.append(f"{queue.qsize()} update(s) waiting to be handled")
    wall = time.time()
    seen = {}
    for user_id, data in list(getattr(app, "user_data", {}).items()):
        if user_id in _admins or not isinstance(data, dict):
            continue
        last = data.get("_last_seen")
        if isinstance(last, (int, float)):
            seen[user_id] = last
    recent = [wall - last for last in seen.values() if wall - last < QUIET_MINUTES * 60]
    if recent:
        reasons.append(f"{len(recent)} person(s) active in the last {QUIET_MINUTES:g}m "
                       f"(latest {_minutes(min(recent))} ago)")
    try:
        open_steps = []
        for key in _conversation_users(app):
            ids = [part for part in key if isinstance(part, int) and part not in _admins]
            if not ids or any(part in _admins for part in key if isinstance(part, int)):
                continue
            last = max((seen.get(part, 0) for part in ids), default=0)
            if last and wall - last < CONVERSATION_MINUTES * 60:
                open_steps.append(wall - last)
        if open_steps:
            reasons.append(f"{len(open_steps)} conversation(s) part-way through "
                           f"(last step {_minutes(min(open_steps))} ago)")
    except Exception:
        logger.debug("Could not read the open conversations", exc_info=True)
    return reasons


def _peers() -> list:
    """The lifecycle of every bot still running in this process (each bot has its own copy of this module); a bot stopped on its own is no longer one of them."""
    found = [module for name, module in list(sys.modules.items())
             if (name == "lifecycle" or name.endswith(".lifecycle"))
             and getattr(module, "_app", None) is not None and hasattr(module, "usage_here")
             and getattr(module._app, "running", True) is not False]
    me = sys.modules.get(__name__)
    if me is not None and me not in found:
        found.append(me)
    return found


def _set_note(key: str, value: str) -> None:
    import family_link

    with db.pooled() as conn:
        conn.execute(
            f"""
            INSERT INTO {family_link.FAMILY_SCHEMA}.settings (key, value) VALUES (%s, %s)
            ON CONFLICT (key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )
        conn.commit()


def _running_bus_commands(bot_ids: list) -> "list[tuple[str, str, float]]":
    """(bot, command, seconds running) for family commands still running on these bots, apart from stops and restarts (the one asking among them)."""
    import family_link

    with db.pooled() as conn:
        cur = conn.execute(
            f"""
            SELECT target_bot, command, EXTRACT(EPOCH FROM now() - claimed_at)
            FROM {family_link.FAMILY_SCHEMA}.commands
            WHERE status = 'running' AND target_bot = ANY(%s)
              AND command NOT IN ('softstop', 'softstopbot', 'restart')
              AND claimed_at > now() - interval '1 hour'
            """,
            (list(bot_ids),),
        )
        return [(row[0], row[1], float(row[2] or 0)) for row in cur.fetchall()]


def _issued_too_long_ago(issued: "str | None") -> "str | None":
    if issued is None:
        return None
    try:
        age = time.time() - float(issued)
    except ValueError:
        return None
    if age > STOP_REQUEST_MAX_AGE_SECONDS:
        return (f"Not stopping: that request is {_minutes(age)} old (it waited while this service was "
                f"down, or the bus was slow). Send it again if it still stands.")
    return None


async def soft_stop(exit_code: int, issued: "str | None" = None) -> str:
    """Stop this whole process -- every bot in it -- if nobody is using any of them; otherwise say why not. exit_code 0: stay down; 1: the host restarts it."""
    stale = _issued_too_long_ago(issued)
    if stale:
        return stale
    peers = _peers()
    names = sorted(str(getattr(p, "_bot_id", "?")) for p in peers)
    if any(str(getattr(p, "_bot_id", "")).startswith("manager") for p in peers):
        return ("Not stopping: ManagerBot runs in this same process, and nothing would be left to "
                "report or to bring it back. Stop this service from the host instead.")
    if any(getattr(p, "_stop_requested", False) or getattr(p, "_draining", False) for p in peers):
        return "Already stopping."

    for p in peers:
        p._closing = True
    try:
        found = {str(p._bot_id): p.usage_here() for p in peers}
        running = await asyncio.to_thread(_running_bus_commands, names)
    except Exception as exc:
        for p in peers:
            p._closing = False
        return f"Not stopping: could not check whether anybody is using it ({type(exc).__name__}: {exc})."
    for bot_id, command, seconds in running:
        found.setdefault(bot_id, []).append(f"the bus command '{command}' running for {_minutes(seconds)}")
    busy = {bot_id: reasons for bot_id, reasons in found.items() if reasons}
    if busy:
        for p in peers:
            p._closing = False
        lines = [f"Not stopping -- in use. Everything stays open. ({', '.join(names)})"]
        lines += [f"- {bot_id}: " + "; ".join(reasons) for bot_id, reasons in sorted(busy.items())]
        lines.append("Ask again once it is quiet; /idle shows the quiet hours.")
        return "\n".join(lines)

    for p in peers:
        p._stop_requested = True
    process = sys.modules.get("family_process")
    if process is not None:
        process.exit_code = exit_code
    if exit_code == 0:
        stamp = datetime.now(timezone.utc).isoformat()
        for p in peers:
            try:
                await asyncio.to_thread(_set_note, STOPPED_NOTE_PREFIX + str(p._bot_id), stamp)
            except Exception:
                logger.debug("Could not leave the stopped-on-purpose note", exc_info=True)
    logger.warning("Soft %s: nobody is using %s -- stopping in %.0fs.",
                   "restart" if exit_code else "stop", ", ".join(names), STOP_DELAY_SECONDS)
    asyncio.get_running_loop().call_later(STOP_DELAY_SECONDS, os.kill, os.getpid(), signal.SIGTERM)
    if exit_code:
        return (f"Nobody is using it. Restarting {', '.join(names)} (one service) in "
                f"{STOP_DELAY_SECONDS:.0f}s; the host starts it again.")
    return (f"Nobody is using it. Stopping {', '.join(names)} (one service) in {STOP_DELAY_SECONDS:.0f}s. "
            f"It stays down until the host starts it again (a deploy, or Redeploy on the host).")


async def soft_stop_bot(issued: "str | None" = None) -> str:
    """Stop only this bot, if nobody is using it; the other bots of its process carry on. It comes back when the process next starts."""
    global _closing, _stop_requested
    stale = _issued_too_long_ago(issued)
    if stale:
        return stale
    name = str(_bot_id)
    if _app is None:
        return f"Not stopping {name}: it is not running yet."
    if _stop_requested or _draining:
        return "Already stopping."
    _closing = True
    try:
        reasons = usage_here()
        running = await asyncio.to_thread(_running_bus_commands, [name])
    except Exception as exc:
        _closing = False
        return f"Not stopping {name}: could not check whether anybody is using it ({type(exc).__name__}: {exc})."
    reasons += [f"the bus command '{command}' running for {_minutes(seconds)}" for _, command, seconds in running]
    if reasons:
        _closing = False
        return (f"Not stopping {name} -- in use: " + "; ".join(reasons)
                + ". It stays open. Ask again once it is quiet.")
    _stop_requested = True
    try:
        await asyncio.to_thread(_set_note, STOPPED_NOTE_PREFIX + name, datetime.now(timezone.utc).isoformat())
    except Exception:
        logger.debug("Could not leave the stopped-on-purpose note", exc_info=True)
    others = sorted(str(p._bot_id) for p in _peers() if p is not sys.modules.get(__name__))
    logger.warning("Soft stop of %s alone: nobody is using it -- stopping in %.0fs.", name, STOP_DELAY_SECONDS)
    asyncio.get_running_loop().call_later(STOP_DELAY_SECONDS, _stop_this_bot)
    carry_on = f"; {', '.join(others)} carry on" if others else ""
    return (f"Nobody is using {name}. Stopping it alone in {STOP_DELAY_SECONDS:.0f}s{carry_on}. "
            f"It comes back when its service next starts (a /restart, a deploy, or Redeploy on the host).")


def _stop_this_bot() -> None:
    app = _app
    if app is None:
        return
    try:
        app.stop_running()
    except RuntimeError:
        logger.warning("Could not stop %s alone: it had not finished starting.", _bot_id)

