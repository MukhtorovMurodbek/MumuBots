"""ManagerBot's half of the soft stop (since 2.1.6): /shutdown and /restart ask first, the request
carries its time, and the watchdog says "stopped as asked" instead of "gone down"."""
import asyncio
import time
import types
from datetime import datetime, timezone

import pytest

from conftest import load

manager = load("manager_bot", "bot")

PUBLIC_HOST, MANAGER_HOST = "public-1", "manager-1"


class Message:
    def __init__(self):
        self.sent = []
        self.markups = []
        self.text = ""

    async def reply_text(self, text, **kwargs):
        self.sent.append(text)
        self.markups.append(kwargs.get("reply_markup"))


def beat(bot_id, host, seconds_ago=5, version="2.1.6"):
    return {"bot_id": bot_id, "host": host, "seconds_ago": seconds_ago, "version": version,
            "commands": None, "started_at": datetime.now(timezone.utc)}


class FakeDb:
    def __init__(self, beats, notes=None, known=None):
        self.beats = {row["bot_id"]: row for row in beats}
        self.notes = dict(notes or {})
        self.known = dict(known or {})
        self.queued = []
        self.known_set = []

    def all_heartbeats(self):
        return list(self.beats.values())

    def heartbeat_of(self, bot_id):
        return self.beats.get(bot_id)

    def queue_command(self, bot_id, command, args, user_id, chat_id):
        self.queued.append((bot_id, command, args))
        return len(self.queued)

    def get_setting(self, key):
        return self.notes.get(key)

    def set_setting(self, key, value):
        self.notes[key] = value

    def get_known_state(self):
        return dict(self.known)

    def set_known_state(self, bot_id, up):
        self.known_set.append((bot_id, up))

    def expire_stale_commands(self, seconds):
        return 0


@pytest.fixture()
def fakes(monkeypatch):
    told = []

    async def guard(update, context):
        return True

    async def notify_owner(context, text, **kwargs):
        told.append(text)

    async def alerts_on():
        return True
    monkeypatch.setattr(manager, "guard", guard)
    monkeypatch.setattr(manager, "notify_owner", notify_owner)
    monkeypatch.setattr(manager, "_alerts_on", alerts_on)

    async def edit_in_place(message, bot, text, **kwargs):
        message.sent.append(text)
    monkeypatch.setattr(manager, "edit_in_place", edit_in_place)
    manager._SERVICE_ASKS.clear()
    return told


def ask(command, args, db, monkeypatch, message=None):
    monkeypatch.setattr(manager, "db", db)
    message = message or Message()
    update = types.SimpleNamespace(message=message, effective_message=message,
                                   effective_user=types.SimpleNamespace(id=1),
                                   effective_chat=types.SimpleNamespace(id=1))
    asyncio.run(command(update, types.SimpleNamespace(args=list(args), bot=None)))
    return message.sent


def buttons(message):
    markup = message.markups[-1]
    return {button.text: button.callback_data for row in markup.inline_keyboard for button in row}


def tap(data, db, monkeypatch, message):
    monkeypatch.setattr(manager, "db", db)

    async def answer(*args, **kwargs):
        return None
    query = types.SimpleNamespace(data=data, message=message, answer=answer)
    update = types.SimpleNamespace(callback_query=query, message=None, effective_message=message,
                                   effective_user=types.SimpleNamespace(id=1),
                                   effective_chat=types.SimpleNamespace(id=1))
    asyncio.run(manager.service_button(update, types.SimpleNamespace(args=[], bot=None)))
    return message.sent


def a_service(**changes):
    rows = [beat(name, PUBLIC_HOST) for name in ("stickerbot", "convertbot", "downloaderbot", "anonbot")]
    rows.append(beat("managerbot", MANAGER_HOST))
    for row in rows:
        row.update(changes.get(row["bot_id"], {}))
    return rows


def test_shutdown_asks_before_it_queues_anything(fakes, monkeypatch):
    db, message = FakeDb(a_service()), Message()
    ask(manager.shutdown_command, ["anon"], db, monkeypatch, message)
    assert db.queued == []
    assert "shares one service" in message.sent[0] and "StickerBot" in message.sent[0]
    assert set(buttons(message)) == {"Stop only AnonBot", "Stop all 4", "Cancel"}


def test_stop_all_queues_one_service_stop_with_its_time(fakes, monkeypatch):
    db, message = FakeDb(a_service()), Message()
    ask(manager.shutdown_command, ["anon"], db, monkeypatch, message)
    tap(buttons(message)["Stop all 4"], db, monkeypatch, message)
    assert len(db.queued) == 1
    bot_id, command, args = db.queued[0]
    assert (bot_id, command) == ("anonbot", "softstop") and abs(float(args) - time.time()) < 5


def test_stop_only_one_bot(fakes, monkeypatch):
    db, message = FakeDb(a_service()), Message()
    ask(manager.shutdown_command, ["anon"], db, monkeypatch, message)
    tap(buttons(message)["Stop only AnonBot"], db, monkeypatch, message)
    assert [(b, c) for b, c, _ in db.queued] == [("anonbot", "softstopbot")]


def test_cancel_and_a_second_tap_queue_nothing(fakes, monkeypatch):
    db, message = FakeDb(a_service()), Message()
    ask(manager.shutdown_command, ["anon"], db, monkeypatch, message)
    choices = buttons(message)
    tap(choices["Cancel"], db, monkeypatch, message)
    tap(choices["Stop all 4"], db, monkeypatch, message)
    assert db.queued == [] and "Cancelled" in message.sent[-2] and "expired" in message.sent[-1]


