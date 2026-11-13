"""Since 2.1.6: an error says an anonymous technical report was saved, and "Send more details" is
offered only where who it was matters, or once a fault keeps happening to one person; the bots
keep anonymous daily counts of what was done (ManagerBot's /stats); ConvertBot takes a Telegram
GIF that has no file name."""
import asyncio
import types

import pytest

from conftest import connect, load

sf = load("convert_bot", "shared_features")
problems = load("convert_bot", "problems")
i18n = load("convert_bot", "i18n")


@pytest.fixture()
def fresh(monkeypatch):
    monkeypatch.setattr(sf, "_repeats", type(sf._repeats)())
    monkeypatch.setattr(sf, "note_occurrence", lambda *a: None)
    sf.remember_chat_lang(1, "en")
    sf.remember_chat_lang(2, "en")


def send(chat, code):
    kwargs = {}
    _, _, text = asyncio.run(sf.note_problem(chat, None, "It broke." + problems.code_line(code), kwargs))
    return text, sf._has_report(kwargs.get("reply_markup"))


def test_a_download_timeout_asks_for_details_only_once_it_keeps_happening(fresh):
    first, button = send(1, "CV-DOWNLOAD")
    assert not button
    assert first == "It broke.\nAnonymous technical report saved.\n🆔 CV-DOWNLOAD"
    assert not send(1, "CV-DOWNLOAD")[1]
    assert not send(2, "CV-DOWNLOAD")[1]               # someone else's count is their own
    third, button = send(1, "CV-DOWNLOAD")
    assert button and "You can opt in to send more details." in third


def test_crash_erase_feedback_and_a_stray_reply_always_ask(fresh):
    for code in ("FM-CRASH", "CV-CRASH", "FM-ERASE", "FM-FEEDBACK-FAILED", "AN-REPLY-NO-MATCH"):
        text, button = send(1, code)
        assert button, code
        assert text.endswith(i18n.t("en", "problem_logged_note_optin") + "\n" + problems.MARK + code)


def test_a_message_that_explains_itself_never_asks_and_has_no_report_line(fresh):
    for _ in range(5):
        text, button = send(1, "CV-NO-EXTENSION")
        assert not button and "report" not in text.lower()


def test_report_markup_counts_the_occurrence_it_is_for(fresh):
    """ConvertBot builds the button before the message goes out: the third time already has it."""
    for _ in range(2):
        send(1, "CV-TIMEOUT")
    assert sf.report_markup("en", "CV-TIMEOUT", "ABCDEF", chat_id=1) is not None
    assert sf.report_markup("en", "CV-TIMEOUT", "ABCDEF", chat_id=2) is None
    assert sf.report_markup("en", "CV-RESTARTED", "ABCDEF") is None    # refunded already: nobody needed
    assert sf.report_markup("en", "FM-CRASH", "ABCDEF") is not None


def test_an_edit_does_not_count_again(fresh):
    asyncio.run(sf.note_problem(1, 7, "x" + problems.code_line("ST-TG-TIMEOUT"), {}))
    for _ in range(4):
        asyncio.run(sf.note_problem(1, 7, "x" + problems.code_line("ST-TG-TIMEOUT"), {}))
    assert len(sf._repeats[(1, "ST-TG-TIMEOUT")]) == 1


def test_a_note_is_replaced_not_doubled_when_the_button_comes(fresh):
    once = sf._with_logged_note("It broke." + problems.code_line("CV-TIMEOUT"), "CV-TIMEOUT", "en")
    again = sf._with_logged_note(once, "CV-TIMEOUT", "en", offered=True)
    assert again.count("Anonymous technical report saved.") == 1
    assert "You can opt in to send more details." in again


def test_every_bot_and_language_has_the_new_words_and_none_mention_the_owner():
    for folder in ("sticker_bot", "convert_bot", "downloader_bot", "anon_bot"):
        tr = load(folder, "i18n")
        for lang in ("en", "uz", "ru"):
            for key in ("problem_logged_note", "problem_logged_note_optin", "report_button", "report_disclaimer"):
                assert tr.t(lang, key, code="X", incident="Y") != key, (folder, lang, key)
            assert "owner" not in tr.t(lang, "problem_logged_note").lower()
    for key in ("job_reason_timeout", "job_reason_crash", "job_reason_send_failed"):
        assert "owner" not in i18n.t("en", key, minutes=5).lower()
    assert "owner" not in i18n.t("en", "job_reason_too_large", target="PNG", mb=60, limit=50).lower()


