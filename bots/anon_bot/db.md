# `db.py`

[↑ anon_bot](README.md) · [code](db.py)

Postgres layer for AnonBot -- these tables are this bot's
alone. The family shares one Postgres database now, but each bot gets its
own schema in it (DB_SCHEMA below), and no bot reads or writes another's
tables; the only shared tables are `family.*`, which family_link.py owns.

Owns the anonymous-inbox links, conversations,
and reply-routing (which inbox holder each link belongs to, which conversation a follower's next
message belongs to, which inbox-chat message maps to which conversation for
reply-swipes, and who's blocked whom), plus a Telegram Stars ledger +
donation-reminder cooldown for this bot's own /donate flow.

Postgres was chosen (over SQLite) so this survives an ephemeral cloud
container redeploy -- see DEPLOY.md.

Who talks to whom is not stored in the clear (2.1.1). Every column below that
used to hold a Telegram id holds `vault.pseudonym(id)` instead, so queries
keep their shape -- equality, joins, ORDER BY, ON CONFLICT, COUNT(DISTINCT) --
while a copy of this database cannot say which accounts are in a conversation.
The few ids this bot has to send TO are also kept sealed beside their
pseudonym (owner_sealed, follower_sealed, user_sealed) and opened here with
`expected=` that pseudonym, so a sealed id moved onto another row is refused.
Every public function still takes and returns real Telegram ids: the
translation happens in this file and nowhere else. See shared/security/vault.py.

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

_At `PAYER_DOMAIN = "anon.payer"`:_

The payer in star_transactions is stored as vault.pseudonym_in(uid, PAYER_DOMAIN),
not as pseudonym(uid): the family's star_ledger holds the same payment (real
id, bot, stars, time), and a row that matched it would hand over a payer's
pseudonym and so the conversations filed under it. Nothing reads the real id
back from this column -- the one reader asks "has this person paid", by
equality -- so there is no sealed copy of it.

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

### `_open_id()`

The real Telegram id sealed in `blob`, or None.

`expected` is the pseudonym the same row stores: a sealed id moved onto
another row does not match it and is refused, exactly as one that does not
open at all. Either way the row is skipped by the caller and the warning
names the table and the row, never the value.

### `init_db()`

- At `ensure_schema(dsn)`:
  Deliberately on a plain connection rather than the pool: the offline
  tools (db_merge.py, migrate_to_shared_db.py) call this against a
  \*different\* database than the one this process serves, and the pool
  is bound to DATABASE_URL. It runs once, so there is nothing to save.
- At `conn.execute(`:
  user_id is vault.pseudonym(id) and the username is sealed
  (purpose stars.username); a database made before 2.1.1 still has a
  plain `username` column until convert_personal_data_v1 drops it, and
  nothing here ever drops or rewrites it (see below).
