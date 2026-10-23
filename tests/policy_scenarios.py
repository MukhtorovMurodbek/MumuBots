"""/privacy, /terms and /deletemydata, driven end to end.

Same harness as `anon_scenarios.py`: AnonBot's real `Application` against
`tests/sim.py`'s in-process Telegram, on a real Postgres. An update goes in
and what comes out is what somebody's chat would actually contain.

AnonBot is the bot to run this against, because it has the most to erase and
the most to break by erasing it: a link other people already hold, a
conversation with a second person on the other end of it, a block list. If
the erase is right here it is right everywhere.

What this is really for is the half a unit test cannot reach. `erase_user`
is SQL, and SQL that has never been executed is a guess -- a column renamed
three versions ago fails at runtime and nowhere else. Every statement in it
runs here, against the real schema, and what is left afterwards is read back.

    python tests/policy_scenarios.py

Needs a Postgres (TEST_DATABASE_URL, or the local default). No network.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(ROOT / "tests"))
os.chdir(ROOT / "anon_bot")
sys.path.insert(0, str(ROOT / "anon_bot"))
sys.path.insert(1, str(ROOT / "shared"))

os.environ.setdefault("DATABASE_URL", os.environ.get(
    "TEST_DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/botfamily_test"))
os.environ["DB_SCHEMA"] = os.environ.get("SIM_DB_SCHEMA", "anon_bot_sim")
os.environ.setdefault("ABOT_TOKEN", "1:AA")
os.environ["ABOT_USERNAME"] = "anonbot_test"
os.environ["ABOT_ADMIN_ID"] = "5001"
os.environ.setdefault("SIBLING_BOTS", "")
os.environ["DEPLOY_SAFETY"] = "off"
os.environ["ABOT_MSGS_PER_MINUTE"] = "0"
os.environ["ABOT_NEW_CONV_COOLDOWN_SEC"] = "0"
os.environ["FLOOD_UPDATES_PER_MINUTE"] = "0"
# The two lines the notices only print once somebody has configured them.
os.environ["OPERATOR_CONTACT"] = "@somebody"
os.environ["PRIVACY_URL"] = "https://example.invalid/PRIVACY.md"

import sim  # noqa: E402
import db  # noqa: E402
import anon_logic  # noqa: E402
import lifecycle  # noqa: E402
import vault  # noqa: E402
import live_message  # noqa: E402
import shared_features  # noqa: E402
from shared_features import ERASE_PREFIX  # noqa: E402
import bot as ab  # noqa: E402

crashes: list[tuple[str, str]] = []
def _collect_event(level, kind, message, details=None):
    if level in ("error", "critical"):
        crashes.append((kind, message))


shared_features.set_event_hook(_collect_event)

OWNER, GUEST, BYSTANDER = 7001, 7002, 7003

failures: list[str] = []
_checks = 0


def check(label: str, condition: bool, detail: str = "") -> bool:
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


TABLES = ("anon_relay", "anon_follower_state", "anon_conversations",
          "anon_blocks", "anon_links", "user_settings",
          "donation_prompts", "activity_events", "star_transactions")


def reset_tables() -> None:
    with db.pooled() as conn:
        for table in TABLES:
            conn.execute(f"DELETE FROM {table}")
        conn.commit()


def P(telegram_id: int) -> int:
    """The pseudonym a table stores in place of this person's id (shared/vault.py)."""
    return vault.pseudonym(telegram_id)


def PAYER(telegram_id: int) -> int:
    """What the Stars ledger stores for a payer: their own stand-in, never P() (anon_bot/db.py PAYER_DOMAIN)."""
    return vault.pseudonym_in(telegram_id, "anon.payer")


def one(sql: str, params=()):
    with db.pooled() as conn:
        row = conn.execute(sql, params).fetchone()
    return row[0] if row else None


async def fresh():
    reset_tables()
    anon_logic._recent_messages.clear()
    anon_logic._recent_opens.clear()
    shared_features._flood_window.clear()
    shared_features._flood_told.clear()
    shared_features._recently_erased.clear()
    shared_features._activity_buffer.clear()
    ab._albums.clear()
    # A new FakeTelegram restarts message ids at 1, but live_message's
    # watermark is module state and would still hold the previous group's
    # highest id -- so every live message in the new world would look stale
    # and be re-sent instead of edited, which is the opposite of what these
    # scenarios mean to exercise.
    live_message._last_incoming.clear()
    tele = sim.FakeTelegram(bot_username="anonbot_test")
    driver, _ = await sim.build(ab, tele)
    # family_link.attach(), which main() has just run, installs its own event
    # hook -- so the collector set at import above was replaced the first time
    # a bot was built, and every "nothing crashed" check after that has been
    # decorative. Put it back, and register an error handler as well: the two
    # reach the same place by different routes, and neither should be the only
    # thing standing between a raised exception and a passing test.
    shared_features.set_event_hook(_collect_event)

    async def _record_error(update, context):
        crashes.append((type(context.error).__name__, str(context.error)))

    driver.app.add_error_handler(_record_error)
    return driver, tele


