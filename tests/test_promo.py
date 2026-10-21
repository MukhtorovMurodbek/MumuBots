"""The 220 ⚡ gift's state and claim (family_link). No database, no network.

    python tests/test_promo.py

One promotion, started once by the owner's /promo after ManagerBot says the family's unique users
reached 220. For 14 days the first message somebody sends ConvertBot or DownloaderBot gives them 220 ⚡
of bonus credit, once, to spend within 28 days of that day; /promo stop ends the claiming early. This
file holds the rules that decide whether a claim gives anything:

  * the window: closed before the start; open from the start to the end of the 14th day after it
    (midnight UTC), exactly; an early stop closes it at that moment, a stop after the end changes
    nothing;
  * the expiry: the end of the 28th day after the day of the claim (UTC), whatever the claim's time
    zone;
  * starting is possible once: a second start changes nothing, and the first start's moment stays;
  * a claim re-reads the start and the stop, so a cached "open" never gives after a stop, and a
    cached "not started" does not stop the first claim; ids that are not people never get it;
  * a failed database read: a claim, a start, a stop and every status read raise; only the cached
    check a bot makes on each message answers "closed", and tries again a minute later;
  * the milestone notice is claimed once;
  * the texts: the amount, the users, the dates as dd.mm.YYYY, "about N conversions", the bots by
    handle (SIBLING_BOTS) or plain name, and the ad's italic last paragraph.

The credit itself, against a real Postgres, is "the 220 gift" in tests/credit_scenarios.py. To see a
check catch breakage, copy shared/ into tests/.mirror/shared, break the copy and run this again: it
imports from the mirror when there is one.
"""
import logging
import os
import pathlib
import sys
from datetime import datetime, timedelta, timezone

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent


def source_dir(folder):
    mirrored = HERE / ".mirror" / folder
    return mirrored if mirrored.exists() else REPO / folder


sys.path.insert(0, str(source_dir("convert_bot")))
sys.path.insert(1, str(source_dir("shared")))
sys.path.insert(0, str(HERE / "stubs"))

os.environ.update(ENV_FILES="off", DATABASE_URL="postgresql://stub", CBOT_TOKEN="123:test",
                  CBOT_USERNAME="test", CBOT_ADMIN_ID="1")
os.environ.pop("SIBLING_BOTS", None)
logging.disable(logging.CRITICAL)

import family_link as fl  # noqa: E402

UTC = timezone.utc
failures = []
checks = 0


def check(name, ok, detail=""):
    global checks
    checks += 1
    print(("  ok  " if ok else "  FAIL  ") + name + (f" -- {detail}" if not ok and detail != "" else ""))
    if not ok:
        failures.append(name)


class Broken(Exception):
    pass


class Result:
    def __init__(self, rows):
        self.rows = rows

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return list(self.rows)


class FakeDB:
    """family.settings, answering exactly the statements this code sends. An
    unexpected statement fails loudly rather than being guessed at."""

    def __init__(self):
        self.rows, self.down, self.connects = {}, False, 0
        # Reads fail from the next write on: a database that drops right after
        # a commit, to see that a committed start or stop is still reported.
        self.reads_fail_after_write, self.reads_failing = False, False

    def connect(self):
        self.connects += 1
        if self.down:
            raise Broken("database down")
        return FakeConn(self)


class FakeConn:
    def __init__(self, fake):
        self.fake = fake

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def commit(self):
        pass

    def rollback(self):
        pass

    def execute(self, sql, params=()):
        rows, s = self.fake.rows, " ".join(sql.split())
        if s.startswith("SELECT") and self.fake.reads_failing:
            raise Broken("database down after the write")
        if s == ("INSERT INTO family.settings (key, value) VALUES (%s, %s) "
                 "ON CONFLICT (key) DO NOTHING RETURNING key"):
            key, value = params
            self.fake.reads_failing = self.fake.reads_fail_after_write
            if key in rows:
                return Result([])
            rows[key] = value
            return Result([(key,)])
        if s == "SELECT key, value FROM family.settings WHERE key IN (%s, %s)":
            return Result([(k, rows[k]) for k in params if k in rows])
        if s == "SELECT 1 FROM family.settings WHERE key = %s":
            return Result([(1,)] if params[0] in rows else [])
        if s == "SELECT value FROM family.settings WHERE key = %s":
            return Result([(rows[params[0]],)] if params[0] in rows else [])
        if s == "SELECT key FROM family.settings WHERE key LIKE %s":
            prefix = params[0].rstrip("%")
            return Result([(k,) for k in rows if k.startswith(prefix)])
        raise AssertionError(f"unexpected SQL: {s}")


