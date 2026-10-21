"""The one-time gift: bonus credit for everyone, once each. No database, no network.

    python tests/test_gift.py

The owner gives every account some bonus credit when the family passes a milestone (the first is 100
users): `/gift <bot|all> <key> <credits> <last day>` in ManagerBot. Each bot counts the people it knows,
the owner presses Give it, and each bot then gives every one of them the credit once and tells them, in
their own language. Money going to everybody at once is the kind of command that can only be run once,
so this holds the parts that decide whether it was right. The credit itself, against a real Postgres, is
the "the gift" sections of tests/credit_scenarios.py; this file is everything around it:

  * _gift_args, the one parser the owner's command and the bots share: the key (3 to 40 lowercase
    letters, digits and dashes, none that could be a LIKE wildcard or part of a claim's name), the amount
    (1 to GIFT_MAX_CREDIT, ASCII digits only), the last day (a real date, from tomorrow on, not further
    than GIFT_MAX_DAYS), and the moment it turns into: the end of that day, UTC.
  * _cmd_gift, run on a fake database and a fake Telegram. Without --go it only counts: it grants
    nothing, sends nothing, and says what it would do and what each person would read. With --go it
    gives each person who is due the gift once (never ids that are not people, never twice for one
    id, never to whoever already has it), tells each WINNER in their own language and nobody else
    (not someone whose gift was already given by another bot, not someone whose grant failed), counts a
    send that fails as unreachable without losing the credit, and goes on past anyone who fails.
  * gift_notice: the amount, the last day as dd.mm.YYYY, "about N conversions" (amount // 10, at least
    1), and the bots as somebody can tap them (SIBLING_BOTS), with the plain names as the fallback.
  * the three languages of "gift_granted" in all four public bots: present, the same placeholders,
    and the English one equal to the fallback ManagerBot-less code carries.
  * ManagerBot's /gift and its buttons: wrong arguments queue nothing, a valid command queues ONE dry
    run per target bot (never with --go), Give it queues each again with --go, Cancel and an expired
    button queue nothing, and a second tap does nothing.

The clock is frozen at 2026-10-17 12:30 UTC, so the date rules are tested at their edges and not
against whatever day this runs. To see a check catch breakage, copy the bot into tests/.mirror, break the
copy and run this again: it imports from the mirror when there is one.
"""
import asyncio
import importlib.util
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


# ---------------------------------------------------------------------------
# a clock that stands still
# ---------------------------------------------------------------------------

NOW = datetime(2026, 10, 17, 12, 30, tzinfo=timezone.utc)
UTC = timezone.utc


class FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW if tz is not None else NOW.replace(tzinfo=None)


real_datetime, real_i18n = fl.datetime, fl._i18n
fl.datetime = FrozenDatetime


def day(k):
    """The date k days from the frozen today, as the owner would type it."""
    return (NOW + timedelta(days=k)).strftime("%Y-%m-%d")


def dmy(text):
    """2026-11-16 as the notice writes it: 16.11.2026."""
    y, m, d = text.split("-")
    return f"{d}.{m}.{y}"


def end_of(text):
    """The moment a gift whose last day is `text` runs out: midnight at the end of it, UTC."""
    y, m, d = (int(part) for part in text.split("-"))
    return datetime(y, m, d, tzinfo=UTC) + timedelta(days=1)


# ---------------------------------------------------------------------------
# _gift_args
# ---------------------------------------------------------------------------

print("\n== _gift_args ==")
args_of = lambda *a: fl._gift_args(list(a))      # noqa: E731

good = args_of("gift-100", "100", day(30))
check("a valid command is (key, amount, the moment it runs out, the day as written)",
      good == ("gift-100", 100, end_of(day(30)), day(30)), good)
check("... the moment carries its time zone, UTC", isinstance(good, tuple) and good[2].utcoffset() == timedelta(0), good)
check("... and is the END of the last day, not its start",
      args_of("g-1", "5", "2026-12-31")[2] == datetime(2027, 1, 1, tzinfo=UTC))
check("the amount and the day come back as written", good[1] == 100 and good[3] == day(30))

for count_args in ([], ["gift-100"], ["gift-100", "100"], ["gift-100", "100", day(30), "extra"],
                   ["gift-100", "100", day(30), "--go"]):
    got = fl._gift_args(count_args)
    check(f"{len(count_args)} argument(s){' ' + str(count_args[3:]) if count_args[3:] else ''}: the usage line, not a guess",
          isinstance(got, str) and got.startswith("Usage: gift <key> <credits> <last day YYYY-MM-DD>"), got)

print("\n-- the key")
for key, why in (("Gift-100", "an uppercase letter"), ("GIFT", "all uppercase"), ("gift_100", "an underscore"),
                 ("ab", "2 characters"), ("a" * 41, "41 characters"), ("-gift", "a leading dash"),
                 ("", "nothing"), ("gift:1", "a colon, which would cut the claim's name in two"),
                 ("gift%", "a LIKE wildcard"), ("gift 1", "a space"), ("gift.1", "a dot"),
                 ("gïft-1", "a letter that is not a-z"), ("гифт", "Cyrillic")):
    got = args_of(key, "100", day(30))
    check(f"a key with {why} is refused", isinstance(got, str) and "key" in got.lower(), got)
for key in ("abc", "a-b", "100-users", "0-0", "gift-100-users", "a" * 40, "x" * 3):
    got = args_of(key, "100", day(30))
    check(f"the key {key[:12]!r}{'...' if len(key) > 12 else ''} ({len(key)} characters) is accepted",
          isinstance(got, tuple) and got[0] == key, got)

print("\n-- the amount")
for amount, why in (("0", "nothing"), ("1001", "one more than the most"), ("abc", "a word"), ("²", "a superscript two"),
                    ("-5", "negative"), ("+5", "signed"), ("1e3", "scientific"), ("100.5", "a fraction"),
                    ("1,000", "a comma"), ("", "empty"), ("0x10", "hex"), ("٣", "an Arabic-Indic digit"),
                    ("１００", "full-width digits"), ("1" * 7, "seven digits"), ("999999", "six 9s")):
    try:
        got = args_of("gift-100", amount, day(30))
    except Exception as exc:
        check(f"an amount of {why} ({amount!r}) is refused and does not raise", False, repr(exc))
        continue
    check(f"an amount of {why} ({amount!r}) is refused", isinstance(got, str) and "amount" in got.lower(), got)
