# `jobs.py`

[↑ convert_bot](README.md) · [code](jobs.py)

The conversion queue, and what happens to the credit.

A conversion used to run inside the handler the format button called. Every
bot in this family has python-telegram-bot process updates one at a time --
on purpose: album batching depends on the parts of an album being handled in
the order they arrive -- so a four-minute conversion of a 200-megapixel photo
held the whole bot for four minutes. Nobody else's button did anything, and
the next tap failed outright with "query is too old", because Telegram gives
a button fifteen seconds to be answered.

So the handler now decides and enqueues, and the work happens here, in a task
of its own:

```text
one conversion at a time per person, in the order they asked;
MAX_CONCURRENT across the whole bot, because the container is sized for
one heavy job rather than for everyone's at once;
each one in a child process (convert_worker.py) under a time limit that is
actually enforced, because a process can be killed and a thread cannot.
```

What happens to the credit is decided here too, in one place, because it is
the part that has to be right every time:

```text
it is taken when the conversion STARTS, not when it is queued, so removing
a queued job costs nothing and no job waits in a queue holding somebody's
money;
it is not refunded if the person stops a running conversion themselves;
it is refunded, and the person told why, whenever the conversion ends for a
reason that is the bot's rather than theirs;
and the operator is told as well whenever that reason is a fault -- a
timeout, a crash, a result too big to send, a send that failed, a restart
-- rather than a file the bot declined.
```

This module knows nothing about Telegram. bot.py hands it a Runner that does
the talking, and that seam is what lets tests/test_jobs.py drive the whole
policy with a fake worker and no network.

_At `CONVERT_JOB_MEMORY_MB = max(1, int(os.environ.get("CONVERT_JOB_MEMORY_MB") or 1536))`:_

How much memory one conversion may count on: what the heaviest ordinary jobs
measured (4K HDR video to MP4 about 887 MB for ffmpeg alone, a 1080p one 945 MB with
unbounded threads), with room over. Together with the machine's size this decides
how many run at once.

### `_ffmpeg_threads()`

How many CPU threads one ffmpeg is held to (convert_utils.FFMPEG_THREADS).

### `concurrent_for()`

How many conversions fit at once in `job_memory_mb` of memory and `cpus` vCPUs: as many
as the memory holds at `per_job_mb` each, and as the processor feeds at `threads` threads
each, never more than 8 and never fewer than 1. One when the memory is not known (a
laptop), which is what the bot always did.

_At `MAX_CONCURRENT = _max_concurrent()`:_

How many conversions run at once across the whole bot: CONVERT_MAX_CONCURRENT when it is
set, else what the machine holds (concurrent_for). Each person still has one at a time (the
lock in \_run), so with n slots n different people convert at once, and nobody's second job
takes a slot from another person's first. On a small container this is 1, as it was: one
heavy job at a time rather than two racing each other into an out-of-memory kill.

### `log_capacity()`

Said once, when the bot starts.

_At `TIME_LIMIT_S = int(os.environ.get("CONVERT_TIME_LIMIT_SECONDS") or 600)`:_

How long one conversion may run before it is stopped and refunded. Ten
minutes is the ceiling ffmpeg already had; the Pillow path had none at all,
which is how a conversion could be waited on for twenty minutes.

_At `FREE_TIME_LIMIT_S = int(os.environ.get("CONVERT_FREE_TIME_LIMIT_SECONDS") or 60)`:_

Free conversions (under 3 MB) get a much shorter one. Nothing that small
megabyte needs a minute, and a free job that runs to the paid limit costs
real hosting for no revenue at all -- the one case where the pricing could
not "at least cover the hardware".

_At `TIME_BASE_S = int(os.environ.get("CONVERT_TIME_BASE_SECONDS") or 120)`:_

