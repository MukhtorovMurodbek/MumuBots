# `monitoring.py`

[↑ manager_bot](README.md) · [code](monitoring.py)

ManagerBot's own logging / crash tracking / activity tracking.

This is the monitoring half of the other four bots' shared_features.py,
copied here without the halves ManagerBot has no use for (donations, the
sibling-bot cross-promotion, i18n -- ManagerBot has exactly one user and
speaks one language). family_link.py looks for a module named either
`shared_features` or `monitoring` and uses whichever it finds, which is why
this file keeps the same function names.

Keeping ManagerBot on the same vocabulary matters: a bot that watches the
others should be watched by exactly the same machinery, so "ManagerBot has
been quietly crashing" is as visible as it would be for any of them.

_At `_event_hook = None`:_

Set by family_link.attach(); see shared_features.py in any of the other
bots for the same hook.

### `_log_to_files()`

Files on a laptop, stdout only in the cloud -- see the identical
reasoning in the other four bots' shared_features.py.

## Transient network conditions vs. actual faults

A long poll losing its connection, a read timing out, Telegram asking the bot to slow down: PTB retries all of these itself and the bot keeps working, so
waking the operator with a traceback for each one is noise that trains anyone to ignore the channel that also carries real crashes.

The catch is PTB's class hierarchy: BadRequest \*subclasses\* NetworkError,
and a BadRequest is a genuine fault -- a malformed API call, a bug in the bot -- so a
plain `isinstance(exc, NetworkError)` would swallow exactly the errors most
worth hearing about. It has to be excluded explicitly.

_At `NETWORK_ALERT_AFTER = int(os.environ.get("NETWORK_ALERT_AFTER", "20"))`:_

How many blips in a row before saying something. Reset by any update that
arrives, since one arriving proves the connection is working again. Roughly
a few minutes of a dead link at a 30-second poll.

- `_network_blips = 0` — consecutive, since the last update actually arrived

- `_network_blips_total = 0` — since this process started

_At `PERMANENT_NETWORK_MESSAGES = ("entity too large", "file is too big", "too big", "too lar …`:_

PTB raises Telegram's HTTP 413 -- "Request Entity Too Large" -- as a plain
NetworkError, so it matched the tuple above and was filed as a flaky
connection. It is the opposite: permanent, and the same upload fails every
time. That is how two successful ConvertBot conversions of a 200 MP photo
vanished -- result converted, upload refused, error handler said "retried
by PTB", user told nothing, credit kept. Anything whose text says the
payload is too big is a real failure, whatever class it arrived as.

### `note_network_blip()`

Counted and logged, never reported as a crash -- until there have been
enough in a row to mean the connection is gone rather than flaky, which is
worth exactly one message.

### `note_network_ok()`

An update arrived, so the connection works. Called from track_activity,
which runs before every other handler.

## A Conflict is one blip or a second copy -- only a run of them is the second

Telegram answers a getUpdates with "terminated by other getUpdates request"
when two requests for the same token overlap. A second copy of the bot
polling the same token does that on every poll cycle. But a single one is
also what a ~90 s network/database stall leaves behind (2026-10-09): the
bot's own request was still in flight when its retry arrived. Filing every
one as a crash woke the operator for a false alarm, so one is logged as a
warning and only a run of them is reported -- once.

- `CONFLICT_ALERT_COUNT = 3` — this many Conflicts ...

- `CONFLICT_ALERT_WINDOW_S = 120` — ... inside this many seconds means a second copy

- `CONFLICT_QUIET_RESET_S = 600` — seconds with no Conflict at all before it may alert again

_At `_conflict_times: "deque[float]" = deque(maxlen=CONFLICT_ALERT_COUNT)`:_

Monotonic times of the most recent Conflicts (only the last few matter), when
the latest was, and whether this run has been reported. Per process: every
bot is its own process in the base edition, and the combined edition loads a
separate copy of this module per bot, the same as \_network_blips above.

### `note_conflict()`

A Conflict from the long poll (no update behind it). Counted and logged
as a warning, never a crash -- until CONFLICT_ALERT_COUNT of them arrive
within CONFLICT_ALERT_WINDOW_S, which is a second copy polling the same
token and is worth exactly one report. After that it stays quiet until
CONFLICT_QUIET_RESET_S pass with no Conflict, then counts afresh.

The log line carries numbers only: no token, and not the text of the error.

### `error_handler()`

Register with Application.add_error_handler in each bot's main() --
this is PTB's global hook for exceptions that escape a handler callback
uncaught (i.e. actual crashes, not the try/except'd, user-facing errors
already handled inline elsewhere).

