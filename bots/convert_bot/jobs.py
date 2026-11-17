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

CONVERT_JOB_MEMORY_MB = max(1, int(os.environ.get("CONVERT_JOB_MEMORY_MB") or 1536))


def _ffmpeg_threads() -> int:
    try:
        return max(1, int(os.environ.get("FFMPEG_THREADS") or 2))
    except ValueError:
        return 2


def concurrent_for(job_memory_mb: "int | None", cpus: float,
                   per_job_mb: int = CONVERT_JOB_MEMORY_MB, threads: "int | None" = None) -> int:
    if job_memory_mb is None:
        return 1
    threads = threads or _ffmpeg_threads()
    return max(1, min(8, job_memory_mb // per_job_mb, int(cpus // threads)))


def _max_concurrent() -> int:
    fixed = os.environ.get("CONVERT_MAX_CONCURRENT")
    if fixed:
        return max(1, int(fixed))
    return concurrent_for(resources.job_memory_mb("convert_bot"), resources.cpu_limit())


MAX_CONCURRENT = _max_concurrent()


def log_capacity() -> None:
    job_memory = resources.job_memory_mb("convert_bot")
    logger.info("ConvertBot runs up to %d conversions at once (%s MB for its work, %d MB each, %g vCPU).",
                MAX_CONCURRENT, "unknown" if job_memory is None else job_memory,
                CONVERT_JOB_MEMORY_MB, resources.cpu_limit())


TIME_LIMIT_S = int(os.environ.get("CONVERT_TIME_LIMIT_SECONDS") or 600)

FREE_TIME_LIMIT_S = int(os.environ.get("CONVERT_FREE_TIME_LIMIT_SECONDS") or 60)

TIME_BASE_S = int(os.environ.get("CONVERT_TIME_BASE_SECONDS") or 120)
TIME_PER_MB_S = float(os.environ.get("CONVERT_TIME_PER_MB_SECONDS") or 1.0)


def time_limit_for(billable_bytes: int, work: float = 1.0, paid: bool = True) -> int:
    if not paid:
        return FREE_TIME_LIMIT_S
    wanted = TIME_BASE_S + TIME_PER_MB_S * work * billable_bytes / (1024 * 1024)
    return int(min(TIME_LIMIT_S, max(TIME_BASE_S, wanted)))


def ocr_time_limit(ocr_pages: int, paid: bool, billable: int = 0, work: float = 1.0) -> int:
    if not paid:
        return FREE_TIME_LIMIT_S
    import ocr_convert
    return int(min(TIME_LIMIT_S, time_limit_for(billable, work, True) + max(0, ocr_pages) * ocr_convert.OCR_PAGE_S))


def job_limit(job: "Job") -> int:
    return job.time_limit or (TIME_LIMIT_S if job.price > 0 else FREE_TIME_LIMIT_S)


PROJECT_AFTER_S = 30
PROJECT_MARGIN = 1.2

PROGRESS_MARKS_S = (120, 300)

MAX_QUEUED_PER_USER = max(1, int(os.environ.get("CONVERT_MAX_QUEUED_PER_USER") or 5))


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

MAX_MEGAPIXELS = int(os.environ.get("CONVERT_MAX_MEGAPIXELS") or 210)

CARD_EVERY_S = 3.0

HOLD_TOUCH_S = 30
HOLD_STALE_S = 120

WORKER_MAX_MEMORY_MB = int(os.environ.get("CONVERT_WORKER_MAX_MEMORY_MB") or 0)

SEND_LIMIT_MB = big_files.BOT_API_SEND_BYTES // (1024 * 1024)
SEND_LIMIT_BYTES = SEND_LIMIT_MB * 1024 * 1024

BIG_SEND_MAX_BYTES = big_files.MTPROTO_MAX_BYTES
SEND_HEADROOM = 3


def big_sends(chat_id: int) -> bool:
    return LOCAL_BOT_API or big_files.usable(chat_id)


def big_send_limit(out_estimate: int) -> int:
    return min(BIG_SEND_MAX_BYTES, max(SEND_LIMIT_BYTES, SEND_HEADROOM * max(0, out_estimate or 0)))


def send_limit_for(in_bytes: int, out_estimate: int | None, chat_id: int) -> int:
    if not big_sends(chat_id):
        return SEND_LIMIT_BYTES
    return big_send_limit(out_estimate if out_estimate else in_bytes)


def send_ceiling_mb(chat_id: int) -> int:
    return (BIG_SEND_MAX_BYTES if big_sends(chat_id) else SEND_LIMIT_BYTES) // (1024 * 1024)


LOSSLESS_BYTES_PER_PIXEL = {
    "bmp": 3.0,
    "ppm": 3.0,
    "tga": 3.0,
    "tiff": 0.6,
    "png": 0.5,
    "qoi": 0.5,
}

RAW_LOSSLESS_BYTES_PER_PIXEL = {"tiff": 6.0}

TARGET_MAX_MEGAPIXELS = {
    "heic": 142.6,
}

USER_CANCELLED = "user_cancelled"
NO_CREDIT = "no_credit"
REFUSED = "refused"
TIMEOUT = "timeout"
TOO_SLOW = "too_slow"
CRASH = "crash"
NO_MEMORY = "no_memory"
TOO_LARGE = "too_large"
SEND_FAILED = "send_failed"
INTERRUPTED = "interrupted"
PAUSED = "paused"

REFUNDED = frozenset({REFUSED, TIMEOUT, TOO_SLOW, CRASH, NO_MEMORY, TOO_LARGE, SEND_FAILED, INTERRUPTED})

ALERTED = frozenset({TIMEOUT, CRASH, TOO_LARGE, SEND_FAILED, INTERRUPTED})


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
    send_limit: int = SEND_LIMIT_BYTES
    time_limit: int = 0
    ocr_lang: str = ""
    ocr_pages: int = 0
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])
    state: str = "queued"
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
_reserved: dict[object, tuple[int, int]] = {}
_storage_lock: asyncio.Lock | None = None


