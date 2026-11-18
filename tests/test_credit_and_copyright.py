"""Since 2.2.0 (MumuCombined, 2026-11-18): every file DownloaderBot sends carries a copyright note, a
person is told when the operator adds credit by hand, and "can't be sent" names both ceilings."""
import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import load

dl = load("downloader_bot", "bot")
manager = load("manager_bot", "bot")
TERMS = "https://github.com/x/y/blob/main/LEGAL.md#downloaderbot-terms"


def _caption(monkeypatch, url, credit, lang="en"):
    monkeypatch.setattr(dl, "COPYRIGHT_URL", url)
    monkeypatch.setattr(dl, "BOT_USERNAME", "mumu_downloader_bot")
    return dl._media_caption(lang, credit)


def test_every_file_carries_the_copyright_note_with_the_terms_link(monkeypatch):
    caption = _caption(monkeypatch, TERMS, credit=False)
    assert caption.startswith("<i>This material may be protected by copyright.")
    assert f'<a href="{TERMS}">details and contact</a>' in caption and "via @" not in caption
    assert _caption(monkeypatch, TERMS, credit=True).endswith("\n\n⬇️ via @mumu_downloader_bot")
    for lang in ("uz", "ru"):
        assert f'<a href="{TERMS}">' in _caption(monkeypatch, TERMS, credit=False, lang=lang)
    assert _caption(monkeypatch, "", credit=False) == "<i>This material may be protected by copyright.</i>"


def test_the_large_file_route_sends_its_caption_as_html(monkeypatch):
    big_files = load("downloader_bot", "big_files")
    jobs = []

    async def run(job, progress, waiting=None):
        jobs.append(job)

    monkeypatch.setattr(big_files, "_run", run)
    monkeypatch.setattr(big_files, "usable", lambda chat_id: True)
    asyncio.run(big_files.send_document(1, Path(__file__), "talk.mp4", "<i>note</i>", html=True))
    asyncio.run(big_files.send_document(1, Path(__file__), "talk.mp4", "plain"))
    assert [job["parse_mode"] for job in jobs] == ["html", None]


def test_too_big_names_both_ceilings():
    i18n = load("downloader_bot", "i18n")
    for lang in ("en", "uz", "ru"):
        text = i18n.t(lang, "large_files_too_big", max=2000, minutes=180)
        assert "2000" in text and "180" in text and "{" not in text


class _Bot:
    def __init__(self, fail=None):
        self.sent, self.fail = [], fail

    async def send_message(self, chat_id, text, **_):
        if self.fail:
            raise self.fail
        self.sent.append((chat_id, text))


@pytest.mark.parametrize("folder", ["downloader_bot", "convert_bot", "sticker_bot", "anon_bot"])
def test_every_public_bot_can_tell_somebody_their_new_balance(folder, monkeypatch):
    link = load(folder, "family_link")
    monkeypatch.setattr(link, "_language_of", lambda user_id: "ru")
    bot = _Bot()
    output, _, _ = asyncio.run(link._cmd_tellcredit(SimpleNamespace(bot=bot), ["6440918071", "0", "100"]))
    assert output == "told" and bot.sent[0][0] == 6440918071 and "Баланс обновлён" in bot.sent[0][1]
    assert "from 0 ⚡ to 100 ⚡" in link.phrase("credit_updated", "en", before=0, after=100)
    assert "0 ⚡ dan 100 ⚡ ga" in link.phrase("credit_updated", "uz", before=0, after=100)


def test_a_bot_that_cannot_reach_them_says_so(monkeypatch):
    link = load("convert_bot", "family_link")
    monkeypatch.setattr(link, "_language_of", lambda user_id: None)
    output, _, _ = asyncio.run(link._cmd_tellcredit(SimpleNamespace(bot=_Bot(fail=RuntimeError("blocked"))),
                                                    ["5", "0", "100"]))
    assert output.startswith("not reached")
    assert "tellcredit" in link.COMMANDS and "tellcredit" in link.COMMAND_HELP


class _Message:
    def __init__(self):
        self.replies = []

    async def reply_text(self, text, **_):
        self.replies.append(text)


def _update(chat=77, admin=1):
    return SimpleNamespace(effective_chat=SimpleNamespace(id=chat), effective_user=SimpleNamespace(id=admin),
                           message=_Message())


def test_the_operator_s_credit_is_told_by_the_first_bot_that_reaches_them(monkeypatch):
    queued = []
    monkeypatch.setattr(manager.db, "queue_command", lambda *a: queued.append(a) or len(queued))
    monkeypatch.setattr(manager, "_CREDIT_NOTES", {})
    asyncio.run(manager._tell_credit(_update(), 6440918071, 0, 100))
    assert queued == [("downloaderbot", "tellcredit", "6440918071 0 100", 1, 77)]
    bot = _Bot()
    context = SimpleNamespace(bot=bot)
    asyncio.run(manager._deliver_credit_note(context, manager._CREDIT_NOTES.pop(1),
                                             {"ok": True, "output": "not reached (Forbidden)", "status": "done"}))
    assert queued[-1][0] == "convertbot" and bot.sent == []
    asyncio.run(manager._deliver_credit_note(context, manager._CREDIT_NOTES.pop(2),
                                             {"ok": True, "output": "told", "status": "done"}))
    assert bot.sent == [(77, "📨 6440918071 was told through ConvertBot: 0 → 100 ⚡.")]


def test_when_no_bot_reaches_them_the_operator_hears_why(monkeypatch):
    queued = []
    monkeypatch.setattr(manager.db, "queue_command", lambda *a: queued.append(a) or len(queued))
    monkeypatch.setattr(manager, "_CREDIT_NOTES", {})
    asyncio.run(manager._tell_credit(_update(), 5, 10, 60))
    bot = _Bot()
    for _ in manager.CREDIT_NOTE_BOTS:
        note = manager._CREDIT_NOTES.pop(max(manager._CREDIT_NOTES))
        asyncio.run(manager._deliver_credit_note(SimpleNamespace(bot=bot), note,
                                                 {"ok": False, "output": "", "status": "timeout"}))
    assert len(queued) == len(manager.CREDIT_NOTE_BOTS)
    assert bot.sent[0][1].startswith("📨 5 could not be told about the new balance.")
