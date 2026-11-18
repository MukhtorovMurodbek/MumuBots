# `family_link.py`

[↑ shared](README.md) · [code](family_link.py)

The family bus: how one bot talks to ManagerBot, and how ManagerBot talks
back.

Every bot in the family (the four public ones and ManagerBot itself) keeps
this file, byte-identical, exactly like shared_features.py -- the bots stay
independent processes with independent repos, so nothing is imported across
folders. What they \*do\* share is one Postgres database, and this module is
the only thing that touches the parts of it that aren't a single bot's own.

Layout of that shared database:

```text
family.*        this file's tables -- heartbeats, events, command queue
sticker_bot.*   StickerBot's own tables (its db.py, unchanged)
convert_bot.*   ConvertBot's own tables
downloader_bot.*
anon_bot.*
manager_bot.*    ManagerBot's own tables
```

One Postgres schema per bot means the four bots' identically-named tables
(user_settings, star_transactions, activity_events, ...) never collide, and
no bot's SQL had to change -- each connects with its own search_path (see
db.py's DB_SCHEMA). ManagerBot is the only process that reads across schemas.

Three things flow over this bus:

```text
1. **Heartbeats** -- every HEARTBEAT_SECONDS each bot stamps
   family.heartbeats with "still alive, started at X, N errors so far".
   ManagerBot decides a bot is down when that stamp goes stale, which
   works whether the bot crashed, was OOM-killed, lost its network, or
   was never started at all. No open ports, no HTTP between services.
```

```text
2. **Events** -- anything ManagerBot should tell the operator about lands in
   family.events (an unhandled exception, a startup, a donation).
   ManagerBot polls for undelivered ones and forwards them as a DM.
```

```text
3. **Commands** -- ManagerBot inserts a row in family.commands aimed at
   one bot; that bot polls for it (fast while the bus is busy, every
   FAMILY_BUS_POLL_IDLE_SECONDS otherwise), runs it, and writes the answer
   back into the same row. This is how ManagerBot
   runs another bot's operator-only commands (/status, /dbdump, /whois,
   /messageas, ...) without either process needing to reach the other
   over the network. A command aimed at a bot that is down simply stays
   pending until ManagerBot times it out and says so.
```

Every bot also tidies up after itself here, on a slow timer: its own
finished rows in family.commands (which carry file payloads -- a delivered
/dbdump zip is megabytes of BYTEA nobody will read again), its own delivered
events, and its own activity log. Each bot only ever deletes rows keyed to
itself, so the five processes need no coordination to do it.

Everything here is best-effort by design: if the shared database is
unreachable, the family bus goes quiet but the bot itself keeps serving its
users normally. A monitoring layer must never be able to take down the
thing it monitors.

_At `VERSION = os.environ.get("FAMILY_VERSION", "3.0.0")`:_

Bumped with every deployed version -- reported in heartbeats so /status can
show which bots are running stale code after a partial deploy. 3.0.0 because
2.2.0 is taken: it is the deployed codebase's own release (2.1.7 and the gift,
2026-11-18), and this tree is the release after it.

_At `_REPORTS_BECAME_AUTOMATIC = datetime(2026, 9, 13, tzinfo=timezone.utc)`:_

Before 1.7.0 a row in family.problem_reports only existed because somebody
tapped Report, so every row older than this is one of those and is marked
as shared. After it, most rows are automatic and carry nothing personal.

## How a bot finds out there is a command waiting for it

It polls family.commands for its own pending rows. There is no push.

v1.0.1 added a LISTEN/NOTIFY layer on top of the poll: a dedicated
psycopg.AsyncConnection per channel, an asyncio task holding it open, its
own reconnect/backoff. It was meant to turn "picked up within the poll
interval" into "picked up in milliseconds". In practice it earned its
reputation as the most fragile code in the family: the job that started it
was silently misfire-dropped and never ran on any bot for two months
(v1.1.2), and psycopg's async connection cannot run on Windows' default
event loop at all, so it never worked on the laptop the bots are developed
on. Both times the poll underneath carried the whole bus and nobody
noticed. For a bus with one human operator typing /ping now and then, a
held connection and 200 lines of reconnect logic bought a few seconds on a
manual command. v1.1.4 deleted it.

What is left is one poll, made adaptive: fast right after the bus was last
busy, slow when it has been quiet. mark_bus_active() opens a short fast
window; anything that expects bus traffic (a command just queued, claimed
or finished) calls it. The result: a warm bus answers in about a second,
and a cold one within FAMILY_BUS_POLL_IDLE_SECONDS. It behaves identically
on a Windows laptop and on Railway, and it has no failure mode short of the
database itself being unreachable -- which /status shows anyway.

_At `BUS_ACTIVE_WINDOW_SECONDS = float(os.environ.get("FAMILY_BUS_ACTIVE_WINDOW_SECONDS", "20 …`:_

How long the fast cadence lasts after the last time the bus did something.

_At `RUN_LATE = {"misfire_grace_time": None}`:_

Pass to every job_queue.run_once(..., when=0) in the family.

APScheduler drops a job whose run time has already passed by more than
misfire_grace_time, which defaults to one second, and python-telegram-bot
does not override it. A bot's main() stamps "now" before run_polling()
starts the scheduler -- and between those two moments sits post_init: an
advisory lock, the waitlist table, the maintenance flag and set_my_commands,
each a round trip to a database on another continent. Several seconds, every
time. So a run_once(when=0) job would be scheduled, silently discarded as a
misfire, and never run (this is exactly how the old LISTEN listener stayed
dead for two months -- see v1.1.2). `None` means "run it however late it
is", which for a job that only ever means "do this as soon as the loop is
up" is the only correct setting.

_At `_bot_id: str | None = None`:_

Set by attach(); everything below no-ops until then.

_At `HOSTNAME = socket.gethostname()`:_

The host name never changes while the process runs, and gethostname() is a
syscall -- worth doing once rather than on every heartbeat.

_At `COMMAND_RETENTION_HOURS = int(os.environ.get("FAMILY_COMMAND_RETENTION_HOURS", "24"))`:_

How long finished command rows and delivered events are kept before this bot
tidies up after itself. The command queue carries BYTEA payloads (a /dbdump
zip on its way to ManagerBot), so letting it grow forever means paying to
store megabytes of files that were already delivered.

- `HOUSEKEEPING_SECONDS = int(os.environ.get("FAMILY_HOUSEKEEPING_SECONDS", "21600"))` — 6h

_At `def _connect():`:_

Connection helper -- the family schema is shared, so it is always addressed
by its fully-qualified name and never relies on this bot's search_path.
This borrows db.py's connection pool rather than opening its own connection
per heartbeat/poll. At a 3-second command poll that was 1,200 connect-
authenticate-fork-disconnect cycles an hour, per bot, to almost always find
an empty queue.

