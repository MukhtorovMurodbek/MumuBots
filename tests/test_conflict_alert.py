"""One Conflict is a blip; three in two minutes is a second copy of the bot.

A telegram.error.Conflict ("terminated by other getUpdates request") used to
be filed as a crash and wake the owner. On 2026-10-09 a single one came out of
a ~90 s network/database stall and was a false alarm; a real second copy
polling the same token produces one on every poll cycle. So the rule is:

  * a Conflict from the long poll (no update behind it) is a warning in the
    log, with numbers only, and is not recorded
  * CONFLICT_ALERT_COUNT (3) of them inside CONFLICT_ALERT_WINDOW_S (120 s)
    is recorded once, through the same path a crash takes, in words that say
    what it means
  * then silence until CONFLICT_QUIET_RESET_S (600 s) pass with no Conflict at
    all, after which the count starts again
  * every other exception is recorded exactly as it always was

The real error handler is driven with a fake context and a fake monotonic
clock; no database, no Telegram. Each bot has its own copy of the state: the
combined edition loads this module once per bot, and a second copy loaded here
under another name proves nothing is shared between them.

Run through tests/run.py, which builds the .mirror folders it reads.
"""
import asyncio
import importlib.util
import logging
import os
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / ".mirror" / "anon_bot"))
sys.path.insert(0, str(HERE / "stubs"))

os.environ.setdefault("ABOT_TOKEN", "1:AA")
os.environ.setdefault("ABOT_USERNAME", "x")
os.environ.setdefault("DATABASE_URL", "postgresql://stub")

import shared_features as sf              # noqa: E402
# ManagerBot has no shared_features.py: its half of the same machinery is
# monitoring.py. Appended, so that db, family_link and live_message (all it
# imports) resolve to the ones already loaded and `monitoring` is its own.
sys.path.append(str(HERE / ".mirror" / "manager_bot"))
import monitoring as mon                  # noqa: E402
from telegram.error import BadRequest, Conflict, NetworkError   # noqa: E402

failures = []


def check(name, ok, detail=""):
    print(("  ok  " if ok else "  FAIL  ") + name + ("" if ok else f"  -- {detail}"))
    if not ok:
        failures.append(name)


class Ctx:
    def __init__(self, error):
        self.error = error


