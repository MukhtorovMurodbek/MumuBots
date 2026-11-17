import logging
import math
import os
from pathlib import Path

logger = logging.getLogger(__name__)

CGROUP = Path("/sys/fs/cgroup")

ALL_BOTS = ("sticker_bot", "convert_bot", "downloader_bot", "anon_bot", "manager_bot")
WEIGHTS = {"convert_bot": 4, "downloader_bot": 2, "sticker_bot": 1}

_MB = 1024 * 1024
_NO_LIMIT = 1 << 62


def _read(*parts: str) -> "str | None":
    try:
        return CGROUP.joinpath(*parts).read_text(encoding="utf-8").strip()
    except Exception:
        return None


def _read_int(*parts: str) -> "int | None":
    text = _read(*parts)
    if text is None:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _override(name: str, whole: bool = False) -> "float | int | None":
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
    for parts in (("memory.max",), ("memory", "memory.limit_in_bytes")):
        value = _read_int(*parts)
        if value is not None and 0 < value < _NO_LIMIT:
            return value // _MB or None
    return None


def memory_limit_mb() -> "int | None":
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
    text = _read("cpu.max")
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
    quota = _read_int("cpu", "cpu.cfs_quota_us")
    period = _read_int("cpu", "cpu.cfs_period_us")
    if quota is not None and period and quota > 0 and period > 0:
        return quota / period
    return None


def cpu_limit() -> float:
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
    try:
        found = [name.strip() for name in (os.environ.get("MUMU_SERVICE_BOTS") or "").split(",")]
        found = list(dict.fromkeys(name for name in found if name))
        return found or list(ALL_BOTS)
    except Exception:
        return list(ALL_BOTS)


def share(folder: "str | None") -> float:
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
    return _override("MUMU_MEMORY_RESERVE_MB", whole=True) or min(1024, limit // 4)


def job_memory_mb(folder: "str | None") -> "int | None":
    try:
        limit = memory_limit_mb()
        if limit is None:
            return None
        return int(max(0, limit - memory_reserve_mb(limit)) * share(folder))
    except Exception:
        return None


def own_folder(bot_id: "str | None" = None) -> "str | None":
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
    set_by = [name for name, whole in (("MUMU_MEMORY_MB", True), ("MUMU_CPUS", False))
              if _override(name, whole=whole)]
    if set_by:
        return "set by " + " and ".join(set_by)
    if _limit_from_cgroup() is not None or _cpu_quota() is not None:
        return "read from the container"
    return "not known here"


def describe(folder: "str | None") -> str:
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
