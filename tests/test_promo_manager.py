"""ManagerBot's side of the 220 ⚡ gift: the counter's job, /users, /promo and its card. No database, no network.

    python tests/test_promo_manager.py

The owner (2026-10-18): a unique-user counter across all the bots, a message to him when it reaches 220, "one
short command" that starts the gift, and the gift "not general, so it dies after first usage". family_link keeps
the state (tests/test_promo.py holds that); ManagerBot is the owner's side of it, and this holds the parts that
decide whether it is right. The family_link functions it calls are replaced by one in-memory gift that follows
their contracts (so a wrong call, or a call with the wrong freshness, shows), and the clock stands still at
2026-10-20 12:30 UTC:

  * the 15-minute job: below the mark it says nothing; at the mark it messages the owner once, with the milestone
    written only after the message went; a send that fails (notify_owner reporting False, or raising) leaves the
    milestone unwritten so the next count sends it again; once written, never again; a database error is a
    warning naming the kind and nothing else, and the next count carries on;
  * /users: the new last line, counted when /users runs, and its error form;
  * /promo in every state, read fresh every time: not started below the mark, the card once the mark is reached
    (or was, the milestone row outliving a later dip), started and open, stopped early, over by time; a database
    error anywhere; /promo stop and its two answers; a wrong argument;
  * the card's buttons: Start starts it once and a double tap, even two at the same moment, does not start it
    twice nor claim success twice; Cancel; a card that is not the waiting one; the mark checked again at the tap;
    a gift that was started meanwhile; a start that lost the race; a database error; and `guard` on the command
    and on the buttons;
  * the help page, the help line, the menu entry and the wiring (the command, the buttons' pattern, the job).

To see a check catch breakage, copy the folders into a scratch folder, break the copy and run this again from
there: the test reads the folder it is in. (Or build tests/.mirror, as run.py does: it imports from the mirror
when there is one.)
"""
import asyncio
import contextlib
import logging
import os
import pathlib
import re
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent


def source_dir(folder):
    mirrored = HERE / ".mirror" / folder
    return mirrored if mirrored.exists() else REPO / folder


SRC = source_dir("manager_bot")
sys.path.insert(0, str(SRC))
sys.path.insert(1, str(REPO / "shared"))
sys.path.insert(0, str(HERE / "stubs"))

for var in ("SBOT", "CBOT", "DBOT", "ABOT", "MBOT"):
    os.environ[f"{var}_TOKEN"] = "123:test"
    os.environ[f"{var}_USERNAME"] = "test"
    os.environ[f"{var}_ADMIN_ID"] = "1"
os.environ.update(ENV_FILES="off", DATABASE_URL="postgresql://stub", MBOT_REGISTRY="")
os.environ.pop("SIBLING_BOTS", None)
os.environ.pop("FAMILY_REGISTRY", None)

logging.disable(logging.CRITICAL)

import bot  # noqa: E402  (ManagerBot's)
import family_link as fl  # noqa: E402

failures = []
checks = 0


def check(name, ok, detail=""):
    global checks
    checks += 1
    print(("  ok  " if ok else "  FAIL  ") + name + (f" -- {detail}" if not ok and detail != "" else ""))
    if not ok:
        failures.append(name)


# One event loop for the whole run (see test_manager_help.py: a loop per call is a socket pair per call on Windows).
LOOP = asyncio.new_event_loop()


def run_async(coroutine):
    return LOOP.run_until_complete(coroutine)


# ---------------------------------------------------------------------------
# a clock that stands still, and one gift that follows family_link's contracts
# ---------------------------------------------------------------------------

UTC = timezone.utc
NOW = datetime(2026, 10, 20, 12, 30, tzinfo=UTC)
MARK, AMOUNT = fl.PROMO_USERS, fl.PROMO_CREDITS
CLOSES_FROM_NOW = datetime(2026, 11, 4, tzinfo=UTC)         # the end of the 14th day after the 20th: 3 Nov, so midnight 4 Nov


class FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW if tz is not None else NOW.replace(tzinfo=None)


class Boom(Exception):
    pass