A paid job's own limit, since 2.0.0: TIME_BASE_S, and a second more for
every megabyte of work -- the file and its estimated result, times how hard
the pair is to run (convert_utils.work_factor) -- up to TIME_LIMIT_S. The
price is worked out from it (shared/pricing.py), so a small job is not
charged for ten minutes it will never need, and a big one gets the time it
does. A job on course to miss its limit is stopped early and refunded.

### `time_limit_for()`

How long a conversion may run, in seconds.

### `ocr_time_limit()`

How long a job that recognises text may run: what any conversion of that
file is given (time_limit_for: TIME_BASE_S, and a second more for every
megabyte of work in the file and its result), and OCR_PAGE_S more for every
page it reads, up to TIME_LIMIT_S. The file's part is not left out: a 90 MB
PDF of 200 pages with one scanned page took longer than the two minutes
the scan alone is given just to be opened and its text read (reviewed
2026-10-10). Free, it is the free limit, as time_limit_for gives it.

`billable` and `work` are time_limit_for's: convert_utils.billable_bytes of
the file and its estimated result, and convert_utils.work_factor of the pair.

The ONE place this is worked out: the price is made from it
(convert_utils.ocr_limit, for price_for_job) and the job is given it
(bot.\_enqueue, through the same ocr_limit), so the two cannot differ.

- `import ocr_convert` — here, not above: ocr_convert imports convert_utils, which imports this

### `job_limit()`

The limit this job runs under: its own, or for a job made before it
had one, the flat limit of its kind.

_At `PROJECT_AFTER_S = 30`:_

A conversion is stopped early when it is on course to miss its limit: once
it has run PROJECT_AFTER_S and reported some progress, the time it will
take at that rate is projected, and if that is past the limit by more than
PROJECT_MARGIN it stops there and is refunded -- rather than keep somebody
waiting ten minutes for the same refund. The margin is for the first
seconds of a job, which are its slowest.

_At `PROGRESS_MARKS_S = (120, 300)`:_

Updates while a long conversion runs. Ten minutes with no word was too long, so warnings were asked for: one after two minutes of work, two more after it, and the last, at the limit, saying what happened. So a note at two minutes, another at five, and at the
time limit the ending itself -- which the ending already is. A mark at or
past a job's own limit is dropped, so a free conversion sends none.

_At `MAX_QUEUED_PER_USER = max(1, int(os.environ.get("CONVERT_MAX_QUEUED_PER_USER") or 5))`:_

How many conversions one person may have waiting or running at once.

_At `def _default_user_storage_mb() -> int:`:_

Temporary storage, per person and for the whole bot, plus the free disk
that must remain afterwards. Checked before a file is downloaded, against
the size Telegram declares, so a full disk refuses an upload with a sentence
instead of failing half-way through writing it.

The per-person default is sized from the batch limits rather than picked:
an album may hold MAX_BATCH files of MAX_FILE_MB each, and a smaller share
refused the ninth and tenth scans of a ten-page PDF while it was still
arriving, so the PDF quietly came out short. Since the limit is 2000 MB
that is no longer a size anybody has, so it is the larger of an album's
2000 MB and the largest file twice over -- it and what it becomes.

_At `MAX_MEGAPIXELS = int(os.environ.get("CONVERT_MAX_MEGAPIXELS") or 210)`:_

The largest picture converted at all. 210 covers the 200 MP sensors in
current flagship phones -- the Galaxy S23 Ultra among them --
which is exactly the professional case worth supporting. Above it a single
decode needs more memory than a small container has.

_At `CARD_EVERY_S = 3.0`:_

A paid conversion is recorded while it runs (db.conversions_in_flight), and
the running process stamps its record this often. A record unstamped for
HOLD_STALE_S belongs to a process that was killed outright, and its credit
goes back -- the graceful path that refunds an interrupted conversion never
ran. The margin is wide on purpose: conversions run in a child process, so
nothing short of the process dying stops the stamping.
How often a running conversion's status message is redrawn: its stage, its
progress and the time. Every redraw is an edit, and Telegram limits how
fast one chat's messages may be edited; every few seconds reads as live.

