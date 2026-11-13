"""The soft stop behind ManagerBot's /shutdown and /restart (since 2.1.6): a service, or one bot of it,
goes down only when nobody is using it -- and the launcher ends the way the stop asked."""
import asyncio
import contextvars
import signal
import sys
import time
import types

import pytest

from conftest import connect, load

import main

OWNER, SOMEONE = 111, 222


def fake_app(**user_data):
    return types.SimpleNamespace(update_queue=asyncio.Queue(), user_data=dict(user_data), handlers={})


def sql(query, *params):
    with connect() as conn:
        cur = conn.execute(query, params or None)
        rows = cur.fetchall() if cur.description else None
        conn.commit()
        return rows


@pytest.fixture()
def bots(monkeypatch):
    """StickerBot's and ConvertBot's lifecycles, as two bots of one process, both idle."""
    load("sticker_bot", "family_link").init_family_schema()
    sql("DELETE FROM family.settings WHERE key LIKE 'stopped:%%'")
    sql("DELETE FROM family.commands")
    sticker, convert = load("sticker_bot", "lifecycle"), load("convert_bot", "lifecycle")
    for module, bot_id in ((sticker, "stickerbot"), (convert, "convertbot")):
        monkeypatch.setattr(module, "_app", fake_app())
        monkeypatch.setattr(module, "_bot_id", bot_id)
        monkeypatch.setattr(module, "_admins", frozenset({OWNER}))
        monkeypatch.setattr(module, "_closing", False)
        monkeypatch.setattr(module, "_stop_requested", False)
        monkeypatch.setattr(module, "_draining", False)
        monkeypatch.setattr(module, "_in_flight", {})
        monkeypatch.setattr(module, "_in_flight_since", {})
        monkeypatch.setattr(module, "_work_sources", {})
        monkeypatch.setattr(module, "STOP_DELAY_SECONDS", 0.01)
    killed = []
    fake_os = types.SimpleNamespace(kill=lambda pid, sig: killed.append(sig), getpid=lambda: 4242,
                                    environ=__import__("os").environ)
    monkeypatch.setattr(sticker, "os", fake_os)
    process = sys.modules["family_process"]
    monkeypatch.setattr(process, "exit_code", 0)
    return types.SimpleNamespace(sticker=sticker, convert=convert, killed=killed, process=process)


async def stop(module, exit_code=0, issued=None):
    answer = await module.soft_stop(exit_code, issued)
    await asyncio.sleep(0.05)
    return answer


async def stop_one(module, issued=None):
    answer = await module.soft_stop_bot(issued)
    await asyncio.sleep(0.05)
    return answer


def test_one_exit_code_for_the_whole_process():
    assert isinstance(sys.modules.get("family_process"), types.ModuleType)
    assert main.FAMILY_PROCESS is sys.modules["family_process"]


def test_idle_bot_is_not_in_use(bots):
    assert bots.sticker.usage_here() == []


def test_somebody_active_lately_is_use(bots):
    bots.sticker._app.user_data[SOMEONE] = {"_last_seen": time.time() - 60}
    reasons = bots.sticker.usage_here()
    assert len(reasons) == 1 and "active" in reasons[0]
    assert str(SOMEONE) not in " ".join(reasons)


def test_the_owner_is_not_use(bots):
    bots.sticker._app.user_data[OWNER] = {"_last_seen": time.time()}
    assert bots.sticker.usage_here() == []


def test_somebody_gone_quiet_is_not_use(bots):
    bots.sticker._app.user_data[SOMEONE] = {"_last_seen": time.time() - 10 * 60}
    assert bots.sticker.usage_here() == []


def test_work_in_progress_is_use(bots):
    async def run():
        async with bots.sticker.busy(5, "interrupted"):
            return bots.sticker.usage_here()
    reasons = asyncio.run(run())
    assert any("slow work" in r for r in reasons) and any("oldest" in r for r in reasons)
    assert bots.sticker.usage_here() == []


def test_a_work_source_is_use(bots):
    bots.convert.add_work_source("conversion(s)", lambda: 2)
    assert bots.convert.usage_here() == ["2 conversion(s) in progress"]


def test_updates_waiting_are_use(bots):
    bots.sticker._app.update_queue.put_nowait(object())
    assert any("waiting" in r for r in bots.sticker.usage_here())


