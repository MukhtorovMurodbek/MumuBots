"""How loudly a problem is answered, and what the nightly report says.

Three things are checked here and they are the three that decide whether the
owner's phone is useful or ignorable:

  * every code lands in exactly one level, and the urgent set is the small one
  * an urgent problem alerts at once, a crash loop alerts once, and a flood of
    ordinary faults escalates by itself
  * the nightly report lists urgent problems, groups faults, and names the
    rest by what kind of thing happened -- never "refused" (2.1.0: two
    AnonBot messages sent without a swipe-reply were reported as "Refused",
    and the owner read it as people being turned away)
  * and it carries the feedback the owner asked to get there

No database and no Telegram: the alerting reads a clock it is handed and
writes to a captured event hook, and the report builder is pure.
"""
import html
import inspect
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

print("\n== the third level, by kind ==")
# getattr throughout, so a version without kinds fails these checks one by
# one rather than stopping the file at the first missing name.
KINDS = getattr(pr, "KINDS", {})
KIND_LABELS = getattr(pr, "KIND_LABELS", {})
kind = getattr(pr, "kind", lambda code: None)
in_kinds = [code for codes in KINDS.values() for code in codes]
check("every code the bot explains itself is in a kind",
      not (pr.SIMPLE - set(in_kinds)), sorted(pr.SIMPLE - set(in_kinds)))
check("and only those", not (set(in_kinds) - pr.SIMPLE), sorted(set(in_kinds) - pr.SIMPLE))
check("and each in exactly one", len(in_kinds) == len(set(in_kinds)),
      sorted(c for c in set(in_kinds) if in_kinds.count(c) > 1))
check("every kind has an icon, a heading and a meaning, in the order the report uses",
      bool(KINDS) and set(getattr(pr, 'KIND_ORDER', ())) == set(KINDS) == set(KIND_LABELS)
      and all(len(label) == 3 and all(label) for label in KIND_LABELS.values()))
check("not swiping to reply is being shown how, not being refused",
      kind("AN-NEEDS-REPLY") == "guided" and kind("AN-AMBIGUOUS") == "guided")
check("an unknown command is too", kind("FM-UNKNOWN-COMMAND") == "guided")
check("a file over the limit is a limit", kind("CV-TOO-BIG") == "limit")
check("a reaction a bot cannot show is Telegram's limit", kind("AN-REACTION-NOT-PASSED") == "limit")
check("being blocked is the other person's choice", kind("AN-SENDER-BLOCKED") == "other_side")
check("an old button is out of date", kind("DL-BUTTON-EXPIRED") == "gone")
check("a fault has no kind", bool(KINDS) and kind("DL-ALL-ROUTES") is None and kind("FM-CRASH") is None)
check("no kind is called refused",
      not any("refus" in (heading + meaning).lower() for _, heading, meaning in KIND_LABELS.values())
      and "refus" not in pr.LEVEL_NAMES[pr.REFUSED])

# ---------------------------------------------------------------------------
print("\n== who gets woken up ==")
# ---------------------------------------------------------------------------
sys.path.insert(0, str(HERE / ".mirror" / "anon_bot"))
for name in ("problems", "bot", "db", "i18n", "family_link", "shared_features",
             "lifecycle", "live_message"):
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
    # The night the owner asked about: two messages that were not replies.
    row("anonbot", "AN-NEEDS-REPLY", "KKKKKK", 3, minutes=300),
    row("anonbot", "AN-NEEDS-REPLY", "LLLLLL", 3, minutes=301),
]
text = mb.build_problem_report(rows, 24)
print("\n----- rendered -----\n" + text + "\n--------------------\n")

check("urgent problems are listed one by one, with the incident",
      "JNKS4N" in text and "CV-CRASH" in text)
check("faults are grouped with a count rather than listed five times",
      "×5" in text and text.count("DL-ALL-ROUTES") == 1, text.count("DL-ALL-ROUTES"))
check("the loudest fault comes first",
      text.index("DL-ALL-ROUTES") < text.index("CV-TIMEOUT"))
check("nothing is called refused any more", "refus" not in text.lower(), text)
check("a message that was not a reply is reported as somebody shown how",
      "Shown how to use it — 2" in text, text)
check("by its code, its count and what it means",
      "AN-NEEDS-REPLY</code> ×2 — Message did not say which conversation" in text, text)
check("a file over the limit and a full pack are limits, together",
      "Hit a limit — 2" in text and "CV-TOO-BIG" in text and "ST-TG-PACK-FULL" in text, text)
check("each kind says what it means",
      bool(KIND_LABELS) and html.escape(KIND_LABELS["guided"][2]) in text, text)
check("and those are still never listed one incident at a time",
      not any(i in text for i in ("GGGGGG", "HHHHHH", "KKKKKK", "LLLLLL")), text)
check("faults come before the bot being right",
      "Shown how to use it" in text and text.index("Faults") < text.index("Shown how to use it"))
check("somebody who signed a report is marked", "🙋" in text)
check("with the incidents to look up", "FFFFFF" in text)
check("the bots are named the way every other message names them",
      "ConvertBot" in text and "convertbot" not in text, text[:200])
check("it points at where to go next", "/report" in text and "/reports" in text)

quiet = mb.build_problem_report([], 24)
check("a quiet day still sends, and says so", "Nothing at all" in quiet, quiet)
check("with no empty sections", "Urgent" not in quiet and "Faults" not in quiet, quiet)

