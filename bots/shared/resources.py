"""What the machine this service runs on allows, and what a bot may count on.

Since 2.2.0 a service (a Railway service, a container) may be given its own memory and CPU,
and may run all five bots or only some of them. Every size a bot picks for itself -- how many
conversions at once, how many downloads, how big an archive may be -- comes from here, so that
one rule decides it and a larger machine is used without anybody editing a number.

Pure reads, never raising: any error is `None`, or the fallback named in the function. Nothing
here changes anything and nothing personal is logged. The container's limits come from the
cgroup files the kernel keeps (`CGROUP`; tests point it at a scratch folder), and the owner can
always say otherwise:

    MUMU_MEMORY_MB          the service's memory in MB (a positive whole number)
    MUMU_CPUS               the service's vCPUs (a positive number, 0.5 is half a core)
    MUMU_MEMORY_RESERVE_MB  what is kept back for the bots themselves (default a quarter, at most 1024)
    MUMU_SERVICE_BOTS       the bots this service runs, comma-separated folders; main.py sets it

A bad value is ignored, as if it were not set.
"""
import logging
import math
import os
from pathlib import Path

logger = logging.getLogger(__name__)

CGROUP = Path("/sys/fs/cgroup")

# The bots, in the order main.py lists them, and how much of the memory left over after the
# reserve each is planned for. ConvertBot's work is the heavy one (a video, a 200 MP photo),
# DownloaderBot's is next (yt-dlp, an album PDF), StickerBot's small; AnonBot and ManagerBot
# only relay messages, so their work is planned for nothing.
ALL_BOTS = ("sticker_bot", "convert_bot", "downloader_bot", "anon_bot", "manager_bot")
WEIGHTS = {"convert_bot": 4, "downloader_bot": 2, "sticker_bot": 1}

_MB = 1024 * 1024
# A cgroup that has no limit reports a number near 2^63, "the whole machine".
_NO_LIMIT = 1 << 62


def _read(*parts: str) -> "str | None":
    try:
        return CGROUP.joinpath(*parts).read_text(encoding="utf-8").strip()
    except Exception:
        return None


def _read_int(*parts: str) -> "int | None":
    """A whole number from a cgroup file; None for a missing file, "max" or nonsense."""
    text = _read(*parts)
    if text is None:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _override(name: str, whole: bool = False) -> "float | int | None":
    """The positive number in the environment variable `name`, else None."""
    try:
        text = (os.environ.get(name) or "").strip()
        if not text:
            return None
        value = int(text) if whole else float(text)
        if whole:
            return value if value > 0 else None
        return value if value > 0 and math.isfinite(value) else None
    except Exception:
        return None


def _bytes_to_mb(value: "int | None") -> "int | None":
    return None if value is None or value < 0 else value // _MB


def _limit_from_cgroup() -> "int | None":
    """The cgroup's memory limit in MB, v2 first and then v1. None: none, or unreadable."""
    for parts in (("memory.max",), ("memory", "memory.limit_in_bytes")):
        value = _read_int(*parts)
        if value is not None and 0 < value < _NO_LIMIT:
            return value // _MB or None
    return None


def memory_limit_mb() -> "int | None":
    """The memory this service may use, in MB: MUMU_MEMORY_MB, else the cgroup's limit
    (v2 `memory.max`, v1 `memory.limit_in_bytes`). None when neither says."""
    try:
        return _override("MUMU_MEMORY_MB", whole=True) or _limit_from_cgroup()
    except Exception:
        return None


def _cpu_count() -> int:
    try:
        if hasattr(os, "sched_getaffinity"):
            return max(1, len(os.sched_getaffinity(0)))
    except Exception:
        pass
    return os.cpu_count() or 1


def _cpu_quota() -> "float | None":
    """The cgroup's CPU quota as a number of vCPUs; None when there is none."""
    text = _read("cpu.max")                                  # v2: "<quota> <period>"
    if text is not None:
        fields = text.split()
        if len(fields) >= 1 and fields[0] != "max":
            try:
                quota = float(fields[0])
                period = float(fields[1]) if len(fields) > 1 else 100000.0
                if quota > 0 and period > 0:
                    return quota / period
            except ValueError:
                pass
        return None
    quota = _read_int("cpu", "cpu.cfs_quota_us")             # v1: -1 means none
    period = _read_int("cpu", "cpu.cfs_period_us")
    if quota is not None and period and quota > 0 and period > 0:
        return quota / period
    return None


