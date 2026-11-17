import asyncio
import gc
import logging
import os
import socket
import sys
import time
import traceback
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler

from telegram.error import BadRequest, Conflict, NetworkError, RetryAfter

import db
import family_link
import live_message
import resources

_LOG_FORMAT ="%(asctime)s %(name)s %(levelname)s %(message)s"

_recent_errors: deque[tuple[str, str]] = deque(maxlen=10)
_error_count = 0

_event_hook = None


def set_event_hook(fn) -> None:
    global _event_hook
    _event_hook = fn


def emit_event(level: str, kind: str, message: str, details: str | None = None) -> None:
    if _event_hook is None:
        return
    try:
        _event_hook(level, kind, message, details)
    except Exception:
        logging.getLogger(__name__).debug("Family event hook failed", exc_info=True)


def _log_to_files() -> bool:
    override = os.environ.get("LOG_TO_FILES")
    if override is not None:
        return override.strip().lower() in ("1", "true", "yes", "on")
    return not (os.environ.get("RAILWAY_ENVIRONMENT_NAME") or os.environ.get("RAILWAY_ENVIRONMENT"))


def setup_logging(bot_file: str) -> None:
    fmt = logging.Formatter(_LOG_FORMAT)

    root = logging.getLogger()
    root.setLevel(logging.INFO)

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    root.addHandler(console)

    for noisy in ("httpx", "httpcore", "telegram.ext.Updater", "apscheduler"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    if not _log_to_files():
        return

    log_dir = os.path.join(os.path.dirname(os.path.abspath(bot_file)), "logs")
    os.makedirs(log_dir, exist_ok=True)

    info_file = RotatingFileHandler(
        os.path.join(log_dir, "bot.log"), maxBytes=2_000_000, backupCount=3, encoding="utf-8"
    )
    info_file.setFormatter(fmt)
    root.addHandler(info_file)

    error_file = RotatingFileHandler(
        os.path.join(log_dir, "errors.log"), maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    error_file.setLevel(logging.WARNING)
    error_file.setFormatter(fmt)
    root.addHandler(error_file)


TRANSIENT_NETWORK_ERRORS = (NetworkError, RetryAfter)

NETWORK_ALERT_AFTER = int(os.environ.get("NETWORK_ALERT_AFTER", "20"))

_network_blips = 0
_network_blips_total = 0
_network_alerted = False


PERMANENT_NETWORK_MESSAGES = ("entity too large", "file is too big", "too big", "too large")


def is_transient_network_error(exc: BaseException) -> bool:
    if not isinstance(exc, TRANSIENT_NETWORK_ERRORS) or isinstance(exc, BadRequest):
        return False
    text = str(exc).lower()
    return not any(marker in text for marker in PERMANENT_NETWORK_MESSAGES)


def note_network_blip(exc: BaseException) -> None:
    global _network_blips, _network_blips_total, _network_alerted
    _network_blips += 1
    _network_blips_total += 1
    logging.getLogger(__name__).warning(
        "Transient network error (%s): %s -- retried by PTB, %s in a row",
        type(exc).__name__, exc, _network_blips,
    )
    if _network_blips >= NETWORK_ALERT_AFTER and not _network_alerted:
        _network_alerted = True
        emit_event(
            "warning", "network",
            f"{_network_blips} network errors in a row -- this bot may not be "
            f"reaching Telegram. Latest: {type(exc).__name__}: {exc}",
        )


def note_network_ok() -> None:
    global _network_blips, _network_alerted
    if _network_blips and _network_alerted:
        emit_event("info", "network", "Telegram is reachable again.")
    _network_blips = 0
    _network_alerted = False


CONFLICT_ALERT_COUNT = 3
CONFLICT_ALERT_WINDOW_S = 120
CONFLICT_QUIET_RESET_S = 600

_conflict_times: "deque[float]" = deque(maxlen=CONFLICT_ALERT_COUNT)
_conflict_last: "float | None" = None
_conflict_alerted = False


def note_conflict(exc: BaseException) -> None:
    global _conflict_last, _conflict_alerted
    now = time.monotonic()
    if _conflict_last is not None and now - _conflict_last >= CONFLICT_QUIET_RESET_S:
        _conflict_times.clear()
        _conflict_alerted = False
    _conflict_last = now
    _conflict_times.append(now)
    in_window = sum(1 for seen in _conflict_times if now - seen <= CONFLICT_ALERT_WINDOW_S)
    logging.getLogger(__name__).warning(
        "Conflict on getUpdates: %s within the last %s s (a report needs %s)",
        in_window, CONFLICT_ALERT_WINDOW_S, CONFLICT_ALERT_COUNT,
    )
    if in_window >= CONFLICT_ALERT_COUNT and not _conflict_alerted:
        _conflict_alerted = True
        message = (
            "Another copy of this bot seems to be polling with the same token: "
            f"{in_window} conflicts within {CONFLICT_ALERT_WINDOW_S} seconds"
        )
        logging.getLogger(__name__).error(message)
        report = Conflict(message)
        report.__cause__ = exc
        record_error(report)


def record_error(exc: BaseException) -> None:
    global _error_count
    _error_count += 1
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    _recent_errors.append((stamp, repr(exc)))
    emit_event(
        "error", "crash", f"ManagerBot itself hit an unhandled {type(exc).__name__}: {exc}",
        "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
    )


def error_summary() -> str:
    blips = (
        f"\n\U0001f310 {_network_blips_total} transient network error(s) -- retried, not crashes."
        if _network_blips_total else ""
    )
    if _error_count == 0:
        return "✅ No errors since this instance started." + blips
    lines = [f"⚠️ {_error_count} error(s) since start:"]
    lines.extend(f"  {stamp} — {msg}" for stamp, msg in _recent_errors)
    if _error_count > len(_recent_errors):
        lines.append(f"  (+{_error_count - len(_recent_errors)} earlier, see logs/errors.log)")
    return "\n".join(lines) + blips


async def error_handler(update, context) -> None:
    if update is None and is_transient_network_error(context.error):
        note_network_blip(context.error)
        return
    if update is None and isinstance(context.error, Conflict):
        note_conflict(context.error)
        return
    logging.getLogger(__name__).error("Unhandled exception while processing an update", exc_info=context.error)
    record_error(context.error)


ACTIVITY_FLUSH_SECONDS = int(os.environ.get("ACTIVITY_FLUSH_SECONDS", "60"))

_activity_buffer: set[int] = set()


def _flush_activity_now() -> int:
    global _activity_buffer
    if not _activity_buffer:
        return 0
    batch, _activity_buffer = _activity_buffer, set()
    try:
        db.record_activity_batch(batch)
    except Exception:
        _activity_buffer |= batch
        raise
    return len(batch)


async def _flush_activity_job(context) -> None:
    try:
        await asyncio.to_thread(_flush_activity_now)
    except Exception:
        logging.getLogger(__name__).debug("Activity flush failed; will retry", exc_info=True)


async def track_activity(update, context) -> None:
    note_network_ok()
    live_message.note_update(update)
    user = update.effective_user
    if not user:
        return
    _activity_buffer.add(user.id)
    note_usage_update(user.id)


WORKER_THREADS = int(os.environ.get("WORKER_THREADS", "4"))
GC_THRESHOLD = int(os.environ.get("GC_GEN0_THRESHOLD", "5000"))


async def tune_runtime(application) -> None:
    asyncio.get_running_loop().set_default_executor(
        ThreadPoolExecutor(max_workers=WORKER_THREADS, thread_name_prefix="worker")
    )
    gc.collect()
    gc.freeze()
    gc.set_threshold(GC_THRESHOLD, 20, 20)


def attach_maintenance(app) -> None:
    app.job_queue.run_repeating(
        _flush_activity_job, interval=ACTIVITY_FLUSH_SECONDS, first=ACTIVITY_FLUSH_SECONDS
    )
    app.job_queue.run_repeating(
        _usage_sample_job, interval=USAGE_SAMPLE_MINUTES * 60,
        first=USAGE_SAMPLE_MINUTES * 60 + 60,
    )


async def flush_on_shutdown(application) -> None:
    try:
        await asyncio.to_thread(_flush_activity_now)
    except Exception:
        logging.getLogger(__name__).debug("Final activity flush failed", exc_info=True)
    try:
        await asyncio.to_thread(db.close_pool)
    except Exception:
        logging.getLogger(__name__).debug("Closing the connection pool failed", exc_info=True)


def detect_host_environment() -> str:
    railway_env = os.environ.get("RAILWAY_ENVIRONMENT_NAME") or os.environ.get("RAILWAY_ENVIRONMENT")
    if railway_env:
        project = os.environ.get("RAILWAY_PROJECT_NAME", "?")
        service = os.environ.get("RAILWAY_SERVICE_NAME", "?")
        return f"☁️ Cloud (Railway -- project \"{project}\", service \"{service}\", env \"{railway_env}\")"
    return f"💻 Local ({socket.gethostname()})"


def _read_first_int(path: str, key: str | None = None) -> int | None:
    try:
        with open(path) as handle:
            if key is None:
                text = handle.read().strip()
                return int(text) if text.isdigit() else None
            for line in handle:
                if line.startswith(key):
                    return int(line.split()[1])
    except (OSError, ValueError, IndexError):
        return None
    return None


_service_rss_seen = 0


def footprint_numbers() -> dict:
    global _service_rss_seen
    resident = _read_first_int("/proc/self/status", "VmRSS:")
    peak = _read_first_int("/proc/self/status", "VmHWM:")
    ceiling = resources.memory_limit_mb()
    cpu = None
    try:
        import resource

        usage = resource.getrusage(resource.RUSAGE_SELF)
        cpu = usage.ru_utime + usage.ru_stime
        if resident is None:
            scale = 1 if sys.platform == "darwin" else 1024
            peak = usage.ru_maxrss * scale // 1024
    except Exception:
        pass
    numbers = {
        "rss_mb": None if resident is None else resident // 1024,
        "peak_rss_mb": None if peak is None else peak // 1024,
        "ceiling_mb": ceiling,
        "cpu_seconds": None if cpu is None else int(cpu),
        "scope": "process",
    }
    used = resources.memory_used_mb()
    if used is not None:
        _service_rss_seen = max(_service_rss_seen, used)
        service_peak = resources.memory_peak_mb()
        numbers["rss_mb"] = used
        numbers["peak_rss_mb"] = _service_rss_seen if service_peak is None else service_peak
        numbers["scope"] = "service"
        service_cpu = resources.cpu_used_seconds()
        if service_cpu is not None:
            numbers["cpu_seconds"] = int(service_cpu)
    return numbers


def process_footprint() -> str:
    numbers = footprint_numbers()
    parts = []
    if numbers["rss_mb"] is not None:
        if numbers.get("scope") == "service":
            line = f"Memory (this service, its workers included): {numbers['rss_mb']} MB in use"
        else:
            line = f"Memory: {numbers['rss_mb']} MB resident"
        if numbers["peak_rss_mb"]:
            line += f" (peak {numbers['peak_rss_mb']} MB)"
        if numbers["ceiling_mb"]:
            line += f" of {numbers['ceiling_mb']} MB allowed"
        cache = resources.memory_cache_mb() if numbers.get("scope") == "service" else None
        if cache:
            line += f"; disk cache {cache} MB more, which the kernel takes back when it needs to"
        parts.append(line)
    elif numbers["peak_rss_mb"] is not None:
        parts.append(f"Memory: peak {numbers['peak_rss_mb']} MB")
    if numbers["cpu_seconds"] is not None:
        parts.append(f"CPU: {numbers['cpu_seconds']}s used since start")
    trend = usage_trend_line()
    if trend:
        parts.append(trend)
    return (" · ".join(parts) or "Footprint: not readable on this host") + "\n" + resources.describe(
        resources.own_folder(family_link._bot_id))


SLEEP_AFTER_SECONDS = int(os.environ.get("SLEEP_AFTER_SECONDS") or 300)

USAGE_SAMPLE_MINUTES = int(os.environ.get("USAGE_SAMPLE_MINUTES") or 15)
USAGE_MEMORY_WARN_RATIO = float(os.environ.get("USAGE_MEMORY_WARN_RATIO") or 0.85)
USAGE_CPU_WARN_RATIO = float(os.environ.get("USAGE_CPU_WARN_RATIO") or 0.85)
USAGE_SPIKE_FACTOR = float(os.environ.get("USAGE_SPIKE_FACTOR") or 6.0)
USAGE_SPIKE_FLOOR = int(os.environ.get("USAGE_SPIKE_FLOOR") or 60)
USAGE_MONTHLY_USERS_WARN = int(os.environ.get("USAGE_MONTHLY_USERS_WARN") or 400)
SLEEP_AFTER_SECONDS = int(os.environ.get("SLEEP_AFTER_SECONDS") or 300)
USAGE_FAILURE_WARN_RATIO = float(os.environ.get("USAGE_FAILURE_WARN_RATIO") or 0.5)
USAGE_FAILURE_FLOOR = int(os.environ.get("USAGE_FAILURE_FLOOR") or 4)
_USAGE_WINDOW_MEMORY = 24
_ALARM_QUIET_SECONDS = {"memory": 3600, "spike": 3600, "monthly_users": 86400,
                        "failures": 1800, "cpu": 3600}

_usage_updates = 0
_usage_users: set[int] = set()
_usage_recent: "deque[int]" = deque(maxlen=_USAGE_WINDOW_MEMORY)
_usage_alarmed: dict[str, float] = {}
_usage_last_update = time.monotonic()
_usage_window_start = _usage_last_update
_usage_sleepable = 0.0
_usage_max_gap = 0.0
_usage_jobs_ok = 0
_usage_jobs_failed = 0


def _asleep_in_window(now: float) -> float:
    return max(0.0, now - max(_usage_last_update + SLEEP_AFTER_SECONDS, _usage_window_start))


def note_usage_update(user_id: int | None) -> None:
    global _usage_updates, _usage_last_update, _usage_sleepable, _usage_max_gap
    _usage_updates += 1
    now = time.monotonic()
    gap = now - _usage_last_update
    _usage_sleepable += _asleep_in_window(now)
    _usage_last_update = now
    _usage_max_gap = max(_usage_max_gap, gap)
    if user_id is not None and len(_usage_users) < 10000:
        _usage_users.add(user_id)


def note_job(ok: bool) -> None:
    global _usage_jobs_ok, _usage_jobs_failed
    if ok:
        _usage_jobs_ok += 1
    else:
        _usage_jobs_failed += 1


def _alarm_due(kind: str) -> bool:
    now = time.time()
    if now - _usage_alarmed.get(kind, 0) < _ALARM_QUIET_SECONDS.get(kind, 3600):
        return False
    _usage_alarmed[kind] = now
    return True


_usage_cpu_last: "tuple[float, float] | None" = None


def _cpu_window(numbers: dict) -> "tuple[float, float] | None":
    global _usage_cpu_last
    cpu = numbers.get("cpu_seconds")
    now = time.monotonic()
    last, _usage_cpu_last = _usage_cpu_last, (None if cpu is None else (cpu, now))
    if cpu is None or last is None or cpu < last[0] or now <= last[1]:
        return None
    return cpu - last[0], now - last[1]


def _median(values) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[middle])
    return (ordered[middle - 1] + ordered[middle]) / 2


def usage_trend_line() -> str:
    if not _usage_recent:
        return ""
    quiet = int(time.monotonic() - _usage_last_update)
    return (f"Recent: {sum(_usage_recent)} update(s) over the last "
            f"{len(_usage_recent)} window(s) of {USAGE_SAMPLE_MINUTES} min · "
            f"quiet for {quiet // 60}m {quiet % 60}s")


def _monthly_users() -> int | None:
    counter = getattr(db, "count_active_users_since", None)
    if counter is None:
        return None
    try:
        return counter(datetime.now(timezone.utc) - timedelta(days=30))
    except Exception:
        return None


def _check_usage_alarms(numbers: dict, updates: int, users: int,
                        jobs_ok: int = 0, jobs_failed: int = 0) -> None:
    jobs = jobs_ok + jobs_failed
    if (jobs >= USAGE_FAILURE_FLOOR
            and jobs_failed / jobs >= USAGE_FAILURE_WARN_RATIO
            and _alarm_due("failures")):
        family_link.report_event(
            "warning" if jobs_ok else "error", "job_failures",
            f"{jobs_failed} of {jobs} job(s) failed in the last "
            f"{USAGE_SAMPLE_MINUTES} min"
            + ("." if jobs_ok else " -- none succeeded."),
            "A job is whatever this bot is for: a conversion, a download, a pack "
            "edit. All of them failing usually means something outside the bot "
            "stopped answering rather than something inside it breaking.",
        )
    ceiling = numbers.get("ceiling_mb")
    peak = numbers.get("peak_rss_mb")
    if ceiling and peak and peak >= ceiling * USAGE_MEMORY_WARN_RATIO and _alarm_due("memory"):
        family_link.report_event(
            "warning", "memory_headroom",
            f"Memory peaked at {peak} MB of {ceiling} MB allowed "
            f"({peak / ceiling:.0%} of the limit).",
            "Crossing the limit is an out-of-memory kill rather than a slow reply. "
            "Either something is holding more than it should, or this service has "
            "outgrown its plan.",
        )
    window = numbers.get("cpu_window")
    if (window and numbers.get("rss_mb") is not None
            and (numbers.get("scope") == "service" or resources.holds_whole_service())):
        used, seconds = window
        limit = resources.cpu_limit()
        if seconds > 0 and used / seconds >= USAGE_CPU_WARN_RATIO * limit and _alarm_due("cpu"):
            family_link.report_event(
                "warning", "cpu_busy",
                f"CPU: this service used {used / seconds:.1f} of its {limit:g} vCPU over the "
                f"last {round(seconds / 60)} minutes.",
                "Busy for this long means conversions or downloads are queueing for the "
                "processor, and everybody's work slows down together. Either something is "
                "spinning, or this service has outgrown its plan.",
            )

    baseline = _median(_usage_recent)
    if (updates >= USAGE_SPIKE_FLOOR and baseline > 0
            and updates >= baseline * USAGE_SPIKE_FACTOR and _alarm_due("spike")):
        family_link.report_event(
            "warning", "activity_spike",
            f"{updates} updates from {users} person(s) in {USAGE_SAMPLE_MINUTES} min, "
            f"against a recent median of {baseline:.0f}.",
            "Could be a launch and could be one script. /status and the usage table "
            "have the shape of it.",
        )

    monthly = _monthly_users()
    if monthly is not None and monthly >= USAGE_MONTHLY_USERS_WARN and _alarm_due("monthly_users"):
        family_link.report_event(
            "warning", "monthly_users",
            f"{monthly} distinct people used this bot in the last 30 days, "
            f"past the {USAGE_MONTHLY_USERS_WARN} mark.",
            "Nothing is refused and nothing is broken. It is the number that decides "
            "whether the plan this runs on is still the right one.",
        )


def _sample_usage_now() -> None:
    global _usage_updates, _usage_users
    updates, users = _usage_updates, len(_usage_users)
    _usage_updates, _usage_users = 0, set()
    numbers = footprint_numbers()
    global _usage_sleepable, _usage_max_gap, _usage_jobs_ok, _usage_jobs_failed
    global _usage_window_start
    now = time.monotonic()
    trailing = now - _usage_last_update
    sleepable = int(_usage_sleepable + _asleep_in_window(now))
    max_gap = int(max(_usage_max_gap, trailing))
    jobs_ok, jobs_failed = _usage_jobs_ok, _usage_jobs_failed
    _usage_sleepable, _usage_max_gap = 0.0, 0.0
    _usage_jobs_ok, _usage_jobs_failed = 0, 0
    _usage_window_start = now
    numbers["cpu_window"] = _cpu_window(numbers)
    try:
        family_link.record_usage(
            USAGE_SAMPLE_MINUTES, numbers["rss_mb"], numbers["peak_rss_mb"],
            numbers["ceiling_mb"], numbers["cpu_seconds"], updates, users,
            sleepable, max_gap, jobs_ok, jobs_failed,
        )
    except Exception:
        logging.getLogger(__name__).debug("Usage sample not written", exc_info=True)
    try:
        _check_usage_alarms(numbers, updates, users, jobs_ok, jobs_failed)
    except Exception:
        logging.getLogger(__name__).debug("Usage alarm check failed", exc_info=True)
    _usage_recent.append(updates)
    resources.reset_memory_peak()


async def _usage_sample_job(context) -> None:
    await asyncio.to_thread(_sample_usage_now)


def data_protection_lines() -> "list[str]":
    import offline_key
    lines = []
    came = os.environ.get("DATA_KEY_CAME_FROM")
    if came:
        lines.append(f"⚠️ Data key: came through the Railway variable {came}. Put it on the volume and "
                     f"rotate it (private/runbooks/data-protection.md).")
    try:
        public = offline_key.recipient()
        lines.append(f"Backups: encrypted to the offline key {offline_key.fingerprint(public)}" if public
                     else "⚠️ Backups: not encrypted. Set BACKUP_PUBLIC_KEY (private/runbooks/data-protection.md).")
    except offline_key.OfflineKeyError as exc:
        lines.append(f"⚠️ Backups: {exc}")
    return lines


def build_status_text(start_time: datetime, users_last_hour: int, users_since_start: int) -> str:
    now = datetime.now(timezone.utc)
    uptime = now - start_time
    days, rem = divmod(int(uptime.total_seconds()), 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)
    uptime_str = f"{days}d {hours}h {minutes}m" if days else f"{hours}h {minutes}m"
    return "\n".join([
        "📊 Status",
        f"Version: {family_link.LABEL} (commands as of {family_link.VERSION})",
        f"Started: {start_time.strftime('%Y-%m-%d %H:%M:%S UTC')} ({uptime_str} ago)",
        f"Hosted: {detect_host_environment()}",
        f"Active users (last hour): {users_last_hour}",
        f"Active users (since this start): {users_since_start}",
        process_footprint(),
        *data_protection_lines(),
        "",
        error_summary(),
    ])