FAKE = FakeDB()
fl._connect = FAKE.connect


def reset(started=None, stopped=None):
    """An empty family.settings and an empty cache, with the start and the
    stop as given."""
    FAKE.rows.clear()
    FAKE.down = False
    FAKE.reads_fail_after_write = FAKE.reads_failing = False
    fl._promo_cache.update(at=None, started=None, stopped=None)
    if started is not None:
        FAKE.rows[fl.PROMO_STARTED] = started.isoformat()
    if stopped is not None:
        FAKE.rows[fl.PROMO_STOPPED] = stopped.isoformat()


grants = []


def fake_grant(user_id, amount, key, expires_at, detail=None):
    """grant_bonus_once's contract: the first call per (key, user) gives, every
    later one returns None. Its database side is tested in credit_scenarios."""
    grants.append((user_id, amount, key, expires_at))
    claim = f"stars:granted:{key}:{user_id}"
    if claim in FAKE.rows:
        return None
    FAKE.rows[claim] = "now"
    return 1000 + amount


fl.grant_bonus_once = fake_grant

print("The constants are the owner's (2026-10-18)")
check("220 ⚡, at 220 users, 14 days to claim, 28 to spend",
      (fl.PROMO_CREDITS, fl.PROMO_USERS, fl.PROMO_CLAIM_DAYS, fl.PROMO_SPEND_DAYS) == (220, 220, 14, 28))
check("the amount is within what one gift may give", 0 < fl.PROMO_CREDITS <= fl.GIFT_MAX_CREDIT)
check("the key is a valid gift key", bool(fl.GIFT_KEY_RE.fullmatch(fl.PROMO_KEY)))

print("Before the start")
reset()
check("no window", fl.promo_window() is None)
check("not open", fl.promo_open() is False)
grants.clear()
check("a claim is closed", fl.claim_promo_gift(5001) == (fl.PROMO_CLOSED, None))
check("and nothing was granted", grants == [])

print("The window, at its edges")
start = datetime(2026, 11, 20, 15, 0, tzinfo=UTC)
reset(started=start)
started, closes = fl.promo_window()
check("it starts at the stored moment", started == start, started)
check("it closes at the end of the 14th day after the start (midnight UTC)",
      closes == datetime(2026, 12, 5, tzinfo=UTC), closes)
check("closed a second before the start", fl.promo_open(start - timedelta(seconds=1)) is False)
check("open at the start", fl.promo_open(start) is True)
check("open in the last second", fl.promo_open(datetime(2026, 12, 4, 23, 59, 59, tzinfo=UTC)) is True)
check("closed at midnight that ends it", fl.promo_open(datetime(2026, 12, 5, tzinfo=UTC)) is False)
late_start = datetime(2026, 11, 20, 23, 59, 59, tzinfo=UTC)
reset(started=late_start)
check("a start one second before midnight still closes at the end of that 14th day",
      fl.promo_window()[1] == datetime(2026, 12, 5, tzinfo=UTC), fl.promo_window()[1])
local_start = datetime(2026, 11, 21, 2, 0, tzinfo=timezone(timedelta(hours=5)))   # 20 Nov 21:00 UTC
reset(started=local_start)
check("a start stored with another time zone counts its UTC day",
      fl.promo_window()[1] == datetime(2026, 12, 5, tzinfo=UTC), fl.promo_window()[1])

print("A stop")
stop = datetime(2026, 11, 25, 10, 0, tzinfo=UTC)
reset(started=start, stopped=stop)
check("an early stop closes it at that moment", fl.promo_window()[1] == stop)
check("open a second before the stop", fl.promo_open(stop - timedelta(seconds=1)) is True)
check("closed at the stop", fl.promo_open(stop) is False)
reset(started=start, stopped=datetime(2026, 12, 9, tzinfo=UTC))
check("a stop after the end changes nothing", fl.promo_window()[1] == datetime(2026, 12, 5, tzinfo=UTC))

