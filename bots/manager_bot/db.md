# `db.py`

[↑ manager_bot](README.md) · [code](db.py)

Postgres layer for ManagerBot.

ManagerBot is the one process in the family that reads \*across\* schemas. It
owns `manager_bot.*` (just its own activity log, so the same /status
machinery as every other bot works here too), it reads and writes
`family.*` (heartbeats, events, the command queue -- created by
family_link.py, which every bot in the family carries), and it reads the
four public bots' schemas to answer "how many users has ConvertBot had this
week" without going anywhere near their processes.

Read-only across other bots' schemas, deliberately -- ManagerBot never
writes into a bot's own tables. When something needs changing inside a bot,
it goes through the family command queue so that bot does it itself, with
its own code and its own invariants.

_At `DB_SCHEMA = os.environ.get("DB_SCHEMA", "manager_bot")`:_

ManagerBot's own schema in the shared database -- same convention as every
other bot (see family_link.py's layout diagram).

## Connection-string sanity check

Two ways of pointing a bot at a cloud Postgres fail \*quietly\* rather than
loudly, so both are worth catching at startup instead of in the data:

```text
Transaction pooling -- Supabase/Supavisor on port 6543, or PgBouncer in
transaction mode -- multiplexes many clients over a few server
connections, so per-connection startup options do not survive from one
transaction to the next. The pool below passes search_path as exactly
such an option, which means the bot would read and write "public"
instead of its own schema, while still heartbeating perfectly. That
surfaces as wrong data rather than as a broken bot, which is the worst
way to find out. The session pooler (port 5432) keeps one server
connection per client and is the right one here.
```

```text
An unencoded "@" or ":" in the password splits the URL in the wrong
place, so libpq ends up resolving a hostname that is really the tail of
the password -- a DNS error that says nothing about the real cause.
```

These warn rather than refuse: an unusual setup is the operator's business,
and a bot that will not start is worse than one that says why it might
misbehave.

### `check_database_url()`

Human-readable warnings about `dsn`; empty when it looks sane.

## One pooled connection per process

Every function below used to open -- and immediately throw away -- its own
Postgres connection. On a small shared cloud database that is by far the
most expensive thing this bot does: a TCP round trip, a TLS handshake and a
freshly forked backend process on the server, all to run one INSERT that
takes microseconds. At one connection per Telegram update (plus one per
heartbeat, per command poll, per donation check) it is also what decides
how big the database instance has to be.

A pool keeps a warm connection open instead and hands it out. Sized for
cheap: one connection held, a couple more only while several things happen
at once, and any extra handed back to the server after DB_POOL_MAX_IDLE
seconds -- so an idle bot costs the database exactly one backend.

_At `POOL_CHECK_AFTER_IDLE = float(os.environ.get("DB_POOL_CHECK_AFTER_IDLE", "45"))`:_

How long a pooled connection may sit unused before it is worth spending a
round trip proving it is still alive. See \_check_if_idle: the check was
unconditional, and against a database on the other side of the world an
unconditional check is the single most expensive thing about a small query.

_At `_last_known_good: "dict[int, float]" = {}`:_

id(connection) -> when it was last known good. Bounded by max_size. An id
can be reused after a connection is closed, and the worst that costs is a
skipped check on a connection that was only just opened -- which is alive
by construction.

### `_check_if_idle()`

The pool's checkout check, but only for connections that have actually
been sitting there.

A connection idle across a cloud provider's own network timeout comes back
dead, and `ConnectionPool.check_connection` is the guard against handing
one out. It is also a full round trip, and it was being paid on every
checkout -- including the checkout half a second after the last one, on a
connection that could not possibly have gone stale in between.

That is most of them. It cost a quarter of a second each back when this
bot's database was in ap-northeast-2 and the container in EU West -- a
third of the cost of every read. Since v1.2.0 the database is in
eu-central-1, beside the containers, which cuts the absolute cost by an
order of magnitude but leaves the ratio alone: the check is still a whole
extra round trip per read. A connection used within the last
POOL_CHECK_AFTER_IDLE seconds is taken as alive, and everything quieter
than that is still proved before use.

### `_get_pool()`

Created on first use, never at import time -- init_db() has to be able
to create the schema before anything connects into it.

- At `"options": f"-c search_path={DB_SCHEMA},public "`:
  Nothing here may wait on the network for ever. The five bots
  share one process and eight threads, and a query whose
  packets go unacknowledged (a dead network path) otherwise
  waits out TCP retransmission -- minutes -- holding one of
  them: measured in the incident of 2026-10-09 08:40 UTC. So a
  connection attempt gives up after 10 s (connect_timeout),
  data nobody acknowledges after 30 s (tcp_user_timeout, in
  ms), a statement after 60 s (statement_timeout) and a
  transaction left open and idle after 120 s
  (idle_in_transaction_session_timeout). /sql sets its own,
  shorter statement_timeout for the length of its transaction
  (run_readonly_query), which overrides this one.
- At `"keepalives": 1, "keepalives_idle": 30,`:
  Keep an idle connection alive at the TCP level rather than
  discovering it is dead on the next checkout. Cheaper than
  the check it saves, and it happens while nobody is waiting.

### `pooled()`

A connection from the pool, as a context manager. The transaction is
committed on a clean exit and rolled back on an exception; the connection
itself goes back to the pool either way rather than being closed.

For anything that writes. Reads should use pooled_read(), which is the
same connection without the transaction around it.

### `pooled_read()`

A pooled connection in autocommit, for statements that only read.

A read through pooled() costs three round trips to the database: the
implicit BEGIN that psycopg opens with the first statement, the statement
itself, and the COMMIT the context manager sends on the way out. Two of
those exist to make a transaction nobody needed -- a single SELECT is
atomic on its own.

Measured against the family's actual database, one read: 28 ms through
pooled(), 9 ms through this. The same shape holds wherever the database
is; it is round trips, so it scales with the distance rather than washing
out. Everything that writes -- and anything reading several statements
that have to agree with each other -- still goes through pooled().

- At `try:`:
  Back to the pool as it was found, so pooled() still gets a
  connection that opens a transaction.

### `close_pool()`

Shutdown hook -- lets the process exit without waiting on the pool's
own worker threads.

### `connect()`

A brand-new, unpooled connection to an arbitrary database. Only the
offline tools (db_merge.py) need this, because they hold two databases
open at once and drive the transaction by hand. Everything in this module
goes through pooled() instead.

### `init_db()`

Only ManagerBot's own tables. The family.\* tables are created by
family_link.init_family_schema(), which runs in every bot including
this one, so whichever process starts first sets them up.

- At `ensure_schema(dsn)`:
  Deliberately on a plain connection rather than the pool: the offline
  tools (db_merge.py, migrate_to_shared_db.py) call this against a
  \*different\* database than the one this process serves, and the pool
  is bound to DATABASE_URL. It runs once, so there is nothing to save.

_At `def record_activity_batch(user_ids) -> None:`:_

---------- this bot's own activity (mirrors every other bot's db.py) ----------