def cpu_limit() -> float:
    """The vCPUs this service may use: MUMU_CPUS, else the cgroup quota or the CPUs
    this process may run on, whichever is smaller. At worst the CPU count, at least 1."""
    try:
        override = _override("MUMU_CPUS")
        if override:
            return float(override)
        count = float(_cpu_count())
        quota = _cpu_quota()
        return min(quota, count) if quota else count
    except Exception:
        return 1.0


def _memory_stat(*parts: str) -> dict:
    text = _read(*parts)
    found = {}
    for line in (text or "").splitlines():
        fields = line.split()
        if len(fields) == 2 and fields[1].isdigit():
            found[fields[0]] = int(fields[1])
    return found


def _reclaimable_cache(stat: dict, prefix: str = "") -> int:
    files = stat.get(f"{prefix}active_file", 0) + stat.get(f"{prefix}inactive_file", 0)
    return max(0, files - stat.get(f"{prefix}shmem", 0))


def _memory_bytes() -> "tuple[int, int] | None":
    total = _read_int("memory.current")
    if total is not None:
        return total, _reclaimable_cache(_memory_stat("memory.stat"))
    total = _read_int("memory", "memory.usage_in_bytes")
    if total is None:
        return None
    stat = _memory_stat("memory", "memory.stat")
    prefix = "total_" if "total_inactive_file" in stat else ""
    return total, _reclaimable_cache(stat, prefix)


_window_peak_mb: "int | None" = None


def memory_used_mb() -> "int | None":
    global _window_peak_mb
    try:
        found = _memory_bytes()
        if found is None:
            return None
        total, cache = found
        used = _bytes_to_mb(max(0, total - cache))
    except Exception:
        return None
    if used is not None and (_window_peak_mb is None or used > _window_peak_mb):
        _window_peak_mb = used
    return used


def memory_cache_mb() -> "int | None":
    try:
        found = _memory_bytes()
        return None if found is None else _bytes_to_mb(found[1])
    except Exception:
        return None


def note_memory() -> None:
    memory_used_mb()


def memory_peak_mb() -> "int | None":
    return _window_peak_mb


def reset_memory_peak() -> None:
    global _window_peak_mb
    _window_peak_mb = None


def cpu_used_seconds() -> "float | None":
    """CPU seconds the whole service has used since it started (v2 `cpu.stat`'s
    usage_usec, v1 `cpuacct/cpuacct.usage` in nanoseconds). None when unreadable."""
    try:
        text = _read("cpu.stat")
        if text is not None:
            for line in text.splitlines():
                fields = line.split()
                if len(fields) == 2 and fields[0] == "usage_usec":
                    return int(fields[1]) / 1_000_000
        value = _read_int("cpuacct", "cpuacct.usage")
        return None if value is None or value < 0 else value / 1_000_000_000
    except Exception:
        return None


def service_bots() -> "list[str]":
    """The bot folders this service runs: MUMU_SERVICE_BOTS (main.py sets it), else all
    five -- the cautious answer, since each bot then plans for sharing the machine."""
    try:
        found = [name.strip() for name in (os.environ.get("MUMU_SERVICE_BOTS") or "").split(",")]
        found = list(dict.fromkeys(name for name in found if name))
        return found or list(ALL_BOTS)
    except Exception:
        return list(ALL_BOTS)


def share(folder: "str | None") -> float:
    """This bot's part of what the service has for work: its weight over the weights of the
    bots the service runs. 0 for a bot with no weight. A bot that asks is counted among them
    even if the list forgot it, so a share never passes 1."""
    try:
        weight = WEIGHTS.get(folder or "", 0)
        if not weight:
            return 0.0
        running = set(service_bots()) | {folder}
        total = sum(WEIGHTS.get(name, 0) for name in running)
        return weight / total if total else 0.0
    except Exception:
        return 0.0


