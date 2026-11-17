# `resources.py`

[↑ shared](README.md) · [code](resources.py)

What the machine this service runs on allows, and what a bot may count on.

Since 2.2.0 a service (a Railway service, a container) may be given its own memory and CPU,
and may run all five bots or only some of them. Every size a bot picks for itself -- how many
conversions at once, how many downloads, how big an archive may be -- comes from here, so that
one rule decides it and a larger machine is used without anybody editing a number.

Pure reads, never raising: any error is `None`, or the fallback named in the function. Nothing
here changes anything and nothing personal is logged. The container's limits come from the
cgroup files the kernel keeps (`CGROUP`; tests point it at a scratch folder), and the operator can always say otherwise:

```text
MUMU_MEMORY_MB          the service's memory in MB (a positive whole number)
MUMU_CPUS               the service's vCPUs (a positive number, 0.5 is half a core)
MUMU_MEMORY_RESERVE_MB  what is kept back for the bots themselves (default a quarter, at most 1024)
MUMU_SERVICE_BOTS       the bots this service runs, comma-separated folders; main.py sets it
```

A bad value is ignored, as if it were not set.

_At `ALL_BOTS = ("sticker_bot", "convert_bot", "downloader_bot", "anon_bot", "manager_bot")`:_

The bots, in the order main.py lists them, and how much of the memory left over after the
reserve each is planned for. ConvertBot's work is the heavy one (a video, a 200 MP photo),
DownloaderBot's is next (yt-dlp, an album PDF), StickerBot's small; AnonBot and ManagerBot
only relay messages, so their work is planned for nothing.

_At `_NO_LIMIT = 1 << 62`:_

A cgroup that has no limit reports a number near 2^63, "the whole machine".

### `_read_int()`

A whole number from a cgroup file; None for a missing file, "max" or nonsense.

### `_override()`

The positive number in the environment variable `name`, else None.

### `_limit_from_cgroup()`

The cgroup's memory limit in MB, v2 first and then v1. None: none, or unreadable.

### `memory_limit_mb()`

The memory this service may use, in MB: MUMU_MEMORY_MB, else the cgroup's limit
(v2 `memory.max`, v1 `memory.limit_in_bytes`). None when neither says.

### `_cpu_quota()`

The cgroup's CPU quota as a number of vCPUs; None when there is none.

- `text = _read("cpu.max")` — v2: "&lt;quota> &lt;period>"
- `quota = _read_int("cpu", "cpu.cfs_quota_us")` — v1: -1 means none

### `cpu_limit()`

The vCPUs this service may use: MUMU_CPUS, else the cgroup quota or the CPUs
this process may run on, whichever is smaller. At worst the CPU count, at least 1.

### `cpu_used_seconds()`

CPU seconds the whole service has used since it started (v2 `cpu.stat`'s
usage_usec, v1 `cpuacct/cpuacct.usage` in nanoseconds). None when unreadable.

### `service_bots()`

The bot folders this service runs: MUMU_SERVICE_BOTS (main.py sets it), else all
five -- the cautious answer, since each bot then plans for sharing the machine.

### `share()`

This bot's part of what the service has for work: its weight over the weights of the
bots the service runs. 0 for a bot with no weight. A bot that asks is counted among them
even if the list forgot it, so a share never passes 1.

### `memory_reserve_mb()`

What is kept back from `limit` for the bots themselves, their database pool and the
interpreters: MUMU_MEMORY_RESERVE_MB, else a quarter, at most 1024.

### `job_memory_mb()`

The memory this bot's work (conversions, downloads) may count on, in MB: what is left of
the service's memory after the reserve, times the bot's share. None when the limit is not
known (a laptop), and the bot keeps the numbers it always had.

### `own_folder()`

The folder of the bot this copy of the module belongs to. main.py loads a bot's modules as
`<folder>.<module>`; a module loaded on its own (the tests' mirror) falls back on the bot id
family_link holds ("convertbot" -> "convert_bot").

### `holds_whole_service()`

True when this process runs every bot of the service. False in a bot's own process
(MUMU_PROCESSES=separate, where main.py sets MUMU_CHILD).

### `_where()`

Where the numbers in describe() came from.

### `describe()`

One line for /status: the limits this service has and this bot's share of them.
