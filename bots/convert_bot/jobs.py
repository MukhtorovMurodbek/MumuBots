"""The conversion queue, and what happens to the credit.

A conversion used to run inside the handler the format button called. Every
bot in this family has python-telegram-bot process updates one at a time --
on purpose: album batching depends on the parts of an album being handled in
the order they arrive -- so a four-minute conversion of a 200-megapixel photo
held the whole bot for four minutes. Nobody else's button did anything, and
the next tap failed outright with "query is too old", because Telegram gives
a button fifteen seconds to be answered.

So the handler now decides and enqueues, and the work happens here, in a task
of its own:

  one conversion at a time per person, in the order they asked;
  MAX_CONCURRENT across the whole bot, because the container is sized for
  one heavy job rather than for everyone's at once;
  each one in a child process (convert_worker.py) under a time limit that is
  actually enforced, because a process can be killed and a thread cannot.

What happens to the credit is decided here too, in one place, because it is
the part that has to be right every time:

  it is taken when the conversion STARTS, not when it is queued, so removing
  a queued job costs nothing and no job waits in a queue holding somebody's
  money;
  it is not refunded if the person stops a running conversion themselves;
  it is refunded, and the person told why, whenever the conversion ends for a
  reason that is the bot's rather than theirs;
  and the owner is told as well whenever that reason is a fault -- a
  timeout, a crash, a result too big to send, a send that failed, a restart
  -- rather than a file the bot declined.

This module knows nothing about Telegram. bot.py hands it a Runner that does
the talking, and that seam is what lets tests/test_jobs.py drive the whole
policy with a fake worker and no network.
"""
from __future__ import annotations

import asyncio
import itertools
import json
import logging
import math
import os
import shutil
import signal
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import big_files
import resources

logger = logging.getLogger(__name__)

HERE = Path(__spec__.origin if __spec__ else __file__).resolve().parent
WORKER, WORKER_ARGS = HERE.parents[1] / "main.py", ("--module", "convert_bot.convert_worker")

LOCAL_BOT_API = bool(os.environ.get("LOCAL_BOT_API_URL"))

# How much memory one conversion may count on: what the heaviest ordinary jobs
# measured (4K HDR video to MP4 about 887 MB for ffmpeg alone, a 1080p one 945 MB with
# unbounded threads), with room over. Together with the machine's size this decides
# how many run at once.
CONVERT_JOB_MEMORY_MB = max(1, int(os.environ.get("CONVERT_JOB_MEMORY_MB") or 1536))


def _ffmpeg_threads() -> int:
    """How many CPU threads one ffmpeg is held to (convert_utils.FFMPEG_THREADS)."""
    try:
        return max(1, int(os.environ.get("FFMPEG_THREADS") or 2))
    except ValueError:
        return 2


