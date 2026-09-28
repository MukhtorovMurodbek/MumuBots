"""What happens to a conversion, and to its credit, however it ends.

    python3 tests/test_jobs.py

convert_bot/jobs.py owns the queue and the refund policy; bot.py only does
the talking. That split exists so this file can drive every ending with a
fake worker and a fake runner -- no Telegram, no database, no real
conversions -- and assert the part that has to be right every time:

  charged when a conversion STARTS, so a queued job removed costs nothing;
  not refunded when the person stops a running one themselves;
  refunded whenever it ends for the bot's reasons, and the owner alerted
  when that reason is a fault rather than a declined file;
  one conversion at a time per person, in order;
  a staged file kept while another job still needs it, or when asked to.
"""
import asyncio
import os
import pathlib
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / ".mirror" / "convert_bot"))
sys.path.insert(0, str(HERE / "stubs"))
# Files over 20 MB are test_big_files.py's; here the bot runs without them,
# whatever the shell this runs from has set.
for _variable in ("CBOT_API_ID", "CBOT_API_HASH", "CONVERT_MAX_FILE_MB"):
    os.environ.pop(_variable, None)
os.environ.pop("LOCAL_BOT_API_URL", None)

import jobs  # noqa: E402

# The scenarios below swap in fake workers; this is the real one.
REAL_RUN_WORKER = jobs.run_worker

STAGE = pathlib.Path(tempfile.mkdtemp(prefix="convertbot-jobs-"))
failures = []


def check(name, ok, detail=""):
    print(("  ok  " if ok else "  FAIL  ") + name + ("" if ok else f"  -- {detail}"))
    if not ok:
        failures.append(name)


class FakeRunner(jobs.Runner):
    def __init__(self, balance=1000, deliver_error=None):
        self.balance = balance
        self.deliver_error = deliver_error
        self.calls = []

    def work_dir(self):
        return STAGE

    async def charge(self, job):
        self.calls.append(("charge", job.id))
        if job.price > self.balance:
            return None
        self.balance -= job.price
        return self.balance

    async def started(self, job, balance):
        self.calls.append(("started", job.id))

    async def deliver(self, job, outcome):
        self.calls.append(("deliver", job.id))
        if self.deliver_error:
            raise self.deliver_error

    async def succeeded(self, job, outcome):
        self.calls.append(("succeeded", job.id))

    async def refund(self, job, kind):
        self.balance += job.price
        self.calls.append(("refund", job.id, kind))
        return self.balance

    async def failed(self, job, kind, detail, refunded_to):
        self.calls.append(("failed", job.id, kind, refunded_to))

    async def alert(self, job, kind, detail, elapsed, refunded_to):
        self.calls.append(("alert", job.id, kind))

    def names(self, job_id=None):
        return [c[0] for c in self.calls if job_id is None or c[1] == job_id]


def staged(name="in.jpg"):
    path = STAGE / f"1-{name}"
    path.write_bytes(b"x" * 100)
    return path


def make_job(path, price=60, **kw):
    return jobs.Job(user_id=kw.pop("user_id", 1), chat_id=1, paths=[str(path)],
                    src_ext="jpg", target_ext="png", size=100, price=price, lang="en", **kw)


def worker_returning(outcome, delay=0.0, log=None):
    async def fake(job, work_dir, stem, time_limit):
        if log is not None:
            log.append(("worker-start", job.id))
        await asyncio.sleep(delay)
        if log is not None:
            log.append(("worker-end", job.id))
        return outcome
    return fake


def run(coro):
    jobs.reset()
    return asyncio.run(coro)


def ok_output(size=10):
    out = STAGE / "out.png"
    out.write_bytes(b"y" * size)
    return jobs.Outcome(True, path=str(out), bytes=size)


