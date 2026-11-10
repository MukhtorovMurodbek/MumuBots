"""The poll lease: one process at a time polls for a bot, behind any connection pooler.

Up to 2.1.4 the lease was a session-level advisory lock. Behind Supabase's pooler the locks sat on
the pooler's own connections (production, 2026-11-10: opened 2026-10-23), so a new container got
the lease within two seconds while the old one was still polling. The lease is now a row with an
expiry, and nothing about it depends on which connection a statement runs on."""
import asyncio
import os
import subprocess
import sys
import threading
import time
from contextlib import contextmanager

import psycopg
import pytest

from conftest import DATABASE_URL, connect, load

STICKER = "sticker_bot"
BOT = "stickerbot"
ELSEWHERE = "another-container:7:0000abcd"


@pytest.fixture()
def lease(monkeypatch):
    load(STICKER, "db").init_db()
    module = load(STICKER, "lifecycle")
    with connect() as conn:
        conn.execute("DROP TABLE IF EXISTS family.poll_lease")
        conn.commit()
    monkeypatch.setattr(module, "ENABLED", True)
    monkeypatch.setattr(module, "_lease_table_ready", False)
    monkeypatch.setattr(module, "_lease_handed_back", False)
    monkeypatch.setattr(module, "_lease_task", None)
    monkeypatch.setattr(module, "_bot_id", BOT)
    return module


def become(lease, monkeypatch, holder: str) -> None:
    """Act as another process: its holder string, and the machine it runs on."""
    monkeypatch.setattr(lease, "_HOLDER", holder)
    monkeypatch.setattr(lease, "_HOST", holder.split(":", 1)[0])


def holder_now():
    with connect() as conn:
        row = conn.execute("SELECT holder FROM family.poll_lease WHERE bot_id = %s", (BOT,)).fetchone()
    return row[0] if row else None


def held_by(holder: str, seconds: float = 30) -> None:
    """Someone else's lease, as their process would have left it."""
    with connect() as conn:
        conn.execute("CREATE SCHEMA IF NOT EXISTS family")
        conn.execute("CREATE TABLE IF NOT EXISTS family.poll_lease (bot_id TEXT PRIMARY KEY, "
                     "holder TEXT NOT NULL, taken_at TIMESTAMPTZ NOT NULL DEFAULT now(), "
                     "expires_at TIMESTAMPTZ NOT NULL)")
        conn.execute("INSERT INTO family.poll_lease (bot_id, holder, expires_at) "
                     "VALUES (%s, %s, now() + make_interval(secs => %s)) "
                     "ON CONFLICT (bot_id) DO UPDATE SET holder = EXCLUDED.holder, "
                     "expires_at = EXCLUDED.expires_at", (BOT, holder, float(seconds)))
        conn.commit()


def test_one_holder_at_a_time(lease, monkeypatch):
    assert lease._try_lease(BOT) and holder_now() == lease._HOLDER
    mine = lease._HOLDER
    become(lease, monkeypatch, ELSEWHERE)
    assert not lease._try_lease(BOT)
    become(lease, monkeypatch, mine)
    assert lease._try_lease(BOT) and holder_now() == mine


def test_renewing_moves_the_expiry_and_keeps_when_it_was_taken(lease):
    assert lease._try_lease(BOT)
    with connect() as conn:
        taken, before = conn.execute("SELECT taken_at, expires_at FROM family.poll_lease").fetchone()
    time.sleep(0.05)
    assert lease._try_lease(BOT)
    with connect() as conn:
        taken_again, after = conn.execute("SELECT taken_at, expires_at FROM family.poll_lease").fetchone()
    assert taken_again == taken and after > before


def test_an_expired_lease_is_free(lease):
    held_by(ELSEWHERE, seconds=-1)
    assert lease._try_lease(BOT) and holder_now() == lease._HOLDER


def test_a_live_holder_elsewhere_is_waited_for(lease):
    held_by(ELSEWHERE)
    assert not lease._try_lease(BOT) and holder_now() == ELSEWHERE


def test_a_running_process_on_this_machine_is_waited_for(lease):
    running = f"{lease._HOST}:{os.getppid()}:0000abcd"
    held_by(running)
    assert not lease._try_lease(BOT) and holder_now() == running


def test_a_process_on_this_machine_that_has_ended_is_not_waited_for(lease):
    ended = subprocess.Popen([sys.executable, "-c", "pass"])
    ended.wait()
    held_by(f"{lease._HOST}:{ended.pid}:0000abcd")
    assert lease._try_lease(BOT) and holder_now() == lease._HOLDER