def concurrent_for(job_memory_mb: "int | None", cpus: float,
                   per_job_mb: int = CONVERT_JOB_MEMORY_MB, threads: "int | None" = None) -> int:
    """How many conversions fit at once in `job_memory_mb` of memory and `cpus` vCPUs: as many
    as the memory holds at `per_job_mb` each, and as the processor feeds at `threads` threads
    each, never more than 8 and never fewer than 1. One when the memory is not known (a
    laptop), which is what the bot always did."""
    if job_memory_mb is None:
        return 1
    threads = threads or _ffmpeg_threads()
    return max(1, min(8, job_memory_mb // per_job_mb, int(cpus // threads)))


def _max_concurrent() -> int:
    fixed = os.environ.get("CONVERT_MAX_CONCURRENT")
    if fixed:
        return max(1, int(fixed))
    return concurrent_for(resources.job_memory_mb("convert_bot"), resources.cpu_limit())


# How many conversions run at once across the whole bot: CONVERT_MAX_CONCURRENT when it is
# set, else what the machine holds (concurrent_for). Each person still has one at a time (the
# lock in _run), so with n slots n different people convert at once, and nobody's second job
# takes a slot from another person's first. On a small container this is 1, as it was: one
# heavy job at a time rather than two racing each other into an out-of-memory kill.
MAX_CONCURRENT = _max_concurrent()


def log_capacity() -> None:
    """Said once, when the bot starts."""
    job_memory = resources.job_memory_mb("convert_bot")
    logger.info("ConvertBot runs up to %d conversions at once (%s MB for its work, %d MB each, %g vCPU).",
                MAX_CONCURRENT, "unknown" if job_memory is None else job_memory,
                CONVERT_JOB_MEMORY_MB, resources.cpu_limit())


# How long one conversion may run before it is stopped and refunded. Ten
# minutes is the ceiling ffmpeg already had; the Pillow path had none at all,
# which is how a conversion could be waited on for twenty minutes.
TIME_LIMIT_S = int(os.environ.get("CONVERT_TIME_LIMIT_SECONDS") or 600)

# Free conversions (under 3 MB) get a much shorter one. Nothing that small
# megabyte needs a minute, and a free job that runs to the paid limit costs
# real hosting for no revenue at all -- the one case where the pricing could
# not "at least cover the hardware".
FREE_TIME_LIMIT_S = int(os.environ.get("CONVERT_FREE_TIME_LIMIT_SECONDS") or 60)

# A paid job's own limit, since 2.0.0: TIME_BASE_S, and a second more for
# every megabyte of work -- the file and its estimated result, times how hard
# the pair is to run (convert_utils.work_factor) -- up to TIME_LIMIT_S. The
# price is worked out from it (shared/pricing.py), so a small job is not
# charged for ten minutes it will never need, and a big one gets the time it
# does. A job on course to miss its limit is stopped early and refunded.
TIME_BASE_S = int(os.environ.get("CONVERT_TIME_BASE_SECONDS") or 120)
TIME_PER_MB_S = float(os.environ.get("CONVERT_TIME_PER_MB_SECONDS") or 1.0)


def time_limit_for(billable_bytes: int, work: float = 1.0, paid: bool = True) -> int:
    """How long a conversion may run, in seconds."""
    if not paid:
        return FREE_TIME_LIMIT_S
    wanted = TIME_BASE_S + TIME_PER_MB_S * work * billable_bytes / (1024 * 1024)
    return int(min(TIME_LIMIT_S, max(TIME_BASE_S, wanted)))


def ocr_time_limit(ocr_pages: int, paid: bool, billable: int = 0, work: float = 1.0) -> int:
    """How long a job that recognises text may run: what any conversion of that
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
    (bot._enqueue, through the same ocr_limit), so the two cannot differ."""
    if not paid:
        return FREE_TIME_LIMIT_S
    import ocr_convert      # here, not above: ocr_convert imports convert_utils, which imports this
    return int(min(TIME_LIMIT_S, time_limit_for(billable, work, True) + max(0, ocr_pages) * ocr_convert.OCR_PAGE_S))


def job_limit(job: "Job") -> int:
    """The limit this job runs under: its own, or for a job made before it
    had one, the flat limit of its kind."""
    return job.time_limit or (TIME_LIMIT_S if job.price > 0 else FREE_TIME_LIMIT_S)


# A conversion is stopped early when it is on course to miss its limit: once
# it has run PROJECT_AFTER_S and reported some progress, the time it will
# take at that rate is projected, and if that is past the limit by more than
# PROJECT_MARGIN it stops there and is refunded -- rather than keep somebody
# waiting ten minutes for the same refund. The margin is for the first
# seconds of a job, which are its slowest.
PROJECT_AFTER_S = 30
PROJECT_MARGIN = 1.2

# Updates while a long conversion runs. The owner: "Maybe 10 minutes is too
# long to go with no context. Implement warnings after 2 minute of doing work
# ... that there will be 2 more warnings, 3 minutes and 8 minutes later. the
# last warning (that triggers after 10 minutes of work) should say it failed
# or whatever happened." So a note at two minutes, another at five, and at the
# time limit the ending itself -- which the ending already is. A mark at or
# past a job's own limit is dropped, so a free conversion sends none.
PROGRESS_MARKS_S = (120, 300)

# How many conversions one person may have waiting or running at once.
MAX_QUEUED_PER_USER = max(1, int(os.environ.get("CONVERT_MAX_QUEUED_PER_USER") or 5))

# Temporary storage, per person and for the whole bot, plus the free disk
# that must remain afterwards. Checked before a file is downloaded, against
# the size Telegram declares, so a full disk refuses an upload with a sentence
# instead of failing half-way through writing it.
#
# The per-person default is sized from the batch limits rather than picked:
# an album may hold MAX_BATCH files of MAX_FILE_MB each, and a smaller share
# refused the ninth and tenth scans of a ten-page PDF while it was still
# arriving, so the PDF quietly came out short. Since the limit is 2000 MB
# that is no longer a size anybody has, so it is the larger of an album's
# 2000 MB and the largest file twice over -- it and what it becomes.


def _default_user_storage_mb() -> int:
    try:
        import convert_utils
        import formats
        album = formats.MAX_BATCH * convert_utils.MAX_FILE_MB
        return min(album, max(2000, 2 * convert_utils.MAX_FILE_MB)) + 50
    except Exception:
        return 450


USER_STORAGE_MB = int(os.environ.get("CONVERT_USER_STORAGE_MB") or max(120, _default_user_storage_mb()))
STORAGE_MB = int(os.environ.get("CONVERT_STORAGE_MB") or max(1024, USER_STORAGE_MB + 200))
MIN_FREE_MB = int(os.environ.get("CONVERT_MIN_FREE_MB") or 300)

# The largest picture converted at all. 210 covers the 200 MP sensors in
# current flagship phones -- the owner's own Galaxy S23 Ultra among them --
# which is exactly the professional case worth supporting. Above it a single
# decode needs more memory than a small container has.
MAX_MEGAPIXELS = int(os.environ.get("CONVERT_MAX_MEGAPIXELS") or 210)

# A paid conversion is recorded while it runs (db.conversions_in_flight), and
# the running process stamps its record this often. A record unstamped for
# HOLD_STALE_S belongs to a process that was killed outright, and its credit
# goes back -- the graceful path that refunds an interrupted conversion never
# ran. The margin is wide on purpose: conversions run in a child process, so
# nothing short of the process dying stops the stamping.
# How often a running conversion's status message is redrawn: its stage, its
# progress and the time. Every redraw is an edit, and Telegram limits how
# fast one chat's messages may be edited; every few seconds reads as live.
CARD_EVERY_S = 3.0

HOLD_TOUCH_S = 30
HOLD_STALE_S = 120

# Optional hard memory cap on the worker process (POSIX). 0 leaves it to the
# container. See convert_worker._cap_memory.
WORKER_MAX_MEMORY_MB = int(os.environ.get("CONVERT_WORKER_MAX_MEMORY_MB") or 0)

# What a bot may send back. The cloud Bot API refuses a document over 50 MB
# with "Request Entity Too Large" -- which is what the owner's two 200 MP
# conversions ran into, after converting successfully.
SEND_LIMIT_MB = big_files.BOT_API_SEND_BYTES // (1024 * 1024)
SEND_LIMIT_BYTES = SEND_LIMIT_MB * 1024 * 1024

# Past that, a result goes over MTProto (big_files.py) or through a local Bot
# API server, and both would carry 2000 MB. They are not allowed to without
# limit, because every price has to cover the worst job it can buy
# (tests/test_pricing.py) and sending back is what costs: a job may send up
# to SEND_HEADROOM times its estimated result, and never less than the Bot
# API's own 50 MB. The headroom is there because the estimate is a judgement:
# a result can overshoot it and still be sent, just not without bound, and
# the price was worked out on this limit, not on the estimate.
BIG_SEND_MAX_BYTES = big_files.MTPROTO_MAX_BYTES
SEND_HEADROOM = 3


def big_sends(chat_id: int) -> bool:
    """Whether a result over 50 MB can reach this chat at all."""
    return LOCAL_BOT_API or big_files.usable(chat_id)


def big_send_limit(out_estimate: int) -> int:
    """The largest result a conversion estimated at `out_estimate` bytes may
    send where big sends can reach, which is what its price covers."""
    return min(BIG_SEND_MAX_BYTES, max(SEND_LIMIT_BYTES, SEND_HEADROOM * max(0, out_estimate or 0)))


def send_limit_for(in_bytes: int, out_estimate: int | None, chat_id: int) -> int:
    """The largest result this conversion may send back, in bytes."""
    if not big_sends(chat_id):
        return SEND_LIMIT_BYTES
    return big_send_limit(out_estimate if out_estimate else in_bytes)


def send_ceiling_mb(chat_id: int) -> int:
    """The most any result could be sent at in this chat, for sentences
    about a format that is off the menu for being too big."""
    return (BIG_SEND_MAX_BYTES if big_sends(chat_id) else SEND_LIMIT_BYTES) // (1024 * 1024)


# Bytes per pixel a lossless picture comes out at. Only formats whose size is
# predictable from the pixel count are listed; everything lossy is left to the
# result-size check after the conversion, because a JPEG's size depends on
# what is in the picture rather than how many pixels it has.
#
# Deliberately on the LOW side of what a photo produces, so a format is only
# kept off the menu when it is clearly going to be over the limit. A format
# that slips through is caught after converting, refunded, and reported --
# the estimate saves somebody a wait, the check is what keeps it honest.
#
# Calibrated against a 149.8 MP photo (the owner's S23 Ultra at 16:9): PNG
# came out at 163 MB and QOI at 150 MB, about 1.0-1.1 bytes a pixel, on a
# test image noisier than a real photo. Half that is used here, which still
# keeps both off the menu above roughly 100 MP -- where they are certain to
# be over 50 MB -- without hiding them from pictures that might fit.
LOSSLESS_BYTES_PER_PIXEL = {
    "bmp": 3.0,
    "ppm": 3.0,
    "tga": 3.0,
    "tiff": 0.6,
    "png": 0.5,
    "qoi": 0.5,
}

# The same for a picture developed from a camera raw file, which is not what a
# photo the camera already made is: TIFF comes out at 16 bits a channel,
# uncompressed -- six bytes a pixel, exactly -- where an ordinary TIFF is
# compressed and unpredictable.
RAW_LOSSLESS_BYTES_PER_PIXEL = {"tiff": 6.0}

# Encoders with a hard ceiling of their own, in megapixels. x265 refuses more
# than 142,606,336 luma samples (HEVC level 6.2), so HEIC of a 150 MP photo
# fails a few seconds in with an encoder error. Leaving it off the menu is
# better than charging, failing and refunding.
TARGET_MAX_MEGAPIXELS = {
    "heic": 142.6,
}

# How a job can end, other than with a delivered file.
USER_CANCELLED = "user_cancelled"
NO_CREDIT = "no_credit"
REFUSED = "refused"
TIMEOUT = "timeout"
# On course to miss its limit, and stopped before it did (PROJECT_AFTER_S).
TOO_SLOW = "too_slow"
CRASH = "crash"
# The converter was killed for memory: the host is too small for this file
# (a 4K HDR video in a 1 GB container). Refunded, and told as that -- not an
# alert, like TOO_SLOW: the size of the host is known, a smaller file is the
# person's to send.
NO_MEMORY = "no_memory"
TOO_LARGE = "too_large"
SEND_FAILED = "send_failed"
INTERRUPTED = "interrupted"
# Reached the front while the owner had paused the bot for an update. Nothing
# started and nothing was charged, so it is neither refunded nor reported --
# before this it was INTERRUPTED, which paged the owner once per queued job
# and told each person the bot had restarted in the middle of their file.
PAUSED = "paused"

# Refunded when the job had been charged: every ending except the person
# stopping it themselves. NO_CREDIT is never charged in the first place.
REFUNDED = frozenset({REFUSED, TIMEOUT, TOO_SLOW, CRASH, NO_MEMORY, TOO_LARGE, SEND_FAILED, INTERRUPTED})

# Worth the owner's attention: the bot's own faults. A file the bot declined
# (REFUSED) is the bot working as designed, and an alert for every unreadable
# upload would bury the failures that matter.
ALERTED = frozenset({TIMEOUT, CRASH, TOO_LARGE, SEND_FAILED, INTERRUPTED})


# Queue order comes from a counter, not a clock. time.monotonic() ticks about
# every 16 ms on Windows, so two jobs queued back to back got the same
# timestamp and "how many are ahead of me" answered none -- the lock ran
# them in the right order, but the sentence telling people so was wrong.
_sequence = itertools.count()


@dataclass
class Job:
    user_id: int
    chat_id: int
    paths: list
    src_ext: str
    target_ext: str
    size: int
    price: int
    lang: str
    username: str | None = None
    keep_file: bool = False
    # The largest result this job may send back -- send_limit_for().
    send_limit: int = SEND_LIMIT_BYTES
    # How long it may run -- time_limit_for(). 0: the flat limit of its kind.
    time_limit: int = 0
    # Text recognition (ocr_convert.py), both fixed when the job is queued. The
    # language is the person's choice then ("eng", "rus", "uzb"; "" for a job
    # that reads no text); ocr_pages is the number of pages the menu counted
    # and the price was made for, which the worker will not read past.
    ocr_lang: str = ""
    ocr_pages: int = 0
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])
    state: str = "queued"          # queued | running | done
    charged: bool = False
    user_cancelled: bool = False
    task: asyncio.Task | None = None
    proc: object | None = None
    queued_at: float = field(default_factory=time.monotonic)
    seq: int = field(default_factory=lambda: next(_sequence))
    started_at: float | None = None
    extra: dict = field(default_factory=dict)


