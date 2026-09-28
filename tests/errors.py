"""What counts as a crash, and what is just the network being the network.

Run from the repo root:  python tests/errors.py

Every bot reported telegram.error.NetworkError to the owner as a crash, with a
traceback, for what is a long poll losing its connection. This pins the
classification down -- including the trap that made it worth pinning: PTB's
BadRequest *subclasses* NetworkError, so the obvious isinstance check would
have silenced the faults most worth hearing about.

No database and no network: the modules under test are imported directly and
their event hook is replaced with a list.
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

CHILD = r'''
import asyncio, importlib, os, sys, logging
sys.path.insert(0, os.getcwd())
sys.path.insert(1, os.path.join(os.path.dirname(os.getcwd()), "shared"))
logging.disable(logging.CRITICAL)   # the blip path logs a warning by design

MODULE = os.environ["ERRORS_MODULE"]
m = importlib.import_module(MODULE)

from telegram.error import (
    BadRequest, Conflict, Forbidden, NetworkError, RetryAfter, TimedOut,
)

events = []
m.set_event_hook(lambda level, kind, message, details=None: events.append((level, kind, message)))

failures = []
def check(label, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + ("" if cond else f"  -- {detail}"))
    if not cond:
        failures.append(label)

# --- what is transient, and what is emphatically not --------------------
transient = [NetworkError("read error"), TimedOut(), RetryAfter(5)]
faults = [
    BadRequest("chat not found"),      # subclasses NetworkError -- the trap
    Conflict("terminated by other getUpdates"),
    Forbidden("bot was blocked"),
    ValueError("a plain bug"),
]
for exc in transient:
    check(f"{type(exc).__name__} is transient", m.is_transient_network_error(exc))
for exc in faults:
    check(f"{type(exc).__name__} is NOT transient", not m.is_transient_network_error(exc))

# --- a blip raises no alert and no crash count --------------------------
class Ctx:
    def __init__(self, error): self.error = error

before_errors = m._error_count
asyncio.run(m.error_handler(None, Ctx(NetworkError("httpx.ReadError"))))
check("a blip emits no event", events == [], events)
check("a blip is not counted as an error", m._error_count == before_errors, m._error_count)
check("a blip is counted as a blip", m._network_blips_total == 1, m._network_blips_total)
check("/status mentions it as transient",
      "transient network error" in m.error_summary(), m.error_summary())

# --- a real fault still alerts -----------------------------------------
asyncio.run(m.error_handler(None, Ctx(BadRequest("chat not found"))))
check("BadRequest still emits a crash event",
      [e for e in events if e[1] == "crash"], events)
check("BadRequest is counted", m._error_count == before_errors + 1, m._error_count)

# --- a sustained run says something, once ------------------------------
events.clear()
m._network_blips = 0
m._network_alerted = False
for _ in range(m.NETWORK_ALERT_AFTER + 5):
    asyncio.run(m.error_handler(None, Ctx(NetworkError("still down"))))
warnings = [e for e in events if e[1] == "network"]
check("a sustained outage warns exactly once", len(warnings) == 1, warnings)
check("and it is a warning, not a crash", warnings and warnings[0][0] == "warning", warnings)

# --- and recovery clears it --------------------------------------------
events.clear()
m.note_network_ok()
check("recovery is announced once", len(events) == 1 and events[0][1] == "network", events)
check("the streak is cleared", m._network_blips == 0, m._network_blips)
events.clear()
m.note_network_ok()
check("a second quiet update says nothing more", events == [], events)

print("FAILURES:", len(failures), failures or "")
sys.exit(1 if failures else 0)
'''


def main() -> int:
    import subprocess
    failures = 0
    targets = [
        ("anon_bot", "shared_features"),
        ("sticker_bot", "shared_features"),
        ("manager_bot", "monitoring"),
    ]
    for folder, module in targets:
        print(f"== {folder} ({module}.py) ==")
        env = dict(os.environ, ERRORS_MODULE=module, PYTHONIOENCODING="utf-8")
        proc = subprocess.run(
            [sys.executable, "-c", CHILD], cwd=ROOT / folder, env=env,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        print(proc.stdout.rstrip())
        if proc.returncode:
            print(proc.stderr.rstrip()[-1500:])
            failures += 1
    print("\nFAILURES:", failures)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