def reset() -> None:
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
    global _storage_lock
    if _storage_lock is None:
        _storage_lock = asyncio.Lock()
    return _storage_lock


def reserve_storage(user_id: int, nbytes: int) -> object:
    token = object()
    _reserved[token] = (user_id, max(0, int(nbytes or 0)), resources.reserve_disk("convert_bot", nbytes))
    return token


def release_storage(token: object) -> None:
    if token is not None:
        held = _reserved.pop(token, None)
        if held is not None:
            resources.release_disk(held[2])


def ahead_of(job: Job) -> int:
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
    return {str(path) for job in _jobs.values() for path in job.paths}


def active_stems() -> set[str]:
    return {job_stem(job) for job in _jobs.values()}


def uncharged_total(user_id: int) -> int:
    return sum(job.price for job in _jobs.values()
               if job.user_id == user_id and not job.charged)


def storage_problem(user_id: int, incoming_bytes: int, upload_dir) -> tuple[str, int, int] | None:
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
    return await asyncio.create_subprocess_exec(
        sys.executable, "-u", str(WORKER), *WORKER_ARGS, json.dumps(payload),
        cwd=str(HERE), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, **_OWN_GROUP,
    )


def job_stem(job: Job) -> str:
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
        async def drain():
            while chunk := await proc.stderr.read(65536):
                errors.extend(chunk)
                del errors[:-8000]
        draining = asyncio.ensure_future(drain())
        while True:
            try:
                line = await proc.stdout.readline()
            except ValueError:
                continue
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


DESCRIBE_TIMEOUT_S = 30


def _raw_info(answer: bytes):
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
    payload = {"kind": "describe_raw", "path": str(path), "src_ext": src_ext,
               "max_memory_mb": WORKER_MAX_MEMORY_MB}
    answered = await _ask_child(payload, timeout, "a raw photo's size")
    if answered is None:
        return None
    out, returncode = answered
    info, why = _raw_info(out)
    if info is None:
        if why != "refused":
            logger.warning("Reading a raw photo's size failed (%s, exit %s)", why, returncode)
        return None
    return info


async def _ask_child(payload: dict, timeout: float, what: str):
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
    answered = await _ask_child({"kind": "describe_image", "path": str(path),
                                 "max_memory_mb": WORKER_MAX_MEMORY_MB}, timeout, "a picture's size")
    return None if answered is None else _image_size(answered[0])


def _count(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _pdf_info(answer: bytes) -> "dict | None":
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
    if timeout is None:
        import ocr_convert
        timeout = ocr_convert.OCR_DESCRIBE_SECONDS
    answered = await _ask_child_partial({"kind": "describe_pdf", "path": str(path),
                                         "max_memory_mb": WORKER_MAX_MEMORY_MB}, timeout, "a PDF's pages")
    if answered is None:
        return None
    out, returncode = answered
    info = _pdf_info(out)
    if info is None and returncode not in (0, 2, None):
        logger.warning("Reading a PDF's pages failed (exit %s)", returncode)
    return info


class Runner:
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
        return None

    async def deliver(self, job: Job, outcome: Outcome) -> None:
        raise NotImplementedError

    async def succeeded(self, job: Job, outcome: Outcome) -> None:
        return None

    async def refund(self, job: Job, kind: str) -> int:
        raise NotImplementedError

    async def queued(self, job: Job, ahead: int) -> None:
        return None

    async def card(self, job: Job) -> None:
        return None

    async def hold(self, job: Job) -> None:
        return None

    async def release(self, job: Job) -> str:
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
                continue
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
    _jobs[job.id] = job
    coroutine = _run(job, runner)
    if application is not None and hasattr(application, "create_task"):
        job.task = application.create_task(coroutine)
    else:
        job.task = asyncio.get_running_loop().create_task(coroutine)
    return job


def cancel(job_id: str, user_id: int | None = None) -> Job | None:
    job = _jobs.get(job_id)
    if job is None or (user_id is not None and job.user_id != user_id):
        return None
    job.user_cancelled = True
    if job.task is not None and not job.task.done():
        job.task.cancel()
    return job
