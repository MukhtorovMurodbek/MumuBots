"""What 2.1.2-2.1.4 brought that 2.2.0 keeps (the owner, 2026-11-07: "everything in previous version
should be in the newer version"): StickerBot's count of a run of pictures (2.1.4), now on each
sticker's own reply; /gift for bots from 2.1.1 (2.1.2); /problems named the nightly report up to
2.1.2 (2.1.3)."""
import asyncio
import time
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from telegram import Chat, Document, Message, PhotoSize, Update, User

from conftest import load

STICKER = "sticker_bot"


def _message(update_id: int, user_id: int = 7, **media) -> Update:
    chat = Chat(id=user_id, type="private")
    msg = Message(message_id=update_id, date=datetime.now(timezone.utc), chat=chat,
                  from_user=User(id=user_id, first_name="T", is_bot=False), **media)
    return Update(update_id=update_id, message=msg)


def photo(update_id: int, user_id: int = 7) -> Update:
    return _message(update_id, user_id, photo=[PhotoSize(file_id="f", file_unique_id="u", width=9, height=9)])


def image_file(update_id: int, user_id: int = 7) -> Update:
    return _message(update_id, user_id, document=Document(file_id="d", file_unique_id="v", mime_type="image/jpeg",
                                                          file_name="downloadgram_org_1.jpg"))


def words(update_id: int, user_id: int = 7) -> Update:
    return _message(update_id, user_id, text="😎")


class Queue:
    """python-telegram-bot's own queue, for a bot with the per-person processor switched off."""

    def __init__(self, *waiting):
        self.update_queue = asyncio.Queue()
        self.update_processor = SimpleNamespace()
        for item in waiting:
            self.update_queue.put_nowait(item)

    def take(self):
        return self.update_queue.get_nowait()


def test_the_per_person_queue_lists_who_waits():
    features = load(STICKER, "shared_features")

    async def scenario():
        processor = features.PerUserUpdateProcessor()
        gate = asyncio.Event()
        updates = [photo(n) for n in range(3)]

        async def work():
            await gate.wait()

        tasks = [asyncio.create_task(processor.do_process_update(u, work())) for u in updates]
        await asyncio.sleep(0)
        during = processor.waiting(("user", 7))
        app = SimpleNamespace(update_processor=processor)
        seen = features.waiting_updates(app, updates[0])
        gate.set()
        await asyncio.gather(*tasks)
        return updates, during, seen, processor.waiting(("user", 7)), processor._waiting

    updates, during, seen, after, left = asyncio.run(scenario())
    assert during == updates[1:] and seen == updates[1:]
    assert after == [] and left == {}


def test_queued_items_counts_this_chats_pictures_only():
    bot = load(STICKER, "bot")
    features = load(STICKER, "shared_features")
    processor = features.PerUserUpdateProcessor()
    processor._waiting[("user", 7)] = [image_file(2), words(3), photo(4)]
    context = SimpleNamespace(application=SimpleNamespace(update_processor=processor))
    assert bot._queued_items(context, photo(1)) == 2
    assert bot._queued_items(context, photo(1, user_id=8)) == 0
    # With the processor switched off: python-telegram-bot's queue, as in 2.1.4.
    context = SimpleNamespace(application=Queue(photo(2), words(3), photo(4, user_id=8)))
    assert bot._queued_items(context, photo(1)) == 1