async def scenario_success():
    runner = FakeRunner()
    jobs.run_worker = worker_returning(ok_output())
    path = staged()
    job = jobs.submit(None, make_job(path), runner)
    await job.task
    check("a conversion that works is charged once and delivered",
          runner.names() == ["charge", "started", "deliver", "succeeded"], runner.names())
    check("and is not refunded or reported", runner.balance == 940, runner.balance)
    check("its staged file is deleted when nobody asked to keep it", not path.exists())
    check("and its output is deleted after sending", not (STAGE / "out.png").exists())


async def scenario_user_stops_running():
    runner = FakeRunner()
    jobs.run_worker = worker_returning(ok_output(), delay=30)
    job = jobs.submit(None, make_job(staged()), runner)
    await asyncio.sleep(0.05)
    jobs.cancel(job.id, user_id=1)
    await job.task
    failed = [c for c in runner.calls if c[0] == "failed"]
    check("stopping a running conversion yourself ends it as user_cancelled",
          failed and failed[0][2] == jobs.USER_CANCELLED, runner.calls)
    check("and is not refunded", "refund" not in runner.names() and runner.balance == 940, runner.calls)
    check("and is not reported to the owner", "alert" not in runner.names(), runner.calls)


async def scenario_user_removes_queued():
    runner = FakeRunner()
    jobs.run_worker = worker_returning(ok_output(), delay=30)
    first = jobs.submit(None, make_job(staged("a.jpg")), runner)
    second = jobs.submit(None, make_job(staged("b.jpg")), runner)
    await asyncio.sleep(0.05)
    check("a second job for the same person waits its turn", jobs.ahead_of(second) == 1)
    jobs.cancel(second.id, user_id=1)
    await second.task
    check("removing a queued job never charges it",
          ("charge", second.id) not in runner.calls and runner.balance == 940, runner.calls)
    jobs.cancel(first.id, user_id=1)
    await first.task
    check("somebody else cannot cancel your job", jobs.cancel("nope", user_id=2) is None)


async def scenario_bot_failures():
    for kind, outcome_or_error, alerted in (
        (jobs.TIMEOUT, jobs.Outcome(False, jobs.TIMEOUT, "ran past 600s"), True),
        # Stopped early for being on course to miss the limit: refunded like a
        # timeout, but the file's size or length is not the bot's fault.
        (jobs.TOO_SLOW, jobs.Outcome(False, jobs.TOO_SLOW, "on course for 1500s"), False),
        (jobs.REFUSED, jobs.Outcome(False, jobs.REFUSED, "not an image"), False),
        (jobs.CRASH, jobs.Outcome(False, jobs.CRASH, "killed"), True),
        (jobs.TOO_LARGE, "too-large", True),
        (jobs.SEND_FAILED, "send", True),
    ):
        jobs.reset()
        runner = FakeRunner(deliver_error=RuntimeError("Request Entity Too Large") if outcome_or_error == "send" else None)
        if outcome_or_error == "too-large":
            jobs.run_worker = worker_returning(ok_output(jobs.SEND_LIMIT_BYTES + 1))
        elif outcome_or_error == "send":
            jobs.run_worker = worker_returning(ok_output())
        else:
            jobs.run_worker = worker_returning(outcome_or_error)
        job = jobs.submit(None, make_job(staged()), runner)
        await job.task
        failed = [c for c in runner.calls if c[0] == "failed"]
        check(f"{kind}: ends as {kind}", failed and failed[0][2] == kind, runner.calls)
        check(f"{kind}: the credit goes back", runner.balance == 1000 and failed[0][3] == 1000, runner.calls)
        check(f"{kind}: {'the owner is told' if alerted else 'the owner is not bothered'}",
              ("alert" in runner.names()) == alerted, runner.calls)
    check("an oversized result is deleted rather than kept", not (STAGE / "out.png").exists())


async def scenario_no_credit():
    runner = FakeRunner(balance=10)
    jobs.run_worker = worker_returning(ok_output())
    job = jobs.submit(None, make_job(staged()), runner)
    await job.task
    failed = [c for c in runner.calls if c[0] == "failed"]
    check("a job the balance cannot cover does not start",
          failed and failed[0][2] == jobs.NO_CREDIT and "deliver" not in runner.names(), runner.calls)
    check("and nothing is refunded because nothing was taken",
          "refund" not in runner.names() and runner.balance == 10, runner.calls)