check("the refusal says the range, from the constant", "1 to %d" % fl.GIFT_MAX_CREDIT in args_of("gift-100", "0", day(30)))
for amount, want in (("1", 1), ("100", 100), ("1000", 1000), ("0100", 100), ("999", 999)):
    got = args_of("gift-100", amount, day(30))
    check(f"an amount of {amount!r} is accepted as {want}", isinstance(got, tuple) and got[1] == want, got)
check("1000 is the most, GIFT_MAX_CREDIT", fl.GIFT_MAX_CREDIT == 1000)

print("\n-- the last day")
for text, why in (("17.10.2026", "dd.mm.YYYY"), ("2026/10/20", "slashes"), ("tomorrow", "a word"), ("", "nothing"),
                  ("2026-13-01", "month 13"), ("2026-02-30", "a day that is not"), ("2026-10-1x", "a letter in it"),
                  ("20-10-2026", "the wrong order"), ("2026-10-18T00:00", "a time"), ("٢٠٢٦-١٠-٢٠", "Arabic-Indic digits")):
    got = args_of("gift-100", "100", text)
    check(f"a day written as {why} ({text!r}) is refused", isinstance(got, str) and "date" in got.lower(), got)
check("today is refused (the gift would last under a day)", isinstance(args_of("gift-100", "100", day(0)), str))
check("yesterday is refused", isinstance(args_of("gift-100", "100", day(-1)), str))
check("a year ago is refused", isinstance(args_of("gift-100", "100", day(-365)), str))
check("the refusal names the window, in the constant's words",
      "from tomorrow" in args_of("gift-100", "100", day(0)) and str(fl.GIFT_MAX_DAYS) in args_of("gift-100", "100", day(0)))
got = args_of("gift-100", "100", day(1))
check("tomorrow is accepted", isinstance(got, tuple) and got[2] == datetime(2026, 10, 19, tzinfo=UTC), got)
check("... it lasts a day and 11½ hours from the frozen 12:30", got[2] - NOW == timedelta(days=1, hours=11, minutes=30))
check("a month ahead is accepted", isinstance(args_of("gift-100", "100", day(30)), tuple))
check("399 days ahead is accepted", isinstance(args_of("gift-100", "100", day(399)), tuple))
check("401 days ahead is refused", isinstance(args_of("gift-100", "100", day(401)), str))
check("a decade ahead is refused", isinstance(args_of("gift-100", "100", day(3650)), str))
check("a last day of 31 December gives 1 January, 00:00, the next year",
      args_of("gift-100", "100", "2026-12-31")[2] == datetime(2027, 1, 1, tzinfo=UTC))
check("the end of February 2027 gives 1 March", args_of("gift-100", "100", "2027-02-28")[2] == datetime(2027, 3, 1, tzinfo=UTC))

real_now_clock = NOW
NOW = datetime(2026, 10, 17, 23, 59, 59, tzinfo=UTC)
check("one second before midnight, today is still refused", isinstance(args_of("gift-100", "100", day(0)), str))
check("... and tomorrow, which would last 24 hours and a second, is accepted", isinstance(args_of("gift-100", "100", "2026-10-18"), tuple))
NOW = datetime(2026, 10, 17, 0, 0, 1, tzinfo=UTC)
check("one second after midnight, tomorrow is accepted", isinstance(args_of("gift-100", "100", "2026-10-18"), tuple))
check("... and today is refused", isinstance(args_of("gift-100", "100", "2026-10-17"), str))
NOW = real_now_clock

check("_gift_last_day: junk is None, a date is its end", fl._gift_last_day("nope") is None
      and fl._gift_last_day("2026-10-20") == datetime(2026, 10, 21, tzinfo=UTC))


# ---------------------------------------------------------------------------
# _cmd_gift, on a fake database and a fake Telegram
# ---------------------------------------------------------------------------

class FakeI18n:
    """What a public bot's i18n.t would give, reduced to something that shows what it was asked for."""
    @staticmethod
    def t(lang, key, **kwargs):
        return f"<{lang}:{key}:" + ",".join(f"{k}={v}" for k, v in sorted(kwargs.items())) + ">"


class MissingI18n:
    """A translation file that has not caught up: t() returns the key itself."""
    @staticmethod
    def t(lang, key, **kwargs):
        return key


def run_gift(args, users=(), given=(), behave=None, langs=None, send_fails=(), no_list=False, list_raises=None,
             given_raises=None, i18n=FakeI18n, per_second=None):
    """Run _cmd_gift with everything it touches replaced. Returns what it said and what it did."""
    log = SimpleNamespace(out=None, extra=None, error=None, grants=[], sends=[], lookups=[], slept=[], already_asked=[])
    behave = behave or {}

    def list_all_users():
        if list_raises:
            raise list_raises
        return list(users)

    def get_user_language(user_id):
        log.lookups.append(user_id)
        return (langs or {}).get(user_id)

    fake_db = SimpleNamespace(get_user_language=get_user_language)
    if not no_list:
        fake_db.list_all_users = list_all_users

    def already_given(key):
        log.already_asked.append(key)
        if given_raises:
            raise given_raises
        return set(given)

    def grant(user_id, amount, key, expires_at, detail=None):
        log.grants.append((user_id, amount, key, expires_at))
        what = behave.get(user_id, "ok")
        if what == "raise":
            raise RuntimeError("the database said no")
        if what == "value":
            raise ValueError("a gift is out of bounds")
        if what == "none":
            return None
        return 1000 + user_id

    async def send_message(chat_id=None, text=None, **kwargs):
        log.sends.append((chat_id, text))
        if chat_id in send_fails:
            raise RuntimeError("Forbidden: bot was blocked by the user")

    async def fake_sleep(seconds, *a, **k):
        log.slept.append(seconds)

    saved = (fl.db, fl._gift_already_given, fl.grant_bonus_once, asyncio.sleep, fl._i18n, fl.BROADCAST_PER_SECOND)
    fl.db, fl._gift_already_given, fl.grant_bonus_once = fake_db, already_given, grant
    asyncio.sleep, fl._i18n = fake_sleep, (lambda: i18n)
    if per_second is not None:
        fl.BROADCAST_PER_SECOND = per_second
    context = SimpleNamespace(bot=SimpleNamespace(send_message=send_message))
    try:
        result = asyncio.run(fl._cmd_gift(context, list(args)))
    except Exception as exc:                                            # noqa: BLE001 -- the caller asserts on it
        log.error, result = exc, (None, None, None)
    finally:
        fl.db, fl._gift_already_given, fl.grant_bonus_once, asyncio.sleep, fl._i18n, fl.BROADCAST_PER_SECOND = saved
    log.out, *log.extra = result
    return log