_At `WORKER_MAX_MEMORY_MB = int(os.environ.get("CONVERT_WORKER_MAX_MEMORY_MB") or 0)`:_

Optional hard memory cap on the worker process (POSIX). 0 leaves it to the
container. See convert_worker.\_cap_memory.

_At `SEND_LIMIT_MB = big_files.BOT_API_SEND_BYTES // (1024 * 1024)`:_

What a bot may send back. The cloud Bot API refuses a document over 50 MB
with "Request Entity Too Large" -- which is what two 200 MP conversions ran into, after converting successfully.

_At `BIG_SEND_MAX_BYTES = big_files.MTPROTO_MAX_BYTES`:_

Past that, a result goes over MTProto (big_files.py) or through a local Bot
API server, and both would carry 2000 MB. They are not allowed to without
limit, because every price has to cover the worst job it can buy
(tests/test_pricing.py) and sending back is what costs: a job may send up
to SEND_HEADROOM times its estimated result, and never less than the Bot
API's own 50 MB. The headroom is there because the estimate is a judgement:
a result can overshoot it and still be sent, just not without bound, and
the price was worked out on this limit, not on the estimate.

### `big_sends()`

Whether a result over 50 MB can reach this chat at all.

### `big_send_limit()`

The largest result a conversion estimated at `out_estimate` bytes may
send where big sends can reach, which is what its price covers.

### `send_limit_for()`

The largest result this conversion may send back, in bytes.

### `send_ceiling_mb()`

The most any result could be sent at in this chat, for sentences
about a format that is off the menu for being too big.

_At `LOSSLESS_BYTES_PER_PIXEL = {`:_

Bytes per pixel a lossless picture comes out at. Only formats whose size is
predictable from the pixel count are listed; everything lossy is left to the
result-size check after the conversion, because a JPEG's size depends on
what is in the picture rather than how many pixels it has.

Deliberately on the LOW side of what a photo produces, so a format is only
kept off the menu when it is clearly going to be over the limit. A format
that slips through is caught after converting, refunded, and reported --
the estimate saves somebody a wait, the check is what keeps it honest.

Calibrated against a 149.8 MP photo (an S23 Ultra at 16:9): PNG
came out at 163 MB and QOI at 150 MB, about 1.0-1.1 bytes a pixel, on a
test image noisier than a real photo. Half that is used here, which still
keeps both off the menu above roughly 100 MP -- where they are certain to
be over 50 MB -- without hiding them from pictures that might fit.

_At `RAW_LOSSLESS_BYTES_PER_PIXEL = {"tiff": 6.0}`:_

The same for a picture developed from a camera raw file, which is not what a
photo the camera already made is: TIFF comes out at 16 bits a channel,
uncompressed -- six bytes a pixel, exactly -- where an ordinary TIFF is
compressed and unpredictable.

_At `TARGET_MAX_MEGAPIXELS = {`:_

Encoders with a hard ceiling of their own, in megapixels. x265 refuses more
than 142,606,336 luma samples (HEVC level 6.2), so HEIC of a 150 MP photo
fails a few seconds in with an encoder error. Leaving it off the menu is
better than charging, failing and refunding.

_At `USER_CANCELLED = "user_cancelled"`:_

How a job can end, other than with a delivered file.

_At `TOO_SLOW = "too_slow"`:_

On course to miss its limit, and stopped before it did (PROJECT_AFTER_S).

_At `NO_MEMORY = "no_memory"`:_

The converter was killed for memory: the host is too small for this file
(a 4K HDR video in a 1 GB container). Refunded, and told as that -- not an
alert, like TOO_SLOW: the size of the host is known, a smaller file is the
person's to send.

_At `PAUSED = "paused"`:_

Reached the front while the bot was paused for an update. Nothing
started and nothing was charged, so it is neither refunded nor reported --
before this it was INTERRUPTED, which paged the operator once per queued job
and told each person the bot had restarted in the middle of their file.