### `_column_missing()`

True when family.&lt;table> has no `column` yet.

init_family_schema() asks this before each ALTER TABLE and issues the ALTER
only when the column is missing. Every ALTER TABLE takes an ACCESS EXCLUSIVE
lock on its table and keeps it until the transaction ends -- even one that
turns out to change nothing, IF NOT EXISTS or not (measured on Postgres 17,
2026-10-09). All five bots run init_family_schema() at every start, on
tables the heartbeats and the command bus write to every few seconds, in one
transaction that commits at the end: so a start that only wanted to check
queued every other reader and writer of those tables behind it. A read of
the catalog takes no lock that blocks anybody, so a normal start now takes
none. IF NOT EXISTS stays on the statements because two bots starting
together can both see the column missing; the second then waits for the
first and finds nothing left to do (the same reason as in each bot's
db.py). The statements stay literals, one per column.

### `init_family_schema()`

Idempotent; every bot calls it at startup, whoever gets there first
wins. Kept here rather than in ManagerBot alone so a bot started on its
own (no manager running yet) still has somewhere to write.

- At `if _column_missing(conn, "heartbeats", "commands"):`:
  Which bus commands this bot answers, comma-separated. Written from
  COMMANDS itself, so a bot that registers one of its own (ConvertBot's
  `stars`, DownloaderBot's `probe`) advertises it without anybody
  maintaining a list. NULL for a bot older than 1.7.0, which is why
  every reader falls back to the shared set.
- At `conn.execute(`:
  What each bot has been costing, one row per sampling window.

  A heartbeat says a bot is alive; this says what being alive costs.
  On a host that bills resident memory by the second, "is it up" and
  "is it about to be too expensive to keep up" are different
  questions, and until this existed only the first had an answer --
  /status could say what the footprint is \*right now\*, which tells
  nobody whether that is normal.

  Deliberately narrow: no user ids, no message text, nothing about
  what anybody did. A count of updates, a count of distinct people,
  and four numbers read from the kernel.
- At `for column in ("sleepable_seconds", "max_gap_seconds", "jobs_ok", "jobs_failed"):`:
  Added after the table existed on a running family, so they arrive as
  ALTERs rather than as part of the CREATE. IF NOT EXISTS makes both
  paths idempotent, which is what lets every bot run this on startup
  without coordinating.
- At `conn.execute(`:
  How often each button is tapped: a count per bot, per day (UTC), per
  button, written with the usage sample. Counts only -- the key is the
  button's name (and, for a few, its choice from a short fixed list), never
  an id, a link or a name; shared_features.tap_key decides what a key may be.
  Created like usage_samples above: IF NOT EXISTS, no ALTER, no lock on any
  table that already exists.
- At `conn.execute(`:
  Anonymous counts for infographics: what was done, per UTC day -- which
  sites links came from, which conversions, which kind of sticker. No user
  id, no time finer than a day (shared_features.note_stat decides the keys).
- At `conn.execute(`:
  ManagerBot's memory of who was up last time it looked, so it can
  alert on the \*transition\* (down -> up, up -> down) instead of
  repeating "still down" every minute.
- At `conn.execute(`:
  One Stars balance per person, for the whole family.

  It lives here rather than in a bot's own schema because that is the
  whole point of it: stars put in through StickerBot are spent in
  ConvertBot, and neither bot owns them. A per-bot balance would be
  five wallets somebody has to top up separately, which is worse than
  the per-job invoice it replaces.

  `balance` is in CREDITS, not Stars, and the two are not the same
  unit: paying Stars buys a multiple of them (TOPUP_MULTIPLIER), so a
  balance is always larger than the money behind it and can never be
  paid back out. That is why `lifetime_stars_paid` is a column of its
  own -- it is the only figure here denominated in real money, and it
  is the one to ask "has this person ever paid". Credits can arrive as
  a grant or a welcome bonus, and counting those as a payment is how a
  gift turns into a claim that somebody donated.

  The lifetime columns exist so a balance of 40 can be explained --
  topped up 100, spent 60 -- without reading the whole ledger, and so
  that an adjustment cannot inflate what somebody appears to have
  paid: they grow on real top-ups, bonuses and spends, never on an operator's correction.
- At `conn.execute(`:
  Every movement, and why. A balance with no ledger behind it is a
  number nobody can defend: the first question anybody asks about a
  wallet is "where did the other sixty go", and the answer has to be
  a list of dated lines rather than an assurance.

  `balance_after` is redundant on purpose. It makes the ledger
  self-checking -- the newest row's balance_after must equal the
  balance, and each row's must equal the one before it plus the delta
  -- so a bug that debits without recording, or records without
  debiting, is visible instead of merely suspected.

  `stars_paid` on a row is the real money behind that movement, so
  the ledger can answer "what was actually charged" separately from
  "what was credited" -- which is what a refund has to be reasoned
  about in, and what a promotion rate makes different numbers.

  This is a payment record and is \*\*not pruned and not erased\*\*, for
  the same reason star_transactions is not: it is what a refund is
  issued against and what the totals are counted from. It holds a
  numeric id, an amount and a reason, and nothing anybody wrote.
- At `conn.execute(`:
  Bonus credit, one row per payment that earned some. The balance
  above is the only figure anything spends from; this is what says how
  much of it is bonus and when that part runs out. Spending takes from
  the soonest-expiring lot first, so what a person loses to expiry is
  only ever bonus they had not got round to using.
- At `conn.execute(`:
  Every problem any bot shows anybody (see problems.py). One row per
  incident, so a redraw or a double tap stores once.

  The row is written the moment the problem is \*shown\*, not when
  somebody decides to report it -- it includes the error without the report button being pressed, but stores only generic technical data with nothing specific to a user. So the columns
  in the CREATE are all a row has unless its person volunteered more:
  which bot, the code, the incident, when it happened, the version.
  None of them is about anybody.

  The columns added below are filled in only by somebody tapping
  Report and then Send, having read exactly what those columns are. A
  row with `shared_at` NULL has never carried anything about a person.
- At `if _column_missing(conn, "problem_reports", "shared_at"):`:
  Added in 1.7.0, to a table that held reports only before it.
- At `if _column_missing(conn, "problem_reports", "seen"):`:
  How many times this exact problem was shown. An incident is one
  occurrence, so this only moves when the same message is re-shown --
  a status line redrawn with the failure still on it.
- At `if _column_missing(conn, "problem_reports", "level"):`:
  1 urgent, 2 fault, 3 refused -- see problems.level(). Stored rather
  than derived on read, so that a row keeps the seriousness it had
  when it happened: re-classifying a code later must not rewrite what
  last month's nightly reports said.
- At `conn.execute(f"UPDATE {FAMILY_SCHEMA}.problem_reports "`:
  Everything in the table before 1.7.0 arrived through the Report
  button, which is what `shared_at` now means. Stamped with the time
  the report came in, which is the only time those rows carry.
- At `conn.execute(`:
  /reports reads newest first over a table that now grows with every
  problem rather than with every report.
- At `conn.execute(`:
  What people send with /feedback (shared_features.feedback_command),
  added in 2.1.0.

  The words are what this table is for, which makes it the one place
  in the family schema that keeps something a person wrote -- so it
  keeps nothing else about them: which bot, which language, when, and
  when the operator was sent it. No user id and no username, on the standing rule that nothing pushed at the operator identifies anybody.
  Deleted after FEEDBACK_RETENTION_DAYS whether or not it was ever read.
- At `conn.execute(`:
  Who used /deletemydata, so that restoring a reserve copy taken before
  it erases them again (reserve.py; 2026-11-03). The id is
  sealed: only the restore, which holds the key, can read it. Kept for
  reserve.KEEP_DAYS, the age past which no copy is ever restored.

## The data key (2.1.2CHL)

Personal data in the database is sealed or pseudonymised with a key that
lives in a file of its own (shared/security/vault.py). Two mistakes would be silent
and bad: a bot started with the wrong key file would seal new rows under a
key nothing else can open, and a bot started on a database nobody has
converted would read plain ids as if they were pseudonyms. So every bot
checks, before it polls, that the key it holds is the one this database was
stamped with -- and that what the database holds is sealed, not just
stamped.

```text
Two kinds of stamp, in family.settings:
  data_key:v1      the key's fingerprint (16 hex digits that name the key
                   without revealing it): which key this database is sealed
                   with. One for the whole database.
  sealed:<stage>:v1  "1" once that stage's tables are converted. A stage is
                   a group of tables converted together; releases seal them one group at a time (AnonBot first),
                   and a bot refuses to start while a stage its release
                   needs is not stamped. Written by
                   scripts/security/encrypt_personal_data.py after it has verified
                   the stage, in the transaction that converted it.
A database that holds none of a stage's tables' rows (a new one) is stamped
by whichever bot starts first.
```

_At `STATE_TABLES = (lifecycle.STATE_TABLE, lifecycle.WAITLIST_TABLE)`:_

Saved state is sealed in every schema, whatever the stage: the family's
conversion clears what is not sealed (it lives hours).

_At `_TEST_PREFIX = "testbot_"`:_

TestBot (testbot/) keeps its own family in `testbot_family` and its bots'
tables in `testbot_*`, on the same database as the real family. Each family
is stamped, and judged, on its own schemas only: the test bot must not be
refused because the real tables are full, nor the real bots because of a
test schema.

### `belongs_to_family()`

Is `schema` one of the schemas of the family kept in `family_schema`
(default: this process's)? `family` owns every schema but TestBot's; a
family named `<prefix>family` owns the schemas that start with &lt;prefix>.

### `_tables_of_family()`

{schema: {tables}} for this family's schemas, whatever the connection's
role may read (pg_tables lists all of them).

### `stage_schemas()`

The schemas of this family that belong to `stage`'s bot.

### `personal_data_tables()`

(schema, table) of every table that exists in this family's schemas and
that the conversion of `stages` touches: each stage's tables in its bot's
schemas, and the saved-state tables everywhere.

### `read_stamp()`

One of the stamps above, or None when it is not there (or the settings
table is not).

### `ensure_settings_table()`

Make family.settings if the family has none (a family that never ran a
bot: TestBot's on a database that has only its bots' schemas). The same
table init_family_schema() makes. True when it had to be made.

### `write_stamps()`

Stamp the family with the key and with every one of `stages`. The key's
stamp never overwrites another (the caller compares afterwards); a
stage's stamp is set to "1", because the caller has just converted it or
found nothing to convert.

### `fresh_heartbeats()`

The bot ids whose heartbeat in this family is younger than
`within_seconds`: a bot that is, or may still be, running.

### `DataKeyRefused`

The data key guard ended this bot: the key is missing, unusable or not
the one this database was sealed with, the data is not sealed, or the
check could not be made. A SystemExit, so it passes through every
`except Exception` and ends a bot that runs alone. A process that runs
every bot (main.py) catches SystemExit per bot, and reads
`data_key` to tell this apart from a bot that merely quit: with the key
unchecked nothing may run beside it.

### `_refuse()`

Say it at CRITICAL and end the process. The message goes into the
exception as well: a process that runs every bot (main.py)
catches it per bot and reports it as the reason that bot is not running.

### `inspect_data_key()`

- At `stamped = read_stamp(conn, DATA_KEY_SETTING)`:
  Not a new database -- unless another bot, starting at the same
  moment on a new one, has stamped it and written its first row
  since this one looked.

_At `def _require_transaction(conn) -> None:`:_

--- converting a database sealed under 2.1.1 or earlier -----------------------
scripts/security/encrypt_personal_data.py runs these, with the bots' own, in one
transaction. Both take the caller's connection, never commit, and use only
what they are given.

### `_waitlist_row_opens()`

Is this update_waitlist row in the sealed layout, and does its seal
open to the very ids its pseudonyms stand for?

### `convert_personal_data_v1()`

Clear the saved state that is not sealed. `family_schema` is the real
family's (the default) or TestBot's.

```text
Both tables live hours at most -- the persistence drops a runtime_state
row after STATE_TTL_HOURS, and an update_waitlist row is someone told to
come back after an update -- so, rather than sealing 2.1.1's rows in
place, the rows that are not in the sealed layout are deleted:
  - runtime_state, in every schema of the family;
  - update_waitlist, wherever the family has one (the column who_sealed is
    added first if the table is older than that).
A row that is already sealed and opens is left alone, so a second run
changes nothing. Nothing is committed; nothing but `conn` and
`family_schema` is used.
```

Returns {"changed": {"schema.table": rows deleted}, "skipped": [...]}.

### `verify_personal_data_v1()`

Check that the family's saved state is all sealed: every runtime_state
row opens and belongs to its key, every update_waitlist row has
pseudonyms and a seal that opens to them. Raises RuntimeError naming the
tables and the position of the rows (never a value) when not. Returns
{"checked": {"schema.table": rows}, "skipped": [...]}.

_At `TOPUP_LADDER = _parse_ladder(os.environ.get("FAMILY_TOPUP_LADDER", "500:3,500:2"))`:_

"stars:multiplier" segments of a person's lifetime Stars, in order; after the
last one the multiplier is 1. Empty turns bonuses off.

_At `LEDGER_REASONS = ("topup", "bonus", "grant", "spend", "refund", "adjustment", "expired")`:_

Every reason a balance can move, so that a typo becomes a failure here
rather than a row nobody can group by later.

### `credit_for_stars()`

The ordinary credit `stars` buy, with no bonus -- the part that never
expires.

### `quote_credit()`

What paying `stars` earns for somebody who has already paid
`lifetime_paid` Stars, split into the ordinary part and the bonus.

A payment that straddles a step is priced piece by piece: 400 Stars from
somebody at 300 buys 200 at 3x and 200 at 2x.

### `ladder_position()`

(multiplier the next Star earns, Stars left at that multiplier). The
second is None once past the ladder.

### `_ledger_bot()`

Which bot a movement is recorded against. `attach()` has run by the
time anybody spends anything; a movement before that is still worth
recording, under a name that says it could not be attributed.

### `_lock_wallet_in()`

Make sure the wallet exists and hold it for this transaction. Every
change to a balance goes through here first, so two bots touching one
wallet at the same moment take turns instead of racing.

### `_expire_in()`

Take away this person's bonus credit that has run out. Returns how
much. Called at the start of anything that reads or moves a balance, so
an expired bonus is never spendable even between housekeeping passes.

### `_take_bonus_in()`

Reduce this person's unexpired bonus by `amount`, soonest to expire
first. What is spent is bonus before it is ordinary credit, so what
expiry takes is only bonus nobody got round to using.

### `_cap_bonus_in()`

After a balance goes down for any reason but a spend, bonus cannot be
more than what is left -- or its expiry would later take away credit
that is not there.

### `star_balance()`

What this person has, in credits, after any expired bonus is gone.

### `star_totals()`

Balance, lifetime figures, and how much of the balance is bonus with
when the next of it expires. `stars_paid` is the only figure in real
Stars, and it is what the ladder is counted from.

### `quote_topup()`

quote_credit for this person, from what they have paid so far. For
the sentence before a payment; topup() prices it again under a lock.

### `move_stars()`

Add `delta` credits to a balance and record why. Returns the new
balance. Negative deltas are allowed and may take a balance below zero,
which simply means no paid work until it is positive again. What arrives
this way is ordinary credit and never expires; only topup() creates
bonus.

### `spend_stars()`

Take `amount` credits off a balance if it covers it. Returns the new
balance, or None if it does not. The check happens under the wallet's
lock, so two jobs racing for the last of a balance cannot both win.
Bonus is used before ordinary credit.

_At `_TOPUP_ATTEMPTS = 3`:_

A payment's credit is retried through a database blip (see topup). Three tries
over about four seconds: long enough for a pool to replace a dead connection or a
managed Postgres to fail over its proxy, short enough that the person who paid
is not left looking at nothing. Callers run topup in a thread, so the sleep
never holds the event loop.

_At `UNCREDITED_CLAIM = "uncredited:"`:_

The claim claim_uncredited stakes for a payment topup could not credit starts
with this, where topup's own claim is the time of the credit.

### `claim_uncredited()`

Stake the claim `stars:paid:<charge_id>` for a payment topup gave up on,
before it is refunded: a later delivery of the same payment then finds the
claim and credits nothing, so nobody is given both the credit and the refund.

Returns None when this call staked it. Otherwise the claim that was already
there: topup's (a time -- the credit did land, from an attempt whose answer
was lost), or another delivery's (UNCREDITED_CLAIM...: that one deals with
it). Tried again through a database blip as topup is; a try whose COMMIT went
through but whose answer was lost knows its own claim on the next one. Raises
when the database never answered, and then nothing is known.

