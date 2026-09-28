"""What people write never reaches the database. Asserted, not asserted-to.

Every version of AnonBot's privacy notice says message content is not stored:

    "Messages are relayed and never stored. They exist in the two Telegram
     chats, where each side can delete their own copy, and in no database
     belonging to this bot."

That sentence was false until v1.4.0. A message that did not say which
conversation it answered was HELD -- the whole message, text included, parked
in `user_data` behind a "Send it anyway" button -- and `user_data` is written
to Postgres by `lifecycle.PostgresPersistence`. So the one path a user was
most likely to take by accident was the one path that wrote down what they
had written.

The hold is gone. This is what stops it, or anything like it, coming back.

WHY THIS IS A SUITE OF ITS OWN
    `anon_scenarios.py` runs with DEPLOY_SAFETY=off, because the sim drives
    one process and never redeploys -- so it has no persistence, and "nothing
    was written to the database" would be true there for the wrong reason.
    This one runs with persistence ON, which is how Railway runs it, and
    proves the mechanism is live before trusting a negative from it.

HOW IT LOOKS
    Every message carries a nonce. After each one, the process is made to
    flush, and every text, varchar and json column in the whole schema is
    searched for that nonce -- asked of information_schema rather than from a
    list, so a column added next year is covered on the day it is added.

    python tests/no_storage_scenarios.py

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
os.environ["DB_SCHEMA"] = os.environ.get("SIM_DB_SCHEMA", "anon_bot_nostore")
os.environ.setdefault("ABOT_TOKEN", "1:AA")
os.environ["ABOT_USERNAME"] = "anonbot_test"
os.environ["ABOT_ADMIN_ID"] = "5001"
os.environ.setdefault("SIBLING_BOTS", "")
# The whole point: persistence on, as it is in production.
os.environ["DEPLOY_SAFETY"] = "on"
os.environ["ABOT_MSGS_PER_MINUTE"] = "0"
os.environ["ABOT_NEW_CONV_COOLDOWN_SEC"] = "0"
os.environ["FLOOD_UPDATES_PER_MINUTE"] = "0"

import sim  # noqa: E402
import db  # noqa: E402
import anon_logic  # noqa: E402
import lifecycle  # noqa: E402
import live_message  # noqa: E402
import shared_features  # noqa: E402
import bot as ab  # noqa: E402

SCHEMA = os.environ["DB_SCHEMA"]
OWNER, OTHER_OWNER, GUEST = 9101, 9102, 9103

failures: list[str] = []
_checks = 0
crashes: list[tuple[str, str]] = []


def _collect(level, kind, message, details=None):
    if level in ("error", "critical"):
        crashes.append((kind, message))


shared_features.set_event_hook(_collect)


def check(label: str, condition: bool, detail: str = "") -> bool:
    global _checks
    _checks += 1
    print(("  ok   " if condition else "  FAIL ") + label
          + ("" if condition or not detail else f"\n       {detail}"))
    if not condition:
        failures.append(label)
    return bool(condition)


def section(title: str) -> None:
    print(f"\n== {title} ==")


def searchable_columns() -> list:
    with db.pooled() as conn:
        return conn.execute(
            "SELECT table_name, column_name FROM information_schema.columns "
            "WHERE table_schema = %s AND data_type IN "
            "('text', 'character varying', 'json', 'jsonb') "
            "ORDER BY table_name, column_name",
            (SCHEMA,),
        ).fetchall()


def where_is(needle: str) -> list:
    """Every column in the schema holding this text. Empty is the answer we
    want, and the reason the columns are asked for rather than listed."""
    hits = []
    with db.pooled() as conn:
        for table, column in searchable_columns():
            found = conn.execute(
                f'SELECT 1 FROM "{SCHEMA}"."{table}" WHERE "{column}"::text LIKE %s LIMIT 1',
                (f"%{needle}%",),
            ).fetchone()
            if found:
                hits.append(f"{table}.{column}")
    return hits


async def flush(driver) -> None:
    """Everything the process is holding, written out -- so that a search of
    the database is a search of what a redeploy would have left behind."""
    persistence = driver.app.persistence
    if persistence is None:
        return
    await driver.app.update_persistence()
    await persistence.flush()


def reset() -> None:
    with db.pooled() as conn:
        for table, _ in searchable_columns():
            conn.execute(f'DELETE FROM "{SCHEMA}"."{table}"')
        conn.commit()


async def fresh():
    reset()
    anon_logic._recent_messages.clear()
    anon_logic._recent_opens.clear()
    shared_features._flood_window.clear()
    shared_features._flood_told.clear()
    ab._albums.clear()
    live_message._last_incoming.clear()
    tele = sim.FakeTelegram(bot_username="anonbot_test")
    driver, _ = await sim.build(ab, tele)
    shared_features.set_event_hook(_collect)

    async def _record_error(update, context):
        crashes.append((type(context.error).__name__, str(context.error)))

    driver.app.add_error_handler(_record_error)
    return driver, tele


async def onboard(person) -> None:
    await person.send("/start")
    await person.tap("English")


async def main() -> int:
    db.init_db()
    lifecycle.init_state_table()
    driver, tele = await fresh()

    # ---- the mechanism has to be live, or every negative below is free ----
    section("persistence is actually on")
    check("the bot built a real persistence layer",
          driver.app.persistence is not None, str(driver.app.persistence))
    check("and the state table exists",
          any(t == "runtime_state" for t, _ in searchable_columns()),
          str(sorted({t for t, _ in searchable_columns()})))

    owner = driver.person(OWNER, "Owner")
    other = driver.person(OTHER_OWNER, "OtherOwner")
    guest = driver.person(GUEST, "Guest")
    await onboard(owner)
    await onboard(other)
    await onboard(guest)
    await flush(driver)

    # A language choice DOES persist, which is what makes "not found" below
    # mean something rather than meaning the writer never ran.
    check("something the bot does keep is in the database",
          bool(where_is('"lang"')) or bool(where_is("lang")),
          "runtime_state holds no user_data at all -- the search proves nothing")

    # ---- the traffic ----
    section("nothing anybody writes is written down")
    token = (await asyncio.to_thread(db.get_or_create_anon_link, OWNER))[0]
    other_token = (await asyncio.to_thread(db.get_or_create_anon_link, OTHER_OWNER))[0]

    cases = []

    async def case(name: str, nonce: str, run) -> None:
        await run()
        await flush(driver)
        hits = where_is(nonce)
        cases.append(name)
        check(f"{name}: nothing of it is in the database", not hits, ", ".join(hits))

    await guest.send(f"/start q_{token}")

    # 1. A delivered message. Relayed and gone: only ids are kept.
    await case("a delivered opening message", "NONCE-DELIVERED-1",
               lambda: guest.send("NONCE-DELIVERED-1"))

    # 2. The case that used to be stored. An unaddressed message, refused.
    await case("a refused unaddressed message", "NONCE-REFUSED-2",
               lambda: guest.send("NONCE-REFUSED-2"))

    # 3. A reply to something the bot does not track.
    await case("a reply to an untracked message", "NONCE-STALE-3",
               lambda: guest.send("NONCE-STALE-3", reply_to=1))

    # 4. Ambiguous: two inboxes open, so no destination can be guessed.
    await guest.send(f"/start q_{other_token}")
    await case("an ambiguous unaddressed message", "NONCE-AMBIGUOUS-4",
               lambda: guest.send("NONCE-AMBIGUOUS-4"))

    # 5. An edit of a message that was already relayed.
    first = [m for m in guest.chat if "NONCE-DELIVERED-1" in (m.get("text") or "")]
    if first:
        await case("an edited message", "NONCE-EDITED-5",
                   lambda: guest.edit(first[0]["message_id"], "NONCE-EDITED-5"))

    # 6. A caption on media, which travels by a different path to text.
    await case("a media caption", "NONCE-CAPTION-6",
               lambda: guest.photo(caption="NONCE-CAPTION-6"))

    # 7. The owner's side of it.
    bubble = [m for m in owner.chat if m.get("reply_markup")]
    if bubble:
        await case("an owner's reply", "NONCE-OWNERREPLY-7",
                   lambda: owner.send("NONCE-OWNERREPLY-7",
                                      reply_to=bubble[-1]["message_id"]))
    await case("an owner's unaddressed message", "NONCE-OWNERBARE-8",
               lambda: owner.send("NONCE-OWNERBARE-8"))

    # 8. A command nobody has, and a message from somebody with no session.
    stranger = driver.person(9104, "Stranger")
    await onboard(stranger)
    await case("a message from somebody with nothing open", "NONCE-NOBODY-9",
               lambda: stranger.send("NONCE-NOBODY-9"))

    section("and neither is anything else")
    check("every case was exercised", len(cases) >= 8, str(cases))
    # One search for all of them at once, in case a later message overwrote
    # what an earlier check saw.
    leftovers = [n for n in ("NONCE-DELIVERED-1", "NONCE-REFUSED-2", "NONCE-STALE-3",
                             "NONCE-AMBIGUOUS-4", "NONCE-EDITED-5", "NONCE-CAPTION-6",
                             "NONCE-OWNERREPLY-7", "NONCE-OWNERBARE-8", "NONCE-NOBODY-9")
                 if where_is(n)]
    check("nothing turns up on a final sweep either", not leftovers, str(leftovers))

    check("no handler raised anything", not crashes,
          "; ".join(f"{k}: {m}" for k, m in crashes))

    with db.pooled() as conn:
        conn.execute(f'DROP SCHEMA "{SCHEMA}" CASCADE')
        conn.commit()
    print(f"\n(dropped {SCHEMA})")

    print(f"\n{_checks} checks, FAILURES: {len(failures)}")
    for name in failures:
        print("  -", name)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
