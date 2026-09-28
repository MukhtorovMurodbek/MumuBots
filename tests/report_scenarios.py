"""Problems against a real Postgres: recorded once per incident whether or
not anybody reports them, read back newest first, and holding nothing about
anybody until somebody chooses to be on one.

Run from the repo root:  python tests/report_scenarios.py
Needs Postgres (TEST_DATABASE_URL, or the local default).
"""
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

os.environ.setdefault("DATABASE_URL", os.environ.get(
    "TEST_DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/botfamily_test"))
os.environ["DB_SCHEMA"] = "report_scenarios"
os.environ.setdefault("CBOT_TOKEN", "1:AA")
os.environ.setdefault("CBOT_USERNAME", "x")

os.chdir(ROOT / "convert_bot")
sys.path.insert(0, str(ROOT / "convert_bot"))
sys.path.insert(1, str(ROOT / "shared"))

import db  # noqa: E402
import family_link as fl  # noqa: E402
import problems  # noqa: E402

# What family_link.attach() sets in a running ConvertBot. Without it every row
# here is filed under "unattached", and the nightly-report count below looked
# for "convertbot" and failed from 1.7.0 on.
fl._bot_id = "convertbot"

failures = []


def check(name, ok, detail=""):
    print(("  ok   " if ok else "  FAIL ") + name + ("" if ok else f"\n       {detail}"))
    if not ok:
        failures.append(name)


def main() -> int:
    db.init_db()
    fl.init_family_schema()
    incidents = [problems.new_incident(), problems.new_incident()]
    when = datetime(2026, 9, 11, 12, 47, tzinfo=timezone.utc)

    print("\n== a problem recorded because it happened ==")
    check("a problem nobody reported is stored",
          fl.record_problem_occurrence("CV-TIMEOUT", incidents[0], when,
                                       problems.level("CV-TIMEOUT")) is True)
    check("the same incident again counts rather than duplicating",
          fl.record_problem_occurrence("CV-TIMEOUT", incidents[0], when,
                                       problems.level("CV-TIMEOUT")) is False)
    check("another incident is its own row",
          fl.record_problem_occurrence("FM-CRASH", incidents[1], when,
                                       problems.level("FM-CRASH")) is True)
    rows = fl.recent_problem_reports(5)
    ours = [row for row in rows if row["incident"] in incidents]
    check("they read back newest first", [row["incident"] for row in ours] == incidents[::-1], ours)
    check("with the code, the time and the version",
          ours and ours[-1]["code"] == "CV-TIMEOUT" and ours[-1]["occurred_at"] == when
          and ours[-1]["version"] == fl.VERSION, ours[-1:])
    check("a re-show is counted on the row it belongs to",
          ours and ours[-1]["seen"] == 2, ours[-1:])
    check("and nothing about a person is on any of them",
          all(row["shared_at"] is None and row["user_id"] is None and row["username"] is None
              for row in ours), ours)
    check("so nothing shows up under the reported-by-somebody view",
          not [row for row in fl.recent_problem_reports(50, shared_only=True)
               if row["incident"] in incidents])

    print("\n== the level is stored with the row ==")
    rows = {row["incident"]: row for row in fl.recent_problem_reports(50)}
    check("a fault is recorded as a fault",
          rows[incidents[0]]["level"] == problems.FAULT, rows[incidents[0]]["level"])
    check("and a crash as urgent",
          rows[incidents[1]]["level"] == problems.URGENT, rows[incidents[1]]["level"])
    check("which is what the nightly report counts",
          fl.problem_counts_since(24).get("convertbot", {}).get(problems.URGENT,
                                                                {}).get("incidents", 0) >= 1,
          fl.problem_counts_since(24))

    print("\n== and then somebody volunteers ==")
    check("their details go onto the row that was already there",
          fl.attach_problem_reporter("CV-TIMEOUT", incidents[0], when,
                                     777, "someone", "uz", "private",
                                     problems.level("CV-TIMEOUT")) is True)
    check("a second volunteer on the same incident changes nothing",
          fl.attach_problem_reporter("CV-TIMEOUT", incidents[0], when,
                                     888, "other", "en", "private",
                                     problems.level("CV-TIMEOUT")) is False)
    row = fl.problem_report_by_incident(incidents[0])
    check("it is readable by incident, with what they agreed to send",
          row and row["user_id"] == 777 and row["username"] == "someone"
          and row["user_lang"] == "uz" and row["chat_kind"] == "private"
          and row["shared_at"] is not None, row)
    check("and that one now shows up under the reported-by-somebody view",
          [r["incident"] for r in fl.recent_problem_reports(50, shared_only=True)
           if r["incident"] in incidents] == [incidents[0]])
    check("the one nobody signed still holds nothing",
          (fl.problem_report_by_incident(incidents[1]) or {}).get("user_id") is None)

    print("\n== a report from a bot that could not reach the database when it failed ==")
    orphan = problems.new_incident()
    incidents.append(orphan)
    check("the row is made rather than the report being lost",
          fl.attach_problem_reporter("FM-CRASH", orphan, when, 999, None, "ru", "private",
                                     problems.level("FM-CRASH")) is True)
    check("and it reads back whole",
          (fl.problem_report_by_incident(orphan) or {}).get("user_id") == 999)

    print("\n== getting rid of the part that is about a person ==")
    check("erasing one person takes them off every problem they signed",
          fl.forget_problem_reporter(777) >= 1)
    after = fl.problem_report_by_incident(incidents[0])
    check("their id, name, language and chat kind are all gone",
          after and after["user_id"] is None and after["username"] is None
          and after["user_lang"] is None and after["chat_kind"] is None, after)
    check("but the problem itself is still counted -- it still happened",
          after and after["code"] == "CV-TIMEOUT" and after["seen"] >= 1, after)
    check("somebody else's row is untouched",
          (fl.problem_report_by_incident(orphan) or {}).get("user_id") == 999)

    # The scheduled version of the same thing, on a clock rather than on
    # request. Nothing here is old enough to be caught, which is the check:
    # a prune that removed today's rows would be a bug that only showed up as
    # a missing nightly report.
    before = len(fl.recent_problem_reports(50))
    fl.prune_problem_reports()
    check("a prune leaves rows that are still inside their retention",
          len(fl.recent_problem_reports(50)) == before, before)

    with db.pooled() as conn:
        conn.execute("DELETE FROM family.problem_reports WHERE incident = ANY(%s)", (incidents,))
        conn.execute("DROP SCHEMA IF EXISTS report_scenarios CASCADE")
        conn.commit()
    db.close_pool()

    print(f"\nFAILURES: {len(failures)}")
    for name in failures:
        print("  -", name)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
