"""AnonBot's ad for the 220 ⚡ gift, driven end to end against a real Postgres.

    python tests/promo_ad_scenarios.py

The owner (2026-10-18): anyone who messages AnonBot while the gift is open sees an advertisement for it, once per
person, with a button for each of the two bots that give it. `promo_ad_note` runs in a handler group of its own, after the
reactions note's, on tests/sim.py's in-process Telegram, so what is checked is what a person's chat would hold:
the ad is the last thing in it, after the bot's own answer. family_link's two reads (promo_window, promo_given_to) are
replaced by fakes that follow their contracts; everything else is real, including the table promo_seen and
`/deletemydata`.

  A  the rules, one by one: only a new private message from a person (not a button, an edit, a reaction, a group, a bot);
     not on /start with an inbox link, but on a bare /start; not while a question waits; not on the update the reactions
     note answered; remembered in the process; not before it opens or after it closes (and then nothing is remembered);
     not without both bots' buttons (and then nothing is recorded); not to anyone who has the gift; a database
     error shows nothing and records nothing; the ad itself: HTML, no link preview, the two URL buttons, in the person's
     language, after the bot's own answer; a send that fails is not tried again
  B  the texts: English equal to family_link's fallback, the same placeholders in en, uz and ru, only <i> in them
  C  once per person: a restart (the process forgetting) does not show it again; each person has their own
  D  promo_seen in the database: claim_promo_seen is True once per (person, announcement), exactly one of several at
     once, and /deletemydata removes the person's rows and nobody else's

Needs a Postgres (TEST_DATABASE_URL, or the local default). No network. To see a check catch breakage, copy the folders
into a scratch folder, break the copy and run this again from there.
"""
from __future__ import annotations

import asyncio
import itertools
import logging
import os
import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(ROOT / "tests"))
# AnonBot imports its own modules by bare name, so its folder is the root.
os.chdir(ROOT / "anon_bot")
sys.path.insert(0, str(ROOT / "anon_bot"))
sys.path.insert(1, str(ROOT / "shared"))

# Never whatever DATABASE_URL the shell happens to hold: this test writes and drops a schema.
os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/botfamily_test")
os.environ["DB_SCHEMA"] = "anon_bot_promosim"
os.environ.setdefault("ABOT_TOKEN", "1:AA")
os.environ["ABOT_USERNAME"] = "anonbot_test"
os.environ["ABOT_ADMIN_ID"] = "5001"
os.environ["ENV_FILES"] = "off"
SIBLINGS = ("stickerbot:StickerBot:sticker_test,convertbot:ConvertBot:convert_test,"
            "downloaderbot:DownloaderBot:download_test,anonbot:AnonBot:anonbot_test")
os.environ["SIBLING_BOTS"] = SIBLINGS
os.environ["DEPLOY_SAFETY"] = "off"
os.environ["ABOT_MSGS_PER_MINUTE"] = "0"
os.environ["ABOT_NEW_CONV_COOLDOWN_SEC"] = "0"
os.environ["FLOOD_UPDATES_PER_MINUTE"] = "0"
os.environ["ABOT_REACTION_NOTE_SECONDS"] = "0.2"

import sim  # noqa: E402
import db  # noqa: E402
import i18n  # noqa: E402
import anon_logic  # noqa: E402
import live_message  # noqa: E402
import shared_features  # noqa: E402
import bot as ab  # noqa: E402
import family_link  # noqa: E402
from telegram.error import BadRequest  # noqa: E402

crashes: list[tuple[str, str]] = []


def _collect_event(level, kind, message, details=None):
    if level in ("error", "critical"):
        crashes.append((kind, message))


shared_features.set_event_hook(_collect_event)

failures: list[str] = []
_checks = 0


def check(label: str, condition, detail="") -> bool:
    global _checks
    _checks += 1
    if condition:
        print(f"  ok   {label}")
    else:
        failures.append(label)
        print(f"  FAIL {label}" + (f"\n       {detail}" if detail else ""))
    return bool(condition)


def section(title: str) -> None:
    print(f"\n== {title} ==")


# ---------------------------------------------------------------------------
# the gift, as family_link's two reads would answer, and the world
# ---------------------------------------------------------------------------

KEY = family_link.PROMO_KEY
gift = SimpleNamespace(window=None, given=set(), raises={}, calls=[])