async def scenario_one_at_a_time_and_keep():
    runner = FakeRunner()
    log = []
    jobs.run_worker = worker_returning(ok_output(), delay=0.05, log=log)
    path = staged()
    first = jobs.submit(None, make_job(path, keep_file=True), runner)
    second = jobs.submit(None, make_job(path, keep_file=False), runner)
    check("a file two jobs need is protected from the sweeper", str(path) in jobs.active_paths())
    await first.task
    check("the file survives the first job because the second still needs it", path.exists())
    await second.task
    order = [event for event, _ in log]
    check("one person's conversions run one at a time",
          order == ["worker-start", "worker-end", "worker-start", "worker-end"], log)
    check("and the file goes once no job needs it and nobody asked to keep it", not path.exists())

    kept = staged("kept.jpg")
    job = jobs.submit(None, make_job(kept, keep_file=True), runner)
    await job.task
    check("a file somebody asked to keep is left for the next format", kept.exists())


async def scenario_paused():
    class PausedRunner(FakeRunner):
        async def refuse_start(self, job):
            return True

    runner = PausedRunner()
    jobs.run_worker = worker_returning(ok_output())
    job = jobs.submit(None, make_job(staged()), runner)
    await job.task
    failed = [c for c in runner.calls if c[0] == "failed"]
    check("a job reaching the front during an announced pause ends as paused",
          failed and failed[0][2] == jobs.PAUSED, runner.calls)
    check("and is neither charged, refunded nor reported",
          not {"charge", "refund", "alert"} & set(runner.names()), runner.calls)


async def scenario_free():
    runner = FakeRunner()
    jobs.run_worker = worker_returning(jobs.Outcome(False, jobs.CRASH, "boom"))
    job = jobs.submit(None, make_job(staged(), price=0), runner)
    await job.task
    check("a free job that fails has nothing to refund", "refund" not in runner.names(), runner.calls)


async def scenario_progress():
    """Updates at each mark while the worker runs, none once it has ended."""
    original_marks, original_free = jobs.PROGRESS_MARKS_S, jobs.FREE_TIME_LIMIT_S
    jobs.PROGRESS_MARKS_S = (0.05, 0.15)

    class WatchingRunner(FakeRunner):
        async def progress(self, job, stage, stages, elapsed_s, next_s, limit_s):
            self.calls.append(("progress", job.id, stage, stages, elapsed_s, next_s, limit_s))

    def updates(runner):
        return [c for c in runner.calls if c[0] == "progress"]

    try:
        runner = WatchingRunner()
        jobs.run_worker = worker_returning(ok_output(), delay=0.3)
        job = jobs.submit(None, make_job(staged()), runner)
        await job.task
        check("a long conversion says it is still going at each mark, in order",
              [(c[2], c[3]) for c in updates(runner)] == [(1, 2), (2, 2)], runner.calls)
        check("the first says when the next comes, the last that the ending is next",
              [c[5] for c in updates(runner)] == [0.15, None], updates(runner))
        check("both before the result, never after",
              runner.names() == ["charge", "started", "progress", "progress", "deliver", "succeeded"],
              runner.names())

        runner = WatchingRunner()
        jobs.run_worker = worker_returning(ok_output(), delay=0.0)
        job = jobs.submit(None, make_job(staged()), runner)
        await job.task
        await asyncio.sleep(0.25)
        check("a quick one sends none, even well after it has ended", not updates(runner), runner.calls)

        runner = WatchingRunner()
        jobs.FREE_TIME_LIMIT_S = 0.1
        jobs.run_worker = worker_returning(ok_output(), delay=0.3)
        job = jobs.submit(None, make_job(staged(), price=0), runner)
        await job.task
        check("a mark at or past the job's own limit is dropped, so its one update is the last",
              [(c[2], c[3], c[5]) for c in updates(runner)] == [(1, 1, None)], updates(runner))
        jobs.FREE_TIME_LIMIT_S = original_free

        runner = WatchingRunner()
        jobs.run_worker = worker_returning(ok_output(), delay=1.0)
        job = jobs.submit(None, make_job(staged()), runner)
        await asyncio.sleep(0.02)
        jobs.cancel(job.id, 1)
        await job.task
        await asyncio.sleep(0.25)
        check("a conversion that was stopped stops updating too", not updates(runner), runner.calls)
    finally:
        jobs.PROGRESS_MARKS_S, jobs.FREE_TIME_LIMIT_S = original_marks, original_free


