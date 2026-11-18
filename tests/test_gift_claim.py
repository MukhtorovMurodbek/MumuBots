"""Since 2.2.0 (2026-11-18): the 220 ⚡ gift is claimed on purpose, with the offer's button or /gift,
within 3 days of its start, and every gift can be spent until 1 December 2026."""
import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from conftest import load

STARTED = datetime(2026, 11, 17, tzinfo=timezone.utc)
CLOSES = datetime(2026, 12, 3, tzinfo=timezone.utc)


class _Bot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, reply_markup=None, **_):
        self.sent.append((chat_id, text, reply_markup))


def _context():
    return SimpleNamespace(bot=_Bot(), user_data={})


@pytest.fixture
def gift(monkeypatch):
    sf = load("convert_bot", "shared_features")
    link = load("convert_bot", "family_link")
    given = set()
    monkeypatch.setattr(link, "promo_window", lambda fresh=False: (STARTED, CLOSES))
    monkeypatch.setattr(link, "promo_given_to", lambda uid: uid in given)
    monkeypatch.setattr(link, "_language_of", lambda uid: "en")

    def claim(uid):
        if uid in given:
            return link.PROMO_HAD, None
        given.add(uid)
        return link.PROMO_GIVEN, link.promo_expiry(datetime.now(timezone.utc))

    monkeypatch.setattr(link, "claim_promo_gift", claim)
    return sf, link, given


def test_claiming_lasts_three_days_and_every_gift_ends_on_1_december():
    link = load("convert_bot", "family_link")
    assert link.PROMO_CLAIM_DAYS == 3
    assert link.promo_closes_for(datetime(2026, 11, 18, 5, tzinfo=timezone.utc)) == datetime(2026, 11, 22, tzinfo=timezone.utc)
    for claimed in (datetime(2026, 11, 18, tzinfo=timezone.utc), datetime(2026, 11, 21, 23, tzinfo=timezone.utc)):
        assert link.promo_expiry(claimed) == datetime(2026, 12, 2, tzinfo=timezone.utc)
    assert link.promo_spend_by() == "01.12.2026"


def test_the_first_message_gets_an_offer_with_the_dates_and_a_button_not_the_credit(gift):
    sf, link, given = gift
    context = _context()
    asyncio.run(sf._offer_gift(context, 5, 5))
    (_, text, markup), = context.bot.sent
    assert "Claim it by 02.12.2026 (UTC)" in text and "spent until 01.12.2026" in text and "/gift" in text
    button = markup.inline_keyboard[0][0]
    assert button.text == "🎁 Claim 220 ⚡" and button.callback_data == sf.PROMO_CALLBACK and given == set()


def test_the_button_or_gift_claims_it_once(gift):
    sf, link, given = gift
    context = _context()
    asyncio.run(sf._claim_and_tell(context, 5, 5))
    asyncio.run(sf._claim_and_tell(context, 5, 5))
    first, second = (text for _, text, _ in context.bot.sent)
    assert "01.12.2026" in first and given == {5}
    assert second.startswith("🎁 This gift has already been claimed")


def test_nothing_is_offered_to_somebody_who_had_it_and_closed_says_so(gift, monkeypatch):
    sf, link, given = gift
    given.add(9)
    context = _context()
    asyncio.run(sf._offer_gift(context, 9, 9))
    assert context.bot.sent == []
    monkeypatch.setattr(link, "claim_promo_gift", lambda uid: (link.PROMO_CLOSED, None))
    asyncio.run(sf._claim_and_tell(context, 7, 7))
    assert context.bot.sent[0][1] == "🎁 No gift can be claimed right now."


@pytest.mark.parametrize("lang", ["en", "uz", "ru"])
def test_the_texts_name_both_dates_in_every_language(lang):
    for folder in ("convert_bot", "downloader_bot"):
        link = load(folder, "family_link")
        offer = link.promo_offer(lang, CLOSES)
        assert "02.12.2026" in offer and "01.12.2026" in offer and "/gift" in offer and "{" not in offer
        for key in ("gift_claim_button", "gift_already_had", "gift_not_open"):
            assert link.phrase(key, lang, amount=220) != key
    ad = load("anon_bot", "family_link").promo_ad(lang, CLOSES)
    assert "/gift" in ad and "01.12.2026" in ad and "{" not in ad