def _promo_window(fresh=False):
    gift.calls.append(("window", fresh))
    if "window" in gift.raises:
        raise gift.raises["window"]
    return gift.window


def _promo_given_to(user_id):
    gift.calls.append(("given", user_id))
    if "given" in gift.raises:
        raise gift.raises["given"]
    return user_id in gift.given


family_link.promo_window, family_link.promo_given_to = _promo_window, _promo_given_to


def now() -> datetime:
    return datetime.now(timezone.utc)


def open_window():
    """Started an hour ago; claims close at the end of the 14th day after that. Returns when they close."""
    started = now() - timedelta(hours=1)
    gift.window = (started, family_link.promo_closes_for(started))
    return gift.window[1]


def close_window():
    gift.window = None


def the_ad(lang: str) -> str:
    return family_link.promo_ad(lang, gift.window[1])


def is_ad(message: dict) -> bool:
    text = message.get("text") or ""
    return bool(message.get("from", {}).get("is_bot")) and text.startswith(("🎉 Mumu bots", "🎉 Mumu botlarida", "🎉 У Mumu"))


def ads_in(person) -> list[dict]:
    return [m for m in person.chat if is_ad(m)]


def seen_rows(user_id: int | None = None) -> int:
    with db.pooled() as conn:
        if user_id is None:
            return conn.execute("SELECT count(*) FROM promo_seen").fetchone()[0]
        return conn.execute("SELECT count(*) FROM promo_seen WHERE user_id = %s", (user_id,)).fetchone()[0]


def cached(s, person):
    return s.app.user_data[person.id].get(ab.PROMO_AD_KEY)


def reset_tables() -> None:
    with db.pooled() as conn:
        for table in ("anon_relay", "anon_follower_state", "anon_conversations", "anon_blocks", "anon_links",
                      "user_settings", "donation_prompts", "activity_events", "promo_seen"):
            conn.execute(f"DELETE FROM {table}")
        conn.commit()


wire: list[tuple[str, dict]] = []                 # every call the bot made to Telegram, as it was made
UIDS = itertools.count(7100)


async def fresh():
    reset_tables()
    gift.window, gift.given, gift.raises = None, set(), {}
    gift.calls.clear()
    wire.clear()
    anon_logic._recent_messages.clear()
    anon_logic._recent_opens.clear()
    anon_logic._reaction_notes.clear()
    shared_features._flood_window.clear()
    shared_features._flood_told.clear()
    shared_features._feedback_sent.clear()
    ab._albums.clear()
    live_message._last_incoming.clear()
    tele = sim.FakeTelegram(bot_username="anonbot_test")
    real_api = tele.api

    async def spying_api(endpoint, data):
        wire.append((endpoint, dict(data)))
        return await real_api(endpoint, data)

    tele.api = spying_api
    driver, _ = await sim.build(ab, tele)
    shared_features.set_event_hook(_collect_event)

    async def _record_error(update, context):
        crashes.append((type(context.error).__name__, str(context.error)))

    driver.app.add_error_handler(_record_error)
    return driver, tele


def newbie(s, lang: str = "en"):
    """Somebody who has picked a language (so /start has no picker to answer first) and has never been shown the ad."""
    user_id = next(UIDS)
    db.set_user_language(user_id, lang)
    return s.person(user_id, f"Person{user_id}")


async def said(person, text: str, **kwargs) -> list[dict]:
    """What the bot put in this person's chat in answer to this message, in order."""
    before = len(person.chat)
    await person.send(text, **kwargs)
    return person.chat[before + 1:]


class DebugLog(logging.Handler):
    """AnonBot's debug lines, to see what they say (and what they must not)."""

    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.lines: list[str] = []

    def emit(self, record):
        self.lines.append(record.getMessage())


debug_log = DebugLog()
ab.logger.addHandler(debug_log)
ab.logger.setLevel(logging.DEBUG)
ab.logger.propagate = False


def ad_buttons(message: dict) -> list[list[tuple[str, str]]]:
    markup = message.get("reply_markup") or {}
    return [[(b["text"], b.get("url")) for b in row] for row in markup.get("inline_keyboard", [])]


# ---------------------------------------------------------------------------
# A -- the rules
# ---------------------------------------------------------------------------