print("The expiry of a claim")
check("claimed 20 Nov 15:00 UTC: runs out at the end of 18 Dec",
      fl.promo_expiry(datetime(2026, 11, 20, 15, 0, tzinfo=UTC)) == datetime(2026, 12, 19, tzinfo=UTC))
check("claimed in the first second of 20 Nov: the same day's rule",
      fl.promo_expiry(datetime(2026, 11, 20, 0, 0, tzinfo=UTC)) == datetime(2026, 12, 19, tzinfo=UTC))
check("claimed in the last second of 20 Nov: the same",
      fl.promo_expiry(datetime(2026, 11, 20, 23, 59, 59, tzinfo=UTC)) == datetime(2026, 12, 19, tzinfo=UTC))
check("a claim time in another zone counts its UTC day",
      fl.promo_expiry(datetime(2026, 11, 21, 2, 0, tzinfo=timezone(timedelta(hours=5))))
      == datetime(2026, 12, 19, tzinfo=UTC))

print("Starting, once")
reset()
first, won = fl.start_promo()
check("the first start starts it", won is True)
check("now", abs((datetime.now(UTC) - first).total_seconds()) < 5, first)
again, won_again = fl.start_promo()
check("a second start does not", won_again is False)
check("and the first start's moment stays", again == first, (again, first))
check("it is open", fl.promo_open() is True)

print("Claims")
grants.clear()
outcome, expires = fl.claim_promo_gift(5001)
check("the first claim gives", outcome == fl.PROMO_GIVEN, outcome)
check("220 ⚡ under the gift's key", grants and grants[-1][1:3] == (220, fl.PROMO_KEY), grants)
check("expiring at the end of the 28th day after today",
      expires == fl.promo_expiry(datetime.now(UTC)) and grants[-1][3] == expires, expires)
check("a second claim by the same person: had it", fl.claim_promo_gift(5001) == (fl.PROMO_HAD, None))
check("another person gets it too", fl.claim_promo_gift(5002)[0] == fl.PROMO_GIVEN)
check("two people have it", fl.promo_claim_count() == 2, fl.promo_claim_count())
check("promo_given_to knows who", fl.promo_given_to(5001) is True and fl.promo_given_to(5003) is False)
before = len(grants)
for not_a_person in (0, -100123, 777000, 1087968824, 136817688):
    check(f"{not_a_person} never gets it", fl.claim_promo_gift(not_a_person) == (fl.PROMO_CLOSED, None))
check("and no grant was even tried for them", len(grants) == before)

print("A claim never trusts the cache")
reset()
fl.promo_open()                                   # caches "not started"
FAKE.rows[fl.PROMO_STARTED] = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
check("the cache still says not started", fl.promo_open() is False)
grants.clear()
check("but a claim reads the start afresh and gives",
      fl.claim_promo_gift(5010)[0] == fl.PROMO_GIVEN, grants)
check("the cache says open", fl.promo_open() is True)
FAKE.rows[fl.PROMO_STOPPED] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
check("the cache still says open after the stop was written", fl.promo_open() is True)
grants.clear()
check("but a claim reads the stop afresh and gives nothing",
      fl.claim_promo_gift(5011) == (fl.PROMO_CLOSED, None))
check("no grant was tried", grants == [])

print("Stopping")
reset(started=datetime.now(UTC) - timedelta(hours=1))
check("an open gift can be stopped", fl.stop_promo() is True)
check("then it is closed", fl.promo_open() is False)
check("a second stop does nothing", fl.stop_promo() is False)
reset()
check("a gift never started cannot be stopped", fl.stop_promo() is False)
check("and that wrote no stop", fl.PROMO_STOPPED not in FAKE.rows)
reset(started=datetime.now(UTC) - timedelta(days=30))
check("a gift already over cannot be stopped", fl.stop_promo() is False)

print("The milestone notice, once")
reset()
check("first: tell the owner", fl.note_promo_milestone() is True)
check("second: do not", fl.note_promo_milestone() is False)
check("and when it was reached is known", fl.promo_milestone_at() is not None)
reset()
check("not reached: None", fl.promo_milestone_at() is None)

