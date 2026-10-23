"""Every way a person can use AnonBot, driven end to end.

`anon_flow.py` calls AnonBot's handlers directly with hand-built stand-ins,
which is how the routing rules were pinned down. This runs the bot's real
`Application` against `tests/sim.py`'s in-process Telegram: an update goes
in, and what comes out is what somebody's chat would actually contain --
including which bubble each answer is anchored to, which buttons are under
it, and what the *other* person's chat looks like at the same moment.

The scenarios are grouped the way a user would meet them:

    A  first contact          /start, language, /link, unknown commands
    B  one conversation       both reply routes, both directions
    C  several at once        the case this bot exists for
    D  one guest, many inboxes
    E  the reply requirement  what happens when a message names nothing
    F  media, captions, albums
    G  block and unblock
    H  edits and deleted anchors
    I  pause, resume, new link
    J  flood control
    K  /cancel
    L  hostile input
    M  the handlers nothing else reached
    N  a link tapped, then a reply to something else
    O  /conversations, archiving, jumping, /which
    P  which bubble a delivered answer hangs under, for every content type
    Q  problems: always logged, a Report button only where it helps
    R  reactions: passed across as the bot's own, and what cannot be
    S  /help in two lengths, and the one-time note that reactions travel
    T  /feedback, and the reply to a guest it must never take

Run from the repo root:  python tests/anon_scenarios.py
Needs a Postgres (TEST_DATABASE_URL, or the local default). No network.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(ROOT / "tests"))
# AnonBot imports its own modules by bare name, so its folder is the root.
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
# The sim drives one process and never redeploys, so the lease and the state
# table have nothing to do here. test_lifecycle.py owns that ground.
os.environ["DEPLOY_SAFETY"] = "off"
# Flood control is a scenario of its own (group J); everywhere else it must
# not fire, and a suite that sends forty messages in a second would trip it.
os.environ["ABOT_MSGS_PER_MINUTE"] = "0"
os.environ["ABOT_NEW_CONV_COOLDOWN_SEC"] = "0"
# ...and so would the shared update ceiling: a suite that drives a hundred
# updates for one user id inside a second is exactly the shape it exists to
# stop. Group J turns it back on and checks it.
os.environ["FLOOD_UPDATES_PER_MINUTE"] = "0"
# The note about a reaction that could not be passed on takes itself down;
# group R waits for that, and six seconds a time would be a slow suite.
os.environ["ABOT_REACTION_NOTE_SECONDS"] = "0.2"

import sim  # noqa: E402
import db  # noqa: E402
import i18n  # noqa: E402
import anon_logic  # noqa: E402
import live_message  # noqa: E402
import shared_features  # noqa: E402
import bot as ab  # noqa: E402
import family_link  # noqa: E402
import problems  # noqa: E402
import vault  # noqa: E402  -- identity columns hold vault.pseudonym(id), not the id
import sealed_check  # noqa: E402
from telegram.error import BadRequest  # noqa: E402

# Every unhandled exception in a handler ends up here, because that is where
# the bot sends them: shared_features.error_handler calls emit_event, which
# is what ManagerBot's crash alerts are built on. Collecting them turns "did
# anything blow up" into an assertion instead of something to notice in a
# log that has scrolled away -- an AttributeError raised deep in a handler
# is otherwise invisible to a test, since PTB catches it and the user simply
# gets no answer.
crashes: list[tuple[str, str]] = []
def _collect_event(level, kind, message, details=None):
    if level in ("error", "critical"):
        crashes.append((kind, message))


shared_features.set_event_hook(_collect_event)

OWNER, GUEST_A, GUEST_B, GUEST_C, OTHER_OWNER = 5001, 6001, 6002, 6003, 5002

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


def only_report_button(message: dict) -> bool:
    """No button but "Report the issue": a refusal may offer to report
    itself, never to send the message after all."""
    markup = message.get("reply_markup") or {}
    return all((button.get("callback_data") or "").startswith("rpt:")
               for row in markup.get("inline_keyboard", []) for button in row)


def section(title: str) -> None:
    print(f"\n== {title} ==")


def reset_tables() -> None:
    with db.pooled() as conn:
        for table in ("anon_relay", "anon_follower_state", "anon_conversations",
                      "anon_blocks", "anon_links", "user_settings",
                      "donation_prompts", "activity_events"):
            conn.execute(f"DELETE FROM {table}")
        conn.commit()


async def fresh():
    """A brand-new world: empty tables, empty chats, a fresh Application.

    Each group starts from one so a failure in an earlier group cannot cause
    a cascade of unrelated ones, and so message ids start at 1 in every chat
    -- which makes the transcripts readable and the anchors checkable by eye.
    """
    reset_tables()
    anon_logic._recent_messages.clear()
    anon_logic._recent_opens.clear()
    anon_logic._reaction_notes.clear()
    shared_features._flood_window.clear()
    shared_features._flood_told.clear()
    shared_features._feedback_sent.clear()
    ab._albums.clear()
    # A new FakeTelegram restarts message ids at 1, but live_message's
    # watermark is module state and would still hold the previous group's
    # highest id -- so every live message in the new world would look stale
    # and be re-sent instead of edited, which is the opposite of what these
    # scenarios mean to exercise.
    live_message._last_incoming.clear()
    tele = sim.FakeTelegram(bot_username="anonbot_test")
    driver, polling = await sim.build(ab, tele)
    driver.polling_kwargs = polling
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



# ---------------------------------------------------------------------------
# "nothing of what they wrote is kept anywhere"
# ---------------------------------------------------------------------------
# The claim every version of this bot's privacy notice makes, asserted rather
# than trusted. Two halves, because there are two places text could survive:
# the process, and the database. tests/no_storage_scenarios.py asks the same
# question with persistence switched on, which is the production shape.

def _text_anywhere(value, needle: str) -> bool:
    """Is `needle` anywhere inside this nest of dicts, lists and strings?"""
    if isinstance(value, str):
        return needle in value
    if isinstance(value, dict):
        return (any(_text_anywhere(v, needle) for v in value.values())
                or any(_text_anywhere(k, needle) for k in value))
    if isinstance(value, (list, tuple, set)):
        return any(_text_anywhere(v, needle) for v in value)
    return False


def _text_in_database(needle: str) -> bool:
    """Every text-ish column in this bot's whole schema, asked at once.

    Deliberately not a list of tables: a column added later is covered on the
    day it is added rather than the day somebody remembers this test."""
    schema = os.environ["DB_SCHEMA"]
    with db.pooled() as conn:
        columns = conn.execute(
            "SELECT table_name, column_name FROM information_schema.columns "
            "WHERE table_schema = %s AND data_type IN "
            "('text', 'character varying', 'json', 'jsonb')",
            (schema,),
        ).fetchall()
        for table, column in columns:
            found = conn.execute(
                f'SELECT 1 FROM "{schema}"."{table}" '
                f'WHERE "{column}"::text LIKE %s LIMIT 1',
                (f"%{needle}%",),
            ).fetchone()
            if found:
                print(f"       !! found in {table}.{column}")
                return True
    return False


async def driver_flush(driver) -> None:
    """Make the process write out whatever it is holding, so that "not in the
    database" means something. A no-op when the sim runs without persistence,
    which is why the dedicated suite exists."""
    persistence = getattr(driver.app, "persistence", None)
    if persistence is None:
        return
    await driver.app.update_persistence()
    await persistence.flush()


async def onboard(person) -> None:
    """/start plus a language choice -- what every user does once."""
    await person.send("/start")
    await person.tap("English")


async def token_for(owner_id: int) -> str:
    token, _ = db.get_or_create_anon_link(owner_id)
    return token


async def open_link(guest, token: str) -> None:
    await guest.send(f"/start q_{token}")


def owner_bubble(owner, conv_number: int) -> int | None:
    """The message id of the newest incoming bubble for that conversation."""
    for message in reversed(owner.chat):
        text = (message.get("text") or message.get("caption") or "")
        if f"#{conv_number}" in text and message.get("reply_markup"):
            return message["message_id"]
    return None


# ---------------------------------------------------------------------------
# A -- first contact
# ---------------------------------------------------------------------------

async def group_a():
    section("A. first contact")
    s, tele = await fresh()
    owner = s.person(OWNER, "Owner")

    await owner.send("/start")
    check("a brand-new user is asked for a language", "Welcome!" in owner.last()[0])
    check("in all three languages", "Xush kelibsiz" in owner.last()[0] and "Добро" in owner.last()[0])
    check("with three buttons", len(owner.buttons()) == 3, owner.buttons())

    await owner.tap("English")
    instructions = ab.build_help_text("en")
    check("picking one prints the instructions",
          any(instructions in (m.get("text") or "") for m in owner.chat), owner.transcript())

    before = len(owner.chat)
    await owner.send("/start")
    check("a second /start goes straight to the instructions",
          instructions in (owner.last()[0] or "") and "Welcome!" not in owner.last()[0],
          owner.last()[0])
    check("and does not ask again", len(owner.chat) == before + 2)

    await owner.send("/language")
    check("/language is the picker on demand", "Welcome!" in owner.last()[0])
    ticked = [b for b in owner.buttons() if "✅" in b or "✓" in b]
    check("with a tick on the language in force", bool(ticked), owner.buttons())
    await owner.tap("English")

    await owner.send("/link")
    check("/link hands over a t.me link", "t.me/anonbot_test?start=q_" in owner.last()[0])
    check("with Pause and New-link buttons", len(owner.buttons()) == 2, owner.buttons())

    await owner.send("/nosuchcommand")
    check("an unknown command says so", owner.said("don't recognize that command"))

    guest = s.person(GUEST_A, "GuestA")
    await onboard(guest)
    await guest.send("/dbdump")
    check("an owner-only command answers a stranger exactly like a typo",
          guest.said("don't recognize that command"))
    typo = guest.last(1)[0]
    await guest.send("/status")
    check("...and /status is word-for-word the same answer",
          guest.last(1)[0] == typo, guest.last(2))


# ---------------------------------------------------------------------------
# B -- one conversation, both reply routes
# ---------------------------------------------------------------------------

async def group_b():
    section("B. one conversation, both reply routes")
    s, tele = await fresh()
    owner, guest = s.person(OWNER, "Owner"), s.person(GUEST_A, "GuestA")
    await onboard(owner)
    await onboard(guest)
    token = await token_for(OWNER)

    await open_link(guest, token)
    check("tapping the link opens a conversation", guest.said("anonymously"))

    await guest.send("first question")
    check("the guest's opening message needs no reply anchor",
          any("first question" in (m.get("text") or "") for m in owner.chat),
          owner.transcript())
    check("it arrives under a conversation header", owner.said("Conversation #1"))
    check("with Reply and Block under it", len(owner.buttons()) == 2, owner.buttons())
    check("and the guest is told it went", guest.said("Sent"))

    # --- the Reply button explains the gesture and sends nothing ---
    incoming_id = owner.chat[-1]["message_id"]
    before = len(owner.chat)
    await owner.tap("How to reply")
    check("the Reply button puts no message in the chat",
          len(owner.chat) == before, owner.transcript())
    check("it explains the gesture in an alert instead",
          any(a.get("show_alert") and "swipe" in (a.get("text") or "").lower()
              for a in tele.answers),
          str(tele.answers[-2:]))

    # --- the one route: a reply to the message being answered ---
    await owner.send("here is my answer", reply_to=incoming_id)
    check("the answer reaches the guest",
          any("here is my answer" in (m.get("text") or "") for m in guest.chat),
          guest.transcript())
    delivered = [m for m in guest.chat if "here is my answer" in (m.get("text") or "")][0]
    check("as a real Telegram reply to what the guest wrote",
          delivered.get("reply_to_message", {}).get("text") == "first question",
          str(delivered.get("reply_to_message")))
    check("with a Reply button for the guest", bool(delivered.get("reply_markup")))
    check("and the owner is told it went", owner.said("Delivered"))

    # --- the same route from the other side ---
    answer_id = delivered["message_id"]
    await guest.send("thanks, follow-up", reply_to=answer_id)
    check("a guest's swipe-reply resolves without any button",
          any("thanks, follow-up" in (m.get("text") or "") for m in owner.chat),
          owner.transcript())
    landed = [m for m in owner.chat if "thanks, follow-up" in (m.get("text") or "")][0]
    check("and quotes the owner's own message on their side",
          landed.get("reply_to_message", {}).get("text", "").endswith("here is my answer</i>")
          or "here is my answer" in landed.get("reply_to_message", {}).get("text", ""),
          str(landed.get("reply_to_message", {}).get("text")))

    # --- route 3: the owner swipe-replies straight to the bubble ---
    await owner.send("and one more thing", reply_to=landed["message_id"])
    check("the owner can swipe-reply to the incoming bubble directly",
          any("and one more thing" in (m.get("text") or "") for m in guest.chat),
          guest.transcript())

    check("the guest was never shown who they are talking to",
          not any(str(OWNER) in (m.get("text") or "") for m in guest.chat))
    check("the owner was never shown the guest's id",
          not any(str(GUEST_A) in (m.get("text") or "") for m in owner.chat))


# ---------------------------------------------------------------------------
# C -- several conversations at once
# ---------------------------------------------------------------------------

async def group_c():
    section("C. three people at once")
    s, tele = await fresh()
    owner = s.person(OWNER, "Owner")
    a, b, c = (s.person(GUEST_A, "GuestA"), s.person(GUEST_B, "GuestB"),
               s.person(GUEST_C, "GuestC"))
    await onboard(owner)
    for guest in (a, b, c):
        await onboard(guest)
    token = await token_for(OWNER)

    for guest in (a, b, c):
        await open_link(guest, token)
    await a.send("message from A")
    await b.send("message from B")
    await c.send("message from C")

    numbers = sorted({n for n in (1, 2, 3) if owner.said(f"Conversation #{n}")})
    check("each guest arrives as their own numbered conversation",
          numbers == [1, 2, 3], str(numbers))

    # Two threads answered out of order, by replying straight to each guest's
    # own bubble. This is the whole bot in four lines: the reply names the
    # conversation, so the order the owner works in cannot matter.
    bubble_a = owner_bubble(owner, 1)
    bubble_b = owner_bubble(owner, 2)
    check("each conversation has a bubble of its own to reply to",
          bubble_a is not None and bubble_a != bubble_b, f"{bubble_a} {bubble_b}")

    await owner.send("answer for B", reply_to=bubble_b)
    await owner.send("answer for A", reply_to=bubble_a)
    check("the reply to B's bubble went to B",
          any("answer for B" in (m.get("text") or "") for m in b.chat), b.transcript())
    check("the reply to A's bubble went to A",
          any("answer for A" in (m.get("text") or "") for m in a.chat), a.transcript())
    check("nothing leaked into C's chat",
          not any("answer for" in (m.get("text") or "") for m in c.chat), c.transcript())

    # A bare message with three threads open must not be guessed at.
    before_a, before_b, before_c = len(a.chat), len(b.chat), len(c.chat)
    await owner.send("who is this even for")
    check("a bare message from the owner is not delivered on a guess",
          (len(a.chat), len(b.chat), len(c.chat)) == (before_a, before_b, before_c))
    check("and is not answered with a false 'Sent'", "Sent" not in owner.last()[0])
    check("the owner is told to name a conversation", owner.said("reply"))
    check("and is offered no way to send it regardless -- a Report button at most",
          only_report_button(owner.chat[-1]), str(owner.chat[-1].get("reply_markup")))

    # A reply to something that is not tracked at all.
    stray = owner.chat[0]["message_id"]     # the owner's own /start
    await owner.send("replying to nothing", reply_to=stray)
    # Compared against i18n rather than against a phrase copied out of it.
    # These three checks were asserting on wordings that v1.5.0's refusal
    # pass rewrote, so all three had been failing on correct behaviour --
    # which is the failure mode that teaches people to ignore a suite.
    check("a reply to an untracked message is refused, not re-interpreted",
          owner.last()[0] == i18n.t("en", "reply_stale"), owner.last()[0])

    # Three guests, three separate reply chains, all live at once.
    for guest, name in ((a, "A"), (b, "B"), (c, "C")):
        target = [m for m in guest.chat if m.get("reply_markup")
                  and "answer for" in (m.get("text") or "")]
        if not target:
            continue
        await guest.send(f"follow-up from {name}", reply_to=target[-1]["message_id"])
    landed = [m.get("text", "") for m in owner.chat if "follow-up from" in (m.get("text") or "")]
    check("all three follow-ups come back, each under its own number",
          len(landed) == 2, str(landed))   # A and B answered; C never was


# ---------------------------------------------------------------------------
# D -- one guest, several inboxes
# ---------------------------------------------------------------------------

async def group_d():
    section("D. one guest writing to two different inboxes")
    s, tele = await fresh()
    first, second = s.person(OWNER, "Owner1"), s.person(OTHER_OWNER, "Owner2")
    guest = s.person(GUEST_A, "GuestA")
    for person in (first, second, guest):
        await onboard(person)
    token1, token2 = await token_for(OWNER), await token_for(OTHER_OWNER)

    await open_link(guest, token1)
    await guest.send("hello owner one")
    check("the first inbox receives it",
          any("hello owner one" in (m.get("text") or "") for m in first.chat))

    bubble = owner_bubble(first, 1)
    await first.tap("Reply", bubble)
    await first.send("owner one answering", reply_to=first.chat[-1]["message_id"])
    answer1 = [m for m in guest.chat if "owner one answering" in (m.get("text") or "")][0]

    # Now the guest opens somebody else's link, which used to silently end
    # their ability to answer the first.
    await open_link(guest, token2)
    await guest.send("hello owner two")
    check("the second inbox receives its own opening message",
          any("hello owner two" in (m.get("text") or "") for m in second.chat),
          second.transcript())

    await guest.send("still talking to one", reply_to=answer1["message_id"])
    check("a reply into the older conversation still reaches the first owner",
          any("still talking to one" in (m.get("text") or "") for m in first.chat),
          first.transcript())
    check("and does not leak to the second",
          not any("still talking to one" in (m.get("text") or "") for m in second.chat))

    before1, before2 = len(first.chat), len(second.chat)
    await guest.send("bare message, two threads open")
    check("a bare message with two threads open is not delivered on a guess",
          (len(first.chat), len(second.chat)) == (before1, before2))
    check("and the guest is not told 'Sent'", "Sent" not in guest.last()[0])
    check("the guest is asked which one they mean", guest.said("reply"))


# ---------------------------------------------------------------------------
# E -- the reply requirement and the way past it
# ---------------------------------------------------------------------------

async def group_e():
    section("E. the reply requirement")
    s, tele = await fresh()
    owner, guest = s.person(OWNER, "Owner"), s.person(GUEST_A, "GuestA")
    await onboard(owner)
    await onboard(guest)
    token = await token_for(OWNER)
    await open_link(guest, token)
    await guest.send("opening message")

    before = len(owner.chat)
    await guest.send("a second one, replying to nothing")
    check("the second bare message is not delivered", len(owner.chat) == before,
          owner.transcript())
    check("the guest is told why", guest.said("reply"))
    check("with no way to send it regardless -- a Report button at most",
          only_report_button(guest.chat[-1]), str(guest.buttons()))
    # The claim is about the wording, not the identity of the string: a
    # refusal has to say that the message did not go, in those words.
    check("and is told plainly that nothing went",
          "was not sent" in guest.last()[0].lower(), guest.last()[0])

    # The refusal points at the message to reply to, rather than only saying
    # that one is required. Sent AS a reply to it, so it is visible.
    check("the refusal is anchored to the message it is asking about",
          guest.chat[-1].get("reply_to_message") is not None, guest.transcript())

    # Nothing of what they wrote may be left anywhere. This is the whole
    # reason the Send-it-anyway button is gone: it parked the message in
    # user_data, which lifecycle.PostgresPersistence writes to Postgres.
    await driver_flush(s)
    check("the refused message is not held in memory",
          not _text_anywhere(s.app.user_data, "a second one, replying to nothing"),
          str(s.app.user_data))
    check("and not written to the database",
          not _text_in_database("a second one, replying to nothing"))

    # It can still be sent -- by replying, which is the only route.
    anchor = [m for m in guest.chat if m.get("reply_markup") is None
              and "anonymously" in (m.get("text") or "")]
    await guest.send("a second one, replying properly",
                     reply_to=(anchor or guest.chat)[0]["message_id"])
    check("replying to the conversation's own anchor delivers it",
          any("replying properly" in (m.get("text") or "") for m in owner.chat),
          owner.transcript())

    # A guest tapping a Reply button under a message they are not part of.
    stranger = s.person(GUEST_C, "Stranger")
    await onboard(stranger)
    tele.answers.clear()
    await stranger.tap_data("aq_reply:999999")
    check("a Reply button for somebody else's conversation is refused",
          any("conversation" in (a.get("text") or "").lower() for a in tele.answers),
          str(tele.answers))


# ---------------------------------------------------------------------------
# F -- media, captions, albums
# ---------------------------------------------------------------------------

async def group_f():
    section("F. media, captions and albums")
    s, tele = await fresh()
    owner, guest = s.person(OWNER, "Owner"), s.person(GUEST_A, "GuestA")
    await onboard(owner)
    await onboard(guest)
    token = await token_for(OWNER)
    await open_link(guest, token)

    await guest.photo(caption="look at this")
    arrived = [m for m in owner.chat if m.get("photo")]
    check("a captioned photo arrives as one bubble", len(arrived) == 1, owner.transcript())
    check("with the header and the caption together",
          "Conversation #1" in (arrived[0].get("caption") or "")
          and "look at this" in (arrived[0].get("caption") or ""),
          str(arrived[0].get("caption")))
    check("and the buttons on it", bool(arrived[0].get("reply_markup")))

    reply_anchor = arrived[0]["message_id"]
    await owner.send("nice photo", reply_to=reply_anchor)
    check("the owner can swipe-reply to a photo bubble",
          any("nice photo" in (m.get("text") or "") for m in guest.chat), guest.transcript())

    # An uncaptioned photo: header above, media below, buttons on the media.
    answer = [m for m in guest.chat if "nice photo" in (m.get("text") or "")][0]
    before = len(owner.chat)
    await guest.photo(reply_to=answer["message_id"])
    added = owner.chat[before:]
    check("an uncaptioned photo comes with a header line above it",
          len(added) == 2 and added[0].get("text") and added[1].get("photo"),
          str([(m.get("text"), bool(m.get("photo"))) for m in added]))
    check("and the buttons go on the media, not the header",
          bool(added[-1].get("reply_markup")) and not added[0].get("reply_markup"),
          str([bool(m.get("reply_markup")) for m in added]))

    # --- the album ---
    s2, tele2 = await fresh()
    owner2, guest2 = s2.person(OWNER, "Owner"), s2.person(GUEST_A, "GuestA")
    await onboard(owner2)
    await onboard(guest2)
    token2 = await token_for(OWNER)
    await open_link(guest2, token2)

    before = len(owner2.chat)
    await guest2.album(3, caption="three of these")
    delivered = [m for m in owner2.chat[before:] if m.get("photo")]
    check("all three photos of an opening album are delivered",
          len(delivered) == 3, owner2.transcript())
    headers = [m for m in owner2.chat[before:]
               if "Conversation #" in (m.get("text") or m.get("caption") or "")]
    check("the album is announced once, not three times", len(headers) == 1,
          str([(m.get("text") or m.get("caption")) for m in headers]))
    check("and confirmed to the guest once",
          len([t for t in guest2.texts() if "Sent" in (t or "")]) == 1,
          guest2.transcript())
    check("no 'you have to reply' is shown for the later parts of an album",
          not any("reply" in (t or "").lower() and "Sent" not in (t or "")
                  for t in guest2.texts()[-3:]),
          guest2.transcript())

    # An album from the owner, answering one specific conversation. Every
    # part carries the same reply anchor, and the first of them takes the
    # Reply prompt down -- so a router that only ever looks at the anchor
    # loses everything after the first photo.
    bubble = owner_bubble(owner2, 1)
    if bubble:
        await owner2.tap("Reply", bubble)
        prompt = owner2.chat[-1]["message_id"]
        before = len(guest2.chat)
        for n in range(2):
            await owner2.send(f"part {n}", reply_to=prompt if n == 0 else None,
                              media_group_id="ownergrp", kind="photo",
                              caption="my album" if n == 0 else None)
        got = [m for m in guest2.chat[before:] if m.get("photo")]
        check("an album from the owner arrives whole", len(got) == 2, guest2.transcript())
        check("announced once", len([m for m in guest2.chat[before:]
                                     if "\U0001f4ec" in (m.get("caption") or m.get("text") or "")]) == 1,
              guest2.transcript())
        # That header used to read "Conversation #N", and N is the OWNER's
        # index for their own inbox -- so it told a stranger they were
        # somebody's Nth correspondent, which is a fact about the owner
        # delivered to a guest with no way for the owner to stop it.
        check("and without naming the owner's conversation number",
              not any("#" in (m.get("caption") or m.get("text") or "")
                      for m in guest2.chat[before:]),
              guest2.transcript())

    # Overlong text is split rather than refused.
    s3, _ = await fresh()
    owner3, guest3 = s3.person(OWNER, "Owner"), s3.person(GUEST_A, "GuestA")
    await onboard(owner3)
    await onboard(guest3)
    await open_link(guest3, await token_for(OWNER))
    before = len(owner3.chat)
    await guest3.send("x" * 4090)
    pieces = [m for m in owner3.chat[before:] if (m.get("text") or "").count("x") > 10]
    check("a message at Telegram's limit is split, not rejected", len(pieces) >= 2,
          str(len(pieces)))
    check("every piece fits", all(len(m["text"]) <= 4096 for m in pieces),
          str([len(m["text"]) for m in pieces]))
    check("the buttons ride on the last piece only",
          sum(1 for m in pieces if m.get("reply_markup")) == 1,
          str([bool(m.get("reply_markup")) for m in pieces]))


# ---------------------------------------------------------------------------
# G -- block and unblock
# ---------------------------------------------------------------------------

async def group_g():
    section("G. block and unblock")
    s, tele = await fresh()
    owner, guest = s.person(OWNER, "Owner"), s.person(GUEST_A, "GuestA")
    await onboard(owner)
    await onboard(guest)
    token = await token_for(OWNER)
    await open_link(guest, token)
    await guest.send("hello")

    bubble = owner_bubble(owner, 1)
    await owner.tap("Block", bubble)
    check("blocking answers with a toast, not a second message",
          any("block" in (a.get("text") or "").lower() for a in tele.answers),
          str(tele.answers))
    check("the button now offers to undo it",
          any("nblock" in b for b in owner.buttons(bubble)), owner.buttons(bubble))

    answer = [m for m in guest.chat if m.get("reply_markup")]
    before = len(owner.chat)
    await guest.send("are you there", reply_to=answer[-1]["message_id"] if answer else None)
    check("a blocked guest's message does not reach the owner", len(owner.chat) == before,
          owner.transcript())

    await owner.send("/blocked")
    check("/blocked lists the conversation number", owner.said("#1"))
    check("and no pseudonym or user id",
          str(GUEST_A) not in owner.last()[0], owner.last()[0])
    markup = owner.chat[-1].get("reply_markup", {})
    data = [b["callback_data"] for row in markup.get("inline_keyboard", []) for b in row]
    check("the unblock button carries no Telegram user id",
          all(str(GUEST_A) not in d for d in data), str(data))

    await owner.tap("nblock")
    check("unblocking from the list redraws the list", owner.said("aven't blocked")
          or owner.said("nobody"), owner.last()[0])

    await open_link(guest, token)
    # Counting the openings rather than reading a number off one: the guest
    # is not shown a number, because the owner's index for their own inbox is
    # not a label a guest can tell two conversations apart by.
    check("and the guest can open a new conversation again",
          len([m for m in guest.texts() if "anonymously" in (m or "")]) == 2,
          guest.transcript())


# ---------------------------------------------------------------------------
# H -- edits and deleted anchors
# ---------------------------------------------------------------------------

async def group_h():
    section("H. edits, and an anchor that has gone")
    s, tele = await fresh()
    owner, guest = s.person(OWNER, "Owner"), s.person(GUEST_A, "GuestA")
    await onboard(owner)
    await onboard(guest)
    await open_link(guest, await token_for(OWNER))
    first = await guest.send("original wording")

    before = len(owner.chat)
    await guest.edit(first, "edited wording")
    check("an edit is not relayed", len(owner.chat) == before, owner.transcript())
    check("and the guest is told so rather than left to assume",
          "already passed that message on" in (guest.last()[0] or ""), guest.last()[0])
    check("and editing does not crash the bot", not crashes, str(crashes[-1:]))

    # The guest deletes their own message -- the one the owner's next reply
    # is anchored to. Telegram rejects a send whose reply target is gone, so
    # relay_content has to retry without the anchor rather than lose it.
    #
    # Answered by replying to the guest's bubble itself. This used to tap
    # Reply and then answer "the prompt", meaning whatever was last in the
    # chat -- but the Reply button has sent no prompt since v1.4.0, so the
    # reply went to whichever bubble happened to be newest, and the check
    # passed for a reason other than the one its name gives.
    anchor = owner_bubble(owner, 1)
    tele.chats[GUEST_A].remove(tele.find(GUEST_A, first))
    await owner.send("an answer to a deleted question", reply_to=anchor)
    check("a deleted anchor does not cost the message",
          any("an answer to a deleted question" in (m.get("text") or "") for m in guest.chat),
          guest.transcript())
    check("it just arrives unquoted",
          all(m.get("reply_to_message") is None for m in guest.chat
              if "an answer to a deleted" in (m.get("text") or "")))

    # The guest blocks the bot: Telegram answers Forbidden.
    tele.blocked_by.add(GUEST_A)
    # Replied to directly, for the same reason as above. Replying to "the
    # prompt" here meant replying to the bot's own "Sent" line, which has no
    # relay row -- so the owner was told the reply matched nothing, and the
    # loose wording of the old check accepted that as being told about a
    # block. It names the message now.
    bubble = owner_bubble(owner, 1)
    await owner.send("can you hear me", reply_to=bubble)
    check("the owner is told when the guest has blocked the bot",
          owner.last()[0] == i18n.t("en", "reply_forbidden"), owner.last()[0])
    tele.blocked_by.clear()


# ---------------------------------------------------------------------------
# I -- pause, resume, new link
# ---------------------------------------------------------------------------

async def group_i():
    section("I. pause, resume, new link")
    s, tele = await fresh()
    owner, guest = s.person(OWNER, "Owner"), s.person(GUEST_A, "GuestA")
    await onboard(owner)
    await onboard(guest)
    token = await token_for(OWNER)

    await owner.send("/pause")
    await open_link(guest, token)
    check("a paused inbox turns a new arrival away",
          guest.said("isn't accepting"), guest.last()[0])

    await owner.send("/resume")
    await open_link(guest, token)
    check("resuming lets them in", guest.said("anonymously"))
    await guest.send("hi there")

    await owner.send("/link")
    await owner.tap("Pause")
    check("the Pause button edits the same message", owner.said("Paused")
          or owner.said("paused"), owner.last()[0])
    await owner.tap("Resume")

    old_token = token
    await owner.send("/newlink")
    new_token = await token_for(OWNER)
    check("a new link is a different token", new_token != old_token)
    await open_link(guest, old_token)
    check("the old link no longer works",
          guest.said("no longer") or guest.said("invalid") or guest.said("doesn't exist"),
          guest.last()[0])

    # The conversation opened under the old link keeps working.
    bubble = owner_bubble(owner, 1)
    if bubble:
        await owner.send("still reachable", reply_to=bubble)
        check("conversations opened under the old link keep working",
              any("still reachable" in (m.get("text") or "") for m in guest.chat),
              guest.transcript())

    await owner.send("/stats")
    check("/stats counts the conversation", owner.said("1"), owner.last()[0])

    # Somebody tapping their own link.
    await owner.send(f"/start q_{new_token}")
    check("your own link tells you it is yours",
          owner.said("your own") or owner.said("yourself"), owner.last()[0])


# ---------------------------------------------------------------------------
# J -- flood control
# ---------------------------------------------------------------------------

async def group_j():
    section("J. flood control")
    anon_logic.MSGS_PER_MINUTE = 5
    anon_logic.NEW_CONV_COOLDOWN = 30
    try:
        s, tele = await fresh()
        owner, guest = s.person(OWNER, "Owner"), s.person(GUEST_A, "GuestA")
        await onboard(owner)
        await onboard(guest)
        token = await token_for(OWNER)
        await open_link(guest, token)
        first = await guest.send("one")

        # Everything after the opening message has to be a reply, so the
        # burst is sent the way a determined spammer would have to send it:
        # as replies into the conversation. (It used to ride a Send-it-anyway
        # button, long gone; sent unaddressed, every message was refused for
        # not being a reply and the limit was never reached -- and "told to
        # wait" still passed, on the word "waiting" in the old long /help.)
        delivered = 0
        for n in range(8):
            await guest.send(f"spam {n}", reply_to=first)
            if any(f"spam {n}" in (m.get("text") or "") for m in owner.chat):
                delivered += 1
        check("a burst is cut off before it all lands", 0 < delivered < 8, str(delivered))
        told = i18n.t("en", "too_fast", seconds=0).split("{")[0].split(" — ")[0]
        check("and the sender is told to wait rather than ignored",
              any((m.get("text") or "").startswith(told) for m in guest.chat),
              guest.last(2))

        second = s.person(GUEST_B, "GuestB")
        await onboard(second)
        await open_link(second, token)
        await open_link(second, token)
        opens = [t for t in second.texts() if "anonymously" in (t or "")]
        check("tapping the link twice in a row does not open two threads",
              len(opens) == 1, str(opens))
    finally:
        anon_logic.MSGS_PER_MINUTE = 0
        anon_logic.NEW_CONV_COOLDOWN = 0

    # The shared ceiling, which is the one that covers commands and taps --
    # the AnonBot limiter above only sees messages on their way to somebody.
    shared_features.FLOOD_UPDATES_PER_MINUTE = 6
    shared_features.FLOOD_REMIND_SECONDS = 60
    try:
        s, tele = await fresh()
        spammer = s.person(GUEST_C, "Spammer")
        await onboard(spammer)
        before = len(spammer.chat)
        for _ in range(30):
            await spammer.send("/stats")
        answered = len(spammer.chat) - before - 30   # minus their own messages
        check("a command loop is cut off well short of the number sent",
              answered < 12, str(answered))
        check("and is told once, not once per message",
              len([t for t in spammer.texts() if "faster than I can keep up" in (t or "")]) == 1,
              str([t for t in spammer.texts() if "faster" in (t or "")]))

        # The owner runs the bot; being locked out of it during an incident
        # is the wrong failure.
        boss = s.person(OWNER, "Owner")
        await onboard(boss)
        for _ in range(30):
            await boss.send("/link")
        check("the owner is exempt",
              len([t for t in boss.texts() if "t.me/anonbot_test" in (t or "")]) >= 25,
              str(len([t for t in boss.texts() if "t.me/anonbot_test" in (t or "")])))
    finally:
        shared_features.FLOOD_UPDATES_PER_MINUTE = 0


# ---------------------------------------------------------------------------
# K -- /cancel
# ---------------------------------------------------------------------------

async def group_k():
    section("K. /cancel")
    s, tele = await fresh()
    owner, guest = s.person(OWNER, "Owner"), s.person(GUEST_A, "GuestA")
    await onboard(owner)
    await onboard(guest)
    token = await token_for(OWNER)

    await owner.send("/cancel")
    check("/cancel with nothing pending answers at once and without buttons",
          not owner.chat[-1].get("reply_markup"), owner.last()[0])

    await open_link(guest, token)
    await guest.send("hello")
    await guest.send("/cancel")
    check("a guest with an open session is asked before it is ended",
          bool(guest.chat[-1].get("reply_markup")), guest.last()[0])
    await guest.tap("keep")
    before = len(owner.chat)

    await guest.send("/cancel")
    await guest.tap_data([b["callback_data"] for row in
                          guest.chat[-1]["reply_markup"]["inline_keyboard"]
                          for b in row if "cancelpick" in b["callback_data"]
                          and "none" not in b["callback_data"]][0])
    await guest.send("after cancelling")
    check("after cancelling the session a bare message goes nowhere",
          len(owner.chat) == before, owner.transcript())

    # /cancel sent as a reply to a tracked message. There is no Reply prompt
    # to back out of any more -- a reply is a reply, and nothing is pending
    # between tapping something and typing -- so the only thing /cancel can
    # offer the owner here is nothing, and it has to say so rather than
    # inventing an item.
    bubble = owner_bubble(owner, 1)
    await owner.send("/cancel", reply_to=bubble)
    check("/cancel replying to a delivered message has nothing to cancel",
          "nothing to cancel" in owner.last()[0].lower(), owner.last()[0])


# ---------------------------------------------------------------------------
# L -- hostile input
# ---------------------------------------------------------------------------

async def group_l():
    section("L. hostile input")
    s, tele = await fresh()
    owner, guest = s.person(OWNER, "Owner"), s.person(GUEST_A, "GuestA")
    await onboard(owner)
    await onboard(guest)
    await open_link(guest, await token_for(OWNER))

    nasty = "<b>bold</b></i><a href='http://evil'>click</a>"
    await guest.send(nasty)
    landed = [m for m in owner.chat if "bold" in (m.get("text") or "")]
    check("markup a guest types arrives as text, not as markup",
          bool(landed) and "&lt;b&gt;" in landed[0]["text"], str(landed[:1]))
    check("and cannot close the italics it is wrapped in",
          bool(landed) and landed[0]["text"].count("</i>") == 1, str(landed[:1]))

    # A token that does not exist, and one that is not a token at all.
    outsider = s.person(GUEST_C, "Outsider")
    await onboard(outsider)
    await outsider.send("/start q_" + "f" * 32)
    check("an invented token is refused", outsider.said("no longer")
          or outsider.said("invalid") or outsider.said("doesn't exist"), outsider.last()[0])
    await outsider.send("/start q_'; DROP TABLE anon_links;--")
    check("a token shaped like SQL is just a token that does not exist",
          outsider.said("no longer") or outsider.said("invalid")
          or outsider.said("doesn't exist"), outsider.last()[0])
    with db.pooled() as conn:
        alive = conn.execute("SELECT count(*) FROM anon_links").fetchone()[0]
    check("and the table is still there", alive >= 1, str(alive))

    # Callback data the client should never have produced.
    tele.answers.clear()
    await outsider.tap_data("aq_block:1")
    check("a Block button for a conversation you do not own is refused",
          any("conversation" in (a.get("text") or "").lower() for a in tele.answers),
          str(tele.answers))
    tele.answers.clear()
    await outsider.tap_data("aq_unblockg:1")
    check("so is an unblock", any("conversation" in (a.get("text") or "").lower()
                                  for a in tele.answers), str(tele.answers))

    # A conversation id that is not a number at all.
    try:
        await outsider.tap_data("aq_reply:notanumber")
        crashed = False
    except Exception:
        crashed = True
    check("a malformed conversation id does not take the handler down", not crashed)



# ---------------------------------------------------------------------------
# M -- the handlers that were never reached
# ---------------------------------------------------------------------------
# python-telegram-bot runs one handler per group. Everything below shared a
# group with a TypeHandler on Update, which matches every update there is, so
# none of it ever ran -- silently, with no error anywhere. These are the
# user-visible consequences, one check each.

async def group_m():
    section("M. the handlers that were never reached")
    s, tele = await fresh()
    owner, guest = s.person(OWNER, "Owner"), s.person(GUEST_A, "GuestA")
    await onboard(owner)
    await onboard(guest)
    token = await token_for(OWNER)

    # 1. The custom donation amount. The reply to "how many Stars?" used to
    #    fall past donate_custom_amount_received and be treated as an
    #    ordinary message -- in this bot, an anonymous one.
    await guest.send("/donate")
    check("/donate offers a Custom button",
          any("ustom" in b for b in guest.buttons()), guest.buttons())
    await guest.tap("ustom")
    prompt = guest.chat[-1]["message_id"]
    check("and asks for an amount", "donate" in (guest.last()[0] or "").lower()
          or "how many" in (guest.last()[0] or "").lower(), guest.last()[0])
    invoices_before = [c for c in tele.log if c.get("endpoint") == "sendInvoice"]
    await guest.send("250", reply_to=prompt)
    invoices = [c for c in tele.log if c.get("endpoint") == "sendInvoice"]
    check("typing the amount reaches the donation flow",
          len(invoices) > len(invoices_before), guest.transcript())
    check("and is not treated as an anonymous message",
          not any("250" in (m.get("text") or "") for m in owner.chat), owner.transcript())

    # 2. Editing a message. update.message is None on an edit, so every
    #    handler written for a message crashed on it.
    await open_link(guest, token)
    first = await guest.send("as written")
    before = len(owner.chat)
    await guest.edit(first, "as rewritten")
    check("an edit gets an explanation rather than an exception",
          "already passed that message on" in (guest.last()[0] or ""), guest.last()[0])
    check("and nothing new is relayed", len(owner.chat) == before)

    # 3. The order rule that replaced "is there any other thread at all".
    s2, _ = await fresh()
    first_owner, second_owner = s2.person(OWNER, "Owner1"), s2.person(OTHER_OWNER, "Owner2")
    g = s2.person(GUEST_A, "GuestA")
    for person in (first_owner, second_owner, g):
        await onboard(person)
    t1, t2 = await token_for(OWNER), await token_for(OTHER_OWNER)

    await open_link(g, t1)
    await g.send("hello one")
    bubble = owner_bubble(first_owner, 1)
    await first_owner.tap("Reply", bubble)
    await first_owner.send("hello back", reply_to=first_owner.chat[-1]["message_id"])

    await open_link(g, t2)
    await g.send("hello two")
    check("a returning guest's second inbox takes their opening message",
          any("hello two" in (m.get("text") or "") for m in second_owner.chat),
          second_owner.transcript())
    check("and it did not go to the first", 
          not any("hello two" in (m.get("text") or "") for m in first_owner.chat))

    # ...but if the first owner speaks in between, the tap is no longer the
    # newest thing in the chat and the message could be for either of them.
    s3, _ = await fresh()
    o1, o2 = s3.person(OWNER, "Owner1"), s3.person(OTHER_OWNER, "Owner2")
    g3 = s3.person(GUEST_A, "GuestA")
    for person in (o1, o2, g3):
        await onboard(person)
    t1, t2 = await token_for(OWNER), await token_for(OTHER_OWNER)
    await open_link(g3, t1)
    await g3.send("hello one")
    await open_link(g3, t2)
    # Owner one answers now, after the second link was tapped.
    bubble = owner_bubble(o1, 1)
    await o1.tap("Reply", bubble)
    await o1.send("owner one speaking", reply_to=o1.chat[-1]["message_id"])
    before1, before2 = len(o1.chat), len(o2.chat)
    await g3.send("who am I even answering")
    check("a message typed after somebody else's reply landed is held",
          (len(o1.chat), len(o2.chat)) == (before1, before2), g3.transcript())
    check("and the guest is asked which conversation they mean",
          "swipe" in (g3.last()[0] or "").lower(), g3.last()[0])



# ---------------------------------------------------------------------------
# N -- opening a conversation when something else interrupts
# ---------------------------------------------------------------------------
# The case the owner asked for by name: somebody taps an inbox link and then,
# instead of typing, replies to a perfectly valid message from a conversation
# they already had. Two things have to be true afterwards and they pull in
# opposite directions.
#
#   The reply goes where the reply says. An explicit reply is the strongest
#   statement of intent this bot has, and the one failure it must never have
#   is delivering to the wrong stranger. A link tap does not override it.
#
#   The new conversation is still openable. Losing the opening exemption --
#   which is correct, because something else has happened since the tap --
#   must not leave the person with a conversation they cannot start. The
#   opening bubble is a relay row from the moment it is sent, so it is always
#   a valid thing to reply to, and the refusal points straight at it.
#
# Getting only the first right is how a bot ends up with a dead conversation
# and somebody typing into it. Getting only the second right is how a message
# reaches the wrong person.

async def group_n():
    section("N. a link tapped, then a reply to something else")
    s, tele = await fresh()
    owner_a = s.person(OWNER, "OwnerA")
    owner_b = s.person(OTHER_OWNER, "OwnerB")
    guest = s.person(GUEST_A, "Guest")
    await onboard(owner_a)
    await onboard(owner_b)
    await onboard(guest)

    # An established conversation with A, answered, so the guest's chat holds
    # a real message of A's to reply to.
    await open_link(guest, await token_for(OWNER))
    await guest.send("hello A")
    bubble_a = owner_bubble(owner_a, 1)
    await owner_a.send("A here, hello back", reply_to=bubble_a)
    a_message = [m for m in guest.chat if "A here" in (m.get("text") or "")][-1]

    # Now B's link. The opening bubble is the newest thing in the chat.
    token_b = await token_for(OTHER_OWNER)
    await open_link(guest, token_b)
    opening = guest.chat[-1]
    check("tapping B's link opens a conversation and says so",
          "anonymously" in (opening.get("text") or "").lower(), guest.last()[0])

    # ...and instead of writing to B, the guest replies to A.
    await guest.send("still talking to A", reply_to=a_message["message_id"])
    check("the reply went to A, who was replied to",
          any("still talking to A" in (m.get("text") or "") for m in owner_a.chat),
          owner_a.transcript())
    check("and did NOT go to B, whose link was tapped",
          not any("still talking to A" in (m.get("text") or "") for m in owner_b.chat),
          owner_b.transcript())

    # The exemption is gone -- something has happened since the tap -- so a
    # bare message must not be guessed at either.
    before_a, before_b = len(owner_a.chat), len(owner_b.chat)
    await guest.send("who even gets this")
    check("a bare message after the interruption is not delivered anywhere",
          (len(owner_a.chat), len(owner_b.chat)) == (before_a, before_b),
          f"A {owner_a.transcript()}\\nB {owner_b.transcript()}")
    refusal = guest.last()[0]
    check("and is not answered with a false 'Sent'", "Sent" not in refusal, refusal)

    # This is the anti-frustration half. The refusal has to say that B is
    # waiting for its first message, and point at the bubble that opens it.
    check("the refusal says the conversation has not started yet",
          "hasn't started" in refusal.lower() or "has not started" in refusal.lower(),
          refusal)
    # No conversation number: conv_number is the owner's index, so the guest
    # would be shown two conversations both called #1. The refusal points at
    # the bubble instead, which is unambiguous where a number is not.
    check("it does not name a number the guest cannot tell apart",
          "#" not in refusal, refusal)
    check("and is anchored to B's own opening bubble",
          guest.chat[-1].get("reply_to_message", {}).get("message_id") == opening["message_id"],
          str(guest.chat[-1].get("reply_to_message")))

    # And doing what it says works.
    await guest.send("hello B, finally", reply_to=opening["message_id"])
    check("replying to that bubble opens the conversation with B",
          any("hello B, finally" in (m.get("text") or "") for m in owner_b.chat),
          owner_b.transcript())
    check("B sees it as their conversation #1", owner_b.said("Conversation #1"))
    check("and A is not sent it", not owner_a.said("hello B, finally"))

    # Both threads are live at once from here.
    await guest.send("second thing for A", reply_to=a_message["message_id"])
    check("A is still reachable afterwards",
          any("second thing for A" in (m.get("text") or "") for m in owner_a.chat),
          owner_a.transcript())

    # The uninterrupted case still works, because the exemption is about what
    # has happened in the chat rather than about how many links have been
    # tapped. A third inbox, tapped and written to straight away.
    third = s.person(GUEST_B, "ThirdOwner")
    await onboard(third)
    await third.send("/link")
    token_c = await token_for(GUEST_B)
    await open_link(guest, token_c)
    await guest.send("straight in, no reply needed")
    check("tapping a link and typing immediately still just works",
          any("straight in" in (m.get("text") or "") for m in third.chat),
          third.transcript())

    check("nothing crashed through any of it", not crashes,
          "; ".join(f"{k}: {m}" for k, m in crashes))


# ---------------------------------------------------------------------------
# O -- the owner's own list of their own conversations
# ---------------------------------------------------------------------------
# One chat holds every conversation an inbox has, interleaved. This group is
# the three questions that follow from that: how many am I in, where does
# this one start, and which of them am I done with.

async def group_o():
    section("O. /conversations, archiving, jumping and /which")
    s, tele = await fresh()
    owner = s.person(OWNER, "Owner")
    await onboard(owner)

    await owner.send("/conversations")
    check("an inbox nobody has written to says so", owner.said("Nothing open right now"))
    check("and points at the link that would change that", owner.said("/link"))

    token = await token_for(OWNER)
    guests = []
    for n, uid in enumerate((GUEST_A, GUEST_B, GUEST_C)):
        guest = s.person(uid, f"Guest{n}")
        await onboard(guest)
        await open_link(guest, token)
        await guest.send(f"message from guest {n}")
        guests.append(guest)

    await owner.send("/conversations")
    listing = owner.chat[-1]
    check("the list counts what is open", "3 open" in (listing.get("text") or ""),
          listing.get("text"))
    labels = owner.buttons(listing["message_id"])
    check("one button per conversation, most recent first",
          [b.split(" ")[0] for b in labels[:3]] == ["#3", "#2", "#1"], labels)

    # --- one conversation's card ---
    await owner.tap("#2", listing["message_id"])
    card = tele.find(OWNER, listing["message_id"])
    check("tapping one opens its card in the same message",
          "Conversation #2" in (card.get("text") or ""), card.get("text"))
    check("with a jump, an archive and a way back",
          len(owner.buttons(listing["message_id"])) == 3,
          owner.buttons(listing["message_id"]))
    check("and no Block, which a mis-tap could not take back",
          not any("Block" in b for b in owner.buttons(listing["message_id"])),
          owner.buttons(listing["message_id"]))

    # --- going to the start of one ---
    before = len(owner.chat)
    await owner.tap("Go to the start", listing["message_id"])
    added = owner.chat[before:]
    check("Go to the start puts one bookmark in the chat", len(added) == 1,
          owner.transcript())
    bookmark = added[0] if added else {}
    check("naming the conversation it points at",
          "#2" in (bookmark.get("text") or ""), bookmark.get("text"))
    origin = [m for m in owner.chat if "guest 1" in (m.get("text") or "")]
    check("anchored to that conversation's first message",
          bool(origin) and bookmark.get("reply_to_message", {}).get("message_id")
          == origin[0]["message_id"],
          f"bookmark points at {bookmark.get('reply_to_message', {}).get('message_id')}, "
          f"first is {origin[0]['message_id'] if origin else None}")

    # A private chat with a bot has no message links, so the quote is the
    # whole of "go there". What the bookmark adds beyond scrolling is a way
    # into a thread without hunting for a message from it first -- it is a
    # relay row, so replying to it says which conversation, same as any
    # other message in it.
    before_guest = len(guests[1].chat)
    await owner.send("answering through the bookmark", reply_to=bookmark["message_id"])
    check("replying to the bookmark writes into that conversation",
          any("answering through the bookmark" in (m.get("text") or "")
              for m in guests[1].chat[before_guest:]),
          guests[1].transcript())
    check("and into no other",
          not any("answering through the bookmark" in (m.get("text") or "")
                  for guest in (guests[0], guests[2]) for m in guest.chat))

    # --- archiving is filing, and nothing else ---
    await owner.send("/conversations")
    listing = owner.chat[-1]
    await owner.tap("#1", listing["message_id"])
    await owner.tap("Archive", listing["message_id"])
    card = tele.find(OWNER, listing["message_id"])
    check("archiving says so on the card", "Archived" in (card.get("text") or ""),
          card.get("text"))
    check("and offers to undo it",
          any("Unarchive" in b for b in owner.buttons(listing["message_id"])),
          owner.buttons(listing["message_id"]))

    await owner.tap("Back", listing["message_id"])
    back = tele.find(OWNER, listing["message_id"])
    check("the open list is one shorter", "2 open" in (back.get("text") or ""),
          back.get("text"))
    check("and says where the other one went", "1 archived" in (back.get("text") or ""),
          back.get("text"))
    check("the archived one is out of the open list",
          not any(b.startswith("#1 ") for b in owner.buttons(listing["message_id"])),
          owner.buttons(listing["message_id"]))

    await owner.tap("Archived", listing["message_id"])
    check("and is in the archived one",
          any(b.startswith("#1 ") for b in owner.buttons(listing["message_id"])),
          owner.buttons(listing["message_id"]))

    # Asked for by name: an archived conversation is still somewhere you can
    # go back to. Archiving files a thread away, it does not put it out of
    # reach.
    before = len(owner.chat)
    await owner.tap("#1", listing["message_id"])
    await owner.tap("Go to the start", listing["message_id"])
    added = owner.chat[before:]
    check("an archived conversation can still be jumped to",
          len(added) == 1 and "#1" in (added[0].get("text") or ""), owner.transcript())

    # Nothing is refused because a conversation is archived, and the guest is
    # never told it happened -- so a message landing in one brings it back.
    opening = [m for m in guests[0].chat
               if m.get("from", {}).get("is_bot") and "anonymously" in (m.get("text") or "")]
    before_owner = len(owner.chat)
    await guests[0].send("still here", reply_to=opening[0]["message_id"])
    check("a guest can still write into an archived conversation",
          any("still here" in (m.get("text") or "") for m in owner.chat[before_owner:]),
          owner.transcript())
    await owner.send("/conversations")
    check("and that brings it back to the open list",
          "3 open" in (owner.chat[-1].get("text") or ""), owner.chat[-1].get("text"))

    # --- /which ---
    await owner.send("/which")
    check("/which on its own asks to be sent as a reply",
          owner.said("Reply to a message with /which"))

    target = [m for m in owner.chat if "guest 2" in (m.get("text") or "")]
    await owner.send("/which", reply_to=target[0]["message_id"])
    check("/which names the conversation a message is in",
          "conversation #3" in (owner.last()[0] or "").lower(), owner.last(2))

    await owner.send("/which", reply_to=owner.chat[0]["message_id"])
    check("and says so when it has no record of the message",
          owner.last()[0] == i18n.t("en", "which_unknown"), owner.last(2))

    # conv_number is the OWNER's index for their own inbox. Telling a guest
    # "#3" would mean "this owner's third correspondent", which is both
    # meaningless to them and a fact about somebody else -- so a guest is
    # pointed at where the conversation starts instead.
    guest = guests[2]
    opening_c = [m for m in guest.chat
                 if m.get("from", {}).get("is_bot") and "anonymously" in (m.get("text") or "")]
    await guest.send("/which", reply_to=opening_c[0]["message_id"])
    answer = guest.chat[-1]
    check("a guest is told which conversation without being given a number",
          "#" not in (answer.get("text") or ""), answer.get("text"))
    check("and is pointed at where that conversation starts",
          answer.get("reply_to_message") is not None, guest.transcript())

    # --- paging ---
    for uid in range(7001, 7008):
        extra = s.person(uid, f"Extra{uid}")
        await onboard(extra)
        await open_link(extra, token)
        await extra.send("hello")

    await owner.send("/conversations")
    listing = owner.chat[-1]
    check("a long list still counts all of it",
          "10 open" in (listing.get("text") or ""), listing.get("text"))
    rows = [b for b in owner.buttons(listing["message_id"]) if b.startswith("#")]
    check(f"{db.CONVERSATIONS_PER_PAGE} conversations to a page",
          len(rows) == db.CONVERSATIONS_PER_PAGE, rows)
    check("with a page indicator",
          any("/" in b for b in owner.buttons(listing["message_id"])),
          owner.buttons(listing["message_id"]))
    await owner.tap_data("aq_convs:0:1", listing["message_id"])
    second = [b for b in owner.buttons(listing["message_id"]) if b.startswith("#")]
    check("and the rest on the next one", len(second) == 4, second)
    check("which is a different set", set(second).isdisjoint(rows), (rows, second))

    # --- somebody else's conversation, and malformed taps ---
    with db.pooled_read() as conn:
        conv_id = conn.execute(
            "SELECT id FROM anon_conversations WHERE owner_user_id = %s "
            "AND archived_at IS NULL ORDER BY id LIMIT 1", (vault.pseudonym(OWNER),)
        ).fetchone()[0]

    other = s.person(OTHER_OWNER, "OtherOwner")
    await onboard(other)
    await other.send("/conversations")
    await other.tap_data(f"aq_conv:{conv_id}:0:0")
    check("somebody else's conversation card is refused",
          not other.said("Conversation #"), other.transcript())
    await other.tap_data(f"aq_jump:{conv_id}")
    check("and so is jumping into it", not other.said("starts here"), other.transcript())
    await other.tap_data(f"aq_arch:{conv_id}:1:0:0")
    with db.pooled_read() as conn:
        archived_at = conn.execute(
            "SELECT archived_at FROM anon_conversations WHERE id = %s", (conv_id,)
        ).fetchone()[0]
    check("and so is archiving it", archived_at is None, archived_at)

    await other.tap_data("aq_conv:notanumber:0:0")
    await other.tap_data("aq_convs:x:y")
    await other.tap_data("aq_arch:1")
    await other.tap_data(f"aq_jump:{conv_id}:extra")
    check("malformed callback data does not take a handler down", not crashes,
          "; ".join(f"{k}: {m}" for k, m in crashes))

    # Telegram refuses callback_data over 64 bytes, and the sim does not --
    # so a keyboard that works here can still be rejected by the real
    # service. The longest of these is aq_arch, which carries four numbers,
    # and conversation ids are a BIGSERIAL that only grows.
    await owner.send("/conversations")
    listing = owner.chat[-1]
    await owner.tap("#", listing["message_id"])
    datas = [b["callback_data"]
             for message in owner.chat
             for row in (message.get("reply_markup") or {}).get("inline_keyboard", [])
             for b in row if b.get("callback_data")]
    longest = max(datas, key=len)
    check("every callback_data fits Telegram's 64-byte limit",
          len(longest.encode("utf-8")) <= 64, f"{longest} is {len(longest)} bytes")


# ---------------------------------------------------------------------------
# P -- which bubble a delivered answer hangs under
# ---------------------------------------------------------------------------
# A conversation exists twice, once in each chat, and message ids are counted
# per chat -- so the message being answered is a different number on each
# side, and something has to translate. Until v1.6.0 nothing did: the
# delivered copy was anchored to the newest message of that conversation in
# the receiving chat, whoever had written it. Right for as long as people
# answer the last thing said, wrong the moment somebody scrolls up -- which
# is what a threaded inbox is for.
#
# The two chats also drift out of step, which is why the content type
# matters. An uncaptioned sticker is two bubbles on the receiving side -- a
# header line, then the sticker -- and one on the sending side, so the same
# conversation ends up four messages deep in one chat and six in the other.
# Nothing that counts, offsets or assumes symmetry survives that; only a
# pairing recorded at the moment of relaying does.

def delivered(person, needle: str) -> "dict | None":
    """The newest bubble in this person's chat containing `needle`."""
    for message in reversed(person.chat):
        if needle in (message.get("text") or message.get("caption") or ""):
            return message
    return None


