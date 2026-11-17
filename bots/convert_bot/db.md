# `db.py`

[↑ convert_bot](README.md) · [code](db.py)

Postgres layer for ConvertBot -- these tables are this bot's
alone. The family shares one Postgres database now, but each bot gets its
own schema in it (DB_SCHEMA below), and no bot reads or writes another's
tables; the only shared tables are `family.*`, which family_link.py owns.

Owns the Telegram Stars ledger for both the
file-converter feature and this bot's own /donate flow (every invoice and
payment outcome, so spend can be audited later), the donation-reminder
cooldown, and each user's chosen language (/en, /uz, /rus).

Postgres was chosen (over SQLite) so this survives an ephemeral cloud
container redeploy -- see DEPLOY.md.

_At `DB_SCHEMA = os.environ.get("DB_SCHEMA", "public")`:_

--- one shared database, one schema per bot ---------------------------------
The whole family now lives in ONE Postgres database, with a schema per bot
(family_link.py has the full layout). DB_SCHEMA is the one this bot owns.
Nothing else in this file had to change: every statement below is still
written against bare table names, and search_path resolves them into this
bot's own schema, so the four bots' identically-named tables
(user_settings, star_transactions, activity_events, ...) never collide.
Leaving DB_SCHEMA unset keeps the old behaviour -- "public" in a database
this bot has entirely to itself.

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
  (idle_in_transaction_session_timeout).
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

### `ensure_schema()`

Deliberately connects \*without\* the search_path option -- the schema
it is about to create may not exist yet, and libpq would not complain
but every later CREATE TABLE would land in public instead.

### `_column()`

(data_type, column_default) of `column` in this bot's schema, or None
when the table has no such column.

init_db() asks this before every ALTER TABLE and issues the ALTER only when
the answer says something is missing or different. Every ALTER TABLE takes
an ACCESS EXCLUSIVE lock on its table and keeps it until the transaction
ends -- even one that turns out to change nothing, IF NOT EXISTS or not
(measured on Postgres 17, 2026-10-09) -- and init_db() is one transaction
that commits at the very end. So a start that only wanted to check used to
queue every other reader and writer of those tables behind it for as long
as init_db() took, and during a deploy overlap those are the old
instance's, still serving. This is a read of the catalog, which takes no
lock that blocks anybody; a normal start now takes none of the table locks.
IF NOT EXISTS stays on the statements, because two instances starting
together can both see the column missing; the second then waits for the
first and finds nothing left to do. The statements are literals, one per
column, on purpose: tests/test_sql_safety.py allows no SQL built from
names.

### `init_db()`

- At `ensure_schema(dsn)`:
  Deliberately on a plain connection rather than the pool: the offline
  tools (db_merge.py, migrate_to_shared_db.py) call this against a
  \*different\* database than the one this process serves, and the pool
  is bound to DATABASE_URL. It runs once, so there is nothing to save.
- At `conn.execute(`:
  Nullable, no default -- NULL means "hasn't picked a language yet",
  which is what gates the first-run picker in bot.py's /start.