async def group_a_message_kinds():
    section("A1. only a new message in a private chat, from a person")
    s, tele = await fresh()
    open_window()

    p = newbie(s)
    tele._store(p.id, {"message_id": tele.next_message_id(p.id), "date": 1_700_000_000,
                       "chat": {"id": p.id, "type": "private"}, "from": dict(tele.bot_user), "text": "A menu",
                       "reply_markup": {"inline_keyboard": [[{"text": "Hi", "callback_data": "help:full"}]]}})
    await p.tap("Hi")
    check("a tap on a button shows no ad, and records nothing",
          ads_in(p) == [] and seen_rows(p.id) == 0 and cached(s, p) is None, p.transcript())
    await p.send("/help")
    check("... and the next message is where it comes: a tap did not use up the one chance", len(ads_in(p)) == 1, p.transcript())

    close_window()
    p = newbie(s)
    mid = await p.send("hello there")
    open_window()
    await p.edit(mid, "hello again")
    check("an edited message shows no ad", ads_in(p) == [] and seen_rows(p.id) == 0, p.transcript())
    await p.send("/help")
    check("... the next new message does", len(ads_in(p)) == 1, p.transcript())

    close_window()
    p = newbie(s)
    mid = await p.send("hi")
    open_window()
    await p.react(mid, "👍")
    check("a reaction shows no ad", ads_in(p) == [] and seen_rows(p.id) == 0, p.transcript())
    await p.send("/help")
    check("... the next new message does", len(ads_in(p)) == 1, p.transcript())

    p = newbie(s)
    await s._process({"update_id": tele.next_update_id(), "message": {
        "message_id": 1, "date": 1_700_000_000, "chat": {"id": -1001234, "type": "supergroup", "title": "A group"},
        "from": {"id": p.id, "is_bot": False, "first_name": "Person"}, "text": "hello everyone"}})
    check("a message in a group shows no ad, and records nothing for the person", seen_rows(p.id) == 0
          and not any(is_ad(m) for chats in tele.chats.values() for m in chats if m["chat"]["id"] < 0), tele.chats.get(-1001234))
    await p.send("/help")
    check("... the same person's private message still gets it", len(ads_in(p)) == 1, p.transcript())

    robot = 7998
    await s._process({"update_id": tele.next_update_id(), "message": {
        "message_id": 1, "date": 1_700_000_000, "chat": {"id": robot, "type": "private"},
        "from": {"id": robot, "is_bot": True, "first_name": "Some bot"}, "text": "/help"}})
    check("a message from another bot shows no ad, and records nothing", seen_rows(robot) == 0
          and not any(is_ad(m) for m in tele.chat_of(robot)), tele.chat_of(robot))
    check("nothing crashed", not crashes, crashes[-2:])


async def group_a_start():
    section("A2. /start: an inbox link waits, a bare /start does not")
    s, tele = await fresh()
    open_window()

    p = newbie(s)
    got = await said(p, "/start q_notarealtoken")
    check("/start with an inbox link shows no ad: that person is about to write their message",
          ads_in(p) == [] and seen_rows(p.id) == 0 and cached(s, p) is None, p.transcript())
    check("... though the bot did answer it", len(got) >= 1, p.transcript())
    got = await said(p, "hello")
    check("their next message gets the ad, after the bot's answer", is_ad(p.chat[-1]) and len(ads_in(p)) == 1, p.transcript())

    p = newbie(s)
    await p.send("/start@anonbot_test q_notarealtoken")
    check("/start@botname with an inbox link: the same", ads_in(p) == [] and seen_rows(p.id) == 0, p.transcript())

    p = newbie(s)
    await p.send("/start")
    check("a bare /start shows the welcome and then the ad",
          is_ad(p.chat[-1]) and ab.build_help_text("en") in (p.chat[-2].get("text") or ""), p.transcript())

    user_id = next(UIDS)
    p = s.person(user_id, f"Person{user_id}")          # has never chosen a language
    await p.send("/start")
    check("somebody brand new: /start shows the language picker and no ad, and nothing is recorded",
          ads_in(p) == [] and seen_rows(p.id) == 0 and cached(s, p) is None, p.transcript())
    picker = p.chat[-1].get("message_id")
    await p.tap_data("setlang:uz", picker)
    check("... choosing a language shows no ad either (a tap is not a message)",
          ads_in(p) == [] and seen_rows(p.id) == 0, p.transcript())
    await p.send("salom")
    ads = ads_in(p)
    check("... their next message has it, in the language they chose",
          len(ads) == 1 and (ads[0].get("text") or "").startswith("🎉 Mumu botlarida"), p.transcript())

    user_id = next(UIDS)
    p = s.person(user_id, f"Person{user_id}")          # never chose a language...
    s.app.user_data[user_id]["lang"] = "en"            # ...though get_lang has cached "en" for them
    await p.send("hello")
    check("a cached 'en' that nobody chose is not a language: no ad, nothing recorded",
          ads_in(p) == [] and seen_rows(p.id) == 0 and cached(s, p) is None, p.transcript())

    for command in ("/deletemydata", "/privacy", "/terms", "/deletemydata@anonbot_test"):
        p = newbie(s)
        await p.send(command)
        check(f"{command} is answered without the ad, and nothing is recorded",
              ads_in(p) == [] and seen_rows(p.id) == 0 and cached(s, p) is None, p.transcript())
        await p.send("hello")
        check(f"... the next message after {command} has it", len(ads_in(p)) == 1, p.transcript())

    p = newbie(s)
    await p.send("/startover")
    check("/startover is another command, not a /start with something after it: an ordinary message, so the ad",
          len(ads_in(p)) == 1, p.transcript())