def quoted(person, needle: str) -> "int | None":
    """Which message the bubble containing `needle` is shown as a reply to."""
    message = delivered(person, needle) or {}
    return (message.get("reply_to_message") or {}).get("message_id")


def bubble_of_kind(person, kind: str, since: int = 0) -> "dict | None":
    for message in person.chat[since:]:
        if sim.kind_of(message) == kind:
            return message
    return None


def peer_pairing_faults() -> list:
    """Every recorded pairing that does not point at a message of the same
    conversation in the other participant's chat.

    An invariant rather than a scenario, and asked of the whole table at
    once. A pairing that crossed conversations would deliver one guest's
    answer quoting another guest's message, which is the only version of
    this bug that would be a leak rather than a confusion -- so it is worth
    being sure of for every row the group has written, not just the handful
    a scenario thinks to look at. `chat_id` for a private chat is the user's
    own id, which is what lets the two be compared at all."""
    with db.pooled_read() as conn:
        return conn.execute(
            """
            SELECT r.chat_id, r.message_id, r.peer_message_id, r.conversation_id
              FROM anon_relay r
              JOIN anon_conversations c ON c.id = r.conversation_id
              LEFT JOIN anon_relay p
                     ON p.chat_id = CASE WHEN r.chat_id = c.owner_user_id
                                         THEN c.follower_user_id
                                         ELSE c.owner_user_id END
                    AND p.message_id = r.peer_message_id
             WHERE r.peer_message_id IS NOT NULL
               AND (p.conversation_id IS NULL
                    OR p.conversation_id <> r.conversation_id)
            """
        ).fetchall()