- At `amount = _column(conn, "star_transactions", "amount_stars")`:
  Both safe to re-run on an existing table -- widens amount_stars
  since some fiat currencies' minor-unit amounts can exceed a plain
  INTEGER's range, and adds currency for bots migrating from
  Stars-only donations (see shared_features.py's FIAT_CURRENCIES).
  Each runs only when it is needed (see \_column).
- At `if _column(conn, "star_transactions", "username_sealed") is None:`:
  2.1.1: the sealed username (purpose stars.username). Added, never
  backfilled here: a table made before 2.1.1 still has its plain
  `username` until convert_personal_data_v1 seals and drops it.
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
- At `if _column(conn, "user_settings", "user_sealed") is None:`:
  2.1.1: user_id is a pseudonym, and the real id is sealed beside it
  (purpose anon.user) because list_all_users / active_user_ids_since
  have to hand real ids back -- the family's broadcast sends to them.
- At `if _column(conn, "user_settings", "from_activity") is None:`:
  2.1.2: whether the row exists only because the person was active
  (record_activity_batch, or the conversion's backfill), as against
  one they made by choosing a language. prune_old_data removes the
  first kind once their activity has aged out, so the broadcast
  reaches the same people as in 2.1.0 (settings UNION activity, the
  activity pruned at ACTIVITY_RETENTION_DAYS). Every row that exists
  before this column does gets FALSE, which is never pruned.
- At `knows = _column(conn, "user_settings", "knows_reactions")`:
  Whether this person has been told that reactions now reach the other
  side of a conversation (2.1.0). It was asked to be a check box in the database, so each current user sees this update note only once.

  The two statements are in this order on purpose. Added without a
  default, the column is NULL on every row that already exists -- the
  current users, who are owed the note. The default added after it is
  TRUE, so everybody who arrives from now on starts out knowing: they
  meet reactions in /help, and a note that reactions now work means nothing to somebody who was never unable to. Each runs only when it is needed
  (the default is read back from the catalog as 'true'), so a second
  start takes neither of the table locks: see \_column.
- At `if _column(conn, "anon_links", "owner_sealed") is None:`:
  2.1.1: owner_user_id is a pseudonym; the real id is sealed
  (purpose anon.link.owner) because get_link_view must say whom to
  deliver to.
- At `if _column(conn, "anon_conversations", "owner_sealed") is None:`:
  2.1.1: both identity columns are pseudonyms and the real ids are
  sealed beside them (purposes anon.conversation.owner / .follower):
  bot.py sends to them. Added only; never backfilled here -- that is
  convert_personal_data_v1, which has the plain ids to seal.
- At `if _column(conn, "anon_conversations", "follower_first_msg_at") is None:`:
  Set when the guest sends their FIRST message in a conversation.
  That one message is the only one either side may send without
  saying what it answers -- it is the one that opens the thread, and
  there is genuinely nothing above it. See handle_message.
- At `if _column(conn, "anon_conversations", "owner_replied_at") is not None:`:
  Replaced by the column above. It asked "has the inbox holder answered yet?", which exempted every message a guest sent to an inbox holder who never answered -- so a guest's whole side of a one-way conversation
  was still being routed by session state rather than by what it
  replied to. Dropped only where it is still there (see \_column).
- At `if _column(conn, "anon_conversations", "archived_at") is None:`:
  Filed away, either by the inbox holder from /conversations or by
  prune_old_data once a conversation has been silent long enough for
  its reply anchors to be removed. NULL means open.

  It is filing and nothing else: no message is ever refused because
  of it, and the guest is never told. A conversation the inbox holder has archived un-archives itself the moment anything is relayed into it,
  because a message arriving means it was not finished after all.
- At `conn.execute(`:
  What /conversations reads: one inbox's threads, newest activity
  first. Without it that is a sequential scan of every conversation
  in the table on every page turn.
- At `conn.execute(`:
  Natural key for a conversation -- also what scripts/db_merge.py
  matches on when reconciling a local DB with a cloud one.
- At `if _column(conn, "anon_relay", "is_prompt") is None:`:
  True for the Reply button's ForceReply prompt, and only for that.
  The prompt is scaffolding: it exists to pin the reply box to one
  conversation, and once the reply it asked for has arrived it has
  nothing left to say. Knowing which rows are prompts is what lets
  bot.py take them back down again -- and it has to be knowable from
  the database rather than from user_data, because the tap and the
  reply can land in different processes.
  Vestigial since v1.4.0: it marked the Reply button's ForceReply
  prompt, and there are no prompts. Kept, and kept being created, so
  that a fresh database has the same shape as the live one -- nothing
  reads it, nothing writes it, and its DEFAULT fills it in.
- At `if _column(conn, "anon_relay", "peer_message_id") is None:`:
  The message in the OTHER chat that this one is the same message as
  -- the copy that was delivered from it, or the original it was
  copied from. A conversation exists twice, once in each chat, and
  message ids are counted per chat, so nothing else in this schema
  says which bubble here is which bubble there.

  It is what makes a reply to an OLDER message arrive on the right
  bubble. Without it the only anchor available was the newest one in
  the receiving chat, so answering the third message from the top
  showed up on the other side as an answer to the most recent thing
  said -- see record_relay_and_touch.

  Nullable, and NULL is not backfillable: the pairing is only
  knowable at the moment of relaying, and the two chats hold
  different numbers of bubbles for the same conversation (a header
  line, an album, a message too long for one) so it cannot be
  reconstructed by lining up what is already there. A row from before
  this column falls back to the old behaviour rather than guessing.
- At `conn.execute(`:
  "The first / newest message of THIS conversation in THIS chat" --
  asked by get_routing_context on every un-replied message, and by
  the bookmark /conversations and /which send. The primary key is
  (chat_id, message_id), which answers neither without walking every
  row the chat has.
- At `conn.execute(`:
  Which one-time announcements this person has been shown (2.2.0: the
  220 gift's ad, see bot.py's promo_ad_note). A row is written before
  the announcement is sent, so a person gets one chance at it and never
  a second. /deletemydata removes the rows. user_id holds the person's
  pseudonym, like every identity column here, and there is no time: a
  database made by 2.2.0 had a seen_at, which the conversion drops.

_At `def record_activity_batch(user_ids) -> None:`:_

---------- /status active-user tracking ----------

### `record_activity_batch()`

One row per user per flush window -- see shared_features.py's
track_activity, which buffers them. Sent as a single statement whatever
the batch size: both readers of this table are COUNT(DISTINCT user_id)
over a time window, so nothing depends on a row per update.

The column holds each person's pseudonym. Nothing in this table can be
turned back into an id: active_user_ids_since finds the real ones through
user_settings.

So that they can be found, every person recorded here also gets a
user_settings row carrying user_sealed (ON CONFLICT DO NOTHING; a row
that somehow lacks the seal is filled in). Someone who used the bot
without ever picking a setting is thereby still reachable by a
broadcast, exactly as the activity table made them in 2.1.0. The row
has no language, which get_user_language reads the same as no row, and is
marked from_activity: prune_old_data takes it away again once the
person's activity has aged out, as 2.1.0's reach ended with it.

### `count_active_users_since()`

- At `with pooled_read() as conn:`:
  Distinct pseudonyms are distinct people, so the count is the same.

### `active_user_ids_since()`

Everyone with activity since `since`, as real Telegram ids. Used by the
family bus for an aimed broadcast -- see BROADCAST_ACTIVE_DAYS in
family_link.py, which sends to every id returned.

activity_events only knows pseudonyms, so the ids come out of
user_settings.user_sealed. record_activity_batch gives everyone it records
a settings row, and the conversion gave one to everyone already in the
table, so nobody is left out; a row without a seal (not expected) cannot
be turned back into an id, is counted in a log line and skipped.

### `list_all_users()`

Everyone this bot could send an unprompted message to, as real ids.

In 2.1.0 this was user_settings UNION activity_events. A pseudonym in
activity_events cannot be turned back into an id, so the settings table
must hold everyone: record_activity_batch adds a row for every person it
records, and the conversion added one for every person who had activity
but no row. Rows added that way (from_activity) go again, in
prune_old_data, once the person's activity is past ACTIVITY_RETENTION_DAYS,
which is when 2.1.0's table union stopped reaching them as well. So the
settings table alone returns the same set of people.
The family's broadcast sends to every id returned, so each comes from
user_sealed, opened against its row's pseudonym; a row that does not open
is skipped with a warning.

### `get_user_language()`

None means the user hasn't picked a language yet (no row, or a row
with no language set).

### `set_user_language()`

- At `with pooled() as conn:`:
  user_sealed is written on every upsert, so a row is never left without
  the means to turn it back into an id (list_all_users needs it). An
  explicit choice also ends the row's life as an activity-only one: it is
  never pruned (see from_activity).

### `claim_reactions_note()`

True exactly once for a person who has not been told about reactions,
and marks them told in the same statement -- so two updates arriving
together cannot both send the note. False for everybody else, including
somebody with no settings row at all (nobody to have been told before).

### `claim_promo_seen()`

True exactly once per (person, announcement): this call recorded that
they are being shown it, and two updates arriving together cannot both win.
False if they were shown it before. Blocking; raises on a database error.

### `get_reaction_route()`

Where a reaction on this bubble should go: its twin in the other chat,
both sides of the conversation, and whether the guest is blocked. One
query, on the path of every reaction -- the database is on another
continent and the three questions are one join.

None for a message that is in no conversation (the bot's own notices, a
bookmark) and for a row from before twins were recorded
(peer_message_id NULL), neither of which has anywhere to send a reaction.
Also None when the conversation's sealed ids do not open or do not belong
to the row -- there is then nobody safe to send it to.

_At `def record_star_invoice(`:_

---------- Telegram Stars ledger (this bot's /donate only) ----------

### `record_star_invoice()`

amount_stars is in the currency's smallest unit for fiat currencies
(see shared_features.py's FIAT_CURRENCIES), or a plain Stars count for
the default currency="XTR".

The payer is stored under their own stand-in for this table
(vault.pseudonym_in(uid, PAYER_DOMAIN)), never under pseudonym(): the
family's star_ledger keeps the real id beside the same amount and time, so
a plain pseudonym here could be lined up with it and name the payer's
pairings. The username is sealed (stars.username): a copy of this table
says neither who paid nor under what name.

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

_At `def get_or_create_anon_link(owner_user_id: int) -> tuple[str, bool]:`:_

---------- AnonBot: anonymous-inbox links, conversations, reply routing, blocks ----------

### `get_or_create_anon_link()`

(token, is_paused) for this inbox holder, creating the row on first use. The
token never changes on its own -- only regenerate_anon_link() rolls it.

One statement: an upsert that does nothing on conflict still RETURNINGs
nothing, so the read comes first and the insert only runs for a genuinely
new inbox holder. /link used to call this and then get_anon_link_state, which
was two round trips for two columns of the same row.

### `regenerate_anon_link()`

Fresh token -- invalidates the old link for NEW conversations.
Conversations already in progress keep working since they're routed by
conversation_id, not by the token.

Returns (token, is_paused). The paused flag is deliberately left alone --
a new link on a paused inbox is still paused -- and returned so the
caller can redraw the screen without a second query for it.

### `get_link_view()`

Everything the "someone tapped a link" path needs to decide what to
do: (inbox holder id or None, is the inbox paused, has this follower been
blocked). One query where there used to be three, on a path that runs
every time anyone opens anyone's inbox link.

The inbox holder comes out of owner_sealed, opened against the row's own
pseudonym. A link whose sealed holder does not open, or belongs to another
row, is treated as no link at all: nobody is sent to an id the row does
not vouch for.

### `get_delivery_context()`

(is this follower blocked, does the inbox still exist, the inbox holder's language). One query on the hot path of every anonymous message -- it was
three, and the third of them was a whole extra connection just to look up
which language to write the header in.

### `start_new_anon_conversation()`

Always creates a brand-new conversation row -- this is what makes
tapping the link again 'the start of a new conversation' -- and points
the follower's active-routing state at it, so their very next un-replied
message lands here instead of wherever they left off before.

Both people are stored as pseudonyms, with their real ids sealed beside
them on the conversation row -- the only place the bot can read them
back from (anon_follower_state keeps pseudonyms alone).

- At `cur = conn.execute(`:
  Numbered per inbox holder, not per (holder, follower) pair. The number is
  the only thing either side is ever told about a thread, and per-pair
  numbering restarted it at 1 for every new person -- so three
  different strangers all arrived in the inbox holder's chat as
  "Conversation #1", and /blocked listed two separate guests both
  described as "#1". Existing rows are left exactly as they are: the inbox holder has already read those numbers in their own chat and
  renumbering would rewrite what they saw. Because this takes the inbox holder's highest number rather than the pair's, every number issued
  from here on is above all of them, so no NEW collision can appear.

### `get_active_conversation_for_follower()`

Where a follower's next un-prompted message should be routed --
'active' meaning 'the conversation their most recent /start link tap
pointed at', see start_new_anon_conversation().

- At `owner_user_id = _open_id("anon_conversations", conversation_id, conv_row[4],`:
  The state row keeps only a pseudonym; the real inbox holder is the one the conversation sealed, and it must be the holder this state row names.

### `get_conversation()`

The conversation, with the REAL inbox holder and follower ids -- bot.py sends
to them and compares them with the sender. Both come out of their sealed
columns, each opened against its own pseudonym on this row; a row whose
seals do not open (or were moved here from another row) is None, as if
the conversation did not exist.

### `record_anon_relays()`

Remembers 'these messages, in this chat, belong to this conversation'
so a later reply can be traced back to the right thread even with many
conversations interleaved in the same chat.

Takes a list because a media message with a header goes out as two, and
both are valid reply anchors; one statement covers however many.

There used to be an `is_prompt` flag here, marking the Reply button's
ForceReply prompt as the one kind of row meant to be deleted again once
answered. There are no prompts, so nothing passed it and nothing read the
column. The column itself stays where it is: dropping one from a live
table to reclaim a bit per row is not a trade worth making, and it keeps
every deployment's schema identical.

### `forget_anon_relay()`

Drop one relay row, so nothing is left pointing at a message that no
longer exists.

### `record_relay_and_touch()`

Everything one relayed message leaves behind, in one statement.

Both chats get rows, and they get them together. Which conversation a
reply belongs to is read out of anon_relay, so a message with no row is
a message neither side can answer -- and the anchors are what make that
reply quote the right bubble. A data-modifying CTE puts the insert and
the anchor update in a single round trip, which matters here rather than
generally: the database is on another continent from the container, so
each extra statement on the hot path of every message is a real fraction
of a second added to how long "Sent" takes to appear.

Either id list may be empty; the arrays simply contribute no rows.

The guest's own messages are in here too, which they were not before.
Without them the guest's side of the chat was only half-tracked: editing
something they had written got no answer at all, while the inbox holder editing a reply was told the edit had not travelled, and a guest quoting their
own earlier message was told it matched no conversation.

Every row also records its counterpart in the other chat, and the two
anchor arguments are already exactly that: `last_follower_msg_id` is the
delivered end of this relay on the follower's side, so it is what each
inbox-side row of the same relay corresponds to, and the other way
round. One relay can be several bubbles on one side and one on the
other -- a header line above an uncaptioned sticker, a message too long
for one bubble, an album -- and every bubble of it points at the single
bubble the other side would quote, which is the last one sent there.
That is the same id the `last_*` anchors carry, so the two agree by
construction rather than by being kept in step.

What it buys is a reply to an OLDER message landing on the right bubble.
The anchors alone can only say "the newest thing in that chat", so
answering the third message from the top arrived on the other side as an
answer to the most recent one -- confusing, and confusing in the one
direction that matters, since a conversation held out of order is
exactly what a threaded inbox is for.

Clearing `archived_at` here is the whole of un-archiving. Filing a
conversation away says "this one is finished", and a message landing
in it says otherwise -- from either side, and without either of them
having to know the flag exists.

### `mark_follower_opened()`

Records that the guest has now sent their opening message, which is
what spends this conversation's one exemption from the reply rule.

Returns the conversation's opening line in the guest's chat (the smallest
relay id there, as in first_relay_message), so its Cancel button can come
off -- answered in the same round trip rather than a second one.

### `cancel_unopened_conversation()`

The Cancel button under the line a link tap opens with. Only a
conversation nothing was said in can be cancelled, and then it goes
entirely: the row, the opening line's relay row (so replying to the
"Cancelled" line sends nothing) and the routing state (so the next
un-replied message is not taken as an opening one). The inbox holder was never told about it, so there is nothing on their side to undo. Returns False
when something was already sent.

### `count_owner_conversations()`

How many conversations this user is the inbox holder of, counting only
the ones something was actually said in. A bare message from someone with
any at all is ambiguous -- it could be meant for any of them -- which is
exactly what the reply rule exists to resolve.

A link tap creates a row whether or not the guest ever writes, so counting
rows counted people who opened the link and left. That pushed an inbox holder into the ambiguous branch on the strength of conversations that had never
contained a word.

### `get_routing_context()`

Everything handle_message needs to route one un-replied message, in a
single round trip.

This used to be two queries and, once the opening-message exemption had to
start checking for other live threads, would have been three. The database
is a long way from the containers -- a bare SELECT 1 measures about a
second -- so three sequential round trips on the hot path of every message
is the most expensive thing this bot does. All three answers come from
different tables and none depends on another, so one statement does.

```text
Returns:
  active           -- the conversation an un-replied message would go to, or None
  owned            -- how many live conversations this user owns an inbox for
  newest_is_active -- whether the last thing this bot put in this chat
                      belongs to `active`, i.e. nothing from another
                      thread has arrived since it was opened
  active_anchor    -- the newest message id in this chat belonging to
                      `active`, so a refusal can be sent as a reply to the
                      very message it is asking the person to reply to
```

`newest_is_active` used to be `other_threads` -- "does this chat hold any
conversation at all besides the active one" -- which is true for every
returning guest and so refused the opening message of every inbox after
their first. Relay message ids climb within a chat, so ORDER BY
message_id DESC LIMIT 1 is the newest bot message in it, and asking
whether \*that\* belongs to the active conversation is the same safety
check made against what actually happened rather than against what has
ever happened.

- At `owner_id = _open_id("anon_conversations", conversation_id, row[9],`:
  The state row holds the inbox holder's pseudonym only; the real id is
  the one the conversation sealed, opened against that pseudonym.
- At `newest_is_active = newest is None or newest == conversation_id`:
  No relay rows at all means nothing has been said in this chat, so
  there is nothing the message could be answering either.

### `get_relay_row()`

This message's relay row: which conversation it belongs to, and which
message in the other chat it is the same message as.

Both in one read because the caller that needs the peer is
handle_message, on the hot path of every reply, and the peer is a column
on the row it was already fetching.

`peer_message_id` is None for a row that has no counterpart -- the line
announcing a new conversation, a /conversations bookmark -- and for
anything relayed before the column existed. Neither is an error: the
caller falls back to the conversation's newest anchor, which is what it
used before there was anything better.

The `is_prompt` column stays where it is. Nothing writes True to it now,
so it is uniformly False, and dropping a column from a live table to
reclaim one bit per row is not a trade worth making.

### `get_conversation_for_relay()`

Which conversation this message in this chat belongs to, if any.

For the callers that have no use for the peer -- /which, /archive, and
the answer to an edit -- all of which ask one question about one message
and then do something that has nothing to do with delivering anything.

### `clear_follower_state()`

Forgets where this follower's next un-replied message was going to be
routed, which is what /cancel does to an open ask session. The
conversation row itself is left alone -- it is a record of messages that
really were sent, and both sides can still reply into it by replying to
a message from it. Returns whether there was anything to forget.

Tapping the inbox link again starts a fresh conversation, exactly as it
did before; see start_new_anon_conversation.

### `list_anon_blocks_with_conversations()`

Blocked guests, each as (a conversation id to address them by, the
conversation numbers they used).

/blocked used to identify people by a hashed pseudonym. Conversation
numbers do the same job with something the inbox holder has actually seen in
their chat -- and unlike the pseudonym they carry no identity at all,
stable or otherwise. One query rather than one per blocked guest.

The first element used to be the guest's Telegram user id, which bot.py
then put in the unblock button's callback_data. Callback data is part of
the keyboard Telegram hands to the client, so anything able to read a
message's markup could read it -- and in a bot whose whole premise is
that the inbox holder never learns who is writing, that is the one field that
must not leave the server. A conversation id says the same thing to the
bot and nothing at all about the person.

_At `CONVERSATIONS_PER_PAGE = 6`:_

---------- the inbox holder's own view of the inbox (/conversations) ----------
Three states, and they do not overlap: open, archived, blocked. The header
of /conversations counts all three; the list itself has a tab for the first
two, and blocked threads belong to /blocked, which already lists them with
an Undo button under each.

"Open" means reachable, not "ever existed". A link tap nobody wrote into is
not a conversation anybody had, and a thread whose reply anchors
prune_old_data has removed has been archived by then -- so the number is
one the inbox holder can act on. /stats keeps the lifetime total.

_At `_LIVE_CONVERSATIONS = """`:_

A conversation only counts once somebody has said something in it, and a
blocked guest's thread is neither open nor archived. Both conditions are
needed by every query below, so they are written once.

### `count_owner_conversation_states()`

How many of this inbox's threads are open, archived and blocked.

### `list_owner_conversations()`

One page of the inbox's threads, whichever tab is open,
newest activity first -- which is the order an inbox is read in.

`archived` is a bound parameter compared against the column rather than
two branches of SQL text, so there is still exactly one statement here
and nothing is built by concatenation.

### `set_conversation_archived()`

File a conversation away, or bring it back. Returns whether there was
such a conversation and it was this inbox holder's to file.

The inbox holder's id is in the WHERE clause as well as in the handler's check.
The handler is the real gate; this is the one that still holds if a
future caller forgets to write one.

### `first_relay_message()`

The earliest message of this conversation still on record in this
chat, which is what "go to the start" scrolls to.

Message ids climb within a chat, so the smallest is the first. It is the
first \*still on record\*, which is not always the first there ever was:
prune_old_data removes a long-silent conversation's rows, and /cancel can
remove one. None means there is nothing left to point at, and the caller
says so rather than pointing somewhere else.

### `get_anon_stats()`

Counts only conversations something was actually said in -- a link tap
that never became a message is not a conversation anybody had.

_At `ACTIVITY_RETENTION_DAYS = int(os.environ.get("ACTIVITY_RETENTION_DAYS", "90"))`:_

---------- housekeeping ----------
Two tables here grow without limit if nobody prunes them: activity_events
(one row per active user per flush window) and anon_relay (one row per
relayed message, forever). anon_relay is what lets a swipe-reply on an old
message still resolve, so it is pruned on conversation activity rather than
on the row's own age -- a long-running conversation keeps all of its
anchors, a conversation nobody has touched in months keeps none.

Relay rows (which chat message belongs to which conversation) are kept only
while a reply to them can still be routed: a conversation idle for
RELAY_RETENTION_DAYS (30) is closed, and its rows go with it.

### `prune_old_data()`

Returns how many rows were removed. Safe to run at any time.

It also archives the conversations it has just taken the last reply
anchor from -- see the comment on that statement. Archiving is not a
removal, so it is not in the count.

- At `cur = conn.execute(`:
  A settings row that exists only because its person was active
  (from_activity) goes with the last of that activity; one they made
  by choosing a language never does. In the same pass as the events
  above, so a person whose last event has just aged out is caught now.
- At `cur = conn.execute(`:
  ...and the conversations those rows belonged to are archived, in
  the same pass and by the same rule.

  This is not tidiness. Every route into a conversation goes through
  a reply to a message that has a relay row, so a conversation with
  none left cannot be written into by either side -- it is closed,
  as a fact rather than as a decision. Leaving it counted as open
  would make "4 open" a lifetime total wearing the word "open",
  which is the number /conversations exists to stop being.

  Deliberately does not touch already-archived rows: archived_at is
  when the inbox holder filed it, and overwriting that with the night the
  pruner happened to run loses the only thing it says.

_At `def erase_user(user_id: int) -> int:`:_

---------- erasing one person, on request ----------
What /deletemydata reaches -- see shared_features.py, which owns the
command and the confirmation. Everything in this schema that is about one
person goes, in a single transaction, with one deliberate exception.

The Stars ledger keeps its rows. A donation is a payment, and a payment
record has to outlive the payer asking to be forgotten: it is what a refund
is issued against and what the totals are counted from. The sealed username
is cleared, since it is the one free-text identifier on the row; the payer
stays under its payer stand-in only (the family's own ledger keeps the numeric id a
refund is sent to). The privacy notice says so rather than implying the
erase is total.

### `erase_user()`

Returns how many rows were removed. Blocking; call through
asyncio.to_thread.

A conversation row is jointly held: it exists only to say that these two
people are talking, so it cannot be erased for one side and kept for the
other. Whichever side asks, the thread stops being routable and both
sides get a fresh conversation number if they ever meet again. What was
actually said is unaffected either way -- it was never stored here, it is
in the two Telegram chats, and each person can delete their own copy.

For an inbox holder this also invalidates their link. Every copy anyone
has posted stops working, which is the point of asking rather than the
cost of it, but it is not something to find out afterwards -- the
confirmation says so before the button is tapped.

Every table here is found by the person's pseudonym, which is what the
identity columns hold; the sealed copies of the id go with their rows.

- At `for statement, params in (`:
  Written out rather than looped, so that no part of a statement here
  is assembled at runtime. There are three of them; a loop that built
  the WHERE clause saved two lines and cost the guarantee that every
  SQL string in this file is visible in this file.

_At `class PersonalDataError(RuntimeError):`:_

---------- converting a database made before 2.1.1 ----------
The one-time step scripts/security/encrypt_personal_data.py runs, with the bots
stopped, on a database whose tables still hold plain Telegram ids and
usernames. Both functions below take the caller's connection and the schema
to work on and nothing else -- no module global, no pool -- run inside the
caller's transaction and never commit, so the script can convert every
schema, verify them all and stamp the key in ONE transaction, and roll the
whole lot back on any failure.

The statements are literals, written out one per table, because
tests/test_sql_safety.py allows no SQL built from names. The schema is not
interpolated either: it is quoted with psycopg.sql.Identifier and set as the
transaction's search_path, which every statement then resolves bare table
names through (exactly how the bot itself reaches its tables). The path is
the schema ALONE -- no `public` after it -- so a table missing from an old
schema is reported as missing instead of being found, and rewritten, in
another one. It is put back before returning.

### `PersonalDataError`

verify_personal_data_v1 found an id still in the clear, or a sealed
value that does not open or does not belong to its row. The message holds
table and column names and counts, never a value.

### `_in_schema()`

- At `pass`:
  The transaction is already aborted (and the caller will roll it
  back, which discards the setting with it); do not hide why.

### `_ready()`

True when the table and every one of `columns` exist. A schema made by
an older version (testbot's) may lack either; what is missing is noted in
`counts` as 'skipped:&lt;table>[.&lt;column>]' = 0 and logged, and the table is
left alone.

### `_plain()`

A real Telegram id: not NULL and not already a pseudonym.

### `_pseudonyms_of()`

(plain ids, their pseudonyms), two lists in the same order. With
`domain`, each is that domain's stand-in (vault.pseudonym_in) instead.

### `convert_personal_data_v1()`

Replaces every Telegram id in `schema`'s AnonBot tables with its
pseudonym, seals the ids and usernames the bot must read back, and drops
star_transactions.username -- all on `conn`, inside its transaction.

Per row, the pseudonym and the sealed copy of the id are written by ONE
UPDATE keyed by the row's primary key, so no row can be left with one and
not the other. A value that is NULL or already a pseudonym is skipped, so
a second run changes nothing and reports 0 everywhere. Columns the new
layout adds (the sealed ones) are added here if init_db has not yet.
A table or column the schema lacks is skipped and named in the result.

Returns {table: rows changed}, plus 'star_transactions.username_sealed'
(usernames sealed), 'star_transactions.username_dropped' (1 when the plain
column was removed) and 'skipped:&lt;what>' = 0 for each thing missing.

- At `plain, pseudo = _pseudonyms_of(`:
  One statement for the whole table: a relay row is one per
  message, and most of the table is the same few thousand chats.
- At `dropped = 0`:
  2.1.0 kept when each bubble was written and which side wrote it,
  for /export. The export is gone; neither column may outlive it.
- At `plain, pseudo = _pseudonyms_of(`:
  A few rows per person (one per announcement): one statement for
  the whole table, like anon_relay. 2.2.0 also kept when each
  announcement was shown; nothing reads it, so the column goes.
- At `plain, pseudo = _pseudonyms_of(`:
  Many rows per person and no index on the column: one pass that
  joins on the list of people, not one pass per person.
- At `added = conn.execute(`:
  Everyone with activity must stay reachable by a broadcast,
  which now reads only user_settings: give each person who
  has activity but no settings row one (the table's defaults
  for the other columns), while their id is still in the clear.
  Marked from_activity: they go again with their activity.
- At `plain, pseudo = _pseudonyms_of(`:
  The payer's stand-in is its own (PAYER_DOMAIN), not pseudonym():
  see the note at PAYER_DOMAIN. A value already in the pseudonym
  range is left as it is.

_At `_IDENTITY_CHECKS = (`:_

What verify_personal_data_v1 asks of every identity column: (rows in the
table, rows still holding a value that is not a pseudonym). The statements
are literals; looping over this constant is the shape tests/test_sql_safety
recognises as the bots' own.

_At `_PAYER_CLASH_CHECKS = (`:_

What no star_transactions.user_id may equal: a person's pseudonym() as any
identity column of the schema holds it. (A payer's row can be lined up with
the family's star_ledger; if it held pseudonym(), that match would name the
payer's conversations.) One literal statement per column; run for the ones
the schema has.

### `verify_personal_data_v1()`

Checks `schema` is fully converted, on `conn`, inside its transaction:
no identity column holds a non-NULL value that is not a pseudonym; no
star_transactions.user_id equals a pseudonym() value held by any identity
column of the schema (a payer is stored under their own stand-in, see
PAYER_DOMAIN); every sealed value opens under its purpose and, where its
row stores the pseudonym, matches it; and star_transactions has no
`username` column.

Returns {table: rows checked, 'table.column': sealed values that opened,
'skipped:&lt;what>': 0 for whatever the schema lacks}. Raises
PersonalDataError, naming tables, columns and counts only, if anything
fails.

#### `verify_personal_data_v1.sealed_column()`

The column is there. One missing from a table that has rows is
a failure: those rows have nothing sealed.

_At `def dump_database_csv_zip() -> bytes:`:_

---------- admin: full database export ----------

### `dump_database_csv_zip()`

Exports every table in this bot's own schema to one CSV per table,
zipped together -- this bot's data only, never a sibling's. Deliberately not pg_dump-based -- that binary
isn't guaranteed to exist wherever this bot ends up hosted, while this
only needs the psycopg connection already used everywhere else here.

### `get_edit_route()`

Where an edit or a deletion of a message goes (2.2.1): every copy the bot made of it in the other chat
(rows whose `peer_message_id` is this message), both sides, and whether the guest is blocked. Only the
other chat is searched, so a message's own row never counts as its copy.

### `forget_anon_relays()`

Drops the rows of messages /delete removed, so nothing points at a message that no longer exists.