A dropped long poll reaches here too, and is not a crash -- see
is_transient_network_error above. Nor is a single Conflict from the long
poll -- see note_conflict.

- At `if update is None and is_transient_network_error(context.error):`:
  Only a failure with no update behind it is the long poll. A network
  error raised while handling somebody's message means that person asked
  for something and did not get it, and that is not a blip however
  transient its cause: it is counted, logged with its traceback, and
  reported like any other failure.
- At `if update is None and isinstance(context.error, Conflict):`:
  The same reasoning for a Conflict: only the poll's own is counted, one
  raised while handling an update is reported like any other failure.

## Active-user tracking, buffered -- see shared_features.py for the reasoning

ManagerBot has one user, so the saving here is small in absolute terms. It is
kept identical anyway: the whole point of this file is that ManagerBot is
measured by exactly the same machinery as the bots it watches, and a
divergence here is a divergence in what /status means.

### `track_activity()`

- At `live_message.note_update(update)`:
  See shared_features: this is what lets an evolving message tell whether
  it is still the last thing in the chat.

### `tune_runtime()`

Call from post_init -- see shared_features.py for why the default
executor is worth capping, why everything imported at startup is worth
freezing out of the garbage collector's reach, and why an idle process
should not be sweeping its heap every few seconds.

### `attach_maintenance()`

One line in ManagerBot's main(). ManagerBot refuses to start without a
job queue at all (see bot.py), so there is no degraded path here.

### `flush_on_shutdown()`

Register as Application.post_stop.

## What this process actually costs to run

A usage-billed host charges for resident memory and CPU seconds, and until
this was on /status there was no way to tell whether a change to either had
helped, hurt, or done nothing. Every number here is read from the kernel,
free, and only when someone asks.

_At `_service_rss_seen = 0`:_

The largest memory reading this process has seen, for when the cgroup keeps no peak
of its own (kernels before 5.19 have no memory.peak).

### `footprint_numbers()`

The same four readings process_footprint() prints, as numbers, and "scope":
"service" when they are the whole service's (the cgroup's counters, workers
included) and "process" when they are only this process's.

Separated out because a sentence is what a person wants and a number is
what a threshold and a database row want, and building the sentence twice
to parse it back would be the kind of thing that breaks silently in one
language and not another.

Any of them may be None: /proc is Linux, the cgroup file is a container,
and `resource` is not on Windows. A missing number means "not measurable
here", never zero -- zero would read as "free" to every caller.

- At `scale = 1 if sys.platform == "darwin" else 1024`:
  ru_maxrss is kilobytes on Linux and bytes on macOS/BSD, and it
  is a high-water mark rather than a live reading -- so it stands
  in for the peak, and there is no live number to report.
- At `used = resources.memory_used_mb()`:
  Where the container's own counters can be read, they are the service's: this process, the
  conversion worker, ffmpeg and the big-file children, which is what the limit is held
  against. /proc/self is only the bots' process, a fraction of it.

### `process_footprint()`

One line: resident memory, its high-water mark, and CPU seconds burned
since startup. Read /proc where it exists (Linux, which is what the
deployed containers are) and fall back to getrusage elsewhere.

## What it costs over time, and when that stops being normal

/status answers "what is this process using right now", which on its own
tells nobody whether right now is unusual. This keeps a rolling window --
updates handled, distinct people, and the four kernel readings -- writes one
row per window into family.usage_samples, and raises an event when a window
is far enough from the others to be worth a message at 3am.

Three things are worth being told about, and they are different questions:

```text
memory headroom   the container is close to the limit it will be killed
                  for crossing. The only one of the three that is an
                  emergency.
an activity spike a window with far more updates than the recent norm.
                  Could be a launch, could be one script. Either way it is better heard from the bot than from the bill.
monthly reach     how many distinct people used it in thirty days, which
                  is the number that decides whether the plan it is on is
                  still the right one. Slow-moving, so it is checked once
                  a day and only ever mentioned when it crosses.
```

Everything here is a count. No user ids, no chat ids, no text.

_At `SLEEP_AFTER_SECONDS = int(os.environ.get("SLEEP_AFTER_SECONDS") or 300)`:_

How long a bot has to be quiet before that quiet could have been sleep.
The same name and the same default as shared_features.py in the four public
bots, because /idle's arithmetic compares this against the sleepable seconds
those bots measured with it -- two different numbers would make the answer
quietly wrong rather than loudly broken.

_At `USAGE_MEMORY_WARN_RATIO = float(os.environ.get("USAGE_MEMORY_WARN_RATIO") or 0.85)`:_

How full the container has to be before it is worth saying so. 0.85 rather
than 0.95: the point is to arrive before the OOM kill, not with it.