async def scenario_in_flight():
    """A paid conversion is recorded while it runs, so a hard kill cannot keep
    its credit -- and the record decides who refunds, so nobody refunds twice."""
    class HoldingRunner(FakeRunner):
        def __init__(self, released="held", **kw):
            super().__init__(**kw)
            self.released = released

        async def hold(self, job):
            self.calls.append(("hold", job.id))

        async def release(self, job):
            self.calls.append(("release", job.id))
            return self.released

    runner = HoldingRunner()
    jobs.run_worker = worker_returning(ok_output())
    job = jobs.submit(None, make_job(staged()), runner)
    await job.task
    check("a paid conversion is recorded once it is charged, and released when it ends",
          runner.names() == ["charge", "hold", "started", "deliver", "release", "succeeded"], runner.names())

    runner = HoldingRunner()
    jobs.run_worker = worker_returning(ok_output())
    job = jobs.submit(None, make_job(staged(), price=0), runner)
    await job.task
    check("a free one is never recorded", "hold" not in runner.names() and "release" not in runner.names(),
          runner.names())

    runner = HoldingRunner()
    jobs.run_worker = worker_returning(jobs.Outcome(False, jobs.CRASH, "boom"))
    job = jobs.submit(None, make_job(staged()), runner)
    await job.task
    check("a failure it still held is refunded here", "refund" in runner.names() and runner.balance == 1000,
          runner.calls)

    runner = HoldingRunner(released="recovered")
    jobs.run_worker = worker_returning(jobs.Outcome(False, jobs.CRASH, "boom"))
    job = jobs.submit(None, make_job(staged()), runner)
    await job.task
    failed = [c for c in runner.calls if c[0] == "failed"]
    check("one already refunded by another process is not refunded again",
          "refund" not in runner.names() and runner.balance == 940, runner.calls)
    check("and does not claim to have refunded", failed and failed[0][3] is None, failed)

    runner = HoldingRunner(released="absent")
    jobs.run_worker = worker_returning(jobs.Outcome(False, jobs.CRASH, "boom"))
    job = jobs.submit(None, make_job(staged()), runner)
    await job.task
    check("one whose record is gone (erased, or never written) is still refunded",
          "refund" in runner.names() and runner.balance == 1000, runner.calls)


