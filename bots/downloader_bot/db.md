# `db.py`

[↑ downloader_bot](README.md) · [code](db.py)

Postgres layer for DownloaderBot -- these tables are this bot's
alone. The family shares one Postgres database now, but each bot gets its
own schema in it (DB_SCHEMA below), and no bot reads or writes another's
tables; the only shared tables are `family.*`, which family_link.py owns.

```text
Owns:
- per-user setting: whether downloaded videos get a credit caption
- per-group settings, for the groups the bot is in (groups.py): one row per
  group chat, holding only the switches, deleted when the bot leaves it
- a Telegram Stars ledger + donation-reminder cooldown, for this bot's own
  /donate flow (see shared_features.py)
```

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
- At `if _column(conn, "settings", "language") is None:`:
  Nullable, no default -- NULL means "hasn't picked a language yet",
  which is what gates the first-run picker in bot.py's /start.
- At `if _column(conn, "settings", "lossless_enabled") is None:`:
  Default 0: compressed is what Telegram does to everyone by default
  and what almost everyone wants, since it is the form that plays
  inline. Lossless is opt-in, per user, and remembered.
- At `if _column(conn, "settings", "large_files_enabled") is None:`:
  Default 0: a download over the free ceiling costs credit, and
  nobody spends credit without having said yes (large_files.py).
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
  One row per download this bot actually started for somebody.

  In the database rather than in memory, which is the opposite choice
  to the per-minute update ceiling in shared_features.py, and for the
  opposite reason: that one is defending the process against a burst,
  so forgetting it on a redeploy costs nothing. This one is a share
  of a day, and a limit that resets with every release is not a limit -- it would hand the heaviest user a fresh allowance on
  every deploy, which is several a day when anything is being worked
  on.
- At `conn.execute(`:
  Which download provider is currently working, and which is not.

  Kept in the database rather than in memory alone so a redeploy does
  not go back to hammering a service that has been dead for a week --
  a container that restarts with every release would otherwise
  re-learn the same lesson from scratch, at a user's expense, several
  times a day. See resolvers.py.
- At `old = _column(conn, "sent_files", "post_key")`:
  Telegram's file ids for what this bot has sent, so the same post can be
  sent again without being downloaded again (sent_files.py has the whole
  argument). The row holds nothing about people: the key is a keyed hash of
  the post, not the post and not the person; the day is a day, not a moment.

  2.2.0 keeps it compact (sent_files.py says how and why): a 16-byte key,
  the ids as bytes, room on each page for the daily "used" mark. A table
  from 2.1.x has a hex text key and JSON ids, and other keys (2.2.0 names a
  post by its identity, STORE_VERSION 2), so none of its rows could be
  found again: it is a cache, and it is dropped and made anew -- what it
  held costs one more download of each post, once.
- At `conn.execute(`:
  A group chat's settings (groups.py), as JSON so a setting added later
  needs no new column. Keyed by the group's chat id; nothing about who
  is in it or who changed what.

_At `def get_group_settings(chat_id: int):`:_

---------- group chats (groups.py) ----------

### `get_group_settings()`

The stored settings of a group, as stored (groups.normalize makes them whole),
or None for a group that never changed any.

### `forget_group()`

The bot has left the group, or was removed: nothing of it is kept.

### `channel_ids()`

The channels the bot is in (channels.py: their rows here are marked kind "channel"), the
most recently changed first.

### `move_group_settings()`

The group became a supergroup, under a new id: its settings go with it.

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
settings has a row per person who has ever picked a setting and is never
pruned, while activity_events reaches people who only ever used the bot
without changing anything -- but is pruned at ACTIVITY_RETENTION_DAYS.
Together they are "everyone the bot still knows about", which is the honest
scope of a broadcast.

### `get_caption_enabled()`

- `return bool(row[0]) if row else True` — default: caption ON

### `get_lossless_enabled()`

Whether to send downloads as files rather than as photos/videos.

Defaults to False for everyone who has never said otherwise: a
compressed video plays in the chat, and a file has to be downloaded
first, so the quality is only worth having when it was asked for.

### `get_large_files_enabled()`

Whether this person has switched on downloads over the free ceiling,
which cost credit. Off for everyone who has never said otherwise.

### `get_user_language()`

None means the user hasn't picked a language yet (no row, or a row
with caption prefs but no language set).

_At `def record_star_invoice(`:_

