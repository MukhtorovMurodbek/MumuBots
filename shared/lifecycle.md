# `lifecycle.py`

[↑ shared](README.md) · [code](lifecycle.py)

Surviving a redeploy without anybody noticing.

Pushing an update to a bot on a platform like Railway means the running
container is replaced. Four separate things go wrong in that gap, and only
one of them is the obvious one:

```text
1. **Two bots, one token.** If the new container starts polling before the
   old one has stopped, Telegram answers one of them with 409 Conflict and
   both flap. `hold_the_lease()` below settles it with a lease row in the
   shared database: exactly one process at a time holds the right to poll,
   and the new one waits for the old one to hand it back (or for it to
   expire) rather than fighting it.
```

```text
2. **Everything the bot was in the middle of is forgotten.** user_data and
   every ConversationHandler state live in that process's memory and go
   with it. Someone who was three stickers into a pack sends the fourth
   and gets the unknown-command answer from a bot that, thirty seconds ago,
   knew exactly what they were doing. `PostgresPersistence` writes both
   into the bot's own schema, so the new process picks the conversation up
   mid-sentence.
```

```text
3. **Work in flight is killed.** An ffmpeg encode or a download that is
   running when the platform sends SIGTERM does not come back, and the
   user is left looking at "converting..." forever. `busy()` marks those
   stretches; when a stop signal arrives, everyone inside one is told, in
   their own chat, that an update interrupted them -- once, immediately,
   instead of never.
```

```text
4. **The operator gets paged for a deploy done on purpose.** ManagerBot
   decides a bot is down from a stale heartbeat, and a redeploy makes
   every heartbeat stale. `mark_expected_restart()` leaves a note in the
   shared database saying this one was deliberate.
```

What is \*not\* a problem, and does not need solving: updates sent while the
bot is between containers. Telegram queues undelivered updates for 24 hours
and hands them over on the next getUpdates, so as long as nothing calls
run_polling(drop_pending_updates=True), the gap costs those users latency
and nothing else.

Copied byte-identically into every bot, like shared_features.py,
family_link.py and live_message.py.

_At `ENABLED = os.environ.get("DEPLOY_SAFETY", "on").lower() not in ("off", "0", "false", "no")`:_

Everything here is opt-out, one env var, and every piece of it degrades to
"the way it worked before" rather than to a bot that will not start.

_At `LEASE_WAIT_SECONDS = float(os.environ.get("DEPLOY_LEASE_WAIT_SECONDS", "240"))`:_

How long a starting process waits for a previous one to let go of the poll
lease before it starts anyway.

Raised from 45s to 240s in v1.2.2R, when railway.json's overlapSeconds went
from 0 to 30. Those two numbers are one decision, not two.

overlapSeconds: 0 meant Railway stopped the old container before starting
the new one, so the bot was away for the whole of the new container's build
and boot -- a minute or three during which every message waited. Telegram
holds those updates and hands them over afterwards, so nothing was ever
lost; what it cost was a user typing into what looked like a dead bot.

With this lease in place, the old container can keep serving while the new
one builds and boots. The new one blocks HERE, in post_init -- before its
first getUpdates, before its first heartbeat -- until Railway stops the old
one and the old one hands the lease back. The gap becomes the drain alone,
which is seconds.

The wait therefore has to outlast the whole handover: the overlap window
(30s) plus a worst-case drain (drainingSeconds, 120s), with room to spare.
If it does not, the new container gives up and starts polling while the old
one is still going, and Telegram answers 409 Conflict and splits the
updates between them -- which is the exact failure overlapSeconds: 0
existed to prevent. Keep DEPLOY_LEASE_WAIT_SECONDS comfortably above
overlapSeconds + drainingSeconds if either is ever changed.

_At `STATE_TTL_HOURS = int(os.environ.get("DEPLOY_STATE_TTL_HOURS", "12"))`:_

State older than this is not loaded at startup, and is deleted when it is
noticed. Matches shared_features' USER_DATA_TTL_HOURS, which drops the same
state from memory for the same reason -- a conversation nobody has touched
for half a day is over, whatever the state machine still thinks.

_At `PERSIST_SECONDS = float(os.environ.get("DEPLOY_PERSIST_SECONDS", "180"))`:_

How often in-memory state is written out \*besides\* at shutdown. A clean
redeploy flushes on the way out and needs none of these; this is purely
insurance against the ways a process dies without warning (an OOM kill, a
host failure), where the alternative is losing everything since startup.
Every pass is one small upsert per user who has changed, so the cost of
making it more frequent is real but modest.