async def onboard(person) -> None:
    await person.send("/start")
    await person.tap("English")


def donation_row(user_id: int, username: str = "somebody") -> None:
    """A paid donation, the one thing an erase deliberately keeps."""
    db.record_star_invoice(user_id, username, 50, "donation",
                           f"donate:{user_id}", status="paid", currency="XTR")


# ---------------------------------------------------------------------------
# A. the two notices
# ---------------------------------------------------------------------------

async def group_a() -> None:
    section("A. what the bot says it holds")
    driver, _ = await fresh()
    owner = driver.person(OWNER, "Owner")
    await onboard(owner)

    await owner.send("/privacy")
    notice = owner.last()[0]
    check("/privacy answers at all", bool(notice))
    check("/privacy names the pairing it keeps",
          "who is talking to whom" in notice.lower(), notice[:200])
    check("/privacy says message content is not kept",
          "never stored" in notice.lower() or "not kept" in notice.lower())
    check("/privacy is honest about who anonymity is from",
          "not anonymity from the bot" in notice.lower())
    check("/privacy points at the erase command", "/deletemydata" in notice)
    check("/privacy carries the configured contact", "@somebody" in notice)
    check("/privacy carries the published URL", "example.invalid" in notice)
    check("/privacy leaked no key name",
          "privacy_" not in notice and "{" not in notice)

    await owner.send("/terms")
    terms = owner.last()[0]
    check("/terms answers", bool(terms) and "terms_" not in terms and "{" not in terms)
    check("/terms says a hidden name is not a licence",
          "licence" in terms.lower() or "license" in terms.lower())
    check("/terms with no TERMS_URL set omits the line rather than printing an empty one",
          "https://" not in terms.split("@somebody")[0] or "example.invalid" not in terms)

    # In the language the person actually chose.
    await owner.send("/rus")
    await owner.send("/privacy")
    russian = owner.last()[0]
    check("/privacy answers in Russian once Russian is chosen",
          "Конфиденциальность" in russian, russian[:80])
    check("the Russian notice leaked no key name",
          "privacy_" not in russian and "{" not in russian)
    await owner.send("/en")


# ---------------------------------------------------------------------------
# B. saying no
# ---------------------------------------------------------------------------

async def group_b() -> None:
    section("B. asking, and saying no")
    driver, _ = await fresh()
    owner = driver.person(OWNER, "Owner")
    await onboard(owner)
    await owner.send("/link")

    await owner.send("/deletemydata")
    confirmation = owner.chat[-1]["message_id"]
    check("the confirmation says there is no undo", "no undo" in owner.last()[0].lower())
    check("it says what erasing costs this bot specifically",
          "link" in owner.last()[0].lower())
    labels = owner.buttons()
    check("it offers exactly two buttons", len(labels) == 2, str(labels))

    await owner.tap("Keep my data")
    check("saying no erases nothing",
          one("SELECT count(*) FROM anon_links WHERE owner_user_id = %s", (P(OWNER),)) == 1)
    check("and says so", "Nothing was erased" in owner.last()[0], owner.last()[0])
    check("the confirmation's own buttons are gone afterwards",
          not owner.buttons(confirmation), str(owner.buttons(confirmation)))
    check("and /link's buttons are left alone",
          len(owner.buttons()) == 2, str(owner.buttons()))


# ---------------------------------------------------------------------------
# C. saying yes, with somebody else's data next to it
# ---------------------------------------------------------------------------