### `set_star_balance()`

Put a balance at exactly `target` credits, recording the movement.

### `grant_stars_once()`

Credit `amount` the first time this (key, user) is ever asked for, and
never again. Returns the new balance, or None if it had already been
done. The claim is staked atomically in family.settings, and released
again if the credit that follows fails.

_At `GIFT_MAX_CREDIT = 1000`:_

The most one gift may give one person. A gift goes to everybody at once, so a
slipped digit (1000 for 100) would be multiplied by every account there is;
past this the code has to change, not just the command.

### `grant_bonus_once()`

A gift: `amount` credits of bonus that runs out at `expires_at`, given
the first time this (key, user) is ever asked for and never again. Returns
the new balance, or None if it had already been given.

The same claim as grant_stars_once (`stars:granted:<key>:<user>`), so one
key can never pay twice through the two of them. Unlike it, the claim and
the credit are one transaction, staked the way topup() stakes a charge id:
they stand or fall together, and two bots granting the same person at once
take turns on the wallet's lock, the second finding the claim taken.

It is bonus, not ordinary credit: a lot of its own in star_bonus_lots, spent
before ordinary credit and taken back by \_expire_in when it runs out.

- At `last_day = expires_at - timedelta(microseconds=1)`:
  The last day it can be spent, as it was typed: expires_at is the
  moment after it (midnight UTC that ends the day).

