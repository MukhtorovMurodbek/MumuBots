# Deploying the family

Five bots, one Postgres. What changes between "running on the laptop" and
"running in the cloud" is one environment variable per bot, so this document
is mostly about where that variable points. See
[ARCHITECTURE.md](../design/ARCHITECTURE.md) for why it is shaped this way.

**The shape this is written for:**

| piece | where | why |
|---|---|---|
| the family database | Supabase, free tier | reachable from anywhere, costs nothing |
| ManagerBot | Railway | the watchdog has to outlive the laptop |
| the four public bots | laptop, or Railway | wherever you want them today |

The database being reachable from everywhere is what makes the last row a
choice rather than a commitment. A bot is only ever bound to a place by its
`DATABASE_URL`; nothing else about it is local or cloudy.

> **One bot, one place at a time.** Two processes polling `getUpdates` with
> the same token gets the second one `409 Conflict` from Telegram. Stop a bot
> where it is running before you start it somewhere else. This is the same
> reason `railway.json` pins `numReplicas: 1`.

---

## 0. Before anything: try it locally

The local setup is the deployed setup with a different URL, so getting it
working on the laptop first is the fastest way to find mistakes.

```bash
docker compose up -d
```

That is the shared Postgres, database `botfamily`, on `localhost:5432`.

Then the settings, in two places (since 2.0.0):

- `shared/.env.example` to `shared/.env` -- what every bot shares: the
  database (leave `DATABASE_URL` as it comes for the local one), the Telegram
  app's API id and hash, your contact for `/privacy`.
- in each of the five bot folders, `.env.example` to `.env` -- that bot's
  token, username, and your numeric Telegram id; leave `DB_SCHEMA` as it
  comes.

A bot's own `.env` wins over `shared/.env`, and the real environment wins over
both (`shared/envfiles.py`). A `.env` written before 2.0.0, with everything in
it, keeps working as it is. Then:

```powershell
.\start_all.ps1
```

Message ManagerBot `/status`. All five should be listed as up within a minute.

### Starting and stopping a subset

Both scripts take `-Exclude` and `-Only`. Once ManagerBot lives in the cloud,
the everyday local command is:

```powershell
.\start_all.ps1 -Exclude ManagerBot
.\stop_all.ps1  -Exclude ManagerBot
```

Matching is case-insensitive and by prefix, against either the display name or
the folder name, so `parent`, `ManagerBot` and `manager_bot` all select the same
bot. `-Only Sticker,Convert` works the same way.

`stop_all.ps1` identifies a bot by the working directory in its parent
window's command line, because the process's own command line is just
`python bot.py` for all five. A bot started some other way therefore cannot be
identified — when a filter is in play those are left running and reported,
rather than guessed at and killed.

### Coming from the old four-database setup

Each bot used to have a database entirely of its own (`stickerbot`,
`convertbot`, …). To bring that data into the shared one:

```bash
python migrate_to_shared_db.py --into postgresql://postgres:postgres@localhost:5432/botfamily --dry-run
```

It reads each bot's old database, creates that bot's schema in the new one
using the bot's own `db.py`, and copies every row across. It never deletes,
never overwrites, and leaves the old databases exactly as they were. Drop
`--dry-run` when the preview looks right.

---

## 1. The database, on Supabase

**New project.** Pick the region nearest **the containers**, not nearest you.
Every query the bots make crosses that distance, and the bots run on Railway,
not on your desk. This family got it wrong first time: the database sat in
`ap-northeast-2` (Seoul) while every container ran in EU West, which put a
~240 ms floor under a bare `SELECT 1` and shaped a year of tuning work — the
connection pool, the idle-check skip, the adaptive bus poll — that mostly
existed to make that distance survivable. It was moved to `eu-central-1`
(Frankfurt) at v1.2.0. Save the database password it generates; the dashboard
will not show it again.

### Which connection string

Supabase offers three, and this is the single most important choice in the
whole document. Project Settings → Database → Connection string.

| | port | use it? |
|---|---|---|
| Direct — `db.<ref>.supabase.co` | 5432 | only if you know you have IPv6; Supabase made this one IPv6-only |
| **Session pooler** — `…pooler.supabase.com` | **5432** | **yes, this one** |
| Transaction pooler — `…pooler.supabase.com` | 6543 | **no — it breaks the bots** |

Transaction pooling multiplexes many clients over a few server connections,
so per-connection startup options do not survive from one transaction to the
next. Every bot passes its `search_path` as exactly such an option
([db.py](../../sticker_bot/db.py), in the pool's `kwargs`), so on port 6543 a bot
reads and writes `public` instead of its own schema — and goes on
heartbeating as though nothing were wrong. It shows up as *wrong data*, not
as a broken bot, which is the worst way to find out. (psycopg's automatic
statement preparation is a second, independent casualty.)

**The session pooler has a ceiling, and the bots come close to it.** In session mode every client of the role
holds a server connection, and all of them together are capped at the project's *Pool Size* (Database settings →
Connection pooling; 15 by default). One combined container holds about 10 when idle -- five poll leases and five
pools of at least one -- and up to 20 when every pool has grown to its three; the base edition's five services the
same. A connection past the cap waits in the pooler for up to a minute and is then refused, which looks like every
bot stalling at once (it may be what happened on 2026-10-09 at 08:40 UTC). **Raise Pool Size to about 30 before
setting Railway's deployment overlap**: during an overlap two containers are connected at once, and even idle that
is over 15.