- At `if _column(conn, "star_transactions", "username_sealed") is None:`:
  Each safe to re-run on an existing table, and each runs only when it is
  needed (see \_column): username_sealed for tables from before 2.2.0 sealed
  the names; amount_stars widened, since some fiat currencies' minor-unit
  amounts can exceed a plain INTEGER's range; currency for bots migrating
  from Stars-only donations (see shared_features.py's FIAT_CURRENCIES).
- At `if _column(conn, "donation_prompts", "times_shown") is None:`:
  How many times this person has ever been shown the nudge. The
  cadence is a schedule that runs out rather than a loop (see
  DONATION_STEPS in shared_features.py), and this is the step counter
  it reads. Safe to re-run on an existing table.
- At `if _column(conn, "donation_prompts", "pinned_message_id") is None:`:
  Set only when DONATION_PIN is on: the message this bot pinned, so
  it can be unpinned again when they donate or when a newer nudge
  replaces it.
- At `conn.execute(`:
  A paid conversion that is running, so that the credit taken for it
  can be given back even when nothing is left to give it back: the
  container killed outright (out of memory, a host failure) ends the
  process without the graceful path that refunds. See jobs.py.

_At `def hold_conversion(job_id: str, user_id: int, chat_id: int, price: int, lang: str | None,`:_

---------- paid conversions in flight ----------
One row per paid conversion while it runs. The process running it stamps
its rows every jobs.HOLD_TOUCH_S; a row nobody has stamped for
jobs.HOLD_STALE_S belongs to a process that is gone, and whichever process
notices refunds it -- once. The refund is decided by who flips `recovered`,
and the process that was presumed dead, if it was only slow, sees the flag
when it finishes and does not refund a second time.

### `release_conversion()`

The conversion ended here. "held" if its row was still this process's to settle,
"recovered" if another process already refunded it, "absent" if there was
no row (never written, or erased with /deletemydata).

### `claim_orphaned_conversions()`

Rows of processes that stopped stamping them, claimed for refunding.
Claimed rows stay, flagged, so a late finisher knows; prune_old_data
removes them a day later.

_At `def record_activity_batch(user_ids) -> None:`:_

---------- /status active-user tracking ----------

### `record_activity_batch()`

One row per user per flush window -- see shared_features.py's
track_activity, which buffers them. Sent as a single statement whatever
the batch size: both readers of this table are COUNT(DISTINCT user_id)
over a time window, so nothing depends on a row per update.

### `active_user_ids_since()`

Everyone with activity since `since`. Used by the family bus for an
aimed broadcast -- see BROADCAST_ACTIVE_DAYS in family_link.py.

### `list_all_users()`

Everyone this bot could send an unprompted message to.

The union of two tables because neither is the whole answer on its own:
user_settings has a row per person who has ever picked a setting and is never
pruned, while activity_events reaches people who only ever used the bot
without changing anything -- but is pruned at ACTIVITY_RETENTION_DAYS.
Together they are "everyone the bot still knows about", which is the honest
scope of a broadcast.

### `get_user_language()`

None means the user hasn't picked a language yet (no row).

_At `def record_star_invoice(`:_

---------- Telegram Stars ledger (file converter + /donate payments) ----------
Every conversion that isn't free gets a row the moment the invoice is sent
(status "invoiced"), which is then flipped to "paid" or "refunded". Free
conversions are logged too, at amount_stars = 0, so usage stays visible
even though no money moved.

### `record_star_invoice()`

amount_stars is in the currency's smallest unit for fiat currencies
(see shared_features.py's FIAT_CURRENCIES), or a plain Stars count for
the default currency="XTR".

### `get_user_star_summary()`

(total Stars actually paid, most recent transactions) for /mystars.

The total is Stars-only (currency = 'XTR'): fiat donations (see
shared_features.py's FIAT_CURRENCIES) are in a different unit, so adding
them to a Stars count would be meaningless. The history below is the full
mixed-currency list, each row tagged with its own currency.

Both halves on one connection, because they are always wanted together.

### `get_star_ledger()`

Everything the operator-only /stars command prints: global totals plus the
most recent transactions.

The totals used to be four separate COUNT/SUM queries over the same table.
They are one scan with FILTER clauses now -- same numbers, a quarter of
the work, and no chance of the four disagreeing with each other because a
payment landed between them.

_At `def bump_donation_action(user_id: int) -> tuple[int, str | None, int, bool]:`:_

---------- donation-reminder cooldown ----------

### `bump_donation_action()`

Increments this user's action counter and returns everything the nudge
decision needs: (actions since the last nudge, when it was last shown,
how many times it has ever been shown, has this person ever donated).

One statement. This runs on the success path of every completed action --
every finished pack, every conversion, every download -- so it is one of
the hottest writes in the family, and the database is on another
continent. The donation check used to be two round trips and the "have
they already given" question would have made it three.

`times_shown` is what turns the cadence from "every N actions forever"
into a schedule that runs out: see DONATION_STEPS in shared_features.py.
The paid check is what stops the bot thanking somebody by asking them
again.

### `reset_donation_prompt()`

Zeroes the action counter, stamps 'last_shown_at' and counts the
showing -- call right after actually showing the nudge, not on every
check.

### `get_pinned_donation_message()`

The id of the nudge this bot pinned in that person's chat, if any.

_At `ACTIVITY_RETENTION_DAYS = int(os.environ.get("ACTIVITY_RETENTION_DAYS", "90"))`:_

---------- housekeeping ----------
activity_events is append-only and powers nothing older than the retention
window below (/status counts the last hour and since-start, ManagerBot's
/users the last N hours). Left alone it is the one table in this schema that
grows without limit, which on a metered database is a bill that only ever
goes up. family_link.py's housekeeping job calls this.

### `prune_old_data()`

Returns how many rows were removed. Safe to run at any time.

_At `def erase_user(user_id: int) -> int:`:_

---------- erasing one person, on request ----------
What /deletemydata reaches -- see shared_features.py, which owns the
command and the confirmation. Everything in this schema that is about one
person goes, in a single transaction, with one deliberate exception.

The Stars ledger keeps its rows. A donation is a payment, and a payment
record has to outlive the payer asking to be forgotten: it is what a refund
is issued against and what the totals are counted from. The username is
cleared, since it is the one free-text identifier on the row; the numeric
id stays, because without it a refund cannot be sent to anybody. The
privacy notice says so rather than implying the erase is total.

### `erase_user()`

Returns how many rows were removed. Blocking; call through
asyncio.to_thread.

Files being converted are not in here at all -- they are written to the
staging directory, deleted the moment the conversion finishes or fails,
and swept on a timer and at startup for anything a crash left behind.

_At `def dump_database_csv_zip() -> bytes:`:_

---------- admin: full database export ----------

### `dump_database_csv_zip()`

Exports every table in this bot's own schema to one CSV per table,
zipped together -- this bot's data only, never a sibling's. Deliberately not pg_dump-based -- that binary
isn't guaranteed to exist wherever this bot ends up hosted, while this
only needs the psycopg connection already used everywhere else here.