def test_every_code_that_always_asks_is_a_known_fault():
    for code in problems.WANTS_DETAILS:
        assert problems.is_code(code) and problems.reportable(code), code
        assert problems.brief(code), code


# --- anonymous daily counts --------------------------------------------------------------------

@pytest.fixture()
def family(monkeypatch):
    link = load("downloader_bot", "family_link")
    link.init_family_schema()
    monkeypatch.setattr(link, "_enabled", True)
    monkeypatch.setattr(link, "_bot_id", "downloaderbot")
    with connect() as conn:
        conn.execute("DELETE FROM family.daily_stats")
        conn.commit()
    stats = load("downloader_bot", "shared_features")
    stats.take_stats()
    return stats


def test_counts_add_up_per_day_and_say_nothing_about_who(family):
    for site in ("tiktok", "tiktok", "instagram"):
        family.note_stat("link_site", site)
    family._flush_stats_now()
    family.note_stat("link_site", "tiktok")
    family._flush_stats_now()
    with connect() as conn:
        columns = [r[0] for r in conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_schema='family' "
            "AND table_name='daily_stats'").fetchall()]
        rows = dict(conn.execute("SELECT key, n FROM family.daily_stats WHERE metric='link_site'").fetchall())
    assert sorted(columns) == ["bot_id", "day", "key", "metric", "n"]
    assert rows == {"tiktok": 3, "instagram": 1}


def test_too_many_keys_fold_into_other(family):
    for i in range(family.STAT_MAX_KEYS + 5):
        family.note_stat("conversion", f"k{i}")
    stats = family.take_stats()
    assert len(stats) == family.STAT_MAX_KEYS + 1 and stats[("conversion", "other")] == 5


def test_managerbot_reports_them(family, monkeypatch):
    family.note_stat("link_site", "tiktok", 3)
    family.note_stat("link_site", "instagram")
    family._flush_stats_now()
    manager = load("manager_bot", "bot")
    monkeypatch.setattr(manager.family_link, "_enabled", True)
    report = manager._stats_report(30)
    assert "Links delivered, by site — 4" in report and "tiktok: 3 (75%)" in report


# --- ConvertBot and a GIF with no file name ---------------------------------------------------

def _media(file_name=None, mime_type=None, file_size=1):
    return types.SimpleNamespace(file_name=file_name, mime_type=mime_type, file_size=file_size, file_id="x")


def test_ext_of_file():
    bot = load("convert_bot", "bot")
    assert bot._ext_of_file(_media(None, "video/mp4"), "") == "mp4"
    assert bot._ext_of_file(_media(None, "image/gif"), "") == "gif"
    assert bot._ext_of_file(_media("clip.webm", "video/mp4"), "") == "webm"   # the name wins
    assert bot._ext_of_file(_media(None, "application/octet-stream"), "") == ""
    assert bot._ext_of_file(_media(None, None), "mp4") == "mp4"


def test_a_gif_with_no_name_is_taken(monkeypatch):
    """A forwarded GIF: animation AND document set, no file name. It used to get CV-NO-EXTENSION."""
    bot = load("convert_bot", "bot")
    replies = []

    async def reply_text(text, **_):
        replies.append(text)

    async def lang(*_):
        return "en"

    gif = _media(None, "video/mp4", file_size=10 ** 12)   # too big on purpose: stops before downloading
    msg = types.SimpleNamespace(document=gif, animation=gif, photo=None, video=None, audio=None,
                                voice=None, chat_id=1, reply_text=reply_text)
    update = types.SimpleNamespace(message=msg, effective_user=types.SimpleNamespace(id=1),
                                   effective_chat=types.SimpleNamespace(id=1, type="private"))
    monkeypatch.setattr(bot.i18n, "get_lang", lang)
    monkeypatch.setattr(bot, "max_file_mb_in", lambda chat_id: 1)
    asyncio.run(bot.convert_receive_file(update, types.SimpleNamespace(user_data={})))
    assert replies and replies[0] != bot.i18n.t("en", "unknown_extension")