async def group_a_prompt_and_note():
    section("A3. not in the middle of a question; A4. not on the update the reactions note answered")
    s, tele = await fresh()
    open_window()

    p = newbie(s)
    await p.send("/feedback")
    prompt = p.chat[-1]["message_id"]
    check("while /feedback waits for its answer there is no ad",
          p.chat[-1].get("text") == i18n.t("en", "feedback_ask") and ads_in(p) == [], p.transcript())
    await p.send("love it", reply_to=prompt)
    await p.send("/help")
    check("once the answer is in, the ad comes (exactly once)", len(ads_in(p)) == 1, p.transcript())

    p = newbie(s)
    with db.pooled() as conn:                              # as somebody from before 2.1.0
        conn.execute("UPDATE user_settings SET knows_reactions = NULL WHERE user_id = %s", (p.id,))
        conn.commit()
    await p.send("/link")
    note = i18n.t("en", "reactions_update_note")
    check("the reactions note comes after the answer, and on that message the ad does not",
          p.chat[-1].get("text") == note and ads_in(p) == [] and seen_rows(p.id) == 0, p.transcript())
    check("... the update the note answered is remembered, in the process",
          s.app.user_data[p.id].get(ab.NOTE_UPDATE_KEY) is not None)
    await p.send("/link")
    check("the next message has the ad, after the answer (the note is not repeated)",
          is_ad(p.chat[-1]) and "t.me/anonbot_test" in (p.chat[-2].get("text") or "")
          and sum(1 for m in p.chat if m.get("text") == note) == 1, p.transcript())


async def group_a_cache_and_window():
    section("A5. remembered in the process; A6. only while the gift is open")
    s, tele = await fresh()
    closes = open_window()

    p = newbie(s)
    await p.send("/help")
    check("the first message: the ad", len(ads_in(p)) == 1 and cached(s, p) == KEY, p.transcript())
    gift.calls.clear()
    for _ in range(3):
        await p.send("/help")
    check("after it, a message costs no question at all: the gift's state is not even read",
          gift.calls == [] and len(ads_in(p)) == 1, (gift.calls, p.transcript()))

    gift.window = None
    p = newbie(s)
    await p.send("/help")
    check("not started yet: no ad, nothing recorded, nothing remembered (it may open later)",
          ads_in(p) == [] and seen_rows(p.id) == 0 and cached(s, p) is None, p.transcript())
    started = now() + timedelta(hours=1)
    gift.window = (started, family_link.promo_closes_for(started))
    await p.send("/help")
    check("a window that has not begun: the same", ads_in(p) == [] and seen_rows(p.id) == 0 and cached(s, p) is None)
    gift.window = (now() - timedelta(days=3), now() - timedelta(seconds=1))
    await p.send("/help")
    check("a window that closed a second ago (closing is exclusive): the same",
          ads_in(p) == [] and seen_rows(p.id) == 0 and cached(s, p) is None)
    gift.window = (now() - timedelta(days=3), now() + timedelta(hours=5))
    await p.send("/help")
    check("... and the moment it is open, the next message has the ad",
          len(ads_in(p)) == 1 and ads_in(p)[0]["text"] == family_link.promo_ad("en", gift.window[1]), p.transcript())
    check("the claim-by day in it is the last day of the window (the day before the midnight it closes)",
          family_link.promo_ad("en", closes).split("Claim it by ")[1].split(";")[0]
          == (closes - timedelta(days=1)).strftime("%d.%m.%Y"))