KEY, AMOUNT, DAY = "gift-100", 100, day(30)
EXPIRES = end_of(DAY)
SAY = [KEY, str(AMOUNT), DAY]

print("\n== _cmd_gift: without --go it only counts ==")
CROWD = [11, 22, 33, 33, 0, -5, 1087968824, 136817688, 777000, 44, "55", None, 66.0, 22]
dry = run_gift(SAY, users=CROWD, given={22, 99}, i18n=None)
check("it answers with the text and nothing else (the shape every family command returns)",
      isinstance(dry.out, str) and dry.extra == [None, None], dry.extra)
check("it gave nobody anything", dry.grants == [], dry.grants)
check("it sent nobody anything", dry.sends == [], dry.sends)
check("it did not even look up anyone's language", dry.lookups == [], dry.lookups)
check("it did not sleep (nothing was paced, because nothing was sent)", dry.slept == [], dry.slept)
check("it asked who already has this gift, by its key", dry.already_asked == [KEY], dry.already_asked)
check("it counts the people: 11, 22, 33, 44 (once each; not 0, a negative, the three ids that are not people, "
      "a string, None or a float)", "This bot knows 4 people;" in dry.out, dry.out)
check("... three of them due (22 already has it), and one already has it", "3 would get it now (1 already have it" in dry.out, dry.out)
check("... and the credit that is: 3 x 100", "300 ⚡ in all" in dry.out, dry.out)
check("it says what the gift is: key, amount, last day, and when that ends",
      f"Gift '{KEY}': 100 ⚡ of bonus credit each, good until {DAY} (the end of that day, UTC)." in dry.out, dry.out)
check("it says plainly that nothing was given", "Nothing was given" in dry.out, dry.out)
check("it prints the notice they would read, in English, with the real numbers",
      fl.gift_notice("en", AMOUNT, EXPIRES) in dry.out and "We have passed 100 users" in dry.out
      and "100 ⚡ of free credit" in dry.out and f"good until {dmy(DAY)}." in dry.out, dry.out)
check("... no placeholder is left in it", "{" not in dry.out and "}" not in dry.out, dry.out)

some = run_gift(SAY, users=[1, 2, 3], given=set(), i18n=None)
check("everyone due: 3 would get it, 0 already have it", "3 would get it now (0 already have it" in some.out, some.out)
none_due = run_gift(SAY, users=[1, 2, 3], given={1, 2, 3, 4}, i18n=None)
check("everyone has it: 0 would get it, and 0 ⚡", "0 would get it now (3 already have it" in none_due.out and "0 ⚡ in all" in none_due.out, none_due.out)
empty = run_gift(SAY, users=[], i18n=None)
check("a bot that knows nobody counts zero and does not fail", "This bot knows 0 people; 0 would get it now" in empty.out, empty.out)
check("the sum follows the amount (7 x 25)", "175 ⚡ in all" in run_gift([KEY, "25", DAY], users=range(1, 8), i18n=None).out)
for place, argv in (("last", SAY + ["--go"]), ("first", ["--go"] + SAY)):
    check(f"--go anywhere ({place}) is a go, not part of the arguments", run_gift(argv, users=[1], i18n=None).grants != [])

print("\n== _cmd_gift: bad arguments give nobody anything, whatever else is typed ==")
for label, argv in (("no arguments", []), ("a key that is not allowed", ["Bad_Key", "100", DAY]), ("a negative amount", [KEY, "-5", DAY]),
                    ("too much", [KEY, "1001", DAY]), ("today", [KEY, "100", day(0)]), ("a bad date", [KEY, "100", "soon"]),
                    ("an extra word", [KEY, "100", DAY, "now"])):
    for go in (False, True):
        got = run_gift(argv + (["--go"] if go else []), users=[1, 2], i18n=None)
        check(f"{label}{' with --go' if go else ''}: the sentence that says what is wrong, nobody given or told",
              got.out == fl._gift_args([a for a in argv]) and got.grants == [] and got.sends == [] and got.lookups == [], (got.out, got.grants))
check("--go alone is the usage line", run_gift(["--go"], users=[1], i18n=None).out.startswith("Usage: gift"))

print("\n== _cmd_gift --go ==")
CROWD = [11, 22, 33, 44, 55, 66, 77, 22, 0, -1, 1087968824, 136817688, 777000, "88", None, 99.0]
go = run_gift(SAY + ["--go"], users=CROWD, given={11}, behave={33: "none", 44: "raise"}, send_fails={55},
              langs={22: "uz", 55: "ru", 66: None, 77: "en"})
due = [22, 33, 44, 55, 66, 77]
check("it gave each person who is due, once, in the order the bot knows them (11 already had it; 22 is listed twice)",
      [g[0] for g in go.grants] == due, [g[0] for g in go.grants])
check("... the amount, the key and the end of the last day, each time",
      all(g[1:] == (AMOUNT, KEY, EXPIRES) for g in go.grants), go.grants)
check("... and no id that is not a person was ever tried",
      not {0, -1, 1087968824, 136817688, 777000, "88", None, 99.0} & {g[0] for g in go.grants})
check("it told the people who got it, and only them: 22, 55 (who could not be reached), 66, 77",
      [chat for chat, _ in go.sends] == [22, 55, 66, 77], go.sends)