only_refused = mb.build_problem_report([row("convertbot", "CV-TOO-BIG", "IIIIII", 3)], 24)
check("a day of nothing but explained problems prints no urgent heading",
      "Urgent" not in only_refused, only_refused)
check("and does not claim nothing happened",
      "Nothing at all" not in only_refused, only_refused)

many = [row("anonbot", code, f"M{n:05d}", 3)
        for n, code in enumerate(sorted(KINDS.get("guided", ()))[:9])]
crowded = mb.build_problem_report(many, 24)
check(f"a kind lists {getattr(mb, 'MAX_CODES_PER_KIND', '?')} codes and sums the rest into one line",
      crowded.count("<code>AN-") + crowded.count("<code>FM-") + crowded.count("<code>ST-")
      + crowded.count("<code>CV-") + crowded.count("<code>DL-") == getattr(mb, "MAX_CODES_PER_KIND", -1)
      and "+3 more, under 3 other code(s)" in crowded, crowded)

print("\n== feedback in the report ==")
fb = [{"id": 7, "bot_id": "convertbot", "lang": "uz", "created_at": NOW,
       "delivered_at": None, "body": "Make <b>HEIC</b> faster & cheaper"},
      {"id": 8, "bot_id": "anonbot", "lang": "en", "created_at": NOW,
       "delivered_at": None, "body": "x" * (getattr(mb, "FEEDBACK_SHOWN_CHARS", 3000) + 50)}]
try:
    with_fb = mb.build_problem_report([], 24, fb)
except TypeError:
    with_fb = ""  # a report that takes no feedback
check("feedback asked for at night is in the report", "💬 <b>Feedback — 2</b>" in with_fb, with_fb)
check("with where it came from and in which language", "ConvertBot · uz" in with_fb, with_fb)
check("and its words escaped, never markup", "&lt;b&gt;HEIC&lt;/b&gt; faster &amp; cheaper" in with_fb,
      with_fb)
check("a long one is cut, with where to read the rest",
      "/feedback 8</code> for the rest" in with_fb and "x" * (getattr(mb, "FEEDBACK_SHOWN_CHARS", 3000) + 1) not in with_fb)
check("and nothing about who sent it",
      not any(w in with_fb for w in ("user_id", "username", "@")), with_fb)
try:
    noted = mb.build_problem_report([], 24, feedback_note="3 feedback message(s), each sent as it came")
except TypeError:
    noted = ""
check("when it already went out, a line says so instead", "💬 3 feedback message(s)" in noted, noted)

# A row written before the level column existed falls back rather than failing.
legacy = dict(row("anonbot", "AN-DELIVERY", "JJJJJJ", 2))
legacy["level"] = None
check("a row with no level is classified from its code",
      "Urgent" in mb.build_problem_report([legacy], 24))

print("\n== download routes in the report (the owner, 2026-10-07) ==")
T = 1_800_000_000.0
DAY = 86400.0
routes = {
    "routewatch:last_run": f"{T - 5 * 3600:.0f}",
    "routewatch:state:instagram": f"up|{T - 9 * DAY:.0f}|0",
    "routewatch:state:tiktok": f"last_resort|{T - 2 * DAY:.0f}|{T - 2 * DAY:.0f}",
    "routewatch:state:twitter": f"down|{T - 3600:.0f}|{T - 3600:.0f}",
    "routewatch:state:youtube": f"down|{T - 40 * DAY:.0f}|0",
    "routewatch:ytdlp_alerted": "2026.09.30",
}
with_routes = mb.build_problem_report([], 24, routes=routes, now=T)
check("the routes are in the report, with when they were checked",
      "Download routes</b> · checked 5h ago" in with_routes, with_routes)
check("every platform's standing, YouTube's included (never pushed, so this is where it is seen)",
      all(name in with_routes for name in ("Instagram ✅", "TikTok ⚠️", "X ❌", "YouTube ❌")), with_routes)
check("trouble older than a day says for how long", "last route only for 2d" in with_routes
      and "YouTube ❌ down for 40d" in with_routes, with_routes)
check("trouble younger than a day does not", "X ❌ down ·" in with_routes, with_routes)
check("in the platforms' usual order", with_routes.index("Instagram") < with_routes.index("TikTok")
      < with_routes.index("X ❌") < with_routes.index("YouTube"), with_routes)
check("the yt-dlp bookkeeping key is not shown as a platform", "ytdlp" not in with_routes, with_routes)
stale = dict(routes, **{"routewatch:last_run": f"{T - 3 * DAY:.0f}"})
check("a check that stopped running says so",
      "has stopped running" in mb.build_problem_report([], 24, routes=stale, now=T))
check("a check that never ran says how to run it",
      "not checked yet" in mb.build_problem_report([], 24, routes={}, now=T))
check("a damaged record is skipped, not a crash",
      "Instagram ✅" in mb.build_problem_report(
          [], 24, routes=dict(routes, **{"routewatch:state:pinterest": "garbage"}), now=T))
check("without routes the section is left out, as before",
      "Download routes" not in mb.build_problem_report([], 24))
check("both the 22:00 report and /nightly pass the routes in",
      inspect.getsource(mb.nightly_problem_report).count("routes=routes") >= 2
      and "routes=routes" in inspect.getsource(mb.nightly_command))

print()
if failures:
    print(f"FAILURES ({len(failures)}):")
    for name in failures:
        print("  -", name)
    sys.exit(1)
print("problems are levelled, alerted once, and reported nightly")
