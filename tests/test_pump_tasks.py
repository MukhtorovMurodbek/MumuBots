"""ManagerBot's 1 s tick decides whether to sweep; the sweep runs beside it.

Seen in production on 2026-10-10, after the 2.1.0C release: `_event_backstop`
"skipped: maximum number of running instances" every ~30.5 s, ~2,800 warning
lines a day. The idle event sweep now does two database round trips in a row
(events, then feedback), so together they sometimes outlast the 1 s tick; the
tick was awaiting the sweep, so the scheduler skipped the ticks that fell
inside it -- harmless, but the very line a real stall prints (2026-10-09).

So the tick must hand the sweep to a task of its own and return at once, never
start a second sweep on top of a running one, say so (once) when one has run
suspiciously long, and keep the two pumps (results, events) apart.

It also keeps, in memory, how long each sweep took, and the /status board shows
a line with the slowest event sweep of the last 15 minutes and since start, so a
sweep that is slow every time (the database round trip from Railway) can be told
from a one-off.

The real `_pump_if_due` is driven with a fake context and clock; no database,
no Telegram. Run through tests/run.py, which builds the .mirror folders.
"""
import ast
import asyncio
import inspect
import logging
import os
import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / ".mirror" / "manager_bot"))
sys.path.insert(0, str(HERE / "stubs"))
for var in ("MBOT",):
    os.environ[f"{var}_TOKEN"] = "test"
    os.environ[f"{var}_USERNAME"] = "test"
    os.environ[f"{var}_ADMIN_ID"] = "1"
os.environ["DATABASE_URL"] = "postgresql://stub"
os.environ["MBOT_REGISTRY"] = ""

import bot as mbot  # noqa: E402

failures = []


def check(name, ok, detail=""):
    print(("  ok  " if ok else "  FAIL  ") + name + ("" if ok else f"  -- {detail}"))
    if not ok:
        failures.append(name)