def test_a_conversation_part_way_is_use_for_a_while(bots):
    from telegram.ext import CommandHandler, ConversationHandler

    async def noop(update, context):
        return None
    handler = ConversationHandler(entry_points=[CommandHandler("x", noop)], states={}, fallbacks=[])
    handler._conversations[(SOMEONE, SOMEONE)] = 1
    bots.sticker._app.handlers = {0: [handler]}
    bots.sticker._app.user_data[SOMEONE] = {"_last_seen": time.time() - 12 * 60}
    assert any("conversation" in r for r in bots.sticker.usage_here())
    bots.sticker._app.user_data[SOMEONE] = {"_last_seen": time.time() - 40 * 60}
    assert bots.sticker.usage_here() == []


def test_closing_declines_new_work(bots):
    assert not bots.sticker.is_paused()
    bots.sticker._closing = True
    assert bots.sticker.is_paused()


def test_every_bot_of_the_process_is_a_peer(bots):
    peers = bots.sticker._peers()
    assert bots.sticker in peers and bots.convert in peers
    assert len(peers) == len({id(p) for p in peers})


def test_refused_while_another_bot_of_the_process_is_in_use(bots):
    bots.convert.add_work_source("conversion(s)", lambda: 1)
    answer = asyncio.run(stop(bots.sticker))
    assert answer.startswith("Not stopping") and "convertbot: 1 conversion(s)" in answer
    assert not bots.sticker._closing and not bots.convert._closing
    assert bots.killed == [] and bots.process.exit_code == 0


def test_refused_while_a_bus_command_runs(bots):
    sql("INSERT INTO family.commands (target_bot, command, args, status, claimed_at) "
        "VALUES ('convertbot', 'broadcast', '', 'running', now() - interval '20 seconds')")
    answer = asyncio.run(stop(bots.sticker))
    assert answer.startswith("Not stopping") and "broadcast" in answer and bots.killed == []


def test_a_command_running_in_this_process_is_use(bots):
    link = load("convert_bot", "family_link")
    link._running_here[999] = "broadcast"
    try:
        bots.convert.add_work_source("family command(s)", link.running_here)
        assert any("family command" in r for r in bots.convert.usage_here())
        link._running_here[999] = "softstop"
        assert bots.convert.usage_here() == []
    finally:
        link._running_here.pop(999, None)


def test_a_stale_request_is_refused(bots):
    answer = asyncio.run(stop(bots.sticker, issued=str(time.time() - 3600)))
    assert "old" in answer and bots.killed == [] and not bots.sticker._closing


def test_idle_stop_stays_down(bots):
    answer = asyncio.run(stop(bots.sticker, 0, str(time.time())))
    assert "Stopping" in answer
    assert bots.killed == [signal.SIGTERM] and bots.process.exit_code == 0
    notes = dict(sql("SELECT key, value FROM family.settings WHERE key LIKE 'stopped:%%'"))
    assert set(notes) == {"stopped:stickerbot", "stopped:convertbot"}
    assert bots.sticker.is_paused() and bots.convert.is_paused()
    assert asyncio.run(bots.sticker.soft_stop(0, None)) == "Already stopping."


def test_idle_restart_asks_to_be_started_again(bots):
    answer = asyncio.run(stop(bots.sticker, 1))
    assert "Restarting" in answer and bots.process.exit_code == 1
    assert sql("SELECT count(*) FROM family.settings WHERE key LIKE 'stopped:%%'")[0][0] == 0


def test_never_with_managerbot_in_the_process(bots, monkeypatch):
    monkeypatch.setattr(bots.convert, "_bot_id", "managerbot")
    answer = asyncio.run(stop(bots.sticker))
    assert "ManagerBot" in answer and bots.killed == []


def test_a_stop_signal_waits_for_work_then_goes(bots):
    count = {"n": 1}
    bots.convert.add_work_source("conversion(s)", lambda: count["n"])

    async def run():
        asyncio.get_running_loop().call_later(0.3, count.update, {"n": 0})
        started = time.monotonic()
        await bots.convert._wait_for_work(5)
        return time.monotonic() - started
    waited = asyncio.run(run())
    assert 0.25 < waited < 2.5
    count["n"] = 1
    started = time.monotonic()
    asyncio.run(bots.convert._wait_for_work(0.2))
    assert time.monotonic() - started < 1.5