### `expire_bonus_credit()`

Housekeeping: expire every person's run-out bonus. Returns how much
credit it took in total. Safe from five bots at once -- each wallet is
locked, and a lot already cleared is not cleared again.

### `star_ledger_for()`

This person's movements, newest first.

### `star_balance_overview()`

Family-wide totals, and the largest balances. `outstanding` is credit
people hold and have not spent -- work the family still owes -- and
`bonus` is the part of it that will expire if unused.

### `record_problem_occurrence()`

Write down that this problem was shown. True the first time an incident
is seen, False for a re-show of the same one.

Called for every coded message any bot sends, whether or not anybody ever
taps Report. Nothing here is about a person: the bot, the code, the
incident, when, and the version. `seen` counts re-shows, so a status line
redrawn with the same failure on it does not become a second problem.

### `attach_problem_reporter()`

Add the details somebody volunteered to their incident's row. True if
this is the first time anybody has attached themselves to it.

The row usually exists already -- record_problem_occurrence wrote it when
the problem was shown. The INSERT covers the one case where it does not: a
bot that could not reach the database at the moment of the failure, which
is exactly the kind of failure most worth having reported.

Everything written here was named to the person, on screen, before they
tapped Send. Nothing reaches this function any other way.

### `recent_problem_reports()`

The latest problems, newest first. `shared_only` narrows it to the ones
somebody attached their own details to.

### `problem_report_by_incident()`

One incident, whichever bot it came from.

This is where details somebody volunteered are read: deliberately, by the operator asking for that incident, rather than pushed in an alert.
The alert says a report arrived and nothing about who sent it.

### `recent_problem_reports_since()`

Everything recorded in the last `hours`, newest first. What the nightly
report is built from -- one read rather than one per bot, because the
report is about the family and the table already knows which bot each row
came from.

### `problem_counts_since()`

{bot_id: {level: count}} for the last `hours`. The cheap version of the
line above, for a summary that only needs numbers.

## Getting rid of them again