_At `REFUNDED = frozenset({REFUSED, TIMEOUT, TOO_SLOW, CRASH, NO_MEMORY, TOO_LARGE, SEND_FAIL …`:_

Refunded when the job had been charged: every ending except the person
stopping it themselves. NO_CREDIT is never charged in the first place.

_At `ALERTED = frozenset({TIMEOUT, CRASH, TOO_LARGE, SEND_FAILED, INTERRUPTED})`:_

Worth the operator's attention: the bot's own faults. A file the bot declined
(REFUSED) is the bot working as designed, and an alert for every unreadable
upload would bury the failures that matter.

_At `_sequence = itertools.count()`:_

Queue order comes from a counter, not a clock. time.monotonic() ticks about
every 16 ms on Windows, so two jobs queued back to back got the same
timestamp and "how many are ahead" answered none -- the lock ran
them in the right order, but the sentence telling people so was wrong.

### `Job`

- At `send_limit: int = SEND_LIMIT_BYTES`:
  The largest result this job may send back -- send_limit_for().
- At `time_limit: int = 0`:
  How long it may run -- time_limit_for(). 0: the flat limit of its kind.
- At `ocr_lang: str = ""`:
  Text recognition (ocr_convert.py), both fixed when the job is queued. The
  language is the person's choice then ("eng", "rus", "uzb"; "" for a job
  that reads no text); ocr_pages is the number of pages the menu counted
  and the price was made for, which the worker will not read past.
- `state: str = "queued"` — queued | running | done

_At `_reserved: dict[object, tuple[int, int]] = {}`:_

Downloads that passed storage_problem and have not finished: token -> (user_id, bytes).
Updates of different people run at the same time (PerUserUpdateProcessor), so two of them
can both be told "there is room" before either has written a byte; the room is promised
to the first at the moment it is told (reserve_storage), and storage_problem counts what
is promised as already used.

### `reset()`

For tests, which run each scenario on a fresh event loop: asyncio
primitives belong to the loop they were first used on.

### `storage_gate()`

Held around "ask storage_problem, then reserve_storage": the question is answered in a
thread, and without this two people's questions would both be answered before either
reservation was made.

### `reserve_storage()`

Promise `nbytes` of the temporary storage to a download that is about to start.
Give the token back to release_storage when the download ends, however it ends.

### `ahead_of()`

How many jobs will start before this one, across everybody.

This person's own jobs ahead of it all count. Another person's count only up to one more
than that, because the turns alternate by person (a person's second job waits for their
first, then queues behind everyone already waiting): someone who has queued ten jobs does
not put ten in front of a person who has none, only one. And while this person has a job
ahead of this one, this one is not in the line yet, so everybody waiting counts, whenever
they asked; with none, only those who asked before.

### `active_paths()`

Every staged file a queued or running job still needs. The sweeper and
\_discard_pending skip these: a file must not be deleted out from under a
conversion that is about to read it.

### `active_stems()`

The stems (job_stem) of every queued or running job: what the files and
folders a job makes for itself in the work directory are named after. The
sweeper leaves anything starting with one alone, whatever its age says --
an unpacked folder's mtime is whatever the ARCHIVE said its `./` was, and a
7z made with `-C dir .` can say 1970 (review, 2026-10-08).

### `uncharged_total()`

Credit this person's queued jobs will need when they start. Checked at
enqueue so a queue cannot be built that the balance will not cover.

## Before anything is downloaded or charged

### `storage_problem()`

None if there is room for `incoming_bytes` more; otherwise which limit
stops it -- "user", "global" or "disk" -- with the MB used and the limit.

Scans the staging directory rather than keeping a running total, because a
running total has to be right about every crash and restart, and a
directory listing of a handful of files cannot be wrong.

- At `promised = tuple(_reserved.values())`:
  What other downloads, begun and not finished, have been promised (reserve_storage).

### `unsendable_targets()`

