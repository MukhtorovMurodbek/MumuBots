"""The 220 ⚡ gift is claimed by writing to ConvertBot or DownloaderBot: the claim handler, driven end to end.

    python tests/promo_claim_scenarios.py [convert_bot downloader_bot]

The owner (2026-10-18): "each person claims their gift automatically with the first message they send to
convert/downloader bot". `shared_features.attach_promo_claim` is that handler, registered in a group of its own
after the flood gate's. Each bot runs its real main() on tests/sim.py's in-process Telegram, in a process of its own
(both have modules called bot, db and i18n), and people write to it. family_link's `promo_window` and
`claim_promo_gift` are replaced by fakes that follow their contracts, so what is checked is the handler and not the
gift (tests/test_promo.py and credit_scenarios.py hold that):

  1  the gift not open: a message gives nothing and sends no notice; nothing is remembered, so when it opens the next
     message claims; once the window is over, a message costs nothing at all
  2  open: the first message (and a bare /start) makes one claim, the bot's usual answer, and the notice as a
     message of its own after it, in the person's language (en, uz, ru)
  3  the second message makes no claim call at all; with the process's memory dropped it makes one, is told
     PROMO_HAD, and sends no notice
  4  two messages before the first claim finishes: one claim call
  5  a button, an edited message, a group: no claim
  6  a claim that raises: no notice, nothing remembered, the next message claims again and succeeds
  7  PROMO_CLOSED: no notice, remembered
  8  the bot's own answer is never held up by the claim, even one that hangs until the test lets it go
  9  gift_claimed in the bot's i18n: en, uz and ru, the same placeholders as family_link's fallback, the English one equal

Needs a Postgres (TEST_DATABASE_URL, or the local default). No network, and no .env read (ENV_FILES=off). To see a
check catch breakage, copy the folders into a scratch folder, break the copy and run this again from there.
"""
import os
import subprocess
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
DSN = os.environ.get("TEST_DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/botfamily_test")
PREFIX = {"convert_bot": "CBOT", "downloader_bot": "DBOT"}

