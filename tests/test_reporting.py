"""How loudly a problem is answered, and what the nightly report says.

Three things are checked here and they are the three that decide whether the
owner's phone is useful or ignorable:

  * every code lands in exactly one level, and the urgent set is the small one
  * an urgent problem alerts at once, a crash loop alerts once, and a flood of
    ordinary faults escalates by itself
  * the nightly report lists urgent problems, groups faults, and only counts
    refusals

No database and no Telegram: the alerting reads a clock it is handed and
writes to a captured event hook, and the report builder is pure.
"""
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / ".mirror" / "manager_bot"))
sys.path.insert(0, str(HERE / "stubs"))

os.environ.setdefault("MBOT_TOKEN", "x")
os.environ.setdefault("MBOT_USERNAME", "x")
os.environ.setdefault("MBOT_ADMIN_ID", "1")
os.environ.setdefault("DATABASE_URL", "postgresql://stub")

import problems as pr   # noqa: E402
import bot as mb        # noqa: E402

failures = []


def check(name, ok, detail=""):
    print(("  ok  " if ok else "  FAIL  ") + name + ("" if ok else f"  -- {detail}"))
    if not ok:
        failures.append(name)


# ---------------------------------------------------------------------------
print("== every code has exactly one level ==")
# ---------------------------------------------------------------------------
levels = {code: pr.level(code) for code in pr.PROBLEMS}
check("every declared code gets a level", all(l in (1, 2, 3) for l in levels.values()),
      sorted({l for l in levels.values()}))
check("nothing is both urgent and merely refused",
      not (pr.URGENT_CODES & pr.SIMPLE), sorted(pr.URGENT_CODES & pr.SIMPLE))
check("every urgent code is a code that exists",
      not (pr.URGENT_CODES - set(pr.PROBLEMS)), sorted(pr.URGENT_CODES - set(pr.PROBLEMS)))

urgent = [c for c, l in levels.items() if l == pr.URGENT]
refused = [c for c, l in levels.items() if l == pr.REFUSED]
faults = [c for c, l in levels.items() if l == pr.FAULT]
print(f"      {len(urgent)} urgent, {len(faults)} fault, {len(refused)} refused")
check("urgent is the small set -- an alert for everything is an alert for nothing",
      len(urgent) < len(pr.PROBLEMS) / 8, f"{len(urgent)} of {len(pr.PROBLEMS)}")
check("a crash is urgent", pr.urgent("FM-CRASH"))
check("so is a message accepted and never delivered", pr.urgent("AN-DELIVERY"))
check("a file over the limit is not", pr.level("CV-TOO-BIG") == pr.REFUSED,
      pr.level("CV-TOO-BIG"))
check("a download that failed on our side is a fault",
      pr.level("DL-ALL-ROUTES") == pr.FAULT, pr.level("DL-ALL-ROUTES"))
check("an unknown code is a fault rather than assumed harmless",
      pr.level("ZZ-NEVER-HEARD-OF-IT") == pr.FAULT)

# ---------------------------------------------------------------------------
print("\n== who gets woken up ==")
# ---------------------------------------------------------------------------
sys.path.insert(0, str(HERE / ".mirror" / "anon_bot"))
for name in ("problems", "bot", "db", "i18n", "family_link", "shared_features",
             "lifecycle", "live_message", "transcript"):
    sys.modules.pop(name, None)
os.environ.setdefault("ABOT_TOKEN", "1:AA")
os.environ.setdefault("ABOT_USERNAME", "x")
import shared_features as sf  # noqa: E402

sent = []
sf._event_hook = lambda level, kind, message, details=None: sent.append(
    {"level": level, "kind": kind, "message": message, "details": details})


def reset():
    sent.clear()
    sf._alerted.clear()
    sf._recent_faults.clear()
    sf._problem_buffer.clear()


reset()
sf.note_occurrence("FM-CRASH", "AAAAAA", datetime.now(timezone.utc))
check("an urgent problem goes out at once", len(sent) == 1, sent)
check("as a critical event, which ManagerBot forwards",
      sent and sent[0]["level"] == "critical", sent[:1])
check("naming the code", bool(sent) and "FM-CRASH" in sent[0]["message"], sent[:1])
check("and the incident, so /report finds it",
      bool(sent) and "AAAAAA" in (sent[0]["details"] or ""), sent[:1])
check("and nothing about anybody",
      not any(w in str(sent[:1]) for w in ("user_id", "@", "username")), str(sent[:1]))

reset()
# Distinct incidents, because note_occurrence drops a repeat of one it is
# already holding -- a crash loop is two hundred *occurrences*, not two
# hundred re-shows of one.
alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
for n in range(200):
    incident = "".join(alphabet[(n >> (5 * i)) % len(alphabet)] for i in range(6))
    sf.note_occurrence("FM-CRASH", incident, datetime.now(timezone.utc))