### `record_activity_batch()`

One row per user per flush window -- see shared_features.py's
track_activity, which buffers them. Sent as a single statement whatever
the batch size: both readers of this table are COUNT(DISTINCT user_id)
over a time window, so nothing depends on a row per update.

_At `def active_user_ids_since(since) -> list[int]:`:_

---------- family.heartbeats ----------

### `active_user_ids_since()`

Everyone with activity since `since`. Used by the family bus for an
aimed broadcast -- see BROADCAST_ACTIVE_DAYS in family_link.py.

### `heartbeat_of()`

One bot's row, or None if it has never been seen.

Separate from all_heartbeats() because it is asked on the way into every
/run: the version check in bot.py wants one bot, not five, and this runs
in front of a command the operator is waiting on.

_At `def get_known_state() -> dict[str, bool]:`:_

---------- family.bot_state (edge-triggered up/down alerting) ----------

_At `def take_unnotified_events(limit: int = 20) -> list[dict]:`:_

---------- family.events ----------

### `take_unnotified_events()`

Claims and returns them in one statement, so a ManagerBot that gets
restarted mid-DM doesn't re-send the whole backlog.

### `log_event()`

ManagerBot's own events go in already marked as notified -- it is the
one doing the notifying, so forwarding them to itself would be a loop.

_At `def queue_command(target_bot: str, command: str, args: str, requested_by: int,`:_

---------- family.commands ----------

### `queue_command()`

Queue one command for a bot to pick up.

The target finds it on its next bus poll -- about a second if the bus is
already busy, at most FAMILY_BUS_POLL_IDLE_SECONDS if it has been quiet
(see family_link.\_bus_tick). The caller should call
family_link.mark_bus_active() right after this so ManagerBot's own result
pump goes to the fast cadence while the answer is on its way back.

- At `try:`:
  One choke point for every /ping, /run, /pause, ... so ManagerBot's result
  pump goes to its fast cadence the instant a command is outstanding,
  without each call site having to remember to say so. Imported here
  rather than at module scope because family_link imports this module.

