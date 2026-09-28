"""Import ManagerBot for real (with a stubbed telegram + database) and check
the /ping report: the arithmetic, the units, and what it says when a bot
never answers."""
import os, sys
from datetime import datetime, timedelta, timezone

# The rendered report is printed in full, and it has emoji in it. A Windows
# console is cp1252 unless told otherwise, and would fail on the ping bat
# rather than on anything being wrong -- the same guard the older suites
# open with.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# fakedb first: it shadows ManagerBot's own db.py, which this machine's
# Python 3.9 cannot even parse (the deployed bots run 3.11+).
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".mirror", "manager_bot"))
sys.path.insert(0, os.path.join(HERE, "stubs"))

os.environ.setdefault("MBOT_TOKEN", "x")
os.environ.setdefault("MBOT_USERNAME", "x")
os.environ.setdefault("MBOT_ADMIN_ID", "1")
os.environ.setdefault("DATABASE_URL", "postgresql://stub")

import bot as pb

failures = []
def equals(name, got, want):
    """Named for what it does, because the rest of this suite spells an
    assertion check(name, ok, detail) -- and a comparison written in that
    shape by mistake passes whenever `got` is truthy, which is a test that
    reports success without testing anything."""
    if got != want:
        failures.append(f"{name}: got {got!r}, want {want!r}")
    else:
        print(f"  ok  {name}")

def contains(name, haystack, needle):
    equals(name, needle in haystack, True)

# ---- _ms takes both a timedelta and a plain number of milliseconds ----
equals("timedelta under a second reads in ms", pb._ms(timedelta(milliseconds=41)).strip(), "41 ms")
equals("timedelta over a second reads in s", pb._ms(timedelta(seconds=1.5)).strip(), "1.50 s")
equals("a bare number is milliseconds", pb._ms(31.4).strip(), "31 ms")
equals("a bare number over 1000 becomes seconds", pb._ms(2500).strip(), "2.50 s")
equals("nothing measured says so", pb._ms(None).strip(), "?")

# ---- a full report ----
t0 = datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc)
trace = {
    "bot": {"id": "stickerbot", "name": "StickerBot"},
    "here": {"db_ms": 11.2, "skew_ms": 2.0, "where": "Local (mba)"},
    "to_manager": timedelta(milliseconds=380),
    "ack_ms": 31.0,
    "queue_ms": 12.5,
}
result = {
    "status": "done", "ok": True,
    "output": '{"db_ms":8.4,"skew_ms":-1.0,"where":"Railway","up":"3h 2m"}',
    "created_at": t0,
    "claimed_at": t0 + timedelta(milliseconds=9),
    "finished_at": t0 + timedelta(milliseconds=15),
    "taken_at": t0 + timedelta(milliseconds=29),
}
text = pb._render_ping(trace, result)
print("\n----- rendered -----\n" + text + "\n--------------------\n")

contains("names the bot", text, "StickerBot")
contains("reports the bus round trip", text, "29 ms")
contains("shows the queue wait", text, "9 ms")
contains("shows the far end answering", text, "6 ms")
contains("shows the result coming back", text, "14 ms")
contains("shows the locally measured ack", text, "31 ms")
contains("shows the queue write", text, "12 ms")
contains("marks the Telegram leg as approximate", text, "±1 s")
contains("says where ManagerBot is", text, "Local (mba)")
contains("says where the far end is", text, "Railway")
contains("reports each end's distance from Supabase", text, "Supabase 8 ms")
contains("reports clock skew", text, "clock -1 ms")
contains("reports uptime", text, "3h 2m")

# the legs must add up to the total
legs = (result["claimed_at"] - result["created_at"]) + \
       (result["finished_at"] - result["claimed_at"]) + \
       (result["taken_at"] - result["finished_at"])
equals("the three bus legs sum to the bus total", legs, result["taken_at"] - result["created_at"])

# ---- a bot that never answered ----
timeout = dict(result, status="timeout", claimed_at=None, finished_at=None, output=None)
text = pb._render_ping(trace, timeout)
contains("a timeout says the process is not running", text, "not running")
equals("a timeout does not invent numbers", "bus round trip" in text, False)

# ---- a far end that could not reach the database still reports ----
broken = dict(result, output='{"error":"OperationalError: no route to host"}')
text = pb._render_ping(trace, broken)
contains("a failing probe is shown, not swallowed", text, "no route to host")

# ---- what a user waits for, and the order the family ping is read in ----
# The bus figure is ManagerBot's own path and nobody else's. The owner asked
# for the number a person would recognise, and for the summary to be ordered
# by it rather than by the alphabet.
with_user = dict(result, output=(
    '{"db_ms":8.4,"skew_ms":-1.0,"where":"Railway","up":"3h 2m",'
    '"user":{"info_ms":18.0,"telegram_ms":62.0,"total_ms":80.0,"info_query":"user_settings"}}'))