## 1. One poller at a time

Telegram allows exactly one getUpdates consumer per token. Two is not a
degraded mode, it is a coin flip per update: 409 Conflict to whichever
asked second, and updates split unpredictably between the two.

The lease is a row in the family schema saying which process may poll for
a bot, and until when. Its holder renews it every LEASE_RENEW_SECONDS and
deletes it on the way out; a holder that dies without deleting it (an OOM
kill, or SIGKILL at the end of the drain) loses it when it expires.

Up to 2.1.4 the lease was a session-level advisory lock, which the server
drops when the connection that took it closes. Behind a connection pooler
that connection is the pooler's, not the bot's: it outlives the bot, keeps
the lock, and is handed to whoever connects next, the new container
included. Production showed exactly that on 2026-11-10 (Supabase's
Supavisor): the five locks sat on pooler connections opened on 2026-10-23,
and each new container "got" its lease within two seconds, while the old
one was still polling. A row with an expiry works through any pooler,
because every statement stands on its own.

_At `LEASE_TTL_SECONDS = float(os.environ.get("DEPLOY_LEASE_TTL_SECONDS", "30"))`:_

How long a lease lasts unless it is renewed. This is what a holder that
died without handing the lease back costs whoever comes next: up to this
long before it may poll. Renewed three times a period, so one slow round
trip to the database does not lose it.

_At `_HOST = socket.gethostname()`:_

Who holds a lease: this machine, this process, and a token of its own. The
token tells two lives of one process id apart -- the first process in a
container usually gets the same number every time it starts.

### `_lock_key()`

A stable signed 64-bit key, which is what pg_advisory_xact_lock takes.

### `init_lease_table()`

Blocking. Every bot starts at once, so on the first start they would all
create the table together, and two CREATE TABLE IF NOT EXISTS that meet can
still fail. A transaction-level advisory lock lines them up: unlike the
session-level kind it ends with its transaction, and a pooler keeps a
transaction on one connection from start to finish.

### `_take()`

Take the lease if it is free or has expired, or renew it if this process holds it. True if this process holds it now. Blocking.

### `_gone()`

A holder on this machine that is no longer running: a process that
crashed and was started again in the same container. Whoever comes next
need not wait out its expiry.

- At `return True`:
  The same number with another token: an earlier life of this process.
- At `return False`:
  There, just not this process's to signal: running.

### `_try_lease()`

One attempt at taking (or renewing) the lease. Blocking.

Under a lock, so that a renewal already on its way cannot put the row back
after release_lease() has deleted it.

### `hold_the_lease()`

Block until this process is the only one polling for `bot_id`.

Returns True when this process holds the lease. Returns False -- and lets the bot
start anyway -- if it could not be had: an unreachable database must not
be able to stop a bot from serving its users, and a lease nobody can
check is no worse than the no-lease-at-all this family ran with before.
Either way it then minds the lease in the background, renewing it or
taking it once it is free, so that the next container waits for this one.

### `_mind_the_lease()`

For as long as this process runs: renew the lease, or take it once it
is free.

### `release_lease()`

Hand the lease back on the way out, once polling has stopped, so the
next container need not wait for it to expire. Blocking.

## 2. State that outlives the process

One table in the bot's own schema (search_path is already pointed there by
db.py, so this needs no qualification and cannot collide with a sibling's).