@pytest.fixture()
def adding(monkeypatch):
    bot = load(STICKER, "bot")
    numbers, replies, broken = iter(range(1, 100)), [], set()

    async def add(context, user, sticker):
        if sticker in broken:
            raise RuntimeError("STICKERSET_INVALID")
        n = next(numbers)
        return f"file{n}", n + 10

    async def send_message(chat_id, text, **kwargs):
        # Since 2.1.6 each answer is a reply to its own item, sent with reply_parameters.
        replies.append(text)
        return SimpleNamespace(chat_id=chat_id, message_id=len(replies) + 100)

    async def no_buttons(*args, **kwargs):
        return None

    monkeypatch.setattr(bot, "_add_input_sticker", add)
    monkeypatch.setattr(bot, "_pack_keyboard_for", no_buttons)
    monkeypatch.setattr(bot, "record_error", lambda exc: None)
    bot.replies, bot.broken = replies, broken

    def step(context, update, sticker="ok"):
        """One picture through _batch_item and _add_and_confirm, its reply recorded."""
        item = {"chat": 7, "msg": update.update_id, "kind": "static", "file_id": "f", "set": None, "uid": None,
                "at": time.time()}

        async def inner(update_, context_):
            await bot._add_and_confirm(update_, context_, "en", sticker, item)
            return bot.EDITING

        context.bot = SimpleNamespace(send_message=send_message)
        msg = SimpleNamespace(chat_id=7, message_id=update.update_id)
        wrapped = SimpleNamespace(update_id=update.update_id, message=msg, effective_chat=update.effective_chat,
                                  effective_user=update.effective_user)
        return asyncio.run(bot._batch_item(wrapped, context, inner))

    bot.step = step
    return bot


def session(application) -> SimpleNamespace:
    return SimpleNamespace(application=application, bot=None, user_data={
        "target_pack": "memes_by_bot", "title": "Memes", "sticker_count": 0, "nudge_counted": True})


def test_each_reply_counts_the_run(adding):
    bot = adding
    app = Queue(image_file(2), image_file(3))
    context = session(app)
    bot.step(context, image_file(1))
    assert bot.replies[-1].startswith("✅ Added to “Memes” as #11") and bot.replies[-1].endswith("\n⏳ 1/3")
    app.take()
    bot.step(context, image_file(2))
    assert bot.replies[-1].endswith("\n⏳ 2/3")
    app.take()
    bot.step(context, image_file(3))
    assert bot.replies[-1].endswith("\n✅ 3/3")
    assert context.user_data["batch_open"] is False and context.user_data["sticker_count"] == 3


def test_a_failure_is_counted_and_said_at_the_end(adding):
    bot = adding
    app = Queue(photo(2), photo(3))
    context = session(app)
    bot.step(context, photo(1))
    app.take()
    bot.broken.add("bad")
    bot.step(context, photo(2), sticker="bad")
    # Before the error, whose problem code stays the last line.
    assert bot.replies[-1].startswith("⏳ 2/3\n")
    app.take()
    bot.step(context, photo(3))
    assert bot.replies[-1].endswith("\n⚠️ 3/3 — 1 not added")


def test_a_single_picture_has_no_count_and_a_question_is_no_failure(adding):
    bot = adding
    context = session(Queue())
    bot.step(context, photo(1))
    assert "/" not in bot.replies[-1].splitlines()[-1]
    app = Queue(photo(2))
    context = session(app)

    async def asks(update, context_):
        context_.user_data["batch_handled"] = True  # _sticker_already_in_pack's question
        return bot.EDITING

    asyncio.run(bot._batch_item(photo(1), context, asks))
    app.take()
    bot.step(context, photo(2))
    assert bot.replies[-1].endswith("\n✅ 2/2")


def test_done_forgets_the_run():
    bot = load(STICKER, "bot")
    context = SimpleNamespace(bot=None, user_data={key: 1 for key in bot.BATCH_KEYS})
    asyncio.run(bot._end_status(context, "done"))
    assert not any(key in context.user_data for key in bot.BATCH_KEYS)


def test_every_language_has_the_closing_line():
    i18n = load(STICKER, "i18n")
    for lang in ("en", "uz", "ru"):
        text = i18n.t(lang, "batch_not_added", done=7, total=7, failed=2)
        assert text.startswith("⚠️ 7/7 — ") and "2" in text


def test_gift_reaches_bots_from_2_1_1_and_the_nightly_name():
    manager = load("manager_bot", "bot")
    assert manager._too_old_for("2.1.3", "gift") is None
    assert manager._too_old_for("2.1.1", "gift") is None
    assert manager._too_old_for("2.1.0", "gift") is not None
    assert "/problems → /nightly (its name from 1.7.0 to 2.1.2)" in manager.HELP