@dataclass
class Outcome:
    ok: bool
    kind: str | None = None
    message: str = ""
    path: str | None = None
    zipped: bool = False
    items: int = 1
    bytes: int = 0


_jobs: dict[str, Job] = {}
_user_locks: dict[int, asyncio.Lock] = {}
_slots: asyncio.Semaphore | None = None
# Downloads that passed storage_problem and have not finished: token -> (user_id, bytes).
# Updates of different people run at the same time (PerUserUpdateProcessor), so two of them
# can both be told "there is room" before either has written a byte; the room is promised
# to the first at the moment it is told (reserve_storage), and storage_problem counts what
# is promised as already used.
_reserved: dict[object, tuple[int, int]] = {}
_storage_lock: asyncio.Lock | None = None


def reset() -> None:
    """For tests, which run each scenario on a fresh event loop: asyncio
    primitives belong to the loop they were first used on."""
    global _slots, _storage_lock
    _jobs.clear()
    _user_locks.clear()
    _reserved.clear()
    _slots = None
    _storage_lock = None


def _global_slots() -> asyncio.Semaphore:
    global _slots
    if _slots is None:
        _slots = asyncio.Semaphore(MAX_CONCURRENT)
    return _slots


def get(job_id: str) -> Job | None:
    return _jobs.get(job_id)