class Captured(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.records = []

    def emit(self, record):
        self.records.append(record)


class FakeApplication:
    """What the real one does that matters here: a task it keeps and returns."""

    def __init__(self):
        self.created = []

    def create_task(self, coro, *, update=None, name=None):
        task = asyncio.get_running_loop().create_task(coro, name=name)
        self.created.append(name)
        return task


class Ctx:
    def __init__(self):
        self.application = FakeApplication()


class Clock:
    def __init__(self):
        self.now = 1000.0

    def monotonic(self):
        return self.now


def fresh(clock):
    """Pristine state, a fake clock, an idle bus."""
    mbot._pump_tasks.clear()
    mbot._pump_started.clear()
    mbot._pump_stall_logged.clear()
    mbot._sweep_recent.clear()
    mbot._sweep_slowest.clear()
    mbot._last_pumped.update(result=0.0, event=0.0)
    mbot.time_module = clock
    mbot.family_link.bus_is_active = lambda: False
    mbot.PUMP_STALL_WARN_SECONDS = 60


async def scenario():
    clock = Clock()
    fresh(clock)
    ctx = Ctx()
    log = Captured()
    mbot.logger.addHandler(log)
    mbot.logger.setLevel(logging.DEBUG)

    calls = {"event": 0, "result": 0}
    release = asyncio.Event()

    async def slow_event_pump(context):
        calls["event"] += 1
        await release.wait()

    async def quick_result_pump(context):
        calls["result"] += 1

    # 1. The tick returns at once however long the sweep takes.
    real = time.perf_counter()
    try:
        # a tick that awaits the sweep never comes back: fail, do not hang
        await asyncio.wait_for(mbot._pump_if_due(ctx, "event", slow_event_pump, 2, 30), 1.0)
    except asyncio.TimeoutError:
        check("the tick returns at once while the sweep is still running", False, "it waited for the sweep")
        mbot.logger.removeHandler(log)
        release.set()
        return
    took = time.perf_counter() - real
    check("the tick returns at once while the sweep is still running",
          took < 0.05 and not release.is_set(), f"took {took:.3f}s")
    await asyncio.sleep(0)
    check("the sweep did start, once, as a task of the application",
          calls["event"] == 1 and ctx.application.created == ["event-sweep"],
          f"{calls} {ctx.application.created}")

    # 2. Ticks that fall inside the sweep neither wait for it nor start another,
    #    even when the interval says another is due.
    for _ in range(5):
        clock.now += 31
        mbot._last_pumped["event"] = 0.0           # long overdue by any clock
        started = time.perf_counter()
        await mbot._pump_if_due(ctx, "event", slow_event_pump, 2, 30)
        check_fast = time.perf_counter() - started < 0.05
        if not check_fast:
            break
    await asyncio.sleep(0)
    check("ticks inside a sweep return at once", check_fast)
    check("and never start a second sweep on top of it", calls["event"] == 1, str(calls))

    # 3. A sweep running past the warn limit is named, once.
    stalls = [r for r in log.records if "sweep has been running" in r.getMessage()]
    check("a sweep stuck for over a minute is named in the log, once",
          len(stalls) == 1 and "event" in stalls[0].getMessage(), str([r.getMessage() for r in stalls]))
    check("the stall line carries numbers only",
          all(r.levelno == logging.WARNING for r in stalls))

    # 4. The other pump is not held up by the stuck one.
    clock.now += 1
    await mbot._pump_if_due(ctx, "result", quick_result_pump, 2, 30)
    await asyncio.sleep(0)
    check("the result pump runs while the event sweep is stuck", calls["result"] == 1, str(calls))

    # 5. When the sweep ends, the next due tick starts the next one -- and only then.
    release.set()
    for _ in range(3):
        await asyncio.sleep(0)
    mbot._last_pumped["event"] = clock.now          # just swept
    await mbot._pump_if_due(ctx, "event", slow_event_pump, 2, 30)
    await asyncio.sleep(0)
    check("not due yet: no new sweep", calls["event"] == 1, str(calls))
    clock.now += 29
    await mbot._pump_if_due(ctx, "event", slow_event_pump, 2, 30)
    await asyncio.sleep(0)
    check("still inside the idle interval (30 s): no new sweep", calls["event"] == 1, str(calls))
    clock.now += 2
    await mbot._pump_if_due(ctx, "event", slow_event_pump, 2, 30)
    await asyncio.sleep(0)
    check("past the idle interval: the next sweep starts", calls["event"] == 2, str(calls))

    # 6. An active bus switches to the fast interval, as before.
    release.set()
    for _ in range(3):
        await asyncio.sleep(0)
    mbot.family_link.bus_is_active = lambda: True
    clock.now += 2.5
    before = calls["event"]
    await mbot._pump_if_due(ctx, "event", slow_event_pump, 2, 30)
    await asyncio.sleep(0)
    check("while the bus is active the fast interval (2 s) applies", calls["event"] == before + 1, str(calls))

    # 7. A sweep that raises ends its task; the next due tick is not blocked by it.
    release.set()
    for _ in range(3):
        await asyncio.sleep(0)

    async def raising(context):
        raise RuntimeError("database gone")

    mbot.family_link.bus_is_active = lambda: False
    clock.now += 31
    await mbot._pump_if_due(ctx, "event", raising, 2, 30)
    await asyncio.sleep(0.01)
    task = mbot._pump_tasks["event"]
    check("a sweep that raises is finished, not left blocking", task.done())
    task.exception()                                  # mark retrieved; the real one goes to the error handler
    clock.now += 31
    before = calls["event"]
    await mbot._pump_if_due(ctx, "event", slow_event_pump, 2, 30)
    await asyncio.sleep(0)
    check("and the next due tick starts a fresh sweep", calls["event"] == before + 1, str(calls))
    release.set()
    await asyncio.sleep(0.01)
    mbot.logger.removeHandler(log)


async def sweep_timing_scenario():
    """How long the sweeps take, and the /status line about it. The clock is
    fake: a sweep starts at one time and ends at another."""
    clock = Clock()
    fresh(clock)
    ctx = Ctx()
    gates = {}

    def held(key, boom=False):
        async def pump(context):
            await gates[key].wait()
            if boom:
                raise RuntimeError("database gone")
        return pump

    async def sweep(key, started, seconds, boom=False):
        """Starts a sweep at `started` and lets it end `seconds` later."""
        gates[key] = asyncio.Event()
        clock.now = started
        mbot._last_pumped[key] = 0.0
        await mbot._pump_if_due(ctx, key, held(key, boom), 2, 30)
        await asyncio.sleep(0)
        clock.now = started + seconds
        gates[key].set()
        for _ in range(3):
            await asyncio.sleep(0)
        if boom:
            mbot._pump_tasks[key].exception()         # mark retrieved; the real one goes to the error handler

    NONE_YET = "⏱ Event sweep: none finished yet since start"

    # 9. Before any sweep has finished, and while one is still running.
    check("no sweep finished yet: the line says so", mbot._sweep_line() == NONE_YET, mbot._sweep_line())
    gates["event"] = asyncio.Event()
    clock.now = 1000.0
    await mbot._pump_if_due(ctx, "event", held("event"), 2, 30)
    await asyncio.sleep(0)
    clock.now = 1050.0
    check("a sweep still running is not counted", mbot._sweep_line() == NONE_YET and not mbot._sweep_recent)
    clock.now = 1000.4                                 # it ends 0.4 s after it started
    gates["event"].set()
    for _ in range(3):
        await asyncio.sleep(0)

    # 10. A sweep that ends records its duration, read from the clock.
    entries = list(mbot._sweep_recent.get("event", ()))
    check("a sweep that ends is recorded once, with its duration from the clock",
          len(entries) == 1 and abs(entries[0][1] - 0.4) < 1e-6 and entries[0][0] == 1000.4, str(entries))
    check("the first sweep reads as 0.40 s, in the window and since start",
          mbot._sweep_line() == "⏱ Event sweep: slowest 0.40 s in the last 15 min (1 sweep) · 0.40 s since start",
          mbot._sweep_line())

    # 11. The window counts sweeps and shows the slowest of them, not the last.
    await sweep("event", 1100.0, 2.5)
    await sweep("event", 1200.0, 0.1)
    check("the window counts sweeps and names the slowest, not the latest",
          mbot._sweep_line() == "⏱ Event sweep: slowest 2.50 s in the last 15 min (3 sweeps) · 2.50 s since start",
          mbot._sweep_line())

    # 12. An entry older than 900 s leaves the window but stays as "since start".
    clock.now = 1102.5 + 901                           # 901 s after the 2.5 s sweep finished
    check("an entry older than 15 min leaves the window but stays as the slowest since start",
          mbot._sweep_line() == "⏱ Event sweep: slowest 0.10 s in the last 15 min (1 sweep) · 2.50 s since start",
          mbot._sweep_line())

    # 13. The result pump's timings are its own.
    await sweep("result", 2050.0, 9.0)
    check("the result pump's timings do not appear in the event line",
          mbot._sweep_line() == "⏱ Event sweep: slowest 0.10 s in the last 15 min (1 sweep) · 2.50 s since start"
          and "9.00" not in mbot._sweep_line(), mbot._sweep_line())
    check("the result pump keeps its own, named by its key",
          mbot._sweep_line("result")
          == "⏱ Result sweep: slowest 9.00 s in the last 15 min (1 sweep) · 9.00 s since start",
          mbot._sweep_line("result"))

    # 14. A sweep that raises is recorded like any other (it ended), and old entries are dropped.
    await sweep("event", 2100.0, 3.0, boom=True)
    check("a sweep that raises is still recorded",
          mbot._sweep_line() == "⏱ Event sweep: slowest 3.00 s in the last 15 min (1 sweep) · 3.00 s since start",
          mbot._sweep_line())
    check("recording a sweep drops entries older than the window (memory stays small)",
          len(mbot._sweep_recent["event"]) == 1, str(list(mbot._sweep_recent["event"])))

    # 15. The window edge: 900 s old is still in, past it is out; the slowest since start stays.
    await sweep("event", 3100.0, 1.0)                  # finished at 3101.0
    clock.now = 3101.0 + 900
    check("exactly 15 min old is still in the window",
          mbot._sweep_line() == "⏱ Event sweep: slowest 1.00 s in the last 15 min (1 sweep) · 3.00 s since start",
          mbot._sweep_line())
    clock.now = 3101.0 + 901
    check("none in the window: the line says so and keeps the slowest since start",
          mbot._sweep_line() == "⏱ Event sweep: none in the last 15 min · slowest since start 3.00 s",
          mbot._sweep_line())

    # 16. A sweep that is cancelled (shutdown) ended too.
    gates["event"] = asyncio.Event()
    clock.now = 6000.0
    mbot._last_pumped["event"] = 0.0
    await mbot._pump_if_due(ctx, "event", held("event"), 2, 30)
    await asyncio.sleep(0)
    clock.now = 6002.0
    mbot._pump_tasks["event"].cancel()
    for _ in range(3):
        await asyncio.sleep(0)
    check("a sweep that is cancelled is recorded too",
          mbot._sweep_line() == "⏱ Event sweep: slowest 2.00 s in the last 15 min (1 sweep) · 3.00 s since start",
          mbot._sweep_line())

    # 17. The note itself: never raises, never records less than nothing.
    try:
        mbot._note_sweep("probe", None)
        raised = False
    except Exception:
        raised = True
    check("noting a sweep never raises, even for a start time it cannot use", not raised)
    mbot._note_sweep("probe", clock.now + 5)           # a start "after" the end (a clock oddity)
    check("a negative duration is recorded as 0.00 s",
          mbot._sweep_line("probe")
          == "⏱ Probe sweep: slowest 0.00 s in the last 15 min (1 sweep) · 0.00 s since start",
          mbot._sweep_line("probe"))
    check("the line has no HTML special characters (the board is HTML)",
          all(ch not in mbot._sweep_line() for ch in "<>&"), mbot._sweep_line())


class FakeBoard:
    """A minimal db.status_snapshot: no beats, nothing active, a database, alerts on."""

    @staticmethod
    def status_snapshot(schemas, since):
        return {"beats": [], "active": {}, "alerts": "on",
                "database": {"name": "family", "version": "16.2", "size": "12 MB"}}


def board_scenario():
    real = mbot.db.status_snapshot
    mbot.db.status_snapshot = FakeBoard.status_snapshot
    try:
        line = "⏱ Event sweep: slowest 0.40 s in the last 15 min (1 sweep) · 0.40 s since start"
        rows = mbot._build_board(line).split("\n")
        db_at = next((i for i, row in enumerate(rows) if row.startswith("🗄 Database")), -1)
        check("the sweep line is on its own line right after the database line",
              db_at >= 0 and rows[db_at + 1] == line, str(rows[-4:]))
        check("and before the alerts line",
              db_at >= 0 and rows[db_at + 2].startswith("🔔 Alerts") and rows.count(line) == 1, str(rows[-4:]))
        bare = mbot._build_board().split("\n")
        db_at = next((i for i, row in enumerate(bare) if row.startswith("🗄 Database")), -1)
        check("with no sweep line the board is as before (database line, then alerts)",
              db_at >= 0 and bare[db_at + 1].startswith("🔔 Alerts") and len(bare) == len(rows) - 1,
              str(bare[-4:]))
    finally:
        mbot.db.status_snapshot = real


asyncio.run(scenario())
asyncio.run(sweep_timing_scenario())
board_scenario()

# 8. Source: both backstops still go through the one gate, and nothing awaits a pump inline.
src = (HERE / ".mirror" / "manager_bot" / "bot.py").read_text(encoding="utf-8")
check("both backstops go through _pump_if_due",
      'await _pump_if_due(context, "result", result_pump' in src
      and 'await _pump_if_due(context, "event", event_pump' in src)
check("no pump is awaited inline inside the tick any more", "    await pump(context)\n" not in src)

# 18. Source: the sweep timings are read on the event loop and handed to the board, never read in its thread.
whole = inspect.getsource(mbot)
check("all three places that build the board pass it _sweep_line()",
      whole.count("to_thread(_build_board, _sweep_line())") == 3, str(whole.count("to_thread(_build_board, _sweep_line())")))
check("and none builds it without",
      "to_thread(_build_board)" not in whole)
used = {node.id for node in ast.walk(ast.parse(inspect.getsource(mbot._build_board)))
        if isinstance(node, ast.Name)}
check("_build_board (which runs in a worker thread) never touches the sweep timings",
      not used & {"_sweep_line", "_sweep_recent", "_sweep_slowest", "_note_sweep"}, str(used))
check("every sweep task gets the note-the-duration callback",
      "add_done_callback(functools.partial(_note_sweep, key, now))" in inspect.getsource(mbot._pump_if_due))

print()
if failures:
    print(f"{len(failures)} failed: " + "; ".join(failures))
    sys.exit(1)
print("All pump-task checks passed.")