check("... not 33, whose gift another bot had already given", 33 not in [chat for chat, _ in go.sends])
check("... not 44, whose grant failed", 44 not in [chat for chat, _ in go.sends])
check("... not 11, who had it already", 11 not in [chat for chat, _ in go.sends])


def notice(lang):
    """What the fake translations give for this language, built the way the bots build it."""
    saved, fl._i18n = fl._i18n, (lambda: FakeI18n)
    try:
        return fl.gift_notice(lang, AMOUNT, EXPIRES)
    finally:
        fl._i18n = saved


check("each was told in their own language: Uzbek, Russian, nothing known = English, English",
      [text for _, text in go.sends] == [notice("uz"), notice("ru"), notice(None), notice("en")], go.sends)
check("... and those four really are different texts for different languages",
      go.sends[0][1].startswith("<uz:") and go.sends[1][1].startswith("<ru:") and go.sends[2][1].startswith("<en:"), go.sends)
check("their language was looked up for the people it told, and for nobody else", go.lookups == [22, 55, 66, 77], go.lookups)
check("the notice carries the amount, the last day and the bots",
      "amount=100" in go.sends[0][1] and f"date={dmy(DAY)}" in go.sends[0][1]
      and "conversions=10" in go.sends[0][1] and "convert=ConvertBot" in go.sends[0][1], go.sends[0][1])
check("it paused once after each person it gave to, at the rate it is told to send",
      go.slept == [1.0 / fl.BROADCAST_PER_SECOND] * 4, go.slept)
check("the summary counts: gave it to 4 (400 ⚡), told 3, one could not be reached but has the credit, two already had it "
      "(11, and 33 whom another bot got to first)",
      "Gave it to 4 people (400 ⚡) and told 3 of them; 1 could not be reached but have the credit. 2 already had it." in go.out, go.out)
check("... one failed, and it says to run the same command again",
      "1 failed (database errors, see the log); run the same command again to finish them." in go.out, go.out)
check("... and the head says which gift", go.out.startswith(f"Gift '{KEY}': 100 ⚡ of bonus credit each, good until {DAY}"), go.out)
check("it returns the shape every family command returns", go.extra == [None, None])

clean = run_gift(SAY + ["--go"], users=[5, 6, 7], i18n=FakeI18n)
check("nobody failing, nobody unreachable: no failure line and no unreachable clause",
      "failed" not in clean.out and "could not be reached" not in clean.out and "Gave it to 3 people (300 ⚡) and told 3 of them. 0 already had it." in clean.out,
      clean.out)
for kind in ("raise", "value"):
    broken = run_gift(SAY + ["--go"], users=[5, 6, 7], behave={5: kind, 6: kind, 7: kind})
    check(f"every grant failing with a {'RuntimeError' if kind == 'raise' else 'ValueError'}: all three tried, nobody told, none counted given",
          [g[0] for g in broken.grants] == [5, 6, 7] and broken.sends == [] and broken.lookups == []
          and "Gave it to 0 people (0 ⚡) and told 0 of them." in broken.out and "3 failed" in broken.out, broken.out)
lost = run_gift(SAY + ["--go"], users=[5, 6], behave={5: "none", 6: "none"})
check("two winners of the claim somewhere else: nobody told, both counted as already having it",
      lost.sends == [] and lost.lookups == [] and "Gave it to 0 people" in lost.out and "2 already had it" in lost.out and "failed" not in lost.out, lost.out)
first_fails = run_gift(SAY + ["--go"], users=[5, 6, 7], behave={5: "raise"})
check("the first person failing does not stop the rest", [g[0] for g in first_fails.grants] == [5, 6, 7]
      and [c for c, _ in first_fails.sends] == [6, 7], first_fails.sends)
last_unreachable = run_gift(SAY + ["--go"], users=[5, 6], send_fails={5, 6})
check("everybody unreachable still has the credit, and it says so",
      "Gave it to 2 people (200 ⚡) and told 0 of them; 2 could not be reached but have the credit." in last_unreachable.out
      and len(last_unreachable.grants) == 2, last_unreachable.out)
check("a send that fails is not retried (one attempt each)", len(last_unreachable.sends) == 2)
check("a bot with no pacing configured sends without pausing",
      run_gift(SAY + ["--go"], users=[5, 6], per_second=0).slept == [])
check("a run that gives nobody anything does not pause",
      run_gift(SAY + ["--go"], users=[5, 6], given={5, 6}).slept == [])
second = run_gift(SAY + ["--go"], users=CROWD, given={11, 22, 33, 55, 66, 77}, behave={44: "ok"})
check("run again after a failure: only the person who failed is tried, and told",
      [g[0] for g in second.grants] == [44] and [c for c, _ in second.sends] == [44], (second.grants, second.sends))

print("\n== _cmd_gift: what it cannot do, it says, and gives nothing ==")
unreadable = run_gift(SAY + ["--go"], users=[1, 2], list_raises=RuntimeError("no database"))
check("a user list that cannot be read: nothing given, and the error's kind is said",
      unreadable.out == "Could not read this bot's users (RuntimeError); nothing was given." and unreadable.grants == []
      and unreadable.sends == [], unreadable.out)
check("... the same without --go", run_gift(SAY, users=[1], list_raises=KeyError("x")).out
      == "Could not read this bot's users (KeyError); nothing was given.")
no_list = run_gift(SAY + ["--go"], no_list=True)
check("a bot that keeps no list of its users says so and gives nothing",
      no_list.out == "This bot keeps no list of its users, so it has nobody to give to." and no_list.grants == [], no_list.out)
blind = run_gift(SAY + ["--go"], users=[1, 2], given_raises=RuntimeError("claims unreadable"))
check("claims that cannot be read: the error goes out (the bus reports it), and nobody is given or told anything",
      isinstance(blind.error, RuntimeError) and blind.grants == [] and blind.sends == [], blind.error)
blind = run_gift(SAY, users=[1, 2], given_raises=RuntimeError("claims unreadable"))
check("... and a dry run that cannot read them does not guess a count", isinstance(blind.error, RuntimeError) and blind.out is None)


# ---------------------------------------------------------------------------
# gift_notice
# ---------------------------------------------------------------------------