def jobs_for(user_id: int) -> list[Job]:
    return sorted((job for job in _jobs.values() if job.user_id == user_id),
                  key=lambda job: job.seq)


def storage_gate() -> asyncio.Lock:
    """Held around "ask storage_problem, then reserve_storage": the question is answered in a
    thread, and without this two people's questions would both be answered before either
    reservation was made."""
    global _storage_lock
    if _storage_lock is None:
        _storage_lock = asyncio.Lock()
    return _storage_lock


def reserve_storage(user_id: int, nbytes: int) -> object:
    """Promise `nbytes` of the temporary storage to a download that is about to start.
    Give the token back to release_storage when the download ends, however it ends."""
    token = object()
    _reserved[token] = (user_id, max(0, int(nbytes or 0)), resources.reserve_disk("convert_bot", nbytes))
    return token


def release_storage(token: object) -> None:
    if token is not None:
        held = _reserved.pop(token, None)
        if held is not None:
            resources.release_disk(held[2])


def ahead_of(job: Job) -> int:
    """How many jobs will start before this one, across everybody.

    This person's own jobs ahead of it all count. Another person's count only up to one more
    than that, because the turns alternate by person (a person's second job waits for their
    first, then queues behind everyone already waiting): someone who has queued ten jobs does
    not put ten in front of a person who has none, only one. And while this person has a job
    ahead of this one, this one is not in the line yet, so everybody waiting counts, whenever
    they asked; with none, only those who asked before."""
    own = 0
    others: dict[int, int] = {}
    waiting_behind_own = any(other.user_id == job.user_id and other is not job and other.seq < job.seq
                             and other.state in ("queued", "running") for other in _jobs.values())
    for other in _jobs.values():
        if other is job or other.state not in ("queued", "running"):
            continue
        if other.user_id == job.user_id:
            if other.seq < job.seq:
                own += 1
        elif waiting_behind_own or other.seq < job.seq:
            others[other.user_id] = others.get(other.user_id, 0) + 1
    return own + sum(min(count, own + 1) for count in others.values())


def active_paths() -> set[str]:
    """Every staged file a queued or running job still needs. The sweeper and
    _discard_pending skip these: a file must not be deleted out from under a
    conversion that is about to read it."""
    return {str(path) for job in _jobs.values() for path in job.paths}


def active_stems() -> set[str]:
    """The stems (job_stem) of every queued or running job: what the files and
    folders a job makes for itself in the work directory are named after. The
    sweeper leaves anything starting with one alone, whatever its age says --
    an unpacked folder's mtime is whatever the ARCHIVE said its `./` was, and a
    7z made with `-C dir .` can say 1970 (review, 2026-10-08)."""
    return {job_stem(job) for job in _jobs.values()}


def uncharged_total(user_id: int) -> int:
    """Credit this person's queued jobs will need when they start. Checked at
    enqueue so a queue cannot be built that the balance will not cover."""
    return sum(job.price for job in _jobs.values()
               if job.user_id == user_id and not job.charged)


# ---------------------------------------------------------------------------
# Before anything is downloaded or charged
# ---------------------------------------------------------------------------