async def group_c() -> None:
    section("C. erasing, with a bystander alongside")
    driver, _ = await fresh()
    owner = driver.person(OWNER, "Owner")
    guest = driver.person(GUEST, "Guest")
    bystander = driver.person(BYSTANDER, "Bystander")

    await onboard(owner)
    await onboard(guest)
    await onboard(bystander)
    await bystander.send("/link")
    donation_row(OWNER)
    donation_row(BYSTANDER)

    token, _ = db.get_or_create_anon_link(OWNER)
    await guest.send(f"/start q_{token}")
    await guest.send("hello, this is anonymous")
    await owner.send("/blocked")          # touches nothing, just realistic use

    before = one("SELECT count(*) FROM anon_conversations WHERE owner_user_id = %s", (P(OWNER),))
    check("the world is set up: a conversation exists", before == 1, str(before))

    await owner.send("/deletemydata")
    await owner.tap("Erase it")

    check("the link is gone",
          one("SELECT count(*) FROM anon_links WHERE owner_user_id = %s", (P(OWNER),)) == 0)
    check("the conversation is gone",
          one("SELECT count(*) FROM anon_conversations WHERE owner_user_id = %s", (P(OWNER),)) == 0)
    check("the relay rows went with it",
          one("SELECT count(*) FROM anon_relay") == 0)
    check("the language setting is gone",
          one("SELECT count(*) FROM user_settings WHERE user_id = %s", (P(OWNER),)) == 0)
    check("the activity rows are gone",
          one("SELECT count(*) FROM activity_events WHERE user_id = %s", (P(OWNER),)) == 0)

    check("the payment record is kept",
          one("SELECT count(*) FROM star_transactions WHERE user_id = %s", (PAYER(OWNER),)) == 1)
    check("but without the username on it",
          one("SELECT username_sealed FROM star_transactions WHERE user_id = %s", (PAYER(OWNER),)) is None)

    check("the bystander's link is untouched",
          one("SELECT count(*) FROM anon_links WHERE owner_user_id = %s", (P(BYSTANDER),)) == 1)
    check("the bystander's language is untouched",
          one("SELECT count(*) FROM user_settings WHERE user_id = %s", (P(BYSTANDER),)) == 1)
    sealed_name = one("SELECT username_sealed FROM star_transactions WHERE user_id = %s", (PAYER(BYSTANDER),))
    check("the bystander's username is untouched",
          sealed_name is not None and vault.open_text(sealed_name, "stars.username") == "somebody")

    said = owner.last()[0]
    check("the person is told how much went", "erased" in said.lower(), said)
    check("and invited back", "/start" in said)

    # M2: the confirming tap was itself an update, buffered as activity before
    # the handler ran, and the next flush would write the person straight back
    # (an activity row, and a settings row that makes them reachable again).
    check("M2: the erase took the confirming tap out of the activity buffer",
          OWNER not in shared_features._activity_buffer and OWNER in shared_features._recently_erased)
    shared_features._flush_activity_now()
    check("M2: a flush after the erase writes nothing back: no settings row, no activity row",
          one("SELECT count(*) FROM user_settings WHERE user_id = %s", (P(OWNER),)) == 0
          and one("SELECT count(*) FROM activity_events WHERE user_id = %s", (P(OWNER),)) == 0)
    check("M2: ...and the person is not in the broadcast list", OWNER not in db.list_all_users())
    check("M2: ...while the bystander, recorded by the same flush, is",
          one("SELECT count(*) FROM activity_events WHERE user_id = %s", (P(BYSTANDER),)) >= 1)
    # A second, older confirmation tapped afterwards (or any message of theirs
    # in the minutes after) is buffered again; the flush must still skip them.
    await owner.send("/privacy")
    check("M2: an update from the erased person is buffered like any other",
          OWNER in shared_features._activity_buffer)
    shared_features._flush_activity_now()
    check("M2: ...and the flush skips it, for as long as the erase is recent",
          one("SELECT count(*) FROM user_settings WHERE user_id = %s", (P(OWNER),)) == 0
          and OWNER not in db.list_all_users())
    shared_features._recently_erased[OWNER] -= shared_features.ERASED_SKIP_SECONDS + 1
    shared_features._activity_buffer.add(OWNER)
    shared_features._flush_activity_now()
    check("M2: once the erase is older than the skip window they are recorded again, as anyone returning is",
          OWNER in db.list_all_users() and OWNER not in shared_features._recently_erased)
    for fake in range(1, shared_features._FLOOD_MAX_TRACKED + 50):
        shared_features._recently_erased[fake] = 0.0
        shared_features._flood_trim(shared_features._recently_erased)
    check("M2: the record of erased people is bounded",
          len(shared_features._recently_erased) <= shared_features._FLOOD_MAX_TRACKED)
    shared_features._recently_erased.clear()
    db.erase_user(OWNER)


# ---------------------------------------------------------------------------
# D. the taps that were not supposed to happen
# ---------------------------------------------------------------------------

