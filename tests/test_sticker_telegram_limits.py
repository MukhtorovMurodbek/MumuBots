"""StickerBot and Telegram's limits and hiccups (2.1.5, ported the same day). What production
showed between 2026-10-29 and 11-04: an album met the flood limit ("Retry in 10 seconds") and the
picture was lost; a new pack's first sticker failed with "Shortname_occupy_failed"; a picture's
download timed out and the person got the crash reply."""
import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from telegram import Chat, Document, Message, Update, User
from telegram.error import BadRequest, NetworkError, RetryAfter, TimedOut

from conftest import load

STICKER = "sticker_bot"


class FakeFile:
    def __init__(self, bot):
        self.bot = bot

    async def download_as_bytearray(self):
        return await self.bot.step("download", bytearray(b"raw"))


class FakeBot:
    """Telegram, scripted: each method's answers come from `script[method]`, in order. An
    exception instance is raised, anything else returned; past the script, the default."""

    def __init__(self, **script):
        self.script = {method: list(answers) for method, answers in script.items()}
        self.calls = []

    async def step(self, method, default=None, **kwargs):
        self.calls.append((method, kwargs))
        answers = self.script.get(method)
        answer = answers.pop(0) if answers else default
        if isinstance(answer, BaseException):
            raise answer
        return answer

    def count(self, method):
        return sum(1 for called, _ in self.calls if called == method)

    async def get_file(self, file_id):
        return await self.step("get_file", FakeFile(self), file_id=file_id)

    async def create_new_sticker_set(self, **kwargs):
        return await self.step("create_new_sticker_set", True, **kwargs)

    async def add_sticker_to_set(self, **kwargs):
        return await self.step("add_sticker_to_set", True, **kwargs)

    async def get_sticker_set(self, name, **kwargs):
        stickers = [SimpleNamespace(file_id="s1", file_unique_id="u1"),
                    SimpleNamespace(file_id="s2", file_unique_id="u2")]
        return await self.step("get_sticker_set", SimpleNamespace(stickers=stickers), name=name)


@pytest.fixture()
def ops(monkeypatch):
    module = load(STICKER, "sticker_ops")
    slept = []

    async def no_wait(seconds):
        slept.append(seconds)

    monkeypatch.setattr(module.asyncio, "sleep", no_wait)
    module.slept = slept
    return module


def test_a_flood_wait_is_waited_out_and_the_call_made_again(ops):
    fake = FakeBot(add_sticker_to_set=[RetryAfter(10)])
    assert asyncio.run(ops.add_to_pack(fake, 7, "p_by_b", "sticker")) == ("s2", 2)
    assert fake.count("add_sticker_to_set") == 2 and ops.slept == [10.5]


def test_a_long_flood_wait_is_not_waited(ops):
    fake = FakeBot(add_sticker_to_set=[RetryAfter(120)])
    with pytest.raises(RetryAfter):
        asyncio.run(ops.add_to_pack(fake, 7, "p_by_b", "sticker"))
    assert fake.count("add_sticker_to_set") == 1 and ops.slept == []


def test_flood_waits_are_capped(ops):
    fake = FakeBot(add_sticker_to_set=[RetryAfter(5)] * 5)
    with pytest.raises(RetryAfter):
        asyncio.run(ops.add_to_pack(fake, 7, "p_by_b", "sticker"))
    assert fake.count("add_sticker_to_set") == ops.TELEGRAM_TRIES == 3 and len(ops.slept) == 2


def test_a_wait_reads_as_seconds_either_way(ops):
    assert ops.retry_after_seconds(SimpleNamespace(retry_after=timedelta(seconds=7))) == 7
    assert ops.retry_after_seconds(SimpleNamespace(retry_after=3)) == 3
    assert ops.retry_after_seconds(SimpleNamespace(retry_after=None)) > ops.FLOOD_WAIT_MAX


def test_an_add_is_never_repeated_after_a_timeout(ops):
    fake = FakeBot(add_sticker_to_set=[TimedOut()])
    with pytest.raises(TimedOut):
        asyncio.run(ops.add_to_pack(fake, 7, "p_by_b", "sticker"))
    assert fake.count("add_sticker_to_set") == 1


def test_a_download_is_tried_again_after_a_timeout_or_a_dropped_connection(ops):
    fake = FakeBot(get_file=[TimedOut()], download=[NetworkError("Bad Gateway")])
    assert asyncio.run(ops.download(fake, "f")) == b"raw"
    assert fake.count("get_file") == 2 and fake.count("download") == 2


def test_a_refused_download_is_not_tried_again(ops):
    fake = FakeBot(get_file=[BadRequest("File is too big")])
    with pytest.raises(BadRequest):
        asyncio.run(ops.download(fake, "f"))
    assert fake.count("get_file") == 1


def test_a_pack_that_cannot_be_read_back_leaves_no_number(ops):
    fake = FakeBot(get_sticker_set=[TimedOut()] * 5)
    assert asyncio.run(ops.add_to_pack(fake, 7, "p_by_b", "sticker")) == (None, None)
    assert fake.count("add_sticker_to_set") == 1 and fake.count("get_sticker_set") == 3
    assert ops.added_text("en", "Memes", None, "😭") == "✅ Added to “Memes” with 😭."
    ops.remember(7, 987654, "p_by_b", None)  # a message id no other test uses: the memory is shared
    assert ops.sticker_of(SimpleNamespace(chat_id=7, message_id=987654, sticker=None)) is None