print("A failed database read")
reset(started=datetime.now(UTC) - timedelta(hours=1))
FAKE.down = True
before = FAKE.connects
check("the cached check answers closed rather than raising", fl.promo_open() is False)
check("... and does not try again on the next message", fl.promo_open() is False and FAKE.connects == before + 1,
      FAKE.connects - before)
for name, call in (("a fresh check", lambda: fl.promo_open(fresh=True)),
                   ("a claim", lambda: fl.claim_promo_gift(5020)),
                   ("promo_given_to", lambda: fl.promo_given_to(5020)),
                   ("start_promo", lambda: fl.start_promo()),
                   ("stop_promo", lambda: fl.stop_promo()),
                   ("note_promo_milestone", lambda: fl.note_promo_milestone()),
                   ("promo_milestone_at", lambda: fl.promo_milestone_at()),
                   ("promo_claim_count", lambda: fl.promo_claim_count())):
    try:
        call()
        check(f"{name} raises", False, "it returned")
    except Broken:
        check(f"{name} raises", True)
FAKE.down = False
check("back up: still closed until the refresh", fl.promo_open() is False)
fl._promo_cache["at"] -= fl.PROMO_REFRESH_SECONDS
check("after the refresh: open again", fl.promo_open() is True)
check("nothing was started, stopped or noted while it was down",
      set(FAKE.rows) == {fl.PROMO_STARTED}, sorted(FAKE.rows))

print("A write that committed is reported, even if the read-back fails")
reset()
FAKE.reads_fail_after_write = True
try:
    when, won = fl.start_promo()
    check("start: reported as started", won is True and abs((datetime.now(UTC) - when).total_seconds()) < 5, when)
except Broken:
    check("start: reported as started", False, "it raised after the start was written")
check("... and it is written", fl.PROMO_STARTED in FAKE.rows)
reset(started=datetime.now(UTC) - timedelta(hours=1))
FAKE.reads_fail_after_write = True
try:
    check("stop: reported as stopped", fl.stop_promo() is True)
except Broken:
    check("stop: reported as stopped", False, "it raised after the stop was written")
check("... and it is written", fl.PROMO_STOPPED in FAKE.rows)

print("The general /gift cannot take this gift's key")
refusal = fl._gift_args([fl.PROMO_KEY, "10", "2099-01-01"])
check("refused, naming /promo", isinstance(refusal, str) and "/promo" in refusal, refusal)

print("The texts")
reset()
exp = datetime(2026, 12, 19, tzinfo=UTC)
notice = fl.promo_notice("en", exp)
check("the notice names the amount and the users", "220 ⚡" in notice and "220 users" in notice, notice)
check("the last day, as dd.mm.YYYY", "18.12.2026" in notice, notice)
check("about 22 conversions", "about 22 paid" in notice, notice)
check("the bots by plain name without SIBLING_BOTS", "ConvertBot" in notice and "DownloaderBot" in notice, notice)
check("and the balance command", "/balance" in notice)
os.environ["SIBLING_BOTS"] = "convertbot:ConvertBot:mumu_convert_bot,downloaderbot:DownloaderBot:@mumu_downloader_bot"
try:
    notice = fl.promo_notice("en", exp)
    check("the bots by handle with SIBLING_BOTS",
          "@mumu_convert_bot" in notice and "@mumu_downloader_bot" in notice, notice)
    ad = fl.promo_ad("en", datetime(2026, 12, 5, tzinfo=UTC))
    check("the ad: users and amount", "220 unique users" in ad and "220 ⚡" in ad, ad)
    check("the ad: the last day to claim", "04.12.2026" in ad, ad)
    check("the ad: how long it lasts", "28 days" in ad, ad)
    check("the ad: both bots", "@mumu_convert_bot" in ad and "@mumu_downloader_bot" in ad, ad)
    check("the ad: its last paragraph in italics", ad.rstrip().endswith("</i>") and ad.count("<i>") == 1, ad)
finally:
    os.environ.pop("SIBLING_BOTS", None)
check("no placeholder left in either", "{" not in fl.promo_notice("en", exp)
      and "{" not in fl.promo_ad("en", datetime(2026, 12, 5, tzinfo=UTC)))

print()
print(f"{checks - len(failures)}/{checks} checks passed")
if failures:
    print("FAILED: " + "; ".join(failures))
    sys.exit(1)