async def group_a_buttons_and_state():
    section("A7. both bots' buttons or no ad; A8. not to anyone who has it; errors show and record nothing")
    s, tele = await fresh()
    open_window()

    p = newbie(s)
    os.environ["SIBLING_BOTS"] = "stickerbot:StickerBot:sticker_test,anonbot:AnonBot:anonbot_test"
    try:
        await p.send("/help")
        check("no link to ConvertBot or DownloaderBot in this deployment: no ad, nothing recorded or remembered",
              ads_in(p) == [] and seen_rows(p.id) == 0 and cached(s, p) is None, p.transcript())
    finally:
        os.environ["SIBLING_BOTS"] = SIBLINGS
    await p.send("/help")
    check("... and with the links back, the next message has it: the chance was not spent", len(ads_in(p)) == 1, p.transcript())

    p = newbie(s)
    gift.given.add(p.id)
    await p.send("/help")
    check("somebody who already has the gift sees no ad, and nothing about them is recorded",
          ads_in(p) == [] and seen_rows(p.id) == 0, p.transcript())
    check("... it is remembered in the process, so the question is not asked again",
          cached(s, p) == KEY and ("given", p.id) in gift.calls)
    gift.calls.clear()
    await p.send("/help")
    check("... and the next message asks nothing", gift.calls == [])

    for name, patch in (("promo_window", "window"), ("promo_given_to", "given"), ("claim_promo_seen", "seen")):
        p = newbie(s)
        before = len(debug_log.lines)
        real_claim = ab.claim_promo_seen
        if patch == "seen":
            def broken_claim(user_id, promo):
                raise RuntimeError(f"secret detail for {user_id}")
            ab.claim_promo_seen = broken_claim
        else:
            gift.raises[patch] = RuntimeError(f"secret detail for {p.id}")
        try:
            await p.send("/help")
        finally:
            ab.claim_promo_seen = real_claim
            gift.raises.clear()
        lines = debug_log.lines[before:]
        ours = [line for line in lines if line.startswith("The gift ad")]
        check(f"{name} fails: no ad, no row, nothing remembered, no crash",
              ads_in(p) == [] and seen_rows(p.id) == 0 and cached(s, p) is None and not crashes, (p.transcript(), crashes[-1:]))
        check("... a debug line says what kind of error it was, and neither the user id nor the error's words",
              len(ours) == 1 and "RuntimeError" in ours[0] and str(p.id) not in ours[0]
              and not any("secret" in line for line in lines), lines)
        await p.send("/help")
        check("... the next message tries again, and shows the ad", len(ads_in(p)) == 1 and seen_rows(p.id) == 1, p.transcript())