JSONB rather than pickle, on purpose. It is readable from ManagerBot's /sql,
it can be pruned by age with plain SQL, and it cannot execute anything on
the way back in. The price is that values which are not JSON -- notably
whole telegram.Message objects -- are dropped instead of stored; every
caller in this family already treats those as in-memory-only, and the one
place that keeps one (AnonBot's held message) has said "expired" when it is
missing since the day it was written.

_At `STATE_PURPOSE = "lifecycle.state"`:_

What the table's rows are sealed under (shared/security/vault.py).

_At `def _pseudo_key(kind: str, key: str) -> str:`:_

```text
Sealed since 2.1.2CHL. user_data holds a person's language, a half-built
pack's name, an AnonBot inbox token; a conversation's key says who is in
which chat. So a row keeps neither in the clear:
  key    the same shape as before, each id replaced by its pseudonym, so the
         primary key still finds a person's row and /deletemydata can still
         delete it (forget_user);
  value  {"sealed": <base64>}, which opens to {"key": <the original key>,
         "data": <the original value>}.
Reading opens every row and hands back the original key and value, so
nothing above this block ever sees a pseudonym. A row that does not open,
or whose sealed key is not the one it sits under, is skipped with a warning
(the table and the kind, never a key or a value).
```

### `_pseudo_key()`

The key a row is stored under. ValueError when `key` is not one this
table knows how to hide (a part that is not a number, a part that already
is a pseudonym, a kind not stored here) -- never a plain key by default.

### `_seal_state()`

(the key to store, the value to store) for one row.

### `unseal_state()`

(the original key, the original value) of a stored row. VaultError when
the row is not sealed, does not open, or has been moved to another key.

- `except ValueError:` — it opened, but is not the JSON that was sealed

### `_json_safe()`

`value` if it survives a JSON round trip unchanged, else None.

Tuples are the one thing deliberately \*not\* accommodated: JSON has no
tuple, so one would come back as a list and quietly change type under
code that unpacked it. Nothing in this family keeps a tuple in user_data
that has to survive a restart, so refusing them is safer than converting
them.

### `_delete_state()`

- `continue` — never saved under a key that could be hidden, so there is nothing to delete

### `_read_state()`

Everything of one kind that is still young enough to mean something,
deleting the rest on the way past -- the only pruning this table needs,
and it happens once per process start. (key, value) as they were saved,
the ids real again; a row that does not open is skipped.

### `PostgresPersistence`

user_data and conversation states, in the bot's own Postgres schema.

Written at shutdown (python-telegram-bot calls flush() there) and every
PERSIST_SECONDS in between, not on every update: the point is to survive
a restart, and a restart gives ample warning. Conversation states are the
exception -- they are single small rows, they change rarely, and being
one state behind is the difference between picking a conversation up and
dropping it, so those are written as they happen.

#### `PostgresPersistence.__init__()`

- At `update_interval=max(PERSIST_SECONDS * 10, 600),`:
  python-telegram-bot's own timer only \*collects\* changes (it
  calls update_user_data and stops there); the writing is driven
  from install()'s job below, so PTB's pass is pushed far out
  rather than doing the same deep copies twice a period.

- At `async def get_user_data(self) -> dict[int, dict]:`:
  ---- loading, once, at startup ----

#### `PostgresPersistence.get_conversations()`

- At `conv_key = tuple(int(part) for part in pair.split(":"))`:
  Rebuilt with the same arity it was stored with: a
  ConversationHandler's key is (chat, user) by default but
  (user,) with per_chat=False, and handing back the wrong
  shape would silently match nothing.

- At `async def update_user_data(self, user_id: int, data: dict) -> None:`:
  ---- collecting changes ----
- At `async def flush(self) -> None:`:
  ---- writing them out ----

#### `PostgresPersistence.flush()`

- At `deletes.append(("user", str(user_id)))`:
  Their state emptied out -- a /cancel, a /done, or nothing
  worth keeping in the first place. Without this the row
  would be restored as an empty dict forever.
- At `self._dirty_users |= dirty - self._dropped_users`:
  Nothing that was in this pass is known to be saved, so it is all
  still to do: kept for the next pass rather than forgotten until
  the person next does something. Anyone who changed, or was
  dropped, while the write was failing is as they now are, not as
  they were.

## 3. Work that is in flight when the platform says stop

python-telegram-bot already waits for running handlers before it shuts down,
so short work finishes on its own. The problem is the long kind: a video
encode or a download can outlast whatever grace period the platform allows
before SIGKILL, and the user is left watching a "converting..." that will
never change.

There is no way to make that work survive. There is a way to make sure the
user hears about it, which is all busy() does: it keeps a note of who is
waiting on what, and the stop handler answers all of them at once.

_At `_in_flight: dict[int, tuple[int, str]] = {}`:_

chat id -> the message to send there if this work does not survive. The
message rather than a label, because this file is byte-identical in five
bots and four of them are trilingual: the caller has the user's language in
hand and this file has no business importing i18n to go and find it.

_At `_in_flight_since: dict[int, float] = {}`:_

When each stretch began, so a soft stop that finds one can say how long it has run.

### `is_draining()`

True once a stop signal has arrived. Check it before starting
anything slow -- see the guard in each bot's media handlers.

### `busy()`

Mark a stretch of slow work, so a redeploy in the middle of it ends
with an explanation rather than with silence.

```text
async with lifecycle.busy(chat_id, i18n.t(lang, "restarting_send_again")):
    ...
```

`if_interrupted` is the finished, translated message to send if a stop
signal arrives while this is running -- and nothing at all otherwise.