`db.py` warns at startup if it sees port 6543, but the fix is to not put it
there.

### The password, in a URL

Percent-encode it if it contains any of `@ : / ? #` or a space:

| character | write it as |
|---|---|
| `@` | `%40` |
| `:` | `%3A` |
| `/` | `%2F` |
| `#` | `%23` |
| `?` | `%3F` |
| space | `%20` |

`@` is the one that actually bites: it separates credentials from host, so an
unencoded one makes libpq resolve a hostname that is really the tail of your
password. The error names a host you have never heard of and says nothing
about authentication.

Separately, if the password contains `#`, **quote the whole value** in `.env`
— python-dotenv treats an unquoted `#` as the start of a comment and would
silently hand the bot a truncated password.

`db.py` warns about both of these at startup too.

### Free-tier notes

500 MB is far more than this family needs. Free projects pause after about a
week of inactivity, which never happens while any bot is heartbeating every
30 seconds — but if you shut the whole family down for a holiday, expect to
un-pause it from the dashboard. The free plan also allows two active projects
per organization, so check what you already have there.

---

## 2. Moving the data up

The cloud database starts **empty**. Each bot's `init_db()` creates its schema
and tables on first start, so nothing errors — but every language setting,
sticker pack, star transaction and activity row still lives only on your
laptop until you move it.

Use the portable Python path rather than `pg_dump`. Supabase's `postgres`
database already contains its own `auth`, `storage` and `extensions` schemas,
and restoring a whole-database dump into it is a good way to pick a fight with
those and with role ownership. [family_db.py](../../manager_bot/family_db.py) moves
rows, not structure, and the bots build their own schemas.

**1.** Put the session-pooler URL in `manager_bot\.env` as `CLOUD_DATABASE_URL`.
Leave `DATABASE_URL` pointing at localhost for now.

**2.** Start each bot once against the cloud so `init_db()` creates the five
schemas and `family.*`. Deploying ManagerBot (section 3) does its own; for the
other four, temporarily setting `DATABASE_URL` and running `python bot.py` for
a few seconds is enough.

**3.** Copy the data up. `merge` is the default — it inserts only rows the
target does not already have, so it is safe to run twice:

```bash
python manager_bot/family_db.py copy --from local --into cloud
```

**4.** Check it landed:

```bash
python manager_bot/family_db.py tables --at cloud
```

**5.** *Only now* change `DATABASE_URL` in all five `.env` files to the
Supabase URL. `DB_SCHEMA` does not change — it is the same value in both
places.

> Step 5 goes last on purpose. `family_db.py` reads its `local` alias from
> `DATABASE_URL`; flip that first and step 3 copies the cloud onto itself.

`.\db_backup.ps1 push -Force` wraps the same operation, and picks between
`pg_dump` and the Python path automatically.

### Moving to a *different* Supabase project

Done for real at v1.2.0, Seoul -> Frankfurt, so this is the procedure that
worked rather than the one that sounds right.

The two projects are both "cloud", so the `local`/`cloud` aliases stop being
useful. Pass full URLs instead — `resolve_dsn()` takes either.

**1.** Create the new project (region: see section 1) and note its
session-pooler URL.

**2.** Start each bot once against the new URL so `init_db()` builds the five
schemas and `family.*`. `family_db.py` moves rows, not structure; it needs the
tables to exist.

**3.** Pause the family, so nothing is writing to the old database while it is
being copied. ManagerBot's `/pause`, or a `pause` row per bot in
`family.commands`.

**4.** Copy, scoped to the family's own schemas:

```bash
python manager_bot/family_db.py copy --from "postgresql://...OLD..." --into "postgresql://...NEW..." --schemas anon_bot convert_bot downloader_bot sticker_bot manager_bot family
```

`--schemas` is not optional in spirit. `list_tables()` already skips the
Supabase-managed schemas (`auth`, `storage`, `realtime`, `vault`, ...), but
being explicit is what keeps a future platform schema out of the copy: none of
them are the family's data and several do not survive a CSV round trip.

**5.** Point every `DATABASE_URL` at the new project — all five `.env` files
*and* all five Railway services — and redeploy. `DB_SCHEMA` does not change.

**6.** Check the bots came back on the new database before lifting the pause:

```bash
python manager_bot/family_db.py tables --at "postgresql://...NEW..."
```

`family.heartbeats` should show all five checking in, and their `started_at`
should be after the redeploy.

**7.** Lift the pause. Anyone the pause turned away gets messaged.