Two different clocks, because the two halves of a row are two different
things. The generic half -- code, incident, time, version -- is about the
software and is not about anybody, so it can be kept long enough to tell
whether a fault is new or has been happening since June. The half somebody
volunteered \*is\* about them, and the reason it was collected (finding their
case in the logs) stops applying within days of the fault being fixed.

So the details are cleared first and the row survives without them, which
keeps the count honest -- the fault still happened -- while holding nothing
about who hit it. `/deletemydata` does the same thing immediately, for one
person, whether or not the clock has run out.

### `prune_problem_reports()`

Clear old volunteered details, then drop old rows entirely. Returns how
many rows were changed or removed.

### `forget_problem_reporter()`

Take one person off every problem they ever volunteered for, now.

The rows stay, without them: a fault that happened still happened, and the
count of it is not personal data. Called by /deletemydata in every bot, so
that a request to erase everything held about a person covers the family table as well as the
bot's own.

## Feedback

A public bot writes what somebody sent with /feedback; ManagerBot reads it
and sends it to the operator. 2026-10-03: with few users, a notification for each piece of feedback, and a switch between that and all of it grouped in the nightly report. The switch is a row in family.settings, so
changing it needs no redeploy and every process reads the same answer.

### `save_feedback()`

Keep one piece of feedback for ManagerBot to deliver. Returns its id.

### `take_undelivered_feedback()`

The oldest feedback nobody has been sent yet, marked as sent in the same
statement -- so two ManagerBots overlapping in a deploy cannot both send
the same one. Oldest first, the order it was written in.

### `recent_feedback()`

The latest feedback, newest first, delivered or not -- for ManagerBot's
/feedback, which is where it can be read again after the message is gone.

### `prune_feedback()`

Drop feedback older than FEEDBACK_RETENTION_DAYS, sent or not. Run by
every bot over the whole table, for the reason problem reports are: a
family table that is tidied only while one particular bot is up grows
whenever that bot is the one that is down.

## Small shared memory: family.settings

A key and a short text, for a fact that has to survive a redeploy and be the
same in every process -- "when did the route check last run", "which state
was Instagram in". Not for anything about a person. Without a database
(tests, a bot run on a laptop with persistence off) it is a dict in this
process, which is the right amount of memory for those.

### `get_setting()`

Blocking. Never raises: a missing database reads as "not set".

### `settings_with_prefix()`

