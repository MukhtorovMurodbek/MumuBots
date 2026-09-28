"""The numbers the sleep decision will be made on.

    python3 tests/test_usage.py

Two pieces of arithmetic that nothing else checks and that are easy to get
quietly wrong:

**Sleepable seconds.** A host stops charging for a container once it has been
quiet for a while — Railway's threshold is about five minutes — so the value
of a gap between updates is `gap - SLEEP_AFTER_SECONDS`, not `gap`. Adding
the gaps up instead would say a bot handling one message every four minutes
is idle 100% of the time, when in fact it would never sleep at all. That is
the difference between "sleep it" and "leave it alone", which is the whole
decision.

**The alarm thresholds.** Every one of them has a floor as well as a ratio,
and the floor is what stops a quiet bot alarming about nothing. Six times the
median is easy to hit when the median is one.

No database and no Telegram: these are counters and comparisons.
"""
import os
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / ".mirror" / "convert_bot"))
sys.path.insert(0, str(HERE / "stubs"))
os.environ.setdefault("CBOT_TOKEN", "test")
os.environ.setdefault("CBOT_USERNAME", "test")
os.environ.setdefault("DATABASE_URL", "postgresql://stub")

import shared_features as sf                            # noqa: E402

failures = []


def check(name, ok, detail=""):
    print(("  ok  " if ok else "  FAIL  ") + name + (f" -- {detail}" if not ok and detail else ""))
    if not ok:
        failures.append(name)


def replay(gaps, clock_start=1000.0):
    """Feed a run of gaps between updates through the counter and return what
    the window would have written."""
    sf._usage_sleepable = 0.0
    sf._usage_max_gap = 0.0
    sf._usage_updates = 0
    sf._usage_users = set()
    now = clock_start
    sf._usage_last_update = now
    sf._usage_window_start = now
    times = [now]
    for gap in gaps:
        now += gap
        times.append(now)
    original = sf.time.monotonic
    try:
        sequence = iter(times[1:])
        sf.time.monotonic = lambda: next(sequence)
        for _ in gaps:
            sf.note_usage_update(7)
    finally:
        sf.time.monotonic = original
    return sf._usage_sleepable, sf._usage_max_gap


def main() -> int:
    threshold = sf.SLEEP_AFTER_SECONDS
    print(f"=== sleepable time (host sleeps after {threshold}s) ===")

    sleepable, longest = replay([60, 60, 60])
    check("a message every minute is never sleepable", sleepable == 0, str(sleepable))
    check("and the longest gap is still recorded", longest == 60, str(longest))

    sleepable, _ = replay([threshold])
    check("a gap of exactly the threshold is worth nothing", sleepable == 0, str(sleepable))

    sleepable, _ = replay([threshold + 100])
    check("a gap past it is worth only the part past it", sleepable == 100, str(sleepable))

    sleepable, longest = replay([3600, 3600])
    check("two hour-long gaps are worth two hours minus two thresholds",
          sleepable == 2 * (3600 - threshold), str(sleepable))
    check("and the longest gap is an hour", longest == 3600, str(longest))

    # The shape that would be wrong if the gaps were simply summed.
    naive, _ = replay([240] * 15)
    check("a message every four minutes sleeps for none of the hour",
          naive == 0, f"{naive}s -- summing the gaps would have said 3600s")

    print("\n=== a long quiet, across windows ===")
    # Three hours of nothing, sampled every 15 minutes as the bots do: the host
    # would have gone to sleep once, five minutes in, and stayed asleep.
    written = []
    real_record, real_monotonic, real_footprint = sf.family_link.record_usage, sf.time.monotonic, sf.footprint_numbers
    clock = [0.0]
    try:
        sf.family_link.record_usage = lambda *a: written.append(a[7])     # its sleepable_seconds
        sf.footprint_numbers = lambda: {"rss_mb": 1, "peak_rss_mb": 1, "ceiling_mb": 1, "cpu_seconds": 0}
        sf.time.monotonic = lambda: clock[0]
        sf._usage_last_update = sf._usage_window_start = 0.0
        sf._usage_sleepable = sf._usage_max_gap = 0.0
        for window in range(1, 13):
            clock[0] = window * 900.0
            sf._sample_usage_now()
    finally:
        sf.family_link.record_usage, sf.time.monotonic, sf.footprint_numbers = real_record, real_monotonic, real_footprint
    check("the first window of a quiet stretch is asleep from five minutes in",
          written[:1] == [900 - threshold], written[:2])
    check("and every window after it is asleep all through",
          written[1:] == [900] * 11, written[1:])
    check(f"so three quiet hours read as {sum(written) / 10800:.0%} asleep, not 67%",
          sum(written) == 10800 - threshold, sum(written))
    check("and the quiet is measured from the last update, not the last window",
          int(clock[0] - sf._usage_last_update) == 10800)

    print("\n=== the alarm floors ===")

    # A quiet bot: a median of 1 and a window of 8 is eight times the median,
    # and must not be an incident.
    sf._usage_recent.clear()
    sf._usage_recent.extend([1, 0, 2, 1, 1, 0, 1, 1])
    baseline = sf._median(sf._usage_recent)
    spike = 8
    fires = (spike >= sf.USAGE_SPIKE_FLOOR and baseline > 0
             and spike >= baseline * sf.USAGE_SPIKE_FACTOR)
    check("8 updates against a median of 1 is not a spike", not fires,
          f"median={baseline}, floor={sf.USAGE_SPIKE_FLOOR}")

    busy = sf.USAGE_SPIKE_FLOOR * 2
    sf._usage_recent.clear()
    sf._usage_recent.extend([5] * 8)
    baseline = sf._median(sf._usage_recent)
    fires = (busy >= sf.USAGE_SPIKE_FLOOR and baseline > 0
             and busy >= baseline * sf.USAGE_SPIKE_FACTOR)
    check("but a real jump on a bot with a real baseline is", fires,
          f"{busy} against median {baseline}")

    check("the failure alarm has a floor too", sf.USAGE_FAILURE_FLOOR >= 2,
          str(sf.USAGE_FAILURE_FLOOR))
    check("and its ratio is a ratio", 0 < sf.USAGE_FAILURE_WARN_RATIO <= 1,
          str(sf.USAGE_FAILURE_WARN_RATIO))
    check("the memory ratio leaves room before the kill",
          0.5 < sf.USAGE_MEMORY_WARN_RATIO < 1, str(sf.USAGE_MEMORY_WARN_RATIO))

    print("\n=== an alarm does not repeat itself ===")
    sf._usage_alarmed.clear()
    first = sf._alarm_due("memory")
    second = sf._alarm_due("memory")
    check("the same alarm fires once and then stays quiet", first and not second)
    check("a different alarm is not silenced with it", sf._alarm_due("spike"))

    print("\n=== the job counter ===")
    sf._usage_jobs_ok = sf._usage_jobs_failed = 0
    for ok in (True, True, False, True):
        sf.note_job(ok)
    check("counts both sides", (sf._usage_jobs_ok, sf._usage_jobs_failed) == (3, 1),
          f"{sf._usage_jobs_ok}/{sf._usage_jobs_failed}")
    sf._usage_jobs_ok = sf._usage_jobs_failed = 0

    print()
    if failures:
        print(f"FAILURES ({len(failures)}):")
        for name in failures:
            print("  -", name)
        return 1
    print("the idle and alarm arithmetic holds.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