The formats that would certainly come out too big to send, or that
the encoder itself cannot produce at this size.

## The worker

_At `_OWN_GROUP = {} if sys.platform == "win32" else {"start_new_session": True}`:_

The worker starts ffmpeg as a process of its own, and killing the worker
alone left ffmpeg running -- for up to FFMPEG_TIMEOUT_SECONDS, holding a
core and the output file while the next person's conversion waited behind
it. So a worker is started as the head of a process group of its own, and
the whole group is killed: taskkill /T on Windows, killpg elsewhere.

### `_start_worker()`

The one place a child process is started: convert_worker.py, run as
`main.py --module convert_bot.convert_worker` and handed its job as a single
JSON argument. A conversion and a look at a raw photo's size or a PDF's
pages both start here.

### `job_stem()`

What a job's own files in the work directory are named after.

### `run_worker()`

#### `run_worker.follow()`

##### `run_worker.follow.drain()`

Read as it comes rather than all at the end: progress lines go to
the job for its status message, everything else is kept for the
outcome, and stderr is drained alongside so it can never fill up.
In chunks, not lines: a line longer than the stream's 64 KB limit
makes readline() raise, and a reader that dies leaves the pipe full
and wait() waiting for it for ever -- found by a test that wrote
300 KB of stderr with no newline in it.

- `continue` — a line over the limit: not one the worker wrote; readline dropped it
- At `job.extra["projected_s"] = elapsed * total / done`:
  Killed here, and the reads carry on to the end of the
  pipes: returning instead would leave the stderr reader
  running with nobody to wait for it.

## Looking at a raw photo

_At `DESCRIBE_TIMEOUT_S = 30`:_

LibRaw parses the file of a stranger, in C. That used to happen on a thread of
the bot's own process (bot.py asked raw_convert.describe) -- and in the C
edition the process is five bots'. So the question is put to a short-lived
child, started the way a conversion is, and a crash in it costs a menu without
a size and nothing else.

### `_raw_info()`

(the RawInfo in the child's last line of output, "") or (None, why) for
anything that is not exactly that. The numbers are checked against each
other: the child is the bot's own code, but whatever is on its stdout is data.

### `describe_raw()`

raw_convert.describe(), in a child process: the RawInfo of a camera raw
photo, or None for one that cannot be read -- which includes a child that
crashed, ran past `timeout` (it is killed) or answered nonsense. None is
today's "unreadable": the menu says nothing about the size rather than
refuse a file the converter might still take.

- At `if why != "refused":`:
  "refused" is an ordinary unreadable file; anything else is the reader failing.

### `_ask_child()`

Run one describe job in a child (see \_start_worker): (its stdout, its
exit code), or None -- logged -- when it could not be run or overran
`timeout` and was killed.

At most DESCRIBE_AT_ONCE at a time: an album of ten raw photos is ten
uploads in a second, and ten interpreters at once beside a running
conversion is not what a 1 GB container is for (review, 2026-10-08).

### `_image_size()`

Megapixels from a describe_image child's last line, or None.

### `describe_image()`

A picture's size in megapixels from its header, read in a child process
from its header, or None for one that cannot be read.

Not in this process, because "reading the header" is not always only that.
Pillow's ICO plugin decodes the PNG inside an icon while opening it (a
684 KB file claiming 16x16 held a 13000x13000 PNG: 679 MB, review of
2026-10-08), its WebP plugin builds an animation's canvases in open(), and
a TIFF's tag arrays are read whole -- all of it on a thread of the bot's
own process, which by default is five bots'. A child that blows up
costs a menu without a size; the conversion itself still refuses past
MAX_MEGAPIXELS, in the worker.

### `_pdf_info()`

{"pages": n, "scanned": m} from a describe_pdf child's answer, or None
for anything that is not exactly that. Checked like the other describe
answers: whatever is on the child's stdout is data. `scanned` is None when
the child did not get as far as counting them.