async def group_d() -> None:
    section("D. tapping twice, and tapping an old one")
    driver, _ = await fresh()
    owner = driver.person(OWNER, "Owner")
    await onboard(owner)

    # Two confirmations on screen at once: entirely ordinary, since /start
    # does not clear what came before it.
    await owner.send("/deletemydata")
    first = owner.chat[-1]["message_id"]
    await owner.send("/deletemydata")

    await owner.tap("Erase it")                       # the newer one
    check("the newer confirmation erases", "erased" in owner.last()[0].lower())

    # The older bubble still carries live buttons. This is the case that used
    # to raise BadRequest("message is not modified"): nothing is left to
    # erase, so the second answer is word for word the first.
    await owner.tap("Erase it", message_id=first)
    check("tapping the older confirmation is answered too",
          "erased" in owner.last()[0].lower(), owner.last()[0])
    check("and nothing crashed doing it",
          not crashes, "; ".join(k for k, _ in crashes))

    # And a third time, for the same reason.
    await owner.tap("Erase it", message_id=first)
    check("a third tap is still not a crash", not crashes,
          "; ".join(k for k, _ in crashes))


# ---------------------------------------------------------------------------
# E. what the other side sees
# ---------------------------------------------------------------------------

async def group_e() -> None:
    section("E. the person on the other end")
    driver, _ = await fresh()
    owner = driver.person(OWNER, "Owner")
    guest = driver.person(GUEST, "Guest")
    await onboard(owner)
    await onboard(guest)

    token, _ = db.get_or_create_anon_link(OWNER)
    await guest.send(f"/start q_{token}")
    await guest.send("first message")
    check("the owner received it", any("first message" in t for t in owner.texts()))

    await owner.send("/deletemydata")
    await owner.tap("Erase it")

    # The link every copy of which is now dead.
    await guest.send(f"/start q_{token}")
    check("the dead link is refused, not honoured",
          not any("anonymous" in t.lower() and "#" in t for t in guest.last(2)),
          " | ".join(guest.last(2)))
    check("and the guest is told something rather than nothing",
          bool(guest.last()[0].strip()))

    # Writing into a conversation that no longer exists.
    await guest.send("are you still there?")
    check("a message into the erased conversation does not crash",
          not crashes, "; ".join(k for k, _ in crashes))
    check("and the owner does not receive it",
          not any("still there" in t for t in owner.texts()))


# ---------------------------------------------------------------------------
# F. erasing from the guest's side
# ---------------------------------------------------------------------------

async def group_f() -> None:
    section("F. the guest erases instead")
    driver, _ = await fresh()
    owner = driver.person(OWNER, "Owner")
    guest = driver.person(GUEST, "Guest")
    await onboard(owner)
    await onboard(guest)

    token, _ = db.get_or_create_anon_link(OWNER)
    await guest.send(f"/start q_{token}")
    await guest.send("something")

    await guest.send("/deletemydata")
    await guest.tap("Erase it")

    check("the guest's follower state is gone",
          one("SELECT count(*) FROM anon_follower_state WHERE follower_user_id = %s", (P(GUEST),)) == 0)
    check("the jointly-held conversation goes with it",
          one("SELECT count(*) FROM anon_conversations WHERE follower_user_id = %s", (P(GUEST),)) == 0)
    check("the owner's own link survives -- it was never the guest's to erase",
          one("SELECT count(*) FROM anon_links WHERE owner_user_id = %s", (P(OWNER),)) == 1)
    check("and the owner's language survives",
          one("SELECT count(*) FROM user_settings WHERE user_id = %s", (P(OWNER),)) == 1)

    # The owner replying into a thread whose other half asked to be forgotten.
    await owner.send("hello?")
    check("the owner replying into the erased thread does not crash",
          not crashes, "; ".join(k for k, _ in crashes))

    # And the guest can start over cleanly. They are new again, so the link
    # puts the language picker in front of them before the inbox -- the token
    # is carried across it by pending_start_args, which is the same path any
    # first-time visitor arriving on a link takes.
    await guest.send(f"/start q_{token}")
    check("an erased user meeting a link is asked their language, as a new one is",
          bool(guest.buttons()), str(guest.buttons()))
    await guest.tap("English")
    await guest.send("starting again")
    check("the guest can open a fresh conversation afterwards",
          any("starting again" in t for t in owner.texts()), " | ".join(owner.last(3)))


# ---------------------------------------------------------------------------
# G. the state the process still holds
# ---------------------------------------------------------------------------