async def scenario_live_card():
    """While it runs, the status is redrawn with whatever progress the worker
    has reported; while it waits, the queue position is kept current."""
    original = jobs.CARD_EVERY_S
    jobs.CARD_EVERY_S = 0.05

    class CardRunner(FakeRunner):
        async def card(self, job):
            self.calls.append(("card", job.id, job.extra.get("stage"), job.extra.get("progress")))

        async def queued(self, job, ahead):
            self.calls.append(("queued", job.id, ahead))

    async def reporting_worker(job, work_dir, stem, time_limit):
        for done in (1, 2, 3):
            job.extra["progress"] = (done, 4)
            await asyncio.sleep(0.08)
        return ok_output()

    try:
        runner = CardRunner()
        jobs.run_worker = reporting_worker
        job = jobs.submit(None, make_job(staged()), runner)
        await job.task
        cards = [c for c in runner.calls if c[0] == "card"]
        converting = [c for c in cards if c[2] == "converting"]
        check("the card is redrawn while it converts, with the progress reported",
              len(converting) >= 2 and any(c[3] for c in converting), cards)
        check("then once more as it starts sending, before the file goes",
              [c[2] for c in cards][-1] == "sending"
              and runner.names().index("deliver") > max(i for i, c in enumerate(runner.calls) if c[0] == "card"),
              runner.names())
        check("the progress is cleared for the new stage", cards[-1][3] is None, cards[-1])
        await asyncio.sleep(0.2)
        check("and nothing is redrawn after it has ended",
              len([c for c in runner.calls if c[0] == "card"]) == len(cards), runner.calls[-3:])

        # Three people, one slot. The third waits two places back, and is told
        # when it is one place back -- which lasts a whole conversion, so it is
        # always seen. Being told "none ahead" is not asserted: that lasts only
        # from one job ending to the next taking the slot, a few iterations of
        # the loop, and a poll every CARD_EVERY_S usually misses it (the test
        # that asserted it failed about one run in five). Nothing is lost when
        # it is missed: the job starting redraws the card as converting.
        runner = CardRunner()
        jobs.run_worker = worker_returning(ok_output(), delay=0.3)
        first = jobs.submit(None, make_job(staged("a.jpg"), user_id=11), runner)
        second = jobs.submit(None, make_job(staged("b.jpg"), user_id=12), runner)
        third = jobs.submit(None, make_job(staged("c.jpg"), user_id=13), runner)
        await asyncio.gather(first.task, second.task, third.task)
        waits = [c[2] for c in runner.calls if c[0] == "queued" and c[1] == third.id]
        check("a queued job is told when it moves up -- one ahead, once the first has finished",
              1 in waits and 2 not in waits, waits)
        check("and the running one is never told it is queued",
              not [c for c in runner.calls if c[0] == "queued" and c[1] == first.id], runner.calls)
    finally:
        jobs.CARD_EVERY_S = original