def memory_reserve_mb(limit: int) -> int:
    """What is kept back from `limit` for the bots themselves, their database pool and the
    interpreters: MUMU_MEMORY_RESERVE_MB, else a quarter, at most 1024."""
    return _override("MUMU_MEMORY_RESERVE_MB", whole=True) or min(1024, limit // 4)


def job_memory_mb(folder: "str | None") -> "int | None":
    """The memory this bot's work (conversions, downloads) may count on, in MB: what is left of
    the service's memory after the reserve, times the bot's share. None when the limit is not
    known (a laptop), and the bot keeps the numbers it always had."""
    try:
        limit = memory_limit_mb()
        if limit is None:
            return None
        return int(max(0, limit - memory_reserve_mb(limit)) * share(folder))
    except Exception:
        return None


def own_folder(bot_id: "str | None" = None) -> "str | None":
    """The folder of the bot this copy of the module belongs to. main.py loads a bot's modules as
    `<folder>.<module>`; a module loaded on its own (the tests' mirror) falls back on the bot id
    family_link holds ("convertbot" -> "convert_bot")."""
    try:
        package = __name__.partition(".")[0] if "." in __name__ else ""
        if package in ALL_BOTS:
            return package
        if bot_id and bot_id.endswith("bot") and len(bot_id) > 3:
            folder = bot_id[:-3] + "_bot"
            return folder if folder in ALL_BOTS else None
    except Exception:
        pass
    return None


def holds_whole_service() -> bool:
    """True when this process runs every bot of the service. False in a bot's own process
    (MUMU_PROCESSES=separate, where main.py sets MUMU_CHILD)."""
    return not os.environ.get("MUMU_CHILD")


def service_ledger():
    import sys
    import threading
    import types

    found = sys.modules.get("_mumu_service_ledger")
    if found is None:
        made = types.ModuleType("_mumu_service_ledger")
        made.lock = threading.Lock()
        made.disk = {}
        made.slots = {}
        found = sys.modules.setdefault("_mumu_service_ledger", made)
    return found


def reserve_disk(owner: str, nbytes: int) -> object:
    ledger = service_ledger()
    token = object()
    with ledger.lock:
        ledger.disk[token] = (owner, max(0, int(nbytes or 0)))
    return token


def release_disk(token) -> None:
    if token is None:
        return
    ledger = service_ledger()
    with ledger.lock:
        ledger.disk.pop(token, None)


def reserved_disk_bytes(owner: "str | None" = None) -> int:
    ledger = service_ledger()
    with ledger.lock:
        return sum(size for who, size in ledger.disk.values() if owner is None or who == owner)


def shared_slots(name: str, count: int):
    import asyncio

    ledger = service_ledger()
    loop = asyncio.get_running_loop()
    with ledger.lock:
        held = ledger.slots.get(name)
        if held is None or held[0] is not loop:
            held = ledger.slots[name] = (loop, asyncio.Semaphore(max(1, count)), max(1, count))
    return held[1]


def _display_name(folder: str) -> str:
    return "".join(part.capitalize() for part in folder.split("_"))


def _where() -> str:
    """Where the numbers in describe() came from."""
    set_by = [name for name, whole in (("MUMU_MEMORY_MB", True), ("MUMU_CPUS", False))
              if _override(name, whole=whole)]
    if set_by:
        return "set by " + " and ".join(set_by)
    if _limit_from_cgroup() is not None or _cpu_quota() is not None:
        return "read from the container"
    return "not known here"


def describe(folder: "str | None") -> str:
    """One line for /status: the limits this service has and this bot's share of them."""
    try:
        limit = memory_limit_mb()
        text = (f"Service limits: {'unknown' if limit is None else limit} MB memory, "
                f"{cpu_limit():g} vCPU ({_where()}); "
                f"bots in this service: {', '.join(_display_name(name) for name in service_bots())}")
        if folder and WEIGHTS.get(folder, 0):
            job = job_memory_mb(folder)
            text += f"; {_display_name(folder)}'s share for its work: {'unknown' if job is None else job} MB"
        return text + "."
    except Exception:
        return "Service limits: not readable here."