### `_tell_the_interrupted()`

One message each, to everyone who was mid-something. Best-effort and
strictly time-boxed: this runs inside the platform's shutdown grace
period, and holding that up to retry a send helps nobody.

## 4. A deploy is not a crash

ManagerBot calls a bot down when its heartbeat goes stale, which is exactly
what a redeploy does to it. Leaving a note in the shared settings table
lets the watchdog tell "this was on purpose" from "something is wrong"
-- see ManagerBot's watchdog.

## 5. An update announced before it happens

Sections 1-4 are about a redeploy nobody was warned about. This one is for
a deploy known in advance: the operator puts a bot into maintenance from ManagerBot, and until the operator says otherwise it declines to \*start\* anything a
restart would throw away, says roughly how long it expects to be, and
writes down who it turned away so they can be told when it is over.

Three properties, none of which a module-level flag would have:

```text
It has to survive the restart it is announcing. One update is often
several deploys, so the flag lives in family.settings and is read back at
startup rather than being held in the process that is about to end.
```

```text
It has to be free to check. Every incoming message asks "is the bot paused?",
so the answer is a cached flag -- set at startup and whenever ManagerBot
says so -- and not a query per update.
```

```text
The people turned away have to outlive the process too. That is what
family.update_waitlist is: one row per person told to come back, removed
when they are told they can.
```

The announced time is an estimate and nothing more. Maintenance ends when the operator ends it, never on a timer -- because the reason for a manual end is exactly that one update can take several deploys, and a timer
would let the bot reopen between two of them. Once the estimate has passed
the bots stop quoting a number rather than start quoting a wrong one.

_At `DEFAULT_MAINTENANCE_MINUTES = int(os.environ.get("DEPLOY_MAINTENANCE_MINUTES", "10"))`:_

What ManagerBot's /pause promises when it is not told a number.

### `init_waitlist_table()`

- At `import family_link`:
  user_id and chat_id hold pseudonyms since 2.1.2CHL; the real ids are
  in who_sealed, which is how take_held() knows whom to tell. A table
  made by an older version gets the column here and nothing else is
  rewritten (scripts/security/encrypt_personal_data.py empties the old rows).

### `_read_maintenance()`

(is it on, what was promised). Blocking.

- At `return True, None`:
  The row is the flag; a value that will not parse costs the estimate
  and nothing else.

### `refresh_maintenance()`

Read the flag out of the shared database into this process. Blocking.

Called at startup and after ManagerBot changes it -- the only two moments
it can have changed, which is why nothing else has to ask.

### `begin_maintenance()`

Blocking. Returns the moment being promised, so the caller can say it
back to the operator in the same words the users will get.

### `end_maintenance()`

Blocking. Deletes the flag rather than blanking it, so "paused" is the
presence of a row and there is no second way to spell "off".

### `maintenance_minutes_left()`

Whole minutes still promised, or None once the estimate has run out.

None does not mean the pause is over -- only the operator ends that. It
means there is no honest number to quote any more, and the bots say
"shortly" instead of a figure they have already missed.

- At `return max(1, -(-int(left) // 60))`:
  Rounded up, so 90 seconds left is "2 minutes" rather than "1" -- but a
  real ceiling, not seconds//60 + 1, which turns a flat ten minutes into
  eleven the instant it is announced.

### `is_paused()`

True when nothing slow should be \*started\*: a stop signal has already
arrived, an update has been announced, or a soft stop is checking
whether anybody is using the bot (see "Soft stop" at the end of this file).

The check every long handler makes. is_draining() is still the narrower
question -- "is this process going away right now" -- and is what the
shutdown path itself uses.

### `hold_for_update()`

Remember that this person was turned away, so /finishupdates can tell
them it is over. Blocking; best-effort.

One row per person, not per attempt: someone who tries four times during
an update is one person to apologise to, not four.

### `in_flight()`

Who is mid-something right now, as (chat_id, the message they would
get if it were interrupted) -- one entry per stretch of slow work.

The same registry the shutdown path reads, exposed so the operator can warn
these people \*before\* pressing deploy rather than as the door closes.

## Wiring

### `persistence()`

Pass to ApplicationBuilder().persistence(). None when deploy safety is
off, in which case every ConversationHandler must also drop
persistent=True -- which is what persistent() below is for.

### `persistent()`

For ConversationHandler(persistent=..., name=...). Adding a persistent
handler to an Application that has no persistence is an error, so the two
have to agree.