def test_an_earlier_life_of_this_process_id_is_not_waited_for(lease):
    # The first process in a container usually gets the same id each time it is started.
    held_by(f"{lease._HOST}:{os.getpid()}:0000abcd")
    assert lease._try_lease(BOT) and holder_now() == lease._HOLDER


def test_handing_back_lets_the_next_one_in_at_once(lease, monkeypatch):
    assert lease._try_lease(BOT)
    lease.release_lease()
    assert holder_now() is None
    become(lease, monkeypatch, ELSEWHERE)
    monkeypatch.setattr(lease, "_lease_handed_back", False)
    assert lease._try_lease(BOT) and holder_now() == ELSEWHERE


def test_handing_back_leaves_someone_elses_lease_alone(lease):
    held_by(ELSEWHERE)
    lease.init_lease_table()
    lease.release_lease()
    assert holder_now() == ELSEWHERE


def test_a_renewal_after_handing_back_does_not_put_the_lease_back(lease):
    assert lease._try_lease(BOT)
    lease.release_lease()
    assert not lease._try_lease(BOT) and holder_now() is None


def test_every_statement_on_another_connection_changes_nothing(lease, monkeypatch):
    """What a transaction pooler does: no two statements need share a connection."""
    opened = []

    @contextmanager
    def fresh_connection():
        with psycopg.connect(DATABASE_URL) as conn:
            opened.append(conn)
            yield conn

    monkeypatch.setattr(lease.db, "pooled", fresh_connection)
    mine = lease._HOLDER
    assert lease._try_lease(BOT)
    become(lease, monkeypatch, ELSEWHERE)
    assert not lease._try_lease(BOT)
    become(lease, monkeypatch, mine)
    lease.release_lease()
    become(lease, monkeypatch, ELSEWHERE)
    monkeypatch.setattr(lease, "_lease_handed_back", False)
    assert lease._try_lease(BOT) and holder_now() == ELSEWHERE
    assert len(opened) >= 5 and all(conn.closed for conn in opened)


def test_the_bots_creating_the_table_together_is_fine(lease):
    failures = []
    start = threading.Barrier(5)

    def create():
        start.wait()
        try:
            lease.init_lease_table()
        except Exception as exc:  # pragma: no cover - what this test is here to catch
            failures.append(exc)

    threads = [threading.Thread(target=create) for _ in range(5)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert failures == [] and lease._try_lease(BOT)


def test_hold_the_lease_takes_a_free_one_and_minds_it(lease, monkeypatch):
    monkeypatch.setattr(lease, "LEASE_RENEW_SECONDS", 0.01)

    async def run():
        assert await lease.hold_the_lease(BOT)
        task = lease._lease_task
        assert task is not None and not task.done()
        await asyncio.sleep(0.1)
        assert holder_now() == lease._HOLDER
        lease.release_lease()
        await asyncio.sleep(0)
        assert task.cancelled() or task.done()

    asyncio.run(run())
    assert holder_now() is None


def test_hold_the_lease_waits_for_the_old_one_to_hand_it_back(lease):
    held_by(ELSEWHERE)

    async def run():
        loop = asyncio.get_running_loop()
        loop.call_later(0.3, lambda: held_by(ELSEWHERE, seconds=-1))
        started = time.monotonic()
        assert await lease.hold_the_lease(BOT)
        assert time.monotonic() - started < 5
        lease.release_lease()

    asyncio.run(run())


def test_starting_anyway_still_takes_the_lease_once_it_is_free(lease, monkeypatch):
    monkeypatch.setattr(lease, "LEASE_WAIT_SECONDS", 0)
    monkeypatch.setattr(lease, "LEASE_RENEW_SECONDS", 0.01)
    held_by(ELSEWHERE)

    async def run():
        assert not await lease.hold_the_lease(BOT)
        held_by(ELSEWHERE, seconds=-1)
        for _ in range(100):
            await asyncio.sleep(0.02)
            if holder_now() == lease._HOLDER:
                break
        assert holder_now() == lease._HOLDER
        lease.release_lease()

    asyncio.run(run())


def test_a_database_that_cannot_be_reached_does_not_stop_the_bot(lease, monkeypatch):
    def unreachable(_bot_id):
        raise psycopg.OperationalError("connection refused")

    monkeypatch.setattr(lease, "_try_lease", unreachable)
    monkeypatch.setattr(lease, "LEASE_RENEW_SECONDS", 3600)

    async def run():
        assert not await lease.hold_the_lease(BOT)
        lease.release_lease()

    asyncio.run(run())