print("\n== gift_notice ==")
fl._i18n = lambda: None
plain = fl.gift_notice("en", 100, end_of("2027-01-15"))
check("the fallback sentence, whole",
      plain == "\U0001f381 We have passed 100 users, thank you! You get 100 ⚡ of free credit, good until 15.01.2027. "
               "It pays for about 10 paid conversions in ConvertBot or big-file downloads in DownloaderBot. "
               "Your balance: /balance", plain)
check("a bot with no translations (ManagerBot) gets the same in any language", fl.gift_notice("uz", 100, end_of("2027-01-15")) == plain
      and fl.gift_notice(None, 100, end_of("2027-01-15")) == plain)


def conversions_in(amount):
    return int(re.search(r"about (\d+) paid conversions", fl.gift_notice("en", amount, end_of("2027-01-15"))).group(1))


for amount, want in ((1, 1), (9, 1), (10, 1), (19, 1), (20, 2), (99, 9), (100, 10), (250, 25), (1000, 100)):
    check(f"{amount} ⚡ pays for about {want} conversion(s): amount // 10, at least 1", conversions_in(amount) == want, conversions_in(amount))
check("the amount in the notice is the amount", "You get 250 ⚡" in fl.gift_notice("en", 250, end_of("2027-01-15")))
for last, shown in (("2027-01-15", "15.01.2027"), ("2026-12-31", "31.12.2026"), ("2028-02-29", "29.02.2028"),
                    ("2027-03-01", "01.03.2027"), ("2027-02-28", "28.02.2027")):
    check(f"the last day {last} reads {shown} (the day it runs out, not the midnight after it)",
          f"good until {shown}." in fl.gift_notice("en", 100, end_of(last)), fl.gift_notice("en", 100, end_of(last)))

real_siblings = os.environ.get("SIBLING_BOTS")
try:
    os.environ["SIBLING_BOTS"] = "stickerbot:StickerBot:MyStickerBot,convertbot:ConvertBot:MyConvertBot,downloaderbot:DownloaderBot:@MyDownloaderBot,anonbot:AnonBot:MyAnonBot"
    tapped = fl.gift_notice("en", 100, end_of("2027-01-15"))
    check("the bots are named as somebody can tap them: the @username from SIBLING_BOTS, with one @",
          "in @MyConvertBot or big-file downloads in @MyDownloaderBot." in tapped, tapped)
    os.environ["SIBLING_BOTS"] = "convertbot:ConvertBot:MyConvertBot"
    check("a deployment that lists only one: the other keeps its plain name",
          "in @MyConvertBot or big-file downloads in DownloaderBot." in fl.gift_notice("en", 100, end_of("2027-01-15")))
    os.environ["SIBLING_BOTS"] = "convertbot:ConvertBot:,downloaderbot:DownloaderBot:@"
    check("a username that is empty is no username: the plain name, never a bare @",
          "in ConvertBot or big-file downloads in DownloaderBot." in fl.gift_notice("en", 100, end_of("2027-01-15")))
    os.environ["SIBLING_BOTS"] = "convertbot:MyConvertBot,junk,downloaderbot:DownloaderBot:MyDownloaderBot:extra"
    check("entries that are not id:name:username are ignored",
          "in ConvertBot or big-file downloads in DownloaderBot." in fl.gift_notice("en", 100, end_of("2027-01-15")))
    check("_bot_handle: a bot that is not listed is the fallback", fl._bot_handle("nobot", "NoBot") == "NoBot")
finally:
    if real_siblings is None:
        os.environ.pop("SIBLING_BOTS", None)
    else:
        os.environ["SIBLING_BOTS"] = real_siblings

fl._i18n = lambda: FakeI18n
asked = fl.gift_notice("uz", 100, end_of("2027-01-15"))
check("a public bot asks its own translations for 'gift_granted', in the person's language, with all five values",
      asked == "<uz:gift_granted:amount=100,conversions=10,convert=ConvertBot,date=15.01.2027,download=DownloaderBot>", asked)
check("... no language known: English", fl.gift_notice(None, 100, end_of("2027-01-15")).startswith("<en:gift_granted:"))
fl._i18n = lambda: MissingI18n
check("a translation file that has not caught up with the key falls back to the English sentence, never to the bare key",
      fl.gift_notice("ru", 100, end_of("2027-01-15")) == plain)


# ---------------------------------------------------------------------------
# the translations
# ---------------------------------------------------------------------------

print("\n== gift_granted, in every public bot, in every language ==")
PLACEHOLDERS = {"amount", "conversions", "convert", "date", "download"}