**8.** Only then retire the old project, and keep the final backup. What is
left behind in the old database after the copy is a handful of rows the family
deliberately consumes and does not carry over: `family.commands` rows already
`done` and `delivered`, `maintenance:<bot>` settings lifted by the resume, and
`update_waitlist` rows drained when the bots came back. Everything else should
match row for row, and it is worth checking that it does before deleting
anything.

---

## 3. ManagerBot on Railway

ManagerBot is the one bot that has to be somewhere other than the laptop: it is
the thing that tells you when the laptop's bots have stopped, which it cannot
do from inside the same laptop.

**Since 2.0.0 it is built like the other four:** from this repository,
MumuBots, with its own Dockerfile. In a Railway project: **New → GitHub Repo →
`MumuBots`**, then in the service's settings **Config-as-code →
`/manager_bot/railway.json`**. That file names `manager_bot/Dockerfile`, built
from the repository root so it can take `shared/`, and rebuilds the service
only when `manager_bot/` or `shared/` changes; §4 has the rest. (Until 2.0.0
its repository was produced from this monorepo by `publish.ps1` and carried
its own `requirements.txt`, `Procfile`, `railway.json` and `nixpacks.toml`.)

Nothing else needs configuring in the build settings. Two things in
`railway.json` are worth knowing about rather than changing:

- **`numReplicas: 1`**, set explicitly — see the warning at the top of this
  document.
- **`sleepApplication: false`**. A bot that sleeps stops polling, which means
  it stops existing as far as its users are concerned.

Variables:

```
MBOT_TOKEN=…
MBOT_USERNAME=…
MBOT_ADMIN_ID=<your numeric telegram id>   # required — it won't start without this
DB_SCHEMA=manager_bot
DATABASE_URL=<the Supabase session-pooler URL>
```

Note that `DATABASE_URL` here is the ordinary Supabase string, not a Railway
reference variable — there is no Postgres service in this project to refer to.

---

## 4. If you want the four public bots in the cloud too

**Since 2.0.0: one repository, five services.** The bots share `shared/`, so a
service is no longer built from its own folder or its own repository but from
the whole repository, with that bot's Dockerfile. For each bot, a service
connected to the repository, and in its settings:

- **Config-as-code:** `/<bot>/railway.json` (for example `/convert_bot/railway.json`).
  That file names the build -- `<bot>/Dockerfile`, from the repository root --
  and the watch paths: the service rebuilds when `<bot>/` or `shared/`
  changes, and not for a change to another bot. **Railway stops reading these
  files on 2026-12-01** (its CLI announced it on 2026-09-28), so production's
  services carry the same settings on themselves instead:
  `python scripts\railway_move.py prepare --apply` makes the five services
  with them, and the file stays as the description of how a bot is built.
- **Variables:** as below. What every bot shares can be set once as a
  project-level (shared) variable -- `DATABASE_URL`, `TELEGRAM_API_ID`,
  `TELEGRAM_API_HASH`, `OPERATOR_CONTACT` -- and referenced from each service;
  a service's own variable wins.

Each image checks as it is built that every module the bot needs, its own
and `shared/`'s, imports -- a bot that could not start is never deployed.

**What the plan allows decides where they go.** The account production runs
on reported, on 2026-09-29: **5 services in a project, 2 projects, 1 GB of
memory and 2 vCPU a container.** BotFamily and five more is six, so the five
go into a project of their own (`railway_move.py prepare --project MumuBots`),
with BotFamily left stopped in its own for the rollback -- and the second
project is the last one. 1 GB is enough for every bot at rest and for video
up to 1080p; a 4K video with HDR does not convert in it (CHANGES_v2.0.0.md,
*Railway, measured*).

**What each service needs, to paste.** Settings first -- on the service
itself, since `railway.json` is read only until 2026-12-01:

| setting | value |
|---|---|
| Source | GitHub repo `MukhtorovMurodbek/MumuBots`, branch `main` |
| Root directory | empty (the repository root: the image takes `shared/`) |
| Builder | Dockerfile, path `<bot>/Dockerfile` |
| Watch paths | `/<bot>/**` and `/shared/**` |
| Restart policy | on failure, 10 retries |
| Replicas | 1 -- never more: two pollers on one token split its updates |
| Serverless / sleep | off |

Then the variables, per service (`<bot>` and the prefix as in the table):

| service | folder | prefix |
|---|---|---|
| StickerBot | `sticker_bot` | `SBOT` |
| ConvertBot | `convert_bot` | `CBOT` |
| DownloaderBot | `downloader_bot` | `DBOT` |
| AnonBot | `anon_bot` | `ABOT` |
| ManagerBot | `manager_bot` | `MBOT` |

```
<PREFIX>_TOKEN=<from @BotFather>
<PREFIX>_USERNAME=<the bot's username, no @>
ADMIN_ID=<your numeric Telegram id; comma-separated for more than one account>
DATABASE_URL=<the Supabase session-pooler URL>
DB_SCHEMA=<folder>                  # required: without it a bot's tables go to "public"
```

On the four public ones, also:

