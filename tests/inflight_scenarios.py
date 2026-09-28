"""Paid conversions in flight, against a real Postgres: a conversion whose
process was killed outright gets its credit back -- once, from whichever
process notices -- and the person is told.

Run from the repo root:  python tests/inflight_scenarios.py
Needs Postgres (TEST_DATABASE_URL, or the local default).

The graceful path was always covered: a stop signal cancels a running
conversion, and jobs.py refunds it. What was not was the container killed
outright -- out of memory, a host failure -- where nothing runs to refund, and
TERMS.md promises the credit comes back "if the bot was restarted in the
middle of it".
"""
import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

os.environ.setdefault("DATABASE_URL", os.environ.get(
    "TEST_DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/botfamily_test"))
os.environ["DB_SCHEMA"] = "inflight_scenarios"
os.environ.setdefault("CBOT_TOKEN", "1:AA")
os.environ.setdefault("CBOT_USERNAME", "x")
for variable in ("CBOT_API_ID", "CBOT_API_HASH"):
    os.environ.pop(variable, None)

os.chdir(ROOT / "convert_bot")
sys.path.insert(0, str(ROOT / "convert_bot"))
sys.path.insert(1, str(ROOT / "shared"))

import db  # noqa: E402
import family_link as fl  # noqa: E402
import i18n  # noqa: E402
import convert_runner  # noqa: E402
import shared_features  # noqa: E402

failures = []


def check(name, ok, detail=""):
    print(("  ok   " if ok else "  FAIL ") + name + ("" if ok else f"\n       {detail}"))
    if not ok:
        failures.append(name)


def age(job_id, seconds):
    with db.pooled() as conn:
        conn.execute("UPDATE conversions_in_flight SET touched_at = now() - make_interval(secs => %s) "
                     "WHERE job_id = %s", (seconds, job_id))
        conn.commit()


def rows():
    with db.pooled() as conn:
        return {r[0]: r[1] for r in conn.execute("SELECT job_id, recovered FROM conversions_in_flight")}


class FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append((chat_id, text, kwargs.get("reply_markup")))


def main() -> int:
    with db.connect() as conn:
        conn.execute("DROP SCHEMA IF EXISTS inflight_scenarios CASCADE")
        conn.commit()
    db.init_db()
    fl.init_family_schema()

    print("\n== the record ==")
    db.hold_conversion("a", 11, 11, 60, "en", "png->pdf", "p1")
    db.hold_conversion("b", 12, 12, 80, "uz", "mp4->mkv", "p2")
    db.hold_conversion("a", 11, 11, 60, "en", "png->pdf", "p1")
    check("one row per conversion, however often it is written", sorted(rows()) == ["a", "b"], rows())
    check("nothing fresh is taken for lost", db.claim_orphaned_conversions("p1", 120) == [])

    age("b", 300)
    claimed = db.claim_orphaned_conversions("p1", 120)
    check("a row nobody has stamped for longer than the limit is claimed",
          [r["job_id"] for r in claimed] == ["b"] and claimed[0]["price"] == 80 and claimed[0]["lang"] == "uz",
          claimed)
    check("and only once", db.claim_orphaned_conversions("p1", 120) == []
          and db.claim_orphaned_conversions("p3", 120) == [])
    age("a", 300)
    check("a process never claims its own rows", db.claim_orphaned_conversions("p1", 120) == [])
    check("stamping keeps a process's rows alive", db.touch_conversions("p1") == 1
          and db.claim_orphaned_conversions("p2", 120) == [])

    print("\n== how a conversion's ending reads it ==")
    check("the one refunded elsewhere says so", db.release_conversion("b") == "recovered")
    check("the one still held says so", db.release_conversion("a") == "held")
    check("and a second release finds nothing", db.release_conversion("a") == "absent")

    print("\n== erasing and pruning ==")
    db.hold_conversion("c", 13, 13, 20, "en", "jpg->png", "p1")
    check("/deletemydata removes a person's rows", db.erase_user(13) >= 1 and "c" not in rows(), rows())
    db.hold_conversion("d", 14, 14, 20, "en", "jpg->png", "p1")
    age("d", 400)
    db.claim_orphaned_conversions("p9", 120)
    age("d", 2 * 86400)
    db.hold_conversion("e", 15, 15, 20, "en", "jpg->png", "p1")
    age("e", 3 * 86400)
    db.hold_conversion("f", 16, 16, 20, "en", "jpg->png", "p1")
    db.prune_old_data()
    check("claimed rows go after a day, stuck ones after two, live ones stay", sorted(rows()) == ["f"], rows())
    db.release_conversion("f")

    print("\n== recovery, end to end ==")
    events = []
    shared_features.set_event_hook(lambda level, kind, message, details=None: events.append((level, message)))
    before = fl.move_stars(21, 500, "grant", "test balance")
    db.hold_conversion("lost", 21, 21, 140, "ru", "mp4->mkv", "a-killed-process")
    age("lost", 600)
    application = type("App", (), {"bot": FakeBot()})()
    runner = convert_runner.ConvertRunner(application, [], ROOT / "tests", lambda *a: "")
    refunded = asyncio.run(runner.keep_alive_and_recover())
    check("the lost conversion is found", refunded == 1, refunded)
    check("its credit is back on the balance", fl.star_balance(21) == before + 140, (before, fl.star_balance(21)))
    ledger = fl.star_ledger_for(21, 5)
    check("as a refund in the ledger, saying why",
          ledger and ledger[0]["reason"] == "refund" and "stopped before it finished" in (ledger[0]["detail"] or ""),
          ledger[:1])
    sent = application.bot.sent
    check("the person is told, in their language, with the code and a Report button",
          len(sent) == 1 and sent[0][0] == 21
          and sent[0][1].startswith(i18n.t("ru", "job_reason_interrupted"))
          and sent[0][1].endswith("CV-RESTARTED") and sent[0][2] is not None, sent)
    check("the owner hears about it", any("lost to a restart and refunded" in m for _, m in events), events)
    check("a second pass refunds nothing more",
          asyncio.run(runner.keep_alive_and_recover()) == 0 and fl.star_balance(21) == before + 140)
    check("and the process that was presumed dead, if it finishes, is told not to refund",
          db.release_conversion("lost") == "recovered")

    # The wallet lives in the family schema, which other suites count; leave
    # it as it was found.
    with db.pooled() as conn:
        for table in ("star_ledger", "star_bonus_lots", "star_balances"):
            conn.execute(f"DELETE FROM {fl.FAMILY_SCHEMA}.{table} WHERE user_id = 21")
        conn.commit()
    db.close_pool()
    print(f"\nFAILURES: {len(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