CHILD = r'''
import asyncio, os, re, sys, threading, time
from datetime import datetime, timedelta, timezone
from pathlib import Path
ROOT = Path(os.environ["PROMO_ROOT"])
BOT = os.environ["PROMO_BOT"]
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, os.getcwd())
sys.path.insert(1, str(ROOT / "shared"))
import sim, db, i18n, shared_features, family_link
import bot as subject

failures, checks, crashes = [], 0, []
def check(label, ok, detail=""):
    global checks
    checks += 1
    print(("  ok   " if ok else "  FAIL ") + label + ("" if ok else f"\n       {detail}"))
    if not ok:
        failures.append(label)

def placeholders(text):
    return sorted(set(re.findall(r"\{(\w+)\}", text)))

KEY = family_link.PROMO_KEY
PROMO_DONE_KEY = shared_features.PROMO_DONE_KEY

# --- the gift, as family_link would answer, following the contracts -------------------------------------------
state = {}
def reset():
    state.clear()
    state.update(window=None, given=set(), calls=[], gate=None, boom=set(), closed=False)
    shared_features._promo_state["over"] = False
    shared_features._promo_claiming.clear()

def open_window():
    started = datetime.now(timezone.utc) - timedelta(hours=1)
    state["window"] = (started, family_link.promo_closes_for(started))

def fake_window(fresh=False):
    return state["window"]

def fake_claim(user_id):
    state["calls"].append(user_id)
    if state["gate"] is not None:
        state["gate"].wait(10)                # a claim that takes its time (it runs in a thread)
    if user_id in state["boom"]:
        state["boom"].discard(user_id)
        raise RuntimeError("the database is away")
    if state["closed"]:
        return family_link.PROMO_CLOSED, None
    if user_id in state["given"]:
        return family_link.PROMO_HAD, None
    state["given"].add(user_id)
    return family_link.PROMO_GIVEN, family_link.promo_expiry(datetime.now(timezone.utc))

family_link.promo_window, family_link.claim_promo_gift = fake_window, fake_claim

async def settle(condition, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if condition():
            return True
        await asyncio.sleep(0.01)
    return condition()

async def quiet():
    """Long enough for a claim task that was going to run to have run."""
    await asyncio.sleep(0.25)

UIDS = iter(range(7100, 7900))
def newbie(s, lang="en"):
    uid = next(UIDS)
    db.set_user_language(uid, lang)
    return s.person(uid, f"Person{uid}")

def notice(lang):
    return family_link.promo_notice(lang, family_link.promo_expiry(datetime.now(timezone.utc)))

def notices(person, lang):
    return [m for m in person.chat if m.get("from", {}).get("is_bot") and m.get("text") == notice(lang)]

def claims_of(person):
    return state["calls"].count(person.id)

async def main():
    db.init_db()
    tele = sim.FakeTelegram(bot_username="promotest_bot")
    s, _ = await sim.build(subject, tele)
    async def record(update, context):
        crashes.append(f"{type(context.error).__name__}: {context.error}")
    s.app.add_error_handler(record)

    # ---- the wiring: a group of its own, after the flood gate's -----------------------------------------------
    handlers = s.app.handlers
    mine = [h for h in handlers.get(-2, []) if getattr(h.callback, "__name__", "") == "_claim_on_message"]
    check("the handler is registered in group -2, alone in it (one handler runs per group)",
          len(mine) == 1 and len(handlers.get(-2, [])) == 1, {g: [getattr(h.callback, "__name__", "?") for h in hs] for g, hs in handlers.items() if g < 0})
    check("... after the flood gate's group, so somebody being throttled claims nothing",
          any(g < -2 and any(getattr(h.callback, "__name__", "") == "_gate" for h in hs) for g, hs in handlers.items()))

    # ---- 1. the gift is not open ------------------------------------------------------------------------------
    print("\n== 1. the gift not open ==")
    reset()
    p = newbie(s)
    await p.send("/help")
    await quiet()
    check("not started: the bot answers as it always does", subject.build_help_text("en") in (p.chat[-1].get("text") or ""), p.transcript())
    check("... gives nothing and sends no notice", state["calls"] == [] and notices(p, "en") == [] and len(p.chat) == 2, p.transcript())
    check("... and remembers nothing (it may start later)", PROMO_DONE_KEY not in s.app.user_data[p.id])
    open_window()
    await p.send("/help")
    check("it starts: the very next message claims, and is told", await settle(lambda: notices(p, "en")) and claims_of(p) == 1, p.transcript())

    reset()
    state["window"] = (datetime.now(timezone.utc) - timedelta(days=20), datetime.now(timezone.utc) - timedelta(days=6))
    p = newbie(s)
    await p.send("/help")
    await quiet()
    check("a window that is over: no claim, no notice", state["calls"] == [] and notices(p, "en") == [] and len(p.chat) == 2, p.transcript())
    check("... and the process now knows it is over", shared_features._promo_state["over"] is True)
    open_window()                                  # it never reopens; this only shows that a message now costs nothing at all
    q = newbie(s)
    await q.send("/help")
    await quiet()
    check("... so a later message makes no claim, and not even a read of the gift's state", state["calls"] == [] and notices(q, "en") == [])

    # ---- 2. open: the first message -------------------------------------------------------------------------
    print("\n== 2. the first message ==")
    for lang in ("en", "uz", "ru"):
        reset()
        open_window()
        p = newbie(s, lang)
        await p.send("/help")
        got = await settle(lambda: notices(p, lang))
        check(f"[{lang}] one claim, for this person", claims_of(p) == 1 and state["calls"] == [p.id], state["calls"])
        check(f"[{lang}] the bot's usual answer, then the notice as a message of its own after it",
              got and len(p.chat) == 3 and subject.build_help_text(lang) in (p.chat[1].get("text") or "")
              and p.chat[2].get("text") == notice(lang), p.transcript())
        check(f"[{lang}] the notice is in {lang}: it is not the English one" if lang != "en" else "[en] the notice is the English one",
              (notice(lang) != notice("en")) == (lang != "en") and "220" in notice(lang), notice(lang)[:80])

    reset()
    open_window()
    p = newbie(s, "ru")
    await p.send("/start")
    got = await settle(lambda: notices(p, "ru"))
    check("a bare /start is a first message too: one claim, the greeting, then the notice",
          got and claims_of(p) == 1 and len(p.chat) == 3 and p.chat[2].get("text") == notice("ru"), p.transcript())

    # ---- 2b. new to this bot: the language for the notice ---------------------------------------------------
    # The ad sends people to a bot they have never used: nothing chosen here yet, and their first /start shows the
    # language picker. The notice follows their Telegram app's language, never a cached guess, and writes none.
    print("\n== 2b. new to this bot ==")
    for code, want in (("uz", "uz"), ("ru-RU", "ru"), ("en-GB", "en"), ("de", "en"), (None, "en")):
        reset()
        open_window()
        uid = next(UIDS)
        p = s.person(uid, f"Person{uid}")              # has chosen no language in this bot
        p.language_code = code
        await p.send("/start")
        got = await settle(lambda: notices(p, want))
        check(f"Telegram set to {code!r}, nothing chosen here: one claim and the notice in {want}",
              got and claims_of(p) == 1, p.transcript())
        check(f"... and no language was chosen for them by it ({code!r})",
              db.get_user_language(uid) is None and s.app.user_data.get(uid, {}).get("lang") in (None, "en"),
              (db.get_user_language(uid), s.app.user_data.get(uid, {}).get("lang")))
    reset()
    open_window()
    p = newbie(s, "ru")
    p.language_code = "uz"
    await p.send("/help")
    check("a language chosen in this bot wins over the Telegram app's", bool(await settle(lambda: notices(p, "ru"))),
          p.transcript())
    reset()
    open_window()
    uid = next(UIDS)
    p = s.person(uid, f"Person{uid}")
    p.language_code = "ru"
    s.app.user_data[uid]["lang"] = "en"                # what get_lang caches for someone who never chose
    await p.send("/help")
    check("a cached 'en' that nobody chose does not win over the Telegram app's language",
          bool(await settle(lambda: notices(p, "ru"))), p.transcript())

    # ---- 3. the second message ------------------------------------------------------------------------------
    print("\n== 3. the second message ==")
    await p.send("/help")
    await quiet()
    await p.send("/help")
    await quiet()
    check("later messages make no claim call at all (it is remembered in the process)", claims_of(p) == 1, state["calls"])
    check("... and send no second notice", len(notices(p, "ru")) == 1)
    s.app.user_data[p.id].pop(PROMO_DONE_KEY, None)
    await p.send("/help")
    await settle(lambda: claims_of(p) == 2)
    await quiet()
    check("with the process's memory dropped: one claim, answered PROMO_HAD", claims_of(p) == 2, state["calls"])
    check("... and no notice (it was already given)", len(notices(p, "ru")) == 1, p.transcript())
    check("... remembered again", s.app.user_data[p.id].get(PROMO_DONE_KEY) == KEY)

    # ---- 4. two at once -------------------------------------------------------------------------------------
    print("\n== 4. two messages before the first claim finishes ==")
    reset()
    open_window()
    state["gate"] = threading.Event()
    p = newbie(s)
    await p.send("/help")
    await p.send("/help")
    await p.send("/start")
    await asyncio.sleep(0.1)
    state["gate"].set()
    got = await settle(lambda: notices(p, "en"))
    await quiet()
    check("three messages while the claim hangs: one claim call", claims_of(p) == 1 and got, (state["calls"], p.transcript()))
    check("... and one notice", len(notices(p, "en")) == 1)
    check("... and the person is no longer marked as being claimed for", p.id not in shared_features._promo_claiming)

    # ---- 5. a button, an edit, a group ----------------------------------------------------------------------
    print("\n== 5. not a new private message ==")
    reset()
    p = newbie(s)
    mid = await p.send("hello there")             # not open yet: no claim
    await quiet()
    open_window()
    tele._store(p.id, {"message_id": tele.next_message_id(p.id), "date": 1_700_000_000,
                       "chat": {"id": p.id, "type": "private"}, "from": dict(tele.bot_user), "text": "A menu",
                       "reply_markup": {"inline_keyboard": [[{"text": "Hi", "callback_data": "help:full"}]]}})
    await p.tap("Hi")
    await quiet()
    check("a tap on a button makes no claim", state["calls"] == [] and notices(p, "en") == [], state["calls"])
    before = len(crashes)
    await p.edit(mid, "hello again")
    await quiet()
    check("an edited message makes no claim", state["calls"] == [], state["calls"])
    # Not this feature's: both bots' catch-all text handler (unrecognized_message) answers update.message, which an
    # edit does not have, so an edited message ends in its error handler. Found by this test, reported, left alone.
    check("... (the one crash an edit causes is that old one, in the bot's own text handler, not the claim)",
          all("NoneType" in c and ("reply_text" in c or "'text'" in c) for c in crashes[before:]), crashes[before:])
    del crashes[before:]
    group = {"message_id": 1, "date": 1_700_000_000, "chat": {"id": -1001234, "type": "supergroup", "title": "A group"},
             "from": {"id": p.id, "is_bot": False, "first_name": "Person"}, "text": "hello everyone"}
    tele._store(-1001234, group)
    await s._process({"update_id": tele.next_update_id(), "message": group})
    await quiet()
    check("a message in a group makes no claim", state["calls"] == [], state["calls"])
    robot = 7990
    from_a_bot = {"message_id": 1, "date": 1_700_000_000, "chat": {"id": robot, "type": "private"},
                  "from": {"id": robot, "is_bot": True, "first_name": "Some bot"}, "text": "hello"}
    tele._store(robot, from_a_bot)
    await s._process({"update_id": tele.next_update_id(), "message": from_a_bot})
    await quiet()
    check("a message from a bot makes no claim", state["calls"] == [], state["calls"])
    await p.send("/help")
    check("... and none of that used up the person's claim: their next message makes it",
          await settle(lambda: notices(p, "en")) and claims_of(p) == 1, p.transcript())

    # ---- 6. a claim that raises -----------------------------------------------------------------------------
    print("\n== 6. a claim that fails ==")
    reset()
    open_window()
    p = newbie(s)
    state["boom"].add(p.id)
    await p.send("/help")
    await settle(lambda: claims_of(p) == 1 and p.id not in shared_features._promo_claiming)
    await quiet()
    check("the claim raises: the bot's answer is there, no notice", len(p.chat) == 2 and notices(p, "en") == [], p.transcript())
    check("... nothing is remembered, and the person is free to claim again",
          PROMO_DONE_KEY not in s.app.user_data[p.id] and p.id not in shared_features._promo_claiming)
    await p.send("/help")
    check("the next message claims again and succeeds, with the notice",
          await settle(lambda: notices(p, "en")) and claims_of(p) == 2 and len(notices(p, "en")) == 1, p.transcript())
    check("nothing crashed", not crashes, crashes[-2:])

    # ---- 7. closed ---------------------------------------------------------------------------------------------
    print("\n== 7. PROMO_CLOSED ==")
    reset()
    open_window()
    state["closed"] = True
    p = newbie(s)
    await p.send("/help")
    await settle(lambda: claims_of(p) == 1)
    await quiet()
    check("the claim says it is closed: no notice", notices(p, "en") == [] and len(p.chat) == 2, p.transcript())
    check("... remembered", s.app.user_data[p.id].get(PROMO_DONE_KEY) == KEY)
    await p.send("/help")
    await quiet()
    check("... so the next message makes no claim", claims_of(p) == 1, state["calls"])

    # ---- 8. the answer is never held up ---------------------------------------------------------------------
    print("\n== 8. the bot's own answer ==")
    reset()
    open_window()
    state["gate"] = threading.Event()
    p = newbie(s)
    try:
        await asyncio.wait_for(p.send("/help"), 3)
        in_time = True
    except asyncio.TimeoutError:
        in_time = False
    check("with the claim hanging, the update is still finished at once", in_time)
    check("... and the bot's answer is there, with no notice yet",
          subject.build_help_text("en") in (p.chat[-1].get("text") or "") and notices(p, "en") == [], p.transcript())
    state["gate"].set()
    check("the claim is let go: the notice follows the answer", await settle(lambda: notices(p, "en")) and len(p.chat) == 3, p.transcript())
    reset()
    open_window()
    state["gate"] = threading.Event()
    q = newbie(s)
    await q.send("/language")
    check("a command the claim knows nothing about is answered normally while it hangs",
          len(q.chat) == 2 and q.chat[-1].get("reply_markup"), q.transcript())
    state["gate"].set()
    await settle(lambda: notices(q, "en"))

    # ---- 9. the texts ---------------------------------------------------------------------------------------
    print("\n== 9. gift_claimed ==")
    plain = family_link._PLAIN["gift_claimed"]
    for lang in ("en", "uz", "ru"):
        text = i18n.STRINGS[lang].get("gift_claimed")
        check(f"[{lang}] gift_claimed is in the bot's i18n, with the same placeholders as the fallback",
              bool(text) and placeholders(text) == placeholders(plain), (placeholders(text or ""), placeholders(plain)))
    check("the English one is exactly the fallback", i18n.STRINGS["en"]["gift_claimed"] == plain)
    check("uz and ru are their own words", i18n.STRINGS["uz"]["gift_claimed"] != plain and i18n.STRINGS["ru"]["gift_claimed"] != plain)
    check("nothing crashed", not crashes, crashes[-2:])

    with db.pooled() as conn:
        conn.execute('DROP SCHEMA "%s" CASCADE' % os.environ["DB_SCHEMA"])
        conn.commit()
    print(f"{checks} checks, FAILURES: {len(failures)}")
    db.close_pool()
    return 1 if failures else 0

reset()
raise SystemExit(asyncio.run(main()))
'''


def main() -> int:
    bots = sys.argv[1:] or list(PREFIX)
    failed = []
    for bot in bots:
        print(f"\n=== {bot} ===", flush=True)
        prefix = PREFIX[bot]
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1", ENV_FILES="off",
                   PROMO_ROOT=str(ROOT), PROMO_BOT=bot, DATABASE_URL=DSN, DB_SCHEMA=f"{bot}_promosim",
                   DEPLOY_SAFETY="off", FLOOD_UPDATES_PER_MINUTE="0", SIBLING_BOTS="", FAMILY_BUS="off",
                   **{f"{prefix}_TOKEN": "1:AA", f"{prefix}_USERNAME": "promotest_bot", f"{prefix}_ADMIN_ID": "1"})
        # The bot as tests/run.py mirrors it, when there is a mirror (to run a broken copy); else the source.
        mirrored = ROOT / "tests" / ".mirror" / bot
        result = subprocess.run([sys.executable, "-"], input=CHILD.encode("utf-8"),
                                cwd=mirrored if mirrored.exists() else ROOT / bot, env=env)
        if result.returncode:
            failed.append(bot)
    print("\nFAILED: " + ", ".join(failed) if failed else "\nboth bots give the gift to a first message, once")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
