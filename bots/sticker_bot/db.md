# `db.py`

[↑ sticker_bot](README.md) · [code](db.py)

Postgres layer for StickerBot -- these tables are this bot's
alone. The family shares one Postgres database now, but each bot gets its
own schema in it (DB_SCHEMA below), and no bot reads or writes another's
tables; the only shared tables are `family.*`, which family_link.py owns.

```text
Owns:
- per-user setting: chosen UI language (English/Uzbek/Russian)
- which sticker sets belong to which user (for /mypacks, /addsticker picker)
- co-editing: a share-link token per pack (with how many editors it lets in
  and until when), who has been granted add access to a pack, and the
  @usernames its creator invited who have not used the bot yet
- who created each pack (display name snapshot, for /whomade)
- a Telegram Stars ledger + donation-reminder cooldown, for this bot's own
  /donate flow (see shared_features.py)
- group chats (2.2.0, group_mode.py): whether each pack is public (shown and
  open to add to in the groups its creator or a co-editor uses the bot in) or
  private (the default), and which people have used the bot in which group,
  for ACTIVITY_RETENTION_DAYS
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
- At `if _column(conn, "packs", "public") is None:`:
  2.2.0: private (the default, and every pack made before) or public in groups.
- At `conn.execute(`:
  Who has used the bot in which group chat, so a group's /packs can list
  the public packs of its own members (the Bot API cannot list a
  group's members). Pruned with activity_events; erased with the person.
- At `if _column(conn, "pack_share_tokens", "max_editors") is None:`:
  2.2.0 (2026-10-28: a link may be shared, or leaked, to too large a group): a link lets in at most max_editors co-editors in all, and works for
  valid_days from when it was made (NULL: no limit). A link made before this
  gets the defaults from the moment this runs: 10 editors, a week.
- At `if _column(conn, "pack_share_tokens", "editors_delete") is None:`:
  Whether the pack's co-editors may delete stickers (2026-10-28: the creator picks whether the link grants delete rights). Off for every link, old ones included: until the creator says otherwise, only the creator deletes.
- At `conn.execute(`:
  People the creator invited by @username who have not used the bot since:
  a keyed hash to find them by, and the name sealed for the creator's list.
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
  Nullable, no default -- NULL means "hasn't picked a language yet",
  which is what gates the first-run picker in bot.py's /start.

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

None means the user hasn't picked a language yet (no row, or a row
with no language set).

### `get_user_packs()`

Returns list of (name, title) for a user's own packs, newest first.

Only returns packs this user_id \*owns\* -- packs they can co-edit via a
share link don't show up here, since editing there happens entirely
through the /start deep link instead of this picker.

### `set_pack_titles()`

Several at once, in one round trip: the titles /mypacks found changed on
Telegram (see bot.\_refresh_pack_titles).

_At `def add_editor(pack_name: str, user_id: int) -> None:`:_

---------- co-editing ----------

### `add_editor()`

Records that user_id has been granted add-only access to pack_name
(they opened a valid co-edit link). Idempotent.

_At `MAX_EDITORS_CHOICES = (3, 5, 10, 20, 50)`:_

The choices the co-edit screen cycles through.

_At `INVITE_KEEP_DAYS = 30`:_

An invitation nobody has used is dropped after this many days.

### `get_coedit_view()`

Everything the co-edit screen renders, on one connection, making the
pack's link (with the defaults) if it has none: {"token", "title",
"pack_id", "editors", "invited", "max_editors", "valid_days", "expires_at",
"editors_delete"}.

### `reset_share_token()`

Generates a fresh token for the pack, invalidating the old link, valid
for the pack's period again from now. Does NOT remove already-granted
editors -- it only stops the \*old\* link from granting new access.

### `set_link_valid_days()`

The link works for `days` from now (None: no limit).

### `set_editors_delete()`

Whether the pack's co-editors -- through the link or invited -- may delete stickers.

### `join_by_token()`

Someone opened a co-edit link: (outcome, pack name). Outcomes: "invalid" (no such
link), "gone" (its pack is not known any more), "own" (the creator's own link), "already"
(an editor already), "expired", "full" (editors and pending invitations at the link's
limit), "joined". One transaction, the link's row locked, so two people opening it at
once cannot both take its last place.

### `pack_name_by_id()`

The co-edit screen's buttons carry the pack's number, not its name: a name can be
48 characters, and Telegram allows a button 64.

### `shared_packs()`

(name, title) of the packs this person co-edits, by title.

### `invites_usable()`

Invitations hold a name, so they need the data key.

### `add_invites()`

Invite these @usernames (already checked to look like usernames) to co-edit:
(invited, refused for the pack being at its limit). Editors and pending invitations
together stay within the link's max_editors. Refuses without the data key: the
names are kept sealed, or not at all.

- At `"INSERT INTO pack_invites (pack_name, username_mark, username_sealed, added_at) "`:
  clock_timestamp(), not now(): every row of one message would otherwise
  carry the same time, and the creator's list is in the order they were typed.

### `pack_invites()`

(mark, @username) of the pack's pending invitations, oldest first.

### `claim_invites()`

This person has used the bot: every pack that invited their @username makes them a
co-editor now, and the invitation goes. Returns (name, title) of those packs. A pack
they own is not joined, only its invitation dropped.

### `delete_pack_records()`

Wipes all local traces of a pack (creator record, co-editors, share token). Used when the pack itself gets deleted on Telegram's side --
e.g. because its last sticker was removed and Telegram auto-deletes
sets that reach zero stickers -- so it doesn't linger as a dead entry
in /mypacks.

_At `def set_pack_public(name: str, public: bool) -> None:`:_

---------- public packs and group chats (group_mode.py) ----------

### `pack_access()`

Whether user_id may add to (or retag stickers in) pack `name`, and delete
stickers from it, as {"owner_id", "title", "allowed", "can_delete"}; None for
a pack this bot does not know.

Allowed: its creator and its co-editors, anywhere. In a group chat
(`chat_id`), also anybody, when the pack is public and its creator or one of
its co-editors has used the bot in that group. Deleting: its creator, and its
co-editors when the creator allows it (set_editors_delete) -- never somebody
let in by a public pack, and in a group nobody at all (group_mode). One
round trip.

### `group_packs()`

(name, title) of the public packs whose creator or a co-editor has used
the bot in this group, by title.

### `forget_group()`

The bot left the group, or was removed from it.

### `move_group()`

The group became a supergroup, under a new id: its members are noted there.

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

The sticker packs themselves are not the bot's to delete. They live on
Telegram's servers under this bot's name, and dropping the rows here
makes the bot forget them -- it stops listing them and can no longer add
to them -- while every copy anyone installed keeps working. Removing a
pack for real is done from Telegram, by its creator.

- At `cur = conn.execute("DELETE FROM pack_editors WHERE user_id = %s", (user_id,))`:
  Their seat on somebody else's pack, which is theirs to give up and
  takes nothing of the creator's with it.

_At `def dump_database_csv_zip() -> bytes:`:_

---------- admin: full database export ----------

### `dump_database_csv_zip()`

Exports every table in this bot's own schema to one CSV per table,
zipped together -- this bot's data only, never a sibling's. Deliberately not pg_dump-based -- that binary
isn't guaranteed to exist wherever this bot ends up hosted, while this
only needs the psycopg connection already used everywhere else here.