async def group_p():
    section("P. a reply lands on the message it answers")

    # --- the report, step for step -------------------------------------
    s, tele = await fresh()
    owner, guest = s.person(OWNER, "Owner"), s.person(GUEST_A, "GuestA")
    await onboard(owner)
    await onboard(guest)
    await open_link(guest, await token_for(OWNER))

    b_text = await guest.send("the opening question")
    a_text = delivered(owner, "the opening question")
    await owner.send("an answer to it", reply_to=a_text["message_id"])
    check("an answer is quoted on the message the guest wrote",
          quoted(guest, "an answer to it") == b_text, guest.transcript())

    g_answer = delivered(guest, "an answer to it")["message_id"]
    before = len(owner.chat)
    b_sticker = await guest.media("sticker", reply_to=g_answer)
    a_sticker = bubble_of_kind(owner, "sticker", before)
    check("an uncaptioned sticker arrives as a sticker, under a header of its own",
          a_sticker is not None and len(owner.chat[before:]) == 2,
          owner.transcript())

    await owner.send("about that sticker", reply_to=a_sticker["message_id"])
    check("answering the newest message quotes it",
          quoted(guest, "about that sticker") == b_sticker, guest.transcript())

    # ...and now something newer arrives, so the sticker is no longer the
    # last thing said. This is the step the bug was in.
    g_about = delivered(guest, "about that sticker")["message_id"]
    await guest.send("changing the subject", reply_to=g_about)
    await owner.send("back to the sticker", reply_to=a_sticker["message_id"])
    landed = quoted(guest, "back to the sticker")
    check("answering an OLDER message still quotes that message",
          landed == b_sticker,
          f"quoted #{landed}, the sticker is #{b_sticker}\n{guest.transcript()}")
    quoted_bubble = tele.find(GUEST_A, landed) or {}
    check("and the bubble it quotes is the sticker, not the header above it",
          sim.kind_of(quoted_bubble) == "sticker", str(sim.kind_of(quoted_bubble)))

    # The same thing from the guest's side. Their anchors are the bot's
    # delivered copies rather than their own messages, which is the other
    # half of the mapping and was wrong in the same way.
    o_answer = delivered(owner, "an answer to it")
    await guest.send("late thought on your first answer", reply_to=g_answer)
    check("a guest answering an older message quotes the owner's own message",
          quoted(owner, "late thought") == o_answer["message_id"], owner.transcript())

    # Replying to something you wrote yourself is allowed on both sides, and
    # resolves through the same pairing -- the copy of it in the other chat.
    o_about = delivered(owner, "about that sticker")
    await owner.send("adding to what I said", reply_to=o_about["message_id"])
    check("replying to your own earlier message quotes its copy on the other side",
          quoted(guest, "adding to what I said") == g_about, guest.transcript())

    faults = peer_pairing_faults()
    check("nothing crossed conversations", not faults, str(faults))

    # --- whatever the conversation opens with --------------------------
    # The first message is the one with nothing above it, so it is the one
    # relayed with no anchor at all -- and its content type decides how many
    # bubbles the owner's chat starts with. Every type, because "it works
    # for a photo" is how this was checked before.
    for kind in sim.MEDIA_FIELDS:
        s, tele = await fresh()
        owner, guest = s.person(OWNER, "Owner"), s.person(GUEST_A, "GuestA")
        await onboard(owner)
        await onboard(guest)
        await open_link(guest, await token_for(OWNER))

        before = len(owner.chat)
        b_first = await guest.media(kind)
        a_first = bubble_of_kind(owner, kind, before)
        if not check(f"a conversation opened with a {kind} arrives as a {kind}",
                     a_first is not None, owner.transcript()):
            continue

        await owner.send("first answer", reply_to=a_first["message_id"])
        check(f"an answer to that {kind} quotes it",
              quoted(guest, "first answer") == b_first, guest.transcript())

        # Something newer, then back to the opening message.
        g_first = delivered(guest, "first answer")["message_id"]
        await guest.send("a follow-up", reply_to=g_first)
        await owner.send("second answer", reply_to=a_first["message_id"])
        landed = quoted(guest, "second answer")
        check(f"and so does one sent after that {kind} stopped being the newest",
              landed == b_first,
              f"quoted #{landed}, the {kind} is #{b_first}\n{guest.transcript()}")

    # --- every type in one conversation, answered out of order ---------
    s, tele = await fresh()
    owner, guest = s.person(OWNER, "Owner"), s.person(GUEST_A, "GuestA")
    await onboard(owner)
    await onboard(guest)
    await open_link(guest, await token_for(OWNER))

    sent: dict = {}     # kind -> (the guest's own id, the owner's copy of it)
    await guest.send("let me show you some things")
    await owner.send("go on", reply_to=delivered(owner, "let me show you")["message_id"])
    anchor = delivered(guest, "go on")["message_id"]
    for kind in sim.MEDIA_FIELDS:
        before = len(owner.chat)
        caption = None if kind in sim.UNCAPTIONABLE else f"this is the {kind}"
        mine = await guest.media(kind, caption=caption, reply_to=anchor)
        theirs = bubble_of_kind(owner, kind, before)
        sent[kind] = (mine, theirs["message_id"] if theirs else None)
        await owner.send(f"noted the {kind} thanks", reply_to=sent[kind][1])
        anchor = delivered(guest, f"noted the {kind} thanks")["message_id"]

    check("every type made it through one conversation",
          all(theirs is not None for _, theirs in sent.values()), str(sent))
    # The two chats now hold different numbers of bubbles for the same
    # conversation -- two more on the owner's side, one for each of the types
    # that cannot carry a caption and so needed a header line of its own.
    # That is the state which makes any positional guess wrong, and the
    # reason the pairing has to be recorded rather than worked out.
    with db.pooled_read() as conn:
        sides = dict(conn.execute(
            "SELECT chat_id, count(*) FROM anon_relay GROUP BY chat_id"
        ).fetchall())
    check("and the conversation is a different number of bubbles in each chat",
          sides.get(vault.pseudonym(OWNER)) != sides.get(vault.pseudonym(GUEST_A)), str(sides))

    wrong = []
    for kind in reversed(sim.MEDIA_FIELDS):
        mine, theirs = sent[kind]
        await owner.send(f"one more thought on the {kind} itself", reply_to=theirs)
        landed = quoted(guest, f"one more thought on the {kind} itself")
        if landed != mine:
            wrong.append(f"{kind}: quoted #{landed}, wanted #{mine}")
    check("answered in reverse order, every answer lands on its own message",
          not wrong, "\n       ".join(wrong) + "\n" + guest.transcript())
    faults = peer_pairing_faults()
    check("still nothing crossed conversations", not faults, str(faults))

    # --- the two ways there is nothing to point at ---------------------
    # A message whose counterpart the other person has deleted. Telegram
    # refuses a send whose reply target is gone, so this has to arrive
    # unquoted rather than not arrive.
    photo_mine, photo_theirs = sent["photo"]
    tele.chats[GUEST_A].remove(tele.find(GUEST_A, photo_mine))
    await owner.send("you deleted this one", reply_to=photo_theirs)
    check("a deleted counterpart costs the quote, not the message",
          delivered(guest, "you deleted this one") is not None, guest.transcript())
    check("and it arrives with no quote at all",
          quoted(guest, "you deleted this one") is None,
          str(quoted(guest, "you deleted this one")))

    # A row written before this column existed. Every live conversation has
    # rows like that on the day the update ships, and they have to keep
    # working: unpaired, so they fall back to the newest anchor, which is
    # what every reply did before.
    fallback = delivered(guest, "you deleted this one")["message_id"]
    voice_mine, voice_theirs = sent["voice"]
    with db.pooled() as conn:
        conn.execute("UPDATE anon_relay SET peer_message_id = NULL "
                     "WHERE chat_id = %s AND message_id = %s",
                     (vault.pseudonym(OWNER), voice_theirs))
        conn.commit()
    await owner.send("answering an unpaired row", reply_to=voice_theirs)
    check("a relay row from before the pairing existed still delivers",
          delivered(guest, "answering an unpaired row") is not None, guest.transcript())
    check("anchored to the newest message, which is what it used to do",
          quoted(guest, "answering an unpaired row") == fallback,
          f"quoted #{quoted(guest, 'answering an unpaired row')}, newest was #{fallback}")


