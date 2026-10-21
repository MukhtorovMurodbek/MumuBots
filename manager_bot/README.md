<img src="logo.svg" alt="ManagerBot" width="72" align="right">

# ManagerBot

The private one. It watches the other four bots in the family, reports
anything wrong without being asked, and runs any of their owner-only commands
from a single chat.

Unlike its four siblings this bot has no public side at all. `MBOT_ADMIN_ID`
is required rather than optional: with it empty ManagerBot refuses to start
rather than run open to whoever finds it. Anyone not on that list gets one
sentence, and the operator is told they turned up.

This repository is published for reference. It is not intended to be useful
on its own — it monitors four specific sibling bots through a shared Postgres
database, and without them there is nothing for it to watch.

## What it does on its own

- **Watches uptime.** Every bot stamps a heartbeat into the shared database
  every 30 seconds. ManagerBot checks once a minute and reports when one goes
  stale, and again when it comes back. It alerts on the *change*, so a bot
  that stays down does not repeat itself.
- **Forwards crashes.** Every unhandled exception in any bot is counted for
  that bot's own `/status` and also arrives here, with the tail of the
  traceback.
- **Reports donations**, which is the one unprompted interruption worth
  having.
- **Sends a nightly report** at 22:00 Korean time (`MBOT_NIGHTLY_UTC`, 13:00
  UTC by default): urgent problems one by one, faults grouped by code, and
  everything the bots handled correctly by what kind of thing it was — people
  shown how to use a bot, limits reached, old buttons and links, the other
  person's choice, and anyone turned away during an update.
- **Passes on feedback.** What people send with `/feedback` in the public
  bots arrives here, each piece as it comes or all of them with the nightly
  report; `/feedback` switches between the two. It carries the words, the bot
  and the language, never who wrote them — so it is read only: there is
  nobody to answer.
- **Counts the family's unique people.** Every 15 minutes it counts everyone who
  has used any of the bots, each person once however many bots they use, and the
  first time that reaches 220 it tells you, once, so you can start the 220 ⚡
  gift with `/promo`. `/users` shows the same count on its last line.
- **Says when it has gone blind.** If ManagerBot's own database connection
  fails it says so over Telegram, because otherwise the one failure that
  stops every alert would be the one failure nobody hears about.
- **Knows a deploy from a crash.** A redeploy makes a heartbeat stale exactly
  the way a crash does, so a bot shut down on purpose leaves a note in the
  shared database on its way out and the watchdog stays quiet at both ends.
  If it does not come back within `MBOT_REDEPLOY_GRACE_SECONDS` (300), the
  ordinary "it is down" alert fires after all — a deploy that never came
  back is exactly what is worth being told about.
- **Knows a version gap from a fault.** The bots deploy independently, so
  ManagerBot is routinely a version or two ahead of what it is asking. A
  command a bot is too old to know about is answered with both version
  numbers and what to publish, rather than with "Unknown command".

## /ping, in detail

`/ping <bot>` measures the whole round trip rather than answering yes or no:

```
🏓 StickerBot — 29 ms on the bus

you → Telegram → ManagerBot       380 ms   ±1 s
ManagerBot → Telegram (ack)        31 ms
ManagerBot → Supabase (queue)      12 ms
queued → StickerBot claimed it     9 ms
StickerBot answering               6 ms
Supabase → ManagerBot              14 ms
────────────────────────────────────────
bus round trip                    29 ms
```

Two machines, one honest clock: every cross-machine figure is the difference
between two Postgres timestamps, so none of them is contaminated by the gap
between this host's clock and Railway's. Each end additionally reports its
own round trip to Supabase and its own skew against it, which is what makes
"the database is slow from there" distinguishable from "that bot is busy".

The first line is the only approximate one — Telegram stamps messages with
whole seconds — and it is labelled that way rather than quietly presented as
precise.

`/ping` with nothing named asks everyone at once and reports one line each,
with a button per bot underneath for the full breakdown above — rendered
from the ping that just ran, not from a second one. `/ping parent` measures
ManagerBot against the database and back, which is the cleanest reading of how
far this machine is from it.

## Commands

Watching:
- `/status` — every bot: up/down, uptime, host, version, errors, active users
- `/me` — ManagerBot's own status, in the shape every other bot uses
- `/events [bot] [n]` — recent crashes, startups, payments
- `/alerts on|off` — mute or unmute the unprompted messages

A few seconds after ManagerBot itself starts it sends one **startup roll-call**:
which of the four are up, which just came up with it, and which are missing.
One message, not five — the delay (`MBOT_ROLLCALL_SECONDS`, default 5) is
there so the whole family has registered before it reports. `/alerts off`
silences it like everything else.