async def scenario_worker_progress():
    """run_worker reads the worker as it goes: progress lines to the job, the
    last line the outcome, stderr drained so a chatty worker cannot stall."""
    original = jobs.WORKER
    script = STAGE / "fake_worker.py"
    try:
        script.write_text(
            "import json, sys, time\n"
            "sys.stderr.write('x' * 300000)\n"
            "for done in (1, 2, 3):\n"
            "    print(json.dumps({'progress': [done, 4]}), flush=True)\n"
            "    time.sleep(0.05)\n"
            "print(json.dumps({'ok': True, 'path': 'out.png', 'bytes': 12}), flush=True)\n")
        jobs.WORKER = script
        job = make_job(staged())
        outcome = await REAL_RUN_WORKER(job, STAGE, "stem", 30)
        check("the outcome is the last line, whatever came before it",
              outcome.ok and outcome.bytes == 12, outcome)
        check("the last progress line reached the job", job.extra.get("progress") == [3, 4]
              or job.extra.get("progress") == (3, 4), job.extra.get("progress"))

        script.write_text("import time\ntime.sleep(30)\n")
        began = asyncio.get_running_loop().time()
        outcome = await REAL_RUN_WORKER(make_job(staged()), STAGE, "stem", 1)
        took = asyncio.get_running_loop().time() - began
        check("a worker that hangs is stopped at its limit", outcome.kind == jobs.TIMEOUT and took < 10,
              (outcome, took))

        script.write_text("import sys\nsys.stderr.write('Traceback: it broke')\nsys.exit(3)\n")
        outcome = await REAL_RUN_WORKER(make_job(staged()), STAGE, "stem", 30)
        check("one that dies says how, from its stderr", outcome.kind == jobs.CRASH
              and "it broke" in outcome.message, outcome)

        # On course to miss its limit. The worker starts a stand-in for ffmpeg
        # -- a process of its own, beating on a file -- and then reports 2% a
        # second: fifty seconds against a ten-second limit.
        grandchild, beat = STAGE / "fake_ffmpeg.py", STAGE / "fake_ffmpeg.beat"
        grandchild.write_text(
            "import os, pathlib, sys, time\n"
            "while True:\n"
            "    pathlib.Path(sys.argv[1]).write_text(f'{os.getpid()} {time.time()}')\n"
            "    time.sleep(0.1)\n")
        script.write_text(
            "import json, subprocess, sys, time\n"
            f"subprocess.Popen([sys.executable, r'{grandchild}', r'{beat}'])\n"
            "for done in range(1, 1000):\n"
            "    print(json.dumps({'progress': [done, 1000]}), flush=True)\n"
            "    time.sleep(0.05)\n")
        after, margin = jobs.PROJECT_AFTER_S, jobs.PROJECT_MARGIN
        jobs.PROJECT_AFTER_S = 1.0
        job = make_job(staged())
        began = asyncio.get_running_loop().time()
        outcome = await REAL_RUN_WORKER(job, STAGE, "stem", 10)
        took = asyncio.get_running_loop().time() - began
        check("a conversion on course to miss its limit is stopped early, not at the limit",
              outcome.kind == jobs.TOO_SLOW and took < 6, (outcome, round(took, 1)))
        check("with the time it would have taken, for the sentence",
              20 < job.extra.get("projected_s", 0) < 200, job.extra.get("projected_s"))
        before = beat.read_text() if beat.exists() else ""
        await asyncio.sleep(1.0)
        after_kill = beat.read_text() if beat.exists() else ""
        check("and ffmpeg, a process of its own, goes with it -- not left burning a core",
              before and before == after_kill, (before, after_kill))
        if before and before != after_kill:          # clean up after a failure
            pid = int(after_kill.split()[0])
            subprocess.run(["taskkill", "/F", "/PID", str(pid)] if os.name == "nt" else ["kill", "-9", str(pid)],
                           capture_output=True)

        script.write_text(
            "import json, time\n"
            "for done in range(1, 41):\n"
            "    print(json.dumps({'progress': [done, 40]}), flush=True)\n"
            "    time.sleep(0.05)\n"
            "print(json.dumps({'ok': True, 'path': 'out.png', 'bytes': 12}), flush=True)\n")
        outcome = await REAL_RUN_WORKER(make_job(staged()), STAGE, "stem", 10)
        check("one on course to finish in time is left to finish", outcome.ok, outcome)
        jobs.PROJECT_AFTER_S, jobs.PROJECT_MARGIN = after, margin
    finally:
        jobs.WORKER = original


print("\n== endings ==")
for scenario in (scenario_success, scenario_user_stops_running, scenario_user_removes_queued,
                 scenario_bot_failures, scenario_no_credit, scenario_one_at_a_time_and_keep, scenario_paused,
                 scenario_free, scenario_progress, scenario_in_flight, scenario_live_card,
                 scenario_worker_progress):
    run(scenario())

print("\n== before anything is charged ==")
check("HEIC is not offered above x265's 142.6 MP ceiling",
      "heic" in jobs.unsendable_targets(150.0, ["heic", "jpg"]))
check("PNG is not offered for a 150 MP photo", jobs.unsendable_targets(150.0, ["png"]) == ["png"])
check("but is for a 12 MP one", jobs.unsendable_targets(12.0, ["png"]) == [])
check("a lossy format is never guessed at", jobs.unsendable_targets(200.0, ["jpg", "webp"]) == [])
check("an unknown size hides nothing", jobs.unsendable_targets(None, ["png", "heic"]) == [])

for leftover in STAGE.iterdir():
    leftover.unlink()
big = STAGE / "2-big.bin"
big.write_bytes(b"z" * (1024 * 1024))
original = jobs.USER_STORAGE_MB
jobs.USER_STORAGE_MB = 1
problem = jobs.storage_problem(2, 1, STAGE)
check("a person over their storage share is refused", problem is not None and problem[0] == "user", problem)
check("and somebody else is not", jobs.storage_problem(3, 1, STAGE) is None)
jobs.USER_STORAGE_MB = original

print()
if failures:
    print(f"FAILURES ({len(failures)}):")
    for name in failures:
        print("  -", name)
    sys.exit(1)
print("every ending does the right thing with the credit")