class _ProblemLines(logging.Handler):
    """What the bot wrote to its problem log during a group."""

    def __init__(self):
        super().__init__(logging.INFO)
        self.lines: list[str] = []

    def emit(self, record):
        self.lines.append(record.getMessage())


async def group_q():
    section("Q. problems: always logged, a Report button only where it helps")
    seen = _ProblemLines()
    problem_log = logging.getLogger("problems")
    problem_log.addHandler(seen)
    problem_log.setLevel(logging.INFO)
    try:
        await _problems_and_reports(seen)
    finally:
        problem_log.removeHandler(seen)


async def _problems_and_reports(seen: _ProblemLines):
    s, tele = await fresh()
    owner, guest = s.person(OWNER, "Owner"), s.person(GUEST_A, "GuestA")
    await onboard(owner)
    await onboard(guest)
    token = await token_for(OWNER)
    await open_link(guest, token)
    await guest.send("opening message")
    opening = next(m["message_id"] for m in guest.chat if m.get("text") == "opening message")

    # A simple one: the guest's own slip, and the refusal says what to do.
    await guest.send("a second one, replying to nothing")
    refusal = guest.chat[-1]
    code = problems.find_code(refusal.get("text"))
    check("a simple refusal still ends with its code",
          code is not None and not problems.reportable(code), refusal.get("text"))
    check("but offers no Report button", not refusal.get("reply_markup"), str(refusal.get("reply_markup")))
    logged = [line for line in seen.lines if line.startswith(f"{code} incident ")]
    check("and is logged all the same: code, incident, no button",
          len(logged) == 1 and problems.is_incident(logged[0].split()[2].rstrip(","))
          and logged[0].endswith("no Report button"), str(seen.lines))

    # One the guest cannot fix: Telegram will not deliver it.
    deliver = tele._api_send_message

    def refuse_relay(data):
        if int(data["chat_id"]) == OWNER and "will not arrive" in (data.get("text") or ""):
            raise BadRequest("Bad Request: chat not found")
        return deliver(data)

    before = len(crashes)
    tele._api_send_message = refuse_relay
    try:
        await guest.send("this one will not arrive", reply_to=opening)
    finally:
        tele._api_send_message = deliver
    # Provoked on purpose, and a lost message is urgent: the owner's alert is
    # the bot working, so it is checked here and taken out of the "nothing
    # crashed" tally at the end -- which counted it as a crash from 1.7.0 on.
    provoked = crashes[before:]
    del crashes[before:]
    check("the owner is alerted at once, as for any message that was not delivered",
          provoked and all("AN-DELIVERY" in message for _, message in provoked), str(provoked))
    failure = guest.chat[-1]
    code = problems.find_code(failure.get("text"))
    rows = (failure.get("reply_markup") or {}).get("inline_keyboard", [])
    report_data = rows[-1][0]["callback_data"] if rows else ""
    incident = report_data.split(":")[-1]
    check("a message Telegram would not deliver ends with a code worth reporting",
          code is not None and problems.reportable(code), failure.get("text"))
    check("and offers to report it, with an incident of its own",
          guest.buttons(failure["message_id"]) == [i18n.t("en", "report_button")]
          and problems.is_incident(incident), str(rows))
    check("the incident on the button is the one in the log",
          f"{code} incident {incident}, with a Report button" in seen.lines, str(seen.lines))

    reports_to = shared_features.REPORTS_TO

    # The problem is recorded before anybody decides anything -- the whole
    # point of 1.7.0. It is buffered, so the flush is what a running bot's
    # timer would do twenty seconds later.
    check("the failure says it is already written down, with nobody in it",
          i18n.t("en", "problem_logged_note") in (failure.get("text") or ""), failure.get("text"))
    shared_features._flush_problems_now()
    auto = [r for r in family_link.recent_problem_reports(50) if r["incident"] == incident]
    check("and it is in the database with nobody having tapped anything",
          len(auto) == 1 and auto[0]["code"] == code and auto[0]["shared_at"] is None, str(auto))
    check("holding no user, no username, no chat",
          auto and auto[0]["user_id"] is None and auto[0]["username"] is None
          and auto[0]["chat_kind"] is None, str(auto))

    await guest.tap(i18n.t("en", "report_button"), failure["message_id"])
    disclaimer = guest.chat[-1]
    text = disclaimer.get("text") or ""
    check("tapping it names every field that would be sent, before any is",
          str(code) in text and incident in text and "Telegram user ID" in text
          and "@username" in text and "private or a group" in text
          and not tele.chat_of(reports_to), text)
    check("and says what was recorded without them",
          "Already recorded" in text, text)
    check("and asks first, with Send and Cancel",
          guest.buttons(disclaimer["message_id"]) == [i18n.t("en", "report_send"), i18n.t("en", "report_cancel")],
          str(guest.buttons(disclaimer["message_id"])))
    send_data = disclaimer["reply_markup"]["inline_keyboard"][0][0]["callback_data"]

    # Two taps on the same Report button used to leave two questions in the
    # chat, and answering one left the other still offering to send.
    opened = len(guest.chat)
    await guest.tap_data(report_data, failure["message_id"])
    check("asking twice replaces the question rather than adding one",
          len(guest.chat) == opened, guest.transcript())
    disclaimer = guest.chat[-1]
    send_data = disclaimer["reply_markup"]["inline_keyboard"][0][0]["callback_data"]

    await guest.tap(i18n.t("en", "report_send"), disclaimer["message_id"])
    to_owner = tele.chat_of(reports_to)
    check("Send messages the owner's account", len(to_owner) == 1, str(to_owner))
    dm = to_owner[-1].get("text", "") if to_owner else ""
    problem = problems.PROBLEMS.get(code)
    check("with what the code means and the incident",
          problem is not None and problem.title in dm and incident in dm, dm)
    check("and nothing about who sent it or what they wrote",
          not any(needle in dm for needle in (str(GUEST_A), "GuestA", "will not arrive", "chat not found",
                                              str(OWNER))), dm)
    check("it points at where the details are read, rather than carrying them",
          f"/report {incident}" in dm, dm)
    stored = [r for r in family_link.recent_problem_reports(50) if r["incident"] == incident]
    check("the details go onto the row that was already there",
          len(stored) == 1 and stored[0]["user_id"] == GUEST_A
          and stored[0]["shared_at"] is not None, str(stored))
    check("and the person is told what happened to them",
          guest.said(i18n.t("en", "report_sent", incident=incident)), guest.transcript())

    await guest.tap_data(send_data, disclaimer["message_id"])
    stored = [r for r in family_link.recent_problem_reports(50) if r["incident"] == incident]
    check("a second Send stores nothing and messages nobody",
          len(stored) == 1 and len(tele.chat_of(reports_to)) == 1, str(stored))
    check("and says it was already sent",
          guest.said(i18n.t("en", "report_already", incident=incident)), guest.transcript())

    await guest.tap_data(report_data, failure["message_id"])
    await guest.tap(i18n.t("en", "report_cancel"), guest.chat[-1]["message_id"])
    check("Cancel sends nothing", len(tele.chat_of(reports_to)) == 1, str(tele.chat_of(reports_to)))
    check("and says so", guest.said(i18n.t("en", "report_cancelled")), guest.transcript())

    answered = len(tele.answers)
    await guest.tap_data("rpts:ZZ-NOPE:ABCDEF:0")
    check("a forged button with a code nobody defined is refused, and sends nothing",
          len(tele.answers) > answered and "no longer works" in (tele.answers[-1].get("text") or "")
          and len(tele.chat_of(reports_to)) == 1, str(tele.answers[-1:]))

    before = len(seen.lines)
    redrawn = "Still not delivered." + problems.code_line("AN-DELIVERY")
    first = await shared_features.note_problem(GUEST_A, 9001, redrawn, {})
    again = await shared_features.note_problem(GUEST_A, 9001, redrawn, {})
    check("a message redrawn with the same problem keeps its incident and is logged once",
          first == again and len(seen.lines) == before + 1, str(seen.lines[before:]))
    await s.app.bot.answer_callback_query("1", text="Gone." + problems.code_line("AN-CONV-GONE"))
    check("a pop-up with a code is logged too, though it can hold no button",
          bool(seen.lines) and seen.lines[-1].startswith("AN-CONV-GONE incident ")
          and seen.lines[-1].endswith("a pop-up"), str(seen.lines[-1:]))

    with db.pooled() as conn:
        conn.execute("DELETE FROM family.problem_reports WHERE incident = %s", (incident,))
        conn.commit()