check("a crash loop is one message, not two hundred", len(sent) == 1, len(sent))
check("and the next one will say how many it stood in for",
      sf._alerted["FM-CRASH"][1] > 100, sf._alerted["FM-CRASH"][1])

reset()
sf.note_occurrence("CV-TOO-BIG", "BBBBBB", datetime.now(timezone.utc))
check("a refusal never alerts -- the bot was working", not sent, sent)

reset()
for n in range(sf.BURST_COUNT - 1):
    sf.note_occurrence("DL-ALL-ROUTES", f"AAAA{n:02d}".upper(), datetime.now(timezone.utc))
check("an ordinary fault stays quiet on its own", not sent, sent)
sf.note_occurrence("DL-ALL-ROUTES", "ZZZZZZ", datetime.now(timezone.utc))
check(f"but {sf.BURST_COUNT} of them in the window escalates", len(sent) == 1, sent)
check("and says why, so it does not read as one unlucky person",
      "looks like it is down" in sent[0]["message"], sent[0]["message"])

# ---------------------------------------------------------------------------
print("\n== the nightly report ==")
# ---------------------------------------------------------------------------
NOW = datetime(2026, 9, 13, 2, 14, tzinfo=timezone.utc)


def row(bot_id, code, incident, level, shared=False, seen=1, minutes=0):
    return {"reported_at": NOW - timedelta(minutes=minutes), "bot_id": bot_id, "code": code,
            "incident": incident, "occurred_at": NOW, "version": "1.7.0", "seen": seen,
            "shared_at": NOW if shared else None, "user_id": 7 if shared else None,
            "username": None, "user_lang": "en" if shared else None,
            "chat_kind": "private" if shared else None, "level": level}


rows = [
    row("convertbot", "CV-CRASH", "JNKS4N", 1, shared=True),
    row("downloaderbot", "DL-ALL-ROUTES", "AAAAAA", 2, minutes=30),
    row("downloaderbot", "DL-ALL-ROUTES", "BBBBBB", 2, minutes=31),
    row("downloaderbot", "DL-ALL-ROUTES", "CCCCCC", 2, minutes=32),
    row("downloaderbot", "DL-ALL-ROUTES", "DDDDDD", 2, minutes=33),
    row("downloaderbot", "DL-ALL-ROUTES", "EEEEEE", 2, minutes=34),
    row("convertbot", "CV-TIMEOUT", "FFFFFF", 2, shared=True, minutes=90),
    row("convertbot", "CV-TOO-BIG", "GGGGGG", 3, seen=4, minutes=120),
    row("stickerbot", "ST-TG-PACK-FULL", "HHHHHH", 3, minutes=200),
]
text = mb.build_problem_report(rows, 24)
print("\n----- rendered -----\n" + text + "\n--------------------\n")

check("urgent problems are listed one by one, with the incident",
      "JNKS4N" in text and "CV-CRASH" in text)
check("faults are grouped with a count rather than listed five times",
      "×5" in text and text.count("DL-ALL-ROUTES") == 1, text.count("DL-ALL-ROUTES"))
check("the loudest fault comes first",
      text.index("DL-ALL-ROUTES") < text.index("CV-TIMEOUT"))
check("refusals are only counted", "Refused — 2" in text, text)
check("and never listed one by one", "GGGGGG" not in text, text)
check("somebody who signed a report is marked", "🙋" in text)
check("with the incidents to look up", "FFFFFF" in text)
check("the bots are named the way every other message names them",
      "ConvertBot" in text and "convertbot" not in text, text[:200])
check("it points at where to go next", "/report" in text and "/reports" in text)

quiet = mb.build_problem_report([], 24)
check("a quiet day still sends, and says so", "Nothing at all" in quiet, quiet)
check("with no empty sections", "Urgent" not in quiet and "Faults" not in quiet, quiet)

only_refused = mb.build_problem_report([row("convertbot", "CV-TOO-BIG", "IIIIII", 3)], 24)
check("a day of nothing but refusals prints no urgent heading",
      "Urgent" not in only_refused, only_refused)
check("and does not claim nothing happened",
      "Nothing at all" not in only_refused, only_refused)

# A row written before the level column existed falls back rather than failing.
legacy = dict(row("anonbot", "AN-DELIVERY", "JJJJJJ", 2))
legacy["level"] = None
check("a row with no level is classified from its code",
      "Urgent" in mb.build_problem_report([legacy], 24))

print()
if failures:
    print(f"FAILURES ({len(failures)}):")
    for name in failures:
        print("  -", name)
    sys.exit(1)
print("problems are levelled, alerted once, and reported nightly")