class Gift:
    """The state family_link keeps, and the functions ManagerBot calls on it, with their contracts:
    promo_window(fresh) is (started, closes) or None, start_promo is once-only, stop_promo needs it open."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.started = None
        self.stopped = None
        self.milestone = None
        self.people = 0
        self.claims = 0
        self.seen = 0                 # what promo_seen_count answers for AnonBot's schema (None: no such table)
        self.broken = {}              # function name -> the exception it raises
        self.calls = []               # (name, args) in the order called
        self.race = None              # a start_promo that loses: somebody else started it first, at this moment

    def _enter(self, name, *args):
        self.calls.append((name, args))
        if name in self.broken:
            raise self.broken[name]

    def count(self, name):
        return sum(1 for called, _ in self.calls if called == name)

    # --- family_link's ---
    def promo_window(self, fresh=False):
        self._enter("promo_window", fresh)
        if self.started is None:
            return None
        closes = fl.promo_closes_for(self.started)
        if self.stopped is not None and self.stopped < closes:
            closes = self.stopped
        return self.started, closes

    def promo_milestone_at(self):
        self._enter("promo_milestone_at")
        return self.milestone

    def note_promo_milestone(self):
        self._enter("note_promo_milestone")
        first = self.milestone is None
        self.milestone = self.milestone or NOW
        return first

    def promo_claim_count(self):
        self._enter("promo_claim_count")
        return self.claims

    def start_promo(self):
        self._enter("start_promo")
        if self.race is not None:
            self.started = self.race
            return self.started, False
        if self.started is not None:
            return self.started, False
        self.started = NOW
        return self.started, True

    def stop_promo(self):
        self._enter("stop_promo")
        window = self.promo_window()
        if window is None or not window[0] <= NOW < window[1]:
            return False
        self.stopped = NOW
        return True

    # --- ManagerBot's db's ---
    def unique_people(self, schemas):
        self._enter("unique_people", tuple(schemas))
        return self.people

    def promo_seen_count(self, schema, promo):
        self._enter("promo_seen_count", schema, promo)
        return self.seen


gift = Gift()
saved = SimpleNamespace(
    fl={name: getattr(fl, name) for name in ("promo_window", "promo_milestone_at", "note_promo_milestone",
                                             "promo_claim_count", "start_promo", "stop_promo", "datetime")},
    db={name: getattr(bot.db, name) for name in ("unique_people", "promo_seen_count", "active_users_by_schema")},
    bot={name: getattr(bot, name) for name in ("guard", "edit_in_place", "datetime", "notify_owner")},
)
for name in ("promo_window", "promo_milestone_at", "note_promo_milestone", "promo_claim_count", "start_promo",
             "stop_promo"):
    setattr(fl, name, getattr(gift, name))
fl.datetime = FrozenDatetime
bot.datetime = FrozenDatetime
bot.db.unique_people, bot.db.promo_seen_count = gift.unique_people, gift.promo_seen_count


def restore():
    for name, value in saved.fl.items():
        setattr(fl, name, value)
    for name, value in saved.db.items():
        setattr(bot.db, name, value)
    for name, value in saved.bot.items():
        setattr(bot, name, value)


# ---------------------------------------------------------------------------
# a fake Telegram: what a handler said, to whom
# ---------------------------------------------------------------------------

replies, edits, answers, sent = [], [], [], []
order = []                                  # what happened, in what order: ("sent", text) / ("milestone",)


class Message:
    async def reply_text(self, text, **kwargs):
        replies.append((text, kwargs))


class Query:
    def __init__(self, data):
        self.data = data
        self.message = SimpleNamespace(tag="the card")

    async def answer(self, *a, **k):
        answers.append(self.data)


async def edit_in_place(message, bot_, text, **kwargs):
    edits.append(text)


async def allowed(update, context):
    return True


async def refused(update, context):
    replies.append(("This is a private bot.", {}))
    return False


def make_update(query=None):
    return SimpleNamespace(message=Message(), callback_query=query, effective_user=SimpleNamespace(id=7),
                           effective_chat=SimpleNamespace(id=70))


bot.guard, bot.edit_in_place = allowed, edit_in_place


def fresh():
    gift.reset()
    for box in (replies, edits, answers, sent, order):
        box.clear()
    bot._promo_count.update(count=None, at=None)


def command(handler, user_data, *args):
    context = SimpleNamespace(args=list(args), user_data=user_data, bot=SimpleNamespace())
    run_async(handler(make_update(), context))
    return context


def tap(user_data, data):
    query = Query(data)
    context = SimpleNamespace(args=[], user_data=user_data, bot=SimpleNamespace())
    run_async(bot.promo_button(make_update(query), context))
    return query


def card(reply):
    """The buttons on a /promo reply, as (label, callback data)."""
    return [(b.args[0], b.kwargs["callback_data"]) for row in reply[1]["reply_markup"].inline_keyboard for b in row]


def moment(when):
    return when.strftime("%Y-%m-%d %H:%M UTC")


ADMIN = next(iter(bot.ADMIN_IDS))


async def send_message(chat_id=None, text=None, **kwargs):
    if bot_state["send_fails"]:
        raise Boom("Telegram is away")
    sent.append((chat_id, text, kwargs))
    order.append(("sent", text))


bot_state = {"send_fails": False}


def job_context():
    return SimpleNamespace(bot=SimpleNamespace(send_message=send_message), user_data={})


def run_job():
    run_async(bot.count_unique_people(job_context()))


@contextlib.contextmanager
def logged():
    """The warnings the job writes, as one string; the tests keep logging off otherwise."""
    lines = []

    class Collect(logging.Handler):
        def emit(self, record):
            lines.append(record.getMessage())

    handler = Collect(level=logging.DEBUG)
    # Only this handler: the bot's own would print the traceback of a send that is meant to fail.
    previous = (bot.logger.handlers[:], bot.logger.propagate, bot.logger.level)
    bot.logger.handlers, bot.logger.propagate = [handler], False
    bot.logger.setLevel(logging.DEBUG)
    logging.disable(logging.NOTSET)
    try:
        yield lines
    finally:
        logging.disable(logging.CRITICAL)
        bot.logger.handlers, bot.logger.propagate = previous[0], previous[1]
        bot.logger.setLevel(previous[2])


# What the owner is told, and what he reads, written out here and not read from the bot.
def milestone_text(count):
    return (f"🎉 Mumu bots have reached {count} unique users (everyone who has used any of the bots, counted once). "
            f"That's the mark for the 220 ⚡ gift: start it with /promo.")


STALE = ("That card is not the one waiting (a newer /promo replaced it, or it is from before an update). "
         "Nothing was started. Send /promo again.")

ERROR = lambda exc: f"Could not read the gift's state ({exc}). Nothing was changed."      # noqa: E731

check("the figures this test is written for: 220 users, 220 ⚡, 14 days to claim, 28 to spend",
      (MARK, AMOUNT, fl.PROMO_CLAIM_DAYS, fl.PROMO_SPEND_DAYS) == (220, 220, 14, 28))
check("the clock: the end of the 14th day after 20 October is midnight on 4 November",
      fl.promo_closes_for(NOW) == CLOSES_FROM_NOW, fl.promo_closes_for(NOW))

try:
    # -----------------------------------------------------------------------
    print("\n== the 15-minute count ==")
    # -----------------------------------------------------------------------
    for people, why in ((0, "nobody yet"), (MARK - 1, "one short of the mark")):
        fresh()
        gift.people = people
        run_job()
        check(f"{why} ({people}): the owner is told nothing, no milestone is written",
              sent == [] and gift.milestone is None and gift.count("note_promo_milestone") == 0, sent)
        check("... and the figure is kept in memory, with the time",
              bot._promo_count == {"count": people, "at": NOW}, bot._promo_count)
        check("... counted over the schemas of the four bots, not ManagerBot's own",
              gift.calls[0] == ("unique_people", (tuple(b["schema"] for b in bot.CHILDREN),))
              and bot.db.DB_SCHEMA not in gift.calls[0][1][0], gift.calls[0])

    fresh()
    gift.people = MARK
    run_job()
    check("at the mark (220): the owner gets the message, once, with the count and the way to start",
          [(chat, text) for chat, text, _ in sent] == [(ADMIN, milestone_text(MARK))], sent)
    check("... and the milestone is written", gift.milestone == NOW and gift.count("note_promo_milestone") == 1)
    check("... after the message went, not before it", order == [("sent", milestone_text(MARK))]
          and [n for n, _ in gift.calls].index("note_promo_milestone") > [n for n, _ in gift.calls].index("promo_milestone_at"))
    gift.people = MARK + 40
    run_job()
    run_job()
    check("the next counts, with more people, say nothing: never twice", len(sent) == 1 and gift.count("note_promo_milestone") == 1, sent)
    check("... but the figure in memory follows", bot._promo_count["count"] == MARK + 40)

    fresh()
    gift.people = MARK
    gift.started = NOW - timedelta(hours=1)        # the owner saw the count in /users and started it first
    run_job()
    check("already started when the count reaches the mark: no stale 'start it with /promo', the milestone is noted",
          sent == [] and gift.milestone == NOW and gift.count("note_promo_milestone") == 1, sent)
    check("... the state was read fresh, not from a cache", ("promo_window", (True,)) in gift.calls, gift.calls)

    print("\n-- the message does not go, and the milestone is not claimed")
    for how in ("notify_owner reports that nobody got it", "notify_owner raises"):
        fresh()
        gift.people = MARK + 3
        if how.endswith("reports that nobody got it"):
            bot_state["send_fails"] = True          # the real notify_owner catches this and answers False
        else:
            async def notify_raises(context, text):
                raise Boom("the send blew up")
            bot.notify_owner = notify_raises
        with logged() as lines:
            run_job()
        check(f"{how}: no milestone is written, so it is not lost", gift.milestone is None and gift.count("note_promo_milestone") == 0,
              gift.calls)
        check("... a warning says so, and names no person", len(lines) >= 1 and all("Traceback" not in line for line in lines), lines)
        bot_state["send_fails"] = False
        bot.notify_owner = saved.bot["notify_owner"]
        run_job()
        check(f"{how}: the next count sends it, and only then writes the milestone",
              [text for _, text, _ in sent] == [milestone_text(MARK + 3)] and gift.milestone == NOW, (sent, gift.milestone))
        run_job()
        check("... and the one after says nothing", len(sent) == 1 and gift.count("note_promo_milestone") == 1)

    print("\n-- a database error")
    for broken in ("unique_people", "promo_milestone_at", "note_promo_milestone"):
        fresh()
        gift.people = MARK + 1
        gift.broken[broken] = Boom("postgresql://secret-password@host")
        with logged() as lines:
            try:
                run_job()
                raised = False
            except Exception:                                    # noqa: BLE001
                raised = True
        check(f"{broken} fails: the job does not raise, it warns with the kind of error and not its words",
              not raised and any("Boom" in line for line in lines) and not any("secret-password" in line for line in lines), lines)
        check("... and the owner is told nothing it cannot back up"
              if broken != "note_promo_milestone" else "... the notice went but the milestone was not written (it will be sent again)",
              len(sent) == (1 if broken == "note_promo_milestone" else 0) and gift.milestone is None, sent)
        del gift.broken[broken]
        run_job()
        check(f"... the next count carries on once the database is back", gift.milestone == NOW
              and len(sent) == (2 if broken == "note_promo_milestone" else 1), (gift.milestone, sent))

    # -----------------------------------------------------------------------
    print("\n== /users ==")
    # -----------------------------------------------------------------------
    bot.db.active_users_by_schema = lambda schemas, since, include_known=False: {}
    fresh()
    gift.people = 187
    command(bot.users_command, {})
    lines = replies[0][0].splitlines()
    check("/users ends with one line: the unique people across all bots and the gift's mark",
          lines[-1] == "👤 Unique people across all bots: 187 (the gift's mark is 220)", lines)
    check("... counted when /users runs (the database was asked, the job's memory was not used)",
          gift.count("unique_people") == 1 and bot._promo_count["count"] is None)
    check("... after the bots' lines, which are as they were", lines[0].startswith("👥 Active users, last 24h")
          and lines[-2].startswith("  ") and len(lines) == 2 + len(bot.ALL_BOTS) + 1, lines)
    fresh()
    gift.broken["unique_people"] = ValueError("nope, with words")
    command(bot.users_command, {})
    check("/users when the count fails: the line says could not count, with the kind of error and nothing else",
          replies[0][0].splitlines()[-1] == "👤 Unique people across all bots: could not count (ValueError)", replies)
    check("... and the rest of /users is still there", replies[0][0].startswith("👥 Active users, last 24h"))
    fresh()
    bot.guard = refused
    command(bot.users_command, {})
    check("a stranger's /users counts nothing", gift.count("unique_people") == 0 and replies == [("This is a private bot.", {})])
    bot.guard = allowed

    # -----------------------------------------------------------------------
    print("\n== /promo: not started ==")
    # -----------------------------------------------------------------------
    fresh()
    gift.people = 187
    user_data = {}
    command(bot.promo_command, user_data)
    check("below the mark: how many it takes, how many there are, and that the owner will be told",
          [text for text, _ in replies] == ["The 220 ⚡ gift can start once the bots have 220 unique users. Now: 187. "
                                          "I'll message you when it happens."], replies)
    check("... plain text, no buttons, nothing waiting", "reply_markup" not in replies[0][1] and "parse_mode" not in replies[0][1]
          and user_data == {})
    check("... the state was read fresh (never from the cache), and the count was live",
          gift.count("promo_window") >= 1 and all(args == (True,) for name, args in gift.calls if name == "promo_window"), gift.calls)

    for people, milestone, why in ((MARK, None, "the count is exactly the mark"), (MARK + 25, None, "the count is above it"),
                                   (MARK - 70, NOW - timedelta(days=2), "the count dipped back under it after the milestone was noted")):
        fresh()
        gift.people, gift.milestone = people, milestone
        user_data = {}
        command(bot.promo_command, user_data)
        text, kwargs = replies[0]
        nonce = user_data.get(bot.PROMO_PENDING)
        check(f"{why}: the card, in HTML, with the two buttons",
              kwargs.get("parse_mode") == bot.ParseMode.HTML and len(replies) == 1
              and card(replies[0]) == [("▶️ Start the gift", f"promo:go:{nonce}"), ("Cancel", f"promo:cancel:{nonce}")], card(replies[0]))
        check("... a fresh nonce is waiting, 8 hex digits, both callbacks within Telegram's 64 bytes and the registered pattern",
              isinstance(nonce, str) and re.fullmatch(r"[0-9a-f]{8}", nonce) and user_data == {bot.PROMO_PENDING: nonce}
              and all(re.fullmatch(bot.PROMO_BUTTON_PATTERN, data) and len(data.encode()) <= 64 for _, data in card(replies[0])), user_data)
        ad = fl.promo_ad("en", CLOSES_FROM_NOW)
        want = (f"Start the 220 ⚡ gift? This can be done once.\n"
                f"• Anyone who writes to ConvertBot or DownloaderBot until 03.11.2026 (the end of that day, UTC) gets 220 ⚡ of "
                f"bonus credit, once, automatically, and a notice.\n"
                f"• Each gift can be spent for 28 days from the day it is claimed.\n"
                f"• AnonBot shows this ad once to each person who writes to it, until then:\n\n"
                f"{ad}\n\n"
                f"Unique users now: {people}.")
        check(f"... the whole card, word for word, with the ad as people will read it and the day claiming ends", text == want,
              text[:200])
        check("... the last claiming day is the day BEFORE the midnight it closes at (3 November, not 4)",
              "until 03.11.2026" in text and "04.11.2026" not in text.replace(ad, ""))
        check("... read fresh", all(args == (True,) for name, args in gift.calls if name == "promo_window"))

    fresh()
    gift.people = MARK
    first, second = {}, {}
    command(bot.promo_command, first)
    command(bot.promo_command, second)
    check("each /promo card has a nonce of its own", first[bot.PROMO_PENDING] != second[bot.PROMO_PENDING])
    fresh()
    gift.people = MARK
    user_data = {}
    command(bot.promo_command, user_data)
    command(bot.promo_command, user_data)
    check("a newer card replaces the waiting one in the same chat",
          user_data[bot.PROMO_PENDING] == card(replies[1])[0][1].split(":")[2] != card(replies[0])[0][1].split(":")[2])

    fresh()
    user_data = {}
    gift.broken["unique_people"] = Boom("x")
    command(bot.promo_command, user_data)
    check("a database error counting: it says so, with the kind and 'Nothing was changed', and no card waits",
          [text for text, _ in replies] == [ERROR("Boom")] and user_data == {}, replies)
    for broken in ("promo_window", "promo_milestone_at"):
        fresh()
        gift.people = MARK
        gift.broken[broken] = RuntimeError("down")
        user_data = {}
        command(bot.promo_command, user_data)
        check(f"{broken} fails: the same sentence, and no card",
              [text for text, _ in replies] == [ERROR("RuntimeError")] and user_data == {}, replies)

    # -----------------------------------------------------------------------
    print("\n== /promo: started ==")
    # -----------------------------------------------------------------------
    STARTED = NOW - timedelta(days=3, hours=2)                  # 17 Oct 10:30
    OPEN_UNTIL = fl.promo_closes_for(STARTED)                   # 1 Nov 00:00 UTC
    check("the dates this section reads: started 17 Oct 10:30, claims close on 1 Nov at midnight",
          (moment(STARTED), moment(OPEN_UNTIL)) == ("2026-10-17 10:30 UTC", "2026-11-01 00:00 UTC"))
    fresh()
    gift.started, gift.claims, gift.seen = STARTED, 41, 77
    user_data = {}
    command(bot.promo_command, user_data)
    check("open: when it started, until when claims are open, how many claimed, how many saw the ad, and how to stop it",
          [text for text, _ in replies] == ["The 220 ⚡ gift started 2026-10-17 10:30 UTC.\n"
                                          "Claims are open until 2026-11-01 00:00 UTC.\n"
                                          "Claimed by 41 people. AnonBot showed the ad to 77 people.\n"
                                          "To end the claiming now: /promo stop"], replies)
    check("... plain text, no card, nothing waiting", "reply_markup" not in replies[0][1] and "parse_mode" not in replies[0][1]
          and user_data == {})
    anon_schema = next(b["schema"] for b in bot.CHILDREN if b["id"] == "anonbot")
    check("... the ad's count is asked of AnonBot's schema, for this gift's key",
          ("promo_seen_count", (anon_schema, fl.PROMO_KEY)) in gift.calls, gift.calls)
    check("... read fresh, and the people were not counted (the gift is started)",
          all(args == (True,) for name, args in gift.calls if name == "promo_window") and gift.count("unique_people") == 0)

    fresh()
    gift.started, gift.claims, gift.seen = STARTED, 0, None
    command(bot.promo_command, {})
    check("no promo_seen table in AnonBot's schema: '?' people saw the ad", "AnonBot showed the ad to ? people." in replies[0][0]
          and "Claimed by 0 people." in replies[0][0], replies)
    fresh()
    gift.started, gift.claims, gift.seen = STARTED, 5, 9
    real_children = bot.CHILDREN
    bot.CHILDREN = [b for b in real_children if b["id"] != "anonbot"]
    try:
        command(bot.promo_command, {})
    finally:
        bot.CHILDREN = real_children
    check("no AnonBot in the registry: '?' too, and the table is not asked", "showed the ad to ? people." in replies[0][0]
          and gift.count("promo_seen_count") == 0, replies)

    fresh()
    gift.started, gift.claims, gift.seen = STARTED, 41, 77
    gift.stopped = NOW - timedelta(hours=5)
    command(bot.promo_command, {})
    check("stopped early: 'closed at' the moment it was stopped, marked as stopped early, and no way to stop it again",
          [text for text, _ in replies] == ["The 220 ⚡ gift started 2026-10-17 10:30 UTC.\n"
                                          "Claims closed at 2026-10-20 07:30 UTC -- stopped early.\n"
                                          "Claimed by 41 people. AnonBot showed the ad to 77 people."], replies)
    fresh()
    gift.started, gift.claims, gift.seen = NOW - timedelta(days=20), 300, 410
    command(bot.promo_command, {})
    check("over by time (14 days gone): 'closed at' midnight, not marked as stopped early, no way to stop it",
          [text for text, _ in replies] == ["The 220 ⚡ gift started 2026-09-30 12:30 UTC.\n"
                                          "Claims closed at 2026-10-15 00:00 UTC.\n"
                                          "Claimed by 300 people. AnonBot showed the ad to 410 people."], replies)
    fresh()
    gift.started, gift.claims = NOW - timedelta(days=20), 3
    gift.stopped = datetime(2026, 10, 15, tzinfo=UTC) + timedelta(hours=9)       # after it closed on its own
    command(bot.promo_command, {})
    check("a stop that came after the end changes nothing: still not 'stopped early'", "stopped early" not in replies[0][0], replies)
    fresh()
    gift.started = STARTED
    gift.people = 5
    command(bot.promo_command, {})
    check("a started gift is not a card even when the count is under the mark", "reply_markup" not in replies[0][1]
          and "started 2026-10-17 10:30 UTC" in replies[0][0])
    for broken in ("promo_claim_count", "promo_seen_count"):
        fresh()
        gift.started = STARTED
        gift.broken[broken] = OSError("gone")
        command(bot.promo_command, {})
        check(f"{broken} fails: the same sentence, with the kind of error", [text for text, _ in replies] == [ERROR("OSError")], replies)

    # -----------------------------------------------------------------------
    print("\n== /promo stop ==")
    # -----------------------------------------------------------------------
    fresh()
    gift.started, gift.claims = STARTED, 41
    user_data = {}
    command(bot.promo_command, user_data, "stop")
    check("open: stopped, and the people who have it keep it",
          [text for text, _ in replies] == ["Stopped. Nobody can claim the gift any more; the 41 people who have it keep it "
                                          "until it runs out."], replies)
    check("... stop_promo was asked once, and the gift reads as stopped now", gift.count("stop_promo") == 1 and gift.stopped == NOW
          and user_data == {})
    command(bot.promo_command, user_data, "stop")
    check("a second /promo stop: not open any more, nothing to stop",
          replies[-1][0] == "The gift is not open (not started yet, or already over). Nothing to stop.", replies)
    fresh()
    command(bot.promo_command, {}, "stop")
    check("not started: nothing to stop", replies[0][0] == "The gift is not open (not started yet, or already over). Nothing to stop.")
    fresh()
    gift.started = NOW - timedelta(days=20)
    command(bot.promo_command, {}, "stop")
    check("already over: nothing to stop, and nothing written", replies[0][0].startswith("The gift is not open")
          and gift.stopped is None)
    for argv in (["start"], ["stop", "now"], ["go"], ["220"], ["--go"], ["stop;"]):
        fresh()
        gift.people, user_data = MARK, {}
        command(bot.promo_command, user_data, *argv)
        check(f"/promo {' '.join(argv)}: the usage line, and nothing read, started or stopped",
              [text for text, _ in replies] == ["Usage: /promo, or /promo stop."] and gift.calls == [] and user_data == {}, (replies, gift.calls))
    fresh()
    gift.started = STARTED
    gift.broken["stop_promo"] = Boom("x")
    command(bot.promo_command, {}, "stop")
    check("/promo stop when the database fails: the error sentence", [text for text, _ in replies] == [ERROR("Boom")], replies)

    # -----------------------------------------------------------------------
    print("\n== the card's buttons ==")
    # -----------------------------------------------------------------------
    def waiting_card(people=MARK):
        fresh()
        gift.people = people
        user_data = {}
        command(bot.promo_command, user_data)
        go, cancel = [data for _, data in card(replies[-1])]
        return user_data, go, cancel

    user_data, GO, CANCEL = waiting_card()
    nonce = user_data[bot.PROMO_PENDING]
    query = tap(user_data, GO)
    check("Start: the gift is started, once", gift.count("start_promo") == 1 and gift.started == NOW)
    check("... the card says when it started and the last day to claim, and how to follow it",
          edits == ["Started 2026-10-20 12:30 UTC. Claims close 03.11.2026 (the end of that day, UTC). /promo shows how it goes."], edits)
    check("... the tap was answered, nothing waits any more, and the card is remembered as done",
          answers == [GO] and user_data == {bot.PROMO_DONE: nonce}, (answers, user_data))
    tap(user_data, GO)
    tap(user_data, CANCEL)
    check("a second tap of Start, or a Cancel on the same card, does nothing: no second start, the card is left as it says",
          gift.count("start_promo") == 1 and len(edits) == 1, (gift.calls, edits))
    command(bot.promo_command, user_data)
    check("and /promo now shows the started gift, not a card", replies[-1][0].startswith("The 220 ⚡ gift started 2026-10-20 12:30 UTC.")
          and "reply_markup" not in replies[-1][1])

    user_data, GO, CANCEL = waiting_card()
    tap(user_data, CANCEL)
    check("Cancel: nothing is started, the card says so", gift.count("start_promo") == 0 and edits == ["Cancelled. Nothing was started."]
          and gift.started is None, (edits, gift.calls))
    check("... and the card is spent: Start on it afterwards starts nothing (silently, as the card already says cancelled)",
          (tap(user_data, GO), gift.count("start_promo"), len(edits))[1:] == (0, 1))

    print("\n-- two taps at the same moment")
    user_data, GO, CANCEL = waiting_card()
    context = SimpleNamespace(args=[], user_data=user_data, bot=SimpleNamespace())

    async def both():
        await asyncio.gather(bot.promo_button(make_update(Query(GO)), context), bot.promo_button(make_update(Query(GO)), context),
                             bot.promo_button(make_update(Query(CANCEL)), context))

    run_async(both())
    check("Start, Start and Cancel together: started once, and only one card text claims it",
          gift.count("start_promo") == 1 and len(edits) == 1 and edits[0].startswith("Started "), (gift.calls, edits))

    print("\n-- a card that is not the waiting one")
    user_data, OLD_GO, OLD_CANCEL = waiting_card()
    command(bot.promo_command, user_data)
    NEW_GO, NEW_CANCEL = [data for _, data in card(replies[-1])]
    waiting = dict(user_data)
    tap(user_data, OLD_GO)
    check("Start on an older card starts nothing and says that card is not the one waiting",
          gift.count("start_promo") == 0 and edits == [STALE], (edits, gift.calls))
    check("... the newer card still waits, untouched", user_data == waiting, user_data)
    tap(user_data, OLD_CANCEL)
    check("Cancel on the older card does not cancel the newer one", user_data == waiting and edits[-1] == STALE)
    tap(user_data, "promo:go:00000000")
    tap(user_data, "promo:go")
    tap(user_data, "promo:go:")
    check("a nonce that was never issued, none, or an empty one: the same sentence, nothing started, the card still waits",
          gift.count("start_promo") == 0 and edits[-3:] == [STALE] * 3 and user_data == waiting, edits)
    tap(user_data, NEW_GO)
    check("the newer card's Start then starts it", gift.count("start_promo") == 1 and edits[-1].startswith("Started "))
    tap({}, NEW_GO)
    check("nothing waiting at all (a card from before an update): the same sentence, nothing started",
          gift.count("start_promo") == 1 and edits[-1] == STALE)

    print("\n-- the mark is checked again at the tap")
    user_data, GO, CANCEL = waiting_card()
    gift.people, gift.milestone = MARK - 31, None
    tap(user_data, GO)
    check("fewer people than the mark now and no milestone ever noted: not started, and it says how many",
          gift.count("start_promo") == 0 and edits == ["Not yet: 189 unique users of 220. Nothing was started."]
          and gift.started is None, (edits, gift.calls))
    check("... the card is spent (send /promo again)", user_data == {bot.PROMO_DONE: user_data.get(bot.PROMO_DONE)}
          and bot.PROMO_PENDING not in user_data)
    user_data, GO, CANCEL = waiting_card()
    gift.people, gift.milestone = MARK - 31, NOW - timedelta(days=1)
    tap(user_data, GO)
    check("fewer people now but the milestone was noted: it starts (a dip never blocks it)",
          gift.count("start_promo") == 1 and edits[-1].startswith("Started "), edits)

    print("\n-- started meanwhile, by another card or another process")
    user_data, GO, CANCEL = waiting_card()
    gift.started = NOW - timedelta(hours=2)
    tap(user_data, GO)
    check("already started when the tap comes: nothing is started, it says when it was",
          gift.count("start_promo") == 0 and edits == ["It had already started 2026-10-20 10:30 UTC. Nothing changed."], (edits, gift.calls))
    user_data, GO, CANCEL = waiting_card()
    gift.race = NOW - timedelta(seconds=40)
    tap(user_data, GO)
    check("start_promo loses the race (somebody started it between the look and the start): it says so and does not claim success",
          gift.count("start_promo") == 1 and edits == ["It had already started 2026-10-20 12:29 UTC. Nothing changed."], edits)

    print("\n-- a database error")
    for broken, expect_start in (("promo_window", 0), ("unique_people", 0), ("promo_milestone_at", 0), ("start_promo", 1)):
        user_data, GO, CANCEL = waiting_card()
        if broken == "promo_milestone_at":
            gift.people = MARK - 1                  # only then is the milestone asked
        gift.broken[broken] = Boom("down")
        tap(user_data, GO)
        check(f"{broken} fails at the tap: 'Could not start it', the kind of error, nothing was started, send /promo again",
              edits == ["Could not start it (Boom). Nothing was started; send /promo again."] and gift.started is None
              and gift.count("start_promo") == expect_start, (edits, gift.calls))
        check("... and the card is spent, the next /promo is a new card", bot.PROMO_PENDING not in user_data)

    print("\n-- somebody who is not the owner")
    fresh()
    bot.guard = refused
    user_data = {}
    gift.people = MARK
    command(bot.promo_command, user_data)
    command(bot.promo_command, user_data, "stop")
    check("a stranger's /promo and /promo stop read nothing, change nothing, and leave no card",
          gift.calls == [] and user_data == {} and [t for t, _ in replies] == ["This is a private bot."] * 2, (gift.calls, replies))
    bot.guard = allowed
    user_data, GO, CANCEL = waiting_card()
    waiting = dict(user_data)
    bot.guard = refused
    for data in (GO, CANCEL):
        tap(user_data, data)
    check("a stranger's Start or Cancel starts nothing, rewrites nothing, answers nothing and does not use up the owner's card",
          gift.count("start_promo") == 0 and edits == [] and answers == [] and user_data == waiting and gift.started is None,
          (gift.calls, edits, answers, user_data))
    bot.guard = allowed
    tap(user_data, GO)
    check("... and the owner's tap afterwards still starts it", gift.count("start_promo") == 1 and gift.started == NOW)

    source = (SRC / "bot.py").read_text(encoding="utf-8")
    for name in ("promo_command", "promo_button"):
        body = source[source.index(f"async def {name}("):]
        body = body[:body.index("\nasync def ") if "\nasync def " in body[10:] else len(body)]
        check(f"{name} asks guard before it does anything else",
              body.index("guard(") < min(i for i in (body.find("await asyncio.to_thread"), body.find("edit_in_place"),
                                                    body.find("query.answer"), body.find("reply_text")) if i >= 0), name)

    # -----------------------------------------------------------------------
    print("\n== help, menu and wiring ==")
    # -----------------------------------------------------------------------
    check("/help lists /promo, near /gift, with the amount read from the constant",
          "/promo [stop] — the 220 ⚡ gift: start it (once), see how it" in bot.HELP
          and bot.HELP.index("/gift &lt;bot|all&gt;") < bot.HELP.index("/promo [stop]") < bot.HELP.index("/donations"))
    check("... and HELP still fills in (the one placeholder left is {bots})",
          "{" not in bot.HELP.format(bots="x") and "Bots: <code>x</code>" in bot.HELP.format(bots="x"))
    menu = {c.args[0]: c.args[1] for c in bot.BOT_COMMANDS}
    check("the owner's menu has /promo next to /gift, described in under 50 characters",
          menu.get("promo") == "The 220 ⚡ gift: start, status, stop" and len(menu["promo"]) < 50
          and [c.args[0] for c in bot.BOT_COMMANDS].index("promo") == [c.args[0] for c in bot.BOT_COMMANDS].index("gift") + 1)
    pages = list(bot.HELP_PAGES)
    check("the help page is 'promo', right after 'gift'", "promo" in pages and pages.index("promo") == pages.index("gift") + 1, pages)
    text = bot.help_page_text("promo")
    check("it names the figures from the constants: the mark, the amount, the claim days, the spend days, the count's rhythm",
          all(str(n) in text for n in (fl.PROMO_USERS, fl.PROMO_CREDITS, fl.PROMO_CLAIM_DAYS, fl.PROMO_SPEND_DAYS,
                                       bot.PROMO_COUNT_SECONDS // 60)))
    for needle, why in (("once, ever", "that the start is once, ever"), ("cannot be undone", "that it cannot be undone"),
                        ("midnight UTC", "the midnight the claiming ends at"), ("once to each person", "that the ad is once per person"),
                        ("does not have the gift yet", "that it is not shown to anyone who has it"),
                        ("inbox link", "that it is not shown on an inbox link's /start"),
                        ("/promo stop", "the early stop"), ("/gift is the separate", "that /gift is the separate, general tool"),
                        ("Start the gift", "the card's button"), ("/users", "that /users shows the count")):
        check(f"the help page says {why}", needle in text, text[:0])
    old = (fl.PROMO_USERS, fl.PROMO_CREDITS, fl.PROMO_CLAIM_DAYS, fl.PROMO_SPEND_DAYS)
    fl.PROMO_USERS, fl.PROMO_CREDITS, fl.PROMO_CLAIM_DAYS, fl.PROMO_SPEND_DAYS = 3141, 2718, 61, 71
    try:
        changed = bot.help_page_text("promo")
    finally:
        fl.PROMO_USERS, fl.PROMO_CREDITS, fl.PROMO_CLAIM_DAYS, fl.PROMO_SPEND_DAYS = old
    check("the page follows the constants (3141 people, 2718 ⚡, 61 and 71 days) and loses the real ones",
          all(str(n) in changed for n in (3141, 2718, 61, 71)) and "220" not in changed.replace("2718", ""), changed[:0])
    check("the page ends with the way back to /help and has an example that is a form /promo takes",
          text.endswith("\n\nEverything: /help") and re.findall(r"^Example: (/.*)$", text, re.M) == ["/promo stop"])

    patterns = re.findall(r'CallbackQueryHandler\((\w+), pattern=(r"[^"]+"|\w+)\)', source)
    check("main() registers /promo to promo_command and the card's buttons to promo_button, under PROMO_BUTTON_PATTERN",
          'CommandHandler("promo", promo_command)' in source
          and "CallbackQueryHandler(promo_button, pattern=PROMO_BUTTON_PATTERN)" in source, patterns)
    for data, should in (("promo:go:0123abcd", True), ("promo:cancel:0123abcd", True), ("promo:go:0123ABCD", False),
                         ("promo:go:0123abc", False), ("promo:go:0123abcde", False), ("promo:stop:0123abcd", False),
                         ("promo:go", False), ("promo:go:", False), ("xpromo:go:0123abcd", False)):
        check(f"{data} {'reaches' if should else 'does not reach'} promo_button", bool(re.search(bot.PROMO_BUTTON_PATTERN, data)) == should)
    others = [fn for fn, pat in patterns if pat.startswith('r"') and re.search(pat[2:-1], "promo:go:0123abcd")]
    check("no other button handler takes a promo tap first", others == [], others)

    registered = []

    class FakeJobQueue:
        def run_repeating(self, callback, interval=None, first=None, **kwargs):
            registered.append((callback, interval, first))
            return object()

        def run_once(self, *a, **k):
            return object()

        def run_daily(self, *a, **k):
            return object()

    class Ran(Exception):
        pass

    class FakeApp:
        persistence = None

        def __init__(self):
            self.job_queue = FakeJobQueue()
            self.handlers = []

        def add_handler(self, handler, group=0):
            self.handlers.append(handler)

        def add_error_handler(self, callback):
            pass

        def run_polling(self, **kwargs):
            raise Ran

    class FakeBuilder:
        def token(self, *a):
            return self

        post_init = post_stop = persistence = token

        def build(self):
            FakeBuilder.app = FakeApp()
            return FakeBuilder.app

    class Wrote:
        def __init__(self, *args, **kwargs):
            self.args, self.kwargs = args, kwargs

    swapped = (bot.ApplicationBuilder, bot.CommandHandler, bot.MessageHandler, bot.CallbackQueryHandler, bot.init_db,
               bot.lifecycle.persistence, bot.lifecycle.install, fl.attach)
    bot.ApplicationBuilder, bot.CommandHandler, bot.MessageHandler, bot.CallbackQueryHandler = FakeBuilder, Wrote, Wrote, Wrote
    bot.init_db = lambda *a, **k: None
    bot.lifecycle.persistence, bot.lifecycle.install, fl.attach = (lambda: None), (lambda *a, **k: None), (lambda *a, **k: None)
    try:
        try:
            bot.main()
        except Ran:
            pass
    finally:
        (bot.ApplicationBuilder, bot.CommandHandler, bot.MessageHandler, bot.CallbackQueryHandler, bot.init_db,
         bot.lifecycle.persistence, bot.lifecycle.install, fl.attach) = swapped
    wired = FakeBuilder.app.handlers
    check("main() really adds the /promo command (to promo_command)", any(getattr(h, "args", ())[:2] == ("promo", bot.promo_command) for h in wired))
    check("... and the buttons, with the pattern, before nothing that could take them first",
          any(getattr(h, "args", ())[:1] == (bot.promo_button,) and h.kwargs.get("pattern") == bot.PROMO_BUTTON_PATTERN for h in wired))
    check("... and the counting job: every 15 minutes, the first after 60 seconds",
          [(c, i, f) for c, i, f in registered if c is bot.count_unique_people] == [(bot.count_unique_people, 900, 60)], registered)
finally:
    restore()

print()
if failures:
    print(f"FAILED ({len(failures)} of {checks}):")
    for name in failures:
        print("  -", name)
    sys.exit(1)
print(f"{checks} checks. The count, the one notice, /users, /promo in every state, the card that starts it once, and the owner only.")