# ---------------------------------------------------------------------------
# R -- reactions
# ---------------------------------------------------------------------------

async def _conversation_with_reply(s):
    """An owner and a guest one question and one answer in: the four bubbles
    every reaction scenario needs, by id in the chat each is in."""
    owner, guest = s.person(OWNER, "Owner"), s.person(GUEST_A, "GuestA")
    await onboard(owner)
    await onboard(guest)
    await open_link(guest, await token_for(OWNER))
    asked = await guest.send("is this really anonymous?")
    incoming = owner_bubble(owner, 1)
    answered = await owner.send("it really is", reply_to=incoming)
    delivered = [m for m in guest.chat if "it really is" in (m.get("text") or "")][-1]["message_id"]
    return owner, guest, {"asked": asked, "incoming": incoming,
                          "answered": answered, "delivered": delivered}


async def group_r():
    section("R. reactions travel, as the bot's own")
    s, tele = await fresh()
    owner, guest, ids = await _conversation_with_reply(s)

    check("the bot asks Telegram for reactions by name -- none arrive otherwise",
          "message_reaction" in s.polling_kwargs.get("allowed_updates", []),
          str(s.polling_kwargs.get("allowed_updates")))

    before = len(guest.chat)
    await owner.react(ids["incoming"], "❤")
    check("the owner's reaction lands on the guest's own message",
          guest.bot_reaction(ids["asked"]) == ["❤"], guest.bot_reaction(ids["asked"]))
    check("as a reaction, not a message", len(guest.chat) == before, guest.transcript())
    await owner.react(ids["incoming"], "🔥")
    check("changing it changes theirs", guest.bot_reaction(ids["asked"]) == ["🔥"],
          guest.bot_reaction(ids["asked"]))
    await owner.react(ids["incoming"])
    check("taking it off takes theirs off", guest.bot_reaction(ids["asked"]) == [],
          guest.bot_reaction(ids["asked"]))

    await guest.react(ids["delivered"], "👍")
    check("the other way: the guest's reaction lands on the owner's own message",
          owner.bot_reaction(ids["answered"]) == ["👍"], owner.bot_reaction(ids["answered"]))
    await guest.react(ids["asked"], "😁")
    check("a reaction on your own message reaches the copy the other side has",
          owner.bot_reaction(ids["incoming"]) == ["😁"], owner.bot_reaction(ids["incoming"]))
    check("nobody was named by any of it",
          not any(str(GUEST_A) in (m.get("text") or "") for m in owner.chat)
          and not any(str(OWNER) in (m.get("text") or "") for m in guest.chat))

    # --- what cannot travel, and the note that says so ---
    await guest.react(ids["asked"])          # clear the slate on both sides
    anon_logic._reaction_notes.clear()
    before = len(owner.chat)
    await owner.react(ids["incoming"], custom="5368324170671202286")
    check("a Premium (custom emoji) reaction is not passed on",
          guest.bot_reaction(ids["asked"]) == [], guest.bot_reaction(ids["asked"]))
    note = owner.chat[-1] if len(owner.chat) > before else {}
    check("the person who put it is told, briefly",
          (note.get("text") or "") == i18n.t("en", "reaction_not_passed"), str(note.get("text")))
    check("with the problem's code on its last line",
          problems.find_code(note.get("text") or "") == "AN-REACTION-NOT-PASSED", str(note.get("text")))
    check("and without a sound",
          any(e.get("endpoint") == "sendMessage" and e.get("message_id") == note.get("message_id")
              and e.get("silent") for e in tele.log))
    await asyncio.sleep(anon_logic.REACTION_NOTE_SECONDS + 0.3)
    check("then the note takes itself down",
          (OWNER, note.get("message_id")) in tele.deleted
          and tele.find(OWNER, note.get("message_id")) is None, str(tele.deleted[-3:]))

    before = len(owner.chat)
    await owner.react(ids["incoming"], custom="5368324170671202287")
    check("a second one straight after is not a second note",
          len(owner.chat) == before, owner.transcript())

    anon_logic._reaction_notes.clear()
    await owner.react(ids["incoming"], "👍", "🎉")
    check("of two reactions at once, one travels -- the newest",
          guest.bot_reaction(ids["asked"]) == ["🎉"], guest.bot_reaction(ids["asked"]))
    check("and the person is told only one goes through",
          owner.chat[-1].get("text") == i18n.t("en", "reaction_one_only"), owner.chat[-1].get("text"))
    await owner.react(ids["incoming"], "👍")
    check("taking one of two off leaves the other on both sides",
          guest.bot_reaction(ids["asked"]) == ["👍"], guest.bot_reaction(ids["asked"]))

    anon_logic._reaction_notes.clear()
    await owner.react(ids["incoming"], "🫠")
    check("an emoji Telegram does not let a bot use is refused by Telegram, and said so",
          guest.bot_reaction(ids["asked"]) == ["👍"]
          and owner.chat[-1].get("text") == i18n.t("en", "reaction_not_passed"),
          f"{guest.bot_reaction(ids['asked'])} / {owner.chat[-1].get('text')}")
    await asyncio.sleep(anon_logic.REACTION_NOTE_SECONDS + 0.3)

    # --- where there is nowhere to send it ---
    sent_notice = next(m["message_id"] for m in guest.chat if (m.get("text") or "").startswith("Sent"))
    owner_before, guest_before = len(owner.chat), len(guest.chat)
    calls = len([e for e in tele.log if e.get("endpoint") == "setMessageReaction"])
    await guest.react(sent_notice, "👍")
    check("a reaction on the bot's own notice goes nowhere and says nothing",
          len(owner.chat) == owner_before and len(guest.chat) == guest_before
          and len([e for e in tele.log if e.get("endpoint") == "setMessageReaction"]) == calls)

    db.set_anon_blocked(OWNER, GUEST_A, True)
    await guest.react(ids["delivered"], "💔")
    check("a blocked guest's reaction does not reach the owner",
          owner.bot_reaction(ids["answered"]) != ["💔"], owner.bot_reaction(ids["answered"]))
    await owner.react(ids["incoming"], "🙏")
    check("nor does the owner's reach a guest they blocked",
          guest.bot_reaction(ids["asked"]) != ["🙏"], guest.bot_reaction(ids["asked"]))
    db.set_anon_blocked(OWNER, GUEST_A, False)

    tele.blocked_by.add(GUEST_A)
    await owner.react(ids["incoming"], "🤝")
    check("a guest who blocked the bot costs nothing but the reaction",
          not crashes, str(crashes[-2:]))
    tele.blocked_by.discard(GUEST_A)

    check("and not one reaction was written down anywhere",
          not any(_text_in_database(e) for e in ("❤", "🔥", "🎉", "🤝")))