async def group_a_the_ad():
    section("A9. the ad itself")
    s, tele = await fresh()
    closes = open_window()

    for lang in ("en", "uz", "ru"):
        p = newbie(s, lang)
        wire.clear()
        await p.send("/help")
        ad = p.chat[-1]
        check(f"[{lang}] the ad is the last thing in the chat, right after the bot's own answer to /help",
              is_ad(ad) and p.chat[-2].get("text") == ab.build_help_text(lang), p.transcript())
        check(f"[{lang}] it is the text family_link.promo_ad makes for {lang}", ad.get("text") == family_link.promo_ad(lang, closes),
              (ad.get("text") or "")[:120])
        sends = [data for endpoint, data in wire if endpoint == "sendMessage" and (data.get("text") or "") == ad.get("text")]
        preview = (sends[0].get("link_preview_options") if sends else None)
        preview = preview.to_dict() if hasattr(preview, "to_dict") else (preview or {})
        check(f"[{lang}] sent as HTML, with the link preview off",
              len(sends) == 1 and sends[0].get("parse_mode") == "HTML" and preview.get("is_disabled") is True, sends)
        check(f"[{lang}] one row of two buttons: DownloaderBot first, then ConvertBot, each a link to its bot",
              ad_buttons(ad) == [[("↗️ DownloaderBot", "https://t.me/download_test"), ("↗️ ConvertBot", "https://t.me/convert_test")]],
              ad_buttons(ad))
        check(f"[{lang}] the bots are named in it as people can tap them", "@download_test" in ad["text"] and "@convert_test" in ad["text"]
              and "220 ⚡" in ad["text"] and "28" in ad["text"])
        check(f"[{lang}] and the person's one chance is written down", seen_rows(p.id) == 1 and cached(s, p) == KEY)

    p = newbie(s)
    real_api = tele.api

    async def failing_api(endpoint, data):
        if endpoint == "sendMessage" and (data.get("text") or "").startswith("🎉 Mumu bots"):
            raise BadRequest("Bad Request: simulated")
        return await real_api(endpoint, data)

    tele.api = failing_api
    before = len(debug_log.lines)
    await p.send("/help")
    tele.api = real_api
    check("an ad that cannot be sent: no crash, a debug line, and the person's chance is spent all the same",
          ads_in(p) == [] and seen_rows(p.id) == 1 and not crashes and any("BadRequest" in l for l in debug_log.lines[before:]),
          (p.transcript(), debug_log.lines[before:], crashes[-1:]))
    await p.send("/help")
    await p.send("/help")
    check("... and it is not tried again, then or later", ads_in(p) == [] and seen_rows(p.id) == 1, p.transcript())
    check("nothing crashed", not crashes, crashes[-2:])


# ---------------------------------------------------------------------------
# B -- the texts
# ---------------------------------------------------------------------------