Every setting whose key starts with `prefix`, as {key: value}. Blocking.
Never raises, for the same reason as get_setting: a missing database reads
as nothing set. (ManagerBot reads DownloaderBot's route check this way.)

### `set_setting()`

Blocking. Never raises, for the same reason.

## The adaptive poll

One timer, ticking every BUS_POLL_ACTIVE_SECONDS, that decides on each tick
whether this is also a database poll: always while the bus is "active",
otherwise once per BUS_POLL_IDLE_SECONDS. mark_bus_active() opens a fresh
active window and is called by anything that has reason to expect bus
traffic in the next few seconds -- a command just queued, claimed, or
finished. So a bus that is doing something polls at ~1s and one that has
been quiet for a while polls at ~10s, with no held connection and no state
machine to get wrong.

- `_bus_active_until = 0.0` — monotonic; poll fast while time.monotonic() < this

### `mark_bus_active()`

Poll at the fast cadence for the next BUS_ACTIVE_WINDOW_SECONDS. Cheap
and idempotent -- call it whenever bus traffic is likely (a command was
just written, picked up, or answered).

## Outbound: heartbeats and events

### `_monitoring()`

The module holding this bot's error counter / status text. Called
`shared_features` in the four public bots and `monitoring` in ManagerBot
(which has no donations or sibling cross-promotion to share) -- looked
up lazily, because this one file serves all five.

`monitoring` is asked for first. Since 2.0.0 shared_features.py lives in
shared/, which is on ManagerBot's path as well, so asking for it first
would hand ManagerBot the public bots' module instead of its own.

_At `USAGE_RETENTION_DAYS = int(os.environ.get("FAMILY_USAGE_RETENTION_DAYS") or 45)`:_

Usage samples are one small row per bot per window and are the only thing
here anybody will want to look \*back\* through, so they outlive the rest.
45 days is a month and a half: long enough to compare this month with last.

### `report_event()`

Blocking -- call it through asyncio.to_thread from async code, or let report_event_soon() below do that.

level is one of info / warning / error / critical; ManagerBot decides
which levels are worth a DM at 3am (see its ALERT_LEVELS). occurred_at is
for an event held while the database was unreachable (keep_for_later).

_At `OUTBOX_MAX = 200`:_

ManagerBot picks this up on its event pump's next pass -- fast, because a
bot reporting an event is usually in the middle of something the bus
cares about, and the pump runs its active cadence whenever ManagerBot has
recently been busy.

### `record_usage()`

One sampling window's worth of what this process cost. Blocking.

Never raises: the whole point of this table is to notice a problem, and a
monitoring write that took the bot down with it would be the problem.

### `record_taps()`

Add a window's button counts to today's (UTC) row of each button. Blocking.
One transaction; `taps` is {key: count}, nothing else is stored.

### `button_taps_history()`

Taps per bot and button over the last `days` UTC days (today and the days-1
before it), most tapped first within a bot. Every bot's rows: this is what
ManagerBot asks for.

### `record_stats()`

Add a window's anonymous counts, {(metric, key): n}, to today's (UTC) rows.

### `stats_history()`

Counts per bot, metric and key over the last `days` UTC days, largest
first within a metric. Every bot's rows: this is what ManagerBot's /stats
asks for.

### `usage_history()`

Recent samples, newest first. `bot_id=None` means every bot, which is
what ManagerBot asks for.

## Inbound: the command queue

Each entry returns (text, file_name, file_bytes). Only ManagerBot ever puts
rows in the queue, and only the operator can drive ManagerBot, so these are the same trust level as each bot's own operator-only commands.

### `ping_probe()`

Blocking -- call through asyncio.to_thread.

Two numbers, and the second is the one that makes the whole report
trustworthy. `db_ms` is how far this process is from Postgres. `skew_ms`
is how far this machine's clock is from Postgres's: without it, any
"sent at X, arrived at Y" figure computed across two hosts is that skew
plus the real latency, with no way to tell which is which. Postgres's
clock is the one both ends can see, so it is the referee.

- At `with db.pooled_read() as conn:`:
  Through pooled_read, so this is ONE round trip and reports the distance
  to the database rather than the distance times however many statements
  the pool wraps around it. Measured through pooled() it was the query
  plus an implicit BEGIN plus a COMMIT plus a liveness check -- four trips
  to the database reported as though they were one, which is how
  "Supabase 976 ms" ended up on screen for a link that was nearer 250.
- At `midpoint = local_before + (local_after - local_before) / 2`:
  The query result was produced somewhere inside the round trip; the
  midpoint of it is the least wrong instant to compare against.

_At `PROBE_USER_ID = 0`:_

The id the user-path probe reads with. Not anybody's: it is below every id
Telegram issues, so the SELECT walks the same index to the same depth as a
real lookup and finds nothing, and no real person's row is read to time a
ping.

### `user_path_probe()`

Blocking -- call through asyncio.to_thread. The database half of what a
person actually waits for.

/ping measures the \*\*admin bus\*\*: ManagerBot writing a row in Postgres,
this bot noticing it, answering into another row, ManagerBot collecting
it. Nobody using the bot ever takes that path, which is why the report
says so -- but saying "this is not what a user waits for" and then not
saying what a user \*does\* wait for leaves the useful question unanswered.

An info command is the cheapest real thing a person can ask for -- /start
with a language already chosen is one indexed read of `user_settings` and
one reply -- and it is the floor under every other answer this bot gives.
So the read is done here, for real, through the same pool every handler
uses, and reported next to the trip to Telegram that carries the answer
back.

- At `with db.pooled_read() as conn:`:
  ManagerBot has no user_settings. Time the pool itself rather than
  report nothing -- it is the same round trip minus the index walk.

### `_telegram_round_trip()`

One call to the Bot API and back, in milliseconds.

getWebhookInfo, because it is the cheapest method that always goes to
Telegram: getMe is cached by python-telegram-bot after startup and would
report nought, which is the one wrong answer that looks like a fast one.

### `_cmd_ping()`

Plain `ping` answers a sentence. `ping trace` answers the numbers
ManagerBot needs to draw the full round trip -- see its /ping.

- At `user: dict = {}`:
  What a person waits for, measured rather than guessed. Both halves are
  optional: a failure here must not cost the round trip its report, which
  is the number the command was written for.
- At `user["total_ms"] = round(user["info_ms"] + user["telegram_ms"], 2)`:
  The answer's own cost: read the row, hand the reply to Telegram.
  Their trip to Telegram and back is on top and is not measurable
  from here, which is what the report says rather than folding an
  invented number in.

### `installed_versions()`

Every distribution this process can import, as `name==version`, sorted
-- what `pip freeze` would print, without starting pip.

The requirements files name lower bounds (`yt-dlp[default]>=2025.11.12`),
so what production actually runs is whatever the last build happened to
resolve. This is how that list is read back, to be pinned in the next
release, from the process itself rather than from a guess about the image.

### `_cmd_versions()`

`versions` sends the whole list as a requirements-style file;
`versions <name>` answers the matching lines as text.

### `_cmd_tellcredit()`

Tells one person, as this bot and in their language, that the operator changed their ⚡ balance:
"told", or why not. Asked for on 2026-11-18 ("give updates to the person who is receiving a gift
from me personally") and shipped first in MumuCombined 2.2.0. ManagerBot asks the public bots one
at a time (bot.py, CREDIT_NOTE_BOTS), because a bot can only message somebody who has started it.

### `_cmd_message()`

Sends as THIS bot -- that is the whole point of routing it here
rather than having ManagerBot send it: the user only ever sees the bot
they actually talked to.

### `_RecentLines`

The last lines one of the log files would hold, kept in memory. A host
that writes no log files -- Railway, unless LOG_TO_FILES=1 -- would
otherwise give /logs nothing to show: its console output goes to the
platform's own viewer, which is not somewhere ManagerBot can reach.

### `keep_recent_log_lines()`

Keep the latest lines of each log in memory, for /logs on a host with
no log files. Idempotent; attach() calls it.

### `_tail_lines()`

Reads the last `wanted` lines by seeking backwards from the end of the
file. bot.log rotates at 2 MB, and pulling all of it into memory to throw
away everything but the last forty lines is exactly the kind of spike this
bot cannot afford on a small container.

_At `LOG_TAIL_DEFAULT = 40`:_

How much of a log /logs prints: this many lines when none are asked for, and
never more than the maximum. Named so ManagerBot's logs page under /help reads the same
numbers the command uses.

### `_cmd_logs()`

- At `tail = _RECENT_LOGS[which].tail(lines_wanted)`:
  No file on this host (the cloud writes none unless LOG_TO_FILES=1),
  so what this process has logged since it started, from memory.

### `_cmd_restart()`

Restart the process this bot is in, through the soft stop
(lifecycle.soft_stop): refused while anybody is using it, and otherwise an
exit with code 1, which the host's restart policy starts again.

`restart force` is the old way, for a bot that is stuck: os.\_exit(1) at
once, whatever is running -- which the launcher turns into a restart of
this bot alone (main.\_BotOs.\_exit), and which ends a process of its own for
the host to start again. Run against a bot started by hand on a laptop, it
just stops it.

### `_cmd_softstop()`

Stop the process this bot is in -- every bot in it, one service -- and
stay down, if nobody is using it. The argument is when ManagerBot issued the
request, so one that waited out a downtime on the bus is not acted on late.

### `_cmd_softstopbot()`

Stop only this bot, if nobody is using it; the rest of its service
carries on, and it comes back when the service next starts.

### `_cmd_crashtest()`

Deliberately raise inside this bot, so the whole crash path can be
checked end to end from ManagerBot without waiting for a real bug.

StickerBot has had a local `/crashtest` for this since v0.4, and it was
the one operator-only command with no way to reach it from ManagerBot. Now
every bot has it, which is the more useful shape: what is actually worth knowing is whether the reporting works for the bot that just went quiet, and that is never the bot whose chat happens to be open.

The raise goes through `application.create_task` rather than being raised
here. A bus handler that raises is caught by `_run_one_command`, which
turns it into a failed command result -- informative, but it never
reaches the error handler, so it would test nothing. `create_task` routes
the exception to exactly where a real handler's would go:
shared_features.error_handler -> record_error -> emit_event -> the alert
ManagerBot forwards. If that alert does not arrive within a few seconds,
the crash reporting is broken, and this is how it was found out.

## Talking to everybody, and announcing an update before it lands

Four commands that all share one shape: ManagerBot decides, the bot the user
actually talks to does the speaking. That is the whole reason these live
here rather than in ManagerBot -- a person who has only ever met StickerBot
should hear about StickerBot's update from StickerBot, in their own
language, and not from a private bot they have never seen.

### `_i18n()`

This bot's translations, or None. ManagerBot has no i18n.py -- it has
exactly one reader -- so everything below degrades to plain English
rather than requiring one.

_At `_PLAIN = {`:_

Fallbacks for ManagerBot, and for any key a translation file has not caught
up with yet. Never the normal path in the four public bots.

### `phrase()`

One of the sentences above, translated if this bot can.

- At `if text and text != key:`:
  i18n.t returns the key itself when it has no such string, which
  is the signal to fall back rather than send someone a key.

_At `BROADCAST_ACTIVE_DAYS = int(os.environ.get("FAMILY_BROADCAST_ACTIVE_DAYS", "7"))`:_

Who counts as "active" when a broadcast is aimed rather than sent to
everybody. Seven days, and the number is a judgement about what an
announcement is for rather than about the data.

An advance notice of an update is only worth sending to somebody it might
actually affect. Sending it to every account that ever typed /start is a
push notification to people who used the bot once in March, which reads as
spam and is the fastest way to teach them to mute it -- so the next notice,
the one that matters, is not seen either. Seven days covers everyone with a
habit, including the weekly user, and excludes the long tail.

It is deliberately not the same window as /users' default (24 hours). That
one answers "is anyone around right now", which is an operational question
asked at the moment of deploying. This one answers "who would want to
know", which is asked the day before.

### `_recently_active()`

Everyone seen in the last `days`, or None if this bot cannot say.

None rather than an empty list, because the two mean opposite things: a
bot with no activity table cannot answer the question, and answering it
with "nobody" would silently turn an aimed broadcast into no broadcast at
all.

_At `BROADCAST_PER_SECOND = float(os.environ.get("FAMILY_BROADCAST_PER_SECOND", "20"))`:_

Telegram's documented ceiling for bulk sends is about 30 messages a second,
and a bot that trips it gets a 429 with a retry_after rather than a queue.
A broadcast is never urgent to the second, so it is paced under the limit
and simply keeps going past anyone who cannot be reached.

### `_send_to_each()`

(delivered, skipped). `targets` is an iterable of (user_id, chat_id);
`text_for(user_id)` returns that person's text, or None to skip them.

Nothing here raises. Someone who blocked the bot, deleted their account
or never really existed is a skip, not a failure -- the alternative is a
broadcast that stops halfway through the alphabet.

### `_cmd_broadcast()`

Sends one message to everyone this bot knows, as this bot.

Verbatim, and deliberately not translated: the operator wrote these words
and this file has no way to write them again in Uzbek. The four sentences
the \*bots\* say about updates are the translated ones.

_At `GIFT_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,39}$")`:_

---- the gift: bonus credit for everyone, once -----------------------------
One promotion at a time: `amount` of bonus credit for everyone this bot knows,
once per person across the family (grant_bonus_once's claim), and a notice in
their own language from the bot that won the claim -- so somebody who uses two
bots is given it once and told once. Without --go it only counts.

Run --go once per bot, at the moment the promotion is announced: a later run
also reaches whoever arrived in between, and a fixed moment is what stops the
gift being farmed with fresh accounts.

_At `GIFT_KEY_RULE = "The key is 3-40 lowercase letters, digits or dashes, starting with a le …`:_

What \_gift_args says of a key that does not fit GIFT_KEY_RE; ManagerBot's
gift page under /help prints the same sentence.

_At `_GIFT_NOT_PEOPLE = {1087968824, 136817688, 777000}`:_

Never real people: anonymous group admins, channels posting as themselves, and
Telegram (the ids delivery_cache.SHARED_SENDER_IDS also refuses).

_At `GIFT_CREDITS_PER_CONVERSION = 10`:_

What a paid conversion costs, roughly, for the notice's "about N conversions":
6-7 ⚡ for a small one, 13-33 for a big video. Ten is the honest middle.

### `_gift_last_day()`

`YYYY-MM-DD` -> the moment the gift runs out: the end of that day, UTC.

### `_gift_args()`

(key, amount, expires_at, last day as written), or the sentence saying
what is wrong. ManagerBot checks the same rules before it queues.

- At `return "That key belongs to the 220 ⚡ gift (/promo). Choose another."`:
  Its claims are the 220 gift's: /gift under it would count as claims
  in /promo and turn the people it reached into "already had it".

### `_bot_handle()`

A sibling as somebody can tap it, from SIBLING_BOTS (the format
shared_features documents), or its plain name.

## The 220 ⚡ gift: claimed by writing to ConvertBot or DownloaderBot (2.2.0)

One promotion, not a tool (2026-10-18: this one gift is not general, and is gone after its first use). ManagerBot counts the family's unique users and tells the operator once they reach PROMO_USERS; the operator's /promo then starts it. The start is a row in family.settings that is written once and
never changed, so this gift can never run a second time.

PROMO_USERS was 220 until 2026-11-18, when it was decided to start the gift
at 204 users. 2.2.0 of the deployed codebase lowered the mark to 200 first, so
the number the texts name is true when they are read. The key
(`gift-220-users`) and the amount stay as they were, so nothing already
written under them changes.

From the start, for PROMO_CLAIM_DAYS (3 since 2026-11-18; it was 14), ConvertBot and DownloaderBot
offer the gift to whoever writes to them, and the offer's button or /gift gives them PROMO_CREDITS ⚡
of bonus credit, once (grant_bonus_once: the claim and the credit in one transaction), to spend
until PROMO_SPEND_UNTIL -- the same day for everyone, the end of 1 December 2026, as the operator
asked on 2026-11-18. PROMO_SPEND_DAYS (28 days from the claim) was the rule before and is no
longer used for it. AnonBot shows its ad to each person once
meanwhile. /promo stop ends the claiming early; credit already given stays.

Every day here ends at midnight UTC, the rule gift dates already follow (TERMS:
"expires at the end of the last day stated"), so the dates people are shown
are whole days.

_At `PROMO_REFRESH_SECONDS = 60`:_

How stale a bot's idea of "has it started" may be. Only the start can lag: the
end is read off the clock, and a claim re-reads both before it gives anything.

_At `PROMO_GIVEN, PROMO_HAD, PROMO_CLOSED = "given", "had", "closed"`:_

What claim_promo_gift says happened.

### `_end_of_day()`

The moment a date ends: the midnight UTC after it.

_At `def _claim_setting(key: str, value: str) -> bool:`:_

Everything below reads and writes the database itself, never get_setting's
in-process fallback: that fallback is what a process gets when its family bus
did not come up, and a gift started in one process's memory would be "started"
for ManagerBot's card and for nobody else.

### `_claim_setting()`

Write a setting only if it is not there yet. True if this call wrote it.
Blocking; raises on a database error (unlike set_setting: a caller that
starts something has to know whether it did).

### `_read_promo()`

(started, stopped), each a UTC datetime or None. Blocking.

Cached for PROMO_REFRESH_SECONDS. A `fresh` read raises on a database error,
so that a claim never takes a failed read for "not stopped". A cached-path
read that fails answers "not started" until the next refresh: closed, which
gives nothing away, and one connection attempt a minute rather than one per
message while the database is down.

### `promo_closes_for()`

When claiming ends for a gift started at `started`, unless it is
stopped first: the end of the PROMO_CLAIM_DAYS-th day after the start (UTC).
ManagerBot's card uses it to say what a start now would mean.

### `promo_window()`

(started, closes) once the gift has been started, else None.
`closes` is the end of the PROMO_CLAIM_DAYS-th day after the start, or the
moment it was stopped if that came first. Blocking; raises on a database
error.

### `promo_open()`

Can the gift be claimed at `now`? Blocking. Only a `fresh` read raises
on a database error; the cached one answers False (see \_read_promo).

### `promo_expiry()`

When a gift claimed at `claimed_at` runs out: PROMO_SPEND_UNTIL, whenever it was claimed.

### `promo_spend_by()`

The last day the gift can be spent, as the texts show it (01.12.2026).

### `claim_promo_gift()`

Give this person the gift if it is open and they never had it:
(PROMO_GIVEN, when it runs out), (PROMO_HAD, None) or (PROMO_CLOSED, None).
Re-reads the start and the stop first, so a cached "open" does not outlive
/promo stop. (A claim that read "open" in the moment before the stop was
committed still pays: a handful at most, accepted.) Blocking; raises on a
database error, and then nothing was given.

### `promo_given_to()`

Has this person had the gift? Blocking; raises on a database error.

### `promo_claim_count()`

How many people have had the gift. Blocking; raises on a database error.

### `_refresh_promo_after_write()`

Re-read the state after this process changed it. Never raises: the
write is already committed, and a failed read-back must not make the operator's card say that nothing happened.

### `start_promo()`

Start the gift -- possible once, ever. (when it started, True if this
call started it). Blocking; raises on a database error, but never after
this call's start is written.

### `stop_promo()`

End the claiming now, if the gift is open. True if this call ended it.
Credit already given is kept. Blocking; raises on a database error.

### `note_promo_milestone()`

True the first time ever this is called: ManagerBot's one notice to the operator that the users have reached PROMO_USERS. Blocking; raises on a
database error.

### `promo_milestone_at()`

When the users first reached PROMO_USERS, if they have. Blocking; raises
on a database error.

### `promo_notice()`

What somebody reads in ConvertBot or DownloaderBot when the gift was
just given to them. Plain text.

### `promo_offer()`

What ConvertBot and DownloaderBot say to somebody who writes while the gift is open and has not
had it: the amount, the last day to claim, the last day to spend, and how to claim. Plain text.

### `promo_ad()`

AnonBot's ad for the gift, in HTML (the last paragraph is in italics): how to claim it (/gift, or
the button the bots show) and both dates.

### `_cmd_pause()`

Stop starting work a redeploy would throw away, and say so.

Anyone turned away is written down, so `resume` can tell them it is over.

### `_cmd_warnbusy()`

Tell everyone mid-something that it is about to be lost.

Only the people actually inside a slow stretch of work: with state now
persisted, an open conversation survives a redeploy and warning about it
would be a false alarm. What does not survive is an encode or a download
already running, which is exactly what busy() tracks.

### `_cmd_resume()`

The other half of pause: reopen, and go back to everyone who was
turned away while it was closed.

### `_claim_next_command()`

FOR UPDATE SKIP LOCKED so two copies of the same bot (a laptop one
and a deployed one both pointed at the same database) can't run the
same command twice.

_At `_STOP_COMMANDS = frozenset({"softstop", "softstopbot", "restart"})`:_

ManagerBot's result pump collects this on its next pass. That pass is at
the fast cadence: ManagerBot marked the bus active when it queued the
command, so the whole time an answer could be coming back it is looking
about once a second.

_At `_STOP_COMMANDS = frozenset({"softstop", "softstopbot", "restart"})`:_

Family commands this process is running now -- a lifecycle work source (see
attach), so the soft stop and the SIGTERM wait count them in-process. The
table's 'running' rows were all they had before, and ManagerBot's watchdog
marks a command 'timeout' 90 s after it was issued even while it still runs:
a long broadcast was invisible and could be cut off (review of 2026-11-14).
The stop requests themselves are not counted, or a stop would wait for itself.

_At `MAX_COMMANDS_PER_PASS = int(os.environ.get("FAMILY_MAX_COMMANDS_PER_PASS", "10"))`:_

One pass does not mean one command: several can be queued between two ticks
(a /ping to everything, a bot that was down catching up), so drain rather
than leave the rest for "next time". The ceiling is there so that a queue
somebody filled by accident cannot monopolise the event loop -- what is left
waits for the next tick, which is immediate while the bus is active.

### `_poll_commands()`

- At `mark_bus_active()`:
  Commands cluster -- one arriving means another probably is too.
  Keep looking at the fast cadence for a while.

_At `_last_bus_poll_at = 0.0`:_

\_bus_tick fires every BUS_POLL_ACTIVE_SECONDS and decides whether this tick
is also a database poll: always while the bus is active, otherwise once per
BUS_POLL_IDLE_SECONDS. This is the whole delivery mechanism -- there is no
push behind it any more -- so it is deliberately simple and has nothing that
can be "down".

## Housekeeping -- every bot tidies up after itself

Three tables grow forever if nobody deletes from them: family.commands (with
a BYTEA column, so a handful of /dbdump zips is megabytes), family.events,
and each bot's own activity_events. On a metered database that is a bill
that only goes up, for rows nothing will ever read again.

Each bot prunes only its \*own\* rows, so the five processes never collide and
no coordination is needed -- target_bot / bot_id already partition the shared
tables perfectly, and activity_events lives in the bot's own schema.

### `_prune_family_rows()`

- At `cur = conn.execute(`:
  Button-tap counts live as long as the usage samples do.

### `_prune()`

- At `try:`:
  The volunteered halves of old problem reports, and then the old rows.
  Every bot runs this over the shared table rather than only its own rows:
  it is idempotent, it is a cheap indexed delete, and a family table that
  only gets tidied when one particular bot happens to be up is a family
  table that grows whenever that bot is the one that is down.

## Wiring

_At `_GUARD_RETRY_SLEEPS = (2, 4, 8, 16, 30, 30)`:_

Seconds to wait before each new try when the database does not answer the
check; one more try than there are entries. Module-level so tests can
shorten them.

### `attach()`

- At `app.job_queue.run_repeating(`:
  The command bus. One repeating tick at the fast cadence; \_bus_tick
  decides on each one whether to actually poll (always while the bus is
  active, otherwise every BUS_POLL_IDLE_SECONDS).
- At `mark_bus_active()`:
  A bot that has just started is usually about to be pinged by ManagerBot's
  roll-call, so open with the fast cadence rather than making that first
  command wait a full idle interval.
- At `app.job_queue.run_repeating(_housekeeping, interval=HOUSEKEEPING_SECONDS, first=60)`:
  First pass a minute in rather than at startup, so a restart loop cannot
  turn into a delete storm.