def test_an_old_question_has_expired(fakes, monkeypatch):
    db, message = FakeDb(a_service()), Message()
    ask(manager.shutdown_command, ["anon"], db, monkeypatch, message)
    for entry in manager._SERVICE_ASKS.values():
        entry["at"] -= manager.SERVICE_ASK_SECONDS + 1
    tap(buttons(message)["Stop all 4"], db, monkeypatch, message)
    assert db.queued == [] and "expired" in message.sent[-1]


def test_a_bot_alone_in_its_service_has_no_only_button(fakes, monkeypatch):
    rows = [beat("convertbot", "convert-1"), beat("managerbot", MANAGER_HOST)]
    db, message = FakeDb(rows), Message()
    ask(manager.shutdown_command, ["convert"], db, monkeypatch, message)
    assert set(buttons(message)) == {"Stop ConvertBot", "Cancel"}


def test_shutdown_of_a_bot_that_is_down_asks_nothing(fakes, monkeypatch):
    db = FakeDb(a_service(convertbot={"seconds_ago": 999}))
    sent = ask(manager.shutdown_command, ["convert"], db, monkeypatch)
    assert db.queued == [] and "not running" in sent[0]


def test_shutdown_never_stops_the_managers_own_service(fakes, monkeypatch):
    db = FakeDb(a_service(convertbot={"host": MANAGER_HOST}))
    sent = ask(manager.shutdown_command, ["convert"], db, monkeypatch)
    assert db.queued == [] and "take me down too" in sent[0]


def test_shutdown_of_the_manager_itself_is_refused(fakes, monkeypatch):
    db = FakeDb(a_service())
    sent = ask(manager.shutdown_command, ["managerbot"], db, monkeypatch)
    assert db.queued == [] and "me" in sent[0]


def test_shutdown_needs_one_bot(fakes, monkeypatch):
    db = FakeDb(a_service())
    sent = ask(manager.shutdown_command, [], db, monkeypatch)
    assert db.queued == [] and sent[0].startswith("Usage")
    sent = ask(manager.shutdown_command, ["all"], db, monkeypatch)
    assert db.queued == []


def test_a_bot_too_old_for_it_is_told_so(fakes, monkeypatch):
    db, message = FakeDb(a_service(convertbot={"version": "2.1.5"})), Message()
    ask(manager.shutdown_command, ["convert"], db, monkeypatch, message)
    tap(buttons(message)["Stop all 4"], db, monkeypatch, message)
    assert db.queued == []


def test_restart_force_restarts_that_bot_alone_and_keeps_force(fakes, monkeypatch):
    """In 2.2.0 a forced restart is the launcher reloading that one bot (main._BotOs._exit)."""
    db, message = FakeDb(a_service()), Message()
    ask(manager.restart_command, ["sticker", "force"], db, monkeypatch, message)
    assert db.queued == [] and "at once" in message.sent[0] and "alone" in message.sent[0]
    tap(buttons(message)["Restart StickerBot now"], db, monkeypatch, message)
    bot_id, command, args = db.queued[0]
    words = args.split()
    assert (bot_id, command) == ("stickerbot", "restart") and words[0] == "force"
    assert abs(float(words[1]) - time.time()) < 5


def test_restart_without_force(fakes, monkeypatch):
    db, message = FakeDb(a_service()), Message()
    ask(manager.restart_command, ["sticker"], db, monkeypatch, message)
    tap(buttons(message)["Restart all 4"], db, monkeypatch, message)
    assert db.queued[0][2].split()[0] != "force"


def test_watchdog_reports_a_soft_stop_once_as_stopped(fakes, monkeypatch):
    stamp = datetime.now(timezone.utc).isoformat()
    db = FakeDb(a_service(stickerbot={"seconds_ago": 999}),
                notes={"stopped:stickerbot": stamp},
                known={"stickerbot": True, "convertbot": True, "downloaderbot": True, "anonbot": True})
    monkeypatch.setattr(manager, "db", db)
    asyncio.run(manager.watchdog(types.SimpleNamespace()))
    assert db.known_set == [("stickerbot", False)]
    assert len(fakes) == 1 and "stopped as asked" in fakes[0] and "gone down" not in fakes[0]


def test_watchdog_still_reports_a_bot_that_died(fakes, monkeypatch):
    db = FakeDb(a_service(stickerbot={"seconds_ago": 999}),
                known={"stickerbot": True, "convertbot": True, "downloaderbot": True, "anonbot": True})
    monkeypatch.setattr(manager, "db", db)
    asyncio.run(manager.watchdog(types.SimpleNamespace()))
    assert len(fakes) == 1 and "gone down" in fakes[0]


def test_shutdown_is_in_the_menu_and_help():
    menus = [value for value in vars(manager).values()
             if isinstance(value, list) and value and all(hasattr(c, "command") for c in value)]
    assert any(c.command == "shutdown" for menu in menus for c in menu)
    assert "/shutdown" in manager.HELP


def test_restart_without_force_names_everyone(fakes, monkeypatch):
    db, message = FakeDb(a_service()), Message()
    ask(manager.restart_command, ["sticker"], db, monkeypatch, message)
    assert "AnonBot" in message.sent[0] and "once nobody is using it" in message.sent[0]