def test_every_language_says_an_unnumbered_add():
    i18n = load(STICKER, "i18n")
    for lang in ("en", "uz", "ru"):
        text = i18n.t(lang, "sticker_added_unnumbered", title="Memes", emoji="😭")
        assert text.startswith("✅") and "Memes" in text and "😭" in text


@pytest.fixture()
def bot(monkeypatch, ops):
    module = load(STICKER, "bot")
    packs, replies = [], []

    async def english(*_a, **_k):
        return "en"

    monkeypatch.setattr(module.i18n, "get_lang", english)
    monkeypatch.setattr(module, "add_pack", lambda *args: packs.append(args))
    monkeypatch.setattr(module, "record_error", lambda exc: None)
    module.packs, module.replies, module.slept = packs, replies, ops.slept
    return module


def new_pack_context(fake):
    return SimpleNamespace(bot=fake, user_data={"owner_id": 7, "title": "Memes", "pack_created": False})


def test_a_new_pack_whose_name_is_refused_gets_another(bot):
    fake = FakeBot(create_new_sticker_set=[BadRequest("Shortname_occupy_failed")])
    context = new_pack_context(fake)
    user = SimpleNamespace(id=7, username="t", full_name="T")
    assert asyncio.run(bot._add_input_sticker(context, user, "sticker")) == ("s2", 2)
    names = [kwargs["name"] for called, kwargs in fake.calls if called == "create_new_sticker_set"]
    assert len(names) == 2 and names[0] != names[1]
    assert context.user_data["target_pack"] == names[1] and bot.packs[0][1] == names[1]


def test_a_name_refused_every_time_fails_after_three(bot):
    fake = FakeBot(create_new_sticker_set=[BadRequest("Shortname_occupy_failed")] * 5)
    context = new_pack_context(fake)
    with pytest.raises(BadRequest):
        asyncio.run(bot._add_input_sticker(context, SimpleNamespace(id=7, username="t", full_name="T"), "s"))
    assert fake.count("create_new_sticker_set") == 3 and bot.packs == []
    assert context.user_data["pack_created"] is False


def test_other_refusals_of_a_new_pack_are_not_tried_again(bot):
    fake = FakeBot(create_new_sticker_set=[BadRequest("Stickers_too_much")])
    with pytest.raises(BadRequest):
        asyncio.run(bot._add_input_sticker(new_pack_context(fake), SimpleNamespace(id=7, username="t",
                                                                                   full_name="T"), "s"))
    assert fake.count("create_new_sticker_set") == 1


def test_a_new_pack_that_cannot_be_read_back_is_still_number_one(bot):
    fake = FakeBot(get_sticker_set=[TimedOut()] * 5)
    context = new_pack_context(fake)
    user = SimpleNamespace(id=7, username="t", full_name="T")
    assert asyncio.run(bot._add_input_sticker(context, user, "sticker")) == (None, 1)


def _media_update(update_id: int, **media) -> Update:
    msg = Message(message_id=update_id, date=datetime.now(timezone.utc), chat=Chat(id=7, type="private"),
                  from_user=User(id=7, first_name="T", is_bot=False), **media)
    return Update(update_id=update_id, message=msg)


def test_a_clip_that_cannot_be_fetched_is_a_note_not_a_crash(bot, monkeypatch):
    async def go_ahead(*_a, **_k):
        return None

    async def reply_text(self, text, **_):
        bot.replies.append(text)

    monkeypatch.setattr(bot, "refuse_new_work", go_ahead)
    monkeypatch.setattr(Message, "reply_text", reply_text)
    fake = FakeBot(get_file=[TimedOut()] * 5)
    context = SimpleNamespace(bot=fake, user_data={"status": None}, application=None)
    update = _media_update(3, document=Document(file_id="v", file_unique_id="w", mime_type="video/mp4",
                                                file_name="a.mp4"))
    assert asyncio.run(bot._receive_video_media(update, context)) == bot.EDITING
    assert fake.count("get_file") == 3
    assert len(bot.replies) == 1 and bot.replies[0].startswith("Couldn't convert that: Timed out")


def test_a_whatsapp_pack_that_cannot_be_fetched_is_a_note_not_a_crash(bot, monkeypatch):
    async def go_ahead(*_a, **_k):
        return None

    notes = []

    async def refresh(context, lang, note="", **_):
        notes.append(note)

    monkeypatch.setattr(bot, "refuse_new_work", go_ahead)
    monkeypatch.setattr(bot, "_refresh_status", refresh)
    fake = FakeBot(get_file=[TimedOut()] * 5)
    context = SimpleNamespace(bot=fake, user_data={}, application=None)
    update = _media_update(4, document=Document(file_id="z", file_unique_id="zz", mime_type="application/zip",
                                                file_name="pack.wastickers"))
    assert asyncio.run(bot.receive_whatsapp_import(update, context)) == bot.EDITING
    assert fake.count("get_file") == 3
    assert notes and notes[-1].startswith("Couldn't convert that: Timed out")