async def group_g() -> None:
    section("G. what the process was still holding")
    driver, _ = await fresh()
    owner = driver.person(OWNER, "Owner")
    await onboard(owner)
    await owner.send("/rus")

    check("the language is cached in memory before the erase",
          driver.app.user_data.get(OWNER, {}).get("lang") == "ru",
          str(driver.app.user_data.get(OWNER)))

    await owner.send("/deletemydata")
    await owner.tap_data(shared_features.ERASE_PREFIX + "yes")

    check("the cached language is dropped too, not left to be written back",
          not driver.app.user_data.get(OWNER, {}).get("lang"),
          str(driver.app.user_data.get(OWNER)))
    check("a bot with no state table erases anyway, rather than reporting failure",
          "Готово" in owner.last()[0], owner.last()[0])
    check("and reports it in the language that was just erased, not in English",
          "стёрто" in owner.last()[0], owner.last()[0])

    # And now the production shape of the same thing. The table only exists
    # when deploy safety is on, which the sim turns off -- so it is made here
    # deliberately, and the DELETE that otherwise never runs outside Railway
    # runs against the real schema.
    lifecycle.init_state_table()
    from psycopg.types.json import Jsonb
    saved = []
    for kind, key in (("user", str(OWNER)), ("conversation", f"packs|{OWNER}"), ("user", str(BYSTANDER))):
        stored_key, stored_value = lifecycle._seal_state(kind, key, {})
        saved.append((kind, stored_key, Jsonb(stored_value)))
    with db.pooled() as conn:
        for kind, stored_key, stored_value in saved:
            conn.execute(
                "INSERT INTO runtime_state (kind, key, value) VALUES (%s, %s, %s) "
                "ON CONFLICT (kind, key) DO NOTHING", (kind, stored_key, stored_value))
        conn.commit()
    check("the saved state is sealed: no plain id is a key",
          one("SELECT count(*) FROM runtime_state WHERE key IN (%s, %s, %s)",
              (str(OWNER), f"packs|{OWNER}", str(BYSTANDER))) == 0)
    removed = lifecycle.forget_user(OWNER)
    check("the persisted state rows are deleted for real", removed == 2, str(removed))
    check("and only that person's", one(
        "SELECT count(*) FROM runtime_state WHERE key = %s", (str(P(BYSTANDER)),)) == 1)
    with db.pooled() as conn:
        conn.execute("DROP TABLE runtime_state")
        conn.commit()

    # A returning stranger: the bot must treat them as new, not half-known.
    await owner.send("/start")
    check("a returning erased user is asked their language again, as a new one is",
          any("Xush kelibsiz" in t or "choose your language" in t.lower()
              for t in owner.last(2)), " | ".join(owner.last(2)))


# ---------------------------------------------------------------------------
# H. callback data that was never on a button
# ---------------------------------------------------------------------------

async def group_h() -> None:
    section("H. taps that were never offered")
    driver, _ = await fresh()
    owner = driver.person(OWNER, "Owner")
    await onboard(owner)
    await owner.send("/donate")
    anchor = owner.chat[-1]["message_id"]

    # Every one of these is what a modified client can send back for a button
    # that exists. None of them may raise: an unhandled exception here is a
    # crash report in the owner's chat, sent by anybody who feels like it.
    hostile = [
        "donate:abc", "donate:", "donate:-5", "donate:99999999999",
        "donate:15.5", "donate:٥", "donate:1e9",
        "donatefiat:USD", "donatefiat:XXX:5", "donatefiat:USD:abc",
        "donatefiat::", "donatefiat:USD:999999",
        "donatecustom:XXX", "donatecustom:", "donatecustom:../../etc",
        ERASE_PREFIX, ERASE_PREFIX + "maybe", ERASE_PREFIX + "yes ",
    ]
    for data in hostile:
        await owner.tap_data(data, message_id=anchor)
    check("no callback data raised anything, however malformed",
          not crashes, "; ".join(f"{k}: {m}" for k, m in crashes))

    # And the amounts that were offered still work.
    await owner.tap_data(f"donate:{shared_features.DONATE_STAR_OPTIONS[0]}", message_id=anchor)
    check("a real preset amount still raises an invoice",
          any("invoice" in str(entry.get("endpoint", "")).lower()
              for entry in driver.tele.log), "no sendInvoice in the log")
    check("still nothing crashed", not crashes, "; ".join(k for k, _ in crashes))

    # An erase that was not confirmed must not erase.
    await owner.send("/link")
    await owner.tap_data(ERASE_PREFIX + "no")
    check("a 'no' tap leaves the data alone",
          one("SELECT count(*) FROM anon_links WHERE owner_user_id = %s", (P(OWNER),)) == 1)


async def main() -> int:
    for group in (group_a, group_b, group_c, group_d, group_e, group_f, group_g,
                  group_h):
        await group()

    section("nothing crashed")
    check("no handler raised anything the error handler had to report",
          not crashes, "; ".join(f"{k}: {m}" for k, m in crashes))

    print(f"\n{_checks} checks, FAILURES: {len(failures)}")
    for name in failures:
        print("  -", name)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