The child says the page count on a line of its own the moment it has it
({"pages": n}) and everything on the last ({"ok": true, "pages": n,
"scanned": m}). A last line that is the whole answer is checked as one. When
there is none -- the child ran out of time and was killed, or failed after
the pages were known -- the page count alone is the answer, with no scanned
pages: the menu knows what a PDF weighs in pages and not how many of them
are pictures. A child that never said how many pages is None.

### `_ask_child_partial()`

\_ask_child, for a child whose first lines are worth having even when the
rest is not: (what it wrote, its exit code) -- the exit code None when it
overran `timeout` and was killed, with what it had written by then --
or None when it could not be run at all.

### `describe_pdf()`

How many pages a PDF has and how many of them are pictures without text
({"pages": n, "scanned": m}, ocr_convert.count_scanned), read in a child
process, or None for a PDF that cannot be read -- which includes a child
that crashed or answered nonsense before it knew the page count.

It is awaited inside the handler of the upload, and the bot handles one
update at a time, so it waits ocr_convert.OCR_DESCRIBE_SECONDS in total
(`timeout` overrides it) and not the half minute the other describe jobs
may take: a 1.8 KB PDF whose one page holds a million glyphs took 17 s to
look at and four million over two minutes, with every other person's
buttons waiting. The child reports the page count the moment it has it, so
past the deadline it is killed and the answer is {"pages": n, "scanned":
None}: the page count prices the file, and no scanned page is counted, so
no text is read and the PDF's TXT is today's. The same answer when the
child fails after the pages are known.

Not in this process: MuPDF parses a stranger's file in C, and this is the
bot's own, which by default is five bots'. None is the menu without
the numbers -- the PDF converts as it did before text recognition existed.

- `import ocr_convert` — here, not above: ocr_convert imports convert_utils, which imports this
- `if info is None and returncode not in (0, 2, None):` — 2 is an ordinary "not a readable PDF"

## Running a job

### `Runner`

What bot.py provides. None of these may raise, except `deliver`, whose
exception is the send failure.

#### `Runner.progress()`

Still running at `elapsed_s`. `next_s` is when the next update
comes, or None if the next thing is the ending at `limit_s`.

#### `Runner.queued()`

Still waiting, with `ahead` jobs in front of it -- called when that changes.

#### `Runner.card()`

Redraw the status while it runs; called every CARD_EVERY_S.

#### `Runner.hold()`

A paid conversion has started: record it, so a hard kill cannot keep its credit.

#### `Runner.release()`

It ended here: "held", "recovered" (already refunded elsewhere) or "absent".

### `remove_job_leftovers()`

Remove what a conversion named `stem` leaves in `work_dir` when it is
killed: the folder `<stem>-contents` an archive was unpacked into and the
padded copy `<stem>-source.<ext>` bsdtar reads. A job that is killed -- the
time limit, Stop, the out-of-memory killer -- is SIGKILLed, so no `finally`
in the worker ran, and a 2.7 MB .tgz left 629 MB behind (review, 2026-10-07).

Exactly those two names, directly inside `work_dir`, and nothing wider: the
stem is a user id, a job id and a suffix, and a name that could stand for
anything else is refused. A link by either name is removed as a link and
never followed; a folder that does not resolve to a child of `work_dir` is
left alone. Returns how many entries went.

- `continue` — a folder named like a source copy is not this job's

### `_finish()`

- At `logger.warning("Conversion %s ended %s after it had been refunded as lost", job.id, kind)`:
  Another process took this one for dead and has already given the
  credit back. Giving it back again would be paying twice.
- At `try:`:
  Whatever the job ended with, including the worker having been
  killed: see remove_job_leftovers. Last, and off the event loop
  (a folder of ten thousand files), so nothing above waits for it.

### `submit()`

Queue a job and start its task. Returns the job, whose `ahead_of` the
caller can use to say where it stands.

### `cancel()`

Stop a job at the request of the person it belongs to. Returns the job,
or None if there is no such job or it is somebody else's.
