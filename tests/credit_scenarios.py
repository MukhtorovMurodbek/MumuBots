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
  payments are final   -- there is no function that takes a payment back
  the ledger           -- adds up from zero to the balance, row by row

Run from the repo root:  python tests/credit_scenarios.py
Needs Postgres (TEST_DATABASE_URL, or the local default). No network, and it
uses a scratch schema of its own, never a bot's.
"""
import os
import sys
import threading
from datetime import datetime, timezone
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

USER, OTHER, RACER = 909001, 909002, 909003
USERS = [USER, OTHER, RACER]

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
        conn.execute("DELETE FROM family.star_bonus_lots WHERE user_id = ANY(%s)", (USERS,))
        conn.execute("DELETE FROM family.star_ledger WHERE user_id = ANY(%s)", (USERS,))
        conn.execute("DELETE FROM family.star_balances WHERE user_id = ANY(%s)", (USERS,))
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
    check("it sees all three wallets", totals["wallets"], 3)
    check("and totals the credit still outstanding", totals["outstanding"],
          sum(fl.star_balance(user_id) for user_id in USERS))
    check("and how much of that is bonus", totals["bonus"],
          sum(fl.star_totals(user_id)["bonus"] for user_id in USERS))
    check("a zero balance is left out of the list",
          sorted(row["user_id"] for row in top), sorted([OTHER, RACER]))

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