def load_i18n(folder):
    path = source_dir(folder) / "i18n.py"
    spec = importlib.util.spec_from_file_location(f"gift_i18n_{folder}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


for folder in ("sticker_bot", "convert_bot", "downloader_bot", "anon_bot"):
    try:
        mod = load_i18n(folder)
    except Exception as exc:                                            # noqa: BLE001
        check(f"{folder}: i18n.py loads", False, repr(exc))
        continue
    for lang in ("en", "uz", "ru"):
        template = mod.STRINGS.get(lang, {}).get("gift_granted")
        check(f"{folder} {lang}: gift_granted is there", isinstance(template, str) and template.strip() != "", template)
        if not isinstance(template, str):
            continue
        check(f"{folder} {lang}: its placeholders are exactly {sorted(PLACEHOLDERS)}",
              set(re.findall(r"\{(\w+)\}", template)) == PLACEHOLDERS, re.findall(r"\{(\w+)\}", template))
        text = mod.t(lang, "gift_granted", amount=100, date="15.01.2027", conversions=10, convert="@C", download="@D")
        check(f"{folder} {lang}: it fills, says all five things and has no braces left, and ends on /balance",
              all(part in text for part in ("100", "15.01.2027", "10", "@C", "@D", "⚡")) and "{" not in text
              and "}" not in text and text.endswith("/balance"), text)
        check(f"{folder} {lang}: it opens with the gift emoji", text.startswith("\U0001f381"))
    en = mod.STRINGS.get("en", {}).get("gift_granted")
    check(f"{folder}: the English text is the fallback's text (one sentence, two places)", en == fl._PLAIN["gift_granted"], en)
    check(f"{folder}: Uzbek and Russian are not English", mod.STRINGS.get("uz", {}).get("gift_granted") not in (None, en)
          and mod.STRINGS.get("ru", {}).get("gift_granted") not in (None, en))
    fl._i18n = (lambda m=mod: m)
    check(f"{folder}: gift_notice speaks the person's language through it",
          fl.gift_notice("uz", 100, end_of("2027-01-15")) == mod.t("uz", "gift_granted", amount=100, date="15.01.2027", conversions=10,
                                                                      convert="ConvertBot", download="DownloaderBot")
          and fl.gift_notice("ru", 100, end_of("2027-01-15")) != fl.gift_notice("en", 100, end_of("2027-01-15"))
          and fl.gift_notice("de", 100, end_of("2027-01-15")) == fl.gift_notice("en", 100, end_of("2027-01-15")))
fl._i18n = lambda: None


# ---------------------------------------------------------------------------
# ManagerBot: /gift and its buttons
# ---------------------------------------------------------------------------

print("\n== ManagerBot: /gift ==")
queued, replies, edits, answers = [], [], [], []


class Message:
    async def reply_text(self, text, **kwargs):
        replies.append((text, kwargs))


class Query:
    def __init__(self, data):
        self.data = data
        self.message = SimpleNamespace(tag="the message with the buttons")

    async def answer(self, *a, **k):
        answers.append(self.data)


def queue_command(target_bot, command, args, requested_by, reply_chat_id):
    queued.append((target_bot, command, args, requested_by, reply_chat_id))
    return len(queued)


async def edit_in_place(message, bot_, text, **kwargs):
    edits.append(text)


def make_update(query=None):
    return SimpleNamespace(message=Message(), callback_query=query, effective_user=SimpleNamespace(id=7),
                           effective_chat=SimpleNamespace(id=70))


async def allowed(update, context):
    return True


async def refused(update, context):
    return False


real_queue, real_edit, real_guard = bot.db.queue_command, bot.edit_in_place, bot.guard
bot.db.queue_command, bot.edit_in_place, bot.guard = queue_command, edit_in_place, allowed


def fresh():
    queued.clear(), replies.clear(), edits.clear(), answers.clear()


def command(user_data, *args):
    context = SimpleNamespace(args=list(args), user_data=user_data, bot=SimpleNamespace())
    asyncio.run(bot.gift_command(make_update(), context))
    return context


def tap(user_data, data):
    query = Query(data)
    context = SimpleNamespace(args=[], user_data=user_data, bot=SimpleNamespace())
    asyncio.run(bot.gift_button(make_update(query), context))
    return query


def card(reply):
    """The buttons on a /gift reply, as (label, callback data)."""
    return [(b.args[0], b.kwargs["callback_data"]) for row in reply[1]["reply_markup"].inline_keyboard for b in row]


CHILD_IDS = [b["id"] for b in bot.CHILDREN]
check("ManagerBot knows the four public bots, and it is not one of them", len(CHILD_IDS) == 4 and bot.BOT_NAME not in CHILD_IDS, CHILD_IDS)

try:
    for argv, why in (([], "nothing"), (["all"], "a bot only"), (["all", KEY, "100"], "no day"), (["all", KEY, "100", DAY, "extra"], "an extra word"),
                      (["all", KEY, "100", DAY, "--go"], "--go typed by hand")):
        fresh()
        user_data = {}
        command(user_data, *argv)
        check(f"/gift with {why}: the usage line, nothing queued, nothing waiting",
              len(replies) == 1 and replies[0][0].startswith("Usage: /gift") and queued == [] and user_data == {}, (replies, queued))
    fresh()
    command({}, "all")
    check("... the usage line is HTML (its angle brackets are escaped) and shows an example", replies[0][1].get("parse_mode") is not None
          and "&lt;bot|all&gt;" in replies[0][0] and "<code>/gift all gift-100-users 100" in replies[0][0], replies)
    for argv, why in ((["nobot", KEY, "100", DAY], "an unknown bot"), (["managerbot", KEY, "100", DAY], "ManagerBot itself"),
                      (["manager", KEY, "100", DAY], "ManagerBot by a nickname")):
        fresh()
        user_data = {}
        command(user_data, *argv)
        check(f"/gift to {why}: 'No such bot', nothing queued, nothing waiting",
              len(replies) == 1 and replies[0][0].startswith(f"No such bot: {argv[0]}.") and queued == [] and user_data == {}, (replies, queued))
    for argv, why in (([ "all", "Bad_Key", "100", DAY], "a key that is not allowed"), (["all", KEY, "0", DAY], "no credits"),
                      (["all", KEY, "1001", DAY], "more than the most"), (["all", KEY, "abc", DAY], "an amount that is not a number"),
                      (["all", KEY, "100", day(0)], "today"), (["all", KEY, "100", day(401)], "too far ahead"),
                      (["all", KEY, "100", "2026-02-30"], "a day that is not")):
        fresh()
        user_data = {}
        command(user_data, *argv)
        check(f"/gift with {why}: it says what is wrong (the parser's own sentence), nothing queued, nothing waiting",
              len(replies) == 1 and replies[0][0] == fl._gift_args(argv[1:]) and queued == [] and user_data == {}, (replies, queued))

    print("\n-- a valid /gift to all")
    fresh()
    user_data = {}
    command(user_data, "all", KEY, str(AMOUNT), DAY)
    wanted_args = f"{KEY} {AMOUNT} {DAY}"
    check("one dry run is queued for each of the four bots, in the family's order, as the 'gift' command, for the owner's chat",
          queued == [(bid, "gift", wanted_args, 7, 70) for bid in CHILD_IDS], queued)
    check("... none of them with --go: asking must never give", all("--go" not in entry[2] for entry in queued), queued)
    check("... and the arguments are exactly key, amount, day as the bots will parse them",
          all(fl._gift_args(entry[2].split()) == (KEY, AMOUNT, EXPIRES, DAY) for entry in queued))
    nonce = user_data.get(bot.GIFT_PENDING, {}).get("nonce")
    check("what waits is the bots, the arguments without --go, and the card's nonce",
          user_data == {bot.GIFT_PENDING: {"bots": CHILD_IDS, "args": wanted_args, "nonce": nonce}}
          and isinstance(nonce, str) and re.fullmatch(r"[0-9a-f]{8}", nonce), user_data)
    text, kwargs = replies[0]
    check("the owner is shown the gift: amount, last day and key, in HTML", f"{AMOUNT} ⚡" in text and DAY in text and f"<code>{KEY}</code>" in text
          and kwargs.get("parse_mode") is not None, replies)
    check("... who gets it, and that nothing is given until Give it", "StickerBot" in text and "AnonBot" in text
          and "Nothing is given until you press" in text and "cannot be taken back" in text, text)
    buttons = card(replies[0])
    GO, CANCEL = f"gift:go:{nonce}", f"gift:cancel:{nonce}"
    check("two buttons: Give it and Cancel, each carrying this card's nonce", buttons == [("Give it", GO), ("Cancel", CANCEL)], buttons)
    check("... both reach gift_button's pattern, and fit Telegram's 64 bytes",
          all(re.search(r"^gift:", data) and len(data.encode()) <= 64 for _, data in buttons))

    print("\n-- Give it")
    fresh_len = len(queued)
    query = tap(user_data, GO)
    check("Give it queues each bot again, now with --go, once each, in the same order",
          queued[fresh_len:] == [(bid, "gift", wanted_args + " --go", 7, 70) for bid in CHILD_IDS], queued[fresh_len:])
    check("... exactly one --go in each", all(entry[2].count("--go") == 1 and entry[2].endswith(" --go") for entry in queued[fresh_len:]))
    check("... the dry runs before it are untouched", all("--go" not in entry[2] for entry in queued[:fresh_len]) and fresh_len == 4)
    check("... the tap was answered and the message rewritten to say it is giving, naming the amount and the key",
          answers == [GO] and len(edits) == 1 and edits[0].startswith(f"\U0001f381 Giving {AMOUNT} ⚡ (key {KEY}), through ")
          and all(bid in edits[0] for bid in CHILD_IDS), edits)
    check("... and nothing is waiting any more, the card remembered as done", user_data == {bot.GIFT_DONE: nonce}, user_data)
    count_before = len(queued)
    tap(user_data, GO)
    check("a second tap of Give it queues nothing", len(queued) == count_before, queued[count_before:])
    check("... and leaves the card saying it is giving (no 'no longer waiting' over it)", len(edits) == 1, edits)
    tap(user_data, CANCEL)
    check("... and so does a Cancel on the same card after it", len(queued) == count_before and len(edits) == 1, edits)

    print("\n-- Cancel, and a button that has expired")
    fresh()
    user_data = {}
    command(user_data, "all", KEY, str(AMOUNT), DAY)
    GO, CANCEL = [data for _, data in card(replies[-1])]
    dry_runs = len(queued)
    tap(user_data, CANCEL)
    check("Cancel queues nothing more and says nothing was given, naming the amount and the key",
          len(queued) == dry_runs and edits == [f"Cancelled — nothing was given ({AMOUNT} ⚡, key {KEY})."], (queued, edits))
    check("... and nothing is waiting", bot.GIFT_PENDING not in user_data, user_data)
    tap(user_data, GO)
    check("Give it on the cancelled card afterwards gives nothing and leaves it saying 'Cancelled'", len(queued) == dry_runs and len(edits) == 1, edits)
    fresh()
    tap({}, GO)
    check("Give it with nothing ever asked (a restart lost the memory): nothing queued, 'no longer waiting'",
          queued == [] and edits == ["That gift is no longer waiting. Send /gift again."] and answers == [GO], (queued, edits))
    fresh()
    user_data = {}
    command(user_data, "all", KEY, str(AMOUNT), DAY)
    nonce = user_data[bot.GIFT_PENDING]["nonce"]
    dry_runs = len(queued)
    tap(user_data, f"gift:something-else:{nonce}")
    check("a tap with this card's nonce that is not Give it is not Give it: nothing queued, and the gift is dropped",
          len(queued) == dry_runs and edits == [f"Cancelled — nothing was given ({AMOUNT} ⚡, key {KEY})."] and bot.GIFT_PENDING not in user_data, (queued, edits))

    print("\n-- one bot, a prefix, and a second /gift")
    fresh()
    user_data = {}
    command(user_data, "convert", KEY, "250", DAY)
    check("a name that picks one bot queues the dry run for that bot only",
          queued == [("convertbot", "gift", f"{KEY} 250 {DAY}", 7, 70)], queued)
    check("... and Give it follows it there only",
          (tap(user_data, card(replies[-1])[0][1]), queued[1:])[1] == [("convertbot", "gift", f"{KEY} 250 {DAY} --go", 7, 70)], queued)

    print("\n-- an older card, after a newer /gift")
    STALE = ("That card is not the one waiting (a newer /gift replaced it, or the card is from before an update). "
             "Nothing was given. Use the newest card, or send /gift again.")
    fresh()
    user_data = {}
    command(user_data, "all", "first-gift", "100", DAY)
    OLD_GO, OLD_CANCEL = [data for _, data in card(replies[-1])]
    command(user_data, "sticker", "second-gift", "40", day(5))
    NEW_GO, NEW_CANCEL = [data for _, data in card(replies[-1])]
    waiting = dict(user_data)
    check("each /gift's card has a nonce of its own", OLD_GO != NEW_GO and OLD_CANCEL != NEW_CANCEL
          and waiting[bot.GIFT_PENDING]["args"] == f"second-gift 40 {day(5)}", (OLD_GO, NEW_GO))
    dry_runs = len(queued)
    tap(user_data, OLD_GO)
    check("Give it on the older card gives nothing at all -- not the newer gift (the review's S1)",
          queued[dry_runs:] == [], queued[dry_runs:])
    check("... says that card is no longer the one waiting, and the newer gift still waits, untouched",
          edits == [STALE] and user_data == waiting, (edits, user_data))
    tap(user_data, OLD_CANCEL)
    check("Cancel on the older card does not cancel the newer gift", queued[dry_runs:] == [] and edits[-1] == STALE
          and user_data == waiting, (edits, user_data))
    tap(user_data, "gift:go")
    check("a card from before the nonce (plain gift:go) gives nothing and leaves the newer gift waiting",
          queued[dry_runs:] == [] and edits[-1] == STALE and user_data == waiting, (edits, user_data))
    tap(user_data, "gift:go:")
    check("... nor does an empty nonce", queued[dry_runs:] == [] and edits[-1] == STALE and user_data == waiting)
    tap(user_data, NEW_GO)
    check("the newer card's Give it then gives the newer gift, only",
          queued[dry_runs:] == [("stickerbot", "gift", f"second-gift 40 {day(5)} --go", 7, 70)], queued[dry_runs:])
    count_before = len(queued)
    tap(user_data, OLD_GO)
    check("... and the older card after that still gives nothing ('no longer waiting')",
          len(queued) == count_before and edits[-1] == "That gift is no longer waiting. Send /gift again.", edits)
    fresh()
    user_data = {}
    waiting_before = {bot.GIFT_PENDING: {"bots": CHILD_IDS, "args": f"{KEY} {AMOUNT} {DAY}"}}
    user_data.update(waiting_before)
    tap(user_data, "gift:go")
    check("a gift left waiting by the version before the nonce cannot be given by any tap",
          queued == [] and edits == [STALE] and user_data == waiting_before, (queued, edits))

    print("\n-- a bot that cannot be asked")
    for broken, why in ((["convertbot"], "one of four"), (CHILD_IDS, "all four")):
        fresh()
        user_data = {}
        command(user_data, "all", KEY, str(AMOUNT), DAY)
        GO = card(replies[-1])[0][1]
        dry_runs = len(queued)

        def flaky_queue(target_bot, command_, args, requested_by, reply_chat_id, broken=broken):
            if target_bot in broken and args.endswith(" --go"):
                raise RuntimeError("the database is away")
            return queue_command(target_bot, command_, args, requested_by, reply_chat_id)

        bot.db.queue_command = flaky_queue
        try:
            tap(user_data, GO)
        finally:
            bot.db.queue_command = queue_command
        asked = [entry[0] for entry in queued[dry_runs:]]
        check(f"queueing fails for {why}: every other bot is still asked, once",
              asked == [bid for bid in CHILD_IDS if bid not in broken], asked)
        check("... and the card names the ones that could not be asked, and says sending it again is safe",
              len(edits) == 1 and f"Could not ask {', '.join(broken)}" in edits[0] and "nobody gets it twice" in edits[0], edits)
        check("... saying 'Giving' only if some bot was asked",
              edits[0].startswith("\U0001f381 Giving") == bool(asked) and ("Nothing was given" in edits[0]) == (not asked), edits)

    print("\n-- somebody who is not the owner")
    fresh()
    bot.guard = refused
    user_data = {}
    command(user_data, "all", KEY, str(AMOUNT), DAY)
    check("a stranger's /gift queues nothing and leaves nothing waiting", queued == [] and user_data == {})
    bot.guard = allowed
    command(user_data, "all", KEY, str(AMOUNT), DAY)
    GO = card(replies[-1])[0][1]
    waiting = dict(user_data)
    dry_runs = len(queued)
    bot.guard = refused
    tap(user_data, GO)
    check("a stranger's Give it queues nothing, rewrites nothing, and does not use up the owner's gift",
          len(queued) == dry_runs and edits == [] and user_data == waiting, (queued, edits))
    bot.guard = allowed
finally:
    bot.db.queue_command, bot.edit_in_place, bot.guard = real_queue, real_edit, real_guard

print("\n== the command is wired in ==")
source = (SRC / "bot.py").read_text(encoding="utf-8")
check("/gift is registered to gift_command", 'CommandHandler("gift", gift_command)' in source)
check("the buttons are registered to gift_button, under their own prefix", 'CallbackQueryHandler(gift_button, pattern=r"^gift:")' in source)
patterns = re.findall(r'CallbackQueryHandler\((\w+), pattern=r"([^"]+)"\)', source)
for data in ("gift:go:0123abcd", "gift:cancel:0123abcd"):
    check(f"{data} reaches gift_button and no other button handler",
          [fn for fn, pattern in patterns if re.search(pattern, data)] == ["gift_button"], [fn for fn, pattern in patterns if re.search(pattern, data)])
check("/gift is in the owner's menu, with a description that fits the popup",
      any(c.args[0] == "gift" and 0 < len(c.args[1]) < 51 for c in bot.BOT_COMMANDS), [c.args for c in bot.BOT_COMMANDS if c.args[0] == "gift"])
check("/gift is in /help, with its arguments, and /help still fills in", "/gift &lt;bot|all&gt; &lt;key&gt; &lt;credits&gt; &lt;last day&gt;" in bot.HELP
      and "/gift" in bot.HELP.format(bots=bot.bot_list_hint()))
check("the bots' side: gift is a family command, with its help line",
      fl.COMMANDS.get("gift") is fl._cmd_gift and "--go" in fl.COMMAND_HELP.get("gift", "") and "YYYY-MM-DD" in fl.COMMAND_HELP.get("gift", ""))
check("ManagerBot knows which version brought it", bot.COMMAND_SINCE.get("gift") == "2.2.0")
gift_src = (REPO / "shared" / "family_link.py").read_text(encoding="utf-8")
gift_src = gift_src[gift_src.index("async def _cmd_gift"):gift_src.index("async def _cmd_pause")]
check("no log line of the gift names a person (the key and the kind of failure only)",
      gift_src.count("logger.") >= 2 and not re.search(r"logger\.\w+\((?:[^()]|\([^()]*\))*\buid\b", gift_src),
      re.findall(r"logger\.\w+\(.*", gift_src))
check("... and tells the owner about a bot older than that, and lets one that has it through",
      bot._too_old_for("2.1.0", "gift") is not None and bot._too_old_for("2.2.0", "gift") is None and bot._too_old_for(None, "gift") is None)

fl.datetime, fl._i18n = real_datetime, real_i18n

print()
if failures:
    print(f"FAILED ({len(failures)} of {checks}):")
    for name in failures:
        print("  -", name)
    sys.exit(1)
print(f"{checks} checks. The gift counts before it gives, gives each person once, and tells only the people it gave to.")