_At `USAGE_CPU_WARN_RATIO = float(os.environ.get("USAGE_CPU_WARN_RATIO") or 0.85)`:_

The same for CPU: a window whose CPU seconds come to this share of the service's vCPUs.

_At `USAGE_SPIKE_FACTOR = float(os.environ.get("USAGE_SPIKE_FACTOR") or 6.0)`:_

A window counts as a spike when it is this many times the median of the
recent ones AND clears the floor. The floor is what stops "2 updates, then
12" from being an incident on a quiet bot -- which, on a bot this quiet, is
most of the time.

_At `USAGE_MONTHLY_USERS_WARN = int(os.environ.get("USAGE_MONTHLY_USERS_WARN") or 400)`:_

Distinct people in thirty days, past which the operator is told once. Not a
limit and nothing is refused; it is the number that means "the smallest
plan that fits may no longer be the smallest plan that fits".

_At `SLEEP_AFTER_SECONDS = int(os.environ.get("SLEEP_AFTER_SECONDS") or 300)`:_

How long a host waits for silence before it stops charging for a container.
Railway sleeps a service after roughly five minutes with no \*outbound\*
traffic, so a gap between updates is only worth anything from the five-minute
mark onwards -- which is why this is subtracted from every gap rather than
the gaps simply being added up. Nothing in the bots sleeps yet; this measures
what sleeping \*would\* have saved, so the decision is made on this family's
own traffic rather than on a guess.

_At `USAGE_FAILURE_WARN_RATIO = float(os.environ.get("USAGE_FAILURE_WARN_RATIO") or 0.5)`:_

A window where more than this fraction of the jobs failed is worth being told
about -- a job being a conversion, a download, a pack edit: whatever the bot
is for. The floor is again what stops one failure out of one being an
outage.

_At `_USAGE_WINDOW_MEMORY = 24`:_

How many recent windows the spike test compares against, and how long an
alarm of one kind stays quiet after firing.

_At `_usage_last_update = time.monotonic()`:_

The gap clock. monotonic() rather than time(): this measures a duration, and
a clock that can be stepped by NTP would make one negative. The last update
is the real one, never moved at a window's end; the window's own start is
what keeps each window to its own share of a long quiet stretch.

### `_asleep_in_window()`

How much of this window, up to `now`, the host would have been asleep
for: the quiet since the last update, from SLEEP_AFTER_SECONDS into it,
and only the part of that inside this window.

### `note_usage_update()`

One update happened. Called from track_activity, so it is on the path
of every update there is -- it adds an int to a set and increments a
couple of counters, and must never do anything more expensive than that.

### `note_job()`

One unit of the thing this bot is for finished -- a conversion, a
download, a pack edit. Two counters and nothing else.

What is deliberately NOT here: what was converted, which link, whose it
was, or why it failed. The question this answers is "is the bot still
working", and that needs a ratio, not a record. Per-route detail already
lives where it belongs -- DownloaderBot's provider health, and every
bot's error log.

_At `_usage_cpu_last: "tuple[float, float] | None" = None`:_

The CPU reading at the last sample: (cpu_seconds, time.monotonic()). cpu_seconds is a running
total since the process, or the service, started, so a window's own CPU is the difference
between two samples.

### `_cpu_window()`

(CPU seconds used, seconds elapsed) since the previous sample, or None for the first
sample, for a reading that is missing, or for a total that went down (a restart began
it again). Called once per window.

### `usage_trend_line()`

One line for /status: what the recent windows looked like, so the
number above it has something to be compared with.

### `_monthly_users()`

Distinct people in the last thirty days, if this bot's db can say.

### `_check_usage_alarms()`

Blocking; runs in the same thread as the sample write.

- At `window = numbers.get("cpu_window")`:
  CPU, only where the reading is the service's own: the cgroup's counters, or this process
  when it holds every bot. And only from the one bot that reports memory (the others
  have it blanked), so a service is not told about five times.

### `_sample_usage_now()`

One window: write the row, then decide whether to say anything.

The counters are taken and reset first, so a slow database cannot make
the next window count this one's updates twice.

- At `global _usage_window_start`:
  The window ends with a gap in progress. Its share of this window is
  counted now, and the next window counts from here -- so a bot quiet for
  six hours reports each window's own part of it, not six hours in one.
  The gap itself goes on: the host would have gone to sleep once, five
  minutes in, not again at the start of every window. (Until 2.0.0 the
  clock was restarted here, and a bot quiet for days read as 67% asleep.)

### `data_protection_lines()`

How this service's data is kept safe, in a line or two for the operator: where the data
key came from, and whether the backups are encrypted to the offline key
(private/runbooks/data-protection.md). Only what needs doing is flagged.