---------- Telegram Stars ledger (this bot's /donate only) ----------

### `record_star_invoice()`

amount_stars is in the currency's smallest unit for fiat currencies
(see shared_features.py's FIAT_CURRENCIES), or a plain Stars count for
the default currency="XTR".

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

_At `DOWNLOADS_PER_HOUR = int(os.environ.get("DBOT_DOWNLOADS_PER_HOUR", "12"))`:_

---------- how much of the day one person may have ----------
This is the one bot in the family whose work costs real money and real
time: a fetch out to a site that would rather not be fetched from, an
ffmpeg mux, and an upload back through Telegram. MAX_CONCURRENT_DOWNLOADS
caps how many run at once, which protects the container -- but two slots
shared by everybody is not the same thing as a fair share of them, and one
person pasting a playlist of links holds both for as long as they keep
pasting. Everyone else waits behind them, and the bot looks broken to
people who did nothing wrong.

So: a rolling hour and a rolling day, per person, counted in the database.
The numbers are set where somebody using the bot the way it is meant to be
used never meets them -- the ceiling is for the loop, not for the
enthusiast -- and they are rolling windows rather than a reset at midnight,
because a reset at midnight is an invitation to queue up against it.

Donors get a larger share. Not a different bot and not a queue-jump: the
same bot with the ceiling moved, which is the honest version of paying for
something whose cost is a fetch and an encode. A single donation of any
size counts, forever -- billing anybody monthly for this would cost more to
run than it collects.

### `download_allowance()`

What this person has left, and when the next one frees up.

One query for all of it: how many downloads in the last hour, how many in
the last day, when the oldest of each window falls out, and whether this
person has ever donated. Called once per link, on a database that is a
long way from the container, so it is one round trip rather than four.

```text
Returns a dict with:
  allowed    -- may they start one now
  scope      -- "hour" or "day", which ceiling stopped them
  wait_min   -- whole minutes until the next one frees up
  hour/day   -- how many they have used
  hour_max/day_max -- their ceilings, donor multiplier already applied
  donor      -- whether the larger ceiling is in force
```

- At `if day_max and state["day"] >= day_max:`:
  The day is checked first: being told "about 40 minutes" when the real
  answer is nine hours is worse than not being told at all.

### `record_download()`

Counted when a download is STARTED, not when it succeeds. A fetch that
fails still cost the fetch, and counting only successes would make a
string of failures free -- which is the cheapest way to keep the bot
busy.

_At `ACTIVITY_RETENTION_DAYS = int(os.environ.get("ACTIVITY_RETENTION_DAYS", "90"))`:_

---------- housekeeping ----------
activity_events is append-only and powers nothing older than the retention
window below (/status counts the last hour and since-start, ManagerBot's
/users the last N hours). Left alone it is the one table in this schema that
grows without limit, which on a metered database is a bill that only ever
goes up. family_link.py's housekeeping job calls this.

_At `def load_provider_health() -> list[dict]:`:_

---------- download provider health (resolvers.py) ----------

### `load_provider_health()`

Everything known at startup. Returns plain dicts so resolvers.py does
not have to know what a psycopg row looks like.

### `save_provider_health()`

Upsert the counters that changed since the last flush.

One statement for the whole batch, on a timer, rather than a write per
download -- the same bargain activity_events makes, and for the same
reason: this is advice about which provider to try first, not user data,
and it is not worth a round trip each time somebody pastes a link.

_At `def _days_from_env(name: str, default: int) -> int:`:_

---------- Telegram file ids of what was sent (sent_files.py) ----------

### `_days_from_env()`

A whole number of days, `default` when it is unset, unreadable or under 1:
a typo here must not stop the bot starting, and 0 must not delete every row.

_At `SENT_FILES_KEEP_DAYS = _days_from_env("DBOT_FILEID_KEEP_DAYS", 90)`:_

Rows nobody has used for this long are deleted by prune_old_data.

### `sent_files_get()`

(items bytes, confirmed_at, last_used) of this post's row, or None. `post_key`
is the UUID sent_files.post_key gives. A read: any error is the caller's to treat
as "no row".

### `sent_files_put()`

"Today" for the file-id store is the UTC date on both sides: sent_files.py compares what it reads
with its own UTC clock, while the database's own date follows the session's time zone (UTC on
Supabase, the laptop's zone on a local Postgres), so around midnight a row stored today could
read as yesterday's, or the other way round.

Store a delivery: the ids, seen public now, used today. Replaces what was there.

### `sent_files_confirmed()`

The post was seen public just now (a lookup found it), and the row was used.

### `sent_files_used()`

The row was used today. confirmed_at stays: only a fetch or a lookup proves
that the post is still public. A row already marked today is left alone: in
Postgres an UPDATE that changes nothing still writes a new version of the row,
which for a post sent to many people a day was one dead row per delivery.

### `prune_old_data()`

Returns how many rows were removed. Safe to run at any time.

- At `cur = conn.execute(`:
  The quota only ever looks back a day; anything older is history
  nobody reads. Kept DOWNLOAD_RETENTION_DAYS (2 by default), which is
  what /privacy and LEGAL.md promise.
- At `cur = conn.execute(`:
  File ids nobody has used for DBOT_FILEID_KEEP_DAYS. A row that is used is
  touched at most once a day (a date, not a moment), so "unused for N days"
  is exact to the day.

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

download_events goes with the rest. It is the daily allowance's memory,
so erasing it hands this person a fresh allowance -- which is the right
trade: a per-user counter that survives the user asking to be forgotten
is a record of their behaviour, and the allowance exists to stop one
person costing the bot a fortune in a day, not to punish anybody across
weeks.

_At `def dump_database_csv_zip() -> bytes:`:_

---------- admin: full database export ----------

### `dump_database_csv_zip()`

Exports every table in this bot's own schema to one CSV per table,
zipped together -- this bot's data only, never a sibling's. Deliberately not pg_dump-based -- that binary
isn't guaranteed to exist wherever this bot ends up hosted, while this
only needs the psycopg connection already used everywhere else here.