Reaching into a bot:
- `/run <bot> <command> [args]` — the general form; run it bare for the list
- `/ping [bot]` — the round trip, leg by leg; no bot named pings all at once
- `/errors <bot>` — that bot's errors since it last started
- `/logs <bot> [n]` — tail its `errors.log` (add `bot` for `bot.log`, `problems` for `problems.log`)
- `/errorlog`, `/botlog`, `/problemlog` `[bot] [n]` — the same three logs without remembering how:
  name no bot and each answers with a button per bot. `problems.log` has one line for every
  problem people were shown — time, code, incident, and whether it offered a Report button
- `/whois <bot> <user_id>` — look an account up through that bot
- `/say <bot> <user_id> <text>` — message someone **as** that bot
- `/dbdump <bot>` — that bot's own tables as a zip of CSVs
- `/run <bot> versions [name]` — the packages that process really runs, as a requirements file
  (`name==version`) to pin from; with a name, only the matching lines
- `/restart <bot>` — restart its process
- `/crashtest <bot>` — make it raise on purpose, to confirm the alert arrives
- `/providers`, `/probe` — download-route health, and an active probe
- `/stars` — the Stars ledger

Every owner-only command any bot in the family has is reachable from here.
The last three name only one bot each, so the bot name is optional.

Across the whole family:
- `/users [hours]` — active users per bot, default 24h, and the unique people across all of them
- `/promo [stop]` — the 220 ⚡ gift. Once the bots have 220 unique people, a card to start it: once, ever, and
  it cannot be undone. From the start, the first message a person sends ConvertBot or DownloaderBot in the
  next 14 days gives them 220 ⚡, and AnonBot shows an ad for it once to each person who writes to it. After the
  start, `/promo` shows how it is going (when it started and closes, how many claimed it, how many saw the ad);
  `/promo stop` ends the claiming early, and credit already given stays
- `/donations` — paid donations per bot
- `/sql <SELECT …>` — read-only query against the shared database
- `/backup` — the entire database as one zip of CSVs
- `/problems [hours] [mine]` — the nightly report, on demand (`/nightly` still answers)
- `/reports [n] [mine]` — every problem the bots showed anybody, newest first; `mine` for the ones
  somebody attached their own details to
- `/feedback [instant|nightly]` — what people sent with `/feedback`, and whether each piece arrives
  as it comes or with the nightly report; `/feedback <id>` shows one in full
- `/decode <code>` — what an error code means: `/decode CV-TIMEOUT`, or any unambiguous part of one
- `/start`, `/help` — the same thing: this list, printed in the chat

Any unambiguous prefix names a bot, and so does its Telegram username:
`/logs stick`, `/run conv status`, `/dbdump anon` and `/logs @mumu_chat_bot`
all reach the right one.

## How it reaches the other bots

Not over the network — through the database. `/run` puts a row on
`family.commands`; the target bot's poller claims it, runs the handler in its
own process as its own Telegram identity, and writes the answer back. So:

- it works whether ManagerBot is on a laptop and the bot is in the cloud, or
  the other way round, with neither reachable from the other;
- a bot that is down never claims the command, and ManagerBot says so after 90
  seconds instead of hanging;
- `/say` arrives from the bot the person was already talking to, because that
  bot is what actually sends it.

ManagerBot reads the other bots' tables directly — that is what one shared
database is for — but never writes to them. Anything that changes state goes
through the command queue, so the owning bot does it with its own code.

See [../ARCHITECTURE.md](../docs/design/ARCHITECTURE.md) for the full picture and
`family_link.py` for the bus itself.

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env      # then fill in MBOT_TOKEN, MBOT_USERNAME, MBOT_ADMIN_ID
python bot.py
```

It needs the shared Postgres database the family uses. A numeric Telegram
account id can be read from [@userinfobot](https://t.me/userinfobot).

`/crashtest` raises on purpose, so the whole chain — `errors.log`, the error
counter, and the alert arriving in the chat — can be confirmed without
waiting for a real bug. `/crashtest <bot>` does the same inside another bot,
which is the more useful direction: what is worth knowing when a bot goes
quiet is whether *that* bot would have reported it.

## Also here

`family_db.py` is a portable database exporter and importer. It moves rows
over the same psycopg connection the bots use, so it needs no `pg_dump` and
cannot hit a client/server version mismatch:

```bash
python family_db.py backup  --from cloud
python family_db.py restore --into local --file backups/family_….zip
python family_db.py copy    --from cloud --into local
python family_db.py tables  --at cloud
```

`..\db_backup.ps1` wraps it (and `pg_dump`, when that's available and new
enough) into `save` / `load` / `pull` / `push`.

## Licence

AGPL-3.0-or-later — see [LICENSE](LICENSE).

This is the licence the AGPL'd PyMuPDF asks for, and §13 of it is the reason:
anybody who interacts with this software over a network must be offered its
source. A Telegram bot is exactly that case, since nobody using it ever holds
a copy. The source is here, which satisfies §13 for this deployment; anybody
running a modified version as a service has to publish their changes too.
