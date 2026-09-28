# What these five bots cost to run, and what was done about it

Railway bills resident memory and CPU by the minute; Supabase bills a
database instance sized to the load put on it. Five always-on Python
processes and one Postgres is not an expensive shape, but almost all of what
it *was* spending went on work nobody asked for.

Most of the obvious wins were already taken before this pass — the
connection pool instead of a connection per query, batched activity writes,
capped ffmpeg threads, a measured rather than laddered VP9 encode, a 30s long
poll, pruned per-user memory. What follows is what was left.

## Where the money was going

### 177,000 database queries a day to find nothing

Every bot polled `family.commands` every 3 seconds, forever, asking "anything
for me?". Five processes × 1,200/hour, plus ManagerBot's own result pump
(every 3s) and event pump (every 20s). Around **177,000 queries a day**, of
which the number that returned a row was somewhere near zero.

The fix is an **adaptive poll**. Each reader still polls, but the interval
tightens after activity and relaxes when nothing is happening: about once a
second while the bus is busy, once every ten seconds once it has gone quiet.
`family_link.mark_bus_active()` opens the fast window, and anything that
expects traffic — a command just queued, claimed, or answered — calls it. So
a command is picked up in about a second when the owner is actually using the
bus, and within ten seconds after a long silence.

| | before | after |
|---|---|---|
| command queue poll (idle) | every 3s | every 10s |
| command queue poll (busy) | every 3s | every 1s |
| ManagerBot result pump | every 3s | 1s busy / 20s idle |
| ManagerBot event pump | every 20s | 2s busy / 30s idle |
| queries per day | ~177,000 | ~50,000 |
| a command's delivery latency | 0–3s | ~1s busy, ≤10s cold |

v1.0.1 to v1.1.3 layered a `LISTEN`/`NOTIFY` push on top of the poll to get
millisecond delivery. It was removed in **v1.1.4**: it needed a dedicated
held connection per bot, it silently never started on any bot for two months
(v1.1.2), and `psycopg`'s async connection cannot run on Windows' default
event loop at all — so it never worked on the machine the bots are developed
on. Each time, the poll underneath carried the whole bus and nobody noticed.
For an admin bus with one human operator, a held connection and a reconnect
state machine bought a few seconds on a manually typed command. The adaptive
poll keeps most of the query saving, has nothing that can be "down" short of
the database itself, and behaves identically on a laptop and on Railway.

### Malloc arenas nobody was using

glibc gives a threaded process up to `8 × cpu_count` separate malloc arenas,
and inside a container `cpu_count` is the *machine's* core count, not the
share this bot has. Each arena keeps its own free list, and free lists are
resident memory the process never hands back — which is exactly what a
usage-billed host charges for.

`MALLOC_ARENA_MAX = "2"` in every `nixpacks.toml`. It costs a little
allocator contention at a concurrency these bots will never reach.

### A garbage collector re-scanning the same imports forever

Everything imported at startup — python-telegram-bot, psycopg, the bot's own
modules — is permanent by definition, and the collector walked all of it on
every full collection for the life of the process. `gc.freeze()` at the end
of `tune_runtime()` moves it out of the collector's reach; the generation-0
threshold goes from 700 to 5,000, because a bot handling a message a minute
was otherwise sweeping a heap of long-lived objects every few seconds.

Both are CPU, not memory, and on an idle process CPU is most of the bill.

## Seeing it for yourself

`/status` (in any bot, or `/run <bot> status` from ManagerBot) now ends with a
line the platform's own graphs cannot give you at this granularity:

```
Memory: 84 MB resident (peak 96 MB) of 512 MB allowed · CPU: 214s used since start
```

Read from `/proc/self/status` and the container's own cgroup, so "allowed" is
what this container may use, not what the host has. That is the number to
watch before and after any change here — including whether these ones helped.

`/ping <bot>` answers the other half of the question: the round trip broken
down leg by leg, including each end's own distance from Supabase, so "the
database is slow from Railway" is distinguishable from "that bot is busy".

## What was considered and not done

**Capping container memory** (`limitOverride` in `railway.json`). It does not
reduce a usage-billed bill — you pay for what you use, not what you reserve —
and a cap low enough to be a useful guardrail is also low enough to OOM-kill
a legitimate ffmpeg run. Worth adding as a deliberate blast-radius limit if
a runaway ever happens; not worth adding speculatively.

**Railway's app sleeping.** A slept service costs nothing, and it is
unreachable to these bots: sleeping requires *no outbound traffic*, and a
long-polling bot is outbound traffic by definition. Getting there would mean
switching all five to webhooks, dropping the heartbeats that make ManagerBot
work, and accepting a cold start (and a possible 502) on the first message
after every quiet spell. That is a different product, not an optimisation.

**`PYTHONOPTIMIZE=2`.** These files are roughly half comment and docstring by
volume, so stripping docstrings from the compiled code would measurably cut
resident memory. It also disables every `assert` and breaks any library that
reads `__doc__` at runtime. Measured in v1.7.1 on the one-process shape: 76 MB
down to 72 MB, about 5%. Available if memory ever becomes the binding
constraint; not worth the class of bug it invites for that.

**Running all five bots in one process — built in v1.7.1, as a second way to
run, not the default.** Five interpreters is five copies of Python,
python-telegram-bot, psycopg and httpx, and measured it is most of the
bill: idle after startup, the five as separate processes hold **302 MB**
between them; the same five in one process hold **77 MB**. Each bot adds
about 4 MB to a shared process; the rest is paid once.

It is not the default because it trades away the property every README in
this repo is built on: each bot is its own process, its own repo and its own
deployment, and one bot's crash or bad deploy cannot touch the other four.
The owner made the call in v1.7.1 -- keep the five services, and have the
one-process shape ready for when credit is what runs out.

So it exists alongside them rather than instead of them. Since 2.0.0 it
is its own code, in its own folder (`BotFamily<version>C`, the public
`BotFamily` repository), arranged by function, squeezed, and released less
often -- `DEPLOY.md` §9. What it gives up is exactly that isolation: one OOM
kill or one deploy takes all five, and `/run <bot> restart` restarts all
five.

ConvertBot's conversions run in a child process, so its biggest memory
spike -- a 200 MP decode near 1.5 GB -- is the process the kernel kills
first if the container runs out, not the one the other four live in.