def group_b_texts():
    section("B. the texts")

    def placeholders(text):
        return sorted(set(re.findall(r"\{(\w+)\}", text)))

    en, uz, ru = (i18n.STRINGS[lang]["gift_ad"] for lang in ("en", "uz", "ru"))
    check("the English ad is exactly family_link's fallback", en == family_link._PLAIN["gift_ad"])
    check("the same six placeholders in en, uz and ru",
          placeholders(en) == placeholders(uz) == placeholders(ru) == sorted(["users", "amount", "download", "convert", "claim_by", "days"]),
          (placeholders(en), placeholders(uz), placeholders(ru)))
    for lang, text in (("en", en), ("uz", uz), ("ru", ru)):
        check(f"[{lang}] the only markup is the italic last paragraph", re.findall(r"</?[a-z]+>", text) == ["<i>", "</i>"]
              and text.rstrip().endswith("</i>") and "&" not in text and text.count("\n\n") == 1, text[-80:])
        rendered = family_link.promo_ad(lang, datetime(2026, 11, 4, tzinfo=timezone.utc))
        check(f"[{lang}] it fills in: no brace left, 220 and 28 and the last day, within one message",
              "{" not in rendered and "}" not in rendered and "220" in rendered and "28" in rendered and "03.11.2026" in rendered
              and len(rendered.encode("utf-16-le")) // 2 <= 4096, rendered[:100])
    check("uz and ru are their own words, not the English", uz != en and ru != en and "Mumu botlarida" in uz and "Mumu ботов" in ru)
    check("the ad is a different text from the notice a claimer reads (the notice is plain text)",
          "<i>" not in family_link._PLAIN["gift_claimed"])


# ---------------------------------------------------------------------------
# C -- once per person
# ---------------------------------------------------------------------------

async def group_c_once():
    section("C. once per person")
    s, tele = await fresh()
    open_window()

    a, b = newbie(s), newbie(s, "ru")
    await a.send("/help")
    await b.send("/help")
    check("each person gets it, in their own language", len(ads_in(a)) == 1 and len(ads_in(b)) == 1
          and ads_in(a)[0]["text"] != ads_in(b)[0]["text"], (a.transcript(), b.transcript()))
    check("... and has one row of their own", seen_rows(a.id) == 1 and seen_rows(b.id) == 1 and seen_rows() == 2)
    for _ in range(3):
        await a.send("/help")
    check("further messages bring nothing more", len(ads_in(a)) == 1)

    s.app.user_data[a.id].clear()                       # the process forgot everything: a restart
    gift.calls.clear()
    await a.send("/help")
    check("a restart (the process forgetting them) does not show it again: promo_seen says so",
          len(ads_in(a)) == 1 and seen_rows(a.id) == 1 and ("given", a.id) in gift.calls, (a.transcript(), gift.calls))
    check("... and the process remembers again", cached(s, a) == KEY)
    check("nothing crashed", not crashes, crashes[-2:])


# ---------------------------------------------------------------------------
# D -- promo_seen in the database
# ---------------------------------------------------------------------------

async def group_d_database():
    section("D. promo_seen, and /deletemydata")
    s, tele = await fresh()
    open_window()
    with db.pooled() as conn:
        columns = conn.execute(
            "SELECT column_name, data_type, is_nullable, column_default FROM information_schema.columns "
            "WHERE table_schema = %s AND table_name = 'promo_seen' ORDER BY ordinal_position",
            (os.environ["DB_SCHEMA"],)).fetchall()
        keys = conn.execute(
            "SELECT kcu.column_name FROM information_schema.table_constraints tc JOIN information_schema.key_column_usage kcu "
            "ON tc.constraint_name = kcu.constraint_name AND tc.table_schema = kcu.table_schema "
            "WHERE tc.table_schema = %s AND tc.table_name = 'promo_seen' AND tc.constraint_type = 'PRIMARY KEY' "
            "ORDER BY kcu.ordinal_position", (os.environ["DB_SCHEMA"],)).fetchall()
    check("the table has user_id (bigint), promo (text) and seen_at (timestamptz, default now()), none of them null",
          [(c[0], c[1], c[2]) for c in columns] == [("user_id", "bigint", "NO"), ("promo", "text", "NO"),
                                                    ("seen_at", "timestamp with time zone", "NO")]
          and "now()" in (columns[2][3] or ""), columns)
    check("... and its key is (user_id, promo)", [k[0] for k in keys] == ["user_id", "promo"], keys)

    check("claim_promo_seen is True the first time for a person and an announcement", db.claim_promo_seen(9001, KEY) is True)
    check("... False after that, however often", [db.claim_promo_seen(9001, KEY) for _ in range(3)] == [False] * 3)
    check("... another announcement is another chance", db.claim_promo_seen(9001, "gift-other") is True)
    check("... another person is another chance", db.claim_promo_seen(9002, KEY) is True)
    check("... and the rows are exactly those three", seen_rows() == 3 and seen_rows(9001) == 2)

    barrier = threading.Barrier(8)

    def race(_):
        barrier.wait()
        return db.claim_promo_seen(9003, KEY)

    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(race, range(8)))
    check("eight claims at the same moment: exactly one is True", results.count(True) == 1 and seen_rows(9003) == 1, results)

    erased, bystander = newbie(s), newbie(s)
    await erased.send("/help")
    await bystander.send("/help")
    db.claim_promo_seen(erased.id, "gift-other")
    check("two people have been shown the ad (and one something else too)",
          seen_rows(erased.id) == 2 and seen_rows(bystander.id) == 1)
    await erased.send("/deletemydata")
    await erased.tap("Erase it")
    check("/deletemydata removes every announcement the person was shown", seen_rows(erased.id) == 0, erased.transcript())
    check("... and nobody else's", seen_rows(bystander.id) == 1 and seen_rows(9001) == 2)
    check("erase_user on its own does the same, and reports it in its count",
          db.erase_user(9001) >= 2 and seen_rows(9001) == 0)
    check("nothing crashed", not crashes, crashes[-2:])


async def main() -> int:
    db.init_db()
    group_b_texts()
    for group in (group_a_message_kinds, group_a_start, group_a_prompt_and_note, group_a_cache_and_window,
                  group_a_buttons_and_state, group_a_the_ad, group_c_once, group_d_database):
        await group()
    with db.pooled() as conn:
        conn.execute(f'DROP SCHEMA "{os.environ["DB_SCHEMA"]}" CASCADE')
        conn.commit()
    print(f"\n{_checks} checks, FAILURES: {len(failures)}")
    for label in failures:
        print("  -", label)
    return 1 if failures else 0


if __name__ == "__main__":
    code = asyncio.run(main())
    try:
        db.close_pool()
    except Exception:
        pass
    sys.exit(code)