def test_restart_goes_through_the_soft_stop_unless_forced(bots, monkeypatch):
    link = load("sticker_bot", "family_link")
    asked = []

    async def fake_soft_stop(code, issued=None):
        asked.append((code, issued))
        return "checked"
    monkeypatch.setattr(link.lifecycle, "soft_stop", fake_soft_stop)
    out = asyncio.run(link._cmd_restart(None, ["1700000000"]))
    assert out[0] == "checked" and asked == [(1, "1700000000")]
    out = asyncio.run(link._cmd_softstop(None, ["1700000001"]))
    assert asked[-1] == (0, "1700000001")
    assert "softstop" in link.COMMANDS and "softstop" in link.COMMAND_HELP
    assert "softstopbot" in link.COMMANDS and "softstopbot" in link.COMMAND_HELP


def test_one_bot_alone_stops_when_it_is_idle_even_if_another_is_busy(bots, monkeypatch):
    bots.sticker.add_work_source("sticker run(s)", lambda: 1)
    stopped = []
    bots.convert._app.stop_running = lambda: stopped.append("convert")
    answer = asyncio.run(stop_one(bots.convert))
    assert "Stopping it alone" in answer and "stickerbot carry on" in answer
    assert stopped == ["convert"] and bots.process.exit_code == 0
    assert [k for k, in sql("SELECT key FROM family.settings WHERE key LIKE 'stopped:%%'")] == ["stopped:convertbot"]
    assert not bots.sticker._closing


def test_one_bot_alone_is_refused_while_it_is_in_use(bots):
    bots.convert._app.user_data[SOMEONE] = {"_last_seen": time.time() - 30}
    stopped = []
    bots.convert._app.stop_running = lambda: stopped.append("convert")
    answer = asyncio.run(stop_one(bots.convert))
    assert answer.startswith("Not stopping convertbot") and stopped == [] and not bots.convert._closing


def test_the_stop_requests_themselves_do_not_count_as_use(bots):
    for command in ("softstop", "softstopbot", "restart"):
        sql("INSERT INTO family.commands (target_bot, command, args, status, claimed_at) "
            "VALUES ('convertbot', %s, '', 'running', now())", command)
    stopped = []
    bots.convert._app.stop_running = lambda: stopped.append("convert")
    assert "Stopping it alone" in asyncio.run(stop_one(bots.convert))
    bots.convert._app.running = False
    answer = asyncio.run(stop(bots.sticker))
    assert "Stopping" in answer and "convertbot" not in answer


# --- the launcher: what a stop means to the process ---------------------------------------------

def _fake_bot(name):
    bot = types.SimpleNamespace(name=name, folder=f"{name}_bot", state="running", retry=None, failures=0,
                                restarting=False, task=None, context=contextvars.copy_context())
    bot.app = types.SimpleNamespace(name=name)
    return bot


def test_a_bot_stopped_alone_stays_stopped_and_the_others_carry_on(monkeypatch):
    first, second = _fake_bot("first"), _fake_bot("second")
    family = main.Family([first, second])
    torn_down = []

    async def teardown(bot):
        torn_down.append(bot.name)
    monkeypatch.setattr(family, "teardown", teardown)
    monkeypatch.setattr(family, "hand_over_memory_reports", lambda: None)

    async def go():
        family.loop = asyncio.get_running_loop()
        family.stop_event = asyncio.Event()
        family.on_stop_running(first.app)
        await asyncio.sleep(0.05)
        alone = family.stop_event.is_set()
        family.on_stop_running(second.app)
        await asyncio.sleep(0.05)
        return alone, family.stop_event.is_set()
    alone, all_stopped = asyncio.run(go())
    assert torn_down == ["first", "second"]
    assert not alone and all_stopped
    assert first.state == "stopped" and first.retry is None and family.exit_code == 0


@pytest.mark.parametrize("asked", [0, 1])
def test_the_process_ends_with_the_code_the_soft_stop_asked_for(monkeypatch, asked):
    monkeypatch.setattr(main.FAMILY_PROCESS, "exit_code", asked)
    monkeypatch.setattr(main, "STATUS_MINUTES", 0)
    monkeypatch.setattr(main.gc, "freeze", lambda: None)
    family = main.Family([])
    # Not the process's real signal handlers: the stop arrives as the call SIGTERM would make.
    monkeypatch.setattr(family, "take_signals", lambda: None)

    async def go():
        asyncio.get_running_loop().call_later(0.2, family.on_signal, signal.SIGTERM)
        return await family.run()
    assert asyncio.run(go()) == asked