```
SIBLING_BOTS=stickerbot:StickerBot:mumu_sticker_bot,convertbot:ConvertBot:mumu_convert_bot,downloaderbot:DownloaderBot:mumu_downloader_bot,anonbot:AnonBot:mumu_chat_bot
PRIVACY_URL=https://github.com/MukhtorovMurodbek/MumuBots/blob/main/<folder>/PRIVACY.md
TERMS_URL=https://github.com/MukhtorovMurodbek/MumuBots/blob/main/<folder>/TERMS.md
```

And on ConvertBot and DownloaderBot, for files past 20 MB (optional -- without
them the two stay at the Bot API's 20 MB in and 50 MB out, and say so):

```
TELEGRAM_API_ID=<my.telegram.org -> API development tools>
TELEGRAM_API_HASH=<the same page>
```

`ADMIN_ID` stands in for `<PREFIX>_ADMIN_ID` (either works; the prefixed one
wins). The values every service shares -- `DATABASE_URL`, `ADMIN_ID`, the API
id and hash -- can be pasted once as project-level shared variables instead,
and each service given them. `scripts/railway_move.py prepare --apply` does
all of this without a paste, by reference to the variables the BotFamily
service already holds.

What follows is from before 2.0.0, when each bot deployed from its own
repository (`sticker-bot`, `convert-bot`, `downloader-bot`, `anon-bot`); the
variables are unchanged. Everything below the line is identical across all of
them; only the top three differ.

**sticker-bot**
```
SBOT_TOKEN=…
SBOT_USERNAME=…
SBOT_ADMIN_ID=<your numeric telegram id>
DB_SCHEMA=sticker_bot
```
**convert-bot**
```
CBOT_TOKEN=…
CBOT_USERNAME=…
CBOT_ADMIN_ID=<your numeric telegram id>
DB_SCHEMA=convert_bot
```
**downloader-bot**
```
DBOT_TOKEN=…
DBOT_USERNAME=…
DBOT_ADMIN_ID=<your numeric telegram id>
DB_SCHEMA=downloader_bot
```
**anon-bot**
```
ABOT_TOKEN=…
ABOT_USERNAME=…
ABOT_ADMIN_ID=<your numeric telegram id>
DB_SCHEMA=anon_bot
```

And on each: `DATABASE_URL=<the Supabase session-pooler URL>`, plus
`SIBLING_BOTS` (the same value on all four — it is what draws the
cross-promotion buttons). ManagerBot is private and is deliberately not in that
list.

And, on each of the four, the three that name a human:

```
OPERATOR_CONTACT=<optional; the policy commands print mukhtorovmurodbek@gmail.com without it>
PRIVACY_URL=https://github.com/<account>/<Repo>/blob/main/PRIVACY.md
TERMS_URL=https://github.com/<account>/<Repo>/blob/main/TERMS.md
```

`/privacy`, `/terms` and `/deletemydata` all work without them — the two
notices simply end a line short. What is lost is the only part a user cannot
work out for themselves: who is holding their data, and where to complain.
Set them.

**And paste the privacy URL into @BotFather** — `/mybots` → the bot → *Bot
Settings* → *Privacy Policy*. Telegram asks every bot for one and shows it in
the bot's own info panel, which is where somebody looks before they have sent
a first message. The push to MumuBots is what puts `PRIVACY.md` and
`TERMS.md` at those URLs in the first place --
`https://github.com/<account>/MumuBots/blob/main/<bot>/PRIVACY.md` -- so push
once before pasting.

### What @BotFather still owns

Everything else about a bot's presentation is set from code at startup --
`publish_commands()` for the slash menu and `publish_profile()` for the
description and the short description, in all three languages. Do **not** also
set those in BotFather: the next deploy overwrites them, and you would be left
wondering which of the two you are looking at.

**Your slash menu is not the one users see, and that is deliberate.** Since
v1.4.0 each bot publishes the public list at the default scope, and the public
list plus its owner-only commands -- marked with a lock -- into each id in
`*_ADMIN_ID`, using `BotCommandScopeChat`. So `/status` and `/dbdump` are in
your menu and in nobody else's. The guards have not changed: a scope decides
what is *offered*, `_is_admin` decides what is *allowed*. ManagerBot inverts it
and offers strangers `/start` alone. One consequence worth knowing: a scoped
menu outlives the reason for it, so an id dropped from `*_ADMIN_ID` keeps the
longer menu until `delete_my_commands` is called for that chat. Every command
in it still refuses them.

These are not settable over the Bot API and have to be done by hand, once:

1. **The profile photo.** `/mybots` → the bot → *Edit Bot* → *Edit Botpic*,
   then send `<bot folder>/logo.png`. They are 640×640, which is what
   BotFather asks for; `python scripts/make_logos.py` regenerates them.
   **Still not done on any of the five** — see `DESIGN.md`.
2. **The privacy policy link**, above.
3. **The description picture.** `/mybots` → the bot → *Edit Bot* → *Edit
   Description Picture*, then send `<bot folder>/hero.png`. They are 640×360
   and generated by the same `python scripts/make_logos.py` run as the
   avatars, from the same geometry — so a mark that changes changes both.
   This is the largest thing a stranger sees before pressing Start, and it
   was blank on all five until v1.6.0.

`nixpacks.toml` installs `ffmpeg` for the three bots that shell out to it, and
sets `PIP_NO_CACHE_DIR` (pip's wheel cache is written into the image and never
read again) plus `PYTHONUNBUFFERED` so the log viewer keeps up.

**ConvertBot's optional backends.** Since v1.5.0 the bot reads PDFs, e-books,
Word documents, spreadsheets and iPhone photos, and every one of those is an
optional line in `requirements.txt` — about 45 MB of image between them, most
of it PyMuPDF. None needs a system package, so `nixpacks.toml` is unchanged.
They are optional in a way that is enforced rather than claimed: `formats.py`
probes for each one at startup and leaves the formats it cannot produce off
the menu and out of `/formats`. A build that fails to install one is a bot
with a shorter format list, not a bot that takes somebody's Stars and hands
back an exception. The startup log says which are present; a line reading
`Not installed, so those formats are not offered:` is the thing to look for
after a deploy.

**Sizing.** Each bot idles at roughly 80–120 MB, which is Python plus
python-telegram-bot. Two exceptions. DownloaderBot: yt-dlp's extractor modules
are another 30–40 MB once loaded (deliberately imported on the first download
rather than at startup), and a download in flight adds the clip itself.
ConvertBot: the document and image backends above are imported lazily, so an
idle container does not carry them, but a PDF being rendered to pictures holds
a page at a time in memory on top of them. Give those two the most headroom;
the other three are comfortable on the smallest plan.

**Before you move one up, stop it down here.** See the warning at the top.

---

## 5. Check it

Message ManagerBot:

- `/status` — all five up, with hosts, versions and error counts
- `/ping` — asks every bot directly, through the command queue
- `/run sticker status` — StickerBot's own `/status`, answered by StickerBot
- `/backup` — the whole database, lossless and restorable, sent and pinned in the chat

Then stop one bot. Within about two minutes ManagerBot should message you that
it went down, and again when you start it back up. That round trip — a bot on
your laptop, a watchdog on Railway, a database on Supabase, and no direct
connection between any of them — is the whole system working end to end.

---

## 6. Getting the database out, and back in

```powershell
.\db_backup.ps1 save          # cloud -> backups\family_<stamp>.dump
.\db_backup.ps1 pull          # cloud -> your laptop's Postgres, in one go
.\db_backup.ps1 push -Force   # laptop -> cloud (destructive, hence -Force)
.\db_backup.ps1 list
```

Set `CLOUD_DATABASE_URL` in `manager_bot\.env` first — that's where the script
looks.

It uses `pg_dump`/`pg_restore` when they're installed and falls back to
`manager_bot/family_db.py`, a pure-Python exporter over the same psycopg the
bots use, when they aren't or when the server is newer than your local
Postgres. Force the portable path with `-Portable` if you want a file you can
restore anywhere regardless of versions. Against Supabase, prefer the portable
path — see section 2.

For the surgical version — reconcile both copies row by row, add only what's
missing, never delete — `db_sync.ps1` is still the right tool:

```powershell
.\db_sync.ps1 both            # preview
.\db_sync.ps1 both -Apply
```

And `/backup` in ManagerBot sends the same portable archive `family_db.py`
writes — every table, lossless, compressed — as a pinned document in its chat,
which needs nothing installed at all. Putting one back is below.

Supabase's free plan does not include automated backups, so this section is
the backup story rather than a supplement to one. `save` on a timer is worth
it.

### Restoring from a pinned backup

Every `/backup` stays pinned in ManagerBot's chat, newest first, and nothing in
the bot ever unpins one. Each is the archive `family_db.py` reads back.

1. **Download it** from the chat. A backup over 49 MB arrives as numbered
   parts, `.001`, `.002` and so on; join them first, in order:

   ```powershell
   cmd /c copy /b botfamily_2026-09-11_0400.zip.001+botfamily_2026-09-11_0400.zip.002 botfamily_2026-09-11_0400.zip
   ```

   or `cat botfamily_2026-09-11_0400.zip.0* > botfamily_2026-09-11_0400.zip`
   with any POSIX shell. The `sha256` in the caption is of the joined file, and
   `Get-FileHash` on it should print the same.
2. **Make sure the tables exist.** The archive holds rows, not structure. Start
   each bot once against the target database — locally, with that
   `DATABASE_URL`, is enough — so every schema and table is created, then stop
   them again.
3. **Check it, then load it:**

   ```powershell
   python manager_bot/family_db.py restore --into "postgresql://...TARGET..." --file botfamily_2026-09-11_0400.zip --mode replace --dry-run
   python manager_bot/family_db.py restore --into "postgresql://...TARGET..." --file botfamily_2026-09-11_0400.zip --mode replace
   ```

   `--dry-run` does the whole restore and rolls it back, which is the way to
   find out a backup is sound before needing it. `--mode replace` empties each
   table first, so the target ends up an exact copy; `--mode merge` only adds
   rows the target does not have.
4. **Start the bots**, and check `/status`.

---

## 7. Keeping the bill down

Putting the database on Supabase's free tier and running only ManagerBot on
Railway is most of the saving available: one small always-on service instead
of six, and no paid Postgres.

**The rest of the work is already done, in the defaults.** Every bot holds a
single pooled database connection instead of opening a new one per query,
batches its activity writes, polls Telegram on a 30-second window, asks for
only the update types it handles, and prunes its own old rows on a timer.
ARCHITECTURE.md's "What it costs to run" section has the details and the
numbers. **You do not need to set any of the variables below** — they exist
for when you want a different trade-off.

| variable | default | what more of it buys |
|---|---|---|
| `FAMILY_HEARTBEAT_SECONDS` | `30` | Less background traffic; slower down-detection. Pair with `MBOT_DOWN_AFTER_SECONDS`. |
| `FAMILY_BUS_POLL_IDLE_SECONDS` | `10` | Less background traffic; the first `/run` or `/ping` after a quiet spell takes longer to answer (a warm bus stays at `FAMILY_BUS_POLL_ACTIVE_SECONDS`, `1`). |
| `ACTIVITY_FLUSH_SECONDS` | `60` | Fewer writes; `/status`'s user counts lag by that much. |
| `POLL_TIMEOUT` | `30` | Fewer HTTP requests. No latency cost — Telegram answers as soon as an update exists. |
| `DB_POOL_MAX` | `3` | Headroom under concurrent load, at one reserved Postgres backend each. |
| `WORKER_THREADS` | `4` | Concurrency for blocking work (ffmpeg, image processing, database calls). |
| `DBOT_MAX_CONCURRENT_DOWNLOADS` | `3` | DownloaderBot only. Higher means faster for a crowd and a bigger memory ceiling. |
| `DBOT_MAX_PER_USER` | `2` | DownloaderBot only. How many of those one person may hold at once. |
| `DBOT_MAX_PENDING_PER_USER` | `5` | DownloaderBot only. Links one person may have waiting before another is refused. |
| `DBOT_MIN_FREE_MB` | `300` | DownloaderBot only. Free disk a download needs before it starts. |
| `DBOT_CACHE_SECONDS` | `300` | DownloaderBot only. How long the last download is kept for the redo button and the same link sent again. `0` turns the cache off. |
| `DBOT_CACHE_ENTRY_MAX_MB` / `DBOT_CACHE_MAX_MB` | `100` / `400` | DownloaderBot only. The most one kept delivery may be, and the most all of them may be together. |
| `DBOT_SENT_FILES` | `1` | DownloaderBot only. Whether a post the bot has already sent is sent again by its Telegram file ID, without downloading it. `0` (or a value that is not a whole number) turns it off: no reads, no writes. The references hold nothing about people. |
| `DBOT_FILEID_CONFIRM_HOURS` | `12` | DownloaderBot only. How long a stored file reference is trusted after the post was last seen public; an older one is sent only after a lookup shows the post still public. The texts state 12 (and 90) as fixed numbers: change `PRIVACY.md`, `TERMS.md` and `privacy_stored` (en/uz/ru) first. Unreadable or under 1 means 12. |
| `DBOT_FILEID_KEEP_DAYS` | `90` | DownloaderBot only. Stored file references nobody has used for this many days are deleted by the routine housekeeping. Unreadable or under 1 means 90. |
| `ACTIVITY_RETENTION_DAYS` | `90` | How far back `/users` can see, against stored rows. |

One more that isn't about tuning: `FAMILY_BUS=off` on any bot drops it back to
standalone — no ManagerBot, no heartbeats. Useful for isolating a problem, not
as a permanent setting.

### Credit, payments and the conversion queue

These are policy rather than tuning. **The `FAMILY_*` ones must be the same on
every bot, ManagerBot included**: each bot prices a payment made in it from its
own environment, so a mismatch means the same Stars buy different credit
depending on where they were paid. The defaults are what `TERMS.md` promises;
change one only together with the terms.

| variable | default | what it does |
|---|---|---|
| `FAMILY_TOPUP_MULTIPLIER` | `2` | ⚡ per ⭐ at the ordinary rate. This part of a payment never expires. |
| `FAMILY_TOPUP_LADDER` | `500:3,500:2` | A person's lifetime Stars in steps, with the multiplier on each: the first 500 at 3×, the next 500 at 2×, then 1×. Counted across all their payments. Empty turns bonuses off. |
| `FAMILY_BONUS_EXPIRY_DAYS` | `90` | How long bonus credit (everything above the ordinary rate) lasts. Stored with each payment, so a change applies to new payments only. |
| `FAMILY_STARS_SANDBOX` | off | `1` credits every Stars payment without charging for it. **TestBot only — never on a bot real people pay.** |
| `FAMILY_REPORTS_TO` | `8796896653` | The Telegram user id problem reports are messaged to. A bot that cannot message it — the account never started that bot — raises the report as a warning event instead, which ManagerBot forwards. `/reports` in ManagerBot lists them either way. |
| `FAMILY_REPORT_BUTTONS` | on | `off` hides the "Report the issue" button under coded error messages. The codes stay. |
| `OPERATOR_CONTACT` | `mukhtorovmurodbek@gmail.com` | The contact the policy commands print. Set it only to change it. |
| `CBOT_API_ID`, `CBOT_API_HASH` | unset | ConvertBot only. From my.telegram.org → *API development tools*. With both set, files over the Bot API's 20 MB (in) and 50 MB (out) move over MTProto instead, in private chats — `convert_bot/big_files.py`. Unset, the limit is 20 MB. |
| `CONVERT_MAX_FILE_MB` | `100`, or `20` | ConvertBot only. The largest upload. `100` when files over 20 MB can be fetched at all (the two above, or a local Bot API server); never above `20` without them. Raise it only once the host has shown it converts that much inside `CONVERT_TIME_LIMIT_SECONDS`. |
| `CONVERT_MAX_CONCURRENT` | `1` | ConvertBot only. Conversions running at once. A 200 MP photo alone can take about 1.5 GB. |
| `CONVERT_TIME_LIMIT_SECONDS` | `600` | ConvertBot only. How long a paid conversion may run before it is stopped and its credit given back. |
| `CONVERT_FREE_TIME_LIMIT_SECONDS` | `60` | ConvertBot only. The same, for a free one. |
| `CONVERT_MAX_QUEUED_PER_USER` | `5` | ConvertBot only. Conversions one person may have waiting or running. |
| `CONVERT_USER_STORAGE_MB` | largest album + 50 | ConvertBot only. Disk one person's kept files may use. |
| `CONVERT_STORAGE_MB` | `1024` or more | ConvertBot only. Disk everybody's kept files may use together. |
| `CONVERT_MIN_FREE_MB` | `300` | ConvertBot only. Free disk that must remain after a file is taken in. |
| `CONVERT_MAX_MEGAPIXELS` | `210` | ConvertBot only. A larger picture is refused before anything is charged. |
| `CONVERT_WORKER_MAX_MEMORY_MB` | `0` | ConvertBot only. A hard memory cap on the conversion subprocess; `0` leaves it to the container. |

### A note on running the bots locally against a cloud database

Every query now crosses the internet instead of hitting localhost. The
connection pooling is what makes this tolerable — it is largely why the pool
exists — but it is real latency on every database call, and the laptop needs
a working connection to do anything at all. If a bot feels sluggish where it
did not before, that is where the time is going.

---

## 8. Shipping the ParentBot -> ManagerBot rename

v1.6.0 renamed the private bot. Six of the seven naming layers are text in
this repository and are already done; two things are not, and one of them is
not optional.

### 8a. The database, before the deploy

**Required.** The bus id `parentbot` and the schema `parent_bot` are rows and
objects in the shared database, and editing files does not move them. Skip
this and ManagerBot starts against an empty `manager_bot` schema — its own
tables look wiped — while `/status` reports a sixth bot that has been silent
since the deploy. Nothing is lost, but everything is in two places.

```bash
python scripts/rename_to_managerbot.py --dry-run
python scripts/rename_to_managerbot.py
```

It renames the schema (a catalogue update, not a copy), rewrites `parentbot`
to `managerbot` in every family table that names a bot, and fixes the
`settings` keys that carry the id inside them. It is safe to run twice and
leaves the text of old `family.events` rows alone — a log line saying
"ParentBot started" is what happened.

### 8b. The Railway variables, with it

Every `PBOT_*` variable is now `MBOT_*`, and the schema moved:

| was | is |
|---|---|
| `PBOT_TOKEN` | `MBOT_TOKEN` |
| `PBOT_USERNAME` | `MBOT_USERNAME` |
| `PBOT_ADMIN_ID` | `MBOT_ADMIN_ID` |
| `PBOT_*` (the tuning ones) | `MBOT_*`, same names otherwise |
| `DB_SCHEMA=parent_bot` | `DB_SCHEMA=manager_bot` |

`MBOT_USERNAME` stays `mumu_manager_bot`. **The Telegram username does not
change and neither does the token** — this is the same bot, and the whole
point of the flip was that its address was the one layer with anything
depending on it.

Order: run 8a, set the variables, then redeploy. ManagerBot is private with
one user, so the gap costs nobody anything — it is the one bot in the family
where that is true, which is also why it is the one to ship first.

### 8c. The repository

No longer pending. The `ParentBot` repository was to be renamed
`manager-bot`, to match the rule that a dashed name is private. Since 2.0.0
the bots are folders of one repository, MumuBots, and `ParentBot` is one of
the per-bot repositories that retire with the move
([CHANGES_v2.0.0.md](../changes/CHANGES_v2.0.0.md), "GitHub: 12 repositories
become 3"), so there is nothing left to rename; `PENDING_RENAMES` went from
`tests/test_naming.py` with it.

The lesson of the plan still applies to the renames that move does make:
**after renaming a repository, open every Railway service that builds from
it and confirm it still points at it.** Railway usually follows a rename;
when it does not, the service silently stops rebuilding on push, which looks
exactly like a deploy that worked. Trigger a redeploy and watch it build
before believing it -- on a morning, not the night before an unattended
deploy.

*(AnonBot's `@mumu_chat_bot` is a mismatch of the same shape and must NOT be
changed — every published inbox link embeds it. It is the one exception left
in the family. See ARCHITECTURE.md.)*

## 9. All five as one service

The one-process edition — measured idle, **77 MB** against **302 MB** for the
five services — is its own code since 2.0.0, in its own folder next to this
one: `BotFamily<version>C`, a checkout of the public `MumuCombined`
repository (`BotFamily` until the owner renames it after 2.0.0; GitHub
redirects the old name). Its `README.md` covers running it and deploying it
to Railway, and its `CHANGES_v<version>C.md` covers what it is. It is
released less often than these five, and pushed from its own folder with
git.

The two shapes use the same database, the same settings names and the same
poll lease, so moving between them is a deploy either way: each bot in the
newly started shape waits for the other shape to release that bot's lease.
Never run both on the same tokens for longer than that handover — Telegram
splits a token's updates between two pollers.

**Which one production runs decides which release reaches users.** A base
release -- this folder pushed to MumuBots -- redeploys the five per-bot
services; a C release is the C folder pushed to MumuCombined. Whichever is
not deployed changes nothing for anybody when it is published.

Both are released the same way, from this folder, at 04:00 Tashkent:

```powershell
python scripts\nightly_release.py                 # the base edition: this folder to MumuBots
python scripts\nightly_release.py --edition C     # the C edition: BotFamily<version>C to MumuCombined
python scripts\nightly_release.py --edition C --dry-run --now
```

The pause, the wait for quiet, the wait for every heartbeat to report the new
version and the resume are the same for both -- they go through the shared
database. The release has to be committed and tagged first -- `v<version>` here,
`v<version>C` in the C folder -- and either push carries all five bots, so
`--skip` is gone (it held one bot back while each had a repository of its
own). Before it pauses anybody, the script refuses a folder whose `origin` is
not its edition's repository -- this folder still on `telegram-bot-family`
included -- and, since 2026-10-09, a folder with uncommitted changes or whose
nearest tag is not the version (asked again at the window, in case it changes
during the wait). Whatever then goes wrong once the bots are paused -- an
exception, a git command that times out, Ctrl-C -- resumes them the way a
normal release does and tells the owner in `family.events`. Every git command
runs with `GIT_TERMINAL_PROMPT=0`, `GCM_INTERACTIVE=never`, no stdin and a
timeout, so a credential prompt under Task Scheduler fails at once instead of
hanging with the bots closed. `scripts\run_release.cmd`, the Task Scheduler
launcher, runs the TempTools python and exits 2 when given no arguments: the
scheduled task must say `--edition C` (production runs the C edition), because
the script's default is the base one.
`C:\.prog\.TelegramBots\scripts\run_latest_release.py` passes its arguments
through and never picks a C folder as the newest.

**What the live bots are actually showing** -- their command menus, per
language and scope, against this code -- is
`python scripts\check_live_menus.py`. Read-only, so safe against production
at any time.

## Troubleshooting

**A bot's data is going to the wrong schema.** You are on the transaction
pooler. Check the port in `DATABASE_URL`: 6543 is wrong, 5432 is right. See
section 1. Startup logs will have warned about this.

**`could not translate host name …` naming something that looks like part of
your password.** The password contains an unencoded `@`. Percent-encode it as
`%40`. See section 1.

**Authentication fails and the password is definitely right.** If it contains
a `#`, quote the whole `DATABASE_URL` value in `.env` — python-dotenv cut it
off at the `#`.

**`Network is unreachable` connecting to `db.<ref>.supabase.co`.** That is the
direct connection, which is IPv6-only. Use the session pooler host instead.

**ManagerBot says a bot is down but it's clearly running.** It is reaching a
different database. Check that the bot's `DATABASE_URL` really is the same
string ManagerBot has, and that `DB_SCHEMA` is set — a bot writing to `public`
still heartbeats fine, so this usually shows up as *wrong data* rather than a
missing bot.

**`relation "…" does not exist`.** That bot has never started against this
database. Start it once; `init_db()` creates everything it needs.

**`/run` never answers.** The target's poller isn't running. Check its logs
for `Family bus on:` at startup — if instead it says `Family bus unavailable`,
that bot cannot reach the database.

**Telegram `409 Conflict` in the logs.** The same bot is running in two
places. See the warning at the top of this document.

**pg_dump: server version mismatch.** Your local Postgres is older than the
server's. `.\db_backup.ps1 save -Portable` sidesteps it entirely.

**ManagerBot exits immediately.** `MBOT_ADMIN_ID` is empty. It refuses to
start rather than run open to anyone, which is not how the other four behave
(there, an empty admin list just disables the admin commands).