# ---------------------------------------------------------------------------
# S -- /help in two lengths, and the note that reactions are new
# ---------------------------------------------------------------------------

def _knows(user_id: int):
    with db.pooled() as conn:
        row = conn.execute("SELECT knows_reactions FROM user_settings WHERE user_id = %s",
                           (vault.pseudonym(user_id),)).fetchone()
    return row[0] if row else "no row"


def _as_if_here_before(s, user_id: int) -> None:
    """A user from before 2.1.0: the column NULL, as the migration leaves it,
    and the process's cache of the answer gone, as a restart leaves it."""
    with db.pooled() as conn:
        conn.execute("UPDATE user_settings SET knows_reactions = NULL WHERE user_id = %s",
                     (vault.pseudonym(user_id),))
        conn.commit()
    s.app.user_data[user_id].pop(ab.KNOWS_REACTIONS_KEY, None)


async def group_s():
    section("S. /help in two lengths, and the one-time note about reactions")
    s, tele = await fresh()
    owner = s.person(OWNER, "Owner")
    await onboard(owner)

    await owner.send("/help")
    help_id = owner.chat[-1]["message_id"]
    brief = owner.chat[-1].get("text") or ""
    check("/help is the short version", brief == ab.build_help_text("en"), brief[:120])
    check("a fraction of the full one",
          len(brief) * 2 < len(ab.build_help_text("en", full=True)),
          f"{len(brief)} vs {len(ab.build_help_text('en', full=True))}")
    check("which still says reactions travel, and that Premium ones do not",
          "react" in brief.lower() and "Premium" in brief)
    pages = ["/" + name for name in ab.HELP_PAGES]
    check("with a button per help page, then one to the full guide",
          owner.buttons(help_id) == pages + [i18n.t("en", "help_full_button")], owner.buttons(help_id))
    await owner.tap("Full guide", help_id)
    full = (tele.find(OWNER, help_id) or {}).get("text") or ""
    check("the button turns it into the full guide, in place",
          full == ab.build_help_text("en", full=True), full[:120])
    check("listing every reaction a bot may pass on",
          all(e in full for e in anon_logic.RELAYABLE_REACTIONS),
          [e for e in anon_logic.RELAYABLE_REACTIONS if e not in full])
    check("with the Premium warning in capitals, where it stands out",
          "⚠️ NOTE: Telegram Premium reactions" in full and "can NOT be passed on" in full)
    check("and a way back, under the same page buttons",
          owner.buttons(help_id) == pages + [i18n.t("en", "help_brief_button")], owner.buttons(help_id))
    await owner.tap("Short version", help_id)
    check("which turns it back", (tele.find(OWNER, help_id) or {}).get("text") == brief)
    await owner.send("/help full")
    check("/help full goes straight to the full guide",
          owner.chat[-1].get("text") == ab.build_help_text("en", full=True))
    for lang in ("uz", "ru"):
        long_one, short_one = ab.build_help_text(lang, full=True), ab.build_help_text(lang)
        check(f"[{lang}] the full guide lists every reaction and fits one message",
              all(e in long_one for e in anon_logic.RELAYABLE_REACTIONS)
              and len(long_one.encode("utf-16-le")) // 2 <= 4096)
        check(f"[{lang}] and both versions name Telegram Premium",
              "Premium" in long_one and "Premium" in short_one)

    # --- the one-time note ---
    guest = s.person(GUEST_A, "GuestA")
    await onboard(guest)
    await guest.send("/link")
    check("somebody new is not told reactions are new",
          not guest.said("You can now react"), guest.transcript())
    check("because they start out knowing", _knows(GUEST_A) is True, _knows(GUEST_A))

    _as_if_here_before(s, GUEST_A)
    await guest.send("/link")
    notes = [m for m in guest.chat if (m.get("text") or "") == i18n.t("en", "reactions_update_note")]
    check("somebody who was here before is told, on their next visit",
          len(notes) == 1, guest.transcript())
    check("after what they came for, not instead of it",
          guest.chat[-1].get("text") == i18n.t("en", "reactions_update_note")
          and "t.me/anonbot_test" in (guest.chat[-2].get("text") or ""), guest.transcript())
    check("and it is ticked off in the database", _knows(GUEST_A) is True, _knows(GUEST_A))
    s.app.user_data[GUEST_A].pop(ab.KNOWS_REACTIONS_KEY, None)
    await guest.send("/link")
    notes = [m for m in guest.chat if (m.get("text") or "") == i18n.t("en", "reactions_update_note")]
    check("once -- not again, even after a restart forgets the cache",
          len(notes) == 1, guest.transcript())

    guest_b = s.person(GUEST_B, "GuestB")
    await onboard(guest_b)
    _as_if_here_before(s, GUEST_B)
    await guest_b.send("/help")
    check("reading /help counts as being told: no note after it",
          not guest_b.said("You can now react") and _knows(GUEST_B) is True, guest_b.transcript())

    guest_c = s.person(GUEST_C, "GuestC")
    await onboard(guest_c)
    _as_if_here_before(s, GUEST_C)
    await guest_c.send("/feedback")
    check("a question waiting for its answer is not interrupted by the note",
          not guest_c.said("You can now react"), guest_c.transcript())
    await guest_c.send("love it", reply_to=guest_c.chat[-1]["message_id"])
    await guest_c.send("/link")
    check("which comes at the next visit instead",
          guest_c.chat[-1].get("text") == i18n.t("en", "reactions_update_note"), guest_c.transcript())


# ---------------------------------------------------------------------------
# T -- /feedback
# ---------------------------------------------------------------------------

def _feedback_rows():
    with db.pooled() as conn:
        rows = conn.execute("SELECT bot_id, lang, body FROM family.feedback ORDER BY id").fetchall()
    return [tuple(r) for r in rows]


async def group_t():
    section("T. /feedback, and the reply to a guest it must never take")
    s, tele = await fresh()
    with db.pooled() as conn:
        conn.execute("DELETE FROM family.feedback")
        conn.commit()
    owner, guest = s.person(OWNER, "Owner"), s.person(GUEST_A, "GuestA")
    await onboard(owner)
    await onboard(guest)
    await open_link(guest, await token_for(OWNER))
    await guest.send("hello there")
    incoming = owner_bubble(owner, 1)

    await owner.send("/feedback make the link shorter")
    check("/feedback with words sends them at once",
          owner.chat[-1].get("text") == i18n.t("en", "feedback_thanks"), owner.transcript())
    check("kept as the words, the bot and the language",
          _feedback_rows() == [("anonbot", "en", "make the link shorter")], _feedback_rows())

    await owner.send("/feedback")
    prompt = owner.chat[-1]["message_id"]
    check("/feedback alone asks for it", owner.chat[-1].get("text") == i18n.t("en", "feedback_ask"))
    await owner.send("dark mode, please", reply_to=prompt)
    check("the answer to the question is taken",
          _feedback_rows()[-1] == ("anonbot", "en", "dark mode, please"), _feedback_rows())
    check("and never relayed into a conversation", not guest.said("dark mode"), guest.transcript())

    await owner.send("/feedback")
    prompt = owner.chat[-1]["message_id"]
    await owner.send("my answer to you", reply_to=incoming)
    check("a reply to a guest while /feedback waits goes to the guest",
          guest.said("my answer to you"), guest.transcript())
    check("and not to the developer", all(r[2] != "my answer to you" for r in _feedback_rows()),
          _feedback_rows())
    await owner.send("found a typo in /help", reply_to=prompt)
    check("the question is still open, and answering it still works",
          _feedback_rows()[-1][2] == "found a typo in /help", _feedback_rows())

    await owner.send("/feedback")
    prompt = owner.chat[-1]["message_id"]
    count = len(_feedback_rows())
    await owner.media("photo", reply_to=prompt)
    check("a photo sent as the answer is asked about again, in words",
          owner.chat[-1].get("text") == i18n.t("en", "feedback_text_only")
          and len(_feedback_rows()) == count, owner.transcript())
    check("and is not relayed either", not any(sim.kind_of(m) == "photo" for m in guest.chat),
          guest.transcript())
    await owner.send("/cancel")
    check("/cancel offers to stop the feedback it is waiting for",
          i18n.t("en", "cancel_button_feedback") in owner.buttons(), owner.buttons())
    await owner.tap(i18n.t("en", "cancel_button_feedback"))
    check("and stops it", i18n.t("en", "cancel_item_feedback") in (owner.chat[-1].get("text") or ""),
          owner.chat[-1].get("text"))

    await guest.send("/feedback the guest side works too")
    check("anybody can send it, guests included",
          _feedback_rows()[-1] == ("anonbot", "en", "the guest side works too"), _feedback_rows())

    shared_features.FEEDBACK_PER_DAY = 2
    try:
        shared_features._feedback_sent.clear()
        await guest.send("/feedback one")
        await guest.send("/feedback two")
        await guest.send("/feedback three")
        last = guest.chat[-1]
        check("past the day's allowance it is refused, and says how many",
              last.get("text") == i18n.t("en", "feedback_limit", per_day=2), last.get("text"))
        check("as a limit, with no Report button",
              problems.find_code(last.get("text")) == "FM-FEEDBACK-LIMIT" and only_report_button(last)
              and not last.get("reply_markup"), str(last.get("reply_markup")))
        check("and the third is not kept", all(r[2] != "three" for r in _feedback_rows()))
    finally:
        shared_features.FEEDBACK_PER_DAY = 5
        shared_features._feedback_sent.clear()

    real = shared_features.family_link.save_feedback
    def _away(*args, **kwargs):
        raise RuntimeError("the database is away")
    shared_features.family_link.save_feedback = _away
    try:
        await owner.send("/feedback this one will not make it")
    finally:
        shared_features.family_link.save_feedback = real
    last = owner.chat[-1]
    check("a database that is away is said plainly",
          problems.find_code(last.get("text")) == "FM-FEEDBACK-FAILED"
          and (last.get("text") or "").startswith(i18n.t("en", "feedback_failed").split("\n")[0]),
          last.get("text"))
    check("with a Report button, because it is not theirs to fix",
          any((b.get("callback_data") or "").startswith("rpt:")
              for row in (last.get("reply_markup") or {}).get("inline_keyboard", []) for b in row))

    with db.pooled() as conn:
        columns = {r[0] for r in conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'family' AND table_name = 'feedback'").fetchall()}
    check("the table has nowhere to put who wrote it",
          columns == {"id", "bot_id", "lang", "body", "created_at", "delivered_at"}, sorted(columns))
    with db.pooled() as conn:
        conn.execute("DELETE FROM family.feedback")
        conn.commit()



# ---------------------------------------------------------------------------
# U -- /export is gone, and so is what only /export needed
# ---------------------------------------------------------------------------

def _row_counts() -> dict:
    """Rows in every table of the bot's own schema and of the family schema."""
    counts = {}
    with db.pooled_read() as conn:
        tables = conn.execute(
            "SELECT table_schema, table_name FROM information_schema.tables "
            "WHERE table_type = 'BASE TABLE' AND table_schema IN (current_schema(), 'family')"
        ).fetchall()
        for schema, table in tables:
            counts[f"{schema}.{table}"] = conn.execute(
                f'SELECT count(*) FROM "{schema}"."{table}"').fetchone()[0]
    return counts


async def group_u():
    section("U. /export only says it was removed; relay rows keep no times and sides")
    s, tele = await fresh()
    owner, guest = s.person(OWNER, "Owner"), s.person(GUEST_A, "GuestA")
    guest_b = s.person(GUEST_B, "GuestB")
    await onboard(owner)
    await onboard(guest)
    await onboard(guest_b)
    token = await token_for(OWNER)
    await open_link(guest, token)
    await guest.send("first from A")
    await open_link(guest_b, token)
    await guest_b.send("first from B")
    await driver_flush(s)

    # --- /export answers one line, in the person's language, and nothing else
    check("the command is not in the menu", "export" not in [c.command for c in ab.BOT_COMMANDS],
          str([c.command for c in ab.BOT_COMMANDS]))
    for code, switch in (("en", "/en"), ("uz", "/uz"), ("ru", "/rus")):
        await owner.send(switch)
        await driver_flush(s)
        before_rows, before_chat = _row_counts(), len(owner.chat)
        await owner.send("/export")
        await driver_flush(s)
        added = [m for m in owner.chat[before_chat:] if m.get("text") != "/export"]
        check(f"{code}: /export answers with the removal notice and nothing else",
              len(added) == 1 and added[0].get("text") == i18n.t(code, "export_removed"),
              owner.transcript())
        check(f"{code}: the notice has no buttons", not added[0].get("reply_markup") if added else False)
        check(f"{code}: and it writes no row anywhere", _row_counts() == before_rows,
              str({k: (before_rows.get(k), v) for k, v in _row_counts().items()
                   if before_rows.get(k) != v}))
    await owner.send("/en")
    check("the three notices are the agreed texts",
          i18n.t("en", "export_removed").startswith("📦 /export has been removed")
          and i18n.t("uz", "export_removed").startswith("📦 /export olib tashlandi")
          and i18n.t("ru", "export_removed").startswith("📦 Команда /export удалена"))
    check("and no other export_ text is left in any language",
          all(not k.startswith("export_") or k == "export_removed"
              for lang in i18n.SUPPORTED_LANGUAGES for k in i18n.STRINGS[lang]),
          str([k for k in i18n.STRINGS["en"] if k.startswith("export_")]))

    # --- relay rows carry no time and no side; replies still route
    with db.pooled() as conn:
        conn.execute("ALTER TABLE anon_relay ADD COLUMN IF NOT EXISTS sent_at TIMESTAMPTZ")
        conn.execute("ALTER TABLE anon_relay ADD COLUMN IF NOT EXISTS from_bot BOOLEAN")
        conn.commit()
    await owner.send("answer for B", reply_to=owner_bubble(owner, 2))
    await guest_b.send("and a follow-up from B", reply_to=delivered(guest_b, "answer for B")["message_id"])
    with db.pooled_read() as conn:
        total, with_time, with_side = conn.execute(
            "SELECT count(*), count(sent_at), count(from_bot) FROM anon_relay").fetchone()
    check("relay rows exist for both sides of both conversations", total >= 8, str(total))
    check("none of them has sent_at or from_bot set", (with_time, with_side) == (0, 0),
          f"{with_time} with sent_at, {with_side} with from_bot")
    check("the answer reached guest B and only guest B",
          delivered(guest_b, "answer for B") is not None and delivered(guest, "answer for B") is None,
          guest_b.transcript() + guest.transcript())
    followed = delivered(owner, "and a follow-up from B") or {}
    check("and B's follow-up landed in conversation #2, not #1",
          "#2" in (followed.get("text") or "") and "#1" not in (followed.get("text") or "")
          and not any("follow-up from B" in (m.get("text") or "") for m in guest.chat),
          owner.transcript())

    # --- 30 days of silence closes a conversation and clears its relay rows
    check("the default retention is 30 days", db.RELAY_RETENTION_DAYS == 30,
          str(db.RELAY_RETENTION_DAYS))
    from datetime import datetime as _dt, timedelta as _td, timezone as _tz
    def _idle(conversation: int, days: int) -> None:
        stamp = (_dt.now(_tz.utc) - _td(days=days)).isoformat()
        with db.pooled() as conn:
            conn.execute("UPDATE anon_conversations SET last_activity_at = %s WHERE conv_number = %s",
                         (stamp, conversation))
            conn.commit()
    def _relay_rows(conversation: int) -> int:
        with db.pooled_read() as conn:
            return conn.execute(
                "SELECT count(*) FROM anon_relay r JOIN anon_conversations c ON c.id = r.conversation_id "
                "WHERE c.conv_number = %s", (conversation,)).fetchone()[0]
    check("both conversations have relay rows before the prune",
          _relay_rows(1) > 0 and _relay_rows(2) > 0, f"{_relay_rows(1)} / {_relay_rows(2)}")
    _idle(1, 31)
    _idle(2, 29)
    db.prune_old_data()
    check("idle 31 days: its relay rows are gone", _relay_rows(1) == 0, str(_relay_rows(1)))
    check("idle 29 days: its relay rows stay", _relay_rows(2) > 0, str(_relay_rows(2)))

# ---------------------------------------------------------------------------

GROUPS = [group_a, group_b, group_c, group_d, group_e,
          group_f, group_g, group_h, group_i, group_j, group_k, group_l,
          group_m, group_n, group_o, group_p, group_q, group_r, group_s, group_t, group_u]


async def main() -> int:
    db.init_db()
    only = sys.argv[1:]
    scanned = 0
    for group in GROUPS:
        if only and group.__name__[-1] not in only:
            continue
        await group()
        # 2.1.1: whatever the group just did, no column that names a person or
        # a chat holds a real Telegram id. Asked of the database itself, for
        # every such column it has, so a table added later is covered too.
        with db.pooled_read() as conn:
            leaks, columns, rows = sealed_check.real_ids_left(conn, os.environ["DB_SCHEMA"])
        scanned += rows
        check(f"{group.__name__}: no identity column holds a real Telegram id ({columns} columns)",
              not leaks and columns >= 10, "; ".join(leaks) or f"only {columns} columns found")
    check("and those scans looked at real data, not empty tables", scanned > 0, str(scanned))
    section("nothing crashed")
    check("no handler raised anything the error handler had to report",
          not crashes, " | ".join(f"{k}: {m}" for k, m in crashes))
    print(f"\n{_checks} checks, FAILURES: {len(failures)}")
    for name in failures:
        print("  -", name)
    db.close_pool()
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