### `_drain_and_stop()`

- At `if _bot_id:`:
  Off the event loop: this is a database write, and the signal handler
  that led here runs \*in\* the loop -- blocking it would hold up the
  very shutdown it is announcing.
- At `logger.info("Stopped before startup finished; exiting.")`:
  A stop signal that arrived before run_polling() had finished
  starting. Nothing is serving anyone yet, so there is nothing to
  drain gracefully -- just go.

### `polling_kwargs()`

Wrap each bot's run_polling() arguments.

Where the stop signals can be taken here, they are, because
python-telegram-bot's own handler goes straight to shutdown and there is
no way to get a word in before it -- and the word is the whole point:
an update interrupted the work, send it again. Where they cannot (Windows has
no loop.add_signal_handler), the key is simply left out and PTB's own
default handling stands.

### `_persist_job()`

Collect what changed and write it out. Both halves are needed: PTB's
update_persistence() is what copies live user_data into the persistence
object, and flush() is what puts it in Postgres.

### `install()`

One line in each bot's main(), next to family_link.attach().

Everything it wires is best-effort: a bot whose database is unreachable
keeps running exactly as it did before any of this existed. `admin_ids`
are the operator's accounts, whose own messages do not count as somebody using the bot
when a soft stop asks.

### `on_start()`

Register as (part of) each bot's post_init, before it starts polling.

Two jobs: make sure no previous container is still polling this token,
and take the stop signals so a redeploy can say goodbye properly.

- At `try:`:
  Running again, so whatever stop the last /shutdown recorded is over.
- At `try:`:
  An update is usually several deploys, so a container that has just come
  up has to find out whether it is still inside one before it starts
  taking work it would only have to throw away again.
- At `loop = asyncio.get_running_loop()`:
  polling_kwargs() has already told python-telegram-bot not to install its
  own handlers, so if none of these land the process would be deaf to
  SIGTERM and the platform would go straight to SIGKILL. The plain
  signal.signal() fallback exists for that case alone.

### `on_stop()`

Register as the \*first\* thing in each bot's post_stop -- before
flush_on_shutdown(), which closes the connection pool these writes go
through.

python-telegram-bot flushes persistence itself a moment later, during
shutdown(). Doing it here as well is what makes the ordering explicit
instead of load-bearing: by the time the pool closes, the state is
already written.

_At `QUIET_MINUTES = float(os.environ.get("SOFTSTOP_QUIET_MINUTES", "5"))`:_

```text
--- Soft stop -------------------------------------------------------------------------------------
The only way a service is meant to go down: ManagerBot's /shutdown (exit 0, it stays down) or
/restart (exit 1, the host starts it again). It first closes the door -- every bot in the process
declines new slow work, with no await in between -- then looks for anybody using any of them, and
reopens and refuses if it finds one. "In use", for one bot (usage_here):
  - slow work in progress: a busy() stretch (StickerBot, DownloaderBot) or a registered work source
    (ConvertBot's conversions, queued or running);
  - updates received and not yet handled;
  - somebody other than the admins sent it anything -- a message, a button, a payment -- in the
    last QUIET_MINUTES;
  - somebody other than the admins is part-way through a conversation (a pack being built, a reply
    being written) and took a step in the last CONVERSATION_MINUTES;
and, for the process, a bus command still running on any of its bots (a broadcast, a gift, a dump).
A stop signal from the host (a deploy, Railway's Restart/Remove) cannot be refused; it waits up to
SIGTERM_WAIT_SECONDS for work in progress instead, inside the host's draining window.
```

### `add_work_source()`

`count()` -> how many pieces of slow work of this kind are queued or running now.

### `_wait_for_work()`

Until nothing slow is in progress here, or `limit` seconds.

### `_conversation_users()`

The keys of every open conversation of every ConversationHandler in the app.

### `usage_here()`

Why this bot counts as in use right now, as short phrases about how many and how long ago, never who; empty when it does not.

### `_peers()`

The lifecycle of every bot still running in this process (each bot has its own copy of this module); a bot stopped on its own is no longer one of them.

### `_running_bus_commands()`

(bot, command, seconds running) for family commands still running on these bots, apart from stops and restarts (the one asking among them).

### `soft_stop()`

Stop this whole process -- every bot in it -- if nobody is using any of them; otherwise say why not. exit_code 0: stay down; 1: the host restarts it.

### `soft_stop_bot()`

Stop only this bot, if nobody is using it; the other bots of its process carry on. It comes back when the process next starts.
