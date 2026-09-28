# v2.0.0 — two editions, files over 20 MB, and everything since 1.0.0

A major version. The owner, 2026-09-26: *"Todays end result will be named
2.0.0 as its being hosted differently, added api hosting and some other
stuff."* It was worked on in a folder called `BotFamily1.7.1`, never
committed or released as that, and renamed to `BotFamily2.0.0` before
finalising -- the number is chosen at the end, and 1.7.1 folds into it.

What makes it a major:

1. **Two editions of every version from now on.** This folder is the *base
   version*: the five bots as five programs and five services, sharing one
   folder of modules, `shared/`, and -- once 2.0.0 is out -- one repository,
   MumuBots, instead of five. Beside it, **`BotFamily2.0.0C`** is the same
   release as one process -- its own code, arranged by function and
   squeezed, released 2 to 5 times less often, and published as
   MumuCombined, a repository of its own (BotFamily, which production still
   builds from, is left as it is until the move). The two are
   independent: nothing builds one from the other any more. See *2.0.0: the
   two editions*, *One repository* and *GitHub: 12 repositories become 3*
   below, and `BotFamily2.0.0C/CHANGES_v2.0.0C.md`.
2. **Production moved to one process** on 2026-09-25 (the `BotFamily`
   service on Railway, from another account), after the trial on the first
   account ended.
3. **Files up to 2000 MB**, over MTProto (`shared/big_files.py`): ConvertBot
   takes them and sends results over 50 MB the same way, and DownloaderBot
   fetches and sends them -- for credit, and only for somebody who switched
   that on.

**Committed and published on 2026-09-29**: MumuBots, MumuCombined and
MumuBotsHistory (*Published: three repositories*, below). Production's move
from BotFamily to MumuBots is the owner's call. `publish.ps1` is retired.

A major bump's file covers everything since the previous major, so after
this line of work (1.7.0 → 2.0.0) comes every change file since 1.0.0, in
order, each under its own heading. The backlog is at the end, as always.

---

## 1.7.1 → 2.0.0, as it happened

The owner's request, 2026-09-25:

> *"i want one service that is able to run all bots, just in case. See
> telegram-bot-family, why it can't start on Railway. Or just create a new
> public repo, with all of the source code optimized for hosting and
> spectating. Instead of making 4 separate instances, you can just make one
> big bot.py file that can run all bots. This is for emergency cases where
> I'm close to running out of credits or anything, so make sure the hardware
> usage, especially RAM is minimal, maybe by making all bots take up RAM as
> one instance. There will be updates on that repo as frequent as separate
> instances so make sure they are still easy to maintain. Clean up from any
> extra files or junk codes, unify files when possible."*

---

## Why nothing is running on Railway right now

Checked through the Railway API rather than guessed:

- **All five deployments were removed at the same moment**, 2026-09-21
  between 07:06:50 and 07:06:54 UTC. Nobody redeployed anything; the five
  stopped together.
- **The deployments' own metadata says `plan: "trial"`**, and the workspace
  reads `state: INACTIVE` with no subscription. The project was created on
  2026-08-21 -- thirty-one days before the removal, which is how long
  Railway's trial runs.
- September's usage was **$2.69**, of which **$2.56 was memory**. CPU was
  seven cents. The bill is memory and nothing else.

So the family stopped because the trial ended, not because of anything in
the code, and **no code change -- including this one -- can bring it back
until the workspace has a plan again.** That is a decision on the Railway
dashboard (Hobby, or the free plan with its smaller allowance), not here.

There is a second, smaller reason the question could be asked about
`telegram-bot-family` itself: that repository cannot be deployed as a
service at all. Its root is five programs in five folders with no entry
point, no `requirements.txt` and no build configuration, so a Railway
service pointed at it has nothing to build. It was never meant to be
deployed -- the five per-bot repositories are what the services build from
-- and the new `BotFamily` repository is the deployable form of the whole.

## The earlier decision this sits next to

`ARCHITECTURE.md` and `HOSTING.md` both record **five processes** as a
deliberate choice, and both name one process as the thing not done, for
blast radius and independent deploys. This does not reverse that: the five
services stay the default, the five per-bot repositories stay what they
deploy from, and the one-process shape is a second way to run the same code,
for when memory matters more than isolation. `HOSTING.md` had left it as
"a decision about the project, not a refactor to slip into a cost pass" --
the owner has now made it, as a fallback. Both documents are updated to say
so.

## What was built

### `unified/bot.py` -- the runner

One interpreter, one event loop, five `Application`s. Nothing in the bots
knows it is there.

- **Each bot is a package of its own** (`sticker_bot.db`, `anon_bot.db`),
  loaded by a finder that answers only for those packages. Each bot's
  modules get an `__import__` of their own through their `__builtins__`, so a
  bare `import db` inside StickerBot is still StickerBot's -- including the
  imports inside functions. `family_link` looks its siblings up with
  `importlib.import_module("i18n")`, which bypasses `__import__`; the bot's
  modules get an `importlib` that answers for the bot first. Without that
  the bus messages would have fallen back to English without a word.
- **Shared modules are stored once and loaded once per bot**, because
  `lifecycle`, `family_link` and the rest hold per-bot state in module
  globals. Each sees `__file__` as if it sat in its bot's folder, so logs and
  anything else found relative to a module stay where they were.
- **Settings that share a name** (`DB_SCHEMA`, `PRIVACY_URL`,
  `PAYMENT_PROVIDER_TOKEN_USD`, ...) were first applied only while that bot
  loads -- which missed every setting read later, and was replaced the same
  day by a per-bot view of the environment. See *One settings file for all
  five* below.
- **Five places python-telegram-bot or the bots assume they own the
  process**, each taken over explicitly:
  - `run_polling()` would take the loop and never return: it now records its
    arguments, and the runner starts all five together.
  - `stop_running()` stops the loop: here it marks that bot drained.
  - `loop.add_signal_handler()` keeps one handler per signal, so five bots
    would leave only the last one hearing SIGTERM: the runner owns the
    signals and hands each to every bot.
  - Every bot's `tune_runtime()` installs a 4-thread pool sized for itself:
    one shared pool of 8 stays (threads start only when needed).
  - Every bot records its process's memory for `/usage`: all but one
    (ManagerBot when present) now record it as not measured, so the total
    counts the process once and the memory alarm fires once.
- **Logs**: one console handler, and every line names its bot -- by logger
  name for the bots' own lines, and by the task or thread that wrote it for
  python-telegram-bot's and psycopg's. Each bot's `/logs` and log files are
  filtered to its own lines. A status line (`5/5 polling · 76 MB resident`)
  after startup and every 30 minutes; Python warnings go through the same
  log.
- **One bot failing does not take the others**: a missing token skips it
  without loading it, a refused token or a failed start is logged and left
  out, a network problem at startup is waited out instead of ending the
  process. Only when no bot can start does the process exit non-zero, so
  the host restarts it.
- `--check` loads every bot without starting any and proves the isolation;
  `--list` says which tokens are set; `BOTS=sticker,anon` or
  `python bot.py sticker anon` runs a subset.
- Runs from this folder as well: `python unified/bot.py` uses the bot
  folders beside it, so a change can be tried in one process without
  building anything.

### `scripts/build_unified.py` and the `BotFamily` repository

Builds the public `BotFamily` repository out of this one: `bots/<bot>/` with
each bot's own files, `shared/` with every byte-identical module once
(found by comparing, not from a list), one merged `requirements.txt`, one
`LICENSE`, `SECURITY.md` and `.python-version`, and `unified/`'s files at the
root. Left out: the per-bot deploy files, `db_merge.py`, the BotFather
pictures, and the README sections about running a bot from its own folder
(replaced with a pointer to the top-level README).

It refuses to publish drifted shared modules, a setting a bot's
`.env.example` sets that `unified/.env.example` does not mention, and
anything that looks like a token or a database password.

**`publish.ps1` now builds and pushes it on every release**, so it is never
edited by hand and never behind. It carries every bot, so it is held back
whenever a bot is (`-Skip`, `-Only`) unless named: `-Only BotFamily`. A
failed build is a warning, not a failed release -- `nightly_release.py`
would otherwise report a release that did ship as one that did not.

### `unified/Dockerfile` and `railway.json`

A Dockerfile rather than Nixpacks, so the same image builds on Railway,
Render, Fly.io or a plain host -- the point of an emergency option is to be
movable. `python:3.11-slim` with ffmpeg, `MALLOC_ARENA_MAX=2`,
`LOG_TO_FILES=0`, bytecode compiled at build time, a non-root user, and
`python bot.py --check` as a build step, so an image in which a bot cannot
be imported fails to build instead of failing to start.

### `tests/test_unified.py`

Builds `BotFamily` into a temporary folder, checks its shape, runs the
runner's `--check` against both the build and this working tree (stubs,
mirrored, like the rest of `run.py`), and checks the two private names the
runner reaches into still exist. **Proven to catch it**: with the `importlib`
redirection removed, four bots fail "family_link finds its own
translations"; with the per-bot `__import__` removed, a bot fails to load.
Both restored, all pass.

## Measured

Same image, idle after startup, Linux containers, a local Postgres and a
stand-in Telegram server (scratch harness, not committed):

| | VmRSS | container memory |
|---|---|---|
| five processes, one bot each | **302 MB** (57–62 each) | 225 MB |
| one process, all five | **77 MB** | 60 MB |
| one process, two bots | 64 MB | -- |
| one process, all five, `PYTHONOPTIMIZE=2` | 72 MB | 56 MB |

About **four times less**. Each extra bot in the shared process costs about
4 MB; the rest is the interpreter and libraries, paid once. At September's
rate that is roughly a quarter of the memory bill.

End to end, against that stand-in server:

- all five start, poll, and answer `/start` in their own schema -- each
  schema holds only its own user;
- ManagerBot's startup roll-call, `/status` (5/5 up) and `/logs sticker`
  over the bus, which returned StickerBot's lines and nobody else's;
- SIGTERM: all five drain, save their state and release their leases in
  under a tenth of a second, exit 0;
- a revoked token for one bot, and no token for another: the other three
  run; every token refused: exit 1;
- **the lease across the two shapes**: with a separate StickerBot still
  running, the unified process starts the other four at once and StickerBot
  waits; a SIGTERM during that wait stops cleanly; and in reverse, a
  separate StickerBot waits for the unified one to let go and then takes
  over. Moving between the two shapes on Railway is as safe as a redeploy.
- `/usage` samples: ManagerBot records 76 MB, the other four record none.

## Considered and not done

- **`PYTHONOPTIMIZE=2`**: measured at 4 MB (5%). `HOSTING.md` held it back
  for "the class of bug it invites" -- asserts and docstrings gone -- and
  4 MB does not change that. It is one variable away if ever wanted; it
  also reaches ConvertBot's worker process through the environment.
- **One connection pool for all five.** Each bot's pool is pinned to its
  schema through its connection options, which is what keeps the bots'
  identically named tables apart. Idle pools cost the database a connection
  each, not this process memory worth sharing.
- **Restarting one bot inside the process.** `/run <bot> restart` exits the
  process, which restarts all five. Rebuilding one bot's modules in place
  would leak the old ones; a whole-process restart is a few seconds.
- **A webhook or sleep mode.** Same answer as `HOSTING.md`: a different
  product.

## One settings file for all five (2026-09-25, later)

The owner, after the first half:

> *"create one env. file with all of the placeholders, marked "optional" or
> "required" if the bot hosting does or does not start without those
> variables. Again, unify duplicate vars like admin ID for all bots, because
> I am the only admin."*

### The file

`unified/.env.example` is now every setting any of the five bots reads --
159 of them, grouped by what they are about -- each tagged:

- **`[REQUIRED]`** -- nothing runs without it. Only `DATABASE_URL`: without
  it every bot tries a Postgres on localhost, fails to load, and the process
  exits.
- **`[REQUIRED for X]`** -- X is left out without it, the rest run. Each
  bot's token and username; `ADMIN_ID` for ManagerBot, which refuses to
  start without an owner.
- **`[optional]`** -- everything else, **commented out with its default**.

Commented out rather than left blank on purpose: most knobs are read as
`int(os.environ.get("X", "30"))`, and a blank `X=` in a pasted file is
`int("")` -- a crash at startup. The runner now also treats an empty value
as unset, so even an uncommented blank line means "the default".

Only twelve lines are uncommented: `DATABASE_URL`, `ADMIN_ID`, and five
token/username pairs. The build now refuses a settings file that misses any
setting the code reads (proven: delete `DBOT_INSTAFIX_HOST` and it stops),
and every name in the file was checked to be one the code actually reads.

### What was unified

- **`ADMIN_ID`** stands in for all five `*_ADMIN_ID`. A bot's own
  `SBOT_ADMIN_ID` still wins for that bot.
- **`SIBLING_BOTS`** is written from the four public usernames when not set
  -- it was the same four usernames typed a second time.
- **`POLICY_BASE_URL`** gives every bot its `PRIVACY_URL` and `TERMS_URL`
  as `<base>/<folder>/PRIVACY.md` -- one line instead of eight. None of the
  five Railway services ever had those links set.

Tokens and usernames stay one per bot -- they are different values, not
the same value repeated.

### A bug in the first half, and the class it belonged to

The first version applied per-bot settings by changing the real environment
*while each bot loaded*. That is right for everything read at import, and
wrong for anything read later -- and `PAYMENT_PROVIDER_TOKEN_<currency>` is
read when somebody runs `/donate`. So `CBOT_PAYMENT_PROVIDER_TOKEN_USD`,
which the first `.env.example` told people to set, would never have reached
ConvertBot. Card payments are frozen (`FIAT_DONATIONS=off`), so nothing was
affected, but the documentation promised something the code did not do.

Rather than special-case that one name, the fix takes the whole class:
each bot's modules now get their own `os` -- the real one, except that
`os.environ` is a view through one function, `setting()`, which holds every
rule above. The view applies at import and at every read after, a bot's
writes still go to the real environment, and libraries never see it. The
same view reads a development-tree bot's own `.env`, which the first
version dropped once the bot had loaded.

Checked: the only call-time reads in the bots today are the payment token,
`SIBLING_BOTS`, `LOG_TO_FILES`, the Railway names and `FAMILY_LABEL`; all
of them now resolve per bot.

### Proven

`--check` now also asserts, for every bot, that it reads through its own
view, sees its own `DB_SCHEMA` after loading, receives every
`<its prefix>_<NAME>` in the environment and none of another bot's
one-bot-only names, and that `ADMIN_ID` became its admin list.
`tests/test_unified.py` runs it with one settings file for all five, and
checks the file's shape. Proven against both halves: without the per-bot
`os`, four bots fail "reads settings through its own view"; without the
`ADMIN_ID` fallback, all five fail "ADMIN_ID is its admin list".

**The laptop's own environment holds real tokens.** Four bots' `*_TOKEN` and
`*_USERNAME` are set in the Windows *user* environment, so every Python
process on this machine inherits them -- including `python unified/bot.py`
run from anywhere. The tests strip them; anything else that starts a bot on
this laptop does not.

## The first deploy, and why it did not start (2026-09-25, later)

The owner filled in the required settings, deployed `BotFamily` on Railway,
and it did not start; the log export was in Downloads. Every bot failed on
the same line:

> `failed to resolve host 'aws-0-<region>.pooler.supabase.com'`

**`DATABASE_URL` was still the example from `.env.example`.** It was the one
required line that came pre-filled -- with `<ref>`, `<password>` and
`<region>` in it -- so it read as done while every other required line was
visibly empty. That was the file's fault, not the owner's. The process then
restarted eleven times in sixteen seconds and Railway gave up.

Three things made it worse than it had to be, all fixed:

- **The last line blamed the tokens.** Five bots failed to load on the
  database and the runner ended with "Nothing to run. Each bot needs its
  token" -- the one thing that was right. The end-of-startup message now
  distinguishes "no bot has a token" from "no bot could be loaded: <why>".
- **The reason was five tracebacks deep.** The settings everything depends
  on are now checked before any bot loads, and one database test is made
  first, with the answer in one sentence: an example value still in place,
  `DATABASE_URL` missing or not a `postgresql://` string, a host that does
  not exist, a password refused (with the percent-encoding hint), Supabase's
  "tenant or user not found", `ADMIN_ID` given as a username, a token with
  quotes or spaces around it. A database that merely is not answering is
  waited for (`UNIFIED_DB_WAIT_SECONDS`, 120) before giving up. Only the host
  of a connection string is ever printed, never the password.
- **Every line arrived as "info".** Railway files a line by the stream it
  came on, and everything went to stdout. Warnings and errors now go to
  stderr.

And the file: **every required line is now empty**, with the example only in
the comment above it; a test fails if any uncommented line ever carries a
value again. `@` in front of a username is also tolerated now.

Checked against the real thing without printing it: the `DATABASE_URL` in
the five local `.env` files is identical in all five and to the one the old
StickerBot service used on Railway, it is the session pooler on 5432, and the
database answers. The five `*_ADMIN_ID` values are the same number. The last
heartbeats are from 2026-09-21 07:06 UTC, so nothing else is polling the
tokens.

## ConvertBot takes files over 20 MB (2026-09-26)

The owner asked for a plan to "fix the upload limit for convert bot", then
chose from it: *"1 - B, 2 - you decide the optimal path, 3 - include step 3,
4 - lets do it later, build everything else."* B was MTProto rather than a
local Bot API server; 2 was the new ceiling; 3 was sending results over
50 MB too; 4 was whose my.telegram.org account the API id comes from.

**This changes ConvertBot's code** -- unlike the one-process work before it,
which left the five bot folders byte-identical to v1.7.0. With `CBOT_API_ID` and `CBOT_API_HASH` unset -- which
is every deployment today -- ConvertBot behaves as it did, apart from three
reworded sentences.

### The backlog item was wrong, and why that decided the route

Section 9, item 1 of the carried backlog said a local Bot API server was "a
service to stand up and an environment variable, not a code change". On
Railway it is not:

- **The limits only lift in `--local` mode** (the server's own README), and
  in that mode `getFile` returns a path on the *server's* disk. The bot's
  default `LOCAL_BOT_API_MODE=true` reads that path directly, so the two
  have to share a filesystem -- and Railway volumes cannot be shared between
  services. It would be a second process inside ConvertBot's container, or a
  separate service with a file server added in front of it.
- **Moving a bot onto one means `logOut` from Telegram's cloud**, after which
  it cannot return there for ten minutes (the Bot API docs, `logOut`).
- **While it is down the bot hears nothing at all** -- every update comes
  through it. That is `ARCHITECTURE.md`'s blast-radius argument, inside one
  bot.

MTProto for the big files alone has none of those: nothing to log out of, no
second process, updates untouched, and when it fails the bot is the bot it
was before. The cost was code instead of infrastructure. `LOCAL_BOT_API_URL`
still works for anybody self-hosting.

### What was built

- **`convert_bot/big_files.py`.** Telethon, logged in as the bot with its own
  token, `receive_updates=False` so it can never take an update from polling.
  **Each transfer runs in a child process of its own** (`python big_files.py
  <job>`, progress and outcome as JSON lines) -- first built in-process, and
  moved out on 2026-09-26 once measured: see *The big-file route, measured*
  below. The login is saved to one file in the container's temporary
  directory, private to the bot's user, so a transfer does not log in again;
  never in the database, where it would travel in every `/dbdump`. Nothing is
  started until somebody sends a big file. A failed connection refuses every
  big file for five minutes rather than making each wait out a 20-second
  timeout. Private chats only: there a message id is
  the bot's own, so the file is found by id alone -- and then checked to be
  in that chat, of the announced size, and complete.
- **In:** a file over 20 MB in a private chat is fetched over MTProto, with
  its percentage on the "Downloading..." line. Exactly 20 MB still takes the
  Bot API.
- **Out:** a result over 50 MB is sent over MTProto (`convert_runner.deliver`)
  as a document with the same dated name and caption.
- **The ceiling is 100 MB** (`CONVERT_MAX_FILE_MB`). The transport would
  carry 2000; the container is the limit. A large video's conversion has to
  finish inside `CONVERT_TIME_LIMIT_SECONDS` (600) on a small host, and 100
  MB is where a long video stops fitting -- it is also the top of an existing
  price tier. Raising it to 200 is one variable, once the real host has
  shown the timings. The old local-server default of 250 is now 100 as well,
  for the same reason.
- **Albums are capped on their total before downloading.** They were always
  priced and refused on the total, but only once every file was on disk; at
  20 MB that did not matter, at 100 it would be gigabytes fetched for a
  refusal. The rest of an over-size album is no longer downloaded.
- **In a group**, files over 20 MB are declined with a sentence pointing to a
  private chat, and no MTProto attempt is made.

### Step 3 could not simply raise the send limit to 2000 MB

`tests/test_pricing.py` holds the owner's rule that every price "at least
covers the hardware usage", and its worst case sends back everything the
send limit allows. At 2000 MB the egress alone is $0.098, and the cheapest
paid tier at the deepest bonus rate brings in $0.043 -- the rule would break
(it was already broken for the unused local-server path, which the test
deliberately switched off). So the limit is now per job:
`jobs.send_limit_for` allows **twice what the job was priced on** (input
plus estimated output), never under the Bot API's 50 MB and never over 2000.
The doubling is headroom for the output estimate being a judgement. The
pricing test now checks every tier, and the linear price past 200 MB, with
that limit -- all covered, the tightest 2.9x against the required 2x -- and
asserts that a flat 2000 MB would *not* be, so the rule cannot be quietly
undone. A result over its own limit is refused and refunded as before.

### Messages

New: `file_too_large_in_group` (CV-GROUP-LIMIT, no Report button),
`big_download_unavailable` (CV-BIG-UNAVAILABLE, with one -- the person
cannot fix a route being down), `downloading_progress`,
`download_too_slow`. Reworded: `file_too_large_convert` loses "a bigger
maximum file size is coming soon", which is now either done or not a promise
to keep making; `send_limit_note` and `job_reason_too_large` stop saying "a
bot can send" about a limit that is now per conversion. All three
languages; the Uzbek written from the meaning. `CV-TG-LIMIT`, `CV-TOO-BIG`,
`CV-NOTHING-SENDABLE` and `CV-OUTPUT-TOO-BIG` decode to what is now true,
and `problems.py` is copied to all five bots again.

### Proven

`tests/test_big_files.py`, in `run.py`, with a fake Telethon in
`sys.modules` -- 50-odd checks over the edges listed in its docstring. Each of
eleven behaviours was broken on purpose in the mirrored copy (the 20 MB edge,
the per-job limit, the chat check, the rest window, `receive_updates`, the
album cap, the 50 MB send edge, groups, the doubling, a short download, the
clamp without an API id) and the test failed every time. The real Telethon
1.45 was installed in a scratch folder to check every call and argument the
fake assumes. The whole suite, 50 entries, passes; the unified build takes
the new settings and merges the new requirements.

**Not proven: a real transfer.** That needs an API id, which is waiting on
the owner (below), and a run on TestBot rather than the laptop's `.env`,
which is production.

### Where the records changed

- `convert_bot/README.md` -- a *File size* paragraph, and the two settings.
- `convert_bot/TERMS.md` -- the refund line no longer names 50 MB; reviewed
  date moved. `PRIVACY.md` is unchanged and still true: a file only ever
  comes from Telegram, whichever protocol carries it.
- `DEPLOY.md` -- the settings table and §9's list of what to copy.
- `ARCHITECTURE.md` -- the Memory section, with why not a local server.
- `LICENSING.md` -- Telethon (MIT) and cryptg (CC0).
- `convert_bot/.env.example`, `unified/.env.example` -- the two settings,
  empty. The owner's own `unified/.env` was left alone.
- `tests/README.md`, `tests/run.py`, `tests/test_pricing.py`,
  `tests/test_jobs.py`, `tests/test_batching.py` -- the last two now clear
  `CBOT_API_ID` so a shell that has one cannot change what they test.

## Where the rest of the records changed

- `HOSTING.md` -- the one-process section now describes what exists, with
  the numbers.
- `ARCHITECTURE.md` -- the "if the credit runs short" list gains it, in
  its place.
- `DEPLOY.md` -- §9, switching the family to one service and back.
- `README.md` -- the layout and the documents list.
- `tests/README.md`, `tests/run.py` -- the new test.

## 2.0.0: the two editions (2026-09-26)

The owner, after the API id was set:

> *"I added the variables locally on unified and covnert bot. Now i want you
> to test everything locally without interrupting public bots on railway.
> Todays end result will be named 2.0.0 ... From now on, i want the version
> files to have two variations - a folder containing separate bots, and a
> folder with the same version with the unified code ... like
> BotFamily2.0.0U. I want you to do the organizing and renaming right before
> finalizing, after implementing all functional updates."*

### Tested locally, production untouched

Production was checked first, read-only: all five heartbeats current, 1.7.0,
one host (`868d1838dbc2`) -- the one-process service. So every run below used
the **TestBot token** and a **local Postgres** (Docker, `botfamily`); the
TestBot's own `token.env` points at the shared cloud database, and was
overridden rather than used. Nothing was pushed.

- **`tests/run.py`**: 50 of 50.
- **Every Postgres suite** against a local scratch database: smoke, anon
  scenarios (240), credit (78), policy (61), no-storage (15), erase, report,
  startup, functional, start flow, cancel flow, lossless, anon flow, errors,
  media -- all pass. Two failed first, and **both failed on the released 1.7.0
  as well**, so neither was today's: `anon_scenarios` counted the urgent alert
  it provokes on purpose (an undeliverable message) as a crash, and
  `report_scenarios` recorded problems without the bot having attached, so
  they were filed under "unattached" rather than "convertbot". Both were
  test faults; both tests now set up what a running bot has, and the anon one
  asserts the alert instead of ignoring it.
- **MTProto, live:** `big_files.py` logged in with the owner's API id and the
  TestBot token, and confirmed it was TestBot, not a production bot. Nothing
  was sent. **The round trip with a real file is not done yet** -- it posts
  into the owner's TestBot chat, and the owner was gaming (a notification
  could cover the game).
- **Each public bot, live** (the real `bot.py` on the TestBot token for 40 s):
  StickerBot, ConvertBot, DownloaderBot and AnonBot all reached polling with
  no errors; ConvertBot logged `Largest upload 100 MB ... over MTProto`.
- **The one-process runner, live:** `--check` passed on all five, and
  ConvertBot ran through it. After a dozen restarts in an hour Telegram
  rate-limited TestBot's command-menu updates (`RetryAfter`); the bots caught
  it and kept polling.
- Telethon and cryptg were installed into the laptop's Python so the bots run
  locally as they will in production.

### The C edition, built

`BotFamily2.0.0C` was made by a one-off generator (not kept in either tree):
every module placed in a folder by function, comments stripped, docstrings
cut to one line, blank runs collapsed -- **33,740 unique lines to 24,232**,
and a file was only written if its syntax tree, docstrings aside, equalled the
original's. `main.py` is the old runner with a module table instead of a
manifest, per-bot homes under `data/`, a real conversion in `--check`, and
policy links into one `LEGAL.md`. One `README.md` covers every bot. Its own
record, with the exact list of deliberate code changes, is
`BotFamily2.0.0C/CHANGES_v2.0.0C.md`.

### The big-file route, measured (after the owner's game)

- **Live round trip on TestBot:** 60 MB sent over MTProto to the owner's
  TestBot chat and fetched back by message id, byte-identical; the chat check
  and the size check both refused what they should; the message deleted
  again. Repeated through the child-process version with 25 MB (sent
  silently, the owner being asleep): identical.
- **Speed from the laptop: 0.3–0.5 MB/s**, against 66 MB/s from the same
  laptop to a CDN. The laptop is 260 ms from Telegram's DC2 (Amsterdam), and
  Telethon moves one chunk per round trip. Railway is in Europe, milliseconds
  from DC2; the real figure has to be read there before `CONVERT_MAX_FILE_MB`
  goes up.
- **Memory: importing Telethon costs 26.6 MB, connecting 4 MB more, and none
  of it comes back after disconnecting.** In-process, the first big file
  would have left ConvertBot 31 MB heavier for good -- 40% on top of the
  one-process edition's 77 MB idle. So each transfer now runs in a child
  process, like each conversion, and the bot never imports Telethon (tested:
  `sys.modules` has no `telethon` after every flow). A child costs about a
  second to start, against transfers of tens of seconds.
- `tests/test_big_files.py` was rewritten for it: the children import a fake
  Telethon package written by the test, which keeps "Telegram" in a JSON
  file. 61 checks; 15 deliberate breakages (including removing the kill of a
  hung child, logging in every time, and importing Telethon in the bot), each
  caught. `-S` in the "Telethon missing" probes, so the answer no longer
  depends on what this machine has installed.

### Overnight, 2026-09-26, while the owner slept

The owner, going to bed: *"After you're done you can wander around to search
for anything to fix/remove/add/combine. Anything you think is the best for the
project, both locally and on github. Also try to search for any work proposed
but was not done so far, including renaming and other decorative stuff. Do
everything that doesn't strictly require me."* And: *"Will publish 2.0.0 later,
not today."* So nothing below is committed, pushed or deployed; the GitHub
changes are repository settings only.

- **A conversion lost to a hard kill now gets its credit back.** The graceful
  stop always refunded; a container killed outright (out of memory, a host
  failure) did not -- although `TERMS.md` promises it, and a 200 MP decode is
  exactly the thing that gets a container killed. Each paid conversion now
  has a row in `conversions_in_flight` that its process stamps every 30 s; a
  row unstamped for two minutes is claimed by another process -- once, by
  flipping `recovered` -- refunded, the person told in their language
  (`CV-RESTARTED`, with a Report button), and the owner alerted. The process
  presumed dead, if it was only slow, reads the flag when its conversion ends
  and does not refund a second time. `/deletemydata` erases the rows;
  claimed rows go a day later. `ConvertBot`'s `PRIVACY.md` and the C
  edition's `LEGAL.md` name the record. Tests: five new cases in
  `test_jobs.py`, and `tests/inflight_scenarios.py` against Postgres (claim
  once, only others' stale rows, erase, prune, one recovery end to end
  against the real ledger); four deliberate breakages, each caught.
- **`scripts/nightly_release.py --edition C`** releases the C edition: the
  same pause, window, quiet, heartbeats and resume, with the push being the C
  folder (committed and tagged `v<version>C`) instead of `publish.ps1`.
  Production runs C, so without this the release script could not release
  what users run. `DEPLOY.md` §9.
- **`run_latest_release.py` would have picked the C folder** as "the newest"
  (`BotFamily2.0.0C` sorts after `BotFamily2.0.0`) and died, since a C folder
  holds no release script. It skips C folders now. (That file lives in
  `C:\.prog\.TelegramBots\scripts\`, outside every version folder.)
- **`scripts/check_live_menus.py`** answers the backlog's "nothing checks the
  menus a token is actually serving": it asks Telegram (read-only) what each
  live bot shows, per language and scope, against this code. Run against
  production: all five match, ManagerBot's stranger and owner scopes included.
- **The public READMEs had fallen behind the menus**: `/balance` and
  `/paysupport` in all four, DownloaderBot's `/settings`, AnonBot's `/export`,
  StickerBot's `/convert` pointer. Added, and `tests/test_readmes.py` (in
  `run.py`) now fails when a menu command is missing from its README.
- **`tests/.mirror/` was not in `.gitignore`** -- every test run leaves it, so
  the next commit would have picked it up.
- **A static check** (pyflakes) across all five bots and the C tree: no
  undefined names; five unused imports in ConvertBot's `bot.py`, removed.
- **`credit_scenarios.py` failed now and then** on "the bonus is dated to run
  out in 90 days": the expiry comes from the database's clock and the check
  from the laptop's, and Docker's VM ran slightly ahead. Allowed a quarter of
  an hour either way.
- **The C image builds** (`docker build`, Linux, Python 3.11): its build step
  loads all five bots and runs a real conversion; **61 MB** resident with all
  five loaded.
- **GitHub, settings only:** `ParentBot`'s description said "ParentBot" and
  now says ManagerBot; `telegram-bot-family`'s said "four bots" and now says
  five; the four public bot repositories link to their bots
  (`t.me/mumu_*_bot`) and all six public repositories have topics.

### What left this folder

- `unified/`, `scripts/build_unified.py` and `tests/test_unified.py` -- the
  runner and the build now live in the C edition as `main.py`, and nothing
  generates it.
- `publish.ps1` no longer builds or pushes `BotFamily`; left in, the next base
  release would have overwritten the C edition with a build of this one.
- `DEPLOY.md` §9, `HOSTING.md`, `ARCHITECTURE.md` and `README.md` point at the
  C folder instead.

### Also fixed on the way

- **ConvertBot's public texts had fallen behind the bot.** `PRIVACY.md` said
  conversions are "priced in Telegram Stars" (⚡ credit since 1.6.0);
  `TERMS.md` said the price is shown "before a format is chosen" (after, since
  1.7.0's quotes); `README.md` described an invoice before every paid
  conversion and listed `/donate` and `/mystars` but not `/recharge` and
  `/balance`. All corrected; `PRIVACY.md` also says a file over 20 MB still
  travels only between Telegram and the bot.
- `FAMILY_VERSION` is 2.0.0 in all five copies of `family_link.py`.

### Arguments put to the owner

- **"One bot failure can halt all others with no enough advantages."** The
  failure half is right, and is why the base edition exists. The advantage
  half is not: the one-process shape is 77 MB against 302 MB, and memory is
  the whole bill ($2.56 of September's $2.69) -- which is why production runs
  it today.
- **An LTS that is maintained by hand** means every feature is ported by hand,
  and so is every fix. Recommended: security and data-loss fixes go to C
  straight away; only features wait for the bulk update.
- **"Split the code not by bots but by functionality"** was done at the level
  of files and folders (and every shared module stored once), not by merging
  five bots' handlers into shared ones -- that would be a rewrite of about
  25,000 lines whose bugs would first be met in production, against the
  owner's first priority, "keep as much functionality as possible".
- **There is no repository called `MumuBots`.** The one-process repository is
  `BotFamily`; `MumuBots` is the title given to the my.telegram.org app.
- **There is no separate "api server" to publish.** The API id is used by
  ConvertBot's own process over MTProto; no server was built (route B).

## A card that keeps up while the person waits (2026-09-27)

The owner, with a screenshot of another bot's "Task Details": *"try to make
an updating interface while the user waits."*

- **ConvertBot's status message is a live card now.** The stage (downloading,
  converting, sending), a ten-cell bar, the time so far, the time left once
  there is something to go on (not before 3% and three seconds), and what it
  costs. It is redrawn every 3 s when something changed, and it stays one
  message: when something newer lands below it, it moves to the bottom and
  the old copy is deleted. The bar never says 100% before the file is there,
  and there is no Stop button once the result is on its way. Downloads over
  2 MB show the same card with sizes, over the Bot API (now streamed) and over
  MTProto alike. The notes at 2 and 5 minutes stay, as the owner asked for
  them; they are no longer replies to the card, which moves.
- **Where the numbers come from:** ffmpeg reports its position (`-progress`)
  against the clip's duration from ffprobe, a document reports page by page,
  an album file by file. The worker process writes a progress line as it goes
  and the bot reads them while it waits, instead of only the outcome at the
  end. With nobody listening, a conversion runs exactly the old way.
- **A hang found on the way, in two places.** The worker's stderr and the
  big-file child's were read line by line, and one line over asyncio's 64 KB
  limit made the reader die -- leaving the pipe full and the wait for the
  process waiting until the time limit. Both now read in chunks. Found by a
  test that writes 300 KB of stderr with no newline in it.
- Texts in all three languages. Tests: `tests/test_card.py` (in `run.py`),
  `tests/progress.py` with the real tools, and new cases in `test_jobs.py` and
  `test_big_files.py`; eight deliberate breakages, each caught.

## TestBot as any of the five (2026-09-27)

The owner: *"modify the test bot to be able to act as any 5 main bots, at the
same session. I imagine it work like /asConvert or /convertbot."*

- **`.\testbot\run.ps1`** with no bot now runs all five bots' real code in
  one process on TestBot's token (`testbot/switch.py`). `/asConvert`,
  `/asSticker`, `/asDownloader`, `/asAnon`, `/asManager` -- or `/convertbot`
  and the rest -- switch; `/as` says which one it is. Each bot is loaded as a
  package of its own, so its `import db` is still its own, with its own
  `testbot_<bot>` schema and bus id; all five stay running, and a switch only
  changes where the next update goes. `run.ps1 <bot>` still runs one alone.
- **One poller, so it decides where each update goes:** a button to the bot
  that drew it, a reply to the bot that wrote the message (answering an
  AnonBot message still works after switching away), a payment to the bot
  that sent the invoice, the rest to the current bot. The `/` menu and the
  profile texts belong to the token, so only the bot it starts as publishes
  them, and a switch changes the menu in the tester's own chat -- one call,
  outranking every other menu -- with `/as` added.
- **That last part is the second design, because of Telegram's rate limit.**
  The first rewrote every menu and description on every switch: a few quick
  rounds earned a 285-second wait, then 1941 seconds, during which ManagerBot
  sat under AnonBot's menu. It also waits out a limit now rather than giving
  up, skips what is already up, and `/as` says when the menu is catching up.
- **It refuses a token that is one of the real bots'**, and never reads the
  bot folders' `.env` files, apart from ConvertBot's `CBOT_API_ID` and
  `CBOT_API_HASH`.
- Tests: `tests/test_switch.py` (in `run.py`); fourteen deliberate breakages,
  each caught.

## TestBot on either database, and hosted (2026-09-27, later)

*Changed the next day: TestBot runs on this machine only -- the hosting below
was taken out again -- and keeps its own wallet. See* TestBot stays on this
machine, *further down.*

The owner, after the first version refused the shared database: *"testbot
should be able to work with both local and supabase databases."* And: *"Host
the testbot."*

- **Either database.** `TESTBOT_DATABASE_URL` is the local one or the shared
  one, and any `TESTBOT_*` set in the shell wins over `token.env`.
- **What made the shared one safe was not what anybody thought.**
  `FAMILY_BOT_ID` and the `testbot_<bot>` schemas were the documented
  separation, and they are not enough: lifecycle is handed each bot's
  `BOT_NAME` -- a constant, the real name -- and keys on it the poll lease,
  the maintenance flag, the update waitlist and the expected-restart marker,
  all in the shared family schema. On the shared database a test bot
  stopping marked the real one's restart as expected, a test bot could take
  the real one's lease while it was redeploying (which then waited four
  minutes to start), and a `/pause` sent through the test ManagerBot would
  have paused the real ConvertBot. `switch.py` now replaces every bot's
  `BOT_NAME` with its test id before its `main()` runs, and the audit checks
  it on the running bots. `run.ps1 <bot>` had the same problem on the shared
  database, one bot at a time; there it now goes through
  `switch.py --only <bot>`.
- **What it still shares** is what a person shares between the bots: the ⚡
  wallet. A conversion on TestBot spends real credit, and the test bots show
  as their own rows in the real ManagerBot's `/status`.
- **Hosting:** `testbot/Dockerfile` (built from the repository root; all five
  bots' requirements and ffmpeg, like the C image) and `testbot/railway.json`
  (`overlapSeconds: 0` -- two containers would be two pollers on one token).
  Configured by its variables alone. The build runs `switch.py --check`:
  every bot's modules import, each as its own package, and ConvertBot's
  worker converts a picture. It stops on SIGTERM the way each bot would.
  Measured in Docker: it polls as all five from environment settings alone,
  stops cleanly in under a second on `docker stop`, never writes the token
  to its log, and idles at 64 MB. **Not deployed:** the Railway account this
  machine's CLI is signed in to has been inactive since its trial ended, and
  production's account is not signed in here.

## Every command, against every menu (2026-09-27)

The owner: *"check all of the available commands and whether they are listed
as available commands properly too."*

`.\testbot\run.ps1 audit` loads all five and compares the commands each
running bot really handles with every menu it publishes, in every scope and
language, then checks what the tester's chat really shows after switching to
it. All five menus are clean: nothing listed that nothing handles, the same
list in every language. What each handles and lists
nowhere is on purpose and written down (`tests/test_menu.py`'s exceptions,
and the comment above ManagerBot's `BOT_COMMANDS`); ManagerBot's `/help` names
every one of its 39.

What it did find was in `/help` rather than the menu: **AnonBot's `/help`
never mentioned `/conversations` or `/which`**, both in its menu since 1.7.0,
while still listing `/newlink`, `/pause`, `/resume` and `/stats`, which 1.7.0
moved onto the `/link` screen. Both are in its `/help` now, in all three
languages.

Then the owner: *"/help should list all user commands."* No bot's `/help` had
ever listed the account and policy commands -- `/start`, `/balance`,
`/paysupport`, `/privacy`, `/terms`, `/deletemydata` -- nor the ones that
still work outside the menu: StickerBot's `/convert` pointer, ConvertBot's
`/mystars` and `/donate` (now written as the other names of `/balance` and
`/recharge`), AnonBot's `/archive`. All four bots' `/help` names every one,
in English, Uzbek and Russian; the Russian `/balance` line said ты where the
rest says вы, and now says вы. `test_menu.py` fails when any command a user
can send is missing from any language's `/help` -- the menu's and the
deliberately unlisted ones alike (checked: remove one line, it fails).

## TestBot stays on this machine, and is read-only on the shared database (2026-09-28)

The owner, after the hosted version above: *"i dont think we need to deploy
testbot on railway. I will use testbot only to test the new version
practically before pushing to railway. Only hosting locally is needed for
testbot."* And: *"For the supabase connection, make testbot read only. Maybe
add one extra scheme or table for testbot as an exception to improve the
testing area through testbot."*

- **Local only.** `testbot/Dockerfile`, `testbot/railway.json`, `switch.py
  --check` and its SIGTERM handling are gone, and `run.ps1` is a thin wrapper
  that sends every mode through `switch.py`. **One at a time:** a second
  TestBot on the same machine refuses to start (`testbot/.lock`, held for as
  long as the first runs), because two pollers on one token split its updates.
- **Its own wallet.** The section above ended *"what it still shares is ...
  the ⚡ wallet"*. Not any more: what the real bots keep in `family` --
  heartbeats, the bus, settings, the wallet -- TestBot keeps in
  `testbot_family`, the "one extra scheme" the owner offered. Its credit is
  test credit.
- **Stars are sandboxed by default** (`FAMILY_STARS_SANDBOX=1` unless
  `token.env` says `0`): a top-up is credited without Telegram charging
  anybody, and every message about one says so.
- **Read-only on the shared database, by the database's own rules.**
  `testbot/supabase_role.sql` makes a login of its own, `testbot`: it reads
  every table the real bots keep, including tables made later, and writes
  nowhere but its six `testbot_*` schemas; at most 24 connections, so it can
  never crowd the real bots out; any statement over a minute cancelled; and
  any `testbot_*` schema an earlier TestBot made as `postgres` is handed over.
  Before any bot loads, `switch.py` asks the database what the login it was
  given could change outside `testbot_*` -- superuser, schema, table and
  sequence privileges -- and refuses to start if the answer is anything, so
  the `postgres` login is refused with a sentence saying why. A rule kept by
  the code it protects against is as good as that code; this one is as good
  as Postgres.
  - Found on the way: `CREATE SCHEMA IF NOT EXISTS` checks the database-level
    `CREATE` privilege before it checks whether the schema exists (checked on
    Postgres 17), so without that grant every bot failed at startup on a
    schema that was already there. The login has it; what it adds is the
    right to make new schemas of its own, never to write to one it does not
    own.
- **The one-big-`bot.py` idea, and why not.** The owner proposed avoiding the
  fight over tokens with a separate file holding every feature, so that
  anything TestBot does stays local -- *"It will take a lot of effort but
  will ensure that anything done in testbot stays local only."* Argued
  against, before building: a second copy of every feature has to be kept in
  step by hand, which is the drift the shared modules exist to end, and a
  test of the copy tests the copy. What the copy was for -- keeping a test
  away from production's data -- is what the login above does, enforced by
  Postgres rather than by care. The owner then went further: *"what if
  instead of making a full copy of base bots' features separately, you make
  all base bots share specific functions. This will make future development
  and accessing fresh functions inside test bot easier."* That is `shared/`,
  below, and it is also how TestBot "borrows" the bots' functions: it loads
  their real files, so a function added to a bot or to `shared/` is in
  TestBot the next time it starts. There is nothing to copy.
- Tests: `tests/test_switch.py` grew the new cases (its own family schema,
  the sandbox, the lock, the guard -- which is also skipped for a local
  database, where there is nothing to protect); `tests/testbot_role_scenarios.py`
  runs the SQL file as written against production-shaped schemas on the local
  Postgres and checks what the login can and cannot do. Fourteen and five
  deliberate breakages, each caught.

## One repository: shared/, two .env files, docs by kind, and C (2026-09-28)

The owner: *"This will allow us to have a global folder with shared
modules/functions, both on github and railway. Make sure both MumuBots and
MumuCompressed can both be deployed on railway. There should be two types of
.env files: first type is local, one per bot, second type is global, lives in
shared folder and holds the variables in a centralized file. Build
.env.example based on this. Locals can override shared. Also make several
folders that will hold md files sorted by type for organizing files."* And:
*"i named unified version it U locally and Combined on github. Its only on
2.0.0 so lets change the postfix to C for combined, for consistency."*

- **`shared/`.** `family_link.py`, `lifecycle.py`, `live_message.py`,
  `problems.py` and `shared_features.py` -- until now a byte-identical copy in
  every bot folder, a fix copied five times -- live once, in `shared/`, with
  `big_files.py` (ConvertBot's, generalised so DownloaderBot uses it too) and
  `envfiles.py`. Each `bot.py` puts `../shared` on its path as it starts,
  after its own folder, so a module of a bot's own with the same name would
  quietly win: `tests/test_shared_copies.py` now fails if one appears, where
  it used to hold the five copies byte-identical. ManagerBot keeps
  `monitoring.py`.
- **Two `.env` files.** First match wins: the real environment (Railway's
  variables), the bot's own `.env`, then `shared/.env`. A setting only one
  bot can mean -- `DB_SCHEMA`, its bus id and label, its policy links, its
  payment tokens -- is ignored in `shared/.env`, with a warning saying where it
  belongs; an empty value is no value, so `KEY=` in a bot's file does not hide
  the shared one; `ENV_FILES=off` skips both (TestBot's settings come from
  `token.env`). The examples split the same way: `shared/.env.example` holds
  the database, the Telegram app's API id and hash, the owner's contact, the
  running-cost dials and every bot's token as optional lines; each bot's own
  holds its token, username, admin id, schema and policy links. A `.env`
  written before 2.0.0, with everything in it, works as it is. The release
  script and `scripts/check_live_menus.py` read both files as the bots do.
- **Deployable from the one repository.** Each bot folder has a `Dockerfile`
  -- built from the repository root so it can take `shared/`, ffmpeg for the
  three that shell out to it, and an import of every module at build time, so
  a bot that could not start is never deployed -- and a `railway.json`:
  that Dockerfile, rebuild only when the bot's folder or `shared/` changes,
  and the same overlap and drain as before. `nixpacks.toml` and the `Procfile`s
  are gone. All five images build. `tests/test_naming.py` holds each pair to
  its own folder (a slip would build one bot under another's service, with
  the other's token), the five deploy settings to each other, and the lease
  wait to more than the overlap plus the drain. The C edition deploys from
  its own repository as before.
- **The documents by kind:** `docs/changes/` (this file, `CHANGELOG.md`),
  `docs/design/` (`ARCHITECTURE.md`, `DESIGN.md`), `docs/operations/`
  (`DEPLOY.md`, `HOSTING.md`, `UPDATES.md`), `docs/legal/` (`LICENSING.md`),
  and `SECURITY.md` in `.github/`, where GitHub looks for it. Every relative
  link between them was checked after the move.
- **The licence at the root as well.** `shared/` is in no bot folder, so no
  bot's `LICENSE` covered it; the same AGPL text is now at the root, where it
  covers the whole repository and GitHub reads it, and the copies in the bot
  folders stay.
- **U is C.** `BotFamily2.0.0C`, `CHANGES_v2.0.0C.md`, `nightly_release.py
  --edition C`, the tag `v<version>C`, and `run_latest_release.py`, which
  still never picks the C folder as the newest.
- **`publish.ps1` is retired.** It pushed each bot folder to a repository of
  its own, and a bot folder no longer runs on its own. Run, it says so and
  where to go instead. A release is now a push, and `nightly_release.py` makes
  it: this folder to MumuBots, or the C folder to MumuCombined (or
  BotFamily, the name GitHub will redirect). Before it pauses anybody it
  refuses a folder whose `origin` is not its edition's repository, and a base
  folder whose history still begins with telegram-bot-family's -- a push
  carries every commit HEAD descends from, and the early ones hold tokens.
  `--skip` is gone: every release changes `shared/`, so every release rebuilds
  all five. ManagerBot's "too old for this command" no longer tells the owner
  to run `publish.ps1`. `tests/test_release_push.py`, against throwaway
  repositories with a bare one standing in for GitHub; nine deliberate
  breakages, each caught.

## GitHub: 12 repositories become 3 (the plan, 2026-09-28)

The owner, with a screenshot of `telegram-bot-family`: *"If grouping multiple
existing repos is possible: I want you to combine the existing public latest
bot repos into one "MumuBots". ... If its not possible to move existing repos
into a folder: we will create two repos from scratch. One will be
"MumuBotsHistory", with version history up until 2.0.0, having all the
folders in my disk by versions, and other "MumuBots", that will have the
further commit history starting from 2.0.0. Make sure nothing will have the
hardcoded token in it if things go this way. If this is done either way i will
have only three repos. ... MumuCompressed (this will be the new name for
BotFamily). ... Do everything after finalizing 2.0.0 because BotFamily is
currently being hosted on railway."* The next night the owner called it
*"BotFamily (which is legacy MumuCombined)"* and *"MumuBots and MumuCombined
will be public"*, and **MumuCombined** it is -- C for Combined, as the
folder's suffix already was.

GitHub has no folders for repositories, so it is the second way. **Nothing
below has been done on GitHub**; this is the plan, the checks already made,
and the order.

**Today** (read with `gh`, 2026-09-28), twelve of the account's repositories
are the bots':

| repository | visibility | what it is | becomes |
|---|---|---|---|
| `StickerBot`, `ConvertBot`, `DownloaderBot`, `AnonBot` | public | one commit per release of each bot folder, by `publish.ps1` | MumuBots |
| `ParentBot` | public | the same, for ManagerBot | MumuBots |
| `BotFamily` | public | the one-process edition; **production builds from it** | MumuCombined -- a new repository with BotFamily's history (2026-09-29); BotFamily goes after the move |
| `telegram-bot-family` | private | this monorepo, 104 commits, pre-0.8 tokens in them | MumuBotsHistory |
| `sticker-bot`, `convert-bot`, `downloader-bot`, `anon-bot`, `parent-bot` | private, archived | the per-bot repositories before 1.0.0 | MumuBotsHistory |

**Checked, so nothing is lost:**

- Every file in the five public per-bot repositories is byte-identical (by
  git's content hash) to one in this monorepo at `efe3dc4`, the commit 1.7.0
  was published from. Deleting them loses nothing -- and the backlog item
  *"ParentBot's repository is ahead of the monorepo"* is closed: `c321a9a` was
  superseded by the 1.7.0 publish, which came from here.
- telegram-bot-family's full history, all 104 commits and the branches, is
  in `BotFamily1.7.0\.git` on this laptop, and stays there.
- **MumuBotsHistory is built by `scripts/build_history.py`**, and was built
  and checked here (not pushed): 34 versions, 0.1.0 to 1.7.0, 4,398 files, one
  commit per version dated when it was tagged. Left out: every `.env` and
  `token.env`, database dumps, **the SQLite files of 0.1-0.3, which hold real
  people's packs, anonymous inboxes and payments**, their `-wal`/`-shm`, logs,
  Telethon sessions, each folder's own `.git`, and a coding assistant's
  worktree inside 0.4.0. 61 secrets redacted where code or notes kept them.
  Then every secret value in every `.env` on this laptop and every token the
  old sources hard-code was looked for in the result: none is there.
  `tests/test_build_history.py`; eight deliberate breakages, each caught.
  **It lives on GitHub only** -- the owner, 2026-09-29: *"MumuBotsHistory will
  only be for github because we are removing and grouping the repos."* The
  build beside the version folders was deleted that night, after a second
  build matched it commit for commit (all 35 trees, dates and messages); the
  script now builds into the temp folder, and the runbook deletes it after
  the push.
- **Three hard-coded tokens are still live**: 0.1.1's `bot.py` (TestBot's)
  and 0.3.0's `anon_bot/bot.py` and `downloader_bot/bot.py` (production
  AnonBot's and DownloaderBot's). They have only ever been in private
  repositories and on this laptop, and MumuBotsHistory has them redacted. A
  reissue in @BotFather ends the question for good, but for AnonBot and
  DownloaderBot it means changing production's variables in the same minute.
- **MumuBots would be the whole of this folder, and public.** Until now only
  the bot folders were; `docs/` -- this file's session notes, `DESIGN.md`,
  `LICENSING.md`, which said "internal" -- `tests/`, `testbot/` and `scripts/`
  would be published for the first time. Scanned: no token, password or API
  hash in any of it; the owner's contact address and Telegram id are there on
  purpose, as defaults; one Supabase project reference is named, the old
  Seoul project, paused and useless without its password.

**The order** changed the next night, when the owner moved production to
MumuBots: it is in *Production moves to MumuBots*, further down, with the
exact commands.

## ConvertBot: files up to 2000 MB, and what keeps a heavy one from hurting anybody (2026-09-28)

The owner: *"Raise the upload limit to a safe max and test converting heavy
files using the last video in my phone's gallery. It's shot UHD at 60 fps
with HDR10."* And earlier: *"will i be able to send and receive files over
50 MBs? If yes, whats the limit? i remember you setting it to 200."*

- **The limit is 2000 MB, each way** -- Telegram's own ceiling for a bot over
  MTProto (4000 MB is for Premium accounts, which a bot is not). It was 100;
  200 was only where the price tiers ended. Past 200 MB the price goes on
  rising, ⚡200 for every 100 MB begun, and `test_pricing.py` holds every size
  up to 2000 MB to covering its worst case at the deepest rate (79.5 times
  over at the top). Results over 50 MB go back over MTProto too, up to 2000.
  Without the API id and hash the limit stays the Bot API's 20 MB, as before.
- **What made that safe, rather than just bigger:**
  - **Transfers in parallel**, four parts of 512 KB at a time, both ways.
    Telethon's own choice at this size is one 128 KB part per round trip,
    which from this laptop (260 ms from Telegram's servers) was 0.3-0.5 MB/s.
  - **A stalled transfer is stopped**: nothing moved for 90 seconds
    (`BIG_FILES_STALL_SECONDS`) and it is given up, with a sentence saying
    so; and none may take longer than ten minutes or the file at 256 KB/s,
    whichever is longer.
  - **A conversion that cannot finish in time is stopped early.** After 30
    seconds the progress it reports says how long it will take; if that is
    past 1.2 times its limit (ten minutes paid, one free), it is stopped then
    -- not ten minutes later -- and refunded, with the estimate in the
    message (`CV-TOO-SLOW`, in all three languages).
  - **A stopped conversion stops everything it started.** Killing the worker
    used to leave its ffmpeg running, converting for nobody; the worker is
    now its own process group, and the whole group goes (`taskkill /T` on
    Windows, `killpg` on Linux). Found by a test, which now fails if it comes
    back.
  - **Disk**: a person's share grows with the limit to a file and what it
    becomes, 4,050 MB, and a job is still refused when the disk is short.
  - **HDR**: a phone's HDR10 or HLG video, converted to anything that is not
    HDR -- every video, GIF and picture target -- is tone-mapped to ordinary
    BT.709 colour, when ffmpeg has the filters for it. Before, it came out
    grey and flat.
- **Measured with the owner's clip**: 224 MB, 15.7 seconds, 3840x2160 at 60
  fps, HEVC, HDR10; converted on this laptop through ConvertBot's own
  conversion code.

  | to | took | came out |
  |---|---|---|
  | MP3 | 2 s | 0.2 MB |
  | GIF | 44.7 s | 18 MB |
  | MP4 | 61 s | 37.6 MB, BT.709 colour |
  | MKV | 62.7 s | |
  | WebM | 293 s | 99.2 MB -- slow, and inside the limit, so the early stop rightly let it run |

- **What it would be charged is the part to look at** (revised the same
  night -- *Prices*, below -- to MP4 ⚡36, GIF ⚡84). Then: MP3 ⚡716, MP4 ⚡2,100,
  WebM ⚡2,040, GIF ⚡4,875. The price is on the file plus the estimated
  result, and the estimates assume a video comes out about as big as it went
  in (MP4: 202 MB estimated, 37.6 made) and a GIF far bigger (1.3 GB
  estimated, 18 MB made). Correct by the pricing rule, and steep for the file
  people will most often send at this size: a phone video. In the backlog.
- Tests: new cases in `test_big_files.py`, `test_jobs.py`, `test_pricing.py`
  and `test_problems.py`, and HDR in `tests/progress.py`.

## DownloaderBot: over 50 MB, for credit, only with a yes (2026-09-28)

The owner: *"Make downloader bot be able to download and send files over 50
MB, not for free ofcourse, with a switch to allow or not allow the bot to
download those large files, with default set to off. This is for the user to
not spend their credit unwillingly. Also bot should notify that a download
this big will cost credit."*

- **Off by default, per person**, on the `/settings` screen -- a third row,
  shown only where the MTProto route is set up.
- **Off:** a link whose file is over 48 MB is answered with what it would
  cost and a button to switch large downloads on. Nothing past 48 MB is
  fetched, and nothing is charged.
- **On:** before anything is fetched, the bot says this one costs credit.
  It fetches up to 2000 MB and charges once the file is here and its size is
  known: **⚡10 for every 100 MB begun** (`DBOT_LARGE_CREDITS_PER_100MB`).
  A balance that does not cover it is charged nothing and the file deleted;
  anything past 50 MB goes out over MTProto; a send that fails gives the
  credit back; in a group, it asks for a private chat; and nothing is left
  on disk, whatever happens.
- **The price is egress, mostly**, and `test_pricing.py` holds it to at least
  3.7 times its worst case at the deepest rate, at every size to 2000 MB.
- It needs what ConvertBot's big files need: `TELEGRAM_API_ID` and
  `TELEGRAM_API_HASH` (or `DBOT_API_ID`/`_HASH`). Without them there is no
  switch, and 48 MB stays the limit.
- Six new problem codes (`DL-LARGE-*`), fifteen texts in each language, a
  `/settings` line in `/help`, and the README, `PRIVACY.md` and `TERMS.md`
  caught up. Tests: `tests/test_large_files.py`, through the real
  `_resolve_and_send` with the routes, the wallet and Telegram faked; eight
  deliberate breakages, each caught.

## Prices: cost, plus a markup that grows more slowly than the cost (2026-09-28, later)

The owner: *"Revise the pricing. I want it to be something what other software
services provide. markup and raw cost should be around equal at low raw costs,
then with the cost rising, the markup should also rise, but in a bit lower
rate, so people will hesitate less to send larger files. actually, this is just
an idea, idk if this is going to work. See some online studies or information
about this and come up with the best pricing you can."*

**What the research said.** Quantity-pricing studies find people's
willingness to pay rises with quantity at a falling rate -- a concave curve
(Iyengar & Jedidi, "A Conjoint Model of Quantity Discounts", *Marketing
Science* 31(2), 2012) -- which is the owner's shape exactly. And the service
people would compare against, CloudConvert, charges by processing time: at
least one credit per conversion, a credit a minute, $0.008-0.016 a minute
depending on how many are bought. The old prices were nowhere near either: a
table of size tiers set for what seemed fair, 20 ⚡ for 3 MB up to 450 for 200
and ⚡200 for every 100 MB after, so the owner's own 224 MB phone video to MP4
was quoted **⚡2,100** -- for about a minute of work.

**The rule** (`shared/pricing.py`): price = cost + markup, where the markup
*is* the cost up to 5 ⚡ and grows as cost^0.8 after it -- so it keeps rising,
at a falling share: 100% of the cost for small jobs, 60% at the largest file
there is. Never under 3 ⚡.

**What "cost" is.** The most a job is allowed to cost, not the typical one --
because the price is quoted before the conversion and must never be under
what it turns out to cost: its time limit on two cores and 1.5 GB, and
everything it may send back, at Railway's published rates ($20 a vCPU-month,
$10 a GB-month, $0.05 a GB out; checked 2026-09-28), valued at the deepest
rate a Star ever buys credit at (6 ⚡ in somebody's first 500 Stars). So the
owner's condition on the discounts -- that they "at least cover the hardware
usage" -- is kept by construction, and a typical job, which uses a fraction of
its limits, keeps a much larger margin than the price says.

**The limits the price is made from**, per job, since the price is now only
as good as they are:

- **time**: two minutes, plus a second for every megabyte of work (the file
  and its estimated result, times how hard the pair is to run), up to the ten
  minutes there always were -- a small job is not charged for ten minutes it
  will never use, and a job on course to miss its limit is stopped in its
  first minute and refunded, as since earlier today;
- **what it may send back**: three times its estimated result, 50 MB at least
  and 2000 at most (it was twice the input and output together).

**What it comes to:**

| job | was | now |
|---|---|---|
| the cheapest paid conversion | 20 ⚡ | 6 ⚡ |
| a 3.5 MB photo to PNG | 80 ⚡ | 6 ⚡ |
| a 20 MB song to WAV | 450 ⚡ | 29 ⚡ |
| a 100 MB video to MP4 | 900 ⚡ | 22 ⚡ |
| the owner's 224 MB clip to MP4 | 2,100 ⚡ | 36 ⚡ |
| to GIF | 4,875 ⚡ | 84 ⚡ |
| the largest anything could cost (2000 MB to GIF) | 42,075 ⚡ | 84 ⚡ |
| DownloaderBot, 100 MB | ⚡10 | ⚡7 |
| DownloaderBot, 2000 MB | ⚡200 | ⚡76 |

At the deepest rate 6 ⚡ is one Star; at the ordinary rate, three. Each price
covers at least 1.65 times the worst its job can cost (`tests/test_pricing.py`,
every pair of formats at every size from 3 to 2000 MB), a bigger file of a
kind never costs less and always costs less per megabyte, and the prices the
READMEs and terms quote are checked against what the code charges. The price
list in `/help` and on `/convert` is worked out by the same rule rather than
typed; *"Coming soon: better prices and a bigger maximum file size"* is gone
from it, since both came.

Every number is a setting: `FAMILY_PRICE_KNEE`, `_POWER`, `_MINIMUM`,
`_OVERHEAD`, `FAMILY_USD_PER_STAR` (shared/.env.example), and the ladder
already set the rate. DownloaderBot's `DBOT_LARGE_CREDITS_PER_100MB` is gone:
its large files follow the same rule over what sending them costs, by the
100 MB begun.

## An API id and hash that are wrong: found at startup, not by a user (2026-09-28, later)

The owner: *"Make sure everything works even if the telegram api id and hash
are not set properly."* Unset was already safe -- 20 MB and 48 MB, as before
big files. *Set but wrong* was not: the bot promised 2000 MB and failed the
first person who sent a big file.

Now each bot that has them logs in once, in the background, as it starts
(`big_files.verify`). If Telegram refuses them, the route is off for that
process -- ConvertBot takes 20 MB and says so in its price list, DownloaderBot
has no large-file switch and stops at 48 MB, whatever a person had set --
and the owner gets one warning through ManagerBot. A network that does not
answer is not a verdict: nothing is turned off. **Checked against Telegram
itself**, as TestBot: the real id and hash pass; one character off in the
hash, or the id off by one, and Telegram answers `ApiIdInvalidError` and the
route goes off. (A login saved from earlier keeps working even if the
settings are later made wrong -- Telegram checks the pair when logging in, not
after; a deploy starts with no saved login, so it is checked on every one.)
Tests in `test_big_files.py` and `test_large_files.py`; four deliberate
breakages, each caught. DownloaderBot now also stops any transfer in flight
when it shuts down, as ConvertBot did.

## The last look over it (2026-09-28, later)

The owner: *"revise the codebase one last time. Search for ANY
inconsistencies, command menu and commands' functionality."*

- **Every menu against every handler, live.** `run.ps1 audit` on the TestBot
  token and the local database: all five bots, every scope and language --
  **no findings**. What is handled and listed in no menu is on purpose and
  written down (`/en`, `/uz`, `/rus`; ConvertBot's `/mystars` and `/donate`;
  ManagerBot's owner tools).
- **Every text in every language.** Each key a bot uses exists in English,
  Uzbek and Russian with the same placeholders. **Fifteen of ConvertBot's texts
  were used by nothing** -- the old invoice and "paid from balance" messages,
  `/mystars`' old totals, the old price line, in all three languages -- and are
  gone.
- **A username written with an @** (`@mumu_chat_bot`) broke the base bots'
  links while the one-process edition accepted it; both now take it either
  way. That matters on the night production moves between them: AnonBot's
  inbox links are built from it.
- **ConvertBot and three others default `DB_SCHEMA` to `public`**, where the
  C edition defaults each bot to its own. Left as it is -- a local database
  of one's own is what the default is for -- but a Railway service has to set
  it, and the move below does.
- **`/idle` under-counted sleep.** Every 15-minute sample took the five-minute
  threshold off again, so a bot quiet for days read as 67% sleepable; it is
  counted once per quiet stretch now, and the same stretch reads 99%.
  ManagerBot's copy of the code (`monitoring.py`) is fixed alike, as
  `test_shared_copies.py` requires; `test_usage.py` holds it.
- Lint: one unused import, from the old price tiers. Every file in both
  editions parses as Python 3.11, what the images run.

## Sleeping, the conversion question, and the list of what is planned (2026-09-28, later)

The owner asked for the conditions under which idle bots should sleep, whether
moving heavy conversion elsewhere is legal, music downloads dropped, archives
and camera RAW worked out for the next patch, and *"a list of planned stuff"*
with OCR on it. All of it is in **[PLANNED.md](PLANNED.md)**, the new home of
the feature backlog (each version's *Not done yet* keeps that version's loose
ends). In short:

- **Sleep**: from the family's own tables, sessions are rare -- AnonBot 35 a
  month, DownloaderBot 17, StickerBot 4, ConvertBot 2 -- so: asleep after 15
  quiet minutes, never with work in flight, ManagerBot never, woken by a
  webhook. It saves about $3 a month at today's use, because memory is what an
  idle bot costs: measured on Railway, an idle bot's CPU is under a thousandth
  of a core. (That measurement also showed the usage samples store CPU time
  since start, not per window -- right for `/status`, which says so; anybody
  adding them up would be wrong by a factor of thousands.)
- **Conversion elsewhere**: through a conversion service, dropped --
  CloudConvert's terms forbid "a raw file conversion service", and GitHub's
  forbid Actions as a service. On a second machine the owner runs, legal and
  planned, low priority.
- **Music**: dropped, as the owner said.

## Production moves to MumuBots (prepared, 2026-09-28)

The owner: *"at the usual time pause the bots, do the github combination. When
done, host the bots from MumuBots instead of BotFamily (which is legacy
MumuCombined). ... MumuBots and MumuCombined will be public and history will
stay private."* And, if it could not be done in time: *"just move it to the
next day's standard update time."* The night's work did not fit before the
04:00 window of 2026-09-28, so it is **the window of 2026-09-29**.

**What it costs to run**, measured from the family's own usage table: the five
separate services held about 357 MB idle, the one process about 107 MB --
**about $3.60 a month against $1.10**, both inside the $5 the Hobby plan
includes. CPU at idle is negligible either way.

**The tool: `scripts/railway_move.py`.** Every step a command of its own,
nothing done without `--apply`, and not one secret through it: the five new
services take their tokens, usernames, admin ids and database *by reference*
to what the BotFamily service already holds (`${{BotFamily.SBOT_TOKEN}}`),
which Railway resolves on its side -- so BotFamily's service stays, stopped,
as the place those values live. Each service gets its settings directly --
its Dockerfile, rebuild only for its folder and `shared/`, the overlap and
drain -- because Railway stops reading `railway.json` on 2026-12-01 (its own
CLI said so today). Each gets `DB_SCHEMA` set, and `PRIVACY_URL` and
`TERMS_URL` pointing at its folder in MumuBots. Its read-only `status` and
dry runs were checked against the real project.

**Not done: the steps that change Railway or GitHub.** Creating the five
services was refused by the assistant's own safety check, which does not let
it change a production Railway project on its own authority -- the owner's
instruction notwithstanding -- and the GitHub steps (a public repository, a
rename, pushes) are the same kind of act. So they wait for the owner: to run
them, or to allow them.

**The runbook**, from the base folder. Its GitHub half ran on 2026-09-29
(*Published*, below), with one difference from the plan: MumuCombined is a
repository of its own, not BotFamily renamed -- the owner: *"Don't touch
BotFamily repo yet"* -- so production keeps building from BotFamily until
`stop-c`, and BotFamily is deleted, not renamed, once it no longer does. What
is left is Railway's half:

```powershell
python scripts\railway_move.py status --project MumuBots           # read-only: what is there now
python scripts\railway_move.py prepare --project MumuBots --apply  # any time before: the project and
                                                                   # five services, nothing running
# then paste each service's secrets in the dashboard -- prepare lists them

# 03:40 Tashkent (07:40 on the laptop)
python scripts\railway_move.py pause --apply

# 04:00 -- once nobody has been active for 15 minutes (scripts\release.log, or /users)
python scripts\railway_move.py stop-c --apply                      # BotFamily off, and deaf to pushes
python scripts\railway_move.py connect --project MumuBots --apply  # the five build from MumuBots
python scripts\railway_move.py wait 2.0.0                          # every heartbeat on 2.0.0
python scripts\railway_move.py resume --apply
```

**`--project MumuBots`, because of the plan** (*Railway, measured*, below):
it allows 5 services in a project, and BotFamily and five more is six. So
the five get a project of their own -- the account's second and last -- and
BotFamily stays stopped in its own, for `rollback --project MumuBots`.
References do not cross projects, so the secrets are pasted rather than
referred to; `docs/operations/DEPLOY.md` §4 lists every one a service needs.
The checkpoint is in `CHANGELOG.md`.

If the five do not come up, `python scripts\railway_move.py rollback --apply`
removes them and redeploys BotFamily's last deployment as it was -- 1.7.0,
the one process -- and the poll lease hands every token back. If Railway's
GitHub app cannot see the new MumuBots repository, `connect` fails and says
so; the dashboard's Settings -> Source adds it (or `railway up` from this
folder deploys a service without GitHub at all).

**Before deleting the eleven old repositories**, which is the owner's: check
each public bot's Privacy Policy in @BotFather (/mybots -> the bot -> Bot
Settings -> Privacy Policy); if it points into a repository about to go,
point it at the bot's `PRIVACY.md` in MumuBots. Nothing else in either
edition links to an old repository (checked; the only URLs of old
repositories left are in `tests/test_release_push.py`, as the addresses the
release must refuse). Every file of the five public per-bot repositories is
in MumuBots (checked, by content hash); telegram-bot-family's full history is
in `BotFamily1.7.0\.git` and its released states in MumuBotsHistory.

## Ready for GitHub (2026-09-29)

The owner, at three in the morning: *"I told you that the MumuBotsHistory will
only be for github because we are removing and grouping the repos. Now i have
the history folder and all of the versions at the same time. Remove either
version folders or the history folder."* And: *"I have created .env files, one
on BotFamily2.0.0\shared and one at BotFamily2.0.0C. They have all the
variables set in the second screenshot which is from railway project currently
hosting the bots. Make sure everything is ready to be published to github."*

- **The history folder went; the version folders stayed.** They are the
  source -- MumuBotsHistory is made from them, and `BotFamily1.7.0\.git` is
  the only full copy of telegram-bot-family's commits. Before deleting, a
  second build was compared with the one beside them: all 35 commits the
  same trees, dates and messages, nothing uncommitted, no remote.
  `scripts/build_history.py` now builds into the temp folder, and the runbook
  deletes it after the push.
- **Both new `.env` files are ignored by both repositories**, by git itself:
  checked against a fresh repository over this folder for MumuBots and
  against the checkout for MumuCombined, with every log, TestBot's
  `token.env` and C's `data/`. Then every secret value in every `.env` on this
  laptop -- 164 files, these two among them -- was looked for in every file
  the two repositories would hold, 232 and 20: none is there. The only
  matches were `.env.example`'s placeholder, `postgres.<ref>:<password>@...`,
  which is what the two files' `DATABASE_URL` was at first -- the C edition
  would have refused to start on it and said why (`main.py`'s check). Told,
  the owner put the real address in both within the hour; the scan was run
  again on exactly what was committed, and found nothing.
- **`ADMIN_ID`, one line for all five, is now read by the base bots too.**
  Railway's variables and the C edition had it; the five read only
  `SBOT_ADMIN_ID` and the rest, so a `shared/.env` written with Railway's
  names would leave every owner-only command, and ManagerBot itself, without
  an owner once the bots' own files were gone. `shared/envfiles.py` now fills
  a bot's `<PREFIX>_ADMIN_ID` from `ADMIN_ID` -- in either file or the
  environment -- when the bot's own is not set; `merged()`, which the release
  script reads, agrees; `ENV_FILES=off` (TestBot) applies none of it. The
  loader had no test of its own: `tests/test_envfiles.py` holds its rules,
  18 checks, four of which fail with the new lines taken out. The move sets
  each service's `<PREFIX>_ADMIN_ID` by reference regardless, so production
  never depended on this.

## Published: three repositories (2026-09-29)

The owner, after the section above: *"Do the github grouping. Create three
repos, one for history (private) and one for base and compressed each, public
repos. Ensure hosting will start with no problem from the new MumuBots repo
anytime. I will paste the env variables myself. Don't touch BotFamily repo
yet."*

- **MumuBots** (public): this folder, its history started again at `v2.0.0`.
  The `.git` it had was telegram-bot-family's, all 40 of whose refs are in
  `BotFamily1.7.0\.git` (checked, ref by ref), so it made way rather than
  moving beside the version folders.
- **MumuCombined** (public): `BotFamily2.0.0C` at `v2.0.0C`, on top of
  BotFamily's own history, so deleting BotFamily later loses none of it.
  **An unmerged branch came with it**: `claude/gifted-dirac-yvlsgy`, one
  commit of 2026-09-25 by a cloud session on BotFamily -- a cobalt route for
  YouTube ahead of yt-dlp, and a rethought Report dialog (22 files, about
  1,070 lines). None of it is in 2.0.0. It lives on in MumuCombined as that
  branch; see *Not done yet*.
- **MumuBotsHistory** (private): `scripts/build_history.py`, built in the
  temp folder, checked for secrets, pushed, and deleted.
- **BotFamily is untouched**: still production's source, still public. The C
  folder's `origin` is MumuCombined now, and BotFamily is its remote
  `botfamily`, so a push from that folder can no longer redeploy production
  by accident.
- **Hosting from MumuBots** has every service's settings and variables in
  `docs/operations/DEPLOY.md` §4, in the order they are pasted.

## Railway, measured -- and 2.0.0 cut again with what it found (2026-09-29)

The owner, after the push: *"You can host testbot on railway as a separate
instance and measure the most realistic consumption and wait times. I think
processing large files several times for testing will benefit us enough to
cover the costs for these tests. This is only a suggestion, decide if its
worth testing or not."* And: *"Ensure hosting will start with no problem
from the new MumuBots repo anytime."*

**Decided: worth it, but not as TestBot.** What decides consumption and wait
times is the image and the machine, so the real ConvertBot image was built
from MumuBots in a Railway project of its own, `mumubots-test`, and run
against video made in the container -- the same `convert_media_ffmpeg` the
bot calls, with its own time limits and prices beside each result. TestBot
itself would have needed its token pasted into Railway, which is the
owner's to do, and would have kept the account's second and last project
(below). The project was deleted afterwards; about twenty minutes of 2 vCPU
and 1 GB, some two cents.

**Then 2.0.0 was cut again.** What the test found was fixed in a folder
called `BotFamily2.0.1`, as the practice for work after a release says. The
owner: *"Can't you rollback that 2.0.0 and re commit with the fix as 2.0.0?
I don't want to jump from 1.7.0 to 2.0.1."* It could be, because the first
2.0.0 had reached nobody: production still ran 1.7.0 from BotFamily, nothing
deployed from MumuBots, and the repositories were an hour old. So the fixes
came back into this folder, `BotFamily2.0.1` went, and the one commit
of MumuBots and the one new commit of MumuCombined were made again, with
`v2.0.0` and `v2.0.0C` moved onto them (the first ones, `34b8ab3` and
`6a45525`, are gone from both). A tag that has reached somebody is still
never moved.

### What the account allows

Railway's API, for the workspace production runs in:

| | |
|---|---|
| projects | 2 |
| services in a project | 5 |
| a container | 2 vCPU, 1 GB of memory, 1 GB of disk |
| volumes | 500 MB at most |
| usage included | $5 a month |

Railway's own error, creating a sixth service: *"Free plan resource
provision limit exceeded."* So BotFamily and five services do not fit in one
project (the runbook above), and everything production does happens in
1 GB -- today all five bots in one process, after the move each bot in its
own.

### Measured

The container reports **48 cores** (an AMD EPYC 9655) while it may use 2,
and **2 TB of free disk** while its limit is 1 GB.

| on Railway, 2 vCPU, 1 GB | as first published | with the fix below |
|---|---|---|
| 1080p30, 19 MB -- production's size limit today | **killed for memory**, 945 MB, before its first frame | 4.9 s, 288 MB |
| 1080p30, 218 MB, three minutes | killed for memory | 52 s, 269 MB -- limit 600 s, ⚡35 |
| 4K60 HDR10 HEVC at 120 Mbit/s, the owner's phone's kind, 225 MB | killed for memory | **still killed**, 949 MB |

The 4K HDR case, on five seconds of it (72 MB; memory does not grow with
length, time does):

| | time | peak memory |
|---|---|---|
| 4K out, SDR, no tone map, 2 threads | 26 s | 920 MB |
| 4K out, tone-mapped, 1 thread, no lookahead | 68 s | 862 MB |
| 1080p out, tone-mapped, 2 threads | 14 s | 482 MB |
| 1080p out, tone-mapped, 1 thread | 26 s | 372 MB |

The limit is 953 MB, and the bot itself holds about 80 MB beside its
converter. So on this plan **a 4K video converts only at 1080p**; the owner's
15.7-second clip would take about 43 s that way. The time limits are not
what stops anything: every case finished well inside its limit.

**Disk**: 3,000 MB written in 2 s without complaint. The 1 GB limit was not
enforced in the time the test took, and ConvertBot's free-space check
(`CONVERT_MIN_FREE_MB`) cannot see it anyway -- it sees the host's 2 TB.

**Not measured: transfer speed over MTProto from Railway.** It needs a bot
token and the app's API id in the container, which are the owner's to paste.

### Fixed

- **ffmpeg's thread cap reached only the decoder.** `-threads 2` before `-i`
  caps decoding; the encoder (x264, x265, libvpx) and the filters size their
  pools from the cores the container reports -- 48 -- and each thread holds
  frames. `convert_utils.FFMPEG_CAP_IN` now caps the decoder and the filters
  before `-i`, and `FFMPEG_CAP_OUT` the encoder after its arguments, in both
  ways ffmpeg is started; the C edition has the same, ported.
  `tests/test_conversions.py` holds it: both command lines, both sides of
  `-i`; with the cap taken out again, two checks fail. StickerBot's video
  stickers already capped the encoder. **1.7.0 has the same bug**, and
  production runs it: any conversion of a 1080p video fails there today.
  None is recorded since the move to this account on 2026-09-25 --
  ConvertBot had two people and three messages in that time -- but the next
  one would. It reaches users with the move to MumuBots (the owner: *"Don't
  touch BotFamily repo yet"*).
- **`scripts/railway_move.py`** sent `builder: "DOCKERFILE"`, which the API
  refuses (400) -- a Dockerfile path is what makes a Dockerfile build; every
  other setting it sends was accepted, one by one. It now reads the plan's
  limits and refuses before creating anything when the five would not fit,
  saying what would; and `--project MumuBots` puts the five in a project of
  their own, with no reference to BotFamily (references do not cross
  projects), listing per service what to paste. `stop-c` and `rollback`
  still act on BotFamily where it is. `tests/test_railway_move.py`; with the
  old setting back, five checks fail.

### Checked: hosting from MumuBots

All five images built on Railway from `MukhtorovMurodbek/MumuBots@main`,
each with its own Dockerfile from the repository root, and each started and
stopped where it should with no token: *"Set SBOT_TOKEN and SBOT_USERNAME
environment variables first."* (and the same for the other four). With
variables pasted per `docs/operations/DEPLOY.md` §4, that is where they
start.

---

# Everything since 1.0.0

Each version's own change file, unedited except that every heading is one
level deeper. Every minor's file already covers the micro versions before it
(1.3.0 covers 1.2.1 to 1.2.3, and so on); 1.1.0 had no file of its own -- its
entry is in `CHANGELOG.md`'s history and `git log`.

---

<a id="v1.1.1"></a>

## Full change log — v1.1.1

Session of 2026-09-03. One request: read AnonBot's whole workflow, find what
is wrong with it, and fix it — prompted by three things the owner watched go
wrong in a single minute of their own chat.

**Baseline:** `v1.1.0` (`85ec328`).

AnonBot's `bot.py` and `db.py` were byte-identical from 1.0.2 through 1.1.0,
so everything below was equally present in what was deployed and in what had
been finalised locally.

---

### The minute that started it

At 11:21 PM the owner sent `/pause` and `/broadcast` from ParentBot,
announcing an update. At 11:22 a message arrived anyway. At 11:23 they tapped
**Reply** under it, answered the prompt, and were told *"That reply doesn't
match any conversation I have on record."* They tried again by replying to the
message itself: same answer. At 11:24 they sent it with no reply at all and
were told several conversations were open and nothing had been sent.

Three separate defects, and a fourth hiding behind the third. All four are
reproduced in `tests/anon_flow.py` and `tests/smoke.py`.

---

### 1. A guest's message could be delivered to the wrong person

#### What was observed

Reproduced while chasing (2) below, not seen in the chat — which is the worst
way for this class of bug to be found, because the person it happens to is
told it worked.

A guest mid-conversation with one person, who then taps a second inbox link
and types an answer meant for the first, had it delivered to the *second*
person as an opening message, and was answered **"Sent ✅"**.

#### Why

`handle_message` exempted exactly one message per conversation from the
"say what you are answering" rule: the guest's opening one, which genuinely
has nothing above it to reply to. The test was `follower_first_msg_at IS
NULL` on whichever conversation `anon_follower_state` currently pointed at.

That makes the exemption a property of the newest conversation rather than of
the chat. The chat can hold several live threads; the newest one being empty
says nothing about which of them the message was meant for.

This is the same wrong-recipient failure the reply rule was written for. The
comment in `handle_message` describes it happening to an *owner* and calls it
"delivered to that other conversation entirely — and be told 'Sent'". It was
closed for owners in 1.0.0 and left open on the guest's side.

#### The rule now

The exemption still exists, and still costs nothing in the ordinary case —
tap a link, type, it goes. It now also requires that there be nothing else in
this chat it could have meant: no relay row belonging to any other
conversation. With a second thread present the message is held, with a
**Send it anyway** button naming the one destination, exactly as every other
ambiguous message already was.

### 2. A guest could only ever answer their single newest conversation

#### What was observed

The 11:23 PM failure. A reply — by the Reply button, and again by swiping the
message itself — refused with "doesn't match any conversation I have on
record", while the relay row resolved that message correctly the whole time.

#### Why

`anon_follower_state` is keyed on `follower_user_id`: **one row per person for
the entire bot.** It is written by `start_new_anon_conversation`, which runs
on every link tap, and deleted by `/cancel`. The guest branch of
`handle_message` then refused any reply whose conversation was not that one
row.

So tapping *any* inbox link — the same one again, or somebody else's — ended
the guest's ability to answer everything they already had. Not after a week,
not after a redeploy: after one tap. The Reply buttons under those older
messages stayed on screen and kept failing, and the sentence they got back
blamed the age of the message, which sent them looking in the wrong place.

The owner was never held to this rule and could answer anything for as long
as the relay rows lived. Only the anonymous side was limited, and only by
state that was never meant to carry this meaning.

#### The rule now

An explicit reply names its conversation through `anon_relay`, that lookup is
authoritative, and it is the only thing consulted. `anon_follower_state` now
means what its docstring always said it meant: where an **un-replied** message
goes, and nothing more. Both sides can answer any conversation they are a
party to, for as long as the anchors last — 180 days of conversation silence.

`follow_link_started` no longer implies otherwise: it says the new
conversation is separate *and this one keeps working*.

### 3. `/pause` did nothing at all in AnonBot

#### What was observed

The 11:22 PM message, arriving after the pause. Not stuck, not late — nothing
was ever going to stop it.

#### Why

`refuse_new_work()` was defined in AnonBot's own `shared_features.py` and
never imported by `anon_bot/bot.py`. Everything around it worked:
`lifecycle.on_start` read the flag at boot, `family_link._cmd_pause` set it in
process, `lifecycle.is_paused()` would have answered correctly. No handler
asked.

Two other bots had the gate but not everywhere:

| bot | was gated | was not |
|---|---|---|
| AnonBot | — | everything |
| DownloaderBot | video downloads | Pinterest pins, Reddit image posts, Twitter photo sets |
| StickerBot | video→sticker encode | `/import` and the WhatsApp-zip import — the two longest things it does |
| ConvertBot | the conversion run | — |
| ParentBot | n/a — never a target of its own `/pause` | — |

#### Now

AnonBot asks in three places: opening a conversation from a link, and each
direction of a relay. DownloaderBot asks once at the top of `handle_link`,
which covers every platform instead of the three that happened to route
through the video path. StickerBot asks before both imports.

`tests/test_pause_gate.py` now fails if any child bot's `bot.py` stops calling
`refuse_new_work` at all. It fails on v1.1.0, naming AnonBot.

`/warn anonbot` still answers "nobody is mid-anything", and that is correct:
it reports on `lifecycle.busy()`, and nothing in AnonBot outlives a single
message. An open conversation survives a redeploy, so warning about one would
be a false alarm.

### 4. The "Replying in conversation #6, to: …" message

#### What was observed

Two complaints in one. The prompt quoted the bot's own header back at itself —
*"Replying in conversation #6, to: 📬 Conversation #6 Yo"* — and it stayed in
the chat afterwards, on top of the "Delivered ↩️" that followed it.

#### Why the message cannot simply be removed

Telegram's `ForceReply` has to hang off a real message. It is the only
mechanism that guarantees the next thing typed is aimed at one specific
conversation rather than at whichever button was tapped most recently, and
that guarantee is the whole reason several Reply prompts can be open at once
without any of them going astray.

#### What changed instead

It is now **scaffolding, and treated as such.** `anon_relay` gained an
`is_prompt` column, so the prompt is recognisable in the database rather than
in `user_data` — which matters, because the tap and the reply can land in
different processes. When the reply arrives, its conversation is read out of
that row and the prompt is then deleted, along with its row.

The result is one bot message per reply where there were two, and the one that
remains is the one that says the message actually went. Nothing is lost:
routing has already happened by the time the prompt goes.

`_quote_of` also drops everything above the blank line `relay_content`
inserts, so the quote is what the person wrote and nothing else.

### 5. Two different strangers both arrived as "Conversation #1"

`conv_number` counted per *(owner, follower)* pair, restarting at 1 for every
new person. The number is the only thing either side is ever told about a
thread, so three guests all arrived as "#1" and `/blocked` listed two separate
people both described as "#1".

Numbering is per owner now. **Existing rows are left exactly as they are** —
the owner has already read those numbers in their own chat, and renumbering
would rewrite what they saw. Because the new number comes from the owner's
highest rather than the pair's, every number issued from here on sits above
all of them and no new collision can appear.

### 6. `/blocked` published the anonymous guest's Telegram user id

The unblock buttons carried `callback_data="aq_unblockg:<follower_user_id>"`.
Callback data is part of the keyboard Telegram hands to the client, so
anything able to read a message's markup could read it — and in a bot whose
premise is that the owner never learns who is writing, that is the one field
that must not leave the server.

Buttons are addressed by conversation id now and the guest is looked up
server-side, which is how `aq_reply` and `aq_block` already worked. A blocked
guest with no conversation at all gets a line and no button rather than a
button that would have to name them.

### 7. A long message, or a long caption, was refused outright

`_compose` prepended a header with no length budget against Telegram's 4096
(text) and 1024 (caption) ceilings. A guest who filled the message box had the
whole thing rejected and was told "Couldn't deliver that message: Message is
too long", which reads as their fault.

Overlong text is split at a newline or a space, header on the first bubble and
buttons under the last. An overlong caption goes above its media as its own
bubbles, which is the shape uncaptioned media already used. Lengths are
measured after escaping, which over-counts — the safe direction: an occasional
early split costs a bubble, the other way costs the message.

### 8. Nothing limited how fast a stranger could write

An inbox link is meant to be posted publicly, and Block — which is per
Telegram account — was the only defence. There is now a per-person ceiling of
20 messages a minute and a 30-second cooldown on opening conversations, both
`ABOT_`-tunable. Held in memory rather than in the database: one process holds
the polling lease, so there is one counter to keep, and paying a round trip to
another continent per message to enforce a per-minute limit costs more than
the limit saves. A redeploy forgets it, which is the right way to fail.

### 9. Smaller things

- **Albums.** A three-photo album is three updates, and used to produce three
  headed bubbles, three Reply/Block rows and three "Sent" confirmations. The
  first part says all of it; the rest ride in underneath.
- **Edits.** `edited_message` was not requested, so editing a relayed message
  changed nothing and said nothing. It now says so. Asking Telegram for edits
  means every handler starts seeing updates where `update.message` is None —
  including `CommandHandler`, which matches on `effective_message` — so the
  edit is answered in group -1 and stopped there.
- **Blocking said it twice.** A toast *and* a message. The toast stays; the
  button now names the conversation it would unblock.
- **Donation nudges.** `maybe_donation_nudge` counts calls, and in the other
  bots one call is a whole conversion or download. Here it was one chat
  message, so a pair talking normally were pitched three messages in. It is
  now once per conversation, on the owner's first reply in it, and never to
  the guest — who did not choose this bot.
- **Empty conversations counted.** A link tap creates a row whether or not
  anybody writes. `/stats` and the ambiguity check now count only
  conversations something was actually said in.
- **Three round trips became one.** `get_routing_context` answers "where would
  an un-replied message go, how many inboxes does this person own, and is
  there anything else in this chat" in a single statement. The database is in
  `ap-northeast-2` and the services are in EU West; this was the most
  expensive thing on the hot path.
- **`reply_stale` was misleading.** It blamed the message's age. It now says
  the message is not on record and points at what to reply to instead.

### 10. Test harnesses that had rotted

Two failures predating this session, identical on v1.1.0, unrelated to
anything above and fixed on the way past:

- `tests/anon_flow.py` crashed at the third check: its fake message had no
  `to_dict()`, which `_hold_for_reply` has needed since held messages started
  being persisted.
- `tests/cancel_flow.py` had a `FakeBot` with no `edit_message_text`, so every
  menu rewritten through `live_message` looked like a menu never rewritten.

Both now pass, along with `lossless.py`'s `Status` stub, which was missing the
`chat_id` `LiveMessage.reply_to()` reads off it.

---

### Deliberately not changed

**Blocked guests are still told they are blocked.** `follow_link_blocked` and
`delivery_blocked` both say so, which teaches a determined person to make a
new account; silently accepting and dropping would hide it, at the cost of
lying to someone who is legitimately blocked. That is a policy call with a
real trade-off either way and it belongs to the owner, not to a bug fix.

**There is still no unsend.** Deleting your own message leaves the relayed
copy in place. Doing it properly means tracking every delivered copy and
racing the other person's reading of it; it is a feature, not a fix.

---

### Tests

All 23 dependency-free checks (`python tests/run.py`) and all nine
Postgres-backed scripts pass. New coverage: a guest replying into an older
conversation, a bare message with two threads open, the prompt quote, an
overlong message, the unblock button carrying no user id, per-owner
conversation numbering, `is_prompt` round-tripping, and every child bot
calling `refuse_new_work`.

---

<a id="v1.1.2"></a>

## Full change log — v1.1.2

Session of 2026-09-03, continuing straight from v1.1.1. One request: make
`/ping` faster, and make the bots faster, having noticed that `/ping` reported
27 seconds for something that plainly did not take 27 seconds.

**Baseline:** `v1.1.1` (`7701c64`).

Both halves of that turned out to be one root cause and one long-standing
misreading, and neither was where the report pointed.

---

### What the 27 seconds actually were

```
you → Telegram → ParentBot         168 ms   ±1 s
ParentBot → Telegram (ack)         216 ms
ParentBot → Supabase (queue)      1.24 s
queued → ConvertBot claimed it     983 ms
ConvertBot answering              2.00 s
Supabase → ParentBot             24.36 s        ← this
bus round trip                   27.34 s
```

`Supabase → ParentBot` is `taken_at - finished_at`: how long the answer sat
in the table before ParentBot noticed it. ConvertBot had replied in two
seconds. The remaining twenty-four were ParentBot's own `result_pump` timer,
which runs every thirty seconds — and which was only ever supposed to be the
backstop for the LISTEN/NOTIFY push that has been the real delivery mechanism
since v1.0.1.

Both ends of that ping said `polling`. So did every `/status`. So the push
path was not running anywhere, and had not been for two months.

### 1. The listener was never started. Not once, on any bot, ever.

#### Why nothing said so

`/status` reported *"NOT listening — on the 3s fallback poll"* with no error
underneath it, because `_listen_error` was empty. v1.0.5 added that error
line specifically to catch this, and it caught nothing — there had been no
failure to report. There had been no attempt.

#### The cause

```python
app.job_queue.run_once(_start_listener, when=0)
```

APScheduler drops a job whose run time has already passed by more than
`misfire_grace_time`. That defaults to **one second**, and
python-telegram-bot does not override it.

`attach()` runs inside `main()`, which stamps `when=0` *before*
`run_polling()` starts the scheduler. Between those two moments sits
`post_init`: the advisory lease, `init_waitlist_table`, `refresh_maintenance`
and `set_my_commands` — four round trips to a database in `ap-northeast-2`
from a container in EU West. Several seconds, reliably, every start.

So the job was scheduled, silently discarded, and never ran. Reproduced
directly against APScheduler:

```
post_init took 0.2s -> fired: ['plain when=0', 'misfire_grace_time=None']
post_init took 3.0s -> fired: ['misfire_grace_time=None']
post_init took 6.0s -> fired: ['misfire_grace_time=None']
```

The failure is a non-event, which is why it survived a release that was
looking straight at it. Nothing errored, nothing logged, and the fallback
poll kept the bus working — tens of times slower, with the slowness
attributable to a database that genuinely is far away.

#### The fix

`family_link.RUN_LATE = {"misfire_grace_time": None}`, passed to all thirteen
`run_once(..., when=0)` calls across the five bots. For a job that means "do
this as soon as the loop is up", running however late it is *is* the correct
semantics.

`tests/test_startup_jobs.py` fails if any immediate `run_once` loses it. On
v1.1.1 it names all thirteen.

### 2. The backstop was thirty seconds, and the backstop was in charge

ParentBot's `result_pump` and `event_pump` were scheduled at flat 30s and 60s
intervals on the assumption that LISTEN would carry the real traffic. The
child bots stopped making that assumption in v1.0.5, when `_safety_poll` was
changed to pick its interval from whether a listener is *connected* rather
than from whether one was *wanted*. ParentBot never got the same treatment.

It has it now: both pumps tick every two seconds and decide each time, so a
listener that drops puts the fast poll back within one tick and one that
recovers takes the pressure off again — neither needing a restart, and
neither depending on anybody noticing. While LISTEN is down the result path
is 2 s rather than 30.

Even with the listener fixed, this is the part that matters most. The
listener has been down for two months and the only reason anyone found out
is that a number looked wrong.

### 3. Every database read cost four round trips instead of one

This one is not about the bus. It is on the hot path of every message every
bot handles.

`db.pooled()` wraps a query in more than a query:

| | round trips |
|---|---|
| `check=ConnectionPool.check_connection` on every checkout | 1 |
| implicit `BEGIN` + the statement | 2 |
| `COMMIT` on context-manager exit | 1 |

Measured against the family's real database:

```
check + implicit txn (current)     32.4 ms
no check + implicit txn            25.5 ms
no check + autocommit               7.9 ms   ← one round trip
```

Four round trips to run one `SELECT`. Three of them exist to build a
transaction around a single statement that is already atomic, and to prove a
connection is alive that was last used half a second ago.

This is also exactly what `Supabase 976 ms` in the ping report was: not the
distance to the database, but the distance times four. One round trip
EU West ↔ `ap-northeast-2` is nearer 240 ms.

#### What changed

- **`pooled_read()`** — the same pooled connection, in autocommit, for
  statements that only read. 40 read-only functions across the five bots now
  use it: `get_user_language`, `get_routing_context`,
  `get_conversation_for_relay`, `get_delivery_context`, `get_caption_enabled`,
  `get_lossless_enabled`, `get_pack_owner`, ParentBot's whole dashboard, and
  the rest. It restores the connection's transaction mode on the way out,
  including when the body raises, and `smoke.py` asserts both.
- **`pooled()` is unchanged** and everything that writes still uses it. So
  does `run_readonly_query` behind ParentBot's `/sql`, which is the one read
  that genuinely needs a transaction: `SET TRANSACTION READ ONLY` and
  `SET LOCAL statement_timeout` are what make it safe, and neither means
  anything in autocommit.
- **The checkout check is now staleness-aware.** A connection used within the
  last 45 seconds is taken as alive; anything quieter is still proved before
  use. Plus TCP keepalives on pooled connections, so an idle one is kept
  alive rather than found dead.

Nothing about this depends on the database being far away — it is round
trips, so the saving scales with the distance instead of washing out.

### 4. `/ping` was measuring something other than what it was read as

Three changes, no new machinery:

- `ping_probe` now measures through `pooled_read`, so `Supabase …` is one
  round trip — the distance to the database, which is what it always claimed
  to be.
- The per-end line says `push (LISTEN)` or `⚠️ polling — LISTEN down`, rather
  than a quiet `polling` that had to be recognised.
- `Supabase → ParentBot` is now `answer → ParentBot collected it`, and the
  headline says **admin bus**. That number is ParentBot handing a command to
  another bot through a Postgres table and waiting for the reply. A user's
  message never touches that path — it goes to the bot they are talking to
  directly. The two were easy to conflate while the number was tens of
  seconds, and conflating them is what made a healthy family look broken.

---

### What this should look like now

- `/ping <bot>`: the collection leg goes from ~15 s average (0–30 s) to
  milliseconds, and `Supabase …` should read a few hundred ms rather than
  ~1 s.
- Every bot: one round trip per read instead of four. On a hot path that
  makes several reads before it answers, that is most of the database time
  the user was waiting on.
- Nothing about the actual work changed. AnonBot relays a message with the
  same code; it just spends less of the wait talking to Seoul.

The one number none of this touches is the distance itself. The database is
in `ap-northeast-2` and the services are in EU West, and ~240 ms is the floor
under every round trip that remains. Moving the database is the owner's call
and a hosting job — a Supabase project's region cannot be changed in place.
It is now the largest remaining cost by a wide margin, and it is the only one
left that is bought rather than fixed.

### Tests

24 dependency-free checks (`python tests/run.py`) and all nine
Postgres-backed scripts pass. New: `tests/test_startup_jobs.py`, and
`smoke.py`'s assertions that `pooled_read` is autocommit, round-trips, and
hands the connection back in transaction mode even when the body raises.

---

<a id="v1.1.3"></a>

## Full change log — v1.1.3

Session of 2026-09-03. An Instagram reel came back as a wall of yt-dlp's own
error text with a GitHub preview card under it.

**Baseline:** `v1.1.2` (`389e77f`).

---

### What happened

```
https://www.instagram.com/reel/Da3Q3XFO-eo/?igsi=...

Couldn't download that: ERROR: [Instagram] Da3Q3XFO-eo: The webpage request
was redirected to the login page. You have exceeded the rate-limit for
accessing posts anonymously. Use --cookies-from-browser or --cookies for the
authentication. See https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-
cookies-to-yt-dlp for how to manually pass cookies
```

…followed by a full-width GitHub card for the yt-dlp repository, logo and
all, because Telegram expands the first URL in a message.

Two separate things, and only one of them is fixable here.

### 1. The download itself is not a bug, and cannot be fixed in the bot

Instagram decides what to serve partly from **where** the request comes from.
Every cloud host's address sits in a published datacenter range, and
Instagram stopped serving posts to anonymous callers from those ranges. The
same link opened from a phone is public; opened from Railway it is a login
page. Nothing about the link, the code, or yt-dlp's version changes that.

There are exactly two mitigations, they can be combined, and both need
something the bot cannot supply itself:

- **Cookies** from a browser logged into a throwaway account. Free, five
  minutes, and the first thing to stop working: the session expires in days
  to weeks, and a session used from a datacenter IP is precisely the pattern
  these sites look for when deciding to restrict an account.
- **A residential or mobile proxy.** Costs money, and is the one that holds,
  because it fixes the thing being objected to instead of arguing with it.

So the code gained the wiring for both rather than a fix:

| variable | applies to |
|---|---|
| `DBOT_COOKIES_FILE`, `DBOT_PROXY` | every site — the fallback |
| `DBOT_IG_COOKIES_FILE`, `DBOT_IG_PROXY` | Instagram |
| `DBOT_YT_COOKIES_FILE`, `DBOT_YT_PROXY` | YouTube |
| `DBOT_TT_COOKIES_FILE`, `DBOT_TT_PROXY` | TikTok |

Per-site wins where set, and the split is the point: the YouTube problem
wants a Google account and the Instagram one wants an Instagram account.
Handing either site the other's cookie jar is worse than handing it none.
`DBOT_COOKIES_FILE` used to be the only one, was documented as the YouTube
setting, and was applied to every site regardless.

**Nothing is set, by the owner's decision.** Instagram stays unavailable and
now says so in a sentence. The wiring is there for whenever that changes; it
needs no release, only a restart.

### 2. What was a bug: the bot handed the user yt-dlp's documentation

`BlockedBySource` already existed for exactly this — "the site refused the
*server*, so say so in a sentence rather than showing them advice about
exporting cookies from a browser they are not using". It was matched on
`BOT_CHECK_MARKERS`, which are YouTube's wording:

```python
BOT_CHECK_MARKERS = ("sign in to confirm", "confirm you're not a bot",
                     "confirm you are not a bot")
```

Instagram says none of those. It says *"redirected to the login page"*, so it
missed the branch written for it and fell through to the raw-error path.

#### Now

`LOGIN_WALL_MARKERS` matches Instagram's wording, and `BlockedBySource`
carries which refusal it was — because the two are not the same promise:

- **`bot_check`** — a challenge that may well pass next time. "Try again in a
  while" is honest.
- **`login_required`** — will not pass on any retry. It wants a credential
  the bot does not have, and telling somebody to try again later costs them
  the try. The new sentence says the post is behind a login, that their link
  is fine, that retrying will not change it, and that other sites still work.

Error messages also send with `link_preview_options=LinkPreviewOptions(
is_disabled=True)`. A yt-dlp failure routinely quotes its own documentation
URL, and a one-line "couldn't download that" arriving as a full-width GitHub
repository reads far more like the bot having crashed than like a site
saying no.

`tests/test_refusals.py` holds the real yt-dlp strings — the reel from the
report verbatim among them — and checks that each lands in the right
category, that an ordinary 404 lands in neither, and that a URL is matched
to the right site's credentials.

---

### Tests

25 dependency-free checks (`python tests/run.py`) and all nine
Postgres-backed scripts pass.

---

<a id="v1.1.4"></a>

## Full change log — v1.1.4

Session of 2026-09-04, continuing from v1.1.3. Two requests: get the
Seoul → Frankfurt database migration unstuck, and rebuild `/ping` — "all that
work with the listener was for ping" — as efficiently and reliably as
possible.

**Baseline:** `v1.1.3` (`568b459`).

The two turned out to share a root cause: `psycopg`'s async layer, used in two
places the owner's Windows laptop cannot run, each one masking a design that
did not need it.

---

### 1. The migration tool choked on a Supabase generated column

`python parent_bot/family_db.py copy --from local --into cloud` died in the
backup step:

```
psycopg.errors.InvalidColumnReference: column "email" is a generated column
DETAIL:  Generated columns cannot be used in COPY.
```

Two bugs in `family_db.py`, both now fixed:

- **`list_tables()` swept in Supabase's own schemas.** It excluded only
  `information_schema`, so `copy` was trying to move `auth`, `storage`,
  `realtime` and `vault` between projects — platform-managed data these bots
  never touch, and which does not survive a cross-project CSV round trip.
  `SYSTEM_SCHEMAS` now lists the Supabase-managed schemas, plus a
  `supabase\_%` filter.
- **`columns_of()` returned generated columns.** `COPY tab (col, …)` on
  Postgres 15 rejects any generated column named in the list, and
  `auth.identities.email` is `GENERATED ALWAYS AS (lower(identity_data->>'email'))
  STORED` — the table it crashed on. `columns_of()` now filters
  `is_generated = 'NEVER'`. A stored generated column is recomputed on insert,
  so dropping it from both the backup and the restore is lossless.

The failed run wrote nothing to the target (it died before the restore step),
so no cleanup was needed. Re-running scoped to the family's own schemas —
`--schemas anon_bot convert_bot downloader_bot sticker_bot parent_bot family`
— is the supported path, and the default `copy` no longer does anything
surprising with the rest of the database.

### 2. The family bus had a push layer that never ran where it was written

`/ping <bot>` is the family bus's most latency-visible user: ParentBot queues
a `ping trace` command in `family.commands`, the target bot picks it up, runs
its own probe, and writes the answer back for ParentBot's result pump to
deliver.

Since v1.0.1 that pickup and delivery were meant to be instant, over
`LISTEN`/`NOTIFY`: three `pg_notify` channels (`family_cmd_<bot>`,
`family_result`, `family_event`), each with a dedicated
`psycopg.AsyncConnection` held open by an asyncio task with its own
reconnect/backoff. The poll underneath was demoted to a 30-second safety net.

That layer has never carried the bus for any meaningful stretch:

- **v1.1.2** found the job that starts the listeners had been silently
  misfire-dropped and *had not run on any bot for two months*. `/status` said
  "NOT listening" with no error, because there had been no attempt. The
  adaptive safety poll carried everything, and the only symptom was that the
  bus felt slow.
- **`psycopg.AsyncConnection` cannot run on Windows' default `ProactorEventLoop`
  at all.** On the laptop the bots are developed on, every listener throws on
  connect, logs a warning, backs off 2 → 4 → … → 60 s, and floods the
  console. The bus silently falls back to the poll.

For an admin bus with one human operator typing `/ping` now and then, a held
connection per bot and ~200 lines of the most comment-heavy, most bug-prone
code in the project bought a few seconds on a manual command — when it
worked, which was on Railway and not on the laptop.

#### What replaced it: one adaptive poll

`family_link.py` lost `_listen_forever`, `listen_for`, `stop_listening`,
`is_listening`, `listen_error`, `command_channel`, `notify`, the three channel
constants, `LISTEN_ENABLED`, and every `pg_notify`. `parent_bot/db.py` lost
its copy of the channel prefix and the `pg_notify` in `queue_command`.
`lifecycle.py` lost the `stop_listening()` shutdown hook. ParentBot's `main()`
lost the listener-bootstrap jobs.

In their place, the poll that was always the real mechanism, made properly
adaptive. One `_bus_tick` fires every `FAMILY_BUS_POLL_ACTIVE_SECONDS` (1) and
decides each time whether to actually query:

- **while the bus is active** — `bus_is_active()` — every tick, so ~1 s;
- **once it has gone quiet** — every `FAMILY_BUS_POLL_IDLE_SECONDS` (10).

`mark_bus_active()` opens a `FAMILY_BUS_ACTIVE_WINDOW_SECONDS` (20) fast
window and is called wherever bus traffic is imminent: `db.queue_command()`
(one choke point for every `/ping`, `/run`, `/pause`, …), a non-empty command
drain, and ParentBot's result and event pumps after a delivery. ParentBot's
two pumps use the same `bus_is_active()` switch instead of the old
`is_listening()` one — `RESULT_POLL_FAST/SECONDS` `1`/`20`,
`EVENT_POLL_FAST/SECONDS` `2`/`30`.

| | v1.0.0 | v1.0.1–v1.1.3 (as intended) | v1.1.4 |
|---|---|---|---|
| command queue poll | every 3 s | every 30 s + LISTEN push | 1 s busy / 10 s idle |
| ParentBot result pump | every 3 s | every 30 s + LISTEN push | 1 s busy / 20 s idle |
| ParentBot event pump | every 20 s | every 60 s + LISTEN push | 2 s busy / 30 s idle |
| held connections | 0 | 1 per bot + 1 | 0 |
| queries/day (bus) | ~177,000 | ~19,000 | ~50,000 |
| `/ping` pickup latency | 0–3 s | single-digit ms | ~1 s busy, ≤10 s cold |
| works on the dev laptop | yes | no | yes |

The trade, stated plainly: the first `/ping` or `/run` after a long quiet
spell now waits up to the idle interval instead of answering in
milliseconds; every command after it in that session is back to ~1 s. In
exchange the bus has one mechanism instead of two, no connection-type
dependency, nothing that can be "down" short of the database itself, and it
behaves the same on Windows and on Railway. `/status` no longer carries a
"family bus: NOT listening" line, because there is no listener to be down.

#### `/ping` report

`_describe_end` drops the `push (LISTEN)` / `⚠️ polling — LISTEN down` line
per end — every end polls now, so the line carried no information. The rest of
the report is unchanged: the leg-by-leg Postgres-clock breakdown, each end's
own distance from Supabase and its clock skew, and the "admin bus" framing
from v1.1.2.

### Config

New (all optional, all with the defaults above):
`FAMILY_BUS_POLL_ACTIVE_SECONDS`, `FAMILY_BUS_POLL_IDLE_SECONDS`,
`FAMILY_BUS_ACTIVE_WINDOW_SECONDS`.

Gone: `FAMILY_LISTEN`, `FAMILY_COMMAND_POLL_SECONDS`,
`FAMILY_COMMAND_IDLE_POLL_SECONDS`. `PBOT_RESULT_POLL_SECONDS` /
`PBOT_EVENT_POLL_SECONDS` and their `_FAST_` counterparts keep their names,
with quicker defaults. `.env.example` (×5), `DEPLOY.md`, `HOSTING.md`,
`ARCHITECTURE.md` and `tests/README.md` updated to match.

### Tests

`python tests/run.py` — all 25 checks pass. `test_ping.py` fixtures lost their
`listen` keys (nothing asserted on the removed line). `test_startup_jobs.py`
is unchanged and still passes: `RUN_LATE` stays defined, and the two surviving
immediate `run_once(when=0)` calls — ParentBot's startup result and event
sweeps — still pass `job_kwargs=RUN_LATE`.

`family_link.py` and `lifecycle.py` remain byte-identical across all five bot
folders (`md5sum */family_link.py`, `md5sum */lifecycle.py`).

---

<a id="v1.2.0"></a>

## Full change log — v1.2.0

Session of 2026-09-04, continuing from v1.1.4. The owner had paused the old
Supabase project in Seoul and asked for it to be backed up if that had not
happened yet, so the project could be deleted and one Supabase account could
hold both the bot family's database and a separate app's.

**Baseline:** `v1.1.4` (`68f828b`).

This version does not add a feature. It closes the database move that v1.1.1
through v1.1.4 were mostly about, and gives it the version number the owner
judged it deserved: a different server under all five bots is a minor bump,
not a patch.

---

### 1. Where the family's data actually is now

| | before | after |
|---|---|---|
| Supabase project | `evnxcezdrwrrdiiailns` | `qtbxrzvtxrfrmblelcpu` |
| region | `ap-northeast-2` (Seoul) | `eu-central-1` (Frankfurt) |
| distance to the containers | ~240 ms round trip | same continent as Railway's EU West |
| Postgres | 15 | 17.6 |

The five Railway services were already pointed at Frankfurt and redeployed at
the end of the v1.1.4 session; `family.heartbeats` shows all five checking in
from there. What was left over — and what this version is — was the paperwork:
the laptop's own `parent_bot\.env` still had `DATABASE_URL` on Seoul, the code
comments still argued from a 240 ms round trip, and nothing in the repository
recorded how to do the move or that it had been verified.

### 2. The old database, backed up and checked

Seoul is paused, and a paused Supabase project is off the network — the
pooler answers with `FATAL: (ENOTFOUND) tenant/user postgres.evnxcezdrwrrdiiailns
not found`. So no new backup could be taken, and none was needed: the cutover
backup already existed, taken at **2026-09-03 14:38:54 UTC** by
`family_db.py copy`, scoped to the six schemas the family owns.

It is kept twice in `backups/` — under the name the tool gave it,
`_copy_2026-09-03_143854.zip`, and as `seoul_final_2026-09-03T143854Z.zip`,
which says what it is without needing this file. `backups/` is gitignored:
it is real user data and has never been committed. There is also a fresh
`frankfurt_after_migration.zip` taken during this session, so the current
state has a snapshot of its own.

**Verified row for row before recommending deletion.** Every row in the Seoul
backup was looked up by primary key in Frankfurt. 36 tables, and the only
absences were 13 rows in three tables, all of them state the family
deliberately consumes:

| table | absent | why that is correct |
|---|---|---|
| `family.commands` | 8 | `pause`/`warnbusy` rows from 2026-09-02, already `done` and `delivered`, since pruned by the retention job |
| `family.settings` | 4 | the `maintenance:<bot>` flags, lifted when the bots were resumed after the move |
| `family.update_waitlist` | 1 | the owner's own row, held during the pause and messaged when the bots came back |

Nothing a user would notice is missing. The write gap between the backup and
the bots restarting on Frankfurt (14:38 → 15:33 UTC) is empty of real traffic
too: the bots were paused for it, and the first row on the new database is a
heartbeat.

**Deleting the Seoul project is safe from the bots' side.** Nothing in the
repository or on Railway points at it any more.

### 3. What changed in the code

Nothing that runs. Three comments that had gone from true to false, and one
`.env`:

- **`parent_bot\.env`** — `DATABASE_URL` moved from Seoul to Frankfurt, so the
  laptop stops talking to a database that no longer answers.
  `CLOUD_DATABASE_URL` is now the same URL, because there is one database
  again; `family_db.py`'s `local` and `cloud` aliases both resolve, and
  `db_backup.ps1` keeps working. The retired project's ref and the name of its
  final backup are written into the comment above it, so the next person to
  read that file does not have to come here.
- **`db.py` (×5)** — the pool's idle-check skip was justified by "this bot's
  database is in ap-northeast-2 and the container is in EU West, so one round
  trip is a quarter of a second". Half of that sentence stopped being true.
  The optimisation did not: the check is a whole extra round trip per read
  whatever the distance, so the ratio survives the move even though the
  absolute cost drops by an order of magnitude. The comment now says that.
- **`family_link.py` (×5)** — `/ping`'s "four trips to Seoul reported as
  though they were one" is now "four trips to the database", and the 250 ms it
  quotes is in the past tense. Still byte-identical across all five bots, as
  is `lifecycle.py`.

`FAMILY_VERSION` is `1.2.0` in all five copies.

### 4. What changed in the docs

- **`DEPLOY.md` §1** — "Pick the region nearest you" is now "pick the region
  nearest **the containers**, not nearest you", with this family's own mistake
  as the worked example. The bots run on Railway, not on the owner's desk, and
  a year of pool tuning, idle-check skipping and bus-poll adapting exists
  largely because that distance was 240 ms.
- **`DEPLOY.md` §2** — a new subsection, *Moving to a different Supabase
  project*, written from the move that actually happened rather than from
  first principles. Eight steps: build the schemas on the new project by
  starting each bot once, pause the family, copy with `--schemas` naming the
  six the family owns, repoint all five `.env` files *and* all five Railway
  services, confirm the heartbeats moved, resume, and only then retire the old
  project — checking first, because the handful of rows that legitimately do
  not carry over are worth being able to recognise.

### Tests

`python tests/run.py` — all 25 checks pass, unchanged.

### Not done here

The Seoul Supabase project itself still has to be deleted from the Supabase
dashboard; that is not something the repository can do. Everything that had to
be true before that is safe now is true.

---

<a id="v1.3.0"></a>

## v1.3.0 — everything since 1.2.0

A minor release, so this file covers the whole line rather than the last
step: `1.2.0 → 1.2.1 → 1.2.2 → 1.2.2R → 1.2.3 → 1.3.0`. That is the rule in
`CHANGELOG.md`'s versioning policy — a patch's notes are read by whoever is
about to deploy it, a minor's by somebody asking what changed since they last
looked.

`1.2.0` was the database move to `eu-central-1`. Everything below happened
after it.

---

### Where the line went

**1.2.1 — DownloaderBot stopped asking from here.** Instagram and the rest
decide what to serve partly from *where* the request comes from, and a
datacenter address gets a login page where a phone gets the post. The bot
stopped having one way to fetch a link and grew `resolvers.py`: a chain of
independent providers per platform, tried in order, remembering which ones
work, resting the ones that fail, and putting the route that goes out from
the container's own address last.

**1.2.2 — asking from where the answer counts.** 1.2.1 was verified from a
residential connection, which is the exact thing these sites treat
differently. `/probe` moved onto the family bus so the same question could be
asked from inside the container that actually serves downloads.

**1.2.2R — AnonBot gone over end to end.** A retouch: no feature, one test
harness, and the bugs it found. Detailed below.

**1.2.3 — a command centre, an ask that runs out, a bot to test on.**
Detailed below.

**1.3.0 — naming, frozen payments, and public documentation.** This release.

---

### 1. The bug underneath the other bugs (1.2.2R)

`python-telegram-bot` runs **at most one handler per group**. Every bot
opened its `main()` with a `TypeHandler(Update, track_activity)` in group
`-1`, which matches every update there is — so it won that group every time
and **nothing else registered in it had ever run.** No error, no warning.

Three things were unreachable:

- **AnonBot's `edited_message`.** An edited message arrives with
  `update.message is None`. The handler that existed to answer those never
  ran, so every edit fell through to `handle_message` and raised
  `AttributeError`. The user got nothing; the owner got a crash report.
  Editing a message is an ordinary thing to do.
- **`donate_custom_amount_received`, in all four public bots.** The "Custom"
  donation amount was never picked up. In StickerBot the number was read as a
  pack title; in AnonBot it was relayed to a stranger.

Fixed by giving each its own group, and by correcting the docstring that had
described the group semantics wrongly in the first place.

### 2. AnonBot's routing (1.2.2R)

- **An album arrived one photo at a time.** Three photos share a
  `media_group_id` and are three updates. The first spent the conversation's
  one exemption from the reply rule, or deleted the Reply prompt the others
  were anchored to, so photos two and three were refused. Albums are now
  routed by their first part, once.
- **A returning guest could not open a second inbox.** The test for "is this
  the opening message" asked whether the chat had ever held another thread —
  true forever, for anybody who had used the bot before. Replaced with an
  order-aware test: the exemption holds while the conversation just opened is
  still the newest thing in the chat. The opening line also carries a forced
  reply now, so the ordinary path is routed like every other message and does
  not depend on the exemption at all.
- **The guest's own messages are tracked**, so an edit can be answered and a
  guest can quote themselves. Written in the same single statement that
  already recorded the owner's side — one round trip where the reply path
  previously took three.

### 3. What one person may cost (1.2.2R)

Telegram rate-limits what a bot **sends** and nothing about what it
**receives**, so one script could keep a container and the shared database
busy indefinitely.

- **A per-account ceiling on updates per minute**, in every public bot,
  checked before any handler runs so a refused update costs a dictionary
  lookup rather than a database round trip. It answers once and then goes
  quiet — a gate that replies to every refused message turns an incoming
  flood into an outgoing one. The owner is exempt.
- **DownloaderBot: a share of the day.** A rolling hourly and daily
  allowance per person, in the database so a redeploy does not reset it,
  counted when a download *starts* rather than when it succeeds. Donors get
  the ceiling multiplied. One download slot per person, so two global slots
  cannot both go to one user.
- **Forged callback data** (`aq_reply:notanumber`) raised `ValueError` and
  reported a crash, which let anyone page the owner at will. Parsed
  defensively.

### 4. The deploy gap (1.2.2R)

`overlapSeconds: 0` meant Railway stopped the old container and *then* built
the new one, so the bot was away for the whole build — commonly one to three
minutes. That setting predated the Postgres advisory lease, which is the real
guarantee against two pollers.

`overlapSeconds: 30` lets the old container keep serving while the new one
builds and boots; the new one blocks on the lease before its first
`getUpdates`. **The gap becomes the drain rather than the build.**
`DEPLOY_LEASE_WAIT_SECONDS` moved 45 → 240 with it — the two are one
decision, and the wait must outlast the overlap plus the drain or two
containers end up polling one token.

`UPDATES.md` also gained the release-timing policy and a full design for a
standby service that would replace the remaining silence with a sentence.
That is designed, not built.

### 5. ParentBot as a command centre (1.2.3, extended in 1.3.0)

Every owner-only command any bot has is reachable from ParentBot. Three were
not: ConvertBot's `/stars`, DownloaderBot's `/providers`, and `crashtest`,
which existed only inside StickerBot.

`crashtest` raises through `application.create_task` rather than in the bus
handler — a bus handler that raises is caught and turned into a failed
result, which never reaches the error handler and would therefore test
nothing. Naming a bot is the useful direction: what is worth knowing when a
bot goes quiet is whether *that* bot would have reported it.

**`/ping` grew buttons.** A bare `/ping` reported four numbers; getting the
breakdown for the one that looked wrong meant four more commands. Now each
row has a button, plus "All details", rendered from the ping that just ran —
a second round trip would produce different numbers and invite the question
of which is real.

**Version gaps now read as deploys, not faults (1.3.0).** ParentBot is
private, so it ships first and is routinely a version or two ahead of the
bots it is asking. `COMMAND_SINCE` records which release each bus command
arrived in; a command a bot is too old to know about is refused *before* the
round trip, naming both versions and what to publish. The version comparison
tolerates `1.2.2R` and `test`, and anything it cannot parse never blocks.

### 6. The donation ask (1.2.3)

It was "20 actions **or** 14 days", floored at 3 days. The time half fired on
people barely using the bot and never stopped — a user of three years would
have been asked around seventy times.

Now **15 actions, then 60 more, then 200 more, then never**, and never at all
for anyone who has already donated, checked in the same statement that bumps
the counter. Someone asked three times across 275 successful actions has
answered the question.

Four preset tiers instead of three: **15 / 50 / 150 / 500 ⭐**. The bottom
one exists so that saying thank you is possible; the top one is an anchor as
much as an option.

Pinning the nudge in the chat is implemented and **off** (`DONATION_PIN`).
A pin is a permanent claim on the top of somebody's chat for something
explicitly voluntary, it posts a service message of its own, and with the ask
now capped at three times ever there is little left for it to solve.

### 7. Real-currency donations, frozen (1.3.0)

The USD column is complete and **does nothing**. `FIAT_DONATIONS` defaults to
`off`, checked at `_fiat_provider_token()` — the single point every fiat path
already goes through, so the freeze needed no other line changed.

It was already inert without a provider token. The flag exists because "does
nothing because a variable happens to be empty" is one accidental deploy
variable away from a half-enabled payment path in front of a real user.
Unfreezing needs `FIAT_DONATIONS=on` **and** a provider token — both,
deliberately.

Kept rather than deleted because connecting a provider is a decision about
payouts and a country, not about this code, and the day it is made the work
should already be done. The recommendation meanwhile is Stars: no provider,
no merchant account, no country restriction, and the rail Telegram expects
for anything digital.

### 8. Naming (1.3.0)

Seven places name a bot. All seven now derive mechanically from one canonical
`<Role>Bot`, and `tests/test_naming.py` asserts it against the source so a
sixth bot cannot be added half-named. The table is in `ARCHITECTURE.md`.

**A dashed repository name means private**; `PascalCase` means public.

**One deliberate exception: the Telegram `@username`.** It is an address, not
a label. AnonBot is `@mumu_chat_bot`, and every inbox link anybody has posted
embeds it — changing it would not rename the bot, it would break every link
permanently with no redirect. It is frozen for as long as the bot has users.
So `USERNAME_ALIASES` records it instead, and ParentBot resolves any form:
`/logs anon`, `/logs chat`, `/logs anonbot`, `/logs @mumu_chat_bot`.

Two renames are recorded as pending in `DEPLOY.md` and deliberately not done
in this release: ParentBot's `@mumu_manager_bot` (free — private, no saved
links) and its GitHub repository `ParentBot → parent-bot` (has to be paired
with reconnecting the Railway service that builds from it, so it belongs on a
morning rather than the night before an unattended deploy).

### 9. The published documentation (1.3.0)

The four public bots' READMEs are what the world sees, and they were written
as one side of a conversation — second person throughout, narrating what had
changed release by release, and referring to a monorepo the reader cannot
open. Rewritten as product documentation: third person, present tense, no
change narration, and self-contained. What changed and when belongs here, not
there.

### 10. Testing (1.2.2R, 1.2.3)

**`tests/sim.py`** is a Telegram server inside the test process: a real
`telegram.Bot` with its one network seam redirected, keeping chats, message
ids counted *per chat*, reply anchors and inline keyboards the way the real
service does. The bot's own `main()` builds the real `Application`; what
comes out is what somebody's chat would contain.

**`tests/anon_scenarios.py`** is 122 checks on top of it, groups A–M — both
reply routes, three people at once with two prompts answered out of order,
one guest writing to two inboxes, media and albums, block/unblock, edits,
deleted anchors, both flood ceilings, hostile input. It also asserts that
nothing the error handler was asked to report happened at all, which is how
§1 was found: PTB catches handler exceptions, so an `AttributeError` deep
inside one is otherwise invisible.

**`tests/lossless.py` had been failing since 1.2.1** — it called two
functions removed in that rewrite, so it died on its first case and the five
that still worked were never reached. Rewritten against the code that exists.

**`testbot/`** runs any of the four bots' real code on a spare Telegram
token, labelled `TestBot` on the family bus so it cannot overwrite a real
bot's heartbeat, with one line to fill in. As of 1.3.0 it points at the
shared database rather than a local one.

### 11. Housekeeping (1.2.2R, 1.2.3)

- **One changelog per version folder**, appended to as work lands,
  aggregating at a bigger version shift. Written into the versioning policy.
- **`CHANGELOG.md` 126 KB → ~10 KB**: policy, the current versions in full,
  everything older as one line each. The full text is in the older folders
  and in `git log -p`.
- **`backups/` starts empty in each new version** rather than inheriting
  every dump that came before.
- Dead code removed: an orphaned i18n key, a superseded query signature, and
  twelve stale `CHANGES_*` files.

---

### Files changed in 1.3.0 itself

| file | what changed |
|---|---|
| `*/shared_features.py` (×4) | `FIAT_DONATIONS`, the freeze at `_fiat_provider_token`, a second check before any invoice. Byte-identical |
| `*/family_link.py` (×5) | `broadcast --active`, `BROADCAST_ACTIVE_DAYS`, `FAMILY_VERSION` → `1.3.0`. Byte-identical |
| `*/db.py` (×5) | `active_user_ids_since` |
| `parent_bot/bot.py` | `USERNAME_ALIASES`, `COMMAND_SINCE`, `_version_key`, `_too_old_for`, the pre-flight check in `_dispatch`, version context on an unknown command |
| `parent_bot/db.py` | `heartbeat_of` |
| `*/README.md` (×5) | rewritten as public documentation |
| `ARCHITECTURE.md` | the naming table and the username exception |
| `DEPLOY.md` | the two pending renames, with the Railway step spelled out |
| `tests/test_naming.py` | new; wired into `tests/run.py` |
| `testbot/token.env` | points at the shared database |

### Tests

| suite | result |
|---|---|
| `tests/run.py` | 27/27 |
| `tests/anon_scenarios.py` | 122/122 |
| `tests/smoke.py` · `startup.py` · `functional.py` | 0 failures |
| `start_flow.py` · `cancel_flow.py` · `anon_flow.py` | 0 failures |
| `lossless.py` · `errors.py` | 0 failures |

`tests/media.py` needs `ffmpeg` and was not run.

### Not done, deliberately

- **The standby service** (UPDATES.md §5) is designed and not built.
- **The two renames** above.
- **`DONATION_STEPS` and the download allowances are judgements, not
  measurements.** The data to check them against now exists.
- **ParentBot has no `sim.py`-driven test.** Its flows go through the command
  queue and a second bot answering, which is a bigger fixture than any of
  these releases needed.

---

<a id="v1.3.1h"></a>

## v1.3.1H — the inbox link is a link again

**H for hotfix.** One user-visible change in one bot, cut straight from
`1.3.0` rather than from the `1.4.0` work in progress, so nothing unfinished
travels with it. A micro step, so this file covers only `1.3.0 → 1.3.1H`.

---

### What was wrong

AnonBot's `/link` printed the inbox link inside a `<code>` block:

```
Your anonymous-inbox link (accepting messages):
<code>https://t.me/mumu_chat_bot?start=q_…</code>

Tap it to copy, then post it anywhere -- …
```

Telegram renders a `<code>` entity as grey monospace, and monospace is the
one thing on the screen that does not look like something you can act on.
The link is the whole point of the message — it is what the owner is meant
to take away and post somewhere — and it arrived looking like a fragment of
a config file. It was also unshareable in the ordinary way: a code span
carries no URL entity, so Telegram's own share and forward routes had
nothing to offer, and anybody who received the message forwarded got text
rather than a link.

### What it does now

The URL is sent as plain text, which Telegram auto-detects and renders as a
tappable, forwardable, blue link. Both messages that carry one changed —
`/link` (and its Pause / Resume / New-link buttons, which redraw the same
message) and `/newlink`.

Two things had to move with it:

**The instruction was describing the old entity.** "Tap it to copy" is what
a code block does; tapping a link opens it. All three languages now say to
copy it and post it, which is true of both platforms and of both ways of
copying. Nothing else in the message changed.

- en — *Copy it and post it anywhere …*
- uz — *Uni nusxalab, istalgan joyga joylashtiring …*
- ru — *Скопируйте её и разместите где угодно …*

**A link gets a preview card.** Telegram expands the first URL in a message,
so a two-line message with two buttons would have grown a full-width card
advertising the bot the owner is already talking to. `anon_bot/bot.py` now
carries the same `NO_PREVIEW` constant DownloaderBot has had since `1.2.1`,
on all four sends and edits that can contain a link.

### The one behaviour that changed for the worse, and why it is fine

Tapping the link no longer copies it; it opens the bot. For the owner, that
is their own inbox link, which AnonBot has always answered with
`follow_link_own` — *"That's your own inbox link — messaging yourself would
just be a note to self! Share it with other people instead: /link"*. So the
worst case is one friendly sentence, not a broken conversation, and it was
already written.

### Checks

`python tests/run.py` — 27 checks, all pass.
`python tests/anon_scenarios.py` — 122 checks, all pass, including group A's
*"/link hands over a t.me link"*, which drives the real handler through
`tests/sim.py` and reads what the chat would actually contain.

`FAMILY_VERSION` moved to `1.3.1H` in all five copies of `family_link.py`;
the four shared files are still byte-identical across the bots.
ParentBot's `_version_key` already sorts a suffixed release after the bare
number, as it does for `1.2.2R`.

---

<a id="v1.4.0"></a>

## v1.4.0 — everything since 1.3.0

A minor release, so this file covers the whole line since the previous minor.
`1.3.0` was the last one, so the line is short: `1.3.0 → 1.4.0`.

`1.3.0` was naming, frozen real-currency donations, and the public READMEs.
Everything below happened after that tag was cut.

---

### Where the line went

**Shipping without being awake for it.** `scripts/nightly_release.py` runs the
whole sequence in `UPDATES.md` unattended — pause the four public bots twenty
minutes ahead, wait for 04:00 Tashkent, refuse to publish until nobody
non-admin has been active for fifteen minutes, publish, wait for every
heartbeat to report the new version, resume. Detailed below.

**Saying what the bots hold.** `/privacy`, `/terms` and `/deletemydata` in all
four public bots, in all three languages, with a long-form `PRIVACY.md` and
`TERMS.md` per bot for Telegram's own privacy-policy field. Detailed below.

**An audit of what a user can make the bots do.** Every SQL statement, every
message built with a parse mode, every filename and every callback datum.
Eleven defects, four of them in the commands just added, and three tests that
turned out not to be testing. Detailed below.

**A look.** Five logos, drawn from geometry and rendered to SVG and PNG by a
script. The two profile screens Telegram shows before anybody presses Start,
which had never been set at all. A layout for long messages, so `/privacy`
stops being a wall. Detailed below.

**AnonBot stops keeping what people write.** The privacy notice shipped in
§2 said message content was never stored; a "Send it anyway" button was
storing it. The button is gone, ForceReply with it, and a suite now holds the
notice to its word with persistence switched on. Detailed below.

**AnonBot gets a view of its own inbox.** `/conversations` lists the owner's
threads, goes to where any one of them starts, and archives the ones they are
done with; `/which` says which conversation any message in the chat belongs
to. The conversation number an owner uses to tell strangers apart stops being
shown to the strangers. Detailed below.

**A sweep for dead code and duplication.** One user-facing bug — AnonBot
answering with a raw translation key during every redeploy — six dead
functions, a dead parameter, and a fifth copy of the shared code that the
byte-identity check could not see. Detailed below.

---

### 1. The unattended release (2026-09-05)

`scripts/nightly_release.py`. The sequence `UPDATES.md` describes was a list
of steps somebody had to be awake to perform, at four in the morning, in the
right order. It is now one command.

```
python scripts/nightly_release.py            # the real thing
python scripts/nightly_release.py --dry-run  # says what it would do
python scripts/nightly_release.py --now      # skip the wait, keep the checks
```

It reads the current versions from `family.heartbeats` and stops if everything
is already on the target. It pauses the four public bots by queueing `pause`
rows in `family.commands` — the same rows ParentBot's `/pause` writes, so
anyone who turns up is told an update is coming and is written down for
`/finishupdates` afterwards. It waits for the window, then waits for quiet.

**It aborts rather than guesses.** Somebody still active an hour into the
window means the bots are resumed and nothing is published. A failing
`publish.ps1` means the same. Either way the outcome goes into `family.events`,
which ParentBot forwards to the owner as a Telegram message — the script holds
no bot token and should not.

Admin ids are excluded from every activity count, because a release has been
held back before on the strength of an "active user" who turned out to be the
owner testing their own bot.

### 2. What the bots hold, said out loud (2026-09-07)

#### The gap

Every one of the four public bots held personal data from the first message
and said nothing about it anywhere. A Telegram user id is the only thing a bot
can address a person by, so it is taken before any consent could be asked
for — which is a reason to explain it afterwards, not a reason to leave it
unexplained. Telegram asks every bot for a privacy policy and gives BotFather
a field for the link; the field was empty because there was nothing to put in
it.

Most of a compliance checklist written for websites does not survive contact
with a Telegram bot. There is no browser, so there is no cookie banner, no
consent dialogue, no tracking pixel, no third-party embed, no colour contrast
and no keyboard trap. What does survive is the part underneath all of that:
what is held, who else sees it, how long it stays, and how to get rid of it.

#### Three commands

| command | what it answers |
|---|---|
| `/privacy` | What this bot keeps, who else sees it, how long it stays |
| `/terms` | What the bot may be used for, and where the money stands |
| `/deletemydata` | Erases it, after one confirmation |

All three are in the command menu, translated into all three languages, and
registered outside every `ConversationHandler` — somebody who wants to know
what is held on them, or wants it gone, should not have to finish building a
sticker pack first.

`shared_features.py` owns the shape and the handlers, so the four bots cannot
drift apart on the machinery. Each bot's `i18n.py` owns the four keys where
the truth genuinely differs: `privacy_stored`, `privacy_others`,
`terms_specific` and `delete_data_consequences`. A policy that describes "the
bot" in the abstract describes none of them accurately — StickerBot holds
packs, AnonBot holds who is talking to whom, DownloaderBot hands a link to
somebody else's server.

#### The third command is what makes the first two mean anything

`/deletemydata` asks once, then erases in that moment. No account, no form, no
waiting period. `db.erase_user()` clears that bot's own tables and
`lifecycle.forget_user()` clears the persisted in-progress state, which
`db.py` cannot reach because the table name and the shape of a conversation
key are known only to `lifecycle.py`.

The confirmation says what erasing costs, per bot, because none of it is
undoable and two of them are surprising:

- **StickerBot** forgets the packs made through it — it stops listing them and
  can no longer add to them — but the packs themselves live on Telegram and
  keep working for everyone who installed them. Only their owner can delete
  one, from Telegram.
- **AnonBot** invalidates the inbox link, permanently, including every copy
  anybody has already posted, and open conversations stop being routable.
  Messages already sent are untouched: they were never stored here.
- **DownloaderBot** resets the daily allowance along with the download rows.
  That is the right trade — the allowance exists so one person cannot make the
  bot unaffordable in a day, not to hold behaviour against anybody for weeks.
- **ConvertBot** loses nothing but settings. Files were deleted when their
  conversion ended.

#### The one thing that is kept

The Stars ledger. A payment record has to outlive the payer asking to be
forgotten: it is what a refund is issued against and what the totals are
counted from, and Telegram's refund call needs the numeric id to send it to.
So the row stays and the username — the one free-text identifier on it — is
cleared. Both notices say so, rather than implying the erase is total.

#### AnonBot says what "anonymous" means

The word was doing work the code does not do. Anonymity here is towards the
recipient: they are never shown a sender's name, username or id, and no
command or owner-only view reveals one. It is not anonymity from the operator,
whose database stores the owner↔sender pairing, because that pairing is
exactly what makes a reply reach the right thread.

What the operator cannot read is a single word anybody wrote. Messages are
relayed and never stored — only message ids, mapped to conversations. Both
halves of that are now in `/privacy` and in `PRIVACY.md`, in that order,
because the second half is what makes the first half tolerable.

#### The long forms

`PRIVACY.md` and `TERMS.md` in each of the four bot folders. `publish.ps1`
picks them up with no change — it publishes what `git ls-files` tracks — which
gives each one a URL on the public repository, which is what BotFather's
Privacy Policy field and the new `PRIVACY_URL` / `TERMS_URL` variables want.

They cover what the in-bot text has no room for: a retention table with the
real numbers and the environment variables that set them, why each item is
held at all, the age position, and per bot the copyright question — building
sticker packs out of somebody else's artwork, converting a file there is no
right to convert, and what downloading a public post does and does not make
yours.

**One line in each is deliberately unfinished.** The operator's name and
contact address is an outward-facing detail that should not be invented, so
each file carries a marked placeholder and `OPERATOR_CONTACT` is empty. Until
it is filled in, both notices simply end a line short.

#### Retention: already done

The obvious next move was to add pruning, so that "we keep a timestamped
record of every time you used this, forever" would stop being true. It was
not true: `db.prune_old_data()` has been running on `family_link.py`'s
housekeeping job for several versions, expiring `activity_events` after 90
days and DownloaderBot's `download_events` after 7. A second pruner was
written during this session and removed again on finding the first.

The numbers are now stated publicly, which is the change: 90 days is generous
for a table nothing reads past 7 (`BROADCAST_ACTIVE_DAYS`), and lowering
`ACTIVITY_RETENTION_DAYS` is a one-variable decision the owner can now make
against a published number rather than an undocumented one.

### 3. The audit (2026-09-07)

A pass over what a user can actually make the bots do: every SQL statement,
every message the bots build, every filename and every callback datum that
crosses the wire. Eleven defects — four in the code section 2 had just added,
four from trusting callback data, three from trusting a filename — plus three
in the tests themselves.

#### Nothing a user sends reaches a SQL statement

The rule the family already followed is that values travel as `%s` parameters
and only identifiers the code chose itself are ever interpolated. That was
true everywhere, including sticker pack names and titles, which was the one
specifically asked about: `add_pack` parameterises them, and `slugify` had
already reduced a pack's *short name* to `[a-z0-9_]` with a leading letter
before Telegram ever sees it.

Trusting that is not the same as knowing it, so `tests/test_sql_safety.py`
now proves it. It parses every bot and classifies the statement argument of
every `execute` / `executemany`: a literal, an f-string whose every hole is an
ALL-CAPS module constant, a name bound from a literal tuple, or a name
assigned from one of those. Anything else is reported and has to be listed in
`ALLOWED` **with a reason** -- the same shape as `test_menu.py`'s
`UNADVERTISED`, so "it is in the exception list" is not an argument.

Six files are listed: the four offline `db_merge.py` tools and
`parent_bot/family_db.py`, whose identifiers come from the database catalog or
from a backup manifest the owner chose to restore, and `parent_bot/db.py`,
where `/sql` runs arbitrary SQL on purpose. What makes that one acceptable is
asserted separately: it is behind `guard()`, it runs inside `SET TRANSACTION
READ ONLY`, and it carries a statement timeout.

The test was checked against a planted bug -- a `'{user_id}'` quoted into a
DELETE -- and fails on it. A test of this kind that has never been shown to
fail is a decoration.

#### Nothing a user sends reaches an HTML message

Every message built with `parse_mode` was checked for an unescaped
interpolation. There were none. AnonBot in particular was already careful in
the place it matters most: `italicize()` escapes before wrapping, so a guest
typing `<b>hi</b>` sees `<b>hi</b>` arrive, and `split_to_fit` measures the
*escaped* length so a full message box is not rejected for being too long
after escaping.

#### A filename could decide where a file was written

Two bots build a path with an extension in it, and neither extension is
theirs. ConvertBot's upload extension comes from `document.file_name`, which
the sending client chooses; its output extension comes from callback data,
which a modified client can put anything into. DownloaderBot's comes from
whatever a resolver service or a remote URL said the file was called -- not
even the user's, a third party's.

`Path(dir) / f"{uuid}.{ext}"` treats a `/` in `ext` as a directory separator
and a `..` as a level up, so a `file_name` of `a.b/../../../x` decided where
the download landed. Both now reduce an extension to ASCII alphanumerics or
refuse it, and `tests/test_untrusted_names.py` runs the shapes that mattered
-- traversal, a null byte, a space, a semicolon, four hundred characters --
against both, and checks that ordinary names still work.

DownloaderBot's `_ext_of` had a second bug next to the first: `rpartition(".")`
returns the **whole string** when there is no separator, so a dotless "video"
came back as the extension "video". That reached `_kind_of`, which found it in
neither the video nor the audio list and called it a photo -- and a clip sent
as a photo is a Telegram error, not a slightly wrong label.

#### Callback data is not the button's

The bug behind four of the eleven. Telegram sends back whatever the client puts
in `callback_data`; a modified client can send anything at all for a button
that exists. Four handlers took it on trust:

| handler | what an ordinary phone could do |
|---|---|
| `convert_format_chosen` | ask for a format nobody was offered, which reached the output path and ffmpeg |
| `donate_amount_chosen` | `donate:abc` -> `ValueError`; `donate:99999999999` -> an invoice for an amount no tier offers, which is what `MAX_DONATION_STARS` exists to stop |
| `donate_fiat_amount_chosen` | `donatefiat:x` -> `ValueError` on the unpack; an unknown currency -> `KeyError` |
| `donate_custom_button_chosen` | an unknown currency -> `KeyError` |

Each unhandled exception is a crash report in the owner's Telegram chat, so
this was a way for anybody to fill that channel from a phone. All four now
check what they are given against what the bot actually offered -- the
formats from `TARGETS`, the amounts from `DONATE_STAR_OPTIONS` and each
currency's own `options`, the currencies from `_available_fiat_currencies()`
-- and answer with the invalid-amount sentence instead of raising. StickerBot's
pack callbacks were already right: every one of them checks ownership before
acting.

#### /deletemydata, four fixes

The command section 2 added, taken apart:

**It reported failure after succeeding.** `lifecycle.forget_user` raised
`UndefinedTable` whenever `runtime_state` was absent, which is the normal
state of a bot running with `DEPLOY_SAFETY=off`. The bot's own tables are
cleared first, so the person was told "couldn't erase that just now" with
their data already gone -- the worst answer available for an action with no
undo. `forget_user` now returns 0 when the table is not there, and the two
erasures are reported on separately: the bot's own data is one transaction and
is what the answer is about; a cache of in-progress work that expires within
the day is not allowed to contradict it.

**It could raise on a second tap.** Two confirmations on screen is ordinary,
and the second answer is word for word the first when there is nothing left to
erase -- which is `BadRequest: message is not modified`. Every edit now goes
through `live_message.edit_in_place`, which the rest of this file already
used and which never raises.

**It left StickerBot's state machine holding an erased pack.** A
`ConversationHandler` keeps its state in memory, so erasing mid-session left
EDITING pointing at a pack the bot had just been told to forget, and the next
sticker raised `KeyError('owner_id')` -- paging the owner about a crash that
was really somebody exercising their right to be forgotten. The confirmation
now returns `ConversationHandler.END` and is registered as an entry point, the
same way the language switches already were and for the same reason.

**Its SQL had never run.** Four `erase_user` implementations, none of them
ever executed. `tests/erase_scenarios.py` runs all four against a real
Postgres, in a throwaway schema built by each bot's own `init_db()`.

#### Two of the tests were not testing

Found while writing the above, and worth more than either.

**`anon_scenarios.py`'s "nothing crashed" check could not fail.** The suite
installs an event hook to collect crashes at import -- and every `fresh()`
runs the bot's `main()`, which runs `family_link.attach()`, which installs its
own hook over the top. The collector had been disconnected since the first
scenario of every run. The hook is now re-installed after each build, and an
error handler is registered alongside it so that two independent routes have
to fail before a raised exception passes as a green tick. Verified by removing
one of the fixes above and watching the suite go red.

The other three suites' checks were unaffected -- they assert on what is in a
chat, not on the collector -- and with a working collector all 122 still pass,
so nothing had been hiding behind it.

**Live messages were being re-sent instead of edited.** `live_message`'s
watermark is module state; a new `FakeTelegram` restarts message ids at 1 but
the watermark kept the previous group's highest id, so every live message in a
new scenario group looked stale and was re-sent. Cleared in `fresh()` now.
`sim.Person.buttons(message_id)` also raised `KeyError` on a message whose
keyboard had been taken away, which is exactly the assertion anybody would
write for "is that menu gone yet"; it answers `[]`.

#### What was checked and found already right

- Sticker pack names and titles: parameterised, and slugified before Telegram.
- `subprocess` is called with an argument list and never `shell=True`, in both
  bots that shell out to ffmpeg.
- StickerBot's pack callbacks check ownership.
- ParentBot escapes every user-supplied string it puts in an HTML message,
  including a stranger's username in the alert about them.
- Retention pruning already existed and already runs.
- A deep link survives the language picker, for a first-time visitor and for
  an erased one alike -- `pending_start_args` carries it across.

### 4. How any of it looks (2026-09-07)

Sections 2 and 3 made the bots correct. This one is about the fact that a bot
is almost entirely text and a 34-pixel circle, and that neither had ever been
designed.

#### Five logos

`scripts/make_logos.py` writes `logo.svg` and `logo.png` into each bot's
folder. The geometry is declared once and rendered twice, because an SVG is
the thing worth keeping — it is text and it diffs — and a PNG is the thing
Telegram accepts. Drawing them separately would guarantee they drift.

There is no drawing library. The PNG rasteriser is fifty lines of scanline
fill run at 4× and box-filtered down, which is where the edges come from. A
generator that only runs where Pillow happens to be installed is a generator
that stops running.

The five marks, and what each one is:

| bot | ground | the glyph |
|---|---|---|
| StickerBot | amber | a square with one corner turned back |
| ConvertBot | violet | two arrows passing each other |
| DownloaderBot | teal | an arrow arriving on a line |
| AnonBot | slate | a speech bubble with nobody in it |
| ParentBot | near-black | one in the middle, four around it |

**Everything is original geometry** — circles, rectangles, triangles and thick
lines, placed by hand. No traced artwork, no icon set, and no font, which is
also why no glyph is a letter. The bots' own `TERMS.md` tells people not to
build packs out of other people's work; shipping a logo lifted from an icon
pack while saying that is not a thing to do once.

#### What a test can say about a logo

Not whether it is any good. What `tests/test_logos.py` can catch is the two
ways one stops working, both invisible on the canvas it is drawn on:

- **The circular crop.** Telegram shows every avatar as a circle, so anything
  past the inscribed circle is not there.
- **Chat-list size.** The same image is drawn at about 34 pixels. A stroke
  under 30 units at full size is a smudge at that one.

It also checks contrast, that no two bots share a ground colour, and that the
committed files still match the generator.

It earned its place immediately by failing three times on the first run:

- StickerBot's white glyph sat at **2.1:1** on amber-500 — the softest thing
  in the family, and visible as softness once it was pointed out. The ground
  moved to amber-600, which is 3.2:1 and still amber.
- The peel was barely darker than the sticker. It is a third tone now.
- ParentBot's ring was **20 units** and turned to mush in a chat list. 32.

The contrast rule was wrong once, too, and was fixed rather than waived:
StickerBot's peel is a *fold*, and a fold is a shadow on the surface it folds
from. Held to the 3:1 that ink is held to, it stops reading as a fold and
starts reading as a hole cut in the sticker. Shading has its own floor.

#### The bots had no profile at all

Three pieces of profile text exist and the family was setting one of them.
`set_my_commands` fills the slash menu, which only helps somebody already in
the chat. The other two are what a stranger meets first — the one-line blurb
beside the bot in search results, and the 512 characters an empty chat shows
above the Start button under "What can this bot do?" — and both were **blank**.

`publish_profile()` sets them at startup in all three languages, plus the
English text a second time with no language tag, which is what Telegram falls
back to for a client in a fourth language. It never raises: a rate limit on a
cosmetic call is not a reason to fail a deploy.

The text is held to the rule the notices are written under. `test_policies.py`
now fails the build on "unlimited", "instant", "secure", "encrypted",
"guaranteed", "24/7" and "best" appearing in a profile, because that text is
read by people who have not started the bot and cannot check.

#### A layout, instead of a column of sentences

Telegram gives a bot four things to design with: bold, a monospace run, an
expandable blockquote, and blank lines. That is the whole palette and it is
enough. The family was using none of it, and `/privacy` — about two thousand
characters — was four thumb-flicks of unbroken grey.

There is a small set of helpers in `shared_features.py` now: `heading`,
`body`, `lead_in`, `collapsed`, `command_list`, `joined`. The rules they
encode are that a long block goes behind an expandable blockquote so the shape
of the message is visible without scrolling, and that a `/command` belongs in
a monospace run because it is a thing to type rather than a word in a
sentence.

`lead_in` is the one worth naming. Several strings already open with a short
label and a colon — "Money:", "Pul haqida:", "О деньгах:" — in all three
languages, because that is how somebody writes a paragraph that answers a
question. Promoting those to bold gives a wall of paragraphs the headings it
already implied, in every language at once, without a translator having had to
think about markup.

**Everything interpolated is escaped**, which is not decoration: a stray `<`
in a translated string would otherwise take the whole message down with "can't
parse entities", and that failure reaches the user as silence.

#### The two smaller things

**327 double hyphens.** ` -- ` was standing in for an em dash in every
user-facing string in the family. It is a source-code convention and it had
leaked onto the screen; inside the `STRINGS` tables it is now ` — `. The
comments and the code keep the old convention, which is what it is for.

**A sentence that had stopped being true.** StickerBot and DownloaderBot both
told users, in all three languages, that they were "still being developed and
hosted temporarily" and might not answer. That was written before `1.0.0`, and
`1.0.0` was declared *because* the bots went onto Railway permanently. Six
paragraphs removed. `TERMS.md` carries the honest version of the same idea —
no promise of availability, from one person, for free — where it belongs.

### 5. AnonBot: one way in, and nothing kept (2026-09-08)

#### The privacy notice was not true

Section 2 shipped a notice saying, in three languages and in `PRIVACY.md`:

> Messages are relayed and never stored. They exist in the two Telegram chats,
> where each side can delete their own copy, and in no database belonging to
> this bot.

It was false when it shipped. A message that did not say which conversation it
answered was **held** — `update.message.to_dict()`, text and all, parked in
`user_data` behind a *Send it anyway* button — and `user_data` is written to
Postgres by `lifecycle.PostgresPersistence`. The path a confused person was
most likely to take was the one path that wrote down what they had written.

The button was added to spare somebody a swipe. It cost the sentence above.

#### The rule has no way past it now

Every message either side sends says which conversation it answers, by being a
real Telegram reply to a real message in it. One message is exempt and only
one: a guest's opening message, which has nothing above it to answer.

A message that names nothing is refused. Not held, not queued, not offered
back — refused, with nothing kept. That is true even where exactly one
destination was possible, which is where the button used to appear: one
destination is still a guess, and it was the guess the rule exists to prevent,
made by a button instead of by the router.

#### ForceReply is gone, and with it the second route

Tapping *Reply* used to send a prompt carrying a `ForceReply`, which aimed the
reply box at one conversation. It worked. It was also a **second way into a
conversation**, and this bot's one unforgivable failure is delivering to the
wrong stranger — one route is easier to be sure about than two. Removed with
it: the prompt's relay row, the `is_prompt` flag that marked it, `_retire_prompt`
which deleted it once answered, `_quote_of` which filled it, the `/cancel` item
for backing out of it, and the `FORCE_REPLY_KEY` bookkeeping behind all of it.

`bot.py` is 98 lines shorter.

**The button stays and explains the gesture.** It cannot aim the reply box —
nothing but `ForceReply` can, and only at a message being sent right now — so
what is left for it is to teach the swipe once to whoever has not met it, in
an alert, storing nothing. A rule with no visible way to obey it is the shape
of every bot people give up on. Its label no longer promises to send: it reads
*How to reply*.

#### Not being dead-ended

Losing the opening exemption is correct — something else has happened in the
chat, so a bare message could be answering that instead — but it must not
leave somebody holding a conversation they cannot start. Three things make
that safe, and the third is the one that was missing:

1. The line announcing a conversation is a relay row from the moment it is
   sent, so it is always a valid thing to reply to.
2. The exemption is spent on *the chat*, not on the conversation, so it cannot
   be quietly consumed by a second link tap.
3. **The refusal points at the message.** `get_routing_context` now returns
   `active_anchor` — the newest message in this chat belonging to the
   conversation waiting on one — and the refusal is sent *as a reply to it*.
   Saying "reply to the message you are answering" without saying which one is
   the frustrating half of this rule.

There are two refusals rather than one, because the two situations are asking
for different things: a conversation that has not started yet is asking for a
first message, and one that has is asking for an answer.

#### The guest was being shown a number that meant nothing

`conv_number` is the **owner's** index for their own inbox: their first
correspondent is #1, their second #2. Right for the owner, who has to tell
strangers apart. Meaningless for a guest — whose #1 with one person and #1
with another are two different conversations wearing the same label in the
same chat, which is what the transcript showed.

It is gone from the three guest-facing strings that carried it. The owner's
`Conversation #N` header keeps it. A guest never needs to name a conversation:
they name it by replying to it, and the refusal points at the bubble, which is
unambiguous where a number was not.

#### Two bugs found on the way

**`/cancel` deleted the message it replied to.** `release_force_reply`'s second
route is "delete whatever this `/cancel` is a reply to", which is a fair guess
when every message a bot sends is a menu. AnonBot's messages are somebody's
conversation, so `/cancel` sent as a reply to a delivered message deleted it
and reported cancelling a prompt that never existed. Proven, then fixed:
`finish_cancel` takes `stored_only`, AnonBot passes it, the other three are
unchanged. *They are not entirely safe from it either* — a `/cancel` replying
to a converted file or a finished download deletes that too — but the fix
there is a judgement about those bots, not a side effect of this one.

**`edited_message` swallowed everything.** Its body was a `try` with only a
`finally: raise ApplicationHandlerStop`, and a `raise` in a `finally` replaces
whatever was propagating. When `get_conversation_for_relay` stopped returning a
tuple, the unpack raised `TypeError`, the `finally` ate it, and editing a
message did nothing at all — no answer to the user, no crash report to the
owner. The suite was the only thing that noticed. It logs now.

#### Tests

`anon_scenarios.py` gains **group N**, which is the case asked for by name:
somebody taps an inbox link and then replies to a valid message from a
conversation they already had. Two things have to be true afterwards and they
pull in opposite directions — the reply goes where the reply says, *and* the
new conversation is still openable — and getting only one of them right is
either a message to the wrong stranger or a dead conversation with somebody
typing into it. It ends by checking that the uninterrupted case still just
works: tap a link, type, sent.

`no_storage_scenarios.py` is new and is the one that holds the notice to its
word. It runs with `DEPLOY_SAFETY=on`, unlike the other scenario suites, so
persistence is live the way it is in production and a negative means
something. Every message carries a nonce; after each one the process is made
to flush and every text, varchar and json column in the schema is searched for
it — asked of `information_schema`, so a column added later is covered on the
day it is added. It checks first that something the bot *does* keep is
findable, because a search that can only ever come back empty proves nothing.

Both were checked against the bug they exist to catch. Re-parking the message
in `user_data` — one line, exactly what `_hold_for_reply` did — turns
`no_storage_scenarios.py` red on four of its nine paths.

### 6. The sweep (2026-09-08)

A pass over the whole repository for dead code, duplication and leftovers,
done with tooling rather than by eye — 55,000 lines is past the size where
reading is a reliable way to find what is not there.

#### One user-facing bug

**`restarting_send_again` was missing from AnonBot.** The other three bots
define it; AnonBot never did. `shared_features.refuse_new_work` returns
`i18n.t(lang, "restarting_send_again")` whenever the bot is paused but not in
an announced maintenance window — which is *every ordinary redeploy* — and
`i18n.t()` falls back to English and then to the key itself. So anybody who
messaged AnonBot during a deploy was answered with the literal word
`restarting_send_again`, in all three languages, and nothing anywhere
complained. Added, with the other bots' wording.

That failure mode is why the audit exists: a missing key is silent at import,
silent in the logs, and visible only to the user.

#### Dead code, removed

| what | where | why it was dead |
|---|---|---|
| `db_round_trip_ms()` | `family_link.py` ×5 | a diagnostic nothing ever called |
| `held_count()` | `lifecycle.py` ×5 | superseded by the waitlist read in `family_link` |
| `is_last()` | `live_message.py` ×5 | the public form of `_still_last`, which is what everything uses |
| `command_list()` | `shared_features.py` ×4 | added in §4 and never used |
| `_table_exists()` | `parent_bot/db.py` | superseded by `_existing_tables`, which asks about many schemas at once |
| `is_prompt` parameter | `anon_bot/db.py` | the Reply prompt it marked stopped existing in §5 |
| `italicize` import | `anon_bot/bot.py` | used only by `_quote_of`, removed in §5 |
| `psycopg` import | `db_merge.py` ×4 | never used |
| five unused imports, one unused local, one placeholder-less f-string | `tests/` | — |

`pyflakes` is now clean across every file in the repository.

The `is_prompt` **column** stays. Nothing writes it and nothing reads it, but
dropping a column from a live table to reclaim a bit per row is not a trade
worth making, and keeping it means a fresh database has the same shape as the
running one. The statements that were writing `FALSE` into it explicitly now
leave it to its `DEFAULT`.

#### The fifth copy nobody was checking

Four modules are copied byte-for-byte into every bot, and `UPDATES.md` has a
PowerShell one-liner for confirming it. There is a **fifth** copy it cannot
see: `parent_bot/monitoring.py`, which holds the logging and status half of
`shared_features.py` under a different filename.

It has 22 functions and **none of them are its own**: 18 are
character-for-character identical to their counterparts, and 4 differ. The
four differences are all deliberate and all defensible — ParentBot refuses to
start without a job queue, its `db` always has `close_pool`, its crash message
has to say whose crash it is, and it prunes no per-user cache because it has
one user — so this is not drift. It is an unguarded 18-function copy, which is
a different problem: a fix to `shared_features.setup_logging` would silently
not reach the bot whose entire job is noticing when something is broken.

`tests/test_shared_copies.py` closes both halves. It asserts the four files
hash the same everywhere they appear — the manual check, moved somewhere it
cannot be forgotten — and that every function `monitoring.py` shares with
`shared_features.py` is either identical or on a list of exceptions **with a
reason each**, the same shape as `test_menu.py`'s `UNADVERTISED` and
`test_sql_safety.py`'s `ALLOWED`. It also fails if one of those four documented
exceptions stops being a real difference, so the list cannot rot into a place
things are quietly added to.

Checked against both failures it exists to catch: an extra line in one bot's
`lifecycle.py`, and one statement changed in `monitoring.py`. Both go red.

#### Checked and found already right

The sweep's value is as much in what it cleared as in what it found, and
several things that looked dead were not:

- **Six i18n keys per bot that no `i18n.t("literal")` mentions.**
  `update_will_reset` and `update_done_try_now` are reached through
  `family_link.phrase()`, which passes a variable key; DownloaderBot's four
  download errors are chosen from a dict, and `quota_hour`/`quota_day` by a
  conditional. All live. The first version of the audit reported them as
  unused, which is exactly the wrong answer to act on — it was rewritten to
  read the call sites as syntax rather than as text.
- **`CLOUD_DATABASE_URL`**, documented in five `.env.example` files and read
  by no Python: `db_backup.ps1` and `db_sync.ps1` read it.
- **`PAYMENT_PROVIDER_TOKEN_USD`**, likewise: built at runtime as
  `PAYMENT_PROVIDER_TOKEN_{currency}`.
- **`family_link._PLAIN`**, four English sentences that also exist in every
  `i18n.py`. Not duplication — `phrase()` tries the translation first and
  falls back to these, which is what lets the family bus speak to a user of a
  bot whose `i18n.py` has not heard of the key.
- **`migrate_to_shared_db.py`**, the one-off from the `1.2.0` database move:
  referenced by `DEPLOY.md`, `README.md` and all five `db.py` files.
- **Every callback pattern** has a button that produces it, and every button
  has a handler.
- **Every column** in all five schemas is read or written by something outside
  `init_db`, `is_prompt` aside.

### 7. AnonBot: many conversations, one chat (2026-09-08)

One chat holds every conversation an inbox has, interleaved, and the only
tools for that were a header on each incoming message and the ability to
scroll. Three questions follow from that shape, and none of them had an
answer: how many conversations am I actually in, where does this one start,
and which of these am I finished with.

#### `/conversations`

A list, a card, and one action, editing a single message in place.

The list opens on a count -- `4 open · 6 archived` -- and a button per
thread, most recently active first, six to a page. The conversations are
buttons rather than lines of text because every one of them is something to
tap, and printing each as a line and then repeating it as a button says the
same thing twice in a chat that is mostly somebody else's words already.

Tapping one opens its card in the same message: when it started, when it was
last spoken in, and two things that can be done with it. **Block is
deliberately not one of them.** It is already under every message the guest
has sent, and a list is the wrong place for an action a mis-tap cannot take
back.

**Open means reachable, not "ever existed".** A link tap nobody ever wrote
into is not a conversation anybody had, a blocked guest's thread is neither
open nor archived -- those are in `/blocked`, which already lists them with
an Undo button -- and an archived one is not open by definition. `/stats`
keeps the lifetime total; this number is the one the owner can act on.

Times are relative and unitised -- `3 h ago`, `2 d ago` -- with no clock and
no date anywhere in the feature. Every timestamp in this schema is UTC and
the bot has no idea which timezone the person reading is in, so a clock time
would be wrong for nearly everybody; a date would need month names in three
languages. "How long ago" is the question actually being asked, and four
strings answer it without a plural form in sight.

#### Going to the start of one

A private chat with a bot has no message links: `t.me/c/...` is supergroups
and channels only. So "go to the start" is a **reply-quote** -- the bot sends
a bookmark anchored to that conversation's earliest surviving message, and
tapping the quote is what scrolls there. `anon_relay` already knew which
message that was; message ids climb within a chat, so the smallest is the
first, and one index makes the lookup cheap.

**The bookmark is itself a relay row for its conversation**, which means
replying to it writes into that thread. This is not a hole in the reply rule
and it is worth being exact about why. The rule is that a message must say
which conversation it answers by being a Telegram reply to a message in that
conversation. The bookmark is a message in exactly one conversation, so
replying to it says that -- it is the same thing the line announcing a new
conversation has always been. What it adds is a way into a thread without
having to find a message from it first, which is the other half of what the
list is for.

When nothing survives to point at, it says so and names the retention window
rather than pointing somewhere else.

#### Archive, and why it is not Close

**One flag, and it is filing.** An archived conversation leaves the open list
and the open count. Nothing is refused because of it. The guest is never
told. Anything either side sends into an archived thread clears the flag on
the way past, in the same statement that was already writing the relay rows
-- because a message arriving means it was not finished after all.

An **enforced** close was the other option and it is the wrong one. A guest
can tap the link and open a fresh conversation in one action, so a close that
refuses messages stops nothing and only looks like it does; protection in
name only is worse than none, because somebody relies on it. `/blocked` is
what stops a person writing, and it is per person rather than per thread,
which is the granularity that actually matches the intent.

**Time archives what the owner does not.** `prune_old_data` already deleted
the relay rows of conversations silent for `RELAY_RETENTION_DAYS`; it now
archives those same conversations in the same pass. Every route into a
conversation goes through a reply to a message that has a relay row, so a
conversation with none left cannot be written into by either side -- it is
closed, as a fact rather than as a decision. Without this, "4 open" would
have been a lifetime total wearing the word "open", which is the number the
whole command exists to stop being. It leaves already-archived rows alone:
`archived_at` is when the owner filed it, and overwriting that with the night
the pruner happened to run loses the only thing it says.

#### `/which`

Sent as a reply to any message: which conversation is that in.

Every message the bot delivers already carries a `Conversation #N` header, so
this is not for those. It is for what the bot cannot label -- the owner's own
outgoing bubbles, the second and third photo of an album, the later bubbles
of a message too long for one -- and it answers for any of them because it
asks `anon_relay` rather than reading the screen.

**A guest is not given the number.** `conv_number` is the owner's index for
their own inbox, so `#3` in a guest's chat means "this owner's third
correspondent": meaningless to them, and a fact about somebody else. They are
sent a bookmark to where the conversation starts instead, which identifies it
without labelling it.

#### The number the guest was still being shown

`§5` removed `conv_number` from the guest-facing strings that carried it,
on the grounds that it meant nothing to them. It missed one, and it was the
most visible: `incoming_header_follower`, the header on every reply an owner
sends. So a stranger who got one answer learned they were somebody's Nth
correspondent -- a running count of the owner's other conversations,
delivered to whoever tapped a publicly posted link, with no way for the owner
to stop it. That is a stronger objection than the one §5 was making, and it
had been true the whole time.

The header stays, because it is what separates the bot's own voice from the
words it is relaying. It now reads `📬 New message` and names nothing.
`follow_link_started` was still being passed a `conv_number` its string had
stopped using, which is how a number that must not reach a guest stays one
edit away from doing so; that is gone too. Two suites assert the absence.

#### TestBot could not test AnonBot

Found while trying to. The poll lease is a Postgres advisory lock keyed on
the bot's own name, and that name is a constant in each bot's `bot.py` rather
than `FAMILY_BOT_ID` -- so a TestBot running `anon_bot`'s code asks for
`anonbot`'s lock. Advisory locks are per *database*, not per schema, and
v1.3.0 pointed `TESTBOT_DATABASE_URL` at the shared one. Every test run
therefore queued behind the AnonBot serving real users on Railway for the
full `DEPLOY_LEASE_WAIT_SECONDS`, which is four minutes of not testing.

Nothing was ever at risk: `pg_try_advisory_lock` cannot take a lock off the
process holding it, so the live bot kept its lease throughout, and the 409
Conflict the warning threatens cannot happen between two different tokens.
`testbot/run.ps1` sets the wait to zero and says all of that in a comment.
The rest of `lifecycle.py` stays on, so persisted state, the single-poller
check itself and the SIGTERM handling are all still exercised.

#### What this is not

- **No per-user setting.** A switch that turns conversation numbers on and
  off was the other half of the original idea, and the messages that lack a
  number are the ones a bot cannot edit -- the owner's own. A setting could
  not have fixed them, and `/which` answers for all of them.
- **No "waiting on you" marker in the list.** It is the most useful thing an
  inbox list could add and it needs a column recording which side spoke last,
  which is a schema change for a feature nobody asked for yet.
- **No export, no search, no rename.** Threads are numbered, and the number
  is the owner's index. Naming one would be a pseudonym for a person, which
  is the thing this bot does not do.

### Files changed

| file | what |
|---|---|
| `*/shared_features.py` | new section: `privacy_text`, `terms_text`, the three handlers, the erase keyboard, `OPERATOR_CONTACT` / `PRIVACY_URL` / `TERMS_URL`; and the three donation callbacks now check what they are given |
| `*/lifecycle.py` | `forget_user()` — the persisted state `db.py` cannot reach, and a missing table is not a failure |
| `*/db.py` | `erase_user()`, one per bot, over that bot's own tables |
| `*/i18n.py` | 23 policy strings × 3 languages, four of them bot-specific |
| `*/bot.py` | three handlers, one callback, three menu entries |
| `*/PRIVACY.md`, `*/TERMS.md` | new; published by `publish.ps1` |
| `*/README.md` | the three commands in the table, and a section pointing at the long forms |
| `*/.env.example` | the three variables that name a human |
| `DEPLOY.md` | those variables on Railway, and the BotFather field |
| `convert_bot/bot.py` | `_safe_ext()`, and the chosen format checked against `TARGETS` |
| `downloader_bot/resolvers.py` | `_ext_of()` refuses anything that is not an extension, and no longer reads a dotless name as one |
| `tests/test_policies.py` | new; wired into `tests/run.py` |
| `tests/test_sql_safety.py` | new; wired into `tests/run.py` |
| `tests/test_untrusted_names.py` | new; wired into `tests/run.py`, per bot |
| `tests/policy_scenarios.py` | new; needs a Postgres, like `anon_scenarios.py` |
| `tests/erase_scenarios.py` | new; needs a Postgres |
| `tests/no_storage_scenarios.py` | new; needs a Postgres, and runs with persistence on |
| `tests/test_shared_copies.py` | new; the byte-identity check, plus ParentBot's unguarded fifth copy |
| `*/family_link.py`, `*/lifecycle.py`, `*/live_message.py` | one dead function each |
| `UPDATES.md` | the manual hash check now points at the test that does it |
| `anon_bot/bot.py` | the reply requirement has no way past it; 98 lines shorter |
| `anon_bot/db.py` | `active_anchor` in `get_routing_context`; `get_conversation_for_relay` returns an id |
| `anon_bot/i18n.py` | seven keys gone, two new, the guest's conversation number removed |
| `anon_bot/README.md` | the routing section, which described two routes |
| `tests/anon_scenarios.py` | group N; the prompt scenarios rewritten as swipes |
| `tests/anon_flow.py` | the Send-it-anyway sections replaced with "nothing is kept" |
| `tests/anon_scenarios.py` | the crash collector reconnected — see §3 |
| `tests/sim.py` | `buttons(message_id)` answers `[]` instead of raising |
| `tests/README.md` | the two new database-backed suites, and the logo checks |
| `scripts/make_logos.py` | new; writes both formats from one set of shapes |
| `*/logo.svg`, `*/logo.png` | new; published, and what BotFather's Botpic wants |
| `*/shared_features.py` | the layout helpers, and `publish_profile()` |
| `*/i18n.py` | the two profile strings × 3 languages; 327 dashes; six stale paragraphs gone |
| `*/README.md` | each bot's logo, and the family's in the root table |
| `tests/test_logos.py` | new; wired into `tests/run.py` |
| `anon_bot/db.py` | `archived_at`, two indexes, the four `/conversations` queries, un-archiving folded into `record_relay_and_touch`, auto-archiving into `prune_old_data` |
| `anon_bot/bot.py` | `/conversations`, `/which`, four callbacks, `_ago`, `_send_bookmark`; `_conversation_arg` generalised to `_callback_ints` |
| `anon_bot/i18n.py` | 31 keys × 3 languages; `incoming_header_follower` no longer names a conversation |
| `anon_bot/db_merge.py` | `archived_at` carried across a reconcile |
| `anon_bot/README.md` | the two commands, and a section on keeping track of many at once |
| `anon_bot/PRIVACY.md` | the conversation row says what it actually holds |
| `testbot/run.ps1` | `DEPLOY_LEASE_WAIT_SECONDS=0`, so a test run does not queue behind the live bot |
| `tests/anon_scenarios.py` | group O, 36 checks |
| `tests/anon_flow.py` | the guest-side header asserted to name nothing |

Unchanged on purpose: `family_link.py`. All four byte-identical files still
hash to one value each.

### Tests

| suite | result |
|---|---|
| `tests/run.py` | 36/36 |
| `tests/anon_scenarios.py` | 173/173, now with a crash collector that works |
| `tests/no_storage_scenarios.py` | 15/15 |
| `tests/policy_scenarios.py` | 61/61 |
| `tests/erase_scenarios.py` | all four bots |
| `tests/anon_flow.py`, `smoke`, `startup`, `functional`, `start_flow`, `cancel_flow`, `lossless`, `errors`, `media` | all pass |

Each of the three new safety tests was checked against the bug it exists to
catch -- a quoted interpolation planted in a DELETE, the old `_ext_of`, and
the donation guard removed -- and each goes red. `test_logos.py` needed no
such check: it failed three times on its first run, on real problems, and §4
says what they were. A test of this kind that has
never been seen to fail is a decoration, and §3 has an example of one.

`test_policies.py` renders both documents in all three languages for each of
the four bots and fails on a key name reaching the output — which is the real
failure mode, since `i18n.t()` falls back to the key itself and nothing
notices until somebody in Tashkent is shown the word `privacy_kept_for` in the
middle of a privacy notice.

### Not done yet

- **The operator line**, above. One placeholder per file, four files.
- **`ACTIVITY_RETENTION_DAYS` is still 90.** Now published, not yet argued
  down.
- **No data export.** `/deletemydata` reports a count, and `/privacy` lists
  the categories exhaustively; a machine-readable dump of six settings rows
  would be ceremony rather than a right anybody is short of. Worth revisiting
  if the bots ever hold something a person could not reconstruct.
- **The profile photos are not uploaded.** `logo.png` is generated and
  committed, but *Edit Botpic* is not on the Bot API and has to be done once
  by hand in BotFather. `DEPLOY.md` says where.
- **ParentBot has no profile text.** It has no `i18n.py`, being private and
  untranslated, so `publish_profile()` skips it. One user, who wrote it.
- **`parent_bot/family_db.py` builds identifiers from a backup manifest.** No
  Telegram command reaches it and the owner chooses the archive, so it is
  listed in `test_sql_safety.py`'s `ALLOWED` rather than fixed. Worth
  revisiting if a restore ever becomes something a bot can trigger.

---

<a id="v1.5.0"></a>

## v1.5.0 — everything since 1.4.0

A minor release, so this file covers the whole line since the previous minor:
`1.4.0 → 1.4.1 → 1.5.0`. `1.4.0` was finalised and published, so everything
after its tag is a step toward this one and is written up here — §0 is that
step, and it is why this file opens with work that has nothing to do with file
conversion.

One version, one bot, one question: **what can ConvertBot actually convert?**
Before this it was images, video and audio — three lists totalling fifteen
target formats, hard-coded in `convert_utils.py`. Now it is around seventy-five
formats in, forty-three out and roughly eight hundred pairs, and the bot can
say so.

---

### Where the line went

**Documents, e-books, spreadsheets and iPhone photos.** Four backends past
Pillow and ffmpeg — PyMuPDF, mammoth, openpyxl and pillow-heif — and the
conversions they make possible: any picture (or a whole album) into a PDF, a
PDF or an EPUB into pictures or plain text, a Word document into a PDF, a CSV
into a spreadsheet, subtitles between SRT, VTT and ASS. §1.

**A matrix that is filtered by what actually imports.** Every one of those
backends is optional, and `formats.py` probes for each at startup. A format
whose library is missing is left off the menu and out of `/formats` rather
than failing when somebody picks it — which, above the free threshold, would
have been an invoice for an exception. §2.

**`/formats`.** Six categories, one tap each, plus a direct lookup: `/formats
heic` says what a HEIC becomes, and so does typing "heic" at the bot. Offered
in four places, one of which is the refusal a user reads when their file is
not one the bot takes. §3.

**A menu that survived getting fourteen buttons long.** Six formats and a
"More formats" that redraws the same message. §4.

**Albums.** Five photos used to produce five menus, four of them already
holding a deleted file. They are collected into one conversion now, which is
also what makes "these pictures, one PDF" expressible at all. §5.

**Work limits, because the price is by size.** A 900 KB PDF can be four
hundred pages. Ceilings on pages, frames, rows, cells and batch size, checked
before an invoice rather than after. §6.

**A test that reads the whole matrix.** Every declared pair has a backend;
every backend's claim is reachable. Eight hundred assertions that nobody has
to make by hand. §7.

**And v1.4.1 before any of it.** The owner-only commands were hidden behind
the default command scope, which hid them from the owner as well; ParentBot
was advertising its levers to strangers and had no profile text at all; and
two scripts were printing the shared database's password on every run. §0.

---

### 0. v1.4.1 — the commands nobody was offered (2026-09-08)

`v1.4.0` was tagged and published, and then two more pieces of work landed:
this one and the password fix below. Under the family's own rule they are
`1.4.1`, and micro versions after a finalised release are steps toward the
next minor — so they are part of this line and written up here rather than in
the finished version's notes.

#### A menu is not a permission, and it had been used as one

Every bot in the family handles commands it never advertises. The owner-only
ones — `/status`, `/dbdump`, `/messageas`, and each bot's own `/whois`,
`/stars`, `/providers`, `/crashtest` — were kept out of `set_my_commands` on
reasoning that is sound as far as it goes: there is no point offering a
stranger a command that will refuse them.

What it missed is that the default scope is the *only* scope the family was
using, so those commands were hidden from the one person who can run them.
The owner types `/status` into their own bot from memory or does not, and a
command nobody is reminded of is a command that quietly stops being used.
`/probe` and `/stars` had been through a whole version without being typed.

Telegram already has the answer. A command menu has a **scope**, and
`BotCommandScopeChat` sets one for a single chat. So the menu goes up twice
now: the public list at the default scope, and the public list plus the
owner-only ones in each admin's own chat. `publish_commands()` in
`shared_features.py` does it, beside `publish_profile()`, and it never raises —
`set_my_commands` for a chat Telegram has never seen fails, and an owner who
has not opened their own bot is exactly that chat.

**The guards are untouched.** A scope decides what is *offered*; `_is_admin`
decides what is *allowed*, and it still does. The owner-only entries carry a
🔒 so the owner can see at a glance which half of their menu is theirs alone —
useful in the moment before telling somebody "just type /status".

Two things about scopes are worth writing down. A scoped menu outlives its
reason: drop an id from `*_ADMIN_ID` and that person keeps the longer menu
until `delete_my_commands` is called for their chat. It is cosmetic — every
one of those commands still refuses them — but it is exactly why the menu must
not be mistaken for a permission. And the scope replaces the default rather
than adding to it, which is why the owner's list is built as public + admin
rather than as the admin extras alone.

#### ParentBot, the same rule upside down

ParentBot was publishing its whole list at the default scope. It is a private
bot that answers strangers with one flat sentence, so anybody who found it was
handed a menu reading `/sql`, `/backup`, `/restart`, `/broadcast` — every one
of which `guard()` refuses them. No hole, but a list of the levers is an
invitation to lean on them and there is nothing to gain by publishing it.

Now the full list goes into each owner's chat and the default scope holds
`/start` alone, which is the one command that answers a stranger.

Its list was also four commands short. `/probe`, `/providers`, `/stars` and
`/crashtest` were handled, documented in `HELP`, and absent from the menu —
and `HELP` itself says "every owner-only command any bot has is reachable from
here. If one is missing, that is a bug." The order now follows `HELP`'s own:
watching, reaching into a bot, shipping an update, then the family-wide reads.

#### ParentBot had no profile text either

`publish_profile()` gives the four public bots a short description and a
description in three languages. ParentBot was skipped because it has no
`i18n.py`, being private and untranslated — and the note in *Not done yet*
justified it as "one user, who wrote it".

That is backwards. The owner is the one person who will never read them. They
are for the stranger who has just found a bot called *mumu manager* and is
deciding what it is, and for whom "This bot is not for general use and it will
not answer you" is a better answer than a blank panel and a Start button. It
has both now, in English, with the sibling usernames built from
`USERNAME_ALIASES` rather than typed out again, so a rename carries.

#### The test that let all of this through

`tests/test_menu.py` checked one direction — every public command advertised,
no owner-only command leaked — for four bots. ParentBot was not in the list at
all, which is how its four commands went missing without anything noticing.

It now reads the command lists by assignment name rather than by walking the
file for `BotCommand` calls, because there are two lists and which one a
command is in is the whole point. It checks the other direction (every
owner-only command *is* in the owner's menu), that nothing appears in both,
that the owner's list holds only gated commands, and it covers ParentBot with
the inverted rules — including that `/sql`, `/backup`, `/restart` and
`/broadcast` stay out of the stranger's menu.

`sticker_bot`'s `/convert` stays in `UNADVERTISED` and out of both lists. It is
a redirect to @ConvertBot kept for people still typing it out of habit;
offering it would be advertising a command whose whole answer is "use a
different bot".

#### DESIGN.md

New, at the root, and internal — `publish.ps1` does not carry it. It is the
file that says why the amber is amber-600 rather than amber-500, what each of
the four surfaces a Telegram bot actually has is currently doing, and what is
left: the logos still not uploaded, the description picture nobody is using,
and the command menu still being English-only in a trilingual family.

It also records the one idea that was argued down rather than built. "Interesting
fonts" in a Telegram bot can only mean the Unicode look-alike alphabets —
𝐛𝐨𝐥𝐝, 𝓈𝒸𝓇𝒾𝓅𝓉, ᴄᴀᴘs — and they are a bad trade here: a screen reader says
"mathematical bold small a", in-chat search stops matching the words, older
clients draw boxes, and, decisively, the alphabets are Latin-only. There is no
bold-script block for Cyrillic and none for the Uzbek diacritics, so a styled
interface would be styled in English and plain in the two languages most of
these users read. The real markup — bold, monospace, expandable blockquote —
is already there and already in use.

### 0b. v1.4.1 — two scripts printing a live database password (2026-09-08)

`testbot/run.ps1` printed the whole `TESTBOT_DATABASE_URL` on every start, and
since `v1.3.0` that DSN is the shared Supabase one — so the password of the
database all five bots run on went into terminal scrollback, and into any log
the run was piped to, every time anybody tested anything.

The password was never the useful part of that line. What the reader is
checking is "local, or the real shared database", and host plus database name
answers that on its own. The username goes too, because on Supabase it carries
the project ref. A DSN the regex cannot read says so rather than falling back
to printing it, which is the one behaviour worth ruling out. Checked against a
percent-encoded `@` in the password, a URL with no credentials at all, query
strings, fragments and an empty setting.

`migrate_to_shared_db.py` had the same habit in its closing instructions —
*"Next: in each bot's .env set DATABASE_URL=<the whole thing>"*. It already
named the source database by host two lines earlier, so this is the same
treatment applied to the target, and it only ever saved a paste of a value the
operator typed on that very command line.

The rest is clean: `db_sync.ps1` names the variable but never its value,
`start_all.ps1` and `scripts/nightly_release.py` print neither, and
`db_backup.ps1` hands the DSN to `pg_dump` as an argument without echoing it.
That last one is still visible in the process list while it runs, which is a
different exposure and not this change.

Neither of these two reaches a bot repository: `testbot/` and the root scripts
are monorepo-only, so `publish.ps1` does not carry them.

### 1. What the bot converts now

#### The four new backends

| | what it does | why it |
|---|---|---|
| **PyMuPDF** | PDF, EPUB, MOBI, FB2, CBZ, XPS and SVG: opening them, rendering pages to pictures, extracting text, and laying HTML out onto a page | one wheel, no system package, and it is the only one of these that does all four |
| **pillow-heif** | HEIC and HEIF | what an iPhone camera writes. The format most likely to arrive from somebody who has never converted a file before |
| **mammoth** | .docx to semantic HTML, which PyMuPDF then renders | pure Python, and it keeps headings, emphasis, lists and tables rather than flattening to a wall of text |
| **openpyxl** | .xlsx, both directions | ditto |

Plus `Markdown`, `PyYAML` and `defusedxml`, which are small. About 45 MB of
container image between all of them, most of it PyMuPDF, and **not one of them
needs a system package** — the native code is bundled in the wheels, so
`nixpacks.toml` is unchanged and still asks only for `ffmpeg`.

#### The matrix

Six categories. Sources and targets are different lists on purpose: reading a
format is somebody else's library problem, writing one is a decision.

| | reads | writes |
|---|---|---|
| image | PNG, JPEG, WEBP, BMP, TIFF, ICO, CUR, HEIC, HEIF, AVIF, JP2, PSD, TGA, PCX, DDS, PPM, SGI, XBM, XPM, ICNS, QOI, BLP, IM, SVG | PNG, JPEG, WEBP, **PDF**, ICO, TIFF, BMP, GIF, AVIF, HEIC, JP2, TGA, PPM, QOI, ICNS |
| video | MP4, MOV, M4V, AVI, WEBM, MKV, FLV, WMV, TS, M2TS, MPG, 3GP, OGV, ASF, GIF, APNG | MP4, WEBM, MKV, MOV, AVI, GIF, WEBP, APNG + every audio target |
| audio | MP3, WAV, OGG, Opus, M4A, AAC, FLAC, AIFF, WMA, AMR, AC3, MP2, AU, CAF, W64, VOC | MP3, M4A, AAC, OGG, Opus, WAV, FLAC, AIFF, WMA, AC3 |
| document | PDF, DOCX, EPUB, MOBI, FB2, CBZ, XPS, MD, TXT, HTML | PDF, PNG, JPEG, WEBP, TIFF, SVG, TXT, HTML, MD |
| data | CSV, TSV, JSON, YAML, XLSX, XML | JSON, CSV, TSV, YAML, XLSX, MD, HTML, PDF |
| subtitle | SRT, VTT, ASS/SSA | SRT, VTT, ASS, TXT |

Targets are per *format* first and per category second, because within one
category the answers genuinely differ. An EPUB converts to a PDF; a PDF does
not convert to an EPUB. A FLAC is offered WAV before MP3, because somebody
sending a FLAC is not usually looking for a 128 kbps MP3.

#### The pairs that are the point

The owner's brief was "nicher is better, because it will be a reason to use
specifically my bot". The ones that answer it:

- **HEIC → JPG.** An iPhone photo sent as a file, which every Android user has
  received and been unable to open.
- **Several pictures → one PDF.** §5. The thing people mean when they say
  "image to PDF", and the thing a per-file converter cannot do.
- **PDF or EPUB → PNG / JPG / text.** One page comes back as a picture; forty
  come back as a zip of them.
- **DOCX → PDF, HTML or Markdown.** With the caveat below, which the bot says
  out loud.
- **CSV / JSON / XML → XLSX**, and a workbook → a CSV per sheet, zipped,
  rather than one sheet chosen silently.
- **SRT ↔ VTT ↔ ASS**, and any of them → the transcript with the timings
  stripped out.
- **SVG → PNG or PDF**, MOBI and FB2 → PDF, CBZ → PDF, PSD → PNG.

#### What "DOCX to PDF" means, and what it does not

There is no LibreOffice in the image, so a .docx is not rendered the way Word
would render it: page breaks, columns, headers, footers and exact kerning are
not preserved. The document is — headings, emphasis, lists, tables — reflowed
onto A4 by MuPDF's `Story` layout.

That is a real limitation and the bot says so on the file it sends back
(`converted_layout_note`, three languages) rather than letting somebody
discover it by opening the result. A conversion that "worked" and quietly
produced something other than what was expected is the failure mode worth
spending three lines of caption on.

LibreOffice would fix it and cost about a gigabyte of image and a much slower
cold start on every deploy, for one format pair. *Not done yet*, below.

#### A GIF is two different files wearing one extension

Single frame: a picture, which should be offered PDF and ICO. Animated: a
video with no sound, which must not be offered a still target that would throw
every frame but the first away, silently, in a way that looks like it worked.
The extension cannot tell them apart, so the *file* is asked —
`gif_is_animated()` reads the header, Pillow does not decode the frames — and
the answer travels with the pending conversion so that the menu and the
conversion agree. Same for WEBP and APNG, in the other direction.

#### Two ffmpeg arguments that were missing

`-pix_fmt yuv420p` and an even-dimensions scale filter on every H.264 and VP9
target. A GIF arrives as a palette and a screen recording as RGB, and neither
encoder will open with those: the user saw *Could not open encoder before
EOF*. H.264 also refuses odd dimensions, which a 601-pixel-wide screenshot is
not something anybody should have to know.

GIF output also gained `palettegen`/`paletteuse`. One extra pass, and it is
the difference between a usable GIF and a poster for why nobody uses GIFs.

### 2. `formats.py`, and offering only what exists

The matrix is **declared in full and then filtered by what imports**.
`probe()` runs once in `main()`, before the first menu is ever built: it looks
for each optional module, asks Pillow which features it was compiled with, and
runs `ffmpeg -encoders` once to find out which codecs this build actually
carries. Everything user-facing reads the filtered view.

The failure this prevents is specific. Without it, a build where PyMuPDF did
not install offers a PDF button; the user picks it; above 1 MB an invoice goes
out first; the conversion raises `ImportError`; the bot refunds and apologises.
With it, PDF is not on the menu and `/formats` does not claim it. The startup
log says which backends are present, and names the ones that are not.

`nixpacks` builds are reproducible enough that this should never fire in
production. It fires constantly in development, which is the point: the test
suite runs with none of these installed and `tests/test_conversions.py`
asserts the filtering by removing requirements one at a time.

The one exception in the requirements table is `(IMAGE, "pdf")`. A PDF
*source* needs PyMuPDF; a PDF *target* of a picture does not, because Pillow
writes PDF pages itself. Without that override, a host with no PyMuPDF would
refuse the single most asked-for conversion in the bot for no reason.

### 3. `/formats`

The owner asked for "a helper message that tells which file formats are
supported" and left when and how to show it open. Four places:

| where | why there |
|---|---|
| the command, in the menu and in `/help` | somebody deciding whether this bot is worth keeping |
| a **button under the refusal** when a file's format is not one we take | the highest-value moment there is: the user has just demonstrated they want something converted and has just been told no |
| a button under `/convert` and under "couldn't tell that file's format" | the other two dead ends |
| **a bare word typed at the bot** | "heic", ".epub", "webm?" — one word typed at a converter is usually a format. It costs a dictionary lookup in the catch-all handler that was already running, and it is the kind of thing that makes a bot feel like it is paying attention |

What it does **not** do is print eighty formats in one message. The first
screen is the three counts, six examples in an expandable blockquote, and a
button per category; a category is one tap and shows that category's full
*reads* and *writes* lines. `/formats heic` skips all of it.

The counts in the intro come from `formats.counts()` rather than from the
text, so there is no number in a translated string that somebody has to
remember to update.

### 4. The format menu

An image now has fourteen targets. Fourteen buttons is a menu somebody has to
read instead of a menu somebody can use, so the first screen is six —
`POPULAR_COUNT`, two rows of three, which is what fits on a phone without the
message scrolling — with **More formats** under it. Tapping redraws the same
message rather than sending a second one (`edit_in_place`, so it degrades
correctly if the user has typed since). A list of eight or fewer is shown
whole: there is no point hiding two buttons behind a button.

The short list is the *head* of the long one, and `test_conversions.py`
asserts it. If it were not, "More formats" could hide a button that had been
on screen a moment ago.

### 5. Albums

Telegram sends an album as N separate messages sharing a `media_group_id`, a
fraction of a second apart. Each was handled on its own, and each new arrival
called `_discard_pending()` on the last — so five photos produced five format
menus, of which four already pointed at a deleted file. A bug since the
feature existed, and invisible unless you sent an album.

The first file of a group now schedules a job `CONVERT_BATCH_WAIT_SECONDS`
out; every further file of the same group cancels and re-schedules it. The
menu is drawn once, after the last one lands.

That also makes a set of files expressible, and there are exactly two useful
answers for one:

- **Bind them into a PDF**, one picture to a page, in the order they arrived.
- **Convert each and zip the results**, for any other target.

An album of a photo and a video is refused with a sentence rather than
guessed at. The price is the total size, through the same tiers.

`_discard_pending()` and the pending-conversion state now hold a *list* of
paths; abandoning a batch deletes all of them.

### 6. Work limits

The price is calculated from the file size. That was a fair proxy when the bot
did images and ffmpeg. It is not one now: a 900 KB PDF can be four hundred
pages, a few hundred kilobytes of GIF can be thousands of frames, and a 2 MB
spreadsheet can be a million cells. Rendering any of those is minutes of CPU
sold at the 8-star tier, or free.

The answer is not a more elaborate price table — nobody could predict their
own price from looking at their file — but ceilings on the *work*, refused
before an invoice is sent rather than after:

| | default | env var |
|---|---|---|
| pages rendered or laid out | 200 | `CONVERT_MAX_PAGES` |
| frames in an animation | 1200 | `CONVERT_MAX_FRAMES` |
| spreadsheet rows | 100 000 | `CONVERT_MAX_ROWS` |
| spreadsheet cells | 2 000 000 | `CONVERT_MAX_CELLS` |
| files in one batch | 20 | `CONVERT_MAX_BATCH` |

Alongside the two that already existed: `Image.MAX_IMAGE_PIXELS` at 80
megapixels (a decompression bomb becomes a caught exception rather than an OOM
kill) and `FFMPEG_TIMEOUT_SECONDS`.

#### Parsers pointed at a stranger's file

Four new ones, and two of them are the classic way to lose a container:

- **XML** is read with `defusedxml` and nothing else. The standard library's
  parsers expand entities, which turns a fourteen-line upload into gigabytes
  of memory ("billion laughs") or a read of a local file ("XXE"). `formats.py`
  does not offer XML at all when defusedxml is missing.
- **YAML** is read with `safe_load`, never `load`. The full loader constructs
  arbitrary Python objects named in the document, which from a stranger's file
  is remote code execution.
- Spreadsheets are opened `read_only=True, data_only=True` — streamed rather
  than built into an object model, and formulas come back as the value the
  cell last showed.

### 7. `tests/test_conversions.py`

Three lists of target formats were checkable by reading them. Eight hundred
pairs across four backends are not, and the way that goes wrong is not a
crash: `formats.py` offers a button, the user picks it, `convert()` has no
branch for that pair, and the answer is an apology — after an invoice, if the
file was over a megabyte.

So the rule the new suite pins down is one sentence: **every pair the matrix
declares has a backend that claims it, and every backend's claim is reachable
from the matrix.** Both directions — a target with no branch is a refund, and
a branch nothing points at is dead code. `doc_convert.WRITES` and
`data_convert.WRITES` exist for it, declared rather than inferred.

It also covers the invariants that have already been got wrong once:

- No format converts to itself, no format is in two categories.
- Every alias resolves to a real format, and **no alias shadows one** — if
  `jpeg` were both an alias and a format in its own right, normalisation would
  quietly redirect one real format to another.
- Callback data fits in Telegram's 64 bytes. Over it, the *keyboard* is
  rejected, so the whole menu fails rather than one button.
- The first screen is the head of the full list (§4).
- Removing a requirement removes exactly the formats that need it — and
  **leaves picture-to-PDF alone**, which is the `(IMAGE, "pdf")` override in
  §2.
- Every string the menu looks up exists in all three languages.
- The ceilings in §6 are all still positive numbers.

`test_untrusted_names.py` gained a second table. ConvertBot's `_ext_of()` now
normalises as well as sanitising — `.jpeg` and `.jpg` must not become two
buttons — and normalising is a different job from refusing a path separator,
so it is asserted separately and only for the bot that does it. The check that
a chosen format was actually offered now reads `formats.targets_for()`, which
is the *filtered* list: a host that does not offer PDF must not accept PDF
either, and both answers now come from the same call.

It also renders every screen the feature can draw, in all three languages: a
missing placeholder in one translated string is a `KeyError` inside a handler,
which reaches the user as silence.

`tests/test_batching.py` is the second new suite and drives the album flow
itself — Telegram stubbed out, the files real and small. Five photos arrive;
one job is armed rather than five; one menu is drawn, under the last file of
the album; it holds all five and offers PDF. Then the cases that are easy to
get wrong: a second album replaces the first *and deletes its files*, an album
of two different formats is refused before the odd one is even downloaded, and
cancelling a batch deletes every file in it rather than the last. It ends by
asserting the staging directory is empty, because a file left there is
somebody's photo sitting on a disk until the sweeper finds it.

The conversions themselves were exercised by hand against real fixtures of
every source format — 348 pairs, every one of them producing a non-empty file
— rather than in the suite, which would have meant committing a sample of
every format in the repository.

### 8. Everything else

**`filters.ANIMATION`** was missing from the file handler, so a GIF sent as a
Telegram animation (which is what the client sends when you pick one from the
GIF panel) fell through to "that's not a file I can convert".

**The strings that said "image, video, or audio"** — `/help`, `/convert`,
the two refusals — say what the bot does now, in all three languages. 23 new
keys × 3 languages; `en`, `uz` and `ru` are at 124 keys each with no gaps.

**`convert_utils.convert()`** takes a list of paths and returns a `Result`
(path, item count, whether it is a zip) rather than taking one path and
writing to one. The caption is built from that: a zip says how many files are
in it, a PDF built from pictures says how many pages, a reflowed document
carries the layout note. Three captions that exist because the button said one
thing and the file is another, and a caption that ignored the difference would
be a small lie in the one message the user keeps.

#### The profile card, PRIVACY.md and TERMS.md

All three still opened with "converts files between image, video and audio
formats". The profile card is the first thing a stranger reads, before Start,
and it was the only place left underselling the bot by a factor of five; the
other two are policy documents, where an incomplete description of what the
software does is not a cosmetic problem. All three say what the bot does now,
the card in three languages, with the examples that answer "why this one" --
HEIC to JPG, a set of pictures to one PDF, a PDF to pictures or text.

`tests/test_policies.py` already asserted Telegram's 120- and 512-character
caps on those two strings, and it caught the first two drafts at 532 and 556.
A description over the cap is a `setMyDescription` that fails at startup, so
the guard was worth having and is the reason this is a paragraph rather than
an incident.

PRIVACY.md also said files are converted "using ffmpeg", which is now a third
of the truth. It names the libraries and keeps the point that matters: every
one of them runs on the machine the bot runs on, and no conversion here
uploads a file anywhere -- which is the one thing most online converters
cannot say.

### Files changed

| file | what |
|---|---|
| `convert_bot/formats.py` | **new.** The matrix, the aliases, the requirements, `probe()`, and the questions the rest of the bot asks |
| `convert_bot/doc_convert.py` | **new.** PDF, EPUB, MOBI, FB2, CBZ, XPS, SVG, DOCX, Markdown, HTML — rendering, text, and HTML-to-PDF layout |
| `convert_bot/data_convert.py` | **new.** CSV, TSV, JSON, YAML, XLSX, XML, and the subtitle transcript |
| `convert_bot/convert_utils.py` | the image target table, `images_to_pdf()`, the ffmpeg fixes, `zip_files()`, `Result`, and the dispatcher |
| `convert_bot/bot.py` | `/formats` and its two callbacks, the album batching, the two-level menu, `_result_caption()`, list-of-paths state, `filters.ANIMATION` |
| `convert_bot/i18n.py` | 23 keys × 3 languages; six existing strings widened |
| `convert_bot/requirements.txt` | seven optional dependencies, and why each is there |
| `convert_bot/.env.example` | the five work ceilings, the batch wait, the page dpi |
| `convert_bot/PRIVACY.md`, `convert_bot/TERMS.md` | what the bot converts, and what actually does the converting |
| `convert_bot/README.md` | the format table, the batch and zip behaviour, the untrusted-file note, the new files |
| `tests/test_conversions.py` | **new.** The matrix against the backends, both directions; every screen rendered in three languages |
| `tests/test_batching.py` | **new.** The album flow, driven rather than read |
| `tests/stubs/telegram/__init__.py` | `InlineKeyboardMarkup.inline_keyboard`, so a test can count buttons |
| `tests/test_untrusted_names.py` | normalisation as its own table; the guard now reads `targets_for()` |
| `tests/run.py` | the new suite |
| `*/family_link.py` | `family.usage_samples`, `record_usage()`, `usage_history()`, and its pruning |
| `*/shared_features.py` | `footprint_numbers()`, the sampler, the three alarms, the /status trend line |
| `parent_bot/monitoring.py` | the same block, verbatim |
| `parent_bot/bot.py` | `/usage` |
| `anon_bot/i18n.py` | five refusals name what was not sent; `which_unknown`; nine `/archive` strings x3 |
| `anon_bot/bot.py` | `/archive` and its confirmation; `/which` stops borrowing a send-failure string |
| `anon_bot/README.md` | `/archive` |
| `*/.env.example` | six usage knobs |
| `parent_bot/bot.py` | `/idle`, and its cold-start arithmetic |
| `downloader_bot/bot.py` | the redeliver button, `_reply_image_with_button`, job counting, and `effective_message` in six places |
| `downloader_bot/i18n.py` | four button strings x3 |
| `downloader_bot/resolvers.py` | `snapshot()` removed |
| `downloader_bot/README.md` | the button |
| `downloader_bot/resolvers.py` | `_pin_api`, `_pin_best_video`, and the reordered Pinterest chain |
| `downloader_bot/platforms.py` | Pinterest country subdomains |
| `tests/test_pinterest.py` | **new.** A video pin is a video, and the cross-provider audit as a rule |
| `*/SECURITY.md`, `*/CONTRIBUTING.md` | **new**, in the four public bots |
| `LICENSING.md` | **new**, internal. The AGPL question v1.5.0 creates |
| `tests/test_usage.py` | **new.** The sleepable-gap arithmetic and the alarm floors |
| `README.md` | ConvertBot's line |
| `DEPLOY.md` | the optional backends, what a missing one looks like, and sizing |
| `*/family_link.py` | `FAMILY_VERSION` 1.4.0 → 1.5.0 |
| `*/shared_features.py` | **v1.4.1.** `publish_commands()` sends a scoped menu into each admin's own chat |
| `*/bot.py` | **v1.4.1.** the owner-only command lists, and ParentBot's four missing ones |
| `parent_bot/bot.py` | **v1.4.1.** profile text, and `/start` alone at the default scope |
| `tests/test_menu.py` | **v1.4.1.** both directions, ParentBot included |
| `DESIGN.md` | **v1.4.1.** new, internal: the four surfaces and what is left |
| `testbot/run.ps1`, `migrate_to_shared_db.py` | **v1.4.1.** neither prints a DSN's password |

### 9. What the containers cost, over time

`/status` answers "is this process alive and what is it using right now",
which on its own tells nobody whether right now is unusual. Asked to extend
the monitoring so ConvertBot could say when there is a spike, or when the
monthly reach is outgrowing the plan, the shape that came out is one that
belongs to every bot rather than to that one — so it is in
`shared_features.py`, and `parent_bot/monitoring.py` carries the same block.

**`family.usage_samples`**, one row per bot per window: the four kernel
readings (resident, peak, the container's ceiling, CPU seconds), the number
of updates handled, and the number of distinct people in that window.
Deliberately narrow — no user ids, no chat ids, nothing about what anybody
did. Kept 45 days, which is long enough to compare this month with last, and
pruned by the housekeeping pass that already prunes commands and events.

**Three alarms, because they are three different questions:**

| | question | default |
|---|---|---|
| memory headroom | is this container close to the limit it gets killed for crossing? The only one that is an emergency | peak ≥ 85% of the cgroup ceiling |
| activity spike | far more updates in a window than the recent norm — a launch, or one script | ≥ 6× the median of the last 24 windows, and ≥ 60 updates |
| monthly reach | how many distinct people in 30 days, which is what decides whether the plan is still the right one | 400 |

Each one goes out as a `family.events` row, which ParentBot already forwards
as a DM, and each stays quiet for an hour (a day, for the monthly one) after
firing so a bad afternoon is one message rather than forty. The spike test
needs both a multiplier *and* a floor: on a bot this quiet, "2 updates, then
12" is six times the median and is not an incident.

**`process_footprint()` was split.** `footprint_numbers()` returns the four
readings as numbers and `process_footprint()` builds the sentence from them —
because a threshold and a database row want numbers, and formatting a
sentence to parse it back is the kind of thing that breaks in one language
and not another. `/status` also gained a line saying what the recent windows
looked like, so the number above it has something to be compared with.

**ParentBot's `/usage`** reads it back: average and peak memory per bot, how
close the peak came to the ceiling, updates, the busiest window's headcount,
and what each of those averages costs per month. The rate is
`MEMORY_USD_PER_MB_MONTH`, a named constant defaulting to Railway's published
$10/GB-month, so the figure in the message is arithmetic anybody can check
and one line changes when Railway's price does. `/usage 168` for a week.

### 10. AnonBot: saying what did not happen

Replying to a message AnonBot could not place answered with:

> "I don't have that message on record, so I can't tell which conversation
> this answers. **Nothing was sent.**"

Two things wrong with it, and the second is a bug rather than a wording
problem.

**"Nothing was sent" is ambiguous** — whose nothing, which nothing. Every
refusal in this bot now names the thing that did not happen and whose it was:
*"your message was not sent"*. Five strings, three languages.

**And that string was answering `/which`**, which never sends anything. Ask a
bot a question, be told nothing was sent: the reply path and the lookup path
had been sharing one string because they share a failure. They have their own
now — `which_unknown` says the message is not in any conversation open here
and stops, because that is the whole of the answer to a question.

The opening clause was also the bot talking about its own bookkeeping ("I
don't have that message on record"). The user has no model of what the bot
keeps on record and should not need one to read an error, so it now says what
is true about *their* chat: *"That message isn't in any conversation open
here."* "Open" rather than "active" — the family already uses "open" for a
conversation that has not been archived, and "active" would suggest recency
rather than membership.

**`/archive`** is new, and it exists because the message above ended by
describing a procedure. Archiving already worked, but only as a button on a
card inside `/conversations`: open the list, page to the thread, open its
card, press Archive — four steps to file the thread whose message is already
on screen. Replying to that message and sending `/archive` is one, and it
calls the same function.

It asks first. Archiving is reversible without doing anything — a filed
thread comes straight back the moment anything is relayed into it, and the
other side is never told — but a command typed under a message is much easier
to aim at the wrong message than a button on a card that names the
conversation, so the confirmation is where the naming happens. A guest gets a
sentence saying archiving belongs to whoever owns the inbox, because
`conv_number` is the owner's index and there is nothing for a guest to file.

### 11. Measuring idleness, before sleeping on it

The plan was to put the bots to sleep — Railway stops charging for a container
once it has sent no outbound traffic for about five minutes. Two things came
out of looking at it properly.

**It would not have worked, and it would not have failed loudly.** Railway
detects inactivity from *outbound* packets, and its own documentation names
the thing every bot here does: keeping a database connection pool open
prevents sleeping. Add the family bus polling `family.commands` every one to
ten seconds, a heartbeat every thirty, and a thirty-second long poll to
Telegram, and the flag would have changed nothing at all. Making it work means
webhooks, a pool that closes when idle, and replacing "children poll" with
"ParentBot wakes a child over the private network" — which is the machinery
`/pause`, `/run` and `nightly_release.py` are built on.

**And it should not be decided from a guess.** So nothing sleeps yet, and what
this version adds is the measurement it would be decided from. Each bot keeps
a gap clock: the time between updates, and for each gap the part of it past
the five-minute mark, which is the only part that would ever have been
sleepable. Summing the gaps instead would say a bot handling a message every
four minutes is idle all day when it would in fact never sleep once —
`tests/test_usage.py` pins exactly that case.

**ParentBot's `/idle`** reads it back per bot: what share of the time would
have been asleep, the longest quiet stretch, roughly how many wakes that
implies, what each wake costs the person who caused it, and what the whole
thing would save per month. AnonBot is already ruled out by hand — it relays
messages between two people and a cold start is the one place a few seconds
shows.

### 12. DownloaderBot: the other way round, on a button

Getting a download in the other quality took three steps: `/lossless`, pick
the other setting, find the link, send it again. And `/lossless` is a
*preference*, so changing it to get one file differently left it changed for
every file afterwards.

Every finished download now carries a button that does that one delivery the
other way — **"Get it as an uncompressed file"** under a compressed one,
**"Get it compressed instead"** under a file. The label says what tapping it
gets you rather than which mode you are in, because one of those reads as a
button and the other reads as a setting.

Nothing changes behind it: same resolve, same download, same send, and it
counts against the same daily allowance, because it is a second download
exactly as re-pasting the link would have been. The setting is untouched.

Details worth knowing: the URL cannot ride in callback data (64 bytes, and
these are share links full of tracking parameters), so the last five
deliveries are remembered per user under a short token and older ones answer
"that one is too old for the button now" — one step worse than the button and
no worse than before it existed. Telegram allows no inline keyboard on a media
group, so an album's button goes on one line under it. And Reddit and Twitter
reach the chat through a different function than videos do, so they got the
button through a shared helper rather than being left as the one place it
mysteriously does not appear.

**Outcome counting**, which is the other half of what was asked for: a
`note_job(ok)` counter in `shared_features.py`, two more columns in
`family.usage_samples`, and an alarm when more than half the jobs in a window
failed — worded differently, and at `error` rather than `warning`, when *none*
succeeded, because that is what "every provider is down" looks like from
inside.

It counts what is worth counting and nothing else. A post with no media in it,
or one over the size limit, is the bot working correctly on something it
cannot help with, and counts as neither a success nor a failure — otherwise a
quiet afternoon of text-only links would read as an outage. Only ConvertBot
and DownloaderBot count jobs: they are the two whose work depends on somebody
else's service. StickerBot's and AnonBot's failures are Telegram-side and
mostly mean a user blocked the bot, which is not an outage and should not
raise one.

No URLs, no media, no user ids. Two integers per window.

### 13. The sweep

`pyflakes` over the whole tree, then a pass for names defined and referenced
nowhere — which is the shape dead code actually takes here, and the shape
`pyflakes` says nothing about.

**One real bug, in code from this version.** DownloaderBot's send path reads
`update.message`, which is `None` on a callback-query update. Every one of
those calls is now reachable from the new button, so the first tap would have
raised `AttributeError` inside the download and the user would have watched
the bot go quiet. `update.effective_message` is the reading that is correct
from both entry points. Six call sites across `_after_send`, `_reply_image`,
`_send_media` and `_resolve_and_send`.

**Three dead things, two of them mine.** `_mb()` in `shared_features.py` and
`monitoring.py` — the footprint refactor in §9 stopped using it and nothing
else ever had. And `resolvers.snapshot()` in DownloaderBot, whose docstring
says "for /providers and for the periodic flush" while both of those use
`health()` and `take_dirty()`; it has never been called.

What the scan flags and is **not** dead: `usage_history`, `note_job`,
`reset_user_state`, `COMMAND_HELP` and the two standalone donation handlers
appear unused inside some bots because `family_link.py` and
`shared_features.py` are byte-identical copies — every bot carries the whole
surface and each uses the part it needs. That is the design, and
`tests/test_shared_copies.py` is what keeps it honest.

**Every command a bot handles is now in a menu.** Checked by walking each
`bot.py` for `CommandHandler` registrations and comparing against
`BOT_COMMANDS` / `ADMIN_COMMANDS`: `/formats`, `/archive`, `/usage` and
`/idle` are all published, in the right scope. The one deliberate omission is
StickerBot's `/convert`, which redirects to @ConvertBot and is whitelisted in
`tests/test_menu.py`.

### 14. Pinterest video pins were downloading their cover image

Asked simply whether the downloader supports Pinterest video, the answer on
paper was yes -- `og:video` first, yt-dlp behind it -- and the answer in
practice was no, in the worst way available.

Run against yt-dlp's own Pinterest video test cases, the live v1.4.0 code
returned a **photo** for every one of them. Pinterest has stopped emitting
`og:video` on pin pages. `_pin_og` looks for it, does not find it, falls
through to `og:image` -- which is present, because it is the video's own cover
frame -- and *succeeds*. A provider that succeeds ends the chain, so
`ytdlp:pinterest`, which extracts the video perfectly well, was never reached.

Nothing errored. Nobody would have reported it as a failure; they would have
reported that the bot "sent the wrong thing", or more likely not reported it
at all. It is the same shape as the silent-audio bug `test_formats.py` was
written for.

**The fix is a third provider, first in the chain.** `pinterest_api` asks
Pinterest's own read endpoint -- the one the website's front end calls, and
the one yt-dlp uses -- which answers without a login and is the only source
that tells the three kinds of pin apart:

| pin | where the video is |
|---|---|
| plain video pin | `videos.video_list` |
| story / idea pin | `story_pin_data.pages[].blocks[].video.video_list` |
| photo pin | neither; `images.orig` |

A story pin of several pages gives several videos, in order, the way a
carousel already does. `_pin_og` stays as the fallback for when the endpoint
changes shape, and yt-dlp stays last.

Two details worth writing down. **Only `.mp4` is taken**: the same
`video_list` carries `V_HLSV4` and `V_HLSV3_MOBILE`, which are `.m3u8`
manifests, and uploading one to Telegram would send a few hundred bytes of
text named `.mp4`. And **the biggest one wins** -- the list is unordered and
ranges from a 240-pixel preview to the full 1080.

**Country subdomains, found on the way.** `PINTEREST_RE` allowed `www.` and
nothing else, so `co.pinterest.com/pin/...` -- and `in.`, `br.`, `ru.`, every
market Pinterest runs -- was answered with "that is not a link I recognise".
The share sheet hands out whichever host the sharer was on, so this reads as
the bot not supporting Pinterest at all.

**Verified against live pins, not just canned data.** Two real video pins now
resolve to `video` and their URLs serve `video/mp4` at 18.4 MB and 4.2 MB with
a valid `ftyp` header; a photo pin still resolves to a full-size photo; and
`in.pinterest.com` is now recognised. `tests/test_pinterest.py` pins the rules
offline, with payloads taken from those real pins.

### 15. The rest of the silent-failure class, and two repository files

**Every other provider was audited the same way** the Pinterest one was
found: run against live posts, and compare what each says it is handing over
against what the server actually serves.

| provider | how it decides photo from video | verdict |
|---|---|---|
| `downloadgram` (Instagram) | the filename inside the token, and it drops cover frames when any video is present | safe |
| `instafix` (Instagram) | og tags, then a one-byte range request that **refuses** if it promised video and served an image | safe |
| `tikwm` (TikTok) | the API's explicit `images` field against `play` | safe |
| `vxtwitter` / `fxtwitter` | the API's explicit media `type` | safe |
| Reddit | `post_hint == "image"` only; a video post is left to yt-dlp | safe |
| `tnktok` (TikTok) | og:video, else **fell back to og:image and succeeded** | fixed |

Nothing mismatched against live posts, so nothing else was broken today. But
`tnktok` had the same structure Pinterest did, waiting for TikTok to change
one tag. It no longer guesses: with no `og:video` it refuses, and the chain
moves on to `ytdlp:tiktok`, which handles both videos and photo slideshows.

Refusing is right in both readings of a missing `og:video`. If the post is a
video, the cover image is the wrong file. If it is a slideshow, `og:image` is
only the first of several and the answer is incomplete. `tikwm`, ahead of it
in the chain, is the one that can tell them apart because it reads an explicit
field — which is exactly why `_pin_og` is allowed to keep its `og:image`
fallback: `pinterest_api` now runs first and is authoritative.

`tests/test_pinterest.py` carries the audit as a standing rule rather than as
a thing somebody once read: each of the five safe providers is checked to
still decide the way it decides, and `tnktok` is checked to have no photo
fallback at all.

#### SECURITY.md and CONTRIBUTING.md

Two of GitHub's six community-checklist files, added to the four public
repositories. The other four are argued down in `LICENSING.md`, which is
internal.

`SECURITY.md` exists because somebody who finds a way to make a bot hand one
person another person's file needs a way to say so that is not a public
issue. It says what is in scope, what is not, and points at `PRIVACY.md` for
what a leak would actually expose.

`CONTRIBUTING.md` is inverted, and that is the point: these repositories are
mirrors that `publish.ps1` overwrites wholesale on every release, so a pull
request against one cannot be merged. Without a file saying so, the honest
outcome is somebody spending an evening on a PR that gets closed. It points
at issues, which are the one thing on the repository a publish does not
overwrite.

#### The licence, which is not a checkbox

**v1.5.0 adds PyMuPDF, and PyMuPDF is AGPL-3.0 or a commercial licence from
Artifex.** The five public repositories have no `LICENSE` file at all, which
means all rights reserved — a coherent position until the published source
combines with AGPL code, which is what this version does.

No licence file has been added, because a licence grant is irrevocable for
the version it ships with and that is the owner's call rather than a default.
`LICENSING.md` lays out the three options, the licences of every other
dependency, and a recommendation (AGPL-3.0 across all five). **It is worth
settling before the next `publish.ps1`.**

### Not done yet

**Asked for, and next:**

- **Move the heavy conversions to free third-party services, with the local
  converter as the fallback.** The owner's words: *"I'd actually prefer the
  bot to rely on other free services to do heavy work, like conversion, if
  possible, and make the native conversion the last option, in case of
  unexpected situations that might happen between 3rd party services and the
  bot."* Everything in this version was built local-first, so this is a
  backend added underneath `convert()` rather than a rewrite: a remote
  attempt, a timeout, and the existing code path when it fails. The reason
  is the bill — a container sized for the worst conversion is paid for every
  minute of every day, including the minutes it is idle.

  **`PRIVACY.md` has to change first, in the same release.** It currently
  states that files are converted on the machine the bot runs on and are
  never handed to an outside service. Shipping a remote backend without
  rewriting that sentence would make a published privacy claim false — the
  same class of mistake as the "message contents are never stored" claim
  AnonBot broke in v1.4.0. Every processor has to be named, with what is sent
  and how long they keep it, and the service's own terms have to allow
  automated third-party use by a bot that charges Stars.

- **OCR**, below, is the same decision: a free API first, `tesseract` in the
  image only if there is no service worth relying on.

- **Sleeping the quiet bots.** §11 measures it; nothing acts on it yet. The
  work is webhooks, a pool that closes when idle, and a family bus where
  ParentBot wakes a child rather than the child polling. Worth doing one bot
  at a time, and not AnonBot.

**New, from this line:**

- **No LibreOffice, so DOCX to PDF is reflowed rather than rendered.** §1. It
  would also bring XLSX and PPTX to PDF with real layout. The cost is about a
  gigabyte of image and a much slower cold start on every deploy of a bot
  whose whole point is being small. Worth revisiting if document conversion
  turns out to be what people actually use this for — the usage is in
  `star_transactions.item`, which records every pair.
- **No OCR.** A scanned PDF converts to pictures and refuses "to text" with a
  sentence explaining why. The nichest thing on this list and probably the
  most wanted.

  Measured rather than guessed, because the question was asked directly: a
  local `tesseract` costs **nothing resident** — it is a subprocess, like
  ffmpeg, so it is not in the container's memory except while it runs — and
  Railway bills memory per GB-second, so a 200 MB process for five seconds is
  $0.0000039. A thousand OCR pages a month is under two cents including CPU.
  The constraint is not money, it is the **peak**: two or three concurrent
  Tesseract runs is 300–600 MB on top of a container that idles around 180 MB,
  and that is an OOM kill rather than a slow reply. So a local OCR needs a
  one-at-a-time lock and a page cap, not a bigger plan.
- **No PDF to DOCX**, which needs a layout-reconstruction library well past
  anything here.
- **No archives.** ZIP, TAR and 7z between each other looks like it belongs,
  and it is the one category deliberately left out: the expansion ratio of an
  uploaded archive is unbounded, so it needs its own uncompressed-size guard
  rather than the ceilings in §6, and "convert" is a strange word for it.
- **No camera raw** (CR2, NEF, ARW, DNG). `rawpy` is a wheel and the pairs are
  obvious; it is another 30 MB for a format most people do not send over
  Telegram.
- **The price is still by size, not by work.** §6 caps the work instead, which
  is the safe half of the problem. The unsafe half is a user who pays 30 stars
  for a 15 MB CSV that converts in 40 ms. Nobody has complained; worth a look
  if the ledger shows it happening.
- **`/formats` for the image category is a long message.** Twenty-four source
  formats and fifteen targets, in one blockquote. Fine on a phone, close to
  the edge.

**Carried over, still open:**

- **The operator line.** `OPERATOR_CONTACT` is still an empty placeholder in
  all four public `.env.example` files.
- **`ACTIVITY_RETENTION_DAYS` is still 90.** Published, not yet argued down.
- **No data export.** `/deletemydata` reports a count and `/privacy` lists the
  categories; a dump of six settings rows would be ceremony.
- **The profile photos are still not uploaded.** `logo.png` is generated and
  committed; *Edit Botpic* is not on the Bot API and has to be done once by
  hand in BotFather. `DEPLOY.md` says where.
- **The command menu is English-only**, in a trilingual family.
  `set_my_commands` takes a `language_code`. About 120 short strings, and now
  121 — `/formats` is in the same English-only list.
- **Nothing uses the description picture.** `DESIGN.md` §2.
- **`parent_bot/family_db.py` builds identifiers from a backup manifest.** No
  Telegram command reaches it; listed in `test_sql_safety.py`'s `ALLOWED`.

---

<a id="v1.6.0"></a>

## v1.6.0 — everything since 1.5.0

A minor release, so this file covers the whole line since the previous minor.
Several pieces of work, most of them unrelated to each other:

1. **The private bot was renamed.** ParentBot became ManagerBot, across six
   of the seven naming layers, with its Telegram username deliberately left
   where it was. Everything from the next heading down — it began life as a
   `1.5.1` folder and was folded in here when that folder became `1.6.0`,
   which is why those sections read as a release of their own.
2. **AnonBot delivered replies onto the wrong message.** An answer to
   anything other than the newest message in a conversation arrived on the
   other side attached to whatever *had* been said last. §**The reply that
   landed on the wrong message**.
3. **Credit balances**, replacing an invoice per job: every Stars payment
   becomes ⚡ credit, spent on conversions, with a person's first 1,000 Stars
   earning more and the extra expiring. The sections from §**Credit balances,
   and what paying buys** onwards were written as the work went and describe
   pricing that was later replaced; §**The owner's answers, applied** is the
   version that ships.
4. **A ConvertBot queue** that cannot lose anybody's credit, with each
   conversion in a process of its own. §**Conversions that cannot lose credit,
   a queue, and paying more for less**.
5. **AnonBot `/export`**, a ManagerBot **`/backup` that can be restored
   from**, **`/paysupport`** in every bot that takes Stars, command menus in
   the language a person chose, and the Pinterest video pin. §**The owner's
   answers, applied**.

Uncommitted and unpublished. Nothing here has been pushed and no bot has been
redeployed.

### What was asked for, and why it is the better half of the trade

`DESIGN.md` carried a pending rename, inherited from `ARCHITECTURE.md` and
`DEPLOY.md` and printed as a `todo` line by `tests/test_naming.py` on every
run: the private bot was **ParentBot** and its Telegram username was
**@mumu_manager_bot**, so move the username to `@mumu_parent_bot` and the
family lines up again. Free, on the face of it — the bot is private, has one
user, and nobody holds a link to it.

The owner flipped it: rename everything else instead, and leave the username
where it is.

That is the better direction, and the reason is the one that keeps AnonBot's
`@mumu_chat_bot` frozen. **A username is an address; every other layer is a
label.** An address is the one thing outside this repository that something
can depend on — a saved chat, a link, a bookmark, an autocomplete entry the
owner has been tapping for months. The other six layers are text in files, and
text in files is what a rename is cheap on. Renaming the six to fit the one is
the direction with nothing at risk in it.

It also leaves the family with **one** naming exception rather than two, which
is what an exception list is for: `@mumu_chat_bot` is a genuine mismatch that
cannot be fixed, and it now sits alone rather than next to one that could have
been.

### The seven layers

`ARCHITECTURE.md`'s naming table is the definition. Six of the seven moved:

| layer | was | is |
|---|---|---|
| family name — docs, `/status` | `ParentBot` | `ManagerBot` |
| bus id (`BOT_NAME`, `family.*` keys) | `parentbot` | `managerbot` |
| folder | `parent_bot/` | `manager_bot/` |
| Postgres schema | `parent_bot` | `manager_bot` |
| environment prefix | `PBOT_` | `MBOT_` |
| GitHub repository | `ParentBot` | `manager-bot` — **pending**, below |
| Telegram `@username` | `@mumu_manager_bot` | unchanged, which was the point |

663 replacements across 70 files, plus the folder move. `tests/test_naming.py`
is what makes that a rule rather than a sweep: it derives all seven from the
canonical name and fails if any one of them drifts.

### The part that is not text

Two of those layers are rows and objects in the shared Postgres database, and
no amount of editing this repository moves them.
`scripts/rename_to_managerbot.py` is new and does it:

1. `ALTER SCHEMA parent_bot RENAME TO manager_bot`. A catalogue update —
   tables, rows, indexes and sequences travel with it, and it does not copy
   data.
2. `parentbot` → `managerbot` in every family table that names a bot. The
   columns are **discovered from `information_schema`** rather than listed, so
   a table added later is covered without anybody remembering the file. That
   is the same reasoning the erase tests use, and it is why `usage_samples` —
   which did not exist when this problem was first imaginable — needed no
   thought.
3. The `family.settings` keys that carry a bot id inside them:
   `maintenance:parentbot`, and the restart note `lifecycle.py` leaves.
4. **`family.events` message text is left alone.** A row saying "ParentBot
   started" is what happened. Rewriting it would make the log a worse record,
   not a better one.

`--dry-run` does all of it in a transaction and rolls back. Running it twice
reports zero of everything.

**Skipping it is the failure mode worth naming.** ManagerBot would start, find
no `manager_bot` schema, create an empty one — so its own tables look wiped —
while `family.heartbeats` gained a second row and `/status` reported six bots:
five running and a `parentbot` silent since the deploy. Nothing lost,
everything in two places. `DEPLOY.md` §8 has the order: migrate, set the
Railway variables, then redeploy.

### What was deliberately not renamed

- **`CHANGELOG.md`'s historical entries.** The bot was called ParentBot when
  those were written, and a changelog that says otherwise is a changelog that
  lies. One note at the top records the rename and its date; everything below
  it stands as written. The same goes for the `CHANGES_v*.md` files in the
  older version folders, which are immutable anyway.
- **`family.events` rows**, above.
- **`parent` and `parentbot` as ManagerBot aliases.** `resolve_bot()` still
  answers to both, so `/logs parent` works. They are what the owner has been
  typing for months, and an alias costs one list entry.
- **The `@username`**, obviously.
- **`.parent`, `-Parent`, "the parent window".** Three unrelated uses of the
  word, in `pathlib` calls and PowerShell. Checked one at a time rather than
  swept.

### Two things the sweep got wrong, and how they were caught

A blind replace across 70 files does not know what a sentence means, and two
came out backwards:

- `DEPLOY.md` §8 became an instruction to rename `@mumu_manager_bot` to
  `@mumu_manager_bot`, and `ARCHITECTURE.md` said the same. Both passages were
  *about* the rename that has now been flipped, so both were rewritten by hand
  rather than patched.
- `publish.ps1` started pointing at a repository called `ManagerBot`, which
  does not exist — the repository on GitHub is still `ParentBot`. It points at
  the real one again, and `PENDING_RENAMES` in `tests/test_naming.py` records
  `ParentBot → manager-bot` so the `todo` line keeps printing until somebody
  does it.

The tests caught a third. `tests/test_ping.py` builds a trace dictionary whose
key the sweep had no reason to touch, while `_render_ping` had been renamed to
read `to_manager`. One `KeyError`, in the one suite that imports the bot for
real — which is the argument for that suite existing.

### Tests

| suite | result |
|---|---|
| `tests/run.py` | 40/40 |
| `tests/test_naming.py` | all seven layers, plus the `todo` for the repository |
| `tests/test_sql_safety.py` | 0 failures — the new script is in `ALLOWED`, with its reason |
| `tests/test_shared_copies.py` | the four copied files still hash to one value each |

`scripts/rename_to_managerbot.py` declares its constants one per line rather
than unpacked in pairs, because `test_sql_safety.py` reads a module's ALL_CAPS
assignments to decide which f-string holes are the code's own and only sees a
plain `NAME = ...`. That is the test being right about a real rule, not a
workaround for it.

### Files changed

| file | what |
|---|---|
| `parent_bot/` → `manager_bot/` | the folder, moved with `git mv` |
| `*/family_link.py`, `*/lifecycle.py`, `*/shared_features.py` | the name in comments and docstrings; still byte-identical across all five |
| `manager_bot/bot.py` | `BOT_NAME`, `USERNAME_ALIASES` and the comment above it, the `to_manager` trace key |
| `manager_bot/.env`, `*/.env.example` | `MBOT_*`, and `DB_SCHEMA=manager_bot` |
| `scripts/rename_to_managerbot.py` | new; the database half |
| `scripts/make_logos.py`, `manager_bot/logo.*` | the `manager_bot` key, and both files regenerated |
| `publish.ps1`, `start_all.ps1`, `stop_all.ps1`, `db_backup.ps1`, `db_sync.ps1`, `docker-compose.yml` | the folder name |
| `ARCHITECTURE.md`, `DEPLOY.md`, `DESIGN.md`, `README.md`, `HOSTING.md`, `UPDATES.md` | the name, plus the two sections rewritten by hand |
| `tests/*` | the name; `PENDING_RENAMES`, the `MBOT` prefix, `to_manager`, one `ALLOWED` entry |
| `*/shared_features.py` | `publish_commands()` sends one menu per language; `_menu_in()` |
| `*/i18n.py` | `COMMAND_MENU` — 48 commands × 2 languages |
| `scripts/make_logos.py` | the renderers work on any frame; `bounds()`, `hero()`; the `width` shadowing bug |
| `*/hero.svg`, `*/hero.png` | new; 640×360, what BotFather's Description Picture wants |
| `tests/test_menu.py` | the translations, and Telegram's own name and length limits |
| `tests/test_logos.py` | a second rule set for the landscape card, with its own feature floor |
| `tests/stubs/telegram/__init__.py` | `BotCommand.command` and `.description` |
| `DEPLOY.md`, `DESIGN.md` | the description picture, and the plan closed out |

### The design plan, implemented

`DESIGN.md` had an ordered list of five things. One was already done, one was
this version's rename, and of the three left, two were built and one was
withdrawn on contact with the code. The list is now empty.

#### The slash menu, in three languages

The bots answered in English, Uzbek and Russian everywhere except the one list
somebody reads before they have understood anything. `set_my_commands` takes a
`language_code` the same way `publish_profile()` has been sending the profile
text three times since v1.4.0, so `publish_commands()` now sends the menu four
times per bot: untagged, `en`, `uz`, `ru`. Telegram picks the one matching the
client and falls back to the untagged list for a fourth language.

**English stays in `BOT_COMMANDS`**, in each bot's `bot.py`, where the menu can
be read by reading the file. Uzbek and Russian live in a new `COMMAND_MENU`
table in each `i18n.py`, keyed by command name: 48 commands across the four
bots, 96 strings. Two places rather than one, deliberately — a menu description
is a 256-character label for Telegram's own UI, not chat text, and mixing it
into `STRINGS` would put it through the layout helpers and the policy tests
that have nothing to say about it.

The fallback is per command, not per menu. A command with no translation keeps
its English description rather than dropping out of that language's list, which
is the failure mode worth designing against: a half-translated menu is a menu
with commands missing, and a missing command reads as a bot that cannot do the
thing.

**Four commands are deliberately not translated**, and they are the four that
look most like they should be. `/en`, `/uz` and `/rus` are each described in
the language they switch *to*, and `/language` is written in all three at once.
They are the way back for somebody who picked the wrong language, so they have
to stay legible whatever the menu is currently in — translating them would give
a Russian client three lines of Russian, one of which is the only exit. The
test fails if a translation ever appears for one of them, because that is
somebody disagreeing with the rule without saying so.

The owner's scoped menu stays untagged English, along with all admin output.

`tests/test_menu.py` grew the matching half: a command in `BOT_COMMANDS` with
no `uz` or `ru` entry fails, an entry naming a command that no longer exists
fails, a translation of one of the four self-describing commands fails, an
owner-only command appearing in a translation fails — and both lists are now
checked against what Telegram will actually accept: names matching
`[a-z0-9_]{1,32}`, descriptions of 1 to 256 characters. A rejected list is a
`set_my_commands` that fails at startup and a menu that silently stays on the
previous version, which is invisible from the logs and obvious to a user.

`tests/stubs/telegram/BotCommand` gained `.command` and `.description`. The
real class has both, `publish_commands()` reads them back off a menu it was
handed in order to translate it, and a stub that swallowed them turned that
into an `AttributeError` that no test would have reached.

#### The description picture

The other thing an empty chat shows. Above the Start button Telegram can
display a landscape picture as well as the description text; it is the largest
single piece of screen a stranger sees before pressing anything, and it was
blank on all five.

`scripts/make_logos.py` now writes `hero.svg` and `hero.png` beside the logo —
640×360, the same ground, the same glyph, generated from the same geometry, so
a mark that changes changes both. There is no second design per bot, because
the mark is what a person is being asked to recognise and two of them would be
two things to keep in step.

**No text on it.** The description sits directly underneath in the reader's own
language; a picture repeating it in English would be worse than one that only
says which bot this is. It would also need a font, and this file exists on the
rule that nothing in it is traced, licensed or borrowed.

**The glyph is not cropped, and that is where the plan was wrong.** `DESIGN.md`
proposed "the same glyph oversized and bled off one edge", which is the
ordinary way to make a banner feel confident. It is wrong for these five. Two
of the marks are made of separated parts — ManagerBot's ring of four dots,
ConvertBot's two passing arrows — and an edge through either stops it being
that mark rather than cropping it. A rule that flatters a square and destroys a
constellation is not a rule. The glyph is whole, about seventy per cent of the
frame height, set right of centre, and the open ground on its left is the
composition.

Making the renderers work on a frame that is not square turned up a real bug in
code that had shipped twice. `to_png`'s `ring` branch unpacks a stroke width
into a local called `width`, so a frame dimension by the same name was
overwritten the moment a logo containing a ring was drawn — and the box filter
then ran over 32 columns instead of 640. It only showed up because regenerating
the avatars produced two files under a kilobyte where there had been six and
seven. The frame dimensions are `frame_w` / `frame_h` now, with the reason
written next to them. `_fill_poly` and `_fill_disc` had the same square
assumption in their x bounds, where `len(canvas)` was standing in for a row
width.

`tests/test_logos.py` gained a second rule set, because a landscape card is not
cropped to a circle and the avatar's rules do not transfer:

- nothing touches an edge — the opposite of the avatar's circular crop
- the glyph carries the frame: at least 60% of its height
- there is open ground to its left, which is the composition rather than a
  coincidence
- the card's ground matches the avatar's
- the PNG is 640×360, and both files still match the generator

Its feature floor is its own. `MIN_FEATURE = 30` exists so a stroke survives 34
pixels in a chat list; a description picture is drawn at roughly the width of
the chat, and the card's glyph is *smaller* in absolute units than the avatar's
because it is fitted into 360 of height rather than 640. Inheriting the avatar's
number failed two bots on a rule that does not apply to them. `HERO_MIN_FEATURE`
is 16, which lands at about 11 real pixels.

Uploading is still manual, like the avatar: *Edit Bot* → *Edit Description
Picture*, `hero.png`. `DEPLOY.md` has it in the BotFather list.

#### The spoiler, withdrawn

`DESIGN.md` proposed one use for `<tg-spoiler>`: AnonBot showing a conversation
excerpt in `/conversations`, on the reasoning that a list of openings from
strangers is the one screen somebody might not want visible over their
shoulder.

**There is no excerpt, and there cannot be one.** AnonBot stores no message
contents — that is a published claim in its `PRIVACY.md` and the reason the
"send it anyway" button was removed in v1.4.0. `/conversations` lists numbers
and timestamps as buttons and holds no text at all. Building the spoiler would
have meant first storing the thing the bot promises not to store, to have
something to hide.

Nothing else in the family displays content a spoiler would suit, so the item
is withdrawn rather than relocated.

That is two of three proposals in that list withdrawn on contact with the code
— this one and the link labels — which is worth recording about the file
itself. A design list written from the outside is a list of guesses until each
one has been checked against what the bots actually do.

### Also in this folder: DESIGN.md caught up

Three corrections owed from the session that wrote it:

- The five profile photos **are uploaded** (2026-09-09), so the item that had
  been deferred across two versions is closed, and the circle is the first of
  the four surfaces finished end to end.
- Item 5, the username rename, is what this version flipped.
- **"Link labels instead of bare URLs" is withdrawn.** It should never have
  been on the list: `_policy_footer()` had already decided the other way, on
  purpose, and its reason is the better one — *a privacy notice is the wrong
  place to hide where a link goes behind a word.* The plan had not read the
  code it was proposing to change.

Its ordered list is now empty: items 2 and 3 are built above, item 4 is
withdrawn, and what remains under "What is actually left" is two operational
jobs rather than design decisions — uploading the five cards, and the
repository rename.

### The reply that landed on the wrong message (2026-09-10)

Reported as a sequence rather than as a theory, which is why it was
findable. A guest writes; the owner answers; the guest sends a sticker; the
owner answers that, and the quote is right; the guest writes again — and now
the owner answers **the sticker**, and the guest sees that answer hanging
under their latest message instead.

It is a real bug, it was in both directions, and it had been there since the
relay was written.

#### Why the quote was always the newest message

A conversation exists twice — once in each of the two chats — and Telegram
numbers messages **per chat**. So "the message being answered" has a
different id on each side, and something has to translate between them.
Nothing did. `anon_conversations` carried `last_owner_msg_id` and
`last_follower_msg_id`, the newest bubble of that conversation in each chat,
and both delivery paths used them as the anchor for the outgoing copy:

```python
reply_to_message_id=conv.get("last_follower_msg_id")   # owner -> guest
reply_to_message_id=active.get("last_owner_msg_id")    # guest -> owner
```

That is correct exactly as long as everybody answers the last thing said,
which is why it survived so long: the quote is right on every turn of an
ordinary back-and-forth, and it is right on the step immediately after a
sticker too. It goes wrong on the one gesture a threaded inbox exists for —
scrolling up and answering something older. The bot resolved *which
conversation* from the reply correctly the whole time; the message was
always delivered to the right person. What it discarded was *which message*.

**It was never a leak.** The anchor was always the newest bubble of that same
conversation, so a wrong quote stayed inside the pair it belonged to and
could not surface one guest's message in another guest's chat. The cost was
that a conversation held out of order became unreadable at the far end,
which for a bot whose whole purpose is holding several conversations in one
chat is not a small cost.

#### The pairing, recorded rather than worked out

`anon_relay` gains `peer_message_id`: for every row, the message in the
*other* chat that it is the same message as.

It is free to fill in. `record_relay_and_touch` already receives both sides'
new ids and both anchors in one call, and the anchor for a side is by
definition the bubble the other side would quote — so each owner-side row
takes `last_follower_msg_id`, each follower-side row takes
`last_owner_msg_id`, and the insert stays one statement on the hot path of
every message. `get_relay_row` reads the pairing out of the row
`handle_message` was already fetching, so a reply still costs the same one
round trip it did before.

**It cannot be worked out after the fact, which is the whole reason it has
to be stored.** The two chats hold different numbers of bubbles for the same
conversation: an uncaptioned sticker is a header line *plus* a sticker on the
receiving side against one message on the sending side, a message too long
for one bubble is split, an album is one send per photo. Lining up what is
already in the two chats and pairing them off would be wrong from the first
sticker onwards. So old rows are left NULL and fall back to the previous
behaviour rather than being backfilled with a guess — every live conversation
has rows like that on the day this ships, and `_anchor_for` is the two-line
function that makes "no pairing recorded" mean "do what you used to do".

Three things resolve through the same column, and it is worth naming them
because they used to be three different amounts of wrong:

- **An answer to an older message** arrives on that message.
- **An answer to something you wrote yourself** — allowed on both sides
  since v1.4.0 — arrives on the copy of it in the other chat.
- **An answer to a bubble that is only half of a relay** — the header line
  above an uncaptioned sticker — arrives on the sticker, because the header
  and the media share a pairing and it points at the media.

#### Group P, and eight content types the simulator could not send

`tests/anon_scenarios.py` grows a group: 40 checks on which bubble a
delivered answer hangs under. Reverting the fix fails 14 of them.

It runs for **every** content type a person can send, and that turned out to
be the part `sim.py` could not do: its `incoming()` built a photo for
anything that was not text, so `guest.photo()` was the only media a scenario
could produce and the case that breaks was unreachable. There is now a
`sim.MEDIA_PAYLOADS` — photo, sticker, animation, video, voice, audio,
document, video note — and `Person.media(kind)`. The distinction that matters
is not the file type but **whether Telegram allows a caption on it**: a
sticker and a video note do not, so a relayed one is two bubbles, and from
that moment the two chats are permanently different lengths. That is the
state in which every positional shortcut fails, and it was not reachable in
a test before.

The group also asserts one thing as SQL over the whole table rather than as a
scenario: **no pairing points outside its own conversation.** That is the
version of this bug that *would* have been a leak, and one statement settles
it for every row the group wrote instead of for the handful a scenario
thinks to check.

#### Five checks that had been failing on correct behaviour

Running the suite to get a baseline turned up three failures in
`anon_scenarios.py` and two in `anon_flow.py`, all pre-existing and none of
them a bug in the bot. v1.5.0's refusal pass rewrote the wording of three
messages, and five assertions were still looking for the old phrases —
"nothing was sent" against a sentence that now reads "your message was not
sent", and "don't have that message on record" against one rewritten
entirely.

They are fixed by comparing against `i18n.t()` where the claim is the
identity of the message, and against the surviving phrase where the claim is
about the wording. A suite with permanent failures in it is worse than no
suite, because it teaches everybody who runs it to skim the last line.

### Credit balances, and what paying buys (2026-09-10, in progress)

**Not finished.** The balance itself is built and tested; the two paths that
put money in and take it out are not yet wired, so nothing in a user's chat
has changed. What is done, what is deliberately shaped the way it is, and
what is left are all below, because this one touches money and published
promises and is the wrong thing to leave half-described.

#### What was asked for

> "I want the users who donate stars to have it recorded in their balance,
> instead of just saying thanks. […] someone can recharge 100 stars
> beforehand and use it eventually by converting, instead of making
> individual transactions each time."

> "Give admin special access with no limits for converting and downloading,
> but i want it to be the same with everyone else, just give my account 10k
> stars balance."

> "I also want an easier way to just see/edit someone balance instead of
> going to supabase, maybe through a command in manager bot."

> "any star usage should be rewarded […] I recharge/donate 100 stars but I
> get 200 or 1000 stars worth of usage. the first payment for converting
> should give similar rewards."

> "the reply and prompt to donations should include the fact that its going
> to be added to their balance but cannot be withdrawn."

#### A balance is credit, and credit is not Stars

This is the decision everything else follows from, and it is forced by the
multiplier rather than chosen. If paying 100 Stars puts 200 in a balance,
then a balance shown as "⭐" claims a person holds 200 Telegram Stars — and
that is false twice over: it is more than they paid, and there is nothing in
this family that could pay it back out. Telegram Stars can be refunded by
the seller and, for some purchases, reclaimed by the buyer; a promotional
balance cannot be either.

So credits are their own unit, written **⚡**, and **⭐** means real Telegram
Stars and nothing else — in the code, in ManagerBot's output, and (still to
do) in all three translations. That is the honest form of the disclosure the
owner asked for: one glyph doing the work of a paragraph of small print,
with the sentence saying it outright underneath.

`family.star_balances` carries `lifetime_stars_paid` as a column of its own
for the same reason. It is the only figure here denominated in money, and it
is the one to ask "has this person ever paid" — credit can arrive as a grant
or a welcome bonus, and counting those as a payment is how a gift turns into
a claim that somebody donated. DownloaderBot's donor multiplier reads that
question today and must go on reading the money, not the credit.

#### The arithmetic, in one place

`family_link.py`, because it is copied byte-identical into all five bots and
the whole point of the balance is that it is one wallet: Stars put in
through StickerBot are spent in ConvertBot, and neither bot owns them. Five
per-bot wallets would be five things to top up separately, which is worse
than the per-job invoice it replaces.

| | |
|---|---|
| `credit_for_stars` | the rate, in one function, so raising it is one change |
| `topup` | the whole money-in path: rate, then the first-payment bonus, returns what to say |
| `spend_stars` | conditional debit, refuses atomically if the balance will not cover it |
| `move_stars` | unconditional, may go negative — money in, grants, corrections |
| `refund_topup` | claws back at the rate it was bought at |
| `set_star_balance` | put it at exactly N, recording the movement it took |
| `grant_stars_once` | credit once per (key, person), ever |
| `star_ledger_for`, `star_balance_overview` | what ManagerBot prints |

Two properties are worth naming because they are the ones that would be
"almost right" if written the obvious way:

**Spending is one statement.** `WHERE balance >= amount` sits on the UPDATE
itself, so two conversions started in the same second cannot both spend the
last of a balance. A read, a decision and a write would be correct almost
always, which for money is the wrong kind of correct.

**A refund is applied at the multiplier.** 100 Stars bought 200 credits, so
refunding those 100 Stars takes 200 credits back out, and the balance is
allowed to go negative to do it. Refunding at face value would leave the
bonus behind as free usage every time somebody paid and changed their mind —
a hole rather than a promotion. A negative balance is not an error state; it
means no more paid work until it is positive again.

`family.star_ledger` records every movement with `balance_after`, which is
redundant on purpose: it makes the ledger self-checking, so a bug that
debits without recording is visible rather than merely suspected.
`tests/credit_scenarios.py` walks the whole ledger from zero and asserts it
adds up to the balance.

#### The rate, and the two numbers that are policy rather than fact

The owner named a range — "200 or 1000" — which is a tenfold spread, so it
is a pricing decision rather than a specification and it is an environment
variable:

| | default | what it means |
|---|---|---|
| `FAMILY_TOPUP_MULTIPLIER` | `2` | 100 ⭐ buys 200 ⚡ |
| `FAMILY_FIRST_TOPUP_BONUS` | `100` | a one-off welcome on a first payment |
| `FAMILY_ADMIN_SEED_STARS` | `10000` | the owner's own balance, seeded once |

`2` is the conservative end deliberately. Every price in the family is
quoted in credits, so raising the multiplier is the same act as cutting
every price — at 10 the current tiers make 100 ⭐ buy about 125 conversions
of a 3 MB file, against roughly 12 today. That may well be right, but it is
a decision about what the bots are for, not a rounding choice, and one env
var away is the correct distance for it.

The bonus is granted once per **person**, across the whole family, through
`grant_stars_once` — so two payments landing in two different bots in the
same second cannot both be somebody's first. It fires on a first top-up of
any kind rather than specifically on a first conversion payment, which is
the same thing in effect and does not need a second mechanism.

#### The owner's 10k, and why it is not a bypass

`/balance` in ManagerBot, and a seed at startup.

The instruction was "no limits … but the same with everyone else", and those
two only fit together one way: a large number in the same column a
stranger's balance is in, spent through the same code. A bypass would work
perfectly while the thing it bypasses was broken, and the owner would be the
last person to find out rather than the first.

`seed_admin_balances()` runs in ManagerBot's `_post_init` and is idempotent —
`grant_stars_once` stakes its claim with an `INSERT … ON CONFLICT DO
NOTHING` in `family.settings`, so it is safe on every restart and safe from
five processes at once. To do it again on purpose, delete the settings row
it names. It is in startup rather than in a script the owner has to remember
because a privilege installed by hand is one that is missing on a fresh
database exactly when somebody is working out why the bot is refusing them.

#### `/balance` in ManagerBot

```
/balance                  the family-wide picture: outstanding credit, the
                          rate, and the largest balances
/balance <user_id>        one person: balance, lifetime figures, last 12
                          movements with what each one was for
/balance <user_id> +100   give them 100 ⚡
/balance <user_id> -100   take 100 away (may go negative)
/balance <user_id> =0     set it to exactly 0
```

Three explicit forms rather than a bare number, because a bare number is
ambiguous in the one direction that matters: `/balance 12345 500` could
plausibly mean "give them 500" or "make it 500", and the two differ by
however much they already had.

Every change goes through `family_link`, so an owner's correction writes the
same kind of ledger row a top-up does — with `by admin <id>` in the detail —
and also lands in `family.events`, so it shows up in `/events` beside
everything else that happened that night. There is no path that sets the
number without recording it.

`outstanding` in the family-wide view is the number worth watching: it is
credit people are holding and have not spent, which is work the family still
owes and will have to pay to do. A multiplier makes that liability larger
than the Stars taken for it, which is exactly why it is on the first screen.

#### `tests/credit_scenarios.py`

35 checks against a real Postgres: the rate, the bonus firing exactly once,
an atomic refusal when the balance will not cover a job, a refund clawing
back at the purchase rate and going negative, a correction that moves the
balance without touching what somebody appears to have paid, the grant that
cannot happen twice, and the ledger adding up from zero row by row.

It fixes the multiplier and the bonus in its own environment rather than
reading them, because these assertions are about the arithmetic and a suite
whose expected numbers move with a deployment's pricing tells you nothing.
It uses two scratch user ids and cleans up after itself rather than
truncating shared family tables.

#### Two published promises this breaks, and both have to move in this version

Neither is a detail and neither can ship after the code.

**`TERMS.md` in all four public bots says `/donate` "buys nothing, unlocks
nothing, and grants no priority".** After this it buys usage. That sentence
becomes false the moment the money-in path is wired, and the in-bot
`/terms` says the same thing in three languages. It has to be rewritten in
the same release — and the word "donate" is wrong too: this is prepaid
credit, so the flow should read as a top-up, with `/donate` kept as an alias
because it is what people will type.

**`PRIVACY.md` in all four lists what is held, and a balance and a
per-person money ledger are new categories.** They also outlive
`/deletemydata` — for the reason the Stars ledger already does, that a
payment record has to survive the payer asking to be forgotten — so the
erasure section has to say so rather than implying the erase is total.
`tests/erase_scenarios.py` should be extended to assert that a balance row
is deliberately kept, not accidentally missed.

And one thing worth the owner's attention rather than mine: prepaid credit
that never expires is a liability carried forever, and a multiplier makes it
bigger than the money received for it. `/balance` measures it. Whether it
should expire is a decision, not an oversight, and doing nothing is a valid
answer as long as it is the chosen one.

### The Pinterest pin that is a video and answers with a photo (2026-09-10)

**Diagnosed, not yet fixed.** The owner reported that v1.5.0's Pinterest fix
works on a video pin they found themselves but that a link a friend sent them
still comes back as an image: `https://pin.it/6LNqzHUX0`.

It is a real bug and a different one from the v1.5.0 bug. Established by
running DownloaderBot's own providers against the link rather than by reading
them:

The link resolves in two hops — `pin.it/6LNqzHUX0` →
`api.pinterest.com/url_shortener/6LNqzHUX0/redirect/` →
`www.pinterest.com/pin/1196337405501867/sent/?invite_code=…&sfo=1`. All of
that already works: `net.client()` follows redirects, `_PIN_ID_RE` matches
the `/sent/` form, and `_pin_api` extracts pin id `1196337405501867` and gets
a 200 out of `PinResource`.

**The pin is a video, and Pinterest offers it only as HLS.** `videos` is
`null`; `story_pin_data.pages` has one page whose block carries a video whose
`video_list` has exactly one entry, `V_HLSV3_MOBILE` — an `.m3u8` manifest,
no `.mp4` anywhere. So `_pin_best_video` correctly returns None (it skips
anything not ending `.mp4`, because handing Telegram a manifest uploads a few
hundred bytes of text named `.mp4`) and `_pin_api` then falls through to its
`images` fallback and answers, successfully, with the cover frame.

That is the same shape as the v1.5.0 bug one layer further in: **a provider
that cannot supply the video succeeds with a picture of it, and a provider
that succeeds ends the chain**, so `ytdlp:pinterest` — which handles HLS
perfectly well, that being what it is for — is never reached. The owner's own
test pin worked because most video pins do carry an mp4; this one does not.

The fix is not one line, which is why it is written down rather than rushed:

- `_pin_api` must **refuse** when a pin has a video block but no usable mp4,
  rather than describing it with `images`. Refusing is right for the same
  reason it was right for `tnktok`: if the pin is a video, the cover image is
  the wrong file.
- `_pin_og` has the identical trap and cannot see its way out of it. It found
  the same cover frame, and it is *allowed* its `og:image` fallback only
  because `pinterest_api` runs first and is authoritative. Once the API
  provider correctly declines, og runs next and answers with the photo before
  yt-dlp is reached. There is no `og:video` on the `/sent/` page for it to
  key off, so the ordering has to carry the signal: a "this pin is a video I
  cannot fetch" failure from the API provider has to skip the og provider,
  not merely fall past it.
- Handing the `.m3u8` on as a `MediaItem` is the tempting shortcut and is
  wrong — the download path fetches a URL with httpx, and a manifest is not
  the file.
- `tests/test_pinterest.py` already holds the standing audit that each of the
  five safe providers still decides the way it decides. This case belongs
  there: a pin whose only video is HLS must reach yt-dlp, and neither
  `_pin_api` nor `_pin_og` may answer it with a photo.

### Money in, and paying without paying (2026-09-10)

The second half of the credit work: a payment now becomes credit, there is a
command to look at it, and TestBot can exercise the whole path without
anybody's Stars being charged.

#### "Contribute to hosting", because that is what it is

The invoice said **"Buy the bot a coffee ☕"** and the description called it
"a one-time voluntary donation". Both were true of the old behaviour and
became false with this version: the payment now buys spendable credit, and an
invoice that says *donation* over a purchase is describing the wrong thing —
in Telegram's own confirmation sheet, which is the last thing somebody reads
before paying.

So the title is **"Contribute to hosting"**, the label is "Hosting
contribution", and the description says what the money does *and* what it
buys: `Goes towards what this bot costs to run, and adds {credited} ⚡ of
credit to your balance for conversions and downloads.` The credited figure is
computed for that specific amount rather than described in general, because
the multiplier is the part somebody would otherwise have to be told about
afterwards. All three languages, all four public bots.

#### One function turns money into credit

`_credit_topup()` in `shared_features.py`. Both paths go through it — a real
`successful_payment` and the sandbox — so what a tester sees and what a payer
sees cannot drift, which for a thing that hands out credit is the drift that
matters.

It always names three things: what was charged, what was credited, and what
the credit can and cannot do. The third is the owner's instruction and it is
not decoration — credit is worth more than the Stars paid for it and cannot
be paid back out, so a message that only said "thank you for 15 ⭐" would be
describing a donation rather than a purchase. `credit_cannot_be_withdrawn` is
its own key, appended to the thank-you *and* printed by `/balance`, so the
sentence appears wherever the number does.

**Fiat still only gets the thank-you.** The rate is defined against Stars,
and inventing an exchange rate from a currency's minor units would be making
up a number. A card donation is still a donation.

#### `/balance`

Registered by all four public bots, not only the ones that charge: the
balance is one wallet for the whole family, so somebody who topped up in
StickerBot has to be able to ask StickerBot where it went. It prints the
balance, the lifetime figures once there are any, the rate, the
no-withdrawal line, and the last eight movements.

The rate and the disclosure print even when the balance is zero. A number on
its own invites exactly the wrong question later — "can I have it back" — and
answering it before it is asked costs nothing.

`tests/test_menu.py` caught the thing that would have shipped broken: the
command menu has been trilingual since earlier in this version, and
`/balance` had an English description and no Uzbek or Russian one. Eight
failures, one per bot per language, from a test that reads source and needs
no database. That is the second time this version that suite has earned
itself.

#### `FAMILY_STARS_SANDBOX`

With it on, a Stars top-up skips Telegram entirely: no invoice, no
precheckout, no `successful_payment` update, nothing charged — and the credit
lands in the balance exactly as a real payment would put it there. It exists
because the alternative for testing the money path is spending real Stars on
every run, and the part worth testing is what happens *after* the payment:
the rate, the first-payment bonus, the ledger row, the number the user is
shown.

Two things make it safe to live in the shared file rather than in a fork of
it:

- **Every message it produces says so**, in the user's own language
  (`sandbox_notice`), so nobody can come away believing they paid.
- **It logs a warning at import**, so a production process that somehow had
  it set would announce it in the first line of its log rather than quietly
  handing out free credit. The ledger records it too — the charge id is
  `sandbox:<payload>`, so a sandbox top-up stays distinguishable from money
  that actually arrived, forever, rather than looking like a payment later
  on.

Fiat is deliberately not covered: a card payment has a provider behind it and
a sandbox for it belongs to that provider.

It is off unless the environment sets it, and the environment that sets it is
TestBot's launcher.

#### Where this leaves the credit work

Money in is done. **Money out is not**: ConvertBot still sends a per-job
invoice rather than spending the balance, so a topped-up balance currently
only grows. That, DownloaderBot's paid override, and the `TERMS.md` /
`PRIVACY.md` rewrites are what remain — all still listed under *Not done
yet*, and the `TERMS.md` sentence about `/donate` buying nothing is now
wrong in two places rather than one.

### Money out, and a first payment that scales (2026-09-10)

Three corrections, all from the owner watching it work and saying what was
wrong with it.

#### The first-payment reward is a multiplier, not a flat 100

`FIRST_TOPUP_BONUS = 100` was a misreading of "multiply the donation amount".
It was also backwards on its own terms: a flat bonus rewards somebody testing
with 15 Stars more, proportionally, than somebody committing 500 — the first
gets +667%, the second +20%. The person taking the bigger risk got the
smaller thank-you.

`FAMILY_FIRST_TOPUP_MULTIPLIER = 2` replaces it. A first payment is credited
at twice the ordinary rate, so at the default it lands at **4 ⚡ per ⭐**
against the usual 2, and 500 ⭐ earns 1,000 bonus credits where 15 ⭐ earns 30.
`topup()` still returns `bonus` separately so the thank-you can name it, and
it still goes through `grant_stars_once`, so two payments in two bots in the
same second cannot both be somebody's first.

The wording moved with it. `balance_rate` said "a first payment adds {bonus}
⚡ on top", which was only ever true for one amount; it now quotes two rates —
"1 ⭐ buys 2 ⚡ — or 4 ⚡ on your very first payment."

#### The price tiers were re-denominated, not cut

Prices are in credits now, and `PRICE_TIERS` was still holding the Star
numbers. Left alone, every conversion would have quietly gone half price the
day credit shipped — and then the first-payment multiplier would have
discounted it again on top. Two cuts, neither of them chosen.

So the table doubled: 8/18/30/55/100/180 ⭐ became 16/36/60/110/200/360 ⚡.
That is the same price in a different unit — a 17 MB file is 60 ⚡, which is
30 ⭐ of real money, exactly what the owner's screenshot showed it costing
before. The reward for paying lives in the rate, where it is visible and
adjustable; the price of doing the work lives in the tiers.

**Multiplier and tiers are one decision wearing two names**, and the comment
on `PRICE_TIERS` now says so: change `FAMILY_TOPUP_MULTIPLIER` and these are
the numbers to revisit.

#### Money out, which also makes the sandbox cover everything

`FAMILY_STARS_SANDBOX` only reached donations, so a conversion still raised a
real invoice for real Stars — which is what the owner hit. Fixing that
properly meant finishing the money-out path rather than special-casing the
invoice, and that was the next item on the list anyway.

`convert_format_chosen` now **spends the balance first**. For anybody who has
topped up once, converting is tapping a format: no payment sheet, no waiting
on Telegram, nothing that can fail between deciding and converting. An
invoice is what happens when the balance will not cover it, not what happens
on every job.

When it will not cover it, **only the shortfall is charged**, rounded up to
whole Stars. Credit already in the balance is credit already paid for, and
asking for it twice is the sort of thing somebody notices once and never
comes back from. The paid Stars then become credit and the conversion is paid
for out of it like any other, so there is one way work gets paid for rather
than two.

In sandbox the shortfall is topped up for free and **the ordinary spend then
runs for real** — so the sandbox exercises the path it stands in for instead
of skipping past it. Nothing in the test bot charges anybody now: a top-up is
free, and a conversion spends free credit through the same code a payer's
credit goes through.

**A failed conversion now refunds to the balance**, which is strictly better
than the Telegram refund it replaces: one database write instead of an API
call that can fail on the failure path of a conversion that has already
failed, and the person can spend it again immediately instead of waiting.
`_maybe_refund` keeps the Stars path for a conversion that was paid by
invoice.

A balance-paid conversion deliberately writes **no** `star_transactions` row.
The spend is already a row in `family.star_ledger`, written by the statement
that took the credit, and the alternative was the old free-conversion row —
which would have recorded that this cost nothing, the one thing it did not.

### Conversions that cannot lose credit, a queue, and paying more for less (2026-09-10)

#### What the owner saw

A 200 MP photo from the owner's Galaxy S23 Ultra (16320 x 9180 at 16:9, 149.8
million pixels, 17.3 MB), sent to TestBot twice. Each time: "Converting... 60 ⚡
spent", then nothing for twenty minutes, `/cancel` saying there was nothing to
cancel, the process at 0 % CPU, and 120 ⚡ gone.

#### What actually happened

Both conversions **succeeded**, in about four minutes each. The result was
over the 50 MB a bot may send through the cloud Bot API, the upload failed,
and the family error handler filed the failure as a flaky connection. The two
lines that said so were in the log the whole time:

    17:40:14  NetworkError: httpx.ReadError                 (upload cut off)
    18:03:48  NetworkError: Request Entity Too Large        (Telegram's HTTP 413)

python-telegram-bot raises a 413 as a plain `NetworkError`, it matched
`TRANSIENT_NETWORK_ERRORS`, and `error_handler` logged "retried by PTB" and
returned. No message, no refund, no alert. Three faults were stacked:

1. **Nothing checked the size of a result before uploading it.** A JPEG that
   size decodes to 450 MB of pixels; as PNG it is 163 MB.
2. **A permanent failure inside a paid handler was treated like a dropped long
   poll.** That one is family-wide, not ConvertBot's.
3. **Conversions ran inside the handler, and every bot handles updates one at
   a time.** A four-minute conversion held the whole bot for four minutes —
   the `Query is too old` crash recorded at 16:12 was the next button tap
   arriving after Telegram's fifteen-second window.

And a fourth, found on the way through: since v1.5.0 **every invoice-paid
conversion was refunded and never converted**. The payment handler looked for
the staged file under `conv_state["path"]`, a key that became `"paths"` when
albums arrived in the same version, so it never found it.

#### The classifier (all four `shared_features.py`, and `monitoring.py`)

A failure with no update behind it is the long poll, and still a blip. A
network error raised while handling somebody's update means that person asked
for something and did not get it, and it is now counted, logged with its
traceback and reported. Anything whose text says the payload is too big is
permanent whatever class it arrives as. ManagerBot's `monitoring.py` carries
character-identical copies of both functions and got them copied across from
`shared_features.py` rather than retyped; `test_shared_copies.py` is what
noticed they had drifted.

#### The queue: `convert_bot/jobs.py`, `convert_worker.py`, `convert_runner.py`

The format button now decides and enqueues, and returns in milliseconds. The
work happens in a task: one conversion at a time per person, in order, and one
at a time across the bot (`CONVERT_MAX_CONCURRENT`), because the container is
sized for one heavy job.

Each conversion runs in **a child process**. That is what makes the time limit
real — a thread cannot be killed, a process can — and it returns a gigabyte of
decoded photo to the operating system the moment it exits instead of leaving
the bot resident at that size. A crash or an out-of-memory kill takes the
worker, not the bot. Verified on Windows: a 3-second limit on a slow 54 MP WEBP
returned at 3.1 s and the process was gone from the task list.

Limits: 600 s for a paid conversion, 60 s for a free one (a free job that ran
to the paid limit is the one case where pricing could not cover hardware).

#### What happens to the credit — decided in one place

- **Taken when the conversion starts**, not when it is queued, so removing a
  queued job costs nothing and no job waits in a queue holding money.
- **Not refunded if the person stops a running conversion.** The Stop button
  says "(no refund)"; the status message said so when it started; the options
  screen said so before anything was tapped.
- **Refunded, with the reason, whenever it ends for the bot's reasons**: time
  limit, crash, result too large to send, send failed, restart. A file the bot
  cannot read is refunded too.
- **The owner is messaged directly by the bot** for every one of those except
  a declined file, with the user, file size, formats, run time and refund.
  Direct, because a TestBot on a laptop writes to a local database that
  ManagerBot never reads; recorded as an info event so production does not
  deliver it twice.

`tests/test_jobs.py` drives every ending with a fake worker. Writing it found a
real bug: queue order came from `time.monotonic()`, which ticks every ~16 ms
on Windows, so "N ahead of you" said nobody for two jobs queued back to back.
It is a counter now.

#### Before anything is charged

Calibrated against a synthetic photo of the same pixel count (noisier than a
real one, so these are upper bounds):

| to | size | wall | CPU | fits in 50 MB |
|---|---|---|---|---|
| PNG | 163 MB | 30 s | 30 s | no |
| QOI | 150 MB | 74 s | 73 s | no |
| WEBP | 56 MB | 111 s | 109 s | no |
| JPG | 56 MB | 1.4 s | 1.4 s | no (a real photo is smaller) |
| AVIF | 43 MB | 15 s | 187 s | yes |
| HEIC | refused | 1.7 s | | x265 stops at 142.6 M luma samples |

So: pictures over **210 MP** are refused before the menu (above the 200 MP
sensors professionals shoot with); **HEIC** is not offered above 142.6 MP;
**lossless formats** are not offered when half the measured rate already puts
them over the send limit, and the menu says which were left out and why. What
slips past the estimate is caught after converting — refunded and reported,
which is the backstop the owner's two conversions never had.

#### Keep file, several formats, storage

A toggle under the format menu — "📌 Keep file for more formats" — off unless
turned on, remembered once chosen. On, the menu stays live after a tap and the
same file can be sent to PNG, WEBP and PDF one after another; each waits its
turn in the queue. The file is kept until 15 minutes pass without a conversion
of it, and `/cancel` deletes it sooner. Asked rather than assumed, because the
privacy notice promised deletion after conversion.

Temporary storage is checked against the size Telegram declares, before
anything downloads: 120 MB per person, 1 GB for the bot, 300 MB of disk left
free. The sweeper and a new upload both skip files a queued or running job
still needs. `/cancel` lists each queued and running conversion with what
stopping it costs.

#### DownloaderBot: several links at once

The per-person Lock never mattered: with updates handled one at a time, a
second link was not even read until the first had uploaded. Each link is now
its own task. Two at a time per person, three for the bot, five pending per
person before a new one is refused (before it counts against the allowance).
Temporary storage is bounded by what was already there — three downloads at 48
MB each — plus a free-disk check before starting.

#### Recharging more buys more

`FAMILY_TOPUP_VOLUME_BONUS`, default `50:10,150:25,500:40,1000:50`: +10 % from
50 ⭐, +25 % from 150, +40 % from 500, +50 % from 1,000, on the ordinary 2 ⚡ per
⭐. The thresholds sit on the `/donate` buttons, and the buttons now print the
bonus ("150 ⭐ +25%"). The first-payment ×2 stacks, so the deepest rate is
6 ⚡ per ⭐.

"At least covers the hardware" is `tests/test_pricing.py`. Worst case: a job
using its full 600 s on two cores with 1.5 GB resident, sending back 50 MB —
$0.0152 at Railway's published prices. At the deepest rate the cheapest paid
tier (16 ⚡ = 2.67 ⭐ = $0.035 at Telegram's $0.013 payout) is **2.3× that**, and
larger tiers 5× to 51×. The test fails if tiers or multipliers ever push a
conversion below twice its worst-case cost, and keeps a free conversion under
half a cent.

#### ⭐ and ⚡, everywhere

⭐ is money paid; ⚡ is credit. The options screen was still quoting credit
prices as "60 ⭐" — twice the real cost. Fixed there, in the help text and the
`/convert` prompt (both carry prices), and `/mystars` now shows the credit
history, since conversions are no longer paid in Stars. The in-bot `/terms`
and `/privacy` and the published `TERMS.md` / `PRIVACY.md` in all four public
bots now say what credit is, that it **cannot be withdrawn, refunded as Stars
or transferred**, when a conversion's credit is taken and returned, and that a
balance and its ledger are held and outlive `/deletemydata`.

#### Two things this session broke, and what caught them

- A slice meant to replace `convert_show_more` ran to the next `async def`,
  and what lay between were plain `def`s — `_result_caption`, `_runner`,
  `_touch` — so all of them went. It compiled; the first real conversion would
  have raised `NameError`. `test_conversions.py` caught it by building a
  caption.
- Restoring them through a shell heredoc turned an escaped newline inside a
  string literal into a real line break. `py_compile` caught that one, and the function was rebuilt
  from lines with no backslash passing through the shell at all.

#### Tests

| suite | result |
|---|---|
| `tests/run.py` | 42 / 42 (adds `test_jobs.py`, `test_pricing.py`) |
| `tests/credit_scenarios.py` | 37 checks, 0 failures |
| `tests/anon_scenarios.py` | 213 checks, 0 failures |
| `tests/policy_scenarios.py` | 61 checks, 0 failures |
| `tests/no_storage_scenarios.py` | 15 checks, 0 failures |
| `tests/smoke.py`, `functional.py`, `erase_scenarios.py` | 0 failures |
| the real worker, by hand | converts, refuses, and is killed at its limit |

### A review pass over all of it (2026-09-10)

A bug hunt over everything uncommitted since v1.5.0 found seven things, all
fixed in the working tree:

- **Credit was described as paying for downloads**, in the invoice sheet and
  the no-withdrawal line, in all four bots and three languages. Nothing spends
  credit on downloads and the published terms say so; the strings now say
  conversions in ConvertBot.
- **The donate prompt said nothing about credit.** The owner asked for the
  prompt as well as the thank-you to say a payment becomes balance that cannot
  be withdrawn. It now does, before an amount is chosen, with the rate.
- **A fiat invoice promised credit it would never add**, rendering the amount
  in cents as credits ("adds 500 ⚡" for five dollars). Fiat invoices keep the
  plain donation wording.
- **A queued conversion reaching the front during an announced pause was
  reported as a restart**: an owner alert per queued job, and each person told
  the bot restarted in the middle of their file. It is its own ending now,
  `PAUSED`, neither charged, refunded nor reported; `test_jobs.py` covers it.
- **A grant could be marked done without happening.** `grant_stars_once`
  commits its claim, then credits; if the credit failed, the claim stayed. It
  is released on failure now, so a first-payment bonus or an admin seed is
  retried rather than lost.
- **The per-person storage share was smaller than an album the bot accepts** —
  120 MB against 20 files of 20 MB — so the last pages of a large batch were
  refused while it was still arriving. The default is sized from `MAX_BATCH`
  and `MAX_FILE_MB` (450 MB on the cloud API).
- **DownloaderBot's concurrency was documented as two** in its comment,
  `.env.example`, `DEPLOY.md` and `ARCHITECTURE.md` after the default became
  three. All say three now, and `DEPLOY.md` lists the three new `DBOT_*`
  variables.

`run.py` 42/42, credit 37, policy 61, smoke and erase all pass after the fixes.

### The owner's answers, applied (2026-09-11)

After the review pass the owner was given every open question and everything
on hold as one numbered list, and answered it item by item. This section is
what each answer became. The ones answered "later", "noted" or "not" are in
**Not done yet**, with the owner's words, so they are not asked again.

#### A lifetime ladder, and only the bonus expires

> "however many times the user recharges money, the first 500 stars will give
> 3x and next 500 gives 2x usage."
>
> "only the bonus credit should expire."

Read as multipliers on the ordinary rate of 2 ⚡ per ⭐, the one reading in
which every word does something: the first 500 Stars a person ever pays earn
6 ⚡ each, the next 500 earn 4, and every Star after that earns 2. "However many
times" is what makes it a ladder rather than a per-payment bonus. It counts
Stars across all of somebody's payments, so five payments of 100 earn exactly
what one of 500 does, and a payment across a step is priced piece by piece:
400 Stars from somebody at 300 is 200 at 3× and 200 at 2×.
`tests/test_pricing.py` checks the piecewise property for every split it tries.

The part above the ordinary rate is **bonus credit**. Each payment that earns
some writes a lot to the new `family.star_bonus_lots`, with its own expiry
`FAMILY_BONUS_EXPIRY_DAYS` (90) after the payment. Ordinary credit has no
expiry at all. Three rules keep that honest:

- **Spending takes bonus first**, soonest-expiring first, so what expiry
  removes is only ever bonus that went unused.
- **Expiry happens whenever a wallet is read or moved**, as well as in the
  family housekeeping pass, so an expired bonus is never spendable in the gap
  between two passes.
- **Anything else that lowers a balance lowers the bonus with it** — a
  correction, `/balance <id> =0` — so a later expiry cannot take away credit
  that is no longer there and push the balance negative.

`topup()` prices a payment under the wallet's lock, from the Stars that person
has already paid, so two payments landing at once cannot both be priced as
somebody's first 500. `credit_scenarios.py` fires two at the same moment to
prove it. Grants, `/addcredit` and corrections are ordinary credit and never
expire.

What people read changed with it. `/donate` says what *their* next Stars earn
("Your next 360 ⭐ earn 6 ⚡ each (3×)…") and when the bonus expires. The Stars
invoice and ConvertBot's top-up message quote the credit from the ladder. The
thank-you names the expiry date, `/balance` shows how much of the balance is
bonus and when the next of it runs out, and ManagerBot's `/balance` shows bonus
family-wide and per person. The percentages that were printed on the donate
buttons are gone, because the same button now buys different credit for
different people.

Removed: the first-payment multiplier, the volume bonus,
`FAMILY_ADMIN_SEED_STARS` and `refund_topup`. The deepest rate is still 6 ⚡ per
⭐ — the old stacked maximum — so `test_pricing.py`'s margin over the
worst-case conversion is unchanged.

#### No refunds, said everywhere it matters

> "lets just not do refunds for now. Explicitly warn users that this is not
> possible tho."

"Payments are final: Stars are not refunded, and ⚡ cannot be withdrawn or
turned back into Stars" is now in the `/donate` prompt before an amount is
chosen, in ConvertBot's top-up message before a conversion invoice, in the
thank-you, in `/balance`, in the in-bot `/terms` and in all four `TERMS.md`.

A conversion the bot fails still puts its credit back on the balance. That
was asked for explicitly before, it is credit rather than Stars, and no Stars
refund is involved.

The sweep turned up older text that contradicted the decision. Every bot's
in-bot `/terms` said `/donate` "buys nothing at all" and that a mistaken
payment "comes back if you ask"; `/privacy` and `/deletemydata` justified
keeping payment records "because refunds are issued against them";
ConvertBot's description said a failed conversion is refunded in Stars; and
three ConvertBot strings left from the invoice-per-job days, used by nothing,
still promised Stars back. All are rewritten or removed, in three languages.
Payment records are now kept because a dispute about a payment is settled
against them, and `/deletemydata` says the ⚡ balance survives an erase, which
`PRIVACY.md` already did.

**`/paysupport`, because Telegram requires it.** Telegram's rules for Stars say
a bot "must be able to respond to the command `/paysupport` and process user
requests regarding payment issues", and that the developer is "solely
responsible for processing and rectifying legitimate user disputes". None of
the four had it. It says payments are final, and then how a payment that went
wrong — charged with no credit to show for it, or charged twice — reaches the
operator, to be put right with credit. It is in all four menus, and the
no-withdrawal line points at it.

#### `/addcredit`, instead of a 10,000 ⚡ seed

> "i only want a command that can add a certain amount of stars into anyone's
> account by their telegram ID."

The seed that ran at ManagerBot's startup is gone. `/addcredit <user_id>
<amount>` puts ordinary credit into any account, records it in the ledger as a
grant naming the admin, and logs a family event. `/balance <id> -N` and `=N`
still take credit away and set it.

#### Command menus in the language somebody chose

The screenshot was production ConvertBot — v1.5.0, whose menu is English-only
and whose help is the old text. 1.6.0 already published Uzbek and Russian
menus, but Telegram chooses between per-language menus by the language of the
person's **Telegram app**, not by `/uz`, so most people who would pick Uzbek
still saw English.

Choosing a language now sets a menu for that one chat (`BotCommandScopeChat`),
which outranks every per-language list. A per-chat menu is frozen when it is
set, so each person's `user_data` keeps a signature of theirs, and
`track_activity` sets it again when the bot's menu has changed since. The
owner's chat gets its menu in the owner's language.

> "6 - do it"

Every start now also deletes the three scopes the code never writes —
`AllPrivateChats`, `AllGroupChats` and `AllChatAdministrators`, in every
language — in the four public bots and in ManagerBot. Those scopes are what
kept StickerBot's commands on TestBot's token. The consequence the owner agreed
to: a menu set by hand in one of those scopes is wiped on the next deploy. None
of the five real bots has one.

#### `/backup` that can be redeployed from, and pinned

The old `/backup` was a readable zip of CSVs that summarised binary columns — a
copy nobody could restore from. It now sends `family_db.py`'s own archive:
each table as Postgres's `COPY … CSV`, a `MANIFEST.json` naming every table's
columns, DEFLATE at level 9. It is pinned without a notification and nothing in
ManagerBot unpins, so every backup stays one tap away; `tests/test_backup.py`
fails if any ManagerBot file ever calls an unpin method. Over 49 MB it arrives
as numbered parts, each pinned, with the SHA-256 of the joined file in the
caption. `DEPLOY.md` §6 has the restore, including `--dry-run` as the way to
check a backup before needing it. `db.dump_family_csv_zip` is unused now and is
left where it is.

#### AnonBot `/export`

> "Do the transcript feature to export to a document, supporting exporting
> multiple conversations at once. Add a disclaimer telling what will be
> accessed/stored and what will be not."

Two facts decide how this can work. A bot cannot read a chat's history, so the
only way it sees an older message is somebody forwarding it back. And AnonBot
stores nothing anybody writes, so there is no stored copy to build from — and
building one must not create one.

So `/export` explains itself first — what it reads, what it does not, and that
it keeps nothing — and then collects the messages the person forwards, **in
process memory only**: not `user_data`, which `lifecycle.py` persists to
Postgres, and not a table. Build turns them into one HTML document with a
section per conversation and sends it; the forwards are then discarded and the
forwarded copies deleted from the chat. A session ends after 15 minutes
regardless, on a plain timer rather than a job, and holds at most 3,000
messages or 2 million characters.

A forward carries its original date and who wrote it, but not its message id,
so `anon_relay` gains `sent_at` and `from_bot`: when each bubble was written,
and whether it was the person's own message (`FALSE`), a copy the bot delivered
(`TRUE`), or the bot's own words — a header, a bookmark, a link's opening line
(`NULL`). A forward is placed with the nearest row on its own side of the chat,
inside a small window. The bot's own bubbles are left out, two conversations
equally near are not guessed between, and anything unmatched — including
everything relayed before these columns existed — is listed at the end rather
than put in the wrong thread. Times, not words: nothing anybody wrote is
stored.

What keeps it private: photos, voice messages, videos and files are named, not
downloaded; shared contacts and locations are named without their contents; a
guest's section never shows the owner's conversation number; the document has
no script and loads nothing from anywhere. Forwards into an open export skip
the shared flood gate, since a selection of a hundred messages is a hundred
updates, and `/cancel` offers to stop an export. `PRIVACY.md`, `TERMS.md` and
the in-bot `/privacy` say all of this.

`tests/test_transcript.py` covers placement, escaping, and that memory is empty
afterwards. `anon_scenarios.py` (213) and `no_storage_scenarios.py` (15) pass
with the new columns written on every relay.

#### Smaller things from the same list

- **Contact.** `mukhtorovmurodbek@gmail.com` is the operator contact in every
  `PRIVACY.md` and `TERMS.md`, and the default in `shared_features.py`, so an
  unset `OPERATOR_CONTACT` no longer means a `/privacy` that names nobody.
- **The Pinterest video pin** diagnosed above is fixed. A provider can now say
  which later providers cannot help (`ProviderFailed(skip=…)`): the API route,
  finding a video served only as HLS, rules out the page scraper that would
  return its cover frame, and yt-dlp fetches the stream. `test_pinterest.py`
  covers the pin and the chain.
- **Settings written down.** `DEPLOY.md` and every `.env.example` list the
  credit settings — which must match on all five bots, since each prices a
  payment made in it from its own environment — `FAMILY_STARS_SANDBOX`
  (TestBot only), and the `CONVERT_*` queue and storage limits.
- **Tests that did not test what they said.** `tests/media.py` had not run
  since v1.5.0; it uses `convert()` now, and runs the real worker process — its
  JSON protocol, its exit codes, and `jobs.run_worker` killing one past its
  deadline. `anon_scenarios.py` group H replied to "the prompt", which has not
  existed since v1.4.0, and one of its checks passed on the wrong message; it
  replies to the bubbles it names and checks the exact text now.
  `erase_scenarios.py` asserts that `/deletemydata` keeps the ⚡ balance and its
  ledger.

#### Third-party conversion: not built, because the terms forbid it

The standing preference is free third-party services for heavy work, with the
local converter as the fallback, and the owner said to do it. Before writing
any of it, the terms of the obvious services were read:

- **CloudConvert** (Lunaweb GmbH, Germany; free plan 10 conversions a day):
  "Don't build a raw file conversion service on top of CloudConvert", and "you
  agree to not offer a raw file conversion service as stand-alone product using
  the CloudConvert API."
- **ConvertAPI** (Lithuania): forbids making the service available to third
  parties, and its free allowance is a one-time 250 conversions.
- **Zamzar**: the free API plan is for testing only, with a 1 MB limit.

ConvertBot is a raw file conversion service — that is the whole product — so
wiring any of these in would put the owner in breach of the provider's terms
from the first conversion, with the privacy notices naming a processor the bot
is not allowed to use. Nothing was built. The version of "somebody else's free
compute" that stays open is the bot's own converter running on free compute
the owner controls; that is an infrastructure change and a minor version, and
it is in the list below.

#### Tests

`run.py` 44/44 (new: `test_backup.py`, `test_transcript.py`);
`credit_scenarios.py` 78; `policy_scenarios.py` 61; `anon_scenarios.py` 213;
`no_storage_scenarios.py` 15; `smoke.py`, `erase_scenarios.py` for all four
bots, `functional.py` and `media.py` all pass.

### After testing on TestBot (2026-09-11)

The owner tested the section above on TestBot and came back with two
corrections and a pricing change.

#### Nothing sent to the owner names a user

> "bruh i told you to not send me user specific details"

ConvertBot's conversion-failure alert carried the user's id, username and
balance. It now says what failed, the file's size and formats, how long it ran
and how much credit was involved, and nothing about who.

The same thing was checked across all five bots rather than in the one
message: the top-up and donation events in every `shared_features.py` dropped
the payer's id, username and balance — the ledger still records the id, which
is where `/balance <id>` looks — and ManagerBot's "a stranger found ManagerBot"
alert no longer names the stranger. The owner's own lookups (`/balance <id>`,
`/addcredit`, `/stars`, `whois`) still show ids, because an id is what the owner
gives them.

#### The refund warning, once

> "Too many "no refund" texts. Show it only once after receiving the media,
> and once in the help text menu or the start command response. Make the text
> body start with something that stands out, maybe NOTE with capital letters or
> an emoji."

In the everyday flow it now appears once: in ConvertBot's reply to a received
file that has a price, opening with "⚠️ NOTE:" — credit is taken when the
conversion starts, stopping it yourself keeps it spent, a failure on the bot's
side gives it back, and payments are final. And once in every bot's `/help`.

Removed from the `/donate` prompts, the payment thank-you, `/balance`,
ConvertBot's top-up message, the "converting" message, the Stop button and
`/cancel`'s list. The donate prompt and the thank-you still say that ⚡ cannot be
withdrawn; that was a separate, earlier instruction, and it describes what
credit is rather than repeating the refund rule. `/terms`, `/paysupport` and
`TERMS.md` still state the policy in full, because they are where a person goes
to read it on purpose.

#### Free up to 3 MB, and larger files dearer

> "1 MB seems too low for free. can we raise it a bit in cost of increasing the
> price for documents with higher sizes? Also there should be a note that says
> better pricing and a bigger max file size upload is coming soon."

| size | before | now |
|---|---|---|
| under 1 MB | free | free |
| 1–3 MB | 16 ⚡ | free |
| 3–5 MB | 16 ⚡ | 20 ⚡ |
| 5–10 MB | 36 ⚡ | 44 ⚡ |
| 10–20 MB | 60 ⚡ | 80 ⚡ |
| 20–50 MB | 110 ⚡ | 140 ⚡ |
| 50–100 MB | 200 ⚡ | 250 ⚡ |
| 100–200 MB | 360 ⚡ | 450 ⚡ |
| each 100 MB past that | 160 ⚡ | 200 ⚡ |

Only the tiers up to 20 MB are reachable on the cloud Bot API. "Documents" was
read as files of any kind, since the price has always depended on size alone.
Free conversions keep their 60-second limit, which is what bounds what one can
cost; `test_pricing.py` still passes, with more margin at every paid tier.
`FREE_UNDER_MB` is taken from the first tier, so the messages that name the free
size follow it instead of saying "1 MB" in six places.

"🔜 Coming soon: better prices and a bigger maximum file size" ends the price
list shown by `/help` and `/convert`, and the too-large refusal says a bigger
limit is coming.

#### Tests

`run.py` 44/44; `credit_scenarios.py` 78; `policy_scenarios.py` 61;
`anon_scenarios.py` 213; `no_storage_scenarios.py` 15; `smoke.py`,
`erase_scenarios.py`, `functional.py` and `media.py` all pass.

### ⚡ that goes back, `/recharge`, and Uzbek rewritten (2026-09-11)

The owner's second pass over TestBot.

#### The note in the owner's words, and one term for it

> "Change the note to ⚡️ is taken when the conversion STARTS. Stop it yourself
> and it is NOT given back; if it fails on the bot's side, ⚡️ goes straight
> back to your balance. Payments are final — Stars are not refunded. Apply this
> style to every place that already warns the user about no refund policy. Or
> come up with a term for it so users won't think they are going to get stars
> back."

Both. ConvertBot's ⚠️ NOTE is now that sentence, word for word. Every other
place that states the policy — `/paysupport`, `/terms`, the donate and top-up
prompts, `/help` in all four bots, `TERMS.md` — puts the same capitals on the
words that carry the meaning: payments are FINAL, Stars are NOT refunded, ⚡ can
NOT be withdrawn (НЕ and НЕЛЬЗЯ in Russian, QAYTARILMAYDI and BO'LMAYDI in
Uzbek).

The term is **"goes back to your balance"**. "Refund" is now said only of
Stars, and only to say they are not refunded. ⚡ that comes back after a failure
goes back to the balance everywhere it is mentioned: the failure messages,
`/balance`'s ledger (whose `refund` rows now read "returned"), the owner's
alert and every `PRIVACY.md`. Nobody reads "refunded" and expects Stars.

#### The send limit, said before it bites

> "There should also be a warning that tells the users that an error might
> occur because of bot's send limitations and if it happens ⚡️ is refunded back
> to balance."

ConvertBot's reply to a file with a price now ends, after the NOTE, with "📤 A
bot can send files of up to 50 MB. If the converted file comes out bigger, it
can't be sent — and its ⚡ goes straight back to your balance." The number is
`jobs.SEND_LIMIT_MB`, so a local Bot API server changes the sentence with it.

#### `/recharge` where credit is bought, donate everywhere else

> "Change donate wording to recharge in converter or anywhere user can buy
> anything, everywhere else it should stay as donate"

ConvertBot is the only bot where Stars buy something, so it is the only one
that changed: `/recharge` in its menu and help, "Top up your ⚡ credit" and "⚡
Recharged" in its messages, its `/cancel` list and the nudge after a
conversion. `/donate` still works there, unadvertised, so an old message or a
habit is not met with "unknown command". StickerBot, DownloaderBot and AnonBot
still say donate.

> "when someone donates stars in a certain bot there should be info message
> that thanks the user and tells that they have received bonus credit in
> converter bot that they can use."

A donation in any of those three now thanks the person and says how much ⚡ it
put in ConvertBot, naming it by its @username, with the part that is bonus and
the date it expires on a line of its own.

#### Uzbek, rewritten

> "Revise the uzbek translation, especially the parts that say these"

All 362 Uzbek strings in the four public bots were rewritten as Uzbek rather
than patched, with the English capitals where they carry the meaning, and the
Uzbek command-menu descriptions with them. The two lines the owner quoted now
read:

- the NOTE: "⚠️ DIQQAT: ⚡ konvertatsiya BOSHLANGANDA yechiladi. Uni o'zingiz
  to'xtatsangiz, ⚡ QAYTARILMAYDI; bot tomonidagi xatolik tufayli bajarilmasa,
  ⚡ darhol balansingizga qaytib tushadi. To'lovlar qaytarilmaydi — Stars
  qaytarib berilmaydi."
- `/cancel`: "joriy amalni bekor qilish (bir nechta bo'lsa, qaysi birini
  so'rayman)"

Every placeholder was checked against the English, so no message can fail to
format in Uzbek alone.

### A code on every problem, a Report button, and word while a conversion runs (2026-09-11)

> "Maybe 10 minutes is too long to go with no context. Implement warnings after
> 2 minute of doing work, something like it has took a while and it should say
> that there will be 2 more warnings, 3 minutes and 8 minutes later. the last
> warning (that triggers after 10 minutes of work) should say it failed or
> whatever happened."

> "Give every possible exception that the user might trigger a code or a unique
> identifier. If user triggers something with such an error, they should get a
> button too, that says "report the issue". [...] Make a list or a dictionary
> that can decode this error message with as much accuracy as possible. The
> reports should be sent to my bituraternom account [...] and stored somewhere
> in the cloud too."

#### Word while a conversion runs

A paid conversion may run for ten minutes. `jobs.PROGRESS_MARKS_S = (120, 300)`
makes it say:

| after | what the person gets |
|---|---|
| 2 min | "⏳ Still converting — 2 min so far. I'll write again in 3 min, and in 8 min at the latest you'll hear how it ended." |
| 5 min | "⏳ Still converting — 5 min so far. The last update comes in 5 min: by then it will be done, or stopped with its ⚡ back on your balance." |
| 10 min | the ending itself: the file, or the time-limit failure with its code and its ⚡ back on the balance |

Each update is a new message replying to the "Converting…" line, not an edit of
it: an edit arrives silently, and the point is that somebody who has waited two
minutes hears about it. The ending is then posted below them as a new message,
for the same reason. A conversion that finishes first sends nothing more. A free
one, limited to 60 seconds, sends none. The minutes are counted from the job's
own limit, so a different limit changes the sentences with it, and a stopped
conversion stops updating.

#### Codes, and what they mean: `problems.py`

One file, copied into all five bots and checked like the other copies, holding
100 codes: `FM-` for what every public bot shares, then `CV-`, `DL-`, `ST-` and
`AN-` for ConvertBot, DownloaderBot, StickerBot and AnonBot. Each says which bot,
what happened, the likely causes, and what to check. That is the dictionary,
and ManagerBot reads it:

- `/decode CV-TIMEOUT` — the explanation; any unambiguous part of a code works
- `/reports [n]` — the latest reports, newest first

A code names a kind of problem and never changes once shipped, so a report
sent months later still decodes. It is the last line of the message: "🆔
CV-TIMEOUT". `i18n.t()` adds that line to every message key listed in
`problems.KEYS`, so no call site can forget it. The few messages assembled from
several strings — ConvertBot's job endings, StickerBot's sticker errors and
failed imports, DownloaderBot's allowance message — get theirs where they are
assembled, at the end.

An **incident** names one occurrence: six characters, made when the message goes
out, carried by its Report button and logged beside the code. A crash's
incident is logged with its traceback, and a failed conversion's is in the
owner's alert, so a report finds its log line.

What "every possible exception that the user might trigger" was taken to mean:
every message telling somebody their request did not happen — refusals,
limits, expired buttons, failures — and a crash. Confirmations and ordinary
guidance carry no code.

#### The Report button

Added in one place, the bot's own `send_message` and `edit_message_text`, which
every message leaves through (`attach_problem_reports` in
`shared_features.py`). Any message ending in a known code gets a "🐞 Report the
issue" row under whatever buttons it already had. Adding it at each call site
would have made a hundred places to forget it.

Tapping it shows exactly what would be sent, and asks first:

> 📨 Send a report about this problem to the bot's owner?
>
> What is sent: the bot's name, the error code CV-TIMEOUT, the incident number
> K7Q2MX, when it happened and the bot's version.
>
> What is NOT sent: your name, username or Telegram id, your files, links or
> messages — nothing personal.

with **📨 Send report** and **✖️ Cancel**, in all three languages.

**Send** stores the report in `family.problem_reports`, in the shared Supabase
database: the bot, the code, the incident, when it happened, when it was
reported and the version, and no column that could identify anybody. One row
per incident, so a second tap is told it was already sent. That bot then
messages the owner's account (`FAMILY_REPORTS_TO`, default 8796896653) with the
decoded explanation. A bot can message only an account that has started it; if
the owner never started that bot, the report goes out as a warning event, which
ManagerBot forwards, so it arrives either way.

`FAMILY_REPORT_BUTTONS=off` hides the button. The codes stay.

#### A crash now says so

A crash in a handler used to be logged and reported to the owner while the
person who hit it heard nothing. In a private chat they are now told "⚠️
Something went wrong on the bot's side while handling that, so it wasn't done.
Please try again in a moment.", with FM-CRASH and a Report button whose
incident is the one logged with the traceback. ManagerBot's `error_handler` is
unchanged, since its only user is the owner, and `test_shared_copies.py` records
that difference as deliberate.

#### Tests

- `test_problems.py`, in `run.py`: every code well formed and fully described;
  every code used, and every code used defined; every coded message present in
  all three languages; every pop-up still within Telegram's 200 characters with
  its code; a code read back only from the end of a message; the longest Report
  button's data within 64 bytes.
- `test_jobs.py`: updates at each mark, in order, the last saying the ending is
  next; none for a quick conversion, none at or past a job's own limit, none
  after it was stopped.
- `report_scenarios.py`, against Postgres: stored once per incident, read back
  newest first, and no column that could identify a person.
- `sim.py` refuses a message with a code anywhere but its last line, so every
  simulated suite checks placement in passing.
- `anon_scenarios.py`, group Q, walks the whole flow through the simulated
  Telegram: a refusal ends with its code and offers Report; the disclaimer names
  the code and the incident before anything is sent; Send stores one row and
  messages the owner's account what the code means, with nothing about the
  person; a second Send and a Cancel send nothing; a forged button is refused.
  Elsewhere in the suite a refusal may now carry a Report button, and still
  nothing that would send the message after all.

`run.py` 45/45, including `test_problems.py` and the progress scenario in `test_jobs.py`; `anon_scenarios.py` 227, fourteen of them group Q; `credit_scenarios.py` 78; `policy_scenarios.py` 61; `no_storage_scenarios.py` 15; `report_scenarios.py`, `smoke.py`, `erase_scenarios.py`, `functional.py` and `media.py` all pass.

### Report buttons where they help, every problem logged, and the logs from ManagerBot (2026-09-11)

> "actually, not every command needs a report button. But keep the report
> itself. Every exception should be logged inside the bot automatically with
> time and id, but this one is simple."

> "reduce the last "what is not sent" part to "no personal info". Then give me
> easier commands to see the logs from the parent bot. Add them to the command
> menu, proof check everything, and schedule the update [...]"

#### A Report button only where it helps

`problems.SIMPLE` names the 61 problems, of 100, that keep their code line
but offer no button: refusals that explain themselves and that the person can
act on — an unknown command, a mistyped amount, a limit reached, an expired
button or somebody else's, the other side's choice. The other 39 keep it: a
crash, a failed download, conversion or send, Telegram refusing something the
bot prepared, the bot's own configuration. `test_problems.py` holds the owner's
example to it, and checks that every conversion ending, every sticker Telegram
refused and the unconfigured-currency case still offer one. `/decode` says
which kind a code is.

#### Every problem logged

Every message that ends in a code is logged as it goes out, pop-ups included:
one line with the time, the code, a new incident id, and whether it offered a
Report button.

    2026-09-11 18:00:02,114 problems INFO FM-UNKNOWN-COMMAND incident 6TY763, no Report button

Nothing about who it happened to. A message redrawn with the same problem still
on it keeps its incident and is logged once, so one occurrence is one line and a
Report button on an older copy still matches. The lines go to `bot.log` with
everything else, to a `problems.log` of their own wherever the bot writes log
files, and into memory.

#### The logs, from ManagerBot, without remembering how

`/errorlog`, `/botlog` and `/problemlog` are in ManagerBot's menu. Name no bot and
each answers with a button per bot; typing still works (`/problemlog conv 100`).
`/logs <bot>` also takes `problems` now.

On Railway the bots write no log files unless `LOG_TO_FILES=1`, so `/logs` used
to answer "No errors.log on this host" in exactly the place the owner needs it.
Every bot now keeps the latest lines of all three logs in memory — 1,500 of
`bot.log`, 800 of `errors.log`, 500 of `problems.log` — and `/logs` reads those
where there is no file, and says so. `tests/test_logs.py` covers both paths.

The reading happens inside the bot being asked, so until ManagerBot itself is
redeployed the running ParentBot reaches the same logs with `/logs <bot> problems`.

#### The disclaimer, shorter

"What is NOT sent: your name, username or Telegram id, your files, links or
messages — nothing personal." is now "No personal info is sent." ("Hech qanday
shaxsiy ma'lumot yuborilmaydi.", "Личные данные не отправляются.") The line
before it still lists exactly what is sent.

#### The release

The owner asked for the update at 18:00 KST, twelve minutes ahead. A background
timer to start the release was refused by this session's permission check, and
the owner then moved it to the start of the next hour, 19:00 KST.
`scripts/nightly_release.py` gained `--at HH:MM`, a window on this computer's
clock instead of 04:00 Tashkent, and `--skip <bot>`; and it now refuses to publish
unless the nearest tag is the target version and nothing tracked is uncommitted.

ManagerBot is held back from this publish. Its 1.6.0 code reads `MBOT_*`
variables and the `manager_bot` schema, and Railway still has `PBOT_*`, so
redeploying it now would stop it at startup. It ships after the Railway variables
are renamed and `scripts/rename_to_managerbot.py` has run (`DEPLOY.md` §8). The
four public bots do not depend on it: the family bus works across versions.

#### Tests

`run.py` 46/46, with `test_logs.py` new; `anon_scenarios.py` 233, twenty of them group Q; `credit_scenarios.py` 78; `policy_scenarios.py` 61; `no_storage_scenarios.py` 15; `report_scenarios.py`, `smoke.py`, `erase_scenarios.py`, `functional.py` and `media.py` all pass. A dry run of `nightly_release.py --at 19:00 --skip manager_bot` reached the production database, found the four public bots on 1.5.0 and nobody active, and stopped where publishing begins.

### The release (2026-09-11)

The four public bots and ManagerBot went out the same evening, on the owner's
time rather than the 04:00 window.

| KST | what happened |
|---|---|
| 18:40 | `nightly_release.py --at 19:00 --skip manager_bot` paused StickerBot, ConvertBot, DownloaderBot and AnonBot |
| 19:00 | nobody active for 15 minutes; `publish.ps1` pushed the family repository and the four public ones |
| 19:04 | all four reported 1.6.0 with no errors and were resumed, which told everyone turned away during the pause |
| 19:05 | tag `v1.6.0` pushed |
| 19:15 | `scripts/rename_to_managerbot.py`: schema `parent_bot` renamed to `manager_bot`, and `parentbot` rewritten to `managerbot` in 56 event rows, 140 usage samples, the heartbeat and one settings key |
| 19:16 | ManagerBot published; it started on 1.6.0 at 19:16:52 |
| 19:30 | a full backup — 55 tables, about 4,550 rows, 38 KB — sent to the owner by ManagerBot and pinned, with a copy in `backups/` |

#### ManagerBot started on a schema of its own making

The rename moved ManagerBot's tables; it did not copy them. `ALTER SCHEMA
parent_bot RENAME TO manager_bot` changes the name the tables are filed under, so
the same rows were under the new name a moment later and nothing existed twice.

On Railway the `PBOT_*` variables were renamed to `MBOT_*`, but `DB_SCHEMA` kept
the value `parent_bot`: of all of them it was the one whose name stays and whose
value changes. ManagerBot 1.6.0 started, looked for a schema called `parent_bot`,
found none — the rename had moved it a minute earlier — and made a new one: two
tables, no rows. Its real data, 71 activity rows, stayed in `manager_bot`,
untouched. Nothing was lost. When this was written the value had not been changed yet. The empty `parent_bot` schema is left
where it is, on the owner's word: "Lets not delete anything from db yet."

#### The release message named the wrong hour

The summary `nightly_release.py` writes for ManagerBot to forward said "shipped at
04:00 Tashkent" whatever `--at` had said. It names the window it used now.

#### Also

- `publish.ps1` stops, rather than only warning, when anything is uncommitted.
  The fix above was set aside with `git stash` for ManagerBot's publish and put
  back straight after.
- TestBot is stopped, and so is the local Postgres cluster on port 55432 that it
  and the test suites used; nothing else was connected to it. The Windows
  PostgreSQL service was already stopped and was not touched.

### Not done yet

**Decided by the owner, recorded so they are not asked again:**

- **DownloaderBot's paid override** — dropped: "forget about 2".
- **Refunds of Stars** — not offered: "lets just not do refunds for now".
  Telegram can still reverse a Stars purchase through its own dispute process,
  which the bot does not control, and with `refund_topup` gone the credit from
  a reversed payment is not taken back automatically. `/balance <id> -N` does
  it by hand.
- **`testbot/token.env` points TestBot at the production Supabase database.**
  `run_testbot.py` overrode it to a local database for every run in this
  session, but `testbot/run.ps1` does not. The owner: "leave as is for now, but
  also save it for later." It has run that way before: the production database
  holds an empty `testbot_anon_bot` schema. Not removed, for the same reason as
  `parent_bot`.
- **The Supabase password** is not being rotated: "10 - not".
- **ConvertBot's Railway memory** — noted by the owner. A 200 MP decode peaks
  around 1.5 GB. An out-of-memory kill now takes only the worker, refunded and
  reported, but the conversion still needs that memory to succeed.
- **Licences** — no `LICENSE` file. Without one the code is all rights reserved
  by default, which is the safe state for a private repository; the owner has
  the plain-language explanation.

**For 1.7.0:**

- **Reports that carry the person's own words or a screenshot.** The owner:
  "Maybe we will add a possibility for users to leave their own descriptions of
  what happened, or screenshots, but its for later. Note for future, or 1.7.0."
  A report today holds nothing anybody wrote, which is what lets the disclaimer
  promise nothing personal. Once it can, the disclaimer has to say so.
- **What the price list now promises.** ConvertBot tells people that better
  prices and a bigger maximum file size are coming soon. The bigger size is
  the local Bot API server below; better prices have no plan yet.
- **DownloaderBot merging files** — pictures into a PDF, songs, a video with
  its subtitles, and the like.
- **A local Bot API server.** It raises what a bot may send from 50 MB to 2 GB
  and removes the 20 MB download limit; `LOCAL_BOT_API_URL` is already
  understood by `jobs.py` and `convert_utils.py`. Infrastructure, so a minor.
- **Heavy conversion off the bot's container, legitimately:** the bot's own
  converter on free compute the owner controls, since every conversion API
  checked forbids a raw conversion service (above).

**Later:**

- **ManagerBot's `DB_SCHEMA` on Railway** still said `parent_bot` after 1.6.0
  shipped. Set it to `manager_bot`; until then ManagerBot runs on the empty
  schema it made at startup (see *The release*).
- **The empty `parent_bot` schema** — two tables, no rows — is left in the
  database on the owner's word ("Lets not delete anything from db yet"). Drop it
  once ManagerBot reads `manager_bot`, and only when the owner says so.
- **The GitHub repository is still `ParentBot`**; it should be `manager-bot`,
  paired with confirming that Railway still builds from it. `DEPLOY.md` §8c.
- **The five `hero.png` description pictures are not uploaded** — a manual
  BotFather step: *Edit Bot* → *Edit Description Picture*. They are in
  the public repositories since the 1.6.0 publish.

**Still open from the work itself:**

- **A pop-up cannot hold a button.** Problems shown as a Telegram pop-up (a tap
  on an expired button or on somebody else's, a full queue) end with their code
  but offer no Report button.
- **Progress updates are ConvertBot's alone.** Nothing else in the family works
  silently for minutes: DownloaderBot and StickerBot already move a status line
  as they go.
- **Jobs live in memory.** A graceful stop refunds a running conversion; a hard
  kill of the container does not, and the drain is 120 s.
- **Background conversions do not send the donation nudge**, which needs a
  handler's context.
- **AVIF is multithreaded** — 187 CPU-seconds in 15 wall-seconds locally — and
  CPU is billed per core-second.
- **An album's later parts chain off the first one** rather than off the
  counterpart of what the album replied to. It reads correctly, and is left
  alone deliberately.
- **Nothing checks the menus a token is actually serving.** `test_menu.py` reads
  source. `publish_commands` now clears the scopes it does not own, which closes
  the TestBot hazard in code, but a check against the live token would be the
  only proof.
- **`/export` holds up to 3,000 messages per person in memory for 15 minutes.**
  Bounded, but many exports at once would be memory the container pays for.
- **OCR, and sleeping the quiet bots**, carried over from v1.5.0, are unchanged.

---

<a id="v1.7.0"></a>

## v1.7.0 — what a user waits for, reports that log themselves, a price that
## knows what it is pricing, and an export that stops asking

A minor bump, so this file covers everything since the previous minor --
which is everything after v1.6.0 was tagged and published, since nothing
shipped in between.

It was written in a folder called `BotFamily1.6.1`, because the number is
the owner's to choose once the work is done and the folder name is only a
label for where it lives. They chose 1.7.0, and a minor is right: AnonBot's
export was rebuilt, ConvertBot's pricing changed shape, Word documents are
new, and errors grew levels, a nightly report and a retention policy.

The owner's list, in their order:

1. **`/ping` sorted by response time, and measuring what a user sees** rather
   than only the admin bus. §*A ping that answers the question people ask*.
2. **`/errors` with no bot named** asks all four at once. Same section.
3. **Problems report themselves.** Every problem is recorded the moment it is
   shown, with nothing about anybody; the button now volunteers the person's
   own details, and says which. §*A problem is logged whether or not anybody
   taps anything*.
4. **ConvertBot prices the conversion, not the upload.** The price appears
   after a format is chosen, from the input size, an estimate of the output
   size, and how hard the pair is to run. §*Pricing what is actually being
   done*.
5. **A converted file comes back with a name of its own.** Same section.
6. **PDF → DOCX**, within honest limits. §*PDF into Word, the bare minimum
   and no more*.
7. **AnonBot's `/export` stops asking people to forward their own chat back
   at it.** §*An export that reads the conversation itself*.

Then, after the owner read that half:

8. **Errors have priority levels.** Urgent ones reach the owner the moment
   they happen, everything else lands in a report ManagerBot sends at midnight
   Seoul time, and the records delete themselves on two different clocks.
   §*Errors that know how loud they are*.
9. **AGPL-3.0 on all five bots**, which had been an open question since
   v1.5.0. Same section.
10. **Two commands that never worked, and a command list that had grown.**
    `/idle` and `/stars` fixed, every menu re-sorted and cut, misleading names
    replaced with the old ones kept working. §*Two broken commands, and a menu
    that had grown*.

`FAMILY_VERSION` reads 1.7.0 in all five copies of `family_link.py`, so the
folder is self-consistent. **The number is provisional and nothing is
committed.** The owner: *"just save everything in 1.7.0 for now and when the
session ends or when i say so, we will decide if the update is big enough for
minor or micro version bump. Even locally."* So the folder name is a label for
where the work lives, not a decision about what it will be called; if it ends
up a minor, renaming the folder and re-bumping `FAMILY_VERSION` is the last
step before the first commit.

### A ping that answers the question people ask

#### The family ping was sorted alphabetically

Four numbers, one per bot, in the order of the alphabet. The owner:

> "first thing to do is to make them appear sorted by the response time, not
> alphabetically."

Alphabetical is the one order that hides the answer. Four numbers are being
compared precisely because one of them might be the odd one out, and finding
it meant reading all four every time. `_summary_lines` now sorts on time,
fastest first, with a bot that never answered last whatever it is called — it
has no time at all, and putting it first because its name begins with A is the
same mistake in a new order.

#### And it was measuring a path no user takes

The old report ended with a disclaimer: *"Admin round trip through Postgres —
not what a user waits for; their message never takes this path."* True, and
useless on its own. The owner:

> "Then lets make it also show what the user sees, as in how much time does it
> take to respond to an info command, like basic start, or something that
> pulls info from the database."

So the ping now measures that too, and measures it rather than estimating it.
A `ping trace` makes each bot do the two things an info command does:

* **The database read.** `/start` with a language already chosen is one
  indexed read of `user_settings` — `family_link.user_path_probe()` runs
  exactly that read, through the same pool every handler uses. It reads
  `PROBE_USER_ID`, which is `0`: below every id Telegram issues, so the index
  is walked to the same depth as a real lookup and no real person's row is
  read to time a ping.
* **The trip to Telegram.** One `getWebhookInfo` and back, which is what
  handing the reply over costs. `getMe` would have been the obvious choice and
  is the wrong one: python-telegram-bot caches it after startup, so it would
  report nought — the one wrong answer that looks like a fast one.

The family summary now shows both numbers per bot, `user` first because it is
the one anybody outside the admin chat would recognise, and sorts on it. The
per-bot breakdown grows a **What a user waits for** block under the leg-by-leg
table.

What is deliberately *not* added in is the person's own trip to Telegram. It
is the largest term for somebody on mobile data, neither end can measure it,
and folding in a number nobody chose would make the total look precise while
being wrong. The report names it as missing instead.

#### `/errors` with no bot named

> "Then i want the errors command to show errors from all bots when the bot
> name is not specified."

It printed `Usage: /errors <bot>`, which is the wrong way round for the
question it answers. "Has anything broken?" is a question about the family,
and making the owner ask it four times — and remember four names to ask with —
is four chances to check three bots and forget the fourth.

`_fanout()` queues one command to every child and rewrites a single live
message as the answers arrive, the same machinery a family `/ping` uses. It is
written generally rather than for `/errors` alone, because anything read-only
and short is shaped this way.

Two details that are not obvious:

* **A bot too old for the command is not queued at all.** It would sit there
  until the ninety-second timeout and then be reported as down, which is the
  one wrong answer that starts a hunt for a fault that is really a missing
  deploy. It is listed as too old, with its version.
* **Sections stay in the family's declared order**, not sorted by how bad the
  news is. A report whose lines move about between runs is one nobody can
  skim.

Naming a bot still works and still goes straight to that one.

### A problem is logged whether or not anybody taps anything

The owner, on the Report button:

> "It should include the error without user pressing the report button, but
> should only store generic technical data with no user specific info, and
> that this issue is logged with no user data. If the user wants to help, they
> can press the report button to include user specific data - type of data
> should be mentioned before actually sending the user details."

Before this, a problem reached the owner only if somebody could be bothered to
report it. `family.problem_reports` was therefore a list of how many people
tapped a button, not a list of what was going wrong — and the two are not the
same list at all, because the problems people hit once and shrug at are
exactly the ones that never get reported and never get fixed.

#### Recorded when it is shown

Every coded message any bot sends now writes a row: the bot, the code, the
incident, when, and the version. Nothing else. `record_problem_occurrence()`
is `INSERT … ON CONFLICT DO UPDATE SET seen = seen + 1`, so a status line
redrawn with the same failure still on it counts rather than duplicating.

**It could not go on the sending path.** A coded message is sent from inside a
failure, often many at once — `FM-FLOOD` is by definition several people at
the same moment — and a Postgres round trip there would add the database to
the list of things that can go wrong while the bot is already going wrong, and
add it to the *user's* wait. So occurrences collect in memory and go out on a
timer, the same shape as the activity buffer next to it and for the same
reason. `flush_on_shutdown` writes the last window out; a hard kill loses at
most twenty seconds of them, and they are in the bot's own `problems.log`
either way.

#### And the message says so

A reportable problem now carries one line above its code:

> This is already written down for the bot's owner — the code, the time and
> the version, and nothing about you.

Above the code line and never below it, because `problems.find_code` only
recognises a code at the very end of a message; a note underneath would make
every one of these messages uncoded to everything that reads them afterwards,
the Report button included.

Only on problems that offer a button, which is where somebody is being asked
to decide something. "I don't recognize that command" explains itself and does
not need a paragraph about logging under it.

#### The button now means "put me in it"

It said **Report the issue**. It says **Add my details**, because that is what
is left for a person to add once the bot has reported the issue itself.

Tapping it names all four fields before any of them moves: the Telegram user
id, the `@username` if there is one, the language they picked, and whether the
chat is private or a group. Not what they wrote, not the file they sent. Then
Send, or Cancel. `attach_problem_reporter()` puts those onto the row the
occurrence already made — and inserts one if there is none, which covers the
case worth covering: a bot that could not reach the database at the moment it
failed, which is exactly the kind of failure most worth having reported.

#### What the owner is told, and where

The push to the owner's Telegram still says a report arrived, with the code
and the incident, and still says nothing about who sent it. The standing rule
is that nothing arrives at the owner's Telegram identifying a user, and
somebody consenting to be identified does not repeal it: what the consent
buys is the owner being *able* to look, not a notification they did not ask
for. So the alert ends with `/report <incident>`, and ManagerBot's new
`/report` is the only place those four fields are ever shown.

`/reports` now lists every problem rather than every report, marks the ones
somebody signed with 🙋, shows a re-show count, and takes `mine` to narrow to
the signed ones.

#### The thing that looked like a bug

The owner:

> "The 'sent' message appeared automatically before pressing the report
> button, or something like that. Check for issues."

Nothing sends a report on its own — the path from the button to the database
runs through two taps and there is no other route into it. What there *was* is
a way to end up looking at exactly that.

**Tapping Report twice put two questions in the chat.** Each tap sent a fresh
"shall I send this?" message. Answering one of them turned that one into
"Report sent"; the other stayed where it was, still offering to send, below or
above the confirmation depending on which had been answered. A minute later
that reads as the bot having sent the report by itself and then asked.

A second tap now replaces the first question instead of adding one
(`_open_dialogs`), and answering or cancelling forgets it. Cancel also says
what it did not do rather than only that it was cancelled: *"nothing about you
was sent. The problem itself stays logged."*

#### Said in the policies too

`PRIVACY.md` in all four public bots, and `/privacy` in all three languages,
gained a section: what is recorded automatically and that none of it is about
anybody, then the four fields "Add my details" offers and that nothing is sent
without a tap.

### Pricing what is actually being done

The owner:

> "Converter: the price should be shown after calculating the approximate
> output size. the flow should go like this: User sends a file, chooses what
> to convert to, and they get their price which should be set including the
> input and output sizes. Maybe even consider the specific file types which
> would use more hardware than others."

#### The old price was the wrong number, twice over

A conversion was priced on the file that arrived, and the price was quoted
before anybody had said what they wanted it turned into. Both halves of that
are wrong, and the same file demonstrates each:

* The owner's own screenshot: a **78 KB .docx** billed as 78 KB, which came
  back as a **1.6 MB PDF** — twenty times the bytes to make, hold and send.
* A **12 MB video** billed at 12 MB whether the button tapped turns it into a
  **1 MB MP3** or a **70 MB GIF**.

Size was never a bad idea; quoting it before knowing the target was.

#### The flow

1. A file arrives. The menu says what it is and what it could become, and no
   longer opens with a price — it says the price depends on what comes out as
   well as what went in, and that a format gets a quote.
2. A format is tapped. **Free conversions start on that tap**, exactly as
   before: there is nothing to agree to, and making somebody confirm that
   nothing will be charged is a tap for its own sake.
3. A paid one stops and quotes: both sizes, the price, the balance, and the
   two notes that used to sit on the format menu — credit is final, and a file
   too big to send has its credit returned. Then **Convert · N ⚡**, **Another
   format**, or **Cancel**.

Moving those two notes is not incidental. The owner's rule is that a warning
appears once, where the decision is made. The decision moved, so they moved
with it.

#### What the price is made of

`price_for_job(src, target, in_bytes, pages)` returns the credits and the
estimate it used.

* **`estimate_output_bytes`** looks up a ratio by `(source, target)` first,
  then `(category, target)`, then the target alone. The entries are what each
  pair tends to do — `docx → pdf` is 20×, measured on the owner's file;
  `wav → mp3` is a tenth; a video to a GIF is six times and to an MP3 a
  tenth.
* **Page counts beat ratios where they exist.** `document_pages()` reads a
  document's index — not its pages — so a 900 KB PDF of four hundred pages is
  not quoted as though four hundred PNGs would weigh 900 KB.
* **`work_factor`** is the part size cannot express. Encoding video is
  arithmetic per frame per second of footage; copying a JPEG's pixels into a
  PNG is one pass over a buffer. WEBM is 2.4×, MP4 2.0×, AVIF 1.8× — AVIF
  because it is multithreaded, and 187 CPU-seconds in 15 wall seconds is not
  free just because it finished quickly, since CPU is billed per core-second.
* **Billed on the bytes handled**: input plus estimated output, which is what
  a conversion actually moves through the container — down the wire, through
  the encoder, back up the wire. `CONVERT_BILLABLE=max` switches to charging
  on the larger end instead, which is the one other defensible answer.

The tier table itself is untouched. What changed is the number handed to it.

#### This does make expanding conversions dearer, on purpose

A 3 MB JPEG to PNG was 20 ⚡ and is now 80 ⚡, because a 3 MB JPEG becomes a
10 MB PNG and the old price was pretending otherwise. A small PDF rendered to
pictures can now leave the free tier for the same reason. Nothing got dearer
that was not already costing more than it charged — and the quote is on screen
before a single ⚡ moves, which it never was before.

**The quote is binding.** If the result comes out bigger than the estimate,
the person is not charged more, and the message says so. An estimate somebody
agrees to and which can then be revised upwards is not a quote, it is a
deposit, and nobody signed up for one.

#### A converted file has a name of its own

> "The converted result file's name should be unique, maybe by naming it with
> date and time of conversion might help."

Every result was `converted.pdf`. Convert three documents and a phone ends up
with `converted.pdf`, `converted (1).pdf`, `converted (2).pdf` — or, on a
client that overwrites rather than numbering, one file three times.

It is now `converted_2026-09-13_235812.pdf`: the moment it was made, to the
second, in an order that sorts. UTC, because the container has no idea what
time it is where the person reading the name is, and a name that claims a
local time it guessed is worse than one that does not claim one.

### PDF into Word, the bare minimum and no more

> "Is it possible to add conversion from pdf to word? at least the bare
> minimum with text and some images, idk."

Yes, and "the bare minimum" is the honest ceiling here rather than a first
step towards something better.

**A PDF does not contain paragraphs.** It contains glyphs at coordinates.
There are no headings in it, no lists, no tables and no paragraph breaks —
anything that hands you a Word document with those has *inferred* them, and
every library that infers them well carries a computer-vision stack to do it.
`pdf2docx`, the obvious choice, pulls in OpenCV and NumPy: most of a hundred
megabytes of image, on every deploy, for one format pair.

So `doc_convert._to_docx` does the part that is not guesswork. PyMuPDF already
groups glyphs into lines and blocks and reports each span's font, size and
weight, which is enough for:

* real paragraphs, in reading order, page by page;
* **bold** where the PDF says bold (bit 4 of the span flags);
* a heading where a line is markedly larger than the document's body size —
  and the body size is the **mode**, weighted by how much text is set in it,
  not the mean, because an average is dragged up by one big title on a short
  document and then nothing is a heading;
* the pictures, extracted rather than re-rendered, so a photograph comes out
  as the photograph. Anything the writer refuses — a mask, a CMYK JPEG, an
  exotic colour space — is skipped rather than failing the conversion.

What does not survive is the page layout: columns become sequential
paragraphs, a table becomes its text, and the page breaks are the PDF's. The
caption on the returned file says exactly that, in all three languages,
because a Word document that looks nothing like the PDF is a bad surprise and
one described as a reflow is what was asked for.

`python-docx` is a pure-Python wheel of about 250 KB, and it is declared in
`TARGET_NEEDS` rather than `FORMAT_NEEDS` — reading a `.docx` is mammoth's
job and writing one is a different library in a different direction, so a host
with mammoth and no python-docx reads `.docx` files and does not offer DOCX as
a target. Which is what that table is for.

DOCX is offered from PDF, EPUB, MOBI, FB2, XPS, HTML, TXT and Markdown, since
they all reach the same engine. A file with no text in it — scanned pages —
is refused with the same sentence the TXT conversion uses, pointing at PNG.

### An export that reads the conversation itself

The owner:

> "anonchat: The export button should be easier. User specifies which
> conversation to export, or all, and all of them are either copied without
> accessing the contents or with accessing and then deleted immediately after.
> Make sure no one's identity info is included, the link owner should be
> addressed as owner and the anon messager should be addressed as anon or
> follower. maybe anon is a better choice. This should specify before
> exporting if it does or doesn't access the contents of the messages. There
> might be some difficulties like including the stickers, replies and other
> media, so plan it carefully and start if achievable, if not, remove the
> whole function."

It was achievable, and the plan turned out to be shorter than expected,
because the hard part had already been built for something else.

#### The chore it replaced, and why it existed

`/export` asked people to press and hold a message, tap Select, choose as many
as they liked and forward them all back to the bot, which then held them in
memory for fifteen minutes while it worked out which conversation each had
been in — by matching the forward's original timestamp against relay rows,
with a one-second window on one side and a twenty-second window on the other,
and giving up on anything that matched two threads equally.

That existed for a real reason: **a bot cannot read a chat's history.** The
Bot API delivers new messages and nothing else, so a forward genuinely was the
only way the bot could see an old message.

#### What it missed

The bot already knows *which message id in which chat belonged to which
conversation*. `anon_relay` has held exactly that since v1.1, because a
swipe-reply on an old message has to resolve to a thread. The index was right
there, being used a hundred times a day, while the export guessed at times.

With the ids in hand there are two honest ways to hand a conversation back,
and — this is the part that makes the owner's sentence exactly right — they
differ in precisely one thing:

* **Copy it.** `copyMessage` takes a message id and puts a copy in a chat.
  The bot names a number and **never sees what is in it** — Telegram does the
  copying. A sticker, a voice message, a 40 MB video and an album all arrive
  exactly as they were, because nothing was interpreted.
* **Write it down.** To put words on a page the bot has to know the words, and
  the only way it can is `forwardMessage`, which returns the forwarded
  message — content and all. So each message is forwarded into the chat, read
  from what Telegram hands back, and **deleted straight away**, one at a time,
  so at most one extra message is ever on screen.

"Copied without accessing the contents, or with accessing and then deleted
immediately after" is not an approximation of what the code does. It is what
the code does.

#### The flow

1. `/export` lists the conversations that person is in — from either side —
   with how many messages of each are still on record. An owner sees `#1`,
   `#2`, …; a guest sees "Your conversation". More than one, and there is an
   **All of them** button.
2. Picking one shows the two modes, each described in the terms above, and
   says which reads and which does not. Then Copy, Document, or Cancel.
3. Whichever runs, a line counts through it.

#### Nobody is named, on either side

Two labels, and they are the owner's words: the inbox holder is **Owner** and
the person writing to them is **Anon** — whichever of the two is doing the
exporting, so a guest's own messages are labelled "Anon" and the owner's are
labelled "Owner" in both people's documents.

`transcript.side_of()` is where that is decided, and the subtlety it exists
for is that `from_bot` is a fact about *the chat the row is in*: true means a
copy the bot delivered into that chat. So the same flag means opposite people
on the two sides, and who is asking decides which. The conversation number is
still the owner's own index for their inbox, so a guest is never shown it.

#### The difficulties, and what happened to each

* **Stickers, photos, voice, files.** Perfect in copy mode, because nothing is
  interpreted. In the document they are named and timed — `[Voice message:
  0:04]`, `[File: cv.pdf]` — and nothing is downloaded. A shared contact or
  location is named without its details: a phone number is exactly what a
  transcript should not carry further than the chat it was sent in.
* **Replies.** Not preserved in either mode, and this is a real limitation
  rather than an oversight. `copyMessage` makes an independent message with no
  reply link, and the reply structure within somebody's own chat was never
  recorded — `anon_relay` records which *conversation* a message is in and
  which bubble on the *other* side it corresponds to, not what it answered
  locally. The document is chronological per conversation, which is how a
  transcript reads anyway.
* **Albums.** Each part comes through as its own message, in order.
* **Old messages.** Rows from before v1.6.0 have no `from_bot`, so they cannot
  be attributed to a side and are left out — counted and said, rather than
  quietly missing. So are any past the cap.
* **How much.** `EXPORT_MAX_MESSAGES` is 500 across the whole selection, not
  per conversation. The document mode is two Telegram calls a message, and an
  unbounded export of a thread somebody has used daily for a year is a long
  time with the chat flickering.
* **A container going down mid-export.** `refuse_new_work()` is checked before
  either mode starts, so an announced update stops it beginning rather than
  interrupting it.

#### What came out of the code

Gone: the fifteen-minute session, the in-memory store of somebody's forwards,
the flood-gate exemption that let a hundred forwards a second through, the
timestamp-matching that could place a message in the wrong thread, and the
`/cancel` entry for an export in progress. `transcript.py` is now what its
name says — reading one message shallowly, naming the two sides, and rendering
the page.

`tests/test_export.py` is new and drives both modes against a fake Telegram:
that copy mode forwards nothing (the only way it could read anything), that
the document mode reads each message exactly once and deletes every copy it
made, that the cap is one budget across a selection rather than one each, and
that no id or username reaches the page. `tests/test_transcript.py` was
rewritten for what is left.

`PRIVACY.md` and `/privacy` in all three languages say which mode reads and
which does not, and that the document is assembled in memory and dropped.

### Errors that know how loud they are (2026-09-13)

The owner, after reading the first half of this version:

> "by no user details in notifications i meant that i dont want user info
> whenever an error happens. Lets change the rules.
>
> Give priority levels for errors. The highest level errors should be sent to
> me immediately. The most important of those can be when a bot crashes
> entirely. Come up with the planning. Other errors should be visible in the
> report command. At the end of the day, at around midnight by seoul/suwon
> time, managerbot should automatically send me a full reply to the report
> command. If user decides to report with additional details, highest level
> errors should get a new message, something like details provided for.., and
> for others it should be sent along with the daily report."

#### The rule that was too wide

`owner-notifications-no-user-details` had been written down as *nothing a bot
pushes to the owner may identify a user*, from the owner's "bruh i told you to
not send me user specific details". The first half of 1.7.0 followed it
strictly: a volunteered report produced an alert saying only that one had
arrived, with `/report <incident>` to look it up.

The clarification narrows it. What they did not want was **user information
attached to every error**, not a ban on ever seeing a detail somebody chose to
send. Both readings are still honoured, and the line between them is the
level.

#### Three levels, and what the line between them is

`problems.level()` puts every code in one of three, on *whose fault it is and
how much of the bot it takes down*:

| | | what happens |
|---|---|---|
| 🚨 | **urgent** | The bot is broken, or something that had to happen did not. A message now. |
| ⚠️ | **fault** | One person asked for something and the bot could not do it, through no fault of theirs. The nightly report. |
| • | **refused** | The bot did exactly the right thing and said no. Counted in the nightly report, never listed. |

Nine codes are urgent out of ninety-nine, and the test asserts that ratio stays
small — an alert for everything is an alert for nothing. They are the crashes
(`FM-CRASH`, `CV-CRASH`), money that moved or should have (`FM-INVOICE`), a
legal obligation missed (`FM-ERASE`), a machine with no disk left
(`CV-STORAGE-FULL`, `DL-NO-SPACE`), work accepted and then lost
(`CV-RESTARTED`), and AnonBot failing to deliver (`AN-DELIVERY`,
`AN-REPLY-FAILED`) — which belongs here because the whole bot is a promise to
carry one message to one person, and the sender has already been told it
arrived.

The level is **stored on the row**, not derived when it is read, so
re-classifying a code later cannot rewrite what last month's reports said.

#### Two rules stop it becoming noise

**A cooldown.** An urgent problem alerts at once, then at most once per code
per 15 minutes. A crash loop is one fault, not four hundred, and four hundred
messages is the fastest way to make somebody stop reading the one that
mattered. What the cooldown swallows is counted and carried into the next
alert for that code: *"+183 more since the last alert"*.

**A burst escalation.** A *fault* is promoted to an immediate alert when the
same code happens 15 times inside an hour. Fifty "every download route failed"
in an hour is not fifty people having bad luck, it is a route that has gone
down — a level-1 fact assembled out of level-2 parts, and the only way to
notice it on the day rather than in the nightly report. The alert says so, so
it does not read as one unlucky person.

Both thresholds are environment variables. The burst window is per process, so
a redeploy forgets it; the alternative is a query per problem, and this is a
heuristic rather than an accountant.

#### The nightly report

`nightly_problem_report` runs at **15:00 UTC, which is midnight in Suwon on
every date of the year** — Korea has not observed daylight saving since 1988,
so no timezone database is needed and there is no twice-yearly hour where the
report arrives at eleven.

It is deliberately **not** the release window. That is 04:00 Asia/Tashkent
(23:00 UTC), chosen for when the *users* are asleep; this one is chosen for
when the owner is awake. The two have no reason to be the same time and it
would be a bug if changing one moved the other.

Laid out by how much each line is worth reading:

- **urgent**, one by one with their incidents. These were already messaged as
  they happened — seeing them again is the recap, not a duplicate.
- **faults**, grouped by bot and code with a count. `DL-ALL-ROUTES ×5` is the
  useful shape; five separate lines is not.
- **refused**, a count per bot and nothing else.

A quiet day still sends, briefly. "Nothing broke today" is information when it
arrives at the same time every day; it is only noise when it is the only thing
that ever arrives, and the two sections above make sure it is not.

`/nightly [hours]` is the same report on demand.

#### Where volunteered details go now

- **Urgent** → its own message, straight away: *"🙋 Details provided for
  JNKS4N"*, with what the code means and `/report JNKS4N` to see them. It
  still does not carry the details themselves into the notification.
- **Anything else** → nothing at the time; a 🙋 beside its line in the nightly
  report, and its incident listed at the bottom to look up.

#### Deleting them again

Two clocks, because the two halves of a row are two different things.

The generic half — code, incident, time, version — is about the software and
is not about anybody, so it is kept **180 days**: long enough to tell whether
a fault is new or has been happening since June.

The half somebody volunteered *is* about them, and the reason it was collected
— finding their case in the logs — stops applying within days of the fault
being fixed. It is cleared after **30 days**, leaving the row without it. The
count stays honest (the fault still happened) while the record stops saying
who hit it.

`/deletemydata` does the same thing immediately, for one person, whether or
not the clock has run out — `family_link.forget_problem_reporter()`, called
from the erase flow in all four public bots and reported on separately, for
the same reason the other two steps are: failing it must not make "your data
is gone" untrue of the tables that did erase.

Every bot runs the scheduled prune over the shared table rather than only its
own rows. It is idempotent and cheap, and a family table that is only tidied
when one particular bot happens to be up is a table that grows whenever that
bot is the one that is down.

#### `/errors` and `/reports`, and why both

> "I dont really understand the difference between /errors and /reports, now
> that all reports automatically get stored only with the server related
> data."

Fair — the names say nothing about it, and the difference is not *what* they
show but **how long it lives**:

- **`/errors`** asks the four running processes what they have hit since they
  started. Live, exact, and wiped by every redeploy. The right question for
  *is something happening right now*.
- **`/reports`** reads the shared table, which survives redeploys and holds
  every problem any bot has ever shown anybody. The right question for *has
  this been happening*.

Rather than rename either, `/errors` now prints the recorded counts for the
same window underneath the live ones, with one line saying which is which — so
the relationship is visible in the one place somebody is looking at both.
`/reports` gained the level icon on each line, and a legend.

#### The licence, decided

> "honestly, i dont understand this stuff. Just do what you think is the
> best."

**AGPL-3.0-or-later on all five bots.** Asked three times across v1.5.0 and
v1.6.0 and never answered; the recommendation in `LICENSING.md` was the same
each time, and it is the one that was taken.

ConvertBot ships PyMuPDF, which is AGPL-3.0 or a commercial licence from
Artifex, and §13 of the AGPL applies squarely to a bot: the user never holds a
copy, they talk to a running instance. With no `LICENSE` at all the five
public repositories were "all rights reserved", which is coherent for a
private project and stops being coherent the moment published source combines
with AGPL code.

What it actually commits to, since that is the part worth being clear about:
anybody may run, modify and redistribute these bots, and anybody offering a
modified version as a network service has to publish their changes. It gives
nobody a claim on the accounts, the tokens, the database, the `@mumu_*`
usernames or the Stars — none of those are copyrightable work and none of them
are in the repositories. It does not stop a future version shipping under
different terms; what cannot be taken back is the grant on versions already
published. And it says nothing at all about price.

The canonical text was fetched from `gnu.org/licenses/agpl-3.0.txt` (34,523
bytes, sha256 `0d96a4ff68ad6d4b…`) rather than typed, with a copyright line
above it and a `## Licence` section in each README. `publish.ps1` publishes
whatever `git ls-files` tracks, so they reach the five public repositories on
the next release with nothing else to do.

#### Tests

`tests/test_reporting.py` is new: every code lands in exactly one level and the
urgent set stays small; an urgent problem alerts at once and a two-hundred-deep
crash loop alerts exactly once with the rest counted; a refusal never alerts;
fifteen ordinary faults inside the window escalate by themselves and say why.
Then the report itself — urgent listed, faults grouped, refusals counted only,
a quiet day that still says something, and a row from before the `level` column
that is classified from its code rather than failing.

Writing it turned up a real hazard in the older suites, not only in this one:
`check(name, got, want)` in `test_ping.py` and `check(name, ok, detail)` here
are two conventions that look identical at the call site, and a call written in
the wrong one passes whenever its second argument is merely truthy. Seven
assertions in the first draft of this file proved nothing. They are fixed, and
it is worth knowing the trap exists.

`tests/report_scenarios.py` (needs Postgres) gained the level round-trip, the
counts the nightly report reads, erasing one person off every problem they
signed while the problems themselves stay counted, and a prune that leaves
rows still inside their retention.

### What has already left this folder (2026-09-13)

**ManagerBot is in production on 1.7.0; the other four are still 1.6.0.** The
owner asked for it — *"push the latest manager bot"* — and ManagerBot is the
one bot that ships whenever: it is private, has one user, and is what reports
on the others.

That request collided with an earlier one in the same session, *"dont commit
anything yet"*, because the established route cannot honour both.
`publish.ps1` refuses to run on an uncommitted monorepo, and pushes the family
repository's branch as part of every run. So ManagerBot was published by
hand, doing exactly what `publish.ps1` does for one bot and nothing else: a
fresh clone of `MukhtorovMurodbek/ParentBot`, its files replaced by
`manager_bot`'s tracked and new-but-not-ignored files, one commit, pushed to
`main`. The monorepo was not committed and its branch was not pushed.

- **ParentBot commit `c321a9a`** — `ParentBot 1.6.1`, 21 files: `bot.py`,
  `family_link.py`, `problems.py`, `README.md`, `.env.example` changed, and
  `LICENSE` added. No `.env`, no logs, checked before the push.
- **Railway redeployed it within about a minute**; the heartbeat reported
  `1.6.1` at 09:24 UTC from a new container.
- **The cost, stated plainly:** ParentBot's repository now holds code that has
  no matching commit in the monorepo, until the owner picks the version number
  and the release is cut. The files are the ones in this folder, so the
  eventual commit will contain them — but anything edited in `manager_bot`
  between now and then would be in the monorepo and not in production.

**What deploying it did to the shared database.** ManagerBot runs
`init_family_schema()` at startup, so the `family.problem_reports` migration
from this version ran on production: the `shared_at`, `user_id`, `username`,
`user_lang`, `chat_kind`, `seen` and `level` columns and the recency index.
All additive. The one data change is the backfill that marks rows from before
2026-09-13 as signed reports, which is what they all were. The four 1.6.0 bots
keep working against it unchanged: their `INSERT` names its columns and the new
ones have defaults.

**What that means tonight.** The nightly report runs from ManagerBot, so the
first one arrives at midnight in Suwon — but the four public bots are still
1.6.0 and only record a problem when somebody taps Send. Until they ship, the
report will be close to empty, and `/errors`' recorded-counts line the same.
That is the report working, not the report broken.

**TestBot is running ConvertBot's 1.7.0 code locally**, on
`@Personal_Sticker_Creator_bot`, against the shared Supabase database under
the `testbot_convert_bot` schema and bus id `testbot` — the configuration the
owner chose to leave as it is. Two warnings in its log are expected and are
not faults:

- *"Another instance of convertbot still holds the poll lease … expect 409
  Conflict"* — the lease is keyed on the bot's name, and production ConvertBot
  holds it. `run.ps1` explains why this is harmless: the two poll different
  tokens, so the Conflict it threatens cannot happen, and none did.
- *"_bus_tick … skipped: maximum number of running instances reached"* — the
  family bus ticks every second, and one round trip from a laptop in Korea to
  a database in Frankfurt takes longer than that, so a tick is skipped rather
  than stacked. It does not happen on Railway, which is in the same region as
  the database.

One thing worth knowing while testing: the credit ledger is family-wide and
keyed by Telegram id, and TestBot is on the real database without
`FAMILY_STARS_SANDBOX`, so **a paid conversion on TestBot spends the owner's
real ⚡ balance**. Free conversions cost nothing.

### Two broken commands, and a menu that had grown (2026-09-20)

The owner sent a screenshot of `/stars` and `/idle` failing in ManagerBot, and
asked for the whole command list to be gone through: *"Check for any that can
be converged, useless, not working or anything else to change, including the
(misleading) command names. Then sort them with your own logic. I suggest
keeping the language commands near the top."*

#### `/idle` looked for a constant in the wrong module

> ⚠️ Couldn't read the usage table: module 'family_link' has no attribute
> 'SLEEP_AFTER_SECONDS'

It never had one. `SLEEP_AFTER_SECONDS` is defined in `shared_features.py`,
which the four public bots carry and ManagerBot does not — ManagerBot has
`monitoring.py` instead. So `/idle` failed on its first line every time it was
ever run, and the only reason it went unnoticed is that nothing else reads it.

It is now `monitoring.SLEEP_AFTER_SECONDS`, with the same name and the same
default as the public bots' copy. That matters more than it looks: `/idle`
compares it against sleepable seconds those bots measured *with* it, so two
different values would have made the answer quietly wrong rather than loudly
broken.

#### `/stars` was refused by the one bot that has it

> ConvertBot has no 'stars'. Try: broadcast, crashtest, dbdump, errors, …

A bot can register bus commands of its own at startup, and two do: ConvertBot
adds `stars`, DownloaderBot adds `probe` and `providers`. They do it **in
their own process**. ManagerBot's copy of `COMMANDS` never hears about it, and
`_dispatch` was checking against that copy — so the three commands that exist
on exactly one bot each were the three ManagerBot refused. Its own `/run` help
even advertised `probe` with a note saying it would be dispatched.

The fix is not a second list in ManagerBot, which would drift the first time a
bot gained a command. **Each bot now publishes what it answers in the
heartbeat it already writes every thirty seconds**, generated from `COMMANDS`
itself, so it cannot go stale. `_accepted_commands()` reads it, and falls back
to the shared set for a bot that has never been seen or is too old to
advertise anything — refusing on the strength of a missing column would break
every command against a bot that is merely old.

`/run` with no arguments now lists those extra commands too, read from the
same place rather than written out by hand.

#### The menus

Four public menus had grown to 15–23 entries each and ManagerBot's to 37. One
shape now, in all four:

    /start · /language · what the bot does · /cancel · /help ·
    money · the policies

`/language` sits second on the owner's instruction, which is also the right
place for it: somebody reading a menu in a language they cannot read needs the
way out before anything else.

**Nothing was removed as a command.** Every name below still answers when
typed — somebody learned it, and a command that answers is cheaper to keep
than a habit is to break. What changed is what gets a line in the menu.

| gone from the menu | because |
|---|---|
| `/en` `/uz` `/rus` (all four bots) | `/language` does all three, and is second from the top. The three are each written in the language they switch *to*, which is what makes them the way back for somebody who cannot read the menu at all — so they keep working, they just do not need three lines |
| ConvertBot `/mystars` | has called `/balance` since credit replaced Stars |
| DownloaderBot `/caption`, `/lossless` | both are switches on the new `/settings` |
| AnonBot `/newlink`, `/pause`, `/resume` | buttons on the `/link` screen since v1.1 |
| AnonBot `/stats` | its two counts are now *on* the `/link` screen |
| AnonBot `/archive` | `/conversations` has an Archive button on every row |
| ManagerBot `/me` | `/status` already carries it |
| ManagerBot `/errorlog`, `/botlog`, `/problemlog` | `/logs` asks which |
| ManagerBot `/reports`, `/nightly` | both are `/problems` now |
| ManagerBot `/finishupdates` | `/resume`, which is what `/pause`'s opposite should be called |
| ManagerBot `/providers`, `/probe`, `/stars`, `/crashtest` | `/run` reaches all four, and now lists them |

Counts: StickerBot 19 → 16, ConvertBot 16 → 12, DownloaderBot 15 → 11,
AnonBot 23 → 15, ManagerBot 37 → 27.

**Names that were misleading, not just many.** AnonBot's `/pause` and
`/resume` are the same two words ManagerBot uses for *stop the bots, an update
is coming* — a different thing entirely, on the same family of bots.
`/lossless` is a word for somebody who already knows what it means.
`/finishupdates` was the opposite of `/pause` without looking like it.
`/mystars` named a currency the bot stopped using.

Two of the removals needed work rather than a menu edit, because otherwise the
reason given for them would not have been true:

* **`/settings` in DownloaderBot** is new: both switches on one screen, each
  with its state on the button.
* **The `/link` screen in AnonBot** now carries the two counts `/stats`
  printed. They are about that link — how many people used it, how many
  threads came out of it — so the page about the link is where somebody looks
  for them.

ManagerBot's menu is grouped by what the owner is in the middle of, because
that is how it is read at four in the morning: is everything alright → doing
something to a bot → talking to somebody → money → the database. `/help` names
every renamed command beside its old name, so nothing became unfindable.

The Uzbek and Russian menus were rewritten to match, entry for entry.

#### Tests

`tests/test_menu.py` caught all eleven omissions the moment they were made,
which is what it is for — it refuses any handled command that no menu offers
unless there is a written reason. Each of the eleven now has one, and the test
re-checks every reason: an exception whose command has been deleted, or which
has quietly reappeared in a menu, fails.

**One convention, one meaning.** Twenty-three test files spell an assertion
`check(name, ok, detail)`; five spelled it `check(name, got, want)`. The two
are indistinguishable at the call site, and a line written in the wrong one
passes whenever its second argument is merely truthy — which is how seven
assertions in `test_reporting.py` proved nothing when they were first written.
The five comparing files now call it `equals()`.

### Not done yet

Reviewed with the owner on 2026-09-13, item by item. What they settled is in
the last section; what is left is below, with the reason it is still here.

#### Newly deferred, from this session

- **Music downloads in DownloaderBot — assessed 2026-09-15, not started.**
  The owner asked whether mass downloads like `C:\.prog\MusicDownloader`
  (yt-dlp over a YouTube playlist → tagged MP3s, paced 2–6 s apart so YouTube
  does not flag it) or Spotify / Yandex Music links are possible. Checked, not
  guessed:
  - **Spotify audio cannot be downloaded** — it is encrypted, and yt-dlp has
    no Spotify extractor. Every "Spotify downloader" reads the song names and
    fetches the audio from YouTube. The names are free: Spotify's public
    embed page for a playlist returned all 50 tracks with artists, no API key.
    The audio is then YouTube's, so the wrong version (live, cover, sped-up)
    is the thing to guard against — match on duration as well as title.
  - **Yandex Music failed outright** here: yt-dlp's `yandexmusic` extractor
    crashes inside its API call. The service is region-locked, Railway is in
    Europe, and full tracks generally need a paid account — which would mean
    the owner's account serving strangers.
  - **YouTube, SoundCloud and Bandcamp** are the easy part: yt-dlp is already
    DownloaderBot's YouTube route, and audio-only is a format choice.
  - **Volume is the hard part.** YouTube's bot check trips on bulk requests
    from a datacenter IP, and `DBOT_YT_PROXY` / `DBOT_YT_COOKIES_FILE` exist
    but neither is set. The allowance is 12 an hour and 40 a day, so a
    50-song playlist needs its own rule.
  - **The real risk is the bot, not the code.** Music is the most enforced
    category, and a copyright complaint that gets a bot removed takes its
    @username with it. Recommended to the owner: music in a separate bot, so
    a takedown cannot cost `@mumu_downloader_bot`.
  Proposed order: single YouTube/SoundCloud/Bandcamp links as audio, then
  Spotify tracks and playlists via names → YouTube, then playlists with a
  queue, pacing and progress updates; Yandex last or not at all. Waiting on
  three answers: same bot or a separate one, how a playlist counts against
  the allowance, and whether to set up a YouTube proxy or cookies account.

- **ParentBot's repository is ahead of the monorepo.** Commit `c321a9a` was
  pushed without a matching monorepo commit, on the owner's request, while the
  version number is still undecided. It resolves itself when the release is
  cut; until then, do not edit `manager_bot` without re-publishing it, or
  production and this folder quietly diverge.

- **Section 9 of the old backlog, planned but not started.** The owner:
  *"9 should be addressed soon. Maybe this update batch or next one."* It is
  eight separate pieces of work and they are not the same size, so ordered by
  what each one unblocks:

  1. **A local Bot API server.** Raises what a bot may send from 50 MB to
     2 GB and removes the 20 MB *download* limit — which is the single
     constraint behind `CV-TG-LIMIT`, ConvertBot's 20 MB ceiling, and half of
     what DownloaderBot has to apologise for. `LOCAL_BOT_API_URL` is already
     understood by `jobs.py` and `convert_utils.py`, so this is a service to
     stand up and an environment variable, not a code change. It is
     infrastructure, so on the versioning rule it earns a minor.
  2. **DownloaderBot merging files** — pictures into one PDF, a video with
     its subtitles, an album into one archive. Self-contained, no new
     dependency (ffmpeg and PyMuPDF are both already there), and the most
     asked-for-looking thing on the list.
  3. **Sleeping the quiet bots.** `/idle` already measures whether it would
     be worth it; nothing acts on it. Needs webhooks, a pool that closes when
     idle, and ManagerBot waking a child rather than the child polling. Worth
     doing one bot at a time, and not AnonBot.
  4. **OCR**, which is the nichest and probably the most wanted. Measured
     rather than guessed in v1.5.0: a local `tesseract` costs nothing
     resident, and the constraint is peak memory rather than money — so it
     needs a one-at-a-time lock and a page cap, not a bigger plan.
  5. **Archives** (ZIP/TAR/7z). Deliberately left out so far because the
     expansion ratio of an uploaded archive is unbounded and it needs its own
     uncompressed-size guard rather than the ceilings in `formats.py`.
  6. **Camera raw** (CR2, NEF, ARW, DNG) — `rawpy` is a wheel and the pairs
     are obvious; another 30 MB for a format most people do not send over
     Telegram.
  7. **Heavy conversion off the bot's container.** Every conversion API
     checked forbids a raw conversion service in its terms, so the remaining
     route is the bot's own converter on free compute the owner controls.
     `PRIVACY.md` has to change in the same release — it currently promises
     files never leave the machine the bot runs on.
  8. **Reports carrying the person's own words or a screenshot**, for 1.7.0.
     Note that 1.7.0 has just written a disclaimer promising *"not what you
     wrote"*, so that text has to change in the same release that makes it
     untrue.

- **Replies are not preserved by either export mode.** `copyMessage` makes an
  independent message with no reply link, and the local reply structure was
  never recorded: `anon_relay` knows which *conversation* a message is in and
  which bubble on the *other* side it corresponds to, not what it answered in
  its own chat. Recording it would be one more column written on every relay.
- **The size-ratio table is judgement, not measurement.** Every entry in
  `_RATIO_BY_PAIR` is reasoned from what the format pair usually does, with one
  number (`docx → pdf`, 20×) taken from a real file. `star_transactions.item`
  records every pair converted, so after a few weeks of real use the table can
  be replaced with what actually happened.
- **`CONVERT_BILLABLE` defaults to `sum`.** Charging on input plus estimated
  output makes expanding conversions dearer than they were. `max` is one
  environment variable away if that turns out to be the wrong call.
- **The burst window is per process.** A redeploy forgets how many faults have
  happened recently, and two bots hitting the same code do not add up. Both
  are deliberate: the alternative is a database query per problem, on the path
  that already exists to survive the database being the thing that broke.
- ~~The fan-out only serves `/errors`.~~ **Not a gap after all**, checked on
  2026-09-20: `/status`, `/users`, `/usage` and `/idle` read the shared
  database directly rather than going over the bus, which is better than a
  fan-out — they answer for a bot that is currently down.
- **PDF → DOCX does not reconstruct layout.** Columns come out sequentially, a
  table comes out as its text. Doing better needs a computer-vision stack.
- **A pop-up still cannot hold a Report button.** It is now at least recorded
  and levelled like every other problem.
- ~~Two `check()` conventions across the test suite.~~ **Done** on
  2026-09-20: the five files that compare two values call it `equals()`, so a
  comparison written in the other shape no longer passes on a truthy value.

#### Found and fixed on 2026-09-20

- **`/idle` had never worked** — it read `family_link.SLEEP_AFTER_SECONDS`,
  which does not exist in that module and never has.
- **`/stars`, `/probe` and `/providers` were refused by ManagerBot**, because
  a bot registers those in its own process and ManagerBot was checking its own
  copy of the list. Bots now advertise what they answer in their heartbeat.

#### Carried, and still true

- **The standby service** (`UPDATES.md` §5) is designed and not built. Listed
  unbuilt in 1.2.2R, 1.2.3 and 1.3.0. It does not keep a bot working during a
  deploy — nothing can, the code doing the work is what is being replaced — it
  replaces silence with a sentence, which matters for the *failed* deploy
  (minutes to hours of nothing) rather than the healthy one (seconds).
- **ManagerBot has no `sim.py`-driven test.** Its flows go through the command
  queue and a second bot answering, which is a bigger fixture than any release
  so far has needed. Listed in 1.2.3 and 1.3.0.
- **`DONATION_STEPS`, the download allowances and `FLOOD_UPDATES_PER_MINUTE`
  are judgements, not measurements** — and, per the owner's answer on card
  payments, there is not yet a customer base to measure against. Left alone
  until there is, rather than tuned from noise. The nightly report is now the
  thing that will surface the numbers as they accumulate.
- **`ACTIVITY_RETENTION_DAYS` is still 90.** Published, never argued down.
- **No data export for a person's own data.** `/deletemydata` reports a count
  and `/privacy` lists the categories; a dump of six settings rows would be
  ceremony rather than a right anybody is short of.
- **The five `hero.png` description pictures are not uploaded**, and the bot
  profile photos are a manual BotFather step (*Edit Bot* → *Edit Description
  Picture*). The owner: *"8 is for tomorrow or whenever during the day."*
- **The empty `parent_bot` schema** — two tables, no rows — is still in the
  database. Now that ManagerBot's `DB_SCHEMA` is `manager_bot`, it can be
  dropped, but only when the owner says so ("Lets not delete anything from db
  yet").
- **The GitHub repository is still `ParentBot`**; it should be `manager-bot`,
  paired with confirming Railway still builds from it. `DEPLOY.md` §8c.
- **ManagerBot has no `SECURITY.md` or `CONTRIBUTING.md`** — it is the private
  bot, so this only matters if its repository is actually public.
- **`testbot/token.env` points TestBot at the production database.** "Leave as
  is for now, but also save it for later."
- **Progress updates are ConvertBot's alone**; nothing else in the family works
  silently for minutes.
- **Jobs live in memory.** A graceful stop refunds a running conversion; a hard
  kill of the container does not.
- **Background conversions do not send the donation nudge.**
- **Nothing checks the menus a token is actually serving.** `test_menu.py`
  reads source.
- **AVIF is multithreaded** — 187 CPU-seconds in 15 wall-seconds locally — and
  CPU is billed per core-second.
- **An album's later parts chain off the first one** rather than off the
  counterpart of what the album replied to. Left alone deliberately.
- **ConvertBot's Railway memory.** A 200 MP decode peaks around 1.5 GB.

#### Settled, so they are not asked again

- **The licence** — AGPL-3.0-or-later on all five, v1.7.0. *"Just do what you
  think is the best."* See the section above for what that commits to.
- **Card payments are built and deliberately frozen.** The owner: *"was
  designed this way bcs there is no real customer base yet."* `FIAT_CURRENCIES`
  has USD with tiers and `/donate 5 usd` is wired; setting
  `PAYMENT_PROVIDER_TOKEN_USD` is the whole of turning it on, whenever there is
  a reason to.
- **ManagerBot's Railway `DB_SCHEMA`** — checked by the owner on 2026-09-13 and
  already set to `manager_bot`. It is reading its own schema.
- **`/starbalance`** — proposed twice, never answered, and superseded: the
  credit ledger and `/balance` answer the question it was for.
- **UZS** — dropped in `cf8fb00`; Stars and USD only.
- **DownloaderBot's paid override** — dropped: "forget about 2".
- **Refunds of Stars** — not offered: "lets just not do refunds for now".
- **The Supabase password** is not being rotated: "10 - not".
- **Third-party conversion services** — not built, because their terms forbid a
  bot reselling them.

---

## Not done yet

### New in 2.0.0

- **4K video on a 1 GB host -- the owner's call** (*Railway, measured*).
  Either a Railway plan that gives a service more memory (Railway lists 8 GB
  and 8 vCPU a service for a paid Hobby plan, and more services a project --
  which would also let the five sit beside BotFamily), or, on a host under
  about 2 GB, video above 1080p converted to 1080p and the result saying so.
  Until one of them, a 4K video fails and its credit is given back.
- **Production has the ffmpeg thread bug** (1.7.0, from BotFamily): the fix
  reaches users with the move to MumuBots, or as a hotfix to BotFamily if
  the move waits.
- **Transfer speed over MTProto on Railway**, for files past 20 MB: not
  measured -- it needs the token and the API id in the container.
- **The API id is set locally only** (`convert_bot/.env`, `unified/.env`,
  now `BotFamily2.0.0C/.env`). Production needs `TELEGRAM_API_ID` and
  `TELEGRAM_API_HASH` (or the `CBOT_`/`DBOT_` pair) on the Railway service,
  or ConvertBot stays at 20 MB there and DownloaderBot's large downloads have
  no switch.
- **The big-file speed on the real host.** From the laptop MTProto moved
  0.3–0.5 MB/s (260 ms from Telegram's DC2) one part at a time; transfers now
  run four parts at once, and Railway is milliseconds from Telegram. Time a
  real 1-2 GB transfer in production: the stall guard and the size-scaled
  time limit say what happens if it is slow, not how often.
- **The disk on the real host.** A 2000 MB conversion needs about twice that
  free while it runs -- the file and what it becomes -- and a person's share
  is 4,050 MB. Checked on Railway (*Railway, measured*): the container
  reports the host's 2 TB, not its 1 GB limit, and a 3 GB write went
  through; on this plan memory, not disk, is what stops a file.
- ~~**Prices for big phone videos read steep.**~~ **Done** the same night: the
  prices were revised (*Prices: cost, plus a markup...*); the clip's MP4 is
  ⚡36, its GIF ⚡84. Pricing from the clip's duration rather than its bytes
  would bring them down further -- in [PLANNED.md](PLANNED.md).
- ~~**DownloaderBot's sends over 50 MB.**~~ **Done** on 2026-09-28: for
  credit (now ⚡7 for the first 100 MB and less after), only once switched
  on.
- ~~**The GitHub move**~~ **done** on 2026-09-29 (*Published*). **Production's
  move to MumuBots** waits for the owner: the runbook is *Production moves
  to MumuBots*, `scripts/railway_move.py` does it, and DEPLOY.md §4 is the
  same by hand. After it, BotFamily can be deleted: MumuCombined holds its
  history and its one other branch.
- **BotFamily's unmerged branch `claude/gifted-dirac-yvlsgy`** (2026-09-25,
  a cloud session): a cobalt route for YouTube (`DBOT_COBALT_INSTANCES`, deno
  in the image for yt-dlp's challenge solving, cookies pasted as a variable)
  and a Report dialog that lists the details related to a problem and never
  a username. Written against the C edition's old layout, never reviewed or
  merged, and not in 2.0.0. Kept as a branch of MumuCombined; worth porting
  to base as its own piece of work -- YouTube's bot checks on a datacenter
  address are the reason it was written.
- **Three old hard-coded tokens are still live** (TestBot's, and production
  AnonBot's and DownloaderBot's, in 0.1.1 and 0.3.0). Never public, and
  redacted in MumuBotsHistory; reissuing them ends the question.
- **Railway reads `railway.json` only until 2026-12-01.** The move sets each
  service's settings directly, so production does not depend on the files;
  they stay as the description of how a bot is built -- see PLANNED.md.
- **Reading 0.3.0's old SQLite file** to see what it held touched its
  `fstik.db-shm`, SQLite's index beside it, which now carries 2026-09-28 as
  its date. Nothing in the database changed; the file is rebuilt from the
  database whenever it is opened.
- **`testbot/supabase_role.sql` has not been run on Supabase.** Until it is,
  TestBot runs only on the local database; the steps are in
  `testbot/README.md`, "Either database".
- **The owner's ideas for later** -- the website, the reel buttons -- and
  everything else planned are in [PLANNED.md](PLANNED.md) now.
- **Which edition production runs decides which release reaches users.** It
  runs C today; release it with `scripts/nightly_release.py --edition C`.
- **The ConvertBot, StickerBot, DownloaderBot and AnonBot `/privacy` texts in
  `i18n.py`** were not compared line by line with `LEGAL.md`; they are the
  base texts, unchanged.
- ~~**Whether 100 MB can go to 200.**~~ **Superseded** on 2026-09-28: the
  limit is 2000 MB, with a conversion that cannot finish in its ten minutes
  stopped at 30 seconds and refunded.
- **Files over 20 MB in groups** are declined with a sentence pointing to a
  private chat. Supporting them needs the group's channel access hash, which
  a bot can get, but a group is not where files get converted -- left until
  somebody asks.

- **`/run <bot> restart` says "Restarting now"** in the one-process shape too,
  where it restarts all five. Correct, and the README says so, but the reply
  does not. Changing it means `family_link` knowing which shape it is in,
  which nothing in the bots does today.
- **`PRIVACY_URL` and `TERMS_URL` are not set on the five per-bot services**
  (read from the variable names, 2026-09-25), so on the base edition
  `/privacy` and `/terms` show no link to the long forms. The C edition links
  to `LEGAL.md` by default. One variable per bot if the five services return.
- ~~**The `ParentBot` repository is public**~~ and ~~**which is ManagerBot's
  repository meant to be**~~ -- **superseded** on 2026-09-28: ManagerBot is a
  folder of MumuBots, `ParentBot` retires with the other per-bot
  repositories, and `.github/SECURITY.md` covers the whole repository.

### Carried from v1.7.0 (and 1.7.1)

Reviewed with the owner on 2026-09-13, item by item. What they settled is in
the last section; what is left is below, with the reason it is still here.

#### Deferred during 1.7.0

- **Music downloads in DownloaderBot — assessed 2026-09-15, not started.**
  The owner asked whether mass downloads like `C:\.prog\MusicDownloader`
  (yt-dlp over a YouTube playlist → tagged MP3s, paced 2–6 s apart so YouTube
  does not flag it) or Spotify / Yandex Music links are possible. Checked, not
  guessed:
  - **Spotify audio cannot be downloaded** — it is encrypted, and yt-dlp has
    no Spotify extractor. Every "Spotify downloader" reads the song names and
    fetches the audio from YouTube. The names are free: Spotify's public
    embed page for a playlist returned all 50 tracks with artists, no API key.
    The audio is then YouTube's, so the wrong version (live, cover, sped-up)
    is the thing to guard against — match on duration as well as title.
  - **Yandex Music failed outright** here: yt-dlp's `yandexmusic` extractor
    crashes inside its API call. The service is region-locked, Railway is in
    Europe, and full tracks generally need a paid account — which would mean
    the owner's account serving strangers.
  - **YouTube, SoundCloud and Bandcamp** are the easy part: yt-dlp is already
    DownloaderBot's YouTube route, and audio-only is a format choice.
  - **Volume is the hard part.** YouTube's bot check trips on bulk requests
    from a datacenter IP, and `DBOT_YT_PROXY` / `DBOT_YT_COOKIES_FILE` exist
    but neither is set. The allowance is 12 an hour and 40 a day, so a
    50-song playlist needs its own rule.
  - **The real risk is the bot, not the code.** Music is the most enforced
    category, and a copyright complaint that gets a bot removed takes its
    @username with it. Recommended to the owner: music in a separate bot, so
    a takedown cannot cost `@mumu_downloader_bot`.
  Proposed order: single YouTube/SoundCloud/Bandcamp links as audio, then
  Spotify tracks and playlists via names → YouTube, then playlists with a
  queue, pacing and progress updates; Yandex last or not at all. Waiting on
  three answers: same bot or a separate one, how a playlist counts against
  the allowance, and whether to set up a YouTube proxy or cookies account.

- ~~**ParentBot's repository is ahead of the monorepo.**~~ **Resolved**:
  checked on 2026-09-28, every file in `ParentBot` (and in the four other
  per-bot repositories) is identical to this monorepo at `efe3dc4`; the 1.7.0
  publish superseded `c321a9a`.

- **Section 9 of the old backlog, planned but not started.** The owner:
  *"9 should be addressed soon. Maybe this update batch or next one."* It is
  eight separate pieces of work and they are not the same size, so ordered by
  what each one unblocks:

  1. ~~**A local Bot API server.**~~ **Done differently for ConvertBot on
     2026-09-26** -- MTProto for the big files only, see *ConvertBot takes
     files over 20 MB*; the claim here that a local server was "not a code
     change" did not hold on Railway. DownloaderBot's half is still open:
     its sends are capped at the Bot API's 50 MB, and `big_files.py` is the
     pattern to copy when it is picked up.
  2. **DownloaderBot merging files** — pictures into one PDF, a video with
     its subtitles, an album into one archive. Self-contained, no new
     dependency (ffmpeg and PyMuPDF are both already there), and the most
     asked-for-looking thing on the list.
  3. **Sleeping the quiet bots.** `/idle` already measures whether it would
     be worth it; nothing acts on it. Needs webhooks, a pool that closes when
     idle, and ManagerBot waking a child rather than the child polling. Worth
     doing one bot at a time, and not AnonBot.
  4. **OCR**, which is the nichest and probably the most wanted. Measured
     rather than guessed in v1.5.0: a local `tesseract` costs nothing
     resident, and the constraint is peak memory rather than money — so it
     needs a one-at-a-time lock and a page cap, not a bigger plan.
  5. **Archives** (ZIP/TAR/7z). Deliberately left out so far because the
     expansion ratio of an uploaded archive is unbounded and it needs its own
     uncompressed-size guard rather than the ceilings in `formats.py`.
  6. **Camera raw** (CR2, NEF, ARW, DNG) — `rawpy` is a wheel and the pairs
     are obvious; another 30 MB for a format most people do not send over
     Telegram.
  7. **Heavy conversion off the bot's container.** Every conversion API
     checked forbids a raw conversion service in its terms, so the remaining
     route is the bot's own converter on free compute the owner controls.
     `PRIVACY.md` has to change in the same release — it currently promises
     files never leave the machine the bot runs on.
  8. **Reports carrying the person's own words or a screenshot**, for 1.7.0.
     Note that 1.7.0 has just written a disclaimer promising *"not what you
     wrote"*, so that text has to change in the same release that makes it
     untrue.

- **Replies are not preserved by either export mode.** `copyMessage` makes an
  independent message with no reply link, and the local reply structure was
  never recorded: `anon_relay` knows which *conversation* a message is in and
  which bubble on the *other* side it corresponds to, not what it answered in
  its own chat. Recording it would be one more column written on every relay.
- **The size-ratio table is judgement, not measurement.** Every entry in
  `_RATIO_BY_PAIR` is reasoned from what the format pair usually does, with one
  number (`docx → pdf`, 20×) taken from a real file. `star_transactions.item`
  records every pair converted, so after a few weeks of real use the table can
  be replaced with what actually happened.
- **`CONVERT_BILLABLE` defaults to `sum`.** Charging on input plus estimated
  output makes expanding conversions dearer than they were. `max` is one
  environment variable away if that turns out to be the wrong call.
- **The burst window is per process.** A redeploy forgets how many faults have
  happened recently, and two bots hitting the same code do not add up. Both
  are deliberate: the alternative is a database query per problem, on the path
  that already exists to survive the database being the thing that broke.
- ~~The fan-out only serves `/errors`.~~ **Not a gap after all**, checked on
  2026-09-20: `/status`, `/users`, `/usage` and `/idle` read the shared
  database directly rather than going over the bus, which is better than a
  fan-out — they answer for a bot that is currently down.
- **PDF → DOCX does not reconstruct layout.** Columns come out sequentially, a
  table comes out as its text. Doing better needs a computer-vision stack.
- **A pop-up still cannot hold a Report button.** It is now at least recorded
  and levelled like every other problem.
- ~~Two `check()` conventions across the test suite.~~ **Done** on
  2026-09-20: the five files that compare two values call it `equals()`, so a
  comparison written in the other shape no longer passes on a truthy value.

#### Found and fixed on 2026-09-20

- **`/idle` had never worked** — it read `family_link.SLEEP_AFTER_SECONDS`,
  which does not exist in that module and never has.
- **`/stars`, `/probe` and `/providers` were refused by ManagerBot**, because
  a bot registers those in its own process and ManagerBot was checking its own
  copy of the list. Bots now advertise what they answer in their heartbeat.

#### Carried, and still true

- **The standby service** (`UPDATES.md` §5) is designed and not built. Listed
  unbuilt in 1.2.2R, 1.2.3 and 1.3.0. It does not keep a bot working during a
  deploy — nothing can, the code doing the work is what is being replaced — it
  replaces silence with a sentence, which matters for the *failed* deploy
  (minutes to hours of nothing) rather than the healthy one (seconds).
- **ManagerBot has no `sim.py`-driven test.** Its flows go through the command
  queue and a second bot answering, which is a bigger fixture than any release
  so far has needed. Listed in 1.2.3 and 1.3.0.
- **`DONATION_STEPS`, the download allowances and `FLOOD_UPDATES_PER_MINUTE`
  are judgements, not measurements** — and, per the owner's answer on card
  payments, there is not yet a customer base to measure against. Left alone
  until there is, rather than tuned from noise. The nightly report is now the
  thing that will surface the numbers as they accumulate.
- **`ACTIVITY_RETENTION_DAYS` is still 90.** Published, never argued down.
- **No data export for a person's own data.** `/deletemydata` reports a count
  and `/privacy` lists the categories; a dump of six settings rows would be
  ceremony rather than a right anybody is short of.
- **The five `hero.png` description pictures are not uploaded**, and the bot
  profile photos are a manual BotFather step (*Edit Bot* → *Edit Description
  Picture*). The owner: *"8 is for tomorrow or whenever during the day."*
- **The empty `parent_bot` schema** — two tables, no rows — is still in the
  database. Now that ManagerBot's `DB_SCHEMA` is `manager_bot`, it can be
  dropped, but only when the owner says so ("Lets not delete anything from db
  yet").
- ~~**The GitHub repository is still `ParentBot`**~~ and ~~**ManagerBot has no
  `SECURITY.md`**~~ -- superseded by MumuBots (2026-09-28).
- **`testbot/token.env` points TestBot at the production database.** "Leave as
  is for now, but also save it for later." Since 2026-09-28 TestBot refuses
  that login -- `postgres` can write everywhere -- so the line has to become
  the `testbot` login (`testbot/README.md`) or the local database.
- **Progress updates are ConvertBot's alone**; nothing else in the family works
  silently for minutes.
- ~~**Jobs live in memory.**~~ **Done** in 2.0.0: a conversion lost to a hard
  kill is refunded by the next process that notices (`conversions_in_flight`).
- **Background conversions do not send the donation nudge.**
- ~~**Nothing checks the menus a token is actually serving.**~~ **Done** in
  2.0.0: `scripts/check_live_menus.py`, read-only, all five matched.
- **AVIF is multithreaded** — 187 CPU-seconds in 15 wall-seconds locally — and
  CPU is billed per core-second.
- **An album's later parts chain off the first one** rather than off the
  counterpart of what the album replied to. Left alone deliberately.
- **ConvertBot's Railway memory.** A 200 MP decode peaks around 1.5 GB.

#### Settled, so they are not asked again

- **The licence** — AGPL-3.0-or-later on all five, v1.7.0. *"Just do what you
  think is the best."* See the section above for what that commits to.
- **Card payments are built and deliberately frozen.** The owner: *"was
  designed this way bcs there is no real customer base yet."* `FIAT_CURRENCIES`
  has USD with tiers and `/donate 5 usd` is wired; setting
  `PAYMENT_PROVIDER_TOKEN_USD` is the whole of turning it on, whenever there is
  a reason to.
- **ManagerBot's Railway `DB_SCHEMA`** — checked by the owner on 2026-09-13 and
  already set to `manager_bot`. It is reading its own schema.
- **`/starbalance`** — proposed twice, never answered, and superseded: the
  credit ledger and `/balance` answer the question it was for.
- **UZS** — dropped in `cf8fb00`; Stars and USD only.
- **DownloaderBot's paid override** — dropped: "forget about 2".
- **Refunds of Stars** — not offered: "lets just not do refunds for now".
- **The Supabase password** is not being rotated: "10 - not".
- **Third-party conversion services** — not built, because their terms forbid a
  bot reselling them.
