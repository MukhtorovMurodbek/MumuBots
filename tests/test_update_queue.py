"""A crowd never holds more than UPDATES_IN_MEMORY_MAX updates in one bot's memory (the owner,
2026-11-09: RAM): polling waits while the queue and the updates being handled reach the bound,
and goes on as soon as one is done. The stop signal never waits."""
import asyncio
from datetime import datetime, timezone

from telegram import Chat, Message, Update, User

from conftest import load


def _update(n: int, user: int) -> Update:
    chat = Chat(id=user, type="private")
    return Update(update_id=n, message=Message(message_id=n, date=datetime.now(timezone.utc), chat=chat,
                                                from_user=User(id=user, first_name="U", is_bot=False),
                                                text="/start"))


def test_polling_waits_at_the_bound_and_resumes():
    shared = load("sticker_bot", "shared_features")

    async def scenario():
        processor = shared.PerUserUpdateProcessor()
        queue = shared.UpdateQueue(processor, limit=3)
        release = asyncio.Event()

        async def handled():
            await release.wait()

        # Three updates of three people are being handled: the bound is reached.
        running = [asyncio.create_task(processor.do_process_update(_update(n, 100 + n), handled()))
                   for n in range(3)]
        await asyncio.sleep(0)
        assert processor.in_flight == 3 and queue.full_now()
        putting = asyncio.create_task(queue.put(_update(9, 200)))
        await asyncio.sleep(0.05)
        assert not putting.done() and queue.qsize() == 0 and queue.held_back == 1
        # python-telegram-bot's stop signal is not an Update: it never waits.
        await asyncio.wait_for(queue.put(object()), timeout=0.5)
        assert queue.qsize() == 1
        await queue.get()
        # One is done: polling goes on at once (well within the one-second fallback).
        release.set()
        await asyncio.wait_for(putting, timeout=0.5)
        await asyncio.gather(*running)
        assert processor.in_flight == 0 and queue.qsize() == 1

    asyncio.run(scenario())


def test_the_bound_can_be_switched_off():
    shared = load("sticker_bot", "shared_features")

    async def scenario():
        processor = shared.PerUserUpdateProcessor()
        processor.in_flight = 10_000
        queue = shared.UpdateQueue(processor, limit=0)
        await asyncio.wait_for(queue.put(_update(1, 1)), timeout=0.5)
        assert queue.qsize() == 1

    asyncio.run(scenario())


def test_a_dropped_or_failed_update_still_counts_down():
    shared = load("sticker_bot", "shared_features")

    async def scenario():
        processor = shared.PerUserUpdateProcessor()

        async def fails():
            raise RuntimeError("handler failed")

        try:
            await processor.do_process_update(_update(1, 1), fails())
        except RuntimeError:
            pass
        assert processor.in_flight == 0 and processor.done.is_set()

    asyncio.run(scenario())