def storage_problem(user_id: int, incoming_bytes: int, upload_dir) -> tuple[str, int, int] | None:
    """None if there is room for `incoming_bytes` more; otherwise which limit
    stops it -- "user", "global" or "disk" -- with the MB used and the limit.

    Scans the staging directory rather than keeping a running total, because a
    running total has to be right about every crash and restart, and a
    directory listing of a handful of files cannot be wrong."""
    upload_dir = Path(upload_dir)
    mb = 1024 * 1024
    used_all = used_user = 0
    prefix = f"{user_id}-"
    if upload_dir.is_dir():
        for path in upload_dir.iterdir():
            try:
                if path.is_file():
                    size = path.stat().st_size
                    used_all += size
                    if path.name.startswith(prefix):
                        used_user += size
            except OSError:
                continue
    # What other downloads, begun and not finished, have been promised (reserve_storage).
    promised = tuple(_reserved.values())
    promised_all = sum(size for _, size, _ in promised)
    used_all += promised_all
    used_user += sum(size for owner, size, _ in promised if owner == user_id)
    promised_all += resources.reserved_disk_bytes() - resources.reserved_disk_bytes("convert_bot")
    if USER_STORAGE_MB and used_user + incoming_bytes > USER_STORAGE_MB * mb:
        return ("user", used_user // mb, USER_STORAGE_MB)
    if STORAGE_MB and used_all + incoming_bytes > STORAGE_MB * mb:
        return ("global", used_all // mb, STORAGE_MB)
    try:
        probe = upload_dir if upload_dir.is_dir() else upload_dir.parent
        free = shutil.disk_usage(probe).free
    except OSError:
        free = None
    if free is not None and MIN_FREE_MB and free - incoming_bytes - promised_all < MIN_FREE_MB * mb:
        return ("disk", free // mb, MIN_FREE_MB)
    return None


def estimate_output_bytes(megapixels: float | None, target_ext: str, raw: bool = False) -> int | None:
    ratio = (RAW_LOSSLESS_BYTES_PER_PIXEL.get(target_ext) if raw else None) or LOSSLESS_BYTES_PER_PIXEL.get(target_ext)
    if megapixels is None or ratio is None:
        return None
    return int(megapixels * 1_000_000 * ratio)


def unsendable_targets(megapixels: float | None, targets, in_bytes: int = 0,
                       chat_id: int = 0, raw: bool = False) -> list[str]:
    """The formats that would certainly come out too big to send, or that
    the encoder itself cannot produce at this size."""
    out = []
    for target in targets:
        ceiling = TARGET_MAX_MEGAPIXELS.get(target)
        if megapixels is not None and ceiling is not None and megapixels > ceiling:
            out.append(target)
            continue
        estimate = estimate_output_bytes(megapixels, target, raw)
        if estimate is not None and estimate > send_limit_for(in_bytes, estimate, chat_id):
            out.append(target)
    return out


# ---------------------------------------------------------------------------
# The worker
# ---------------------------------------------------------------------------

# The worker starts ffmpeg as a process of its own, and killing the worker
# alone left ffmpeg running -- for up to FFMPEG_TIMEOUT_SECONDS, holding a
# core and the output file while the next person's conversion waited behind
# it. So a worker is started as the head of a process group of its own, and
# the whole group is killed: taskkill /T on Windows, killpg elsewhere.
_OWN_GROUP = {} if sys.platform == "win32" else {"start_new_session": True}


async def _kill(proc) -> None:
    if proc is None or proc.returncode is not None:
        return
    try:
        if sys.platform == "win32":
            killer = await asyncio.create_subprocess_exec(
                "taskkill", "/F", "/T", "/PID", str(proc.pid),
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
            await killer.wait()
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    except Exception:
        logger.debug("Could not kill the conversion worker's group", exc_info=True)
    if proc.returncode is None:
        try:
            proc.kill()
        except ProcessLookupError:
            return
        except Exception:
            logger.debug("Could not kill conversion worker", exc_info=True)
    try:
        await asyncio.wait_for(proc.wait(), timeout=10)
    except Exception:
        logger.debug("Conversion worker did not exit after kill", exc_info=True)


def _parse(returncode, out: bytes, err: bytes) -> Outcome:
    lines = [line for line in (out or b"").decode("utf-8", "replace").splitlines() if line.strip()]
    if lines:
        try:
            data = json.loads(lines[-1])
        except ValueError:
            data = None
        if isinstance(data, dict):
            if data.get("ok"):
                return Outcome(True, None, "", data.get("path"), bool(data.get("zipped")),
                               int(data.get("items") or 1), int(data.get("bytes") or 0))
            return Outcome(False, data.get("kind") or CRASH, str(data.get("message") or ""))
    tail = (err or b"").decode("utf-8", "replace").strip()[-400:]
    if returncode is not None and (returncode < 0 or returncode == 137):
        return Outcome(False, CRASH,
                       f"the worker was killed (exit {returncode}), most likely for memory. {tail}".strip())
    return Outcome(False, CRASH, f"the worker exited {returncode} without a result. {tail}".strip())


async def _start_worker(payload: dict):
    """The one place a child process is started: convert_worker.py, run as
    `main.py --module convert_bot.convert_worker` and handed its job as a single
    JSON argument. A conversion and a look at a raw photo's size or a PDF's
    pages both start here."""
    return await asyncio.create_subprocess_exec(
        sys.executable, "-u", str(WORKER), *WORKER_ARGS, json.dumps(payload),
        cwd=str(HERE), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, **_OWN_GROUP,
    )


def job_stem(job: Job) -> str:
    """What a job's own files in the work directory are named after."""
    return f"{job.user_id}-{job.id}-out"


async def run_worker(job: Job, work_dir, stem: str, time_limit: int) -> Outcome:
    payload = {
        "in_paths": [str(path) for path in job.paths],
        "src_ext": job.src_ext,
        "target_ext": job.target_ext,
        "work_dir": str(work_dir),
        "stem": stem,
        "max_pixels": MAX_MEGAPIXELS * 1_000_000,
        "max_memory_mb": WORKER_MAX_MEMORY_MB,
        "ocr_lang": job.ocr_lang,
        "ocr_pages": job.ocr_pages,
    }
    proc = await _start_worker(payload)
    job.proc = proc
    kept: list[bytes] = []
    errors = bytearray()

    async def follow():
        # Read as it comes rather than all at the end: progress lines go to
        # the job for its status message, everything else is kept for the
        # outcome, and stderr is drained alongside so it can never fill up.
        # In chunks, not lines: a line longer than the stream's 64 KB limit
        # makes readline() raise, and a reader that dies leaves the pipe full
        # and wait() waiting for it for ever -- found by a test that wrote
        # 300 KB of stderr with no newline in it.
        async def drain():
            while chunk := await proc.stderr.read(65536):
                errors.extend(chunk)
                del errors[:-8000]
        draining = asyncio.ensure_future(drain())
        while True:
            try:
                line = await proc.stdout.readline()
            except ValueError:
                continue          # a line over the limit: not one of ours; readline dropped it
            if not line:
                break
            try:
                data = json.loads(line) if line.startswith(b'{"progress"') else None
            except ValueError:
                data = None
            if data is not None:
                done, total = data["progress"]
                job.extra["progress"] = (done, total)
                elapsed = time.monotonic() - started
                if (elapsed >= PROJECT_AFTER_S and total and done > 0 and "projected_s" not in job.extra
                        and elapsed * total / done > time_limit * PROJECT_MARGIN):
                    # Killed here, and the reads carry on to the end of the
                    # pipes: returning instead would leave the stderr reader
                    # running with nobody to wait for it.
                    job.extra["projected_s"] = elapsed * total / done
                    await _kill(proc)
            else:
                kept.append(line)
        await proc.wait()
        await draining

    started = time.monotonic()
    try:
        await asyncio.wait_for(follow(), timeout=time_limit)
        if "projected_s" in job.extra:
            return Outcome(False, TOO_SLOW, f"on course for {job.extra['projected_s']:.0f}s against a "
                                            f"{time_limit}s limit, stopped after {time.monotonic() - started:.0f}s")
        out, err = b"".join(kept), bytes(errors)
    except asyncio.TimeoutError:
        await _kill(proc)
        return Outcome(False, TIMEOUT, f"ran past the {time_limit}s limit")
    except asyncio.CancelledError:
        await asyncio.shield(_kill(proc))
        raise
    finally:
        job.proc = None
    return _parse(proc.returncode, out, err)


# ---------------------------------------------------------------------------
# Looking at a raw photo
# ---------------------------------------------------------------------------

# LibRaw parses the file of a stranger, in C. That used to happen on a thread of
# the bot's own process (bot.py asked raw_convert.describe) -- and in the C
# edition the process is five bots'. So the question is put to a short-lived
# child, started the way a conversion is, and a crash in it costs a menu without
# a size and nothing else.
DESCRIBE_TIMEOUT_S = 30


def _raw_info(answer: bytes):
    """(the RawInfo in the child's last line of output, "") or (None, why) for
    anything that is not exactly that. The numbers are checked against each
    other: the child is our own code, but whatever is on its stdout is data."""
    import raw_convert
    lines = [line for line in (answer or b"").decode("utf-8", "replace").splitlines() if line.strip()]
    if not lines:
        return None, "no answer"
    try:
        data = json.loads(lines[-1])
    except ValueError:
        return None, "an answer that is not JSON"
    if not isinstance(data, dict):
        return None, "an answer that is not an object"
    if not data.get("ok"):
        return None, str(data.get("kind") or "crash")
    try:
        fields = data["info"]
        sizes = [fields[name] for name in ("width", "height", "out_width", "out_height")]
        sizes_mp = [fields["megapixels"], fields["out_megapixels"]]
        half = fields["half"]
        if (not isinstance(half, bool) or any(isinstance(n, bool) or not isinstance(n, int) or n <= 0 for n in sizes)
                or any(isinstance(n, bool) or not isinstance(n, (int, float)) or not math.isfinite(n) or n <= 0
                       for n in sizes_mp)):
            return None, "an answer that makes no sense"
        width, height, out_width, out_height = sizes
        megapixels, out_megapixels = (float(n) for n in sizes_mp)
        if (not math.isclose(megapixels, width * height / 1_000_000, rel_tol=1e-9)
                or not math.isclose(out_megapixels, out_width * out_height / 1_000_000, rel_tol=1e-9)):
            return None, "an answer that makes no sense"
    except (KeyError, TypeError):
        return None, "an answer that makes no sense"
    return raw_convert.RawInfo(width, height, megapixels, half, out_width, out_height, out_megapixels), ""


async def describe_raw(path, src_ext: str, timeout: float = DESCRIBE_TIMEOUT_S):
    """raw_convert.describe(), in a child process: the RawInfo of a camera raw
    photo, or None for one that cannot be read -- which includes a child that
    crashed, ran past `timeout` (it is killed) or answered nonsense. None is
    today's "unreadable": the menu says nothing about the size rather than
    refuse a file the converter might still take."""
    payload = {"kind": "describe_raw", "path": str(path), "src_ext": src_ext,
               "max_memory_mb": WORKER_MAX_MEMORY_MB}
    answered = await _ask_child(payload, timeout, "a raw photo's size")
    if answered is None:
        return None
    out, returncode = answered
    info, why = _raw_info(out)
    if info is None:
        # "refused" is an ordinary unreadable file; anything else is the reader failing.
        if why != "refused":
            logger.warning("Reading a raw photo's size failed (%s, exit %s)", why, returncode)
        return None
    return info


async def _ask_child(payload: dict, timeout: float, what: str):
    """Run one describe job in a child (see _start_worker): (its stdout, its
    exit code), or None -- logged -- when it could not be run or overran
    `timeout` and was killed.

    At most DESCRIBE_AT_ONCE at a time: an album of ten raw photos is ten
    uploads in a second, and ten interpreters at once beside a running
    conversion is not what a 1 GB container is for (review, 2026-10-08)."""
    async with _describe_slots:
        return await _ask_child_now(payload, timeout, what)


DESCRIBE_AT_ONCE = 2
_describe_slots = asyncio.Semaphore(DESCRIBE_AT_ONCE)


async def _ask_child_now(payload: dict, timeout: float, what: str):
    proc = None
    try:
        proc = await _start_worker(payload)
        out, _err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        await _kill(proc)
        logger.warning("Reading %s took over %.0f s and was stopped", what, timeout)
        return None
    except asyncio.CancelledError:
        await asyncio.shield(_kill(proc))
        raise
    except Exception as exc:
        await _kill(proc)
        logger.warning("Reading %s failed to run (%s)", what, type(exc).__name__)
        return None
    return out, proc.returncode


def _image_size(answer: bytes) -> "float | None":
    """Megapixels from a describe_image child's last line, or None."""
    lines = [line for line in (answer or b"").decode("utf-8", "replace").splitlines() if line.strip()]
    try:
        data = json.loads(lines[-1])
        width, height = data["width"], data["height"]
    except (IndexError, ValueError, TypeError, KeyError):
        return None
    if not data.get("ok") or any(isinstance(n, bool) or not isinstance(n, int) or n <= 0
                                 for n in (width, height)):
        return None
    return (width * height) / 1_000_000


async def describe_image(path, timeout: float = DESCRIBE_TIMEOUT_S) -> "float | None":
    """A picture's size in megapixels from its header, read in a child process
    from its header, or None for one that cannot be read.

    Not in this process, because "reading the header" is not always only that.
    Pillow's ICO plugin decodes the PNG inside an icon while opening it (a
    684 KB file claiming 16x16 held a 13000x13000 PNG: 679 MB, review of
    2026-10-08), its WebP plugin builds an animation's canvases in open(), and
    a TIFF's tag arrays are read whole -- all of it on a thread of the bot's
    own process, which by default is five bots'. A child that blows up
    costs a menu without a size; the conversion itself still refuses past
    MAX_MEGAPIXELS, in the worker."""
    answered = await _ask_child({"kind": "describe_image", "path": str(path),
                                 "max_memory_mb": WORKER_MAX_MEMORY_MB}, timeout, "a picture's size")
    return None if answered is None else _image_size(answered[0])


def _count(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _pdf_info(answer: bytes) -> "dict | None":
    """{"pages": n, "scanned": m} from a describe_pdf child's answer, or None
    for anything that is not exactly that. Checked like the other describe
    answers: whatever is on the child's stdout is data. `scanned` is None when
    the child did not get as far as counting them.

    The child says the page count on a line of its own the moment it has it
    ({"pages": n}) and everything on the last ({"ok": true, "pages": n,
    "scanned": m}). A last line that is the whole answer is checked as one. When
    there is none -- the child ran out of time and was killed, or failed after
    the pages were known -- the page count alone is the answer, with no scanned
    pages: the menu knows what a PDF weighs in pages and not how many of them
    are pictures. A child that never said how many pages is None."""
    lines = [line for line in (answer or b"").decode("utf-8", "replace").splitlines() if line.strip()]
    try:
        last = json.loads(lines[-1])
    except (IndexError, ValueError):
        last = None
    if isinstance(last, dict) and last.get("ok"):
        pages, scanned = last.get("pages"), last.get("scanned", False)
        if not _count(pages) or not (scanned is None or (_count(scanned) and scanned <= pages)):
            return None
        return {"pages": pages, "scanned": scanned}
    for line in lines:
        try:
            data = json.loads(line)
        except ValueError:
            continue
        if isinstance(data, dict) and "ok" not in data and _count(data.get("pages")):
            return {"pages": data["pages"], "scanned": None}
    return None


async def _ask_child_partial(payload: dict, timeout: float, what: str):
    """_ask_child, for a child whose first lines are worth having even when the
    rest is not: (what it wrote, its exit code) -- the exit code None when it
    overran `timeout` and was killed, with what it had written by then --
    or None when it could not be run at all."""
    async with _describe_slots:
        proc = None
        written: list[bytes] = []
        try:
            proc = await _start_worker(payload)

            async def read_out():
                while chunk := await proc.stdout.read(65536):
                    written.append(chunk)
                await proc.wait()

            async def drain_err():
                while await proc.stderr.read(65536):
                    pass
            await asyncio.wait_for(asyncio.gather(read_out(), drain_err()), timeout=timeout)
        except asyncio.TimeoutError:
            await _kill(proc)
            logger.warning("Reading %s took over %.0f s and was stopped", what, timeout)
            return b"".join(written), None
        except asyncio.CancelledError:
            await asyncio.shield(_kill(proc))
            raise
        except Exception as exc:
            await _kill(proc)
            logger.warning("Reading %s failed to run (%s)", what, type(exc).__name__)
            return None
        return b"".join(written), proc.returncode


async def describe_pdf(path, timeout: "float | None" = None) -> "dict | None":
    """How many pages a PDF has and how many of them are pictures without text
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
    the numbers -- the PDF converts as it did before text recognition existed."""
    if timeout is None:
        import ocr_convert      # here, not above: ocr_convert imports convert_utils, which imports this
        timeout = ocr_convert.OCR_DESCRIBE_SECONDS
    answered = await _ask_child_partial({"kind": "describe_pdf", "path": str(path),
                                         "max_memory_mb": WORKER_MAX_MEMORY_MB}, timeout, "a PDF's pages")
    if answered is None:
        return None
    out, returncode = answered
    info = _pdf_info(out)
    if info is None and returncode not in (0, 2, None):     # 2 is an ordinary "not a readable PDF"
        logger.warning("Reading a PDF's pages failed (exit %s)", returncode)
    return info


# ---------------------------------------------------------------------------
# Running a job
# ---------------------------------------------------------------------------

class Runner:
    """What bot.py provides. None of these may raise, except `deliver`, whose
    exception is the send failure."""

    def work_dir(self) -> Path:
        raise NotImplementedError

    async def refuse_start(self, job: Job) -> bool:
        return False

    async def charge(self, job: Job) -> int | None:
        raise NotImplementedError

    async def started(self, job: Job, balance: int) -> None:
        return None

    async def progress(self, job: Job, stage: int, stages: int, elapsed_s: float,
                       next_s: float | None, limit_s: int) -> None:
        """Still running at `elapsed_s`. `next_s` is when the next update
        comes, or None if the next thing is the ending at `limit_s`."""
        return None

    async def deliver(self, job: Job, outcome: Outcome) -> None:
        raise NotImplementedError

    async def succeeded(self, job: Job, outcome: Outcome) -> None:
        return None

    async def refund(self, job: Job, kind: str) -> int:
        raise NotImplementedError

    async def queued(self, job: Job, ahead: int) -> None:
        """Still waiting, with `ahead` jobs in front of it -- called when that changes."""
        return None

    async def card(self, job: Job) -> None:
        """Redraw the status while it runs; called every CARD_EVERY_S."""
        return None

    async def hold(self, job: Job) -> None:
        """A paid conversion has started: record it, so a hard kill cannot keep its credit."""
        return None

    async def release(self, job: Job) -> str:
        """It ended here: "held", "recovered" (already refunded elsewhere) or "absent"."""
        return "absent"

    async def failed(self, job: Job, kind: str, detail: str, refunded_to: int | None) -> None:
        return None

    async def alert(self, job: Job, kind: str, detail: str, elapsed: float,
                    refunded_to: int | None) -> None:
        return None


async def _safe(awaitable):
    try:
        return await awaitable
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("A conversion job hook failed")
        return None


def _unlink(path) -> None:
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:
        logger.debug("Could not remove %s", path, exc_info=True)


def remove_job_leftovers(work_dir, stem: str) -> int:
    """Remove what a conversion named `stem` leaves in `work_dir` when it is
    killed: the folder `<stem>-contents` an archive was unpacked into and the
    padded copy `<stem>-source.<ext>` bsdtar reads. A job that is killed -- the
    time limit, Stop, the out-of-memory killer -- is SIGKILLed, so no `finally`
    in the worker ran, and a 2.7 MB .tgz left 629 MB behind (review, 2026-10-07).

    Exactly those two names, directly inside `work_dir`, and nothing wider: the
    stem is a user id, a job id and a suffix, and a name that could stand for
    anything else is refused. A link by either name is removed as a link and
    never followed; a folder that does not resolve to a child of `work_dir` is
    left alone. Returns how many entries went."""
    if not stem or stem in (".", "..") or any(sep in stem for sep in ("/", "\\", os.sep)):
        return 0
    try:
        entries = list(os.scandir(work_dir))
        base = os.path.normcase(os.path.realpath(work_dir))
    except OSError:
        return 0
    import convert_utils
    removed = 0
    for entry in entries:
        try:
            contents = entry.name == f"{stem}-contents"
            if not (contents or entry.name.startswith(f"{stem}-source.")):
                continue
            if entry.is_symlink() or not entry.is_dir(follow_symlinks=False):
                os.unlink(entry.path)
            elif contents:
                if os.path.normcase(os.path.dirname(os.path.realpath(entry.path))) != base:
                    logger.warning("Not removing %s: it does not resolve inside the work directory", entry.name)
                    continue
                convert_utils.remove_tree(entry.path)
                if os.path.lexists(entry.path):
                    continue
            else:
                continue                          # a folder named like a source copy is not ours
            removed += 1
        except OSError:
            logger.debug("Could not remove %s", entry.path, exc_info=True)
    return removed


async def _finish(job: Job, runner: Runner, kind: str | None, detail: str,
                  outcome: Outcome | None) -> None:
    try:
        elapsed = time.monotonic() - job.started_at if job.started_at else 0.0
        held = await _safe(runner.release(job)) if job.charged else None
        if kind is None:
            await _safe(runner.succeeded(job, outcome))
            return
        refunded_to = None
        if job.charged and kind in REFUNDED and held == "recovered":
            # Another process took this one for dead and has already given the
            # credit back. Giving it back again would be paying twice.
            logger.warning("Conversion %s ended %s after it had been refunded as lost", job.id, kind)
            job.charged = False
        if job.charged and kind in REFUNDED:
            try:
                refunded_to = await runner.refund(job, kind)
                job.charged = False
            except Exception:
                logger.exception("Refunding conversion %s failed", job.id)
        await _safe(runner.failed(job, kind, detail, refunded_to))
        if kind in ALERTED:
            await _safe(runner.alert(job, kind, detail, elapsed, refunded_to))
    finally:
        _jobs.pop(job.id, None)
        job.state = "done"
        if outcome is not None and outcome.path:
            _unlink(outcome.path)
        if not job.keep_file:
            still_needed = active_paths()
            for path in job.paths:
                if str(path) not in still_needed:
                    _unlink(path)
        lock = _user_locks.get(job.user_id)
        if lock is not None and not lock.locked() and not getattr(lock, "_waiters", None):
            _user_locks.pop(job.user_id, None)
        if job.started_at is not None:
            # Whatever the job ended with, including the worker having been
            # killed: see remove_job_leftovers. Last, and off the event loop
            # (a folder of ten thousand files), so nothing above waits for it.
            try:
                await asyncio.to_thread(remove_job_leftovers, runner.work_dir(), job_stem(job))
            except Exception:
                logger.debug("Could not clean up after conversion %s", job.id, exc_info=True)


async def _watch_progress(job: Job, runner: Runner, limit: int) -> None:
    marks = [mark for mark in PROGRESS_MARKS_S if mark < limit]
    started = time.monotonic()
    for index, mark in enumerate(marks):
        await asyncio.sleep(max(0.0, mark - (time.monotonic() - started)))
        following = marks[index + 1] if index + 1 < len(marks) else None
        await _safe(runner.progress(job, index + 1, len(marks), mark, following, limit))


async def _watch_queue(job: Job, runner: Runner) -> None:
    shown = ahead_of(job)
    while True:
        await asyncio.sleep(CARD_EVERY_S)
        now = ahead_of(job)
        if now != shown:
            shown = now
            await _safe(runner.queued(job, now))


async def _watch_card(job: Job, runner: Runner) -> None:
    while True:
        await asyncio.sleep(CARD_EVERY_S)
        await _safe(runner.card(job))


async def _run(job: Job, runner: Runner) -> None:
    lock = _user_locks.setdefault(job.user_id, asyncio.Lock())
    kind: str | None = None
    detail = ""
    outcome: Outcome | None = None
    queue_watcher = asyncio.ensure_future(_watch_queue(job, runner))
    card_watcher = None
    try:
        async with lock:
            async with _global_slots():
                queue_watcher.cancel()
                if job.user_cancelled:
                    raise asyncio.CancelledError
                job.state = "running"
                job.started_at = time.monotonic()
                if await _safe(runner.refuse_start(job)):
                    kind, detail = PAUSED, "paused for an update before it could start"
                else:
                    balance = await runner.charge(job)
                    if balance is None:
                        kind = NO_CREDIT
                    else:
                        job.charged = job.price > 0
                        if job.charged:
                            await _safe(runner.hold(job))
                        job.extra.update(stage="converting", stage_started=time.monotonic(),
                                         progress=None, balance_after=balance)
                        await _safe(runner.started(job, balance))
                        limit = job_limit(job)
                        watcher = asyncio.ensure_future(_watch_progress(job, runner, limit))
                        card_watcher = asyncio.ensure_future(_watch_card(job, runner))
                        try:
                            outcome = await run_worker(job, runner.work_dir(), job_stem(job), limit)
                        finally:
                            watcher.cancel()
                        if not outcome.ok:
                            kind, detail = outcome.kind or CRASH, outcome.message
                        elif outcome.bytes > job.send_limit:
                            kind = TOO_LARGE
                            detail = f"{outcome.bytes / 1024 / 1024:.0f} MB"
                        else:
                            job.extra.update(stage="sending", stage_started=time.monotonic(), progress=None)
                            await _safe(runner.card(job))
                            try:
                                await runner.deliver(job, outcome)
                            except asyncio.CancelledError:
                                raise
                            except Exception as exc:
                                kind, detail = SEND_FAILED, f"{type(exc).__name__}: {exc}"
    except asyncio.CancelledError:
        queue_watcher.cancel()
        if card_watcher is not None:
            card_watcher.cancel()
        kind = USER_CANCELLED if job.user_cancelled else INTERRUPTED
        detail = ("stopped by the person who asked for it" if job.user_cancelled
                  else "the bot was stopped in the middle of it")
        await asyncio.shield(_finish(job, runner, kind, detail, outcome))
        if not job.user_cancelled:
            raise
        return
    except Exception as exc:
        logger.exception("Conversion job %s failed unexpectedly", job.id)
        kind, detail = CRASH, f"{type(exc).__name__}: {exc}"
    queue_watcher.cancel()
    if card_watcher is not None:
        card_watcher.cancel()
    job.extra["stage"] = "done"
    await _finish(job, runner, kind, detail, outcome)


def submit(application, job: Job, runner: Runner) -> Job:
    """Queue a job and start its task. Returns the job, whose `ahead_of` the
    caller can use to say where it stands."""
    _jobs[job.id] = job
    coroutine = _run(job, runner)
    if application is not None and hasattr(application, "create_task"):
        job.task = application.create_task(coroutine)
    else:
        job.task = asyncio.get_running_loop().create_task(coroutine)
    return job


def cancel(job_id: str, user_id: int | None = None) -> Job | None:
    """Stop a job at the request of the person it belongs to. Returns the job,
    or None if there is no such job or it is somebody else's."""
    job = _jobs.get(job_id)
    if job is None or (user_id is not None and job.user_id != user_id):
        return None
    job.user_cancelled = True
    if job.task is not None and not job.task.done():
        job.task.cancel()
    return job
