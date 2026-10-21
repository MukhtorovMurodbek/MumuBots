"""The credit balance, against a real Postgres.

`family_link.py` holds the family's only money arithmetic: what a paid Star
is worth, how much of that is bonus and when the bonus runs out, what a job
costs, and what an owner's correction is allowed to touch. It is the code in
this repository where being approximately right is worst, so it is asserted
rather than reasoned about.

What this covers, and why each one is here rather than assumed:

  the ladder           -- a person's first 500 Stars ever earn 3x the
                          ordinary rate and the next 500 earn 2x, counted
                          across every payment, so paying in pieces earns
                          exactly what paying at once does
  two payments at once -- cannot both be priced as somebody's first 500
  bonus expiry         -- only the bonus expires, never ordinary credit, and
                          housekeeping does it without anybody reading a
                          balance
  spending             -- uses bonus first, so expiry only takes bonus nobody
                          needed; refuses atomically when the balance will
                          not cover it
  corrections          -- take the bonus down with the balance, so a later
                          expiry cannot take credit that is no longer there,
                          and never change what somebody appears to have paid
  one payment, once    -- a payment Telegram delivers twice (the same charge id) is
                          credited once, even when both deliveries arrive at the same
                          moment, and a failure between the claim and the credit leaves
                          no claim behind
  payments are final   -- there is no function that takes a payment back
  the gift             -- the owner's one-time bonus credit for everyone: given once per
                          person however many bots or threads ask, bonus and not ordinary
                          credit (spent first, and taken back when it runs out), one claim
                          with grant_stars_once, refused when it is out of bounds without
                          writing anything, and never half done when the database fails
  the ledger           -- adds up from zero to the balance, row by row

Run from the repo root:  python tests/credit_scenarios.py
Needs Postgres (TEST_DATABASE_URL, or the local default). No network, and it
uses a scratch schema of its own, never a bot's.
"""
import os
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

os.environ.setdefault("DATABASE_URL", os.environ.get(
    "TEST_DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/botfamily_test"))
os.environ["DB_SCHEMA"] = "credit_scenarios"
os.environ.setdefault("CBOT_TOKEN", "1:AA")
os.environ.setdefault("CBOT_USERNAME", "x")
# Fixed here rather than taken from the environment: these assertions are
# about the arithmetic, and a suite whose expected numbers move with a
# deployment's pricing is a suite that tells you nothing.
os.environ["FAMILY_TOPUP_MULTIPLIER"] = "2"
os.environ["FAMILY_TOPUP_LADDER"] = "500:3,500:2"
os.environ["FAMILY_BONUS_EXPIRY_DAYS"] = "90"

# family_link imports db by bare name, exactly as a bot does, so a bot's
# folder has to be the import root. ConvertBot's, because it is the bot that
# charges for things.
os.chdir(ROOT / "convert_bot")
sys.path.insert(0, str(ROOT / "convert_bot"))
sys.path.insert(1, str(ROOT / "shared"))

import db  # noqa: E402
import family_link as fl  # noqa: E402
import psycopg  # noqa: E402

USER, OTHER, RACER = 909001, 909002, 909003
GIFTED, GIFT_RACE, GIFT_FAIL = 909005, 909006, 909007
USERS = [USER, OTHER, RACER, GIFTED, GIFT_RACE, GIFT_FAIL]
DUP = 909004                      # the wallet the double-delivery checks use (kept out of USERS: the overview counts wallets)

failures: list[str] = []
_checks = 0


def check(label: str, got, want) -> None:
    global _checks
    _checks += 1
    if got == want:
        print(f"  ok   {label}")
    else:
        failures.append(label)
        print(f"  FAIL {label}\n       got {got!r}, wanted {want!r}")


def ok(label: str, condition: bool, detail: str = "") -> None:
    global _checks
    _checks += 1
    if condition:
        print(f"  ok   {label}")
    else:
        failures.append(label)
        print(f"  FAIL {label}\n       {detail}")


def scrub() -> None:
    """The balance tables are shared family ones, so this cannot drop a
    schema to start clean -- it removes its own user ids and its own grant
    claims, and nothing else."""
    with db.pooled() as conn:
        conn.execute("DELETE FROM family.star_bonus_lots WHERE user_id = ANY(%s)", (USERS + [DUP],))
        conn.execute("DELETE FROM family.star_ledger WHERE user_id = ANY(%s)", (USERS + [DUP],))
        conn.execute("DELETE FROM family.star_balances WHERE user_id = ANY(%s)", (USERS + [DUP],))
        conn.execute("DELETE FROM family.settings WHERE key LIKE 't909-%%' OR key LIKE 'stars:paid:t909-%%'")
        for user_id in USERS:
            conn.execute("DELETE FROM family.settings WHERE key LIKE %s",
                         (f"stars:granted:%:{user_id}",))
        conn.commit()