text = pb._render_ping(trace, with_user)
contains("reports the info read", text, "18 ms")
contains("reports the send to Telegram", text, "62 ms")
contains("reports what an info command costs here", text, "80 ms")
contains("says the user's own network is not in it", text, "cannot be measured")
equals("a bot that did not measure it prints no such block",
      "What a user waits for" in pb._render_ping(trace, result), False)

equals("the user figure is read out of the answer", pb._user_wait_ms(with_user), 80.0)
equals("and is None when the bot is too old to send one", pb._user_wait_ms(result), None)

rows = {
    "AnonBot": {"user": 300.0, "bus": 700.0, "note": ""},
    "ConvertBot": {"user": 90.0, "bus": 9800.0, "note": ""},
    "DownloaderBot": {"user": None, "bus": None, "note": "no answer — down"},
    "StickerBot": {"user": 140.0, "bus": 300.0, "note": ""},
}
order = [line.split()[0] for line in pb._summary_lines(rows)]
equals("fastest first, not alphabetical", order, ["ConvertBot", "StickerBot", "AnonBot", "DownloaderBot"])
equals("a bot that never answered is last", order[-1], "DownloaderBot")
contains("each line carries both numbers", pb._summary_lines(rows)[0], "user")
contains("including the bus one", pb._summary_lines(rows)[0], "bus")

older = {"OldBot": {"user": None, "bus": 50.0, "note": ""},
         "NewBot": {"user": 400.0, "bus": 600.0, "note": ""}}
equals("a bot with no user figure sorts after one that has it",
      [line.split()[0] for line in pb._summary_lines(older)], ["NewBot", "OldBot"])

# ---- /errors with no bot named goes to all of them ----
# The answers arrive one at a time over the bus, and the message is rewritten
# as each lands -- so what is checked is what the message says at each stage,
# not just at the end.
group = {"live": None, "title": "Errors", "command": "errors",
         "answers": {}, "queued": {c["id"] for c in pb.CHILDREN}}
text = pb._fanout_text(group)
contains("with nothing back yet it says how many it is waiting on", text,
         f"waiting on {len(pb.CHILDREN)} more")

first = pb.CHILDREN[0]
group["answers"][first["id"]] = ("✅", "No errors since this instance started.")
text = pb._fanout_text(group)
contains("the first answer appears under that bot's name", text, first["name"])
contains("with what it said", text, "No errors since")
contains("and the count of the rest comes down", text,
         f"waiting on {len(pb.CHILDREN) - 1} more")

for child in pb.CHILDREN[1:]:
    group["answers"][child["id"]] = ("⚠️", f"1 error in {child['name']}")
text = pb._fanout_text(group)
equals("when everything is in, nothing is said about waiting", "waiting on" in text, False)
equals("every bot has a section", all(child["name"] in text for child in pb.CHILDREN), True)
order = [text.index(child["name"]) for child in pb.CHILDREN]
equals("in the order the family is declared in, so the report reads the same twice",
      order == sorted(order), True)

# A bot that never answered, and one too old to have been asked.
group["answers"][pb.CHILDREN[1]["id"]] = ("⏳", "no answer in 90s — that bot looks down.")
contains("a bot that did not answer says so where its errors would be",
         pb._fanout_text(group), "looks down")
stale = {"live": None, "title": "Errors", "command": "errors", "queued": set(),
         "answers": {pb.CHILDREN[0]["id"]: ("⏳", "on 1.5.0 — too old for this command")}}
contains("a bot too old for the command is named with its version rather than queued",
         pb._fanout_text(stale), "1.5.0")
equals("and nothing is said about waiting for it", "waiting on" in pb._fanout_text(stale), False)

# The bound on the table that remembers which group a queued id belongs to.
pb._FANOUTS.clear()
for i in range(pb.MAX_FANOUTS + 10):
    pb._remember_fanout(i, group)
equals("the fan-out table is bounded", len(pb._FANOUTS), pb.MAX_FANOUTS)
equals("and it is the oldest that go", 0 in pb._FANOUTS, False)

# ---- the trace table stays bounded ----
pb._PING_TRACES.clear()
for i in range(pb.MAX_PING_TRACES + 20):
    pb._remember_trace(i, {"bot": trace["bot"]})
equals("ping traces are bounded", len(pb._PING_TRACES), pb.MAX_PING_TRACES)
equals("and it is the oldest that go", 0 in pb._PING_TRACES, False)

print()
if failures:
    print("FAILURES:")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("all /ping checks passed")