class Captured(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.records = []

    def emit(self, record):
        self.records.append(record)


# A message with something in it that must never reach a log line, to show the
# log is numbers only.
SECRET = "SECRET-TEXT-123456:ABCDEF"
POLL = "terminated by other getUpdates request; make sure that only one bot instance is running"

clock = [0.0]
events = []
logs = Captured()
for _module in (sf, mon):
    logging.getLogger(_module.__name__).addHandler(logs)
    logging.getLogger(_module.__name__).setLevel(logging.DEBUG)
    logging.getLogger(_module.__name__).propagate = False      # captured here, not printed
real_monotonic = sf.time.monotonic
sf.time.monotonic = lambda: clock[0]     # `time` is one module, so ManagerBot's copy reads it too
for _module in (sf, mon):
    _module.set_event_hook(lambda level, kind, message, details=None: events.append((level, kind, message, details)))


def reset():
    clock[0] = 1000.0     # not zero: a first conflict at time 0 must not look like "never"
    events.clear()
    logs.records.clear()
    for module in (sf, mon):
        module._conflict_times.clear()
        module._conflict_last = None
        module._conflict_alerted = False
        module._error_count = 0
        module._recent_errors.clear()


def fire(exc, at=None, update=None, module=None):
    """Hand the error handler this exception, as PTB does, at a given time."""
    if at is not None:
        clock[0] = 1000.0 + at
    asyncio.run((module or sf).error_handler(update, Ctx(exc)))


def crashes():
    return [e for e in events if e[1] == "crash"]


def warnings_logged():
    return [r for r in logs.records if r.levelno == logging.WARNING]


try:
    # -----------------------------------------------------------------------
    print("== the constants ==")
    # -----------------------------------------------------------------------
    check("three conflicts ...", sf.CONFLICT_ALERT_COUNT == 3, sf.CONFLICT_ALERT_COUNT)
    check("... within two minutes ...", sf.CONFLICT_ALERT_WINDOW_S == 120, sf.CONFLICT_ALERT_WINDOW_S)
    check("... and ten minutes of quiet before it can say so again",
          sf.CONFLICT_QUIET_RESET_S == 600, sf.CONFLICT_QUIET_RESET_S)

    # -----------------------------------------------------------------------
    print("\n== one conflict is a warning, not a crash ==")
    # -----------------------------------------------------------------------
    reset()
    fire(Conflict(f"{POLL} {SECRET}"), at=0)
    check("nothing reaches the owner", events == [], events)
    check("it is not counted as an error", sf._error_count == 0, sf._error_count)
    check("/status still says no errors", "No errors" in sf.error_summary(), sf.error_summary())
    warned = warnings_logged()
    check("it is logged, once, as a warning", len(warned) == 1, [r.getMessage() for r in logs.records])
    text = warned[0].getMessage() if warned else ""
    check("with numbers (how many, of how many, how soon)",
          "1" in text and "120" in text and "3" in text, text)
    check("and nothing from the error itself -- no token can get in",
          all(SECRET not in r.getMessage() and "getUpdates request" not in r.getMessage()
              and r.exc_info is None for r in logs.records), [r.getMessage() for r in logs.records])
    check("and it is not logged as an error either",
          not [r for r in logs.records if r.levelno >= logging.ERROR], [r.getMessage() for r in logs.records])
    fire(Conflict(POLL), at=10)
    check("the second is just as quiet", events == [] and sf._error_count == 0, events)
    check("and logged too", len(warnings_logged()) == 2, len(warnings_logged()))

    # -----------------------------------------------------------------------
    print("\n== three inside two minutes is reported, once ==")
    # -----------------------------------------------------------------------
    reset()
    fire(Conflict(POLL), at=0)
    fire(Conflict(POLL), at=50)
    check("two give no alert", events == [], events)
    fire(Conflict(POLL), at=100)
    got = crashes()
    check("the third gives exactly one", len(events) == 1 and len(got) == 1, events)
    level, kind, message, details = got[0] if got else (None, None, "", "")
    check("through the existing path: an error-level crash event", (level, kind) == ("error", "crash"),
          (level, kind))
    check("in words that say what it means",
          "Another copy of this bot seems to be polling with the same token" in message
          and "3 conflicts within 120 seconds" in message, message)
    check("and it is counted as one error", sf._error_count == 1, sf._error_count)
    check("which /status lists", "Another copy of this bot" in sf.error_summary(), sf.error_summary())
    check("with the poll's own error kept as the cause", "getUpdates" in (details or ""), details)
    check("the alert is logged as an error", len([r for r in logs.records if r.levelno >= logging.ERROR]) == 1,
          [(r.levelname, r.getMessage()) for r in logs.records])
    check("and no log line carries the error's own text",
          all(POLL not in r.getMessage() for r in logs.records))

    fire(Conflict(POLL), at=101)
    fire(Conflict(POLL), at=102)
    fire(Conflict(POLL), at=103)
    check("three more straight after say nothing", len(events) == 1 and sf._error_count == 1, events)
    for n in range(30):
        fire(Conflict(POLL), at=104 + n * 10)
    check("nor does a second copy that keeps on polling", len(events) == 1 and sf._error_count == 1,
          len(events))

    # -----------------------------------------------------------------------
    print("\n== three spread over more than two minutes are not ==")
    # -----------------------------------------------------------------------
    reset()
    for at in (0, 70, 140, 210, 280):          # any three in a row span 140 s
        fire(Conflict(POLL), at=at)
    check("five, each 70 s after the last, never make three in two minutes",
          events == [] and sf._error_count == 0, events)
    check("every one was logged", len(warnings_logged()) == 5, len(warnings_logged()))

    reset()
    for at in (0, 60, 120):
        fire(Conflict(POLL), at=at)
    check("exactly 120 s from first to third still counts", len(crashes()) == 1, events)

    reset()
    for at in (0, 60, 121):
        fire(Conflict(POLL), at=at)
    check("121 s does not", events == [], events)

    # An old one drops out of the count as time passes.
    reset()
    for at in (0, 100, 200):
        fire(Conflict(POLL), at=at)
    check("a conflict that has aged out is no longer counted", events == [], events)
    fire(Conflict(POLL), at=210)
    check("but the two newest and one more inside the window are",
          len(crashes()) == 1, events)

    # -----------------------------------------------------------------------
    print("\n== ten minutes of quiet, and it counts afresh ==")
    # -----------------------------------------------------------------------
    reset()
    for at in (0, 10, 20):
        fire(Conflict(POLL), at=at)
    check("the first run is reported", len(crashes()) == 1, events)
    # 599 s after the last one: not quiet enough. Three more in the window
    # must not report again.
    fire(Conflict(POLL), at=20 + 599)
    fire(Conflict(POLL), at=20 + 599 + 1)
    fire(Conflict(POLL), at=20 + 599 + 2)
    check("599 s of quiet is not enough -- three more say nothing", len(crashes()) == 1, events)
    # And the quiet is measured from the latest conflict, not from the alert.
    last = 20 + 599 + 2
    fire(Conflict(POLL), at=last + 599)
    fire(Conflict(POLL), at=last + 599 + 1)
    fire(Conflict(POLL), at=last + 599 + 2)
    check("it runs from the latest conflict, not from the report", len(crashes()) == 1, events)
    last = last + 599 + 2
    # Exactly 600 s of quiet: the count starts again.
    fire(Conflict(POLL), at=last + 600)
    check("after 600 s of quiet one conflict is a warning again", len(crashes()) == 1, events)
    check("... and the count began at one",
          len(sf._conflict_times) == 1, list(sf._conflict_times))
    fire(Conflict(POLL), at=last + 610)
    check("two are still not enough", len(crashes()) == 1, events)
    fire(Conflict(POLL), at=last + 620)
    check("three are reported again, once", len(crashes()) == 2, events)
    check("with the same words", crashes()[-1][2] == crashes()[0][2], [c[2] for c in crashes()])
    fire(Conflict(POLL), at=last + 621)
    fire(Conflict(POLL), at=last + 622)
    check("and then it is quiet again", len(crashes()) == 2, events)

    # A long quiet with nothing reported before it: the old ones are forgotten.
    reset()
    fire(Conflict(POLL), at=0)
    fire(Conflict(POLL), at=10)
    fire(Conflict(POLL), at=10 + 5000)
    check("two, a long silence, and one are not three", events == [], events)

    # -----------------------------------------------------------------------
    print("\n== everything else is recorded exactly as before ==")
    # -----------------------------------------------------------------------
    reset()
    fire(ValueError("a plain bug"), at=0)
    check("a plain bug is recorded at once, as a crash",
          len(crashes()) == 1 and sf._error_count == 1, events)
    check("with the old words", crashes()[0][2] == "Unhandled ValueError: a plain bug", crashes()[0][2])
    check("and its traceback", "ValueError" in (crashes()[0][3] or ""), crashes()[0][3])
    check("logged as an error with an incident",
          any(r.levelno == logging.ERROR and "incident" in r.getMessage() for r in logs.records),
          [r.getMessage() for r in logs.records])

    reset()
    fire(BadRequest("chat not found"), at=0)
    check("so is a BadRequest", len(crashes()) == 1 and sf._error_count == 1, events)

    reset()
    fire(NetworkError("httpx.ReadError"), at=0)
    check("a network blip is still just a blip", events == [] and sf._error_count == 0, events)

    # A Conflict raised while handling somebody's message is a failure of that
    # message, not the long poll -- it is recorded like any other.
    reset()

    class SomeUpdate:
        effective_chat = None
        effective_user = None

    fire(Conflict(POLL), at=0, update=SomeUpdate())
    check("a Conflict with an update behind it is recorded, as before",
          len(crashes()) == 1 and crashes()[0][2] == f"Unhandled Conflict: {POLL}", events)
    check("and it is not a poll conflict", len(sf._conflict_times) == 0, list(sf._conflict_times))

    reset()
    fire(Conflict(POLL), at=0)
    fire(Conflict(POLL), at=5)
    fire(Conflict(POLL), at=6, update=SomeUpdate())
    check("two poll conflicts and one with an update do not make three",
          len(crashes()) == 1 and "Another copy" not in crashes()[0][2], events)

    reset()
    fire(Conflict(POLL), at=0)
    fire(Conflict(POLL), at=5)
    fire(ValueError("in between"), at=6)
    check("another error in between is recorded and does not disturb the count",
          len(crashes()) == 1 and sf._error_count == 1, events)
    fire(Conflict(POLL), at=7)
    check("the third conflict still reports", len(crashes()) == 2
          and "Another copy" in crashes()[1][2], events)

    # -----------------------------------------------------------------------
    print("\n== each bot has its own count ==")
    # -----------------------------------------------------------------------
    # The combined edition loads one copy of this module per bot. A copy
    # loaded under another name must neither see nor feed this one's count.
    spec = importlib.util.spec_from_file_location("second_bots_shared_features", sf.__file__)
    other = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(other)
    other_events = []     # `time` is one module for both copies, so both read the fake clock
    logging.getLogger(other.__name__).addHandler(Captured())
    logging.getLogger(other.__name__).propagate = False
    other.set_event_hook(lambda level, kind, message, details=None: other_events.append((level, kind, message)))
    reset()
    other._conflict_times.clear()
    other._conflict_last = None
    other._conflict_alerted = False
    check("a second copy is a separate module with separate state",
          other is not sf and other._conflict_times is not sf._conflict_times)
    fire(Conflict(POLL), at=0)
    fire(Conflict(POLL), at=1)
    fire(Conflict(POLL), at=2, module=other)
    check("two here and one there are not three anywhere", events == [] and other_events == [],
          (events, other_events))
    fire(Conflict(POLL), at=3)
    check("the third here reports here", len(crashes()) == 1, events)
    check("and nowhere else", other_events == [], other_events)
    fire(Conflict(POLL), at=4, module=other)
    fire(Conflict(POLL), at=5, module=other)
    check("the other's own third reports there, even though this one already has",
          len([e for e in other_events if e[1] == "crash"]) == 1, other_events)
    check("and not here a second time", len(crashes()) == 1, events)

    # -----------------------------------------------------------------------
    print("\n== ManagerBot's own handler follows the same rule ==")
    # -----------------------------------------------------------------------
    # monitoring.py is a copy of the shared half, not the shared module, with
    # its own error_handler and record_error (test_shared_copies.py keeps
    # note_conflict itself identical). So it is driven the same way here.
    check("it has the same constants",
          (mon.CONFLICT_ALERT_COUNT, mon.CONFLICT_ALERT_WINDOW_S, mon.CONFLICT_QUIET_RESET_S) == (3, 120, 600),
          (mon.CONFLICT_ALERT_COUNT, mon.CONFLICT_ALERT_WINDOW_S, mon.CONFLICT_QUIET_RESET_S))
    check("and state of its own", mon._conflict_times is not sf._conflict_times)

    reset()
    fire(Conflict(f"{POLL} {SECRET}"), at=0, module=mon)
    check("one Conflict reaches nobody and is not an error",
          events == [] and mon._error_count == 0, events)
    check("it is a warning with numbers only",
          len(warnings_logged()) == 1 and SECRET not in warnings_logged()[0].getMessage()
          and "getUpdates request" not in warnings_logged()[0].getMessage(),
          [r.getMessage() for r in logs.records])
    check("and /status still says no errors", "No errors" in mon.error_summary(), mon.error_summary())
    fire(Conflict(POLL), at=50, module=mon)
    check("two are quiet too", events == [] and mon._error_count == 0, events)
    fire(Conflict(POLL), at=100, module=mon)
    got = crashes()
    check("the third is reported, once", len(events) == 1 and len(got) == 1, events)
    check("in the same words, in ManagerBot's own sentence",
          bool(got) and (got[0][0], got[0][1]) == ("error", "crash")
          and got[0][2].startswith("ManagerBot itself hit an unhandled Conflict: ")
          and "Another copy of this bot seems to be polling with the same token: "
              "3 conflicts within 120 seconds" in got[0][2], got[:1])
    check("counted as one error, which /status lists",
          mon._error_count == 1 and "Another copy of this bot" in mon.error_summary(), mon.error_summary())
    for at in (101, 102, 103, 500):
        fire(Conflict(POLL), at=at, module=mon)
    check("more straight after say nothing", len(events) == 1 and mon._error_count == 1, events)
    fire(Conflict(POLL), at=500 + 599, module=mon)
    fire(Conflict(POLL), at=500 + 600, module=mon)
    fire(Conflict(POLL), at=500 + 601, module=mon)
    check("599 s of quiet is not enough", len(events) == 1, events)
    last = 500 + 601
    fire(Conflict(POLL), at=last + 600, module=mon)
    fire(Conflict(POLL), at=last + 610, module=mon)
    check("after 600 s of quiet the count starts again: two are not three", len(events) == 1
          and len(mon._conflict_times) == 2, (len(events), list(mon._conflict_times)))
    fire(Conflict(POLL), at=last + 620, module=mon)
    check("and three more are reported, once more", len(crashes()) == 2, events)

    reset()
    for at in (0, 70, 140, 210):
        fire(Conflict(POLL), at=at, module=mon)
    check("spread over more than two minutes they are not", events == [] and mon._error_count == 0, events)

    reset()
    fire(ValueError("a plain bug"), at=0, module=mon)
    check("any other exception is recorded as before",
          len(crashes()) == 1 and crashes()[0][2] == "ManagerBot itself hit an unhandled ValueError: a plain bug"
          and mon._error_count == 1, events)
    reset()
    fire(Conflict(POLL), at=0, update=SomeUpdate(), module=mon)
    check("so is a Conflict with an update behind it",
          len(crashes()) == 1 and crashes()[0][2] == f"ManagerBot itself hit an unhandled Conflict: {POLL}"
          and len(mon._conflict_times) == 0, events)
    reset()
    fire(NetworkError("httpx.ReadError"), at=0, module=mon)
    check("and a network blip is still a blip", events == [] and mon._error_count == 0, events)

    reset()
    fire(Conflict(POLL), at=0, module=mon)
    fire(Conflict(POLL), at=1, module=mon)
    fire(Conflict(POLL), at=2)
    fire(Conflict(POLL), at=3)
    check("ManagerBot's count and a public bot's are separate: two and two are not three",
          events == [], events)
    fire(Conflict(POLL), at=4)
    check("the public bot's own third reports as the public bot's",
          len(crashes()) == 1 and "ManagerBot" not in crashes()[0][2], events)
    fire(Conflict(POLL), at=5, module=mon)
    check("and ManagerBot's own third still reports, as ManagerBot's",
          len(crashes()) == 2 and crashes()[1][2].startswith("ManagerBot itself hit"), events)
finally:
    sf.time.monotonic = real_monotonic
    for _module in (sf, mon):
        _module.set_event_hook(None)

print()
if failures:
    print(f"FAILURES ({len(failures)}):")
    for name in failures:
        print("  -", name)
    sys.exit(1)
print("one conflict is a warning; three in two minutes is a second copy, said once")