### `expire_stale_commands()`

A command aimed at a bot that is down never gets claimed. Rather than
leave it pending forever, mark it timed out so the operator is told instead of silently waiting.

_At `def get_setting(key: str, default: str | None = None) -> str | None:`:_

---------- family.settings (ManagerBot's own toggles) ----------

_At `def _existing_tables(conn, schemas: list[str], table: str) -> set[str]:`:_

---------- cross-schema reads (the whole point of one shared database) ----------

### `_existing_tables()`

Which of these schemas actually have this table yet -- one lookup for
the whole family rather than one per bot.

### `active_users_by_schema()`

{schema: (active since `since`, all-time known)} for every schema that
has an activity_events table. A schema missing from the result has never
been created -- worth showing differently from a real zero.

One connection and one UNION ALL for the whole family. /status ran this
query once per bot, each on its own connection; ManagerBot's own status
screen was eight separate connections to the same database.

### `donations_by_schema()`

{schema: [(currency, paid transactions, total amount), ...]}. Amounts
are in each currency's own unit -- Stars for XTR, minor units for fiat --
exactly as star_transactions stores them. One trip for the whole family.

_At `PEOPLE_TABLES = ("activity_events", "user_settings", "settings")`:_

Where each bot keeps a row per person, besides activity_events: AnonBot, ConvertBot
and StickerBot call it user_settings, DownloaderBot's per-person table is settings.

### `unique_people()`

How many different people have used any of these bots, each counted once
however many bots they used: the distinct user_id over every schema's
activity_events and whichever of user_settings and settings it has, leaving
out ids that are not people (zero and below, and family_link's
\_GIFT_NOT_PEOPLE: Telegram, anonymous admins, channels). One connection. The
220 gift's count (family_link.PROMO_USERS).

It is built from tables that exist and not from a list of its own, so it can
only under-count: an activity_events row older than ACTIVITY_RETENTION_DAYS is
pruned, and somebody with no settings row then drops out. It can never
over-count, which is what "the bots have passed 200 users" (PROMO_USERS)
needs to stay true. Schema and table names come from the registry and the catalog, never
from a person.

AnonBot stores its people as pseudonyms (negative numbers, which the
`user_id > 0` test below leaves out) and keeps each real id sealed beside
its pseudonym in user_settings.user_sealed. So the count is made in two
steps: the ids the other tables hold in the clear, and the ids opened from
those sealed values (shared/security/vault.py, purpose "anon.user", checked against
the row's pseudonym), joined in memory. A row that does not open is skipped
and counted in one warning that names the table only. Since 2.1.2 an
AnonBot user_settings row exists for everyone with activity, so its
activity_events need not be opened. Before the conversion nothing is
sealed, the plain ids are all there is, and the answer is the same as it
always was. Nothing is stored and no id is logged.

- At `import family_link`:
  Inside the function: family_link imports `db` (this module) at its top, so
  importing it at the top here would be circular.
- At `with_user_id = {tuple(row) for row in conn.execute(`:
  A table that goes by one of these names is only a list of people if it
  has a user_id column (a bot's settings could be a key/value table).
- At `people = {row[0] for row in conn.execute(`:
  UNION, not UNION ALL: a person in two bots comes back once (and
  the set below would hold them once anyway).
- At `for schema in sorted(_existing_tables(conn, schemas, "anon_conversations")):`:
  AnonBot's people: any schema with anon_conversations is AnonBot's
  (the same mark the key guard uses), and its settings hold the sealed ids.
- At `rows = conn.execute(`:
  Only the rows that hold a pseudonym: a plain id (unconverted data)
  was counted above, and has nothing to open.

_At `PEOPLE_COUNTS = f"{FAMILY_SCHEMA}.people_counts"`:_

Unique people per bot. activity_events keeps who used a bot for ACTIVITY_RETENTION_DAYS, so
any stretch inside that window is counted exactly, live, and nothing is stored for it. What
outlives the window is only numbers: each UTC day's and each calendar month's count per bot
(and "\*", everyone across the bots), written once the day or month is over. They hold no ids,
marks or hashes -- nothing that says who -- so there is nothing in them to erase for anybody.

_At `FINAL_AFTER = timedelta(hours=1)`:_

A day (or month) is counted this long after it ends, when every bot has flushed its activity.

### `_ensure_people_counts()`

- At `conn.execute(f"DROP TABLE IF EXISTS {FAMILY_SCHEMA}.people_by_day")`:
  Before release, 2.2.0 kept a keyed mark per person per day here, for good. With the
  data key those marks could tell whether somebody had been active, so they are gone.

### `first_exact_day()`

The oldest UTC day activity_events still holds whole.

### `_real_ids_of()`

AnonBot's pseudonyms -> the real ids sealed beside them, so "\*" counts a person once.

### `_active_by_day()`

{day: {bot id or "\*": set of people}} from first_day to last_day, both included, in
memory only. "\*" is everyone across the `everyone` schemas, each real person once (AnonBot's
pseudonyms opened), and only while the data key opens; without it "\*" is left out.

### `people_since()`

(unique people across `schemas` with activity since `since`, whether that is complete).
For the nightly report's "last 24 hours", which is a window and not whole days. Each real
person once: AnonBot's pseudonyms are opened as in \_active_by_day. Without the data key they
cannot be, so they are left out and the answer says it is short. Nothing is stored.

- At `real = _real_ids_of(conn, schema, pseudonyms)`:
  One that does not open has no seal beside it any more: somebody who
  has since erased their data, and is not counted, rightly.

### `people_live()`

Exact unique people per bot (and "\*") from first_day to last_day, both included. Only
days from first_exact_day() on are complete; the caller keeps to them.

### `record_people_counts()`

Write the count of every day and month that is over and still wholly inside the
activity window but not written yet. Only while the data key opens, so "\*" is never
stored short. Returns how many periods were written.

### `people_stored()`

{start: {bot id or "\*": people}} of the stored counts of `period` starting from
first_day to last_day.

### `promo_seen_count()`

How many people that bot's schema says were shown this announcement, or
None when the schema has no promo_seen table (a bot that never had one, or
not created yet).

_At `SQL_TIMEOUT_SECONDS = 10`:_

What /sql allows itself, named so ManagerBot's sql help page reads the same numbers:
a statement is stopped after this many seconds, and at most this many rows come back.

### `run_readonly_query()`

Backs ManagerBot's /sql. The safety here is the connection being
genuinely read-only at the transaction level -- Postgres itself rejects
any write, so this does not depend on parsing the statement correctly.
The keyword check in bot.py is only there to give a friendlier error.

Both SETs are LOCAL, i.e. scoped to this transaction. The connection is
pooled and gets handed to the next caller afterwards; a plain SET would
leave a ten-second statement timeout stuck on it for the rest of the
process's life.

READ ONLY lasts as long as the transaction does, and one string may hold
several statements: psycopg sends a query without parameters over the
simple protocol, which runs them all, so `SELECT 1; COMMIT; DROP TABLE x`
ended the read-only transaction on its second statement and ran the third
in a new one that was not. `prepare=True` sends it over the extended
protocol instead, in which Postgres itself refuses more than one statement
("cannot insert multiple commands into a prepared statement") -- so the
guarantee is Postgres's, not a parser's.

_At `ACTIVITY_RETENTION_DAYS = int(os.environ.get("ACTIVITY_RETENTION_DAYS", "90"))`:_

---------- housekeeping ----------
activity_events is append-only and powers nothing older than the retention
window below (/status counts the last hour and since-start, ManagerBot's
/users the last N hours). Left alone it is the one table in this schema that
grows without limit, which on a metered database is a bill that only ever
goes up. family_link.py's housekeeping job calls this.

### `prune_old_data()`

Returns how many rows were removed. Safe to run at any time.

_At `def dump_database_csv_zip() -> bytes:`:_

---------- exports ----------

### `dump_database_csv_zip()`

ManagerBot's own schema only -- same contract as every other bot's
db.py, so family_link's `dbdump` command works here unchanged. For the
whole family in one file, see dump_family_csv_zip below.

### `dump_family_csv_zip()`

Every schema in the shared database, one folder per schema, one CSV
per table. This is the "download the whole database" button: it needs
nothing but the psycopg connection already in use here, so it works
from anywhere without pg_dump being installed.

### `_csv_safe()`

family.commands carries a BYTEA column (a finished /dbdump waiting to
be delivered). Writing raw bytes into a CSV would produce megabytes of
unreadable escaping, so they are summarised instead -- the real payload
was already sent to Telegram when the command finished.

### `status_snapshot()`

Everything ManagerBot's /status prints, on one connection: heartbeats,
per-bot active users, the alerts toggle and the database's own size.

Drawing that board used to mean eight connections -- one for the
heartbeats, one per bot for its user count, one for the setting and one
for the database size -- every time four characters were typed.
