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

ENABLED = os.environ.get("DEPLOY_SAFETY", "on").lower() not in ("off", "0", "false", "no")

LEASE_WAIT_SECONDS = float(os.environ.get("DEPLOY_LEASE_WAIT_SECONDS", "240"))

STATE_TTL_HOURS = int(os.environ.get("DEPLOY_STATE_TTL_HOURS", "12"))

PERSIST_SECONDS = float(os.environ.get("DEPLOY_PERSIST_SECONDS", "180"))

_app = None
_draining = False
_drain_started: float | None = None
_lease_task: "asyncio.Task | None" = None
_lease_table_ready = False
_lease_lock = threading.Lock()
_lease_handed_back = False
_bot_id: str | None = None


LEASE_TABLE = "poll_lease"

LEASE_TTL_SECONDS = float(os.environ.get("DEPLOY_LEASE_TTL_SECONDS", "30"))
LEASE_RENEW_SECONDS = LEASE_TTL_SECONDS / 3

_HOST = socket.gethostname()
_HOLDER = f"{_HOST}:{os.getpid()}:{secrets.token_hex(4)}"


def _lock_key(name: str) -> int:
    digest = hashlib.blake2b(name.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big", signed=True)


def init_lease_table() -> None:
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
    host, _, rest = holder.partition(":")
    pid, _, _token = rest.partition(":")
    if host != _HOST or holder == _HOLDER or not pid.isdigit():
        return False
    if int(pid) == os.getpid():
        return True
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return True
    except OSError:
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
    with _lease_lock:
        if _lease_handed_back:
            return False
        if not _lease_table_ready:
            init_lease_table()
        return _take(bot_id) or _take_from_the_gone(bot_id)


async def hold_the_lease(bot_id: str) -> bool:
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


STATE_TABLE = "runtime_state"

STATE_PURPOSE = "lifecycle.state"
WAITLIST_PURPOSE = "lifecycle.waitlist"


def _pseudo_key(kind: str, key: str) -> str:
    if kind == "user":
        return str(vault.pseudonym(int(key)))
    if kind == "conversation":
        name, bar, pair = key.partition("|")
        if not bar:
            raise ValueError("a conversation key has the form name|chat:user")
        return f"{name}|" + ":".join(str(vault.pseudonym(int(part))) for part in pair.split(":"))
    raise ValueError(f"unknown kind of saved state: {kind!r}")


def _seal_state(kind: str, key: str, value) -> "tuple[str, dict]":
    stored_key = _pseudo_key(kind, key)
    blob = vault.seal_json({"key": key, "data": value}, STATE_PURPOSE)
    return stored_key, {"sealed": base64.b64encode(blob).decode("ascii")}


def unseal_state(kind: str, stored_key: str, stored_value) -> "tuple[str, object]":
    if not isinstance(stored_value, dict) or not isinstance(stored_value.get("sealed"), str):
        raise vault.VaultError("A saved state row is not sealed")
    try:
        blob = base64.b64decode(stored_value["sealed"], validate=True)
    except (ValueError, binascii.Error):
        raise vault.VaultError("A saved state row is not sealed") from None
    try:
        payload = vault.open_json(blob, STATE_PURPOSE)
    except ValueError:
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
            continue
    if not stored:
        return
    with db.pooled() as conn:
        with conn.cursor() as cur:
            cur.executemany(f"DELETE FROM {STATE_TABLE} WHERE kind = %s AND key = %s", stored)
        conn.commit()


def _read_state(kind: str) -> list[tuple[str, dict]]:
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
    def __init__(self):
        super().__init__(
            store_data=PersistenceInput(
                bot_data=False, chat_data=False, user_data=True, callback_data=False
            ),
            update_interval=max(PERSIST_SECONDS * 10, 600),
        )
        self._user_data: dict[int, dict] = {}
        self._dirty_users: set[int] = set()
        self._dropped_users: set[int] = set()
        self._conversations: dict[str, dict] = {}
        self._conversations_loaded = False


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


    async def flush(self) -> None:
        dirty, dropped = set(self._dirty_users), set(self._dropped_users)
        writes, deletes = [], [("user", str(u)) for u in dropped]
        for user_id in dirty:
            kept = _clean(self._user_data.get(user_id) or {})
            if kept:
                writes.append(("user", str(user_id), kept))
            else:
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
            self._dirty_users |= dirty - self._dropped_users
            self._dropped_users |= dropped - self._dirty_users


_in_flight: dict[int, tuple[int, str]] = {}
_in_flight_since: dict[int, float] = {}
_next_ticket = 0


def is_draining() -> bool:
    return _draining


@asynccontextmanager
async def busy(chat_id: int, if_interrupted: str):
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


MAINTENANCE_KEY_PREFIX = "maintenance:"
WAITLIST_TABLE = "update_waitlist"

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
        import family_link

        if family_link._column_missing(conn, WAITLIST_TABLE, "who_sealed"):
            conn.execute(f"ALTER TABLE {schema}.{WAITLIST_TABLE} ADD COLUMN IF NOT EXISTS who_sealed BYTEA")
        conn.commit()


def _read_maintenance(bot_id: str) -> "tuple[bool, datetime | None]":
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
        return True, None


def refresh_maintenance(bot_id: str) -> bool:
    global _maintenance_on, _maintenance_until
    try:
        _maintenance_on, _maintenance_until = _read_maintenance(bot_id)
    except Exception:
        logger.debug("Could not read the maintenance flag", exc_info=True)
        return _maintenance_on
    return _maintenance_on


def begin_maintenance(bot_id: str, minutes: int) -> "datetime":
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
    if not _maintenance_on or _maintenance_until is None:
        return None
    left = (_maintenance_until - datetime.now(timezone.utc)).total_seconds()
    if left <= 0:
        return None
    return max(1, -(-int(left) // 60))


def is_paused() -> bool:
    return _draining or _maintenance_on or _closing


WAITLIST_KEEP_DAYS = int(os.environ.get("DEPLOY_WAITLIST_KEEP_DAYS") or 7)


def hold_for_update(user_id: int, chat_id: int, bot_id: "str | None" = None) -> None:
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
    return list(_in_flight.values())


def persistence():
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
    if ENABLED and can_handle_signals():
        extra["stop_signals"] = ()
    return extra


async def _persist_job(context) -> None:
    app = _app
    if app is None or app.persistence is None:
        return
    try:
        await app.update_persistence()
        await app.persistence.flush()
    except Exception:
        logger.debug("Periodic state save failed; the next pass retries", exc_info=True)


def install(app, bot_id: str, admin_ids=()) -> None:
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
    global _bot_id
    _bot_id = bot_id
    if not ENABLED:
        return
    await hold_the_lease(bot_id)

    try:
        await asyncio.to_thread(_set_note, STOPPED_NOTE_PREFIX + bot_id, "")
    except Exception:
        logger.debug("Could not clear the stopped-on-purpose note", exc_info=True)

    try:
        await asyncio.to_thread(init_waitlist_table)
        if await asyncio.to_thread(refresh_maintenance, bot_id):
            logger.info("Still in maintenance -- not starting new long work yet.")
    except Exception:
        logger.debug("Could not read the maintenance flag at startup", exc_info=True)

    if not can_handle_signals():
        return

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
    if application.persistence is not None:
        try:
            await application.update_persistence()
            await application.persistence.flush()
            logger.info("In-progress state saved -- the next start picks it up.")
        except Exception:
            logger.warning("Could not save in-progress state on the way out.", exc_info=True)
    release_lease()


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
    from telegram.ext import ConversationHandler
    keys = []
    for handlers in getattr(app, "handlers", {}).values():
        for handler in handlers:
            if isinstance(handler, ConversationHandler):
                keys.extend(tuple(key) if isinstance(key, (tuple, list)) else (key,)
                            for key in getattr(handler, "_conversations", {}) or {})
    return keys


def usage_here() -> "list[str]":
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