def run_out(user_id: int) -> None:
    """Make every bonus this person still holds due, as if 90 days had gone."""
    with db.pooled() as conn:
        conn.execute("UPDATE family.star_bonus_lots SET expires_at = now() - interval '1 second' "
                     "WHERE user_id = %s AND remaining > 0", (user_id,))
        conn.commit()


def raw_balance(user_id: int) -> int:
    """The stored figure, read without star_balance's own expiry pass."""
    with db.pooled() as conn:
        row = conn.execute("SELECT balance FROM family.star_balances WHERE user_id = %s",
                           (user_id,)).fetchone()
    return int(row[0]) if row else 0


def main() -> int:
    db.init_db()
    fl.init_family_schema()
    scrub()

    print("\n== an empty wallet ==")
    check("an unknown wallet reads as zero", fl.star_balance(USER), 0)
    check("and its totals are all zero", fl.star_totals(USER),
          {"balance": 0, "topped_up": 0, "spent": 0, "stars_paid": 0,
           "bonus": 0, "bonus_next_amount": 0, "bonus_next_expiry": None})

    print("\n== the ladder, as arithmetic ==")
    check("the ordinary rate lives in one function", fl.credit_for_stars(100), 200)
    check("somebody's first Stars earn three times it",
          fl.quote_credit(0, 100), {"stars": 100, "base": 200, "bonus": 400, "total": 600})
    check("the next 500 earn twice it", fl.quote_credit(500, 100)["total"], 400)
    check("and after 1,000 Stars there is no bonus at all",
          fl.quote_credit(1000, 100), {"stars": 100, "base": 200, "bonus": 0, "total": 200})
    # 200 Stars left at 3x, then 200 at 2x: 1200 + 800.
    check("a payment across a step is priced piece by piece",
          fl.quote_credit(300, 400), {"stars": 400, "base": 800, "bonus": 1200, "total": 2000})
    check("and across all three at once", fl.quote_credit(0, 1500)["total"], 3000 + 2000 + 1000)
    check("somebody new is at 3x with 500 to go", fl.ladder_position(0), (3.0, 500))
    check("one Star before the step", fl.ladder_position(499), (3.0, 1))
    check("on the step", fl.ladder_position(500), (2.0, 500))
    check("past the ladder", fl.ladder_position(1000), (1.0, None))
    for piece in (1, 7, 100, 333):
        paid = earned = 0
        while paid < 1500:
            step = min(piece, 1500 - paid)
            earned += fl.quote_credit(paid, step)["total"]
            paid += step
        check(f"paying {piece} at a time earns what paying 1,500 at once does",
              earned, fl.quote_credit(0, 1500)["total"])

    print("\n== paying ==")
    first = fl.topup(USER, 100, "first payment")
    check("a first payment credits the ordinary part", first["credited"], 200)
    check("and a bonus on top, at 3x", first["bonus"], 400)
    check("leaving the balance at both together", first["balance"], 600)
    days = (first["bonus_expires"] - datetime.now(timezone.utc)).total_seconds() / 86400
    # The expiry comes from the database's clock and `days` from this one's;
    # a database a few seconds ahead (Docker's VM often is) read as just over
    # 90 and failed now and then. A quarter of an hour either way is still "90 days".
    ok("the bonus is dated to run out in 90 days", 89.99 < days < 90.01, f"{days:.4f} days")
    check("the quote shown before paying agrees with what paying does",
          fl.quote_topup(USER, 450), fl.quote_credit(100, 450))
    second = fl.topup(USER, 450, "second payment")
    # 400 Stars left at 3x (2400), then 50 at 2x (200).
    check("a second payment carries on up the ladder",
          (second["credited"], second["bonus"]), (900, 1700))
    check("from where the first one left off", second["lifetime_before"], 100)
    check("balances accumulate", second["balance"], 3200)
    third = fl.topup(USER, 500, "third payment")
    # 450 left at 2x (1800), then 50 at the ordinary rate (100).
    check("a payment across the end of the ladder",
          (third["credited"], third["bonus"]), (1000, 900))
    fourth = fl.topup(USER, 10, "past the ladder")
    check("past it, a payment earns no bonus", fourth["bonus"], 0)
    check("and has nothing to expire", fourth["bonus_expires"], None)
    totals = fl.star_totals(USER)
    check("real money is counted apart from credit", totals["stars_paid"], 1060)
    check("credit counts the bonus as credit", totals["topped_up"], 600 + 2600 + 1900 + 20)
    check("and knows how much of the balance is bonus", totals["bonus"], 400 + 1700 + 900)
    check("the next bonus to run out is the oldest", totals["bonus_next_amount"], 400)

    print("\n== two payments at the same moment ==")
    results: list = []
    racers = [threading.Thread(target=lambda: results.append(fl.topup(RACER, 500, "racing")))
              for _ in range(2)]
    for racer in racers:
        racer.start()
    for racer in racers:
        racer.join()
    check("both went through", len(results), 2)
    check("and only one was priced as somebody's first 500",
          sorted(result["bonus"] for result in results), [1000, 2000])
    check("so together they earned what 1,000 at once does",
          fl.star_balance(RACER), fl.quote_credit(0, 1000)["total"])

    print("\n== spending, and what expiry takes ==")
    fl.topup(OTHER, 100, "first payment")  # 200 ordinary + 400 bonus
    check("a free job is not a movement", fl.spend_stars(OTHER, 0, "spend"), 600)
    check("spending what is there succeeds",
          fl.spend_stars(OTHER, 150, "spend", "3 MB jpg->pdf"), 450)
    check("and it came out of the bonus first", fl.star_totals(OTHER)["bonus"], 250)
    check("spending more than there is refuses", fl.spend_stars(OTHER, 1000, "spend"), None)
    check("and a refusal takes nothing", fl.star_balance(OTHER), 450)
    check("lifetime spent counts only real spends", fl.star_totals(OTHER)["spent"], 150)
    run_out(OTHER)
    check("when the bonus runs out, only the unspent bonus goes", fl.star_balance(OTHER), 200)
    newest = fl.star_ledger_for(OTHER, 1)[0]
    check("and the ledger says it expired", (newest["reason"], newest["delta"]), ("expired", -250))
    check("no bonus is left", fl.star_totals(OTHER)["bonus"], 0)
    check("and nothing expires twice", fl.star_balance(OTHER), 200)

    fl.topup(OTHER, 50, "a later payment")  # lifetime 100, still 3x: 100 + 200
    check("a later payment still earns the first step's rate", raw_balance(OTHER), 500)
    run_out(OTHER)
    ok("housekeeping expires bonus without anybody reading a balance",
       fl.expire_bonus_credit() >= 200)
    check("and it took exactly the bonus", raw_balance(OTHER), 300)

    fl.topup(OTHER, 10, "small")  # lifetime 150: 20 ordinary + 40 bonus
    check("after a small payment", fl.star_balance(OTHER), 360)
    check("a spend larger than the bonus", fl.spend_stars(OTHER, 100, "spend"), 260)
    run_out(OTHER)
    check("leaves nothing for expiry to take", fl.star_balance(OTHER), 260)

    print("\n== corrections ==")
    check("taking credit away works on the whole balance",
          fl.move_stars(USER, -5000, "adjustment", "by admin 1"), 120)
    check("and takes the bonus down with it, so expiry cannot take more than is left",
          fl.star_totals(USER)["bonus"], 120)
    check("setting a balance lands exactly there", fl.set_star_balance(USER, 50, "adjustment"), 50)
    check("and caps the bonus the same way", fl.star_totals(USER)["bonus"], 50)
    run_out(USER)
    check("so when that runs out the balance lands on zero, not below", fl.star_balance(USER), 0)
    check("credit given by hand is ordinary credit and never expires",
          (fl.move_stars(USER, 250, "adjustment", "by admin 1"), fl.star_totals(USER)["bonus"]),
          (250, 0))
    check("a correction does not change what they appear to have paid",
          fl.star_totals(USER)["stars_paid"], 1060)
    check("a balance can be taken below zero", fl.move_stars(USER, -400, "adjustment"), -150)
    check("and a negative balance cannot be spent from", fl.spend_stars(USER, 1, "spend"), None)
    check("a conversion's own charge can still come back to the balance",
          fl.move_stars(USER, 150, "refund", "job failed"), 0)
    ok("but nothing takes a payment back: payments are final",
       not hasattr(fl, "refund_topup"), "family_link.refund_topup exists again")

    print("\n== the owner's own levers ==")
    check("a grant lands once", fl.grant_stars_once(OTHER, 500, "test-grant"), 760)
    check("and never a second time", fl.grant_stars_once(OTHER, 500, "test-grant"), None)
    check("and it is not bonus", fl.star_totals(OTHER)["bonus"], 0)
    check("setting another balance lands exactly there",
          fl.set_star_balance(OTHER, 10000, "adjustment"), 10000)
    check("setting it to the same number again changes nothing",
          fl.set_star_balance(OTHER, 10000, "adjustment"), 10000)

    print("\n== the gift ==")
    GIFT_KEY = "gift-t909"
    soon = (datetime.now(timezone.utc) + timedelta(days=30)).replace(microsecond=0)

    def count(sql: str, *params) -> int:
        with db.pooled() as conn:
            return int(conn.execute(sql, params).fetchone()[0])

    def claim_rows(key: str, user_id: int) -> int:
        return count("SELECT count(*) FROM family.settings WHERE key = %s", f"stars:granted:{key}:{user_id}")

    def wallet_rows(user_id: int) -> int:
        return count("SELECT count(*) FROM family.star_balances WHERE user_id = %s", user_id)

    def lot_rows(user_id: int) -> int:
        return count("SELECT count(*) FROM family.star_bonus_lots WHERE user_id = %s", user_id)

    def ledger_rows_of(user_id: int) -> int:
        return count("SELECT count(*) FROM family.star_ledger WHERE user_id = %s", user_id)

    check("nobody has a wallet before the gift, which is most people", wallet_rows(GIFTED), 0)
    check("a first grant returns the new balance", fl.grant_bonus_once(GIFTED, 100, GIFT_KEY, soon), 100)
    check("a second returns None", fl.grant_bonus_once(GIFTED, 100, GIFT_KEY, soon), None)
    check("... and a third, from another bot's process, the same", fl.grant_bonus_once(GIFTED, 100, GIFT_KEY, soon), None)
    check("the balance rose once", fl.star_balance(GIFTED), 100)
    check("there is one claim, one lot and one ledger row",
          (claim_rows(GIFT_KEY, GIFTED), lot_rows(GIFTED), ledger_rows_of(GIFTED)), (1, 1, 1))

    totals = fl.star_totals(GIFTED)
    check("the gift is bonus: all of the balance is bonus", totals["bonus"], 100)
    check("... in one lot of the gift's size", totals["bonus_next_amount"], 100)
    check("... that runs out when the gift does", totals["bonus_next_expiry"], soon)
    check("it is credit, not money: nothing counts as paid", (totals["topped_up"], totals["stars_paid"]), (100, 0))
    newest = fl.star_ledger_for(GIFTED, 1)[0]
    check("the ledger row is a grant, for the whole gift",
          (newest["reason"], newest["delta"], newest["balance_after"]), ("grant", 100, 100))
    ok("... that says which gift it was and when it ends",
       GIFT_KEY in (newest["detail"] or "") and f"{soon:%Y-%m-%d}" in (newest["detail"] or ""), str(newest["detail"]))

    check("the key is the one grant_stars_once uses: it finds the gift given and credits nothing",
          fl.grant_stars_once(GIFTED, 500, GIFT_KEY), None)
    check("... and the balance did not move", fl.star_balance(GIFTED), 100)

    check("ordinary credit given later is not bonus",
          (fl.move_stars(GIFTED, 50, "adjustment", "by admin 1"), fl.star_totals(GIFTED)["bonus"]), (150, 100))
    check("spending takes the gift before the ordinary credit", fl.spend_stars(GIFTED, 60, "spend", "gift first"), 90)
    check("... 40 of the gift is left", fl.star_totals(GIFTED)["bonus"], 40)
    check("... and the 50 ordinary are all still there",
          fl.star_totals(GIFTED)["balance"] - fl.star_totals(GIFTED)["bonus"], 50)
    run_out(GIFTED)
    check("when the gift runs out only the unspent gift goes", fl.star_balance(GIFTED), 50)
    newest = fl.star_ledger_for(GIFTED, 1)[0]
    check("... and the ledger says it expired", (newest["reason"], newest["delta"]), ("expired", -40))
    check("... nothing expires twice", fl.star_balance(GIFTED), 50)
    check("a gift that has run out is not given again by the next run of the command",
          fl.grant_bonus_once(GIFTED, 100, GIFT_KEY, soon), None)
    check("... and the balance is what it was", fl.star_balance(GIFTED), 50)

    check("the claim is shared the other way too: an ordinary grant first", fl.grant_stars_once(GIFTED, 25, "gift-t909-b"), 75)
    check("... then the gift under the same key credits nothing", fl.grant_bonus_once(GIFTED, 100, "gift-t909-b", soon), None)
    check("... no bonus appeared", (fl.star_totals(GIFTED)["bonus"], fl.star_balance(GIFTED)), (0, 75))
    check("a different key is a different gift", fl.grant_bonus_once(GIFTED, 10, "gift-t909-c", soon), 85)
    midnight = (datetime.now(timezone.utc) + timedelta(days=10)).replace(hour=0, minute=0, second=0, microsecond=0)
    check("a gift that ends at midnight UTC, as /gift's last day does", fl.grant_bonus_once(GIFTED, 5, "gift-t909-d", midnight), 90)
    detail = fl.star_ledger_for(GIFTED, 1)[0]["detail"] or ""
    ok("... is written in the ledger with its last day, not the moment after it",
       f"gift-t909-d, bonus through {midnight - timedelta(days=1):%Y-%m-%d}" in detail and f"{midnight:%Y-%m-%d}" not in detail, detail)

    print("\n== the gift, to the same person at the same moment ==")
    check("nobody has a wallet here either", wallet_rows(GIFT_RACE), 0)
    results: list = []
    barrier = threading.Barrier(6)

    def race():
        barrier.wait()
        try:
            results.append(fl.grant_bonus_once(GIFT_RACE, 100, GIFT_KEY, soon))
        except Exception as exc:                              # noqa: BLE001 -- a failure is a failed check, below
            results.append(exc)
    racers = [threading.Thread(target=race) for _ in range(6)]
    for racer in racers:
        racer.start()
    for racer in racers:
        racer.join()
    check("six at once: none of them failed", [r for r in results if isinstance(r, Exception)], [])
    check("exactly one won, and was told the balance", sorted(r for r in results if r is not None), [100])
    check("the others were told it was already given", sum(1 for r in results if r is None), 5)
    check("the balance rose once", fl.star_balance(GIFT_RACE), 100)
    check("one claim, one lot, one ledger row",
          (claim_rows(GIFT_KEY, GIFT_RACE), lot_rows(GIFT_RACE), ledger_rows_of(GIFT_RACE)), (1, 1, 1))
    check("and the lot is the whole gift", fl.star_totals(GIFT_RACE)["bonus"], 100)

    print("\n== the gift, while another process holds the claim ==")

    def blocked() -> bool:
        """True once some session is waiting on a lock: the grant has met the other process's claim."""
        with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as watcher:
            for _ in range(200):
                if watcher.execute("SELECT count(*) FROM pg_stat_activity WHERE wait_event_type = 'Lock' "
                                   "AND datname = current_database()").fetchone()[0]:
                    return True
                time.sleep(0.05)
        return False

    def grant_while_held(key: str, finish: str):
        """Another process has staked this gift's claim in a transaction it has not finished (the way a second bot
        mid-grant looks), and this one asks. Returns (did it have to wait, what it came back with)."""
        outcome: list = []

        def ask():
            try:
                outcome.append(fl.grant_bonus_once(GIFT_RACE, 100, key, soon))
            except Exception as exc:                          # noqa: BLE001 -- a failed check, below
                outcome.append(exc)
        holder = psycopg.connect(os.environ["DATABASE_URL"])
        try:
            holder.execute("INSERT INTO family.settings (key, value) VALUES (%s, %s)",
                           (f"stars:granted:{key}:{GIFT_RACE}", "held"))
            worker = threading.Thread(target=ask)
            worker.start()
            waited = blocked()
            getattr(holder, finish)()
        finally:
            holder.close()
        worker.join(30)
        return waited, outcome

    waited, outcome = grant_while_held("gift-t909-held", "commit")
    check("the grant waits for the other process, and finds the gift taken once it has committed", (waited, outcome), (True, [None]))
    check("... so the balance, the lots and the ledger are as they were",
          (fl.star_balance(GIFT_RACE), lot_rows(GIFT_RACE), ledger_rows_of(GIFT_RACE)), (100, 1, 1))
    waited, outcome = grant_while_held("gift-t909-held-2", "rollback")
    check("if the other process gives up instead, the grant waits and then goes through", (waited, outcome), (True, [200]))
    check("... once: one more lot and one more ledger row",
          (fl.star_balance(GIFT_RACE), lot_rows(GIFT_RACE), ledger_rows_of(GIFT_RACE)), (200, 2, 2))

    print("\n== a gift that is refused, and one that fails half way ==")
    refused = [("no credits", dict(amount=0)),
               ("a negative gift", dict(amount=-5)),
               (f"one more than the most a gift may give ({fl.GIFT_MAX_CREDIT + 1})", dict(amount=fl.GIFT_MAX_CREDIT + 1)),
               ("an expiry with no time zone", dict(expires=datetime.now() + timedelta(days=30))),
               ("an expiry that has already passed", dict(expires=datetime.now(timezone.utc) - timedelta(seconds=1)))]
    for label, change in refused:
        try:
            fl.grant_bonus_once(GIFT_FAIL, change.get("amount", 100), GIFT_KEY, change.get("expires", soon))
        except ValueError:
            ok(f"{label}: refused with ValueError", True)
        except Exception as exc:                                  # noqa: BLE001 -- refused, but not the way callers expect
            ok(f"{label}: refused with ValueError", False, f"{type(exc).__name__} instead: {exc}")
        else:
            ok(f"{label}: refused with ValueError", False, "it was accepted")
    check("a refusal writes nothing: no claim, no wallet, no lot, no ledger row",
          (claim_rows(GIFT_KEY, GIFT_FAIL), wallet_rows(GIFT_FAIL), lot_rows(GIFT_FAIL), ledger_rows_of(GIFT_FAIL)),
          (0, 0, 0, 0))
    check("... so the balance is unchanged", fl.star_balance(GIFT_FAIL), 0)

    real_move = fl._move_in
    calls = {"n": 0}

    def fail_once(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("the database went away between the claim and the credit")
        return real_move(*args, **kwargs)
    fl._move_in = fail_once
    try:
        fl.grant_bonus_once(GIFT_FAIL, fl.GIFT_MAX_CREDIT, GIFT_KEY, soon)
        ok("a failure after the claim propagates", False, "it did not raise")
    except RuntimeError:
        ok("a failure after the claim propagates", True)
    finally:
        fl._move_in = real_move
    check("... and leaves no claim, wallet, lot or ledger row: claim and credit stand or fall together",
          (claim_rows(GIFT_KEY, GIFT_FAIL), wallet_rows(GIFT_FAIL), lot_rows(GIFT_FAIL), ledger_rows_of(GIFT_FAIL)),
          (0, 0, 0, 0))

    real_connect = fl._connect

    class _NoLots:
        """A connection that takes the claim and the credit and then cannot write the lot."""
        def __init__(self, conn):
            self._conn = conn

        def __getattr__(self, name):
            return getattr(self._conn, name)

        def execute(self, sql, *args, **kwargs):
            if "INSERT INTO family.star_bonus_lots" in str(sql):
                raise RuntimeError("the database went away between the credit and the lot")
            return self._conn.execute(sql, *args, **kwargs)

    class _ConnectNoLots:
        def __enter__(self):
            self._cm = real_connect()
            return _NoLots(self._cm.__enter__())

        def __exit__(self, *exc):
            return self._cm.__exit__(*exc)
    fl._connect = _ConnectNoLots
    try:
        fl.grant_bonus_once(GIFT_FAIL, fl.GIFT_MAX_CREDIT, GIFT_KEY, soon)
        ok("a failure after the credit, before the lot, propagates", False, "it did not raise")
    except RuntimeError:
        ok("a failure after the credit, before the lot, propagates", True)
    finally:
        fl._connect = real_connect
    check("... and the credit is taken back with the claim: nothing was written",
          (claim_rows(GIFT_KEY, GIFT_FAIL), wallet_rows(GIFT_FAIL), lot_rows(GIFT_FAIL), ledger_rows_of(GIFT_FAIL)),
          (0, 0, 0, 0))

    check("so the next run of the command gives it, even the most a gift may give",
          fl.grant_bonus_once(GIFT_FAIL, fl.GIFT_MAX_CREDIT, GIFT_KEY, soon), fl.GIFT_MAX_CREDIT)
    check("... as bonus, once",
          (fl.star_totals(GIFT_FAIL)["bonus"], claim_rows(GIFT_KEY, GIFT_FAIL), lot_rows(GIFT_FAIL)),
          (fl.GIFT_MAX_CREDIT, 1, 1))

    run_out(GIFT_FAIL)
    check("a second gift to somebody whose first has run out but not been collected yet: the new balance leaves the old gift out",
          fl.grant_bonus_once(GIFT_FAIL, 10, "gift-t909-d", soon), 10)
    check("... the ledger took the old one back first and then gave the new",
          [row["reason"] for row in fl.star_ledger_for(GIFT_FAIL, 2)], ["grant", "expired"])
    check("... and only the new gift is bonus", fl.star_totals(GIFT_FAIL)["bonus"], 10)

    print("\n== the ledger ==")
    for user_id in USERS:
        rows = fl.star_ledger_for(user_id, 100)
        running, broken = 0, None
        for row in reversed(rows):
            running += row["delta"]
            if running != row["balance_after"]:
                broken = row
                break
        ok(f"{user_id}: the ledger adds up from zero, row by row", broken is None, str(broken))
        check(f"{user_id}: and its running total is the balance", running, fl.star_balance(user_id))
    reasons = {row["reason"] for row in fl.star_ledger_for(USER, 100)}
    ok("a payment's bonus and its expiry are rows of their own",
       {"topup", "bonus", "expired"} <= reasons, str(reasons))
    try:
        fl.move_stars(USER, 1, "vibes")
    except ValueError:
        ok("an unknown reason is refused rather than recorded", True)
    else:
        ok("an unknown reason is refused rather than recorded", False, "it was recorded")

    print("\n== the overview ManagerBot prints ==")
    totals, top = fl.star_balance_overview(10)
    check("it sees every wallet made above", totals["wallets"], len(USERS))
    check("and totals the credit still outstanding", totals["outstanding"],
          sum(fl.star_balance(user_id) for user_id in USERS))
    check("and how much of that is bonus", totals["bonus"],
          sum(fl.star_totals(user_id)["bonus"] for user_id in USERS))
    check("a zero balance is left out of the list",
          sorted(row["user_id"] for row in top),
          sorted(user_id for user_id in USERS if raw_balance(user_id) != 0))
    ok("... and the wallet at zero is the one missing", raw_balance(USER) == 0 and USER not in {row["user_id"] for row in top})

    print("\n== a payment Telegram delivers twice ==")
    first = fl.topup(DUP, 100, "paid", "t909-charge-1")
    ok("the first delivery credits", first is not None and first["credited"] == 200 and first["bonus"] == 400,
       str(first))
    balance = fl.star_balance(DUP)

    def ledger_rows():
        with db.pooled() as conn:
            return conn.execute("SELECT count(*) FROM family.star_ledger WHERE user_id = %s", (DUP,)).fetchone()[0]
    rows = ledger_rows()
    again = fl.topup(DUP, 100, "paid", "t909-charge-1")
    check("the same charge again credits nothing, and says so", again, None)
    check("the balance did not move", fl.star_balance(DUP), balance)
    check("the ledger did not grow", ledger_rows(), rows)
    check("the Stars counted as paid are the 100, not 200", fl.star_totals(DUP)["stars_paid"], 100)
    with db.pooled() as conn:
        claims = conn.execute("SELECT count(*) FROM family.settings WHERE key = 'stars:paid:t909-charge-1'").fetchone()[0]
    check("there is exactly one claim for it", claims, 1)
    second = fl.topup(DUP, 50, "paid", "t909-charge-2")
    ok("a different charge is a different payment and credits (priced up the ladder from 100)",
       second is not None and second["lifetime_before"] == 100, str(second))
    sandbox = [fl.topup(DUP, 10, "sandbox top-up"), fl.topup(DUP, 10, "sandbox top-up")]
    ok("without a charge id (the sandbox, which charges nobody) every call credits, as before",
       all(entry is not None for entry in sandbox))

    results: list = []
    racers = [threading.Thread(target=lambda: results.append(fl.topup(DUP, 20, "racing", "t909-race")))
              for _ in range(6)]
    before = fl.star_balance(DUP)
    for racer in racers:
        racer.start()
    for racer in racers:
        racer.join()
    check("six deliveries of one payment at the same moment: one wins", sum(1 for r in results if r is not None), 1)
    won = next(r for r in results if r is not None)
    check("and the balance moved by that one credit only", fl.star_balance(DUP) - before,
          won["credited"] + won["bonus"])

    real_move = fl._move_in

    def boom(*args, **kwargs):
        raise RuntimeError("the database went away between the claim and the credit")
    fl._move_in = boom
    try:
        fl.topup(DUP, 30, "paid", "t909-crash")
        ok("a failure after the claim propagates", False, "it did not raise")
    except RuntimeError:
        ok("a failure after the claim propagates", True)
    finally:
        fl._move_in = real_move
    with db.pooled() as conn:
        left = conn.execute("SELECT count(*) FROM family.settings WHERE key = 'stars:paid:t909-crash'").fetchone()[0]
    check("... and leaves no claim behind (claim and credit stand or fall together)", left, 0)
    retry = fl.topup(DUP, 30, "paid", "t909-crash")
    ok("so Telegram's redelivery of that payment is credited", retry is not None, str(retry))

    print("\n== a database blip in the middle of a payment ==")
    fl._TOPUP_BACKOFF_SECONDS = (0, 0)

    def claims_for(charge):
        with db.pooled() as conn:
            return conn.execute("SELECT count(*) FROM family.settings WHERE key = %s",
                                (f"stars:paid:{charge}",)).fetchone()[0]

    tries = {"n": 0}

    def flaky(*args, **kwargs):
        tries["n"] += 1
        if tries["n"] == 1:
            raise psycopg.OperationalError("server closed the connection unexpectedly")
        return real_move(*args, **kwargs)
    before = fl.star_balance(DUP)
    fl._move_in = flaky
    try:
        blip = fl.topup(DUP, 40, "paid", "t909-blip")
    except psycopg.OperationalError:
        blip = None
    finally:
        fl._move_in = real_move
    ok("a connection dropped mid-payment is tried again, and credits", blip is not None, str(blip))
    if blip is not None:
        check("... once: the balance moved by that one credit", fl.star_balance(DUP) - before,
              blip["credited"] + blip["bonus"])
    check("... with one claim for it", claims_for("t909-blip"), 1)

    tries["n"] = 0

    def down(*args, **kwargs):
        tries["n"] += 1
        raise psycopg.OperationalError("the database is down")
    before = fl.star_balance(DUP)
    fl._move_in = down
    try:
        fl.topup(DUP, 40, "paid", "t909-down")
        ok("a database that stays down: the error comes out after the last try", False, "it did not raise")
    except psycopg.OperationalError:
        ok("a database that stays down: the error comes out after the last try", True)
    finally:
        fl._move_in = real_move
    check("... after exactly _TOPUP_ATTEMPTS tries", tries["n"], fl._TOPUP_ATTEMPTS)
    check("... crediting nothing", fl.star_balance(DUP), before)
    check("... and leaving no claim, so a later delivery can still credit it", claims_for("t909-down"), 0)

    # The hard case: the COMMIT reached the server and went through, but its answer
    # never came back. The retry must find its own claim and credit nothing more.
    real_connect = fl._connect
    lost = {"done": False}

    class _AnswerLost:
        def __init__(self, conn):
            self._conn = conn

        def __getattr__(self, name):
            return getattr(self._conn, name)

        def commit(self):
            self._conn.commit()
            if not lost["done"]:
                lost["done"] = True
                raise psycopg.OperationalError("connection lost after COMMIT was sent")

    class _Connect:
        def __enter__(self):
            self._cm = real_connect()
            return _AnswerLost(self._cm.__enter__())

        def __exit__(self, *exc):
            return self._cm.__exit__(*exc)
    before = fl.star_balance(DUP)
    fl._connect = _Connect
    try:
        answer_lost = fl.topup(DUP, 40, "paid", "t909-lost-answer")
    finally:
        fl._connect = real_connect
    check("a COMMIT whose answer was lost: the retry finds its own claim and returns None", answer_lost, None)
    moved = fl.star_balance(DUP) - before
    once = fl.quote_credit(fl.star_totals(DUP)["stars_paid"] - 40, 40)
    check("... and the payment was credited exactly once (not zero, not twice)",
          moved, once["base"] + once["bonus"])
    check("... with one claim for it", claims_for("t909-lost-answer"), 1)

    print("\n== the 220 gift: started once, claimed by writing to a bot ==")
    promo_people = [909011, 909012, 909013]

    def promo_scrub() -> None:
        """Its own people and the gift's own rows: the start, the stop, the
        milestone notice, and the claims of these three only."""
        with db.pooled() as conn:
            for table in ("star_bonus_lots", "star_ledger", "star_balances"):
                conn.execute(f"DELETE FROM family.{table} WHERE user_id = ANY(%s)", (promo_people,))
            conn.execute("DELETE FROM family.settings WHERE key = ANY(%s)",
                         ([fl.PROMO_STARTED, fl.PROMO_STOPPED, fl.PROMO_MILESTONE]
                          + [f"stars:granted:{fl.PROMO_KEY}:{p}" for p in promo_people],))
            conn.commit()
        fl._promo_cache.update(at=None, started=None, stopped=None)

    def promo_ledger_rows(user_id: int) -> int:
        with db.pooled() as conn:
            return conn.execute("SELECT count(*) FROM family.star_ledger WHERE user_id = %s",
                                (user_id,)).fetchone()[0]

    first, racer, late = promo_people
    promo_scrub()
    check("before the start a claim gives nothing", fl.claim_promo_gift(first), (fl.PROMO_CLOSED, None))
    check("... and writes no wallet", fl.star_balance(first), 0)

    starts, gate = [], threading.Barrier(4)

    def start_one() -> None:
        gate.wait()
        starts.append(fl.start_promo())

    threads = [threading.Thread(target=start_one) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    check("four starts at the same moment: exactly one starts it", sorted(won for _, won in starts),
          [False, False, False, True])
    check("... and all four read the same start", len({when for when, _ in starts}), 1)
    started = starts[0][0]
    check("a later start changes nothing", fl.start_promo(), (started, False))
    check("it is open", fl.promo_open(fresh=True), True)

    outcome, expires = fl.claim_promo_gift(first)
    check("the first claim gives", outcome, fl.PROMO_GIVEN)
    check("... 220 ⚡", fl.star_balance(first), fl.PROMO_CREDITS)
    totals = fl.star_totals(first)
    check("... all of it bonus, in one lot", (totals["bonus"], totals["bonus_next_amount"]), (220, 220))
    check("... running out when the claim said", totals["bonus_next_expiry"], expires)
    check("... at the end of the 28th day after today (UTC)",
          expires, fl.promo_expiry(datetime.now(timezone.utc)))
    check("a second claim by the same person: had it", fl.claim_promo_gift(first), (fl.PROMO_HAD, None))
    check("... still 220, one ledger row", (fl.star_balance(first), promo_ledger_rows(first)), (220, 1))
    check("promo_given_to knows", (fl.promo_given_to(first), fl.promo_given_to(racer)), (True, False))

    outcomes, gate = [], threading.Barrier(6)

    def claim_one() -> None:
        gate.wait()
        outcomes.append(fl.claim_promo_gift(racer)[0])

    threads = [threading.Thread(target=claim_one) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    check("six claims by one person at the same moment: given once",
          sorted(outcomes), sorted([fl.PROMO_GIVEN] + [fl.PROMO_HAD] * 5))
    check("... 220 in the wallet, one ledger row", (fl.star_balance(racer), promo_ledger_rows(racer)), (220, 1))
    check("two of these people have it", fl.promo_given_to(first) and fl.promo_given_to(racer), True)

    check("the milestone notice: the first time", fl.note_promo_milestone(), True)
    check("... and never again", fl.note_promo_milestone(), False)
    ok("... and when it was reached is kept", fl.promo_milestone_at() is not None)

    check("an open gift can be stopped", fl.stop_promo(), True)
    check("a second stop does nothing", fl.stop_promo(), False)
    check("after the stop a claim gives nothing", fl.claim_promo_gift(late), (fl.PROMO_CLOSED, None))
    check("... and writes no wallet", fl.star_balance(late), 0)
    check("credit already given stays", (fl.star_balance(first), fl.star_balance(racer)), (220, 220))
    check("starting again after the stop is still refused", fl.start_promo()[1], False)
    promo_scrub()

    scrub()
    with db.pooled() as conn:
        conn.execute("DROP SCHEMA IF EXISTS credit_scenarios CASCADE")
        conn.commit()
    db.close_pool()

    print(f"\n{_checks} checks, FAILURES: {len(failures)}")
    for name in failures:
        print("  -", name)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
