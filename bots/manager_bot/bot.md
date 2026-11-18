# `bot.py`

[↑ manager_bot](README.md) · [code](bot.py)

ManagerBot -- the private one, for the operator only.

The other four bots in the family each serve the public and each answer to
their own operator-only commands. ManagerBot serves exactly one person and
answers for all of them:

```text
* **Watches.** Every bot stamps a heartbeat into the shared database every
  30 seconds (family_link.py). ManagerBot checks those stamps once a minute
  and messages the operator the moment one goes stale -- and again when it
  comes back. It never repeats itself while a bot stays down.
* **Reports.** Anything a bot considers worth waking someone for -- an
  unhandled exception, a donation, a restart -- lands in family.events and
  ManagerBot forwards it as a DM within about twenty seconds.
* **Reaches in.** /run <bot> <command> puts a job on the family command
  queue; the target bot runs it in its own process, with its own code and
  its own Telegram identity, and the answer comes back here. That is how
  /dbdump, /whois, /message and the rest work against a bot deployed on a
  machine ManagerBot cannot otherwise reach.
* **Reads across.** One shared Postgres database with a schema per bot
  means "how many people used ConvertBot this week" is a single query, no
  matter whether ConvertBot itself is even running.
```

Nothing here is reachable by anyone but the ids in MBOT_ADMIN_ID. A stranger
who finds this bot gets one flat sentence and nothing else -- and the operator is told they turned up.

Env vars: MBOT_TOKEN, MBOT_USERNAME (no @), MBOT_ADMIN_ID (required -- with
it empty the bot refuses to start rather than run wide open), DATABASE_URL
(the shared family database), DB_SCHEMA (default "manager_bot").

_At `try:`:_

main.py gives each bot its settings; this only reads a .env if one is present.

_At `BOT_USERNAME = (os.environ.get("MBOT_USERNAME") or "").strip().lstrip("@") or None`:_

Without the @, which is also taken off if it was written with one -- the
one-process edition always allowed it, and a link built from "@name" is broken.

_At `DOWN_AFTER_SECONDS = int(os.environ.get("MBOT_DOWN_AFTER_SECONDS", "120"))`:_

A bot is "down" once its heartbeat is this stale. family_link beats every
30s by default, so this is four missed beats -- long enough not to page
anyone over one slow database round-trip, short enough to catch a crash
loop before it has been down for an hour.

_At `COMMAND_TIMEOUT_SECONDS = int(os.environ.get("MBOT_COMMAND_TIMEOUT_SECONDS", "90"))`:_

How long a queued command waits for its target before ManagerBot gives up
and says so.

_At `RESULT_POLL_SECONDS = int(os.environ.get("MBOT_RESULT_POLL_SECONDS", "20"))`:_

How ManagerBot collects command results and child-bot events: the same
adaptive poll the child bots use for the command queue (family_link.\_bus_tick).
One fast tick decides each time whether to sweep -- every FAST interval while
the bus is active (family_link.bus_is_active(), which ManagerBot sets the
moment it queues a command), every SLOW interval once it has gone quiet.

_At `PUMP_TICK_SECONDS = int(os.environ.get("MBOT_PUMP_TICK_SECONDS", "1"))`:_

How often the pumps tick to make that decision.

_At `REDEPLOY_GRACE_SECONDS = int(os.environ.get("MBOT_REDEPLOY_GRACE_SECONDS", "300"))`:_

A redeploy makes a bot's heartbeat stale exactly the way a crash does, and
for the first minute of it there is no way to tell them apart from the
outside. So the inside says: a bot that is shut down on purpose leaves a
note in family.settings on its way out (lifecycle.mark_expected_restart),
and a stale heartbeat with a fresh note next to it is reported as a
redeploy rather than as a failure. If it is still missing when the note has
gone stale, the ordinary "it is down" alert fires after all -- a deploy
that never came back is exactly the thing worth being told about.

_At `STARTUP_ROLLCALL_SECONDS = int(os.environ.get("MBOT_ROLLCALL_SECONDS", "5"))`:_

How long the startup roll-call waits before reporting who is up. The point
of the delay is to send one message instead of five: the other four write
their first heartbeat inside family_link.attach(), synchronously, before
they start polling -- so a few seconds is all it takes for a family started
together to be fully visible. start_all.ps1 already gives them the same head
start, for the same reason. Set to 0 to report immediately.

_At `ROLLCALL_FRESH_SECONDS = int(os.environ.get("MBOT_ROLLCALL_FRESH_SECONDS", "120"))`:_

A bot whose heartbeat is younger than this at roll-call time came up with
this batch rather than having been running already. Affects the wording of
one line, nothing else.

_At `ALERT_LEVELS = {"warning", "error", "critical"}`:_

Which event levels are worth an unprompted DM. Everything is still
recorded either way -- /events shows the rest.

_At `DIGEST_AT_UTC = os.environ.get("MBOT_DIGEST_UTC", "").strip()`:_

"HH:MM" in UTC for a once-a-day summary, or empty for none (the default --
down/up transitions and crashes already arrive on their own, and a daily
"all fine" message is the kind of thing that stops being read).

_At `NIGHTLY_REPORT_AT_UTC = os.environ.get("MBOT_NIGHTLY_UTC", "13:00").strip()`:_

The nightly problem report, at 22:00 in Suwon. It was at around midnight Seoul/Suwon time from 1.7.0; on 2026-10-03 it was moved to 22:00. Korea has not
observed daylight saving since 1988, so KST is UTC+9 all year and 13:00 UTC
is 22:00 in Suwon on every date -- no timezone database needed, and no
twice-yearly hour where the report arrives at nine.

Deliberately NOT the release window. That is 04:00 Asia/Tashkent (23:00
UTC), chosen for when the \*users\* are asleep; this one is chosen for when the operator is awake. The two have no reason to be the same time and it would
be a bug if changing one moved the other.

- `TELEGRAM_MAX_CHARS = 3900` — a little under the real 4096, leaving room for markup

_At `POLL_TIMEOUT = int(os.environ.get("POLL_TIMEOUT", "30"))`:_

Telegram's long-poll window -- see the note in main().

## Who is in the family

id : display name : Postgres schema. The ids match each bot's own BOT_NAME
constant (and the SIBLING_BOTS env var they all share), which is what the
command queue and the heartbeat table key on. Override with FAMILY_REGISTRY
when a sixth bot is added without redeploying this one.

_At `USERNAME_ALIASES = {`:_

The one place a name that is not the family name is allowed to exist.

Every layer of this project derives from one canonical id -- see the naming
table in ARCHITECTURE.md -- with exactly one exception: the Telegram
@username. One of those was chosen before the family names settled and
cannot simply be brought into line, because a username is not a label but
an address:

```text
AnonBot is @mumu_chat_bot, and every inbox link anybody has ever posted
is https://t.me/mumu_chat_bot?start=q_<token>. Changing it does not
rename the bot, it breaks every one of those links, permanently, with no
redirect and no way to find the people holding them. That username is
frozen for as long as the bot has users.
```

So rather than pretend the mismatch is not there, it is written down here
and every form resolves. `/logs chat`, `/logs anon`, `/logs anonbot` and
`/logs @mumu_chat_bot` all reach the same bot.

This bot used to be the second exception. It was ManagerBot's predecessor
ParentBot at @mumu_manager_bot, and the plan was to move the username to
@mumu_parent_bot. v1.6.0 did the opposite -- the name followed the address
rather than the address following the name -- because the username was the
only layer with anything depending on it and the other six were free. So
there is one exception left in the family rather than two.

`parent` and `parentbot` stay as aliases. They are what has been typed for months, and an alias costs a list entry.

### `resolve_bot()`

Accepts "sticker", "stickerbot", "StickerBot", "sticker_bot",
"@mumu_sticker_bot" -- any unambiguous prefix of the id, the display
name, the schema, or the Telegram username.

## Access control

Tracked so the operator hears about a stranger once rather than on every
message they send. In memory only: a restart re-arms it, which is fine --
the point is "someone found the private bot", not an audit log.

### `guard()`

True if this update may proceed. Non-admins get one flat sentence,
and the operator is told about them the first time.

- At `await notify_owner(context, "👀 A stranger found ManagerBot, and was turned away.")`:
  That somebody found it, not who: no user details go to the operator.

### `strangers_stop_here()`

Before every other handler (group -100): an update from anybody but the operator goes
no further, whatever it is. Every command still calls guard() as well; this is the wall
behind a new command, button or handler that forgets to.

## Sending

### `notify_owner()`

Never raises. True if at least one admin got it -- a caller that must
not lose the message (the 220 gift's one notice) tries again on False.

### `send_long()`

Anything past Telegram's message ceiling goes as a file instead of
being silently cut in half -- a truncated log tail is worse than useless
when working out what broke.

## /status -- the board

### `_build_board()`

Runs entirely in a worker thread (it is all blocking SQL), so the
caller wraps it in asyncio.to_thread. One query for the whole board --
see db.status_snapshot. The sweep timings live in memory and are mutated
by the event loop, so the caller reads them there (\_sweep_line()) and
passes the finished line in; this function never touches them.

### `_board_keyboard()`

A refresh button, because /status is the command anyone watching a
deploy types over and over -- and a mute toggle right where it is wanted.

## /usage -- what the containers have been costing, rather than whether they are up

/status answers "is it alive". This answers "what is alive costing, and is
that normal", which is a different question and the one the bill asks.

The rate below is Railway's published memory price. It is here, as a
constant with its own name, so the number in the message is arithmetic
anybody can check rather than a figure somebody remembered -- and so that
when Railway changes it, one line changes.

_At `CPU_USD_PER_VCPU_MONTH = float(os.environ.get("CPU_USD_PER_VCPU_MONTH") or 20)`:_

Railway's published CPU price, per vCPU-month, for the same reason.

### `_cpu_windows()`

(CPU seconds used, seconds elapsed) for each window of one bot's samples.

A sample's cpu_seconds is a running total since the process (or the service) started, not
the window's own, so a window is the difference between a sample and the one before it.
A pair is skipped when the total went down (a restart began it again) or when the two are
far apart (the bot was away between them), so a restart never reads as a spike.

## /idle -- would sleeping this bot be worth it, and what would it cost

Railway stops charging for a container once it has sent no outbound traffic
for about five minutes, and starts again when something wakes it. Whether
that is a good trade for a given bot is two numbers: how much of its life it
spends quiet, and how many times a day somebody would therefore wait for a
cold start.

Both are measured rather than guessed. Every bot accumulates, per window,
the seconds it \*would\* have been asleep (each gap between updates, minus the
five minutes before sleeping begins) and the longest gap it saw. This reads
those back.

Nothing sleeps yet. This is the evidence for deciding what should.

### `_idle_report()`

- At `wakes = min(updates, int(sleepable / max(monitoring.SLEEP_AFTER_SECONDS, 1)) + 1)`:
  One wake per gap long enough to have slept through. The count is not
  stored, so it is bounded from what is: a bot cannot have woken more
  times than it handled updates.

## /buttons -- how often each button is tapped (counts, nothing about who)

Each public bot counts the taps of its buttons per day for the whole bot and
writes the counts with its usage sample (family.button_taps; the key is the
button's name, never an id or a link). DownloaderBot also counts a few events
that are not callbacks -- how long people take to tap "get it as a file", whether
the delivery cache answered it -- which are the numbers that decide how long that
cache should keep a download. ManagerBot's own buttons are not counted.

- `BUTTONS_MAX_LINES = 25` — keys listed per bot, then "+N more"

_At `STATS_DEFAULT_DAYS = 30`:_

/stats: the anonymous daily counts the bots write (shared_features.note_stat,
family_link.record_stats) -- what was done, never who, never finer than a day.

### `board_button()`

The three buttons under the /status board.

- At `await query.message.reply_text(`:
  A second query.answer() on the same callback is rejected by
  Telegram, and this one has already been answered above -- so say it
  in the chat instead.
- At `await edit_in_place(query.message, context.bot, board,`:
  live_message rather than edit_text: it swallows "message is not
  modified" (which the timestamp in the footer makes rare anyway), and it
  moves the board down to the bottom of the chat if anything has been
  said since the buttons were tapped.

### `me_command()`

ManagerBot's own /status, in the same shape every other bot uses.

## /run -- reaching into another bot

## Asking an older bot for something it has never heard of

The bots deploy as separate services, so one can be behind the others --
a deploy that failed, or (until 2.0.0, when publish.ps1 -Skip existed) one
held back on purpose -- and a bot on an older version answers an unknown
family command with "Unknown command" rather than failing. That is the
right behaviour and it stays.

What it is not is a good explanation. ManagerBot is always the first thing
to ship (it is private, so it costs nobody anything), which means it is
routinely a version or two ahead of the bots it is asking. Typing
/providers and being told "Unknown command 'providers'" says the bot is
broken; the truth is that DownloaderBot has not been published since the
command was written.

ManagerBot already knows every bot's version -- it is in the heartbeat and
on the /status board. So it can say which it is, before the round trip.
Anything not listed here has been in the family since before the versions
were tracked, and is never blocked.

_At `"gift": "2.1.1",`:_

Since 2.1.1, where /gift arrived (2.1.2 said so): ManagerBot has its own service now and may
run a newer version than the public bots.

_At `"softstop": "2.1.6",`:_

The soft stop (2.1.6): an older bot does not know these, and its "restart" exits at once.

### `_version_key()`

A sortable form of a family version, or None if it cannot be read.

Tolerant on purpose. It has to cope with `1.2.2R` (a retouch release
sorts after the plain patch), with `test` (what testbot/run.ps1 sets),
and with whatever a future release invents -- and the cost of getting it
wrong is refusing a command that would have worked. So anything it
cannot parse reads as None, and None never blocks.

- At `parts.append((int(digits), chunk[len(digits):]))`:
  The suffix orders after the bare number: 1.2.2 < 1.2.2R.

_At `FLAG_SINCE = {("broadcast", "--active"): "1.3.0", ("logs", "problems"): "1.6.0"}`:_

A flag can be as new as a command, and is more dangerous: an unknown
command is refused, while an unknown flag is just part of the message. A
1.2.x bot handed `broadcast --active hello` would send every user it has
ever seen a message beginning "--active".

### `_too_old_for()`

The sentence to show instead of queueing, or None to go ahead.

### `_accepted_commands()`

Which bus commands a bot answers, from the list it publishes in its own
heartbeat.

Falls back to the shared set for a bot that has never been seen, or one
still on a version from before it advertised anything -- which is the
honest answer for both: the shared commands are the ones every bot has
always had, and refusing on the strength of a missing column would break
every command against a bot that is merely old.

### `_dispatch()`

- At `beat = await asyncio.to_thread(db.heartbeat_of, bot["id"])`:
  Asked before the round trip rather than after it, so a version gap
  costs one lookup instead of ninety seconds and a confusing answer.
- At `accepted = _accepted_commands(beat)`:
  What that bot says it answers, rather than what this process happens to
  have in COMMANDS. A bot can register commands of its own at startup --
  ConvertBot's `stars`, DownloaderBot's `probe` and `providers` -- in its
  own process, where ManagerBot cannot see them. Checking the local dict
  refused exactly those three, which is how /stars came back as
  "ConvertBot has no 'stars'" from the only bot that has it.
- At `await update.effective_message.reply_text(f"→ {bot['name']} · {command} — queued as #{co …`:
  "queued as #N", not "(#N)": the bare number read as a \*result\* -- an
  acknowledgement of "errors" that says (#5) and is answered seconds later
  with "No errors since this instance started" looks like the bot
  contradicting itself. N is the row id of the queued command, which is
  only ever useful for matching this line to the timeout notice that
  quotes the same id.

_At `SERVICE_ASK_SECONDS = 120`:_

/shutdown and /restart ask first, with buttons: which bot, and whether the
whole service it runs in. The question is held here, by a nonce in the
buttons' data, for SERVICE_ASK_SECONDS; a tap after that, or a second tap,
does nothing.

### `_service_of()`

(bot, the ids of every bot in its service) -- or None, having said why
not. A service is every bot whose heartbeat comes from the same host.

### `shutdown_command()`

/shutdown &lt;bot> -- stop that bot, or the whole service it runs in, after
asking which; only if nobody is using it (lifecycle.soft_stop). Once it is
down, a copy of the whole database follows (shutdown_copy).

### `restart_command()`

/restart &lt;bot> [force] -- restart the service that bot runs in, after
asking, once nobody is using it. `force` restarts that bot alone, at once:
in 2.2.0 a bot's os.\_exit(1) is caught by the launcher (main.\_BotOs.\_exit),
which loads it again while the others carry on.

### `service_button()`

A tap under /shutdown or /restart: queue what was chosen, once.

### `run_command()`

/run &lt;bot> &lt;command> [args] -- the general form. The shortcuts below
(/dbdump, /whois, /say, ...) are just this with the command baked in.

- At `try:`:
  Whatever the bots themselves say they answer beyond the shared set.
  Read rather than listed by hand, so a bot that gains a command shows
  up here on its next heartbeat.

### `_shortcut()`

Builds /whois, /say, /logs and friends -- each is /run with the
command fixed and the first argument still naming the target bot.

`default_bot` is for the commands only one bot has. /providers and
/stars are not ambiguous, so having to type which bot has them means remembering something this file already knows. Naming the
bot still works, and still wins, for when that stops being true.

## Asking every bot at once

/errors &lt;bot> was one bot's answer and /errors on its own was a usage line,
which is the wrong way round for the question it answers. "Has anything
broken?" is a question about the family, and having to ask it four times -- and remember four names to ask with -- is four chances to check
three bots and forget the fourth.

So a command with no bot named goes to all of them and comes back as one
message. The machinery is the same as a family /ping: queue the command per
bot, remember which group each queued id belongs to, and rewrite one live
message as the answers arrive. Written generally because /errors is not the
only command shaped this way -- anything read-only and short belongs here.

### `_fanout()`

Ask every child bot the same thing and gather the answers into one
message.

- At `for child in targets:`:
  A bot too old for the command is not queued at all: it would sit there
  until the timeout and then be reported as down, which is the one wrong
  answer that starts a hunt for a fault that is really a missing deploy.

### `_fanout_text()`

One section per bot, in the order the family is declared in -- which is
the order every other list in this bot uses, and the order they are read in. Not sorted by how bad the news is: a report whose lines move
about between runs is one nobody can skim.

- At `if not waiting and group.get("footer"):`:
  Only once everything is in: a footer that appears under a half-finished
  list reads as though the list is finished.

### `_render_fanout()`

- At `await group["live"].set(context.bot, f"{group['title']} — the answers are attached.")`:
  Four bots' worth of error tails can outgrow one message. The file is
  plain text, so the markup comes off rather than being shown as tags.

### `_recorded_line()`

One line summarising what the shared table holds for the same window
the live counts cover -- which is what makes the difference between the
two commands visible without explaining it every time.

### `errors_command()`

/errors &lt;bot> is that bot's errors since it started. /errors on its own
is every bot's, in one message -- which is what the question usually is.

- At `logger.debug("Could not read the recorded problem counts", exc_info=True)`:
  The live half is the half this command is named for; a database
  that will not answer must not cost it.

## /errorlog, /botlog, /problemlog -- the logs, without remembering how

Easier commands to see the logs were asked for. /logs &lt;bot> [n]
[bot|problems] reads all three, but only for somebody who remembers the file
names and the bot names. These carry the file in the command's name, and with
no bot named they answer with a button per bot instead of a usage line.
Typing the bot still works: /problemlog conv 100.

### `log_pick_button()`

A bot tapped under /errorlog, /botlog or /problemlog. The buttons stay,
so another bot's log is one more tap.

## /ping -- the whole round trip, leg by leg

"Is it up?" was the old question and a word was a fine answer. The useful
question is "where does the time go?", and answering that means measuring
five separate hops on two machines whose clocks do not agree:

```text
the phone -> Telegram -> here     network, plus however long the update
                                  sat in Telegram's queue
here -> Supabase                  the INSERT that queues the command
Supabase -> the target bot        how fast it hears about it
the target bot                    its own work, including its own hop to
                                  Supabase
Supabase -> here                  the answer coming back
```

Two machines, one honest clock. Every cross-machine figure below is a
difference between two Postgres timestamps -- created_at, claimed_at,
finished_at, clock_timestamp() at pickup -- so none of them contains the
difference between this host's idea of the time and Railway's. The
per-machine figures (each end's own round trip to Supabase, and its clock
skew against it) are measured locally at each end and reported separately,
which is what makes "the database is slow from there" distinguishable from
"that bot is slow".

The one figure that is \*not\* exact is the first: Telegram stamps a message
with whole seconds, so the phone-to-here leg is only good to about a
second, and it is labelled that way rather than quietly presented as
precise.

### `_ms()`

A duration, in whichever unit makes it readable. Takes a timedelta
(the Postgres-clock differences) or a plain number of milliseconds (the
locally measured legs).

### `_render_ping()`

The report. Everything in the first block is one Postgres clock or a
stopwatch that started and stopped in this process; nothing in it is a
subtraction across two machines' clocks.

- At `head = (f"🏓 <b>{html.escape(name)}</b> — {_ms(taken - created).strip()} "`:
  Named for what it is. This is the admin bus -- ManagerBot handing a
  command to another bot through a Postgres table and waiting for the
  answer to come back. It is not what a user waits for: their message goes
  to the bot they are talking to directly and never touches this path. The
  two were easy to confuse while this number was tens of seconds.

### `_user_wait_ms()`

The number the summary sorts on and shows first: what a person waits
for this bot's own work on an info command. None when the bot is too old
to measure it, or could not.

### `_render_user_wait()`

The other half of the report, and the half anybody outside this chat
would recognise.

The block above it is the admin bus. This is a /start: one indexed read
of the row that says which language somebody chose, and one trip to
Telegram carrying the answer back. Neither is a guess -- the bot ran both
while it was answering the ping.

What is deliberately not added in is the person's own trip to Telegram.
It is the largest term for somebody on mobile data and neither end can
measure it, so it is named as missing rather than folded in at a number
nobody chose.

### `_ping_one()`

- At `try:`:
  This end's own distance from the database, measured the same way the
  far end measures its own -- the two are only comparable because both
  are one round trip from the same probe.

### `broadcast_ping()`

/ping &lt;bot> reports the round trip leg by leg. /ping with nothing
named asks all of them at once and reports one line each -- still the
fastest way to tell "the database is down" from "one bot is down".

- At `started = time_module.perf_counter()`:
  Everything \_render_ping needs is measured here too, per bot, even
  though the summary shows one number each. That is what lets the
  buttons under the result open a full leg-by-leg breakdown instantly,
  from the ping that was actually run, rather than quietly running a
  second one and showing different numbers than the row above it.

## The buttons under a family ping

/ping with no bot named used to end at four numbers, and getting the
breakdown for the one that looked wrong meant typing /ping &lt;bot> and
waiting for a second round trip. Four commands to see four bots.

The results of the ping that just ran are already in memory, so the
breakdown costs nothing to show: one button per bot, plus one that prints
all four. Kept per live message rather than globally, so two pings in the
same chat do not overwrite each other's buttons.

### `ping_detail_button()`

A tap on one of those buttons. Renders from what the ping already
collected, so the detail agrees with the summary above it by
construction -- a second round trip would give different numbers and
invite the question of which of the two is real.

- At `await query.answer("That ping is no longer in memory — run /ping again.",`:
  Only after a redeploy, or sixteen pings ago. Say so rather than
  showing an empty report.
- At `await send_long(context, query.message.chat_id,`:
  Four full reports can outgrow one message. The file is plain text, so
  the markup is stripped rather than shown as tags.

### `_summary_lines()`

The family ping's table: one bot a line, fastest first.

Alphabetical was the order until this was asked for, and
alphabetical is the one order that hides the answer. Four numbers are
being compared precisely because one of them might be the odd one out,
and finding it meant reading all four, every time.

Sorted on what a person would actually wait for, falling back to the bus
figure for a bot too old to report one. A bot that never answered sorts
last whatever it is called: it has no time at all, and putting it first
because its name begins with A is the same mistake in a new order.

### `_deliver_ping()`

A ping's answer replaces the message that announced it, rather than
arriving underneath -- and moves to the bottom of the chat by itself if
anything was said in the meantime (see live_message.py).

- At `group["details"][trace["bot"]["id"]] = (trace, result)`:
  Kept whole, so the buttons below can print the breakdown without
  asking the bot anything a second time.

## Reading the shared database directly

### `users_command()`

No command queue involved -- this is read straight out of the shared
database, so it answers for bots that are currently down too.

- At `try:`:
  Counted now, not taken from the 15-minute job's last answer: this is the
  figure to look at when deciding whether the 220 gift can start.

_At `PEOPLE_MONTHS_SHOWN = 6`:_

How many past months the plain /people lists.

### `_people_range()`

(first day, last day, whether it is one calendar month), or None for the overview.

### `people_command()`

Unique users. Exact, counted live, for any stretch inside the activity window
(db.ACTIVITY_RETENTION_DAYS); before it, only the stored count of a whole day or month.

## Credit balances

The family charges in credits and holds a balance per person, in
family.star_balances (see family_link.py, which owns the arithmetic). Before
this the only way to look at one was Supabase's table editor and the only
way to change one was a hand-written UPDATE -- which is the sort of thing
that goes wrong at 2 a.m. and leaves no record of who did it.

Everything here goes through family_link, so an operator adjusting a balance
writes the same kind of ledger row a top-up does and the history stays
readable. There is no "just set the number" path that skips the ledger.

### `_parse_balance_change()`

`+100`, `-50` or `=0` -> ("add"/"set", amount). None if it is not one.

Deliberately three explicit forms rather than a bare number. A bare
number is ambiguous in the one direction that matters -- `/balance 12345
500` could plausibly mean "give them 500" or "make it 500" -- and the two
differ by however much they already had.

_At `BALANCE_OVERVIEW_ROWS = 20`:_

How many rows /balance shows: the largest balances in the family overview, and
the latest movements of one person. The balance page under /help prints them.

### `balance_command()`

```text
/balance                -- the family-wide picture
/balance <user_id>      -- one person, with their last movements
/balance <user_id> +100 -- give them 100 credits
/balance <user_id> -100 -- take 100 away (may go negative)
/balance <user_id> =0   -- set it to exactly 0
```

- At `await asyncio.to_thread(`:
  Into the family log too, so a change shows up in /events beside
  everything else that happened tonight.

### `addcredit_command()`

/addcredit &lt;user_id> &lt;amount> -- put ⚡ into anybody's balance by id.

The answer to "give an account 10k": not a seed that runs on its
own, but a lever to pull for any account, on purpose, with a record. It
is /balance &lt;id> +N under a name nobody has to remember the syntax of.

Recorded as a grant rather than a bonus, so it never expires, and in the
family log so it shows up in /events beside everything else.

The person is told too (2026-11-18): "The balance has been updated from x to y!", in their language,
by \_tell_credit. So is somebody whose balance /balance raises (+N, or =N above what it was); a
balance taken down tells nobody.

_At `CREDIT_NOTE_BOTS = ("downloaderbot", "convertbot", "stickerbot", "anonbot")`:_

The order the public bots are asked in. A bot can only message somebody who has started it, so the
first one that reaches the person tells them; ⚡ is spent in DownloaderBot and ConvertBot, so those
come first.

### `_tell_credit()`

Starts the chain for one change of balance; the operator hears at once if no bot took the request.

### `_queue_credit_note()`

The next bot in line, asked over the bus (family_link's tellcredit). The request is remembered by
its command id in \_CREDIT_NOTES, where result_pump finds it.

### `_deliver_credit_note()`

One bot's answer: "told" ends the chain with a line to the operator saying which bot told them;
anything else (never started that bot, blocked it, the bot is down) asks the next bot, and when
none is left the operator hears every reason.

_At `EVENTS_DEFAULT = 15`:_

/events: how many it shows when no number is given, and the most it will.

### `sql_command()`

/sql SELECT ... -- read-only, against the whole shared database, so
joins across two bots' schemas work. The connection itself is set READ
ONLY, so Postgres rejects a write even if the keyword check below is
fooled.

## Backups

_At `BACKUP_PART_BYTES = int(os.environ.get("MBOT_BACKUP_PART_MB") or 49) * 1024 * 1024`:_

What one document may be. The cloud Bot API refuses a bot's upload over
50 MB, and the multipart request around the file costs a little of that.

### `_backup_parts()`

(start, end) byte ranges of at most `limit` bytes, covering `size`.

### `_build_backup()`

The whole database as family_db's archive, and what the caption says about it.

### `_in_production()`

Running on Railway (the variables it sets in every service).

### `_for_telegram()`

What goes to Telegram: (file, its numbers, its name, a line about it). Encrypted to
the operator's offline key when BACKUP_PUBLIC_KEY is set (offline_key.py), so the copy
is safe in a chat, a forward or a stolen account, and only the private key in the
operator's password manager opens it. Without the key it goes as it always has. A key
that is set but does not parse raises: a copy is not sent plainer than asked.

- At `if _in_production() and (os.environ.get("MBOT_ALLOW_PLAIN_BACKUPS") or "").strip() != "1":`:
  In production a copy of everybody's data never goes to a chat in the clear unless it
  was asked for in so many words: a Telegram account can be stolen, a file forwarded.

### `_deliver_backup()`

Send `archive` to `chat_id` and pin it: (pinned, parts, what went wrong or None).

Pinned with no notification, and nothing in this bot ever unpins -- so
every backup stays one tap away at the top of this chat, the newest and
all the ones before it. Over BACKUP_PART_BYTES it goes as numbered parts,
each pinned, joined back with `copy /b` or `cat`.

### `backup_command()`

The whole shared database, every schema, as the archive family_db.py
restores from -- sent here, and pinned.

Lossless: each table is Postgres's own COPY into CSV, so binary columns,
NULLs, timestamps and JSON come back exactly, compressed with DEFLATE and
nothing thrown away. MANIFEST.json names every table's columns, which is
what lets a restore line up against a database that has gained columns
since. It replaces the old zip, which summarised binary columns and could
be read but not restored from.

The same copy arrives by itself after every nightly report (\_nightly_reserve_copy)
and after every accepted /shutdown (shutdown_copy).
private/runbooks/reserve-copies.md says how to restore from it.

## Reserve copies (2026-11-03) -- reserve_copies.py says what each is

_At `_RESERVE = {"mark": None, "written_at": None, "tried_at": None, "problem": None}`:_

How the volume's `latest` copy is doing, for the once-a-minute job below and
the nightly caption. `problem` is what the operator was last told went wrong,
so a broken volume is reported once, and its recovery once.

### `_whole_copy_to_operators()`

The whole database to every admin, pinned, encrypted to the offline key when
its public half is set; and, through `keep`, what goes on the volume beside
it. The nightly copy and the copy at shutdown are this one function with a
different heading and a different `keep`. Never raises: a failure is told to
the operator.

Every copy sent this way is the whole database, so it holds everything the
volume's `latest` copy keeps, and the logs `latest` leaves out besides
(tests/test_reserve.py checks that the first is inside the second).

### `_keep_nightly()`

The nightly copy also goes on the volume (`nightly`), and erasure notes
older than the promise are dropped there and from the table. The caption
says how old the volume's `latest` copy is.

### `_nightly_reserve_copy()`

The whole database after the nightly report.

_At `SHUTDOWN_COPY_SECONDS = 30`:_

Asked for on 2026-11-18: one more copy at every accepted /shutdown, on top
of the nightly one. A service going down on purpose is the moment its
database is most worth having in hand. The copy is taken
SHUTDOWN_COPY_SECONDS after the answer arrives, by when the stopped bots
have written their last rows (they stop lifecycle.STOP_DELAY_SECONDS after
accepting, and nobody was using them). ManagerBot runs in a service of its
own and is never stopped this way, so it is still there to take the copy.

### `_stopped_since()`

The public bots whose stopped-on-purpose note
(lifecycle.STOPPED_NOTE_PREFIX) is newer than `since`.

### `_after_a_stop()`

Called by result_pump for every answer. A stop was accepted when the bot it
was sent to has written its stopped note since the command was queued (a
minute's grace for the two clocks); a refusal writes none, and neither does
a restart. `softstop` stops a whole service, so every bot with a fresh note
is named; `softstopbot` names its own bot. Stops that arrive close together
share one copy.

### `_keep_latest_now()`

At shutdown the volume's `latest` copy is written again at once, rather than
within reserve_copies.MIN_SECONDS, so the copy on the volume and the one in
the chat show the same moment.

### `shutdown_copy()`

The copy at shutdown, headed with the bots it followed.

### `keep_reserve_current()`

Every minute: if anything in `latest` changed, and the last copy is at least
reserve_copies.MIN_SECONDS old, write it to the volume again.

## Problem reports

### `reports_command()`

/reports [n] [mine] -- every problem the public bots have shown anybody,
newest first.

It used to list only the ones somebody chose to report, which made it a
list of how many people could be bothered rather than a list of what is
going wrong. Since 1.7.0 each bot records a problem the moment it shows
it, so this is the whole picture; `mine` narrows it to the ones somebody
attached their own details to, which are the ones worth a reply.

A row here carries nothing about anybody. The details a person volunteered
are read one at a time with /report &lt;incident>, which is the point: they
are looked up on purpose, not pushed at the operator in a list.

### `report_command()`

/report &lt;incident> -- one problem in full, including whoever
volunteered to be named on it.

This is the only place the details a person sent are shown, and it takes a
deliberate command to get here. The alert that says a report arrived does
not carry them: the standing rule is that nothing arrives at the operator's Telegram identifying a user, and somebody consenting to be identified buys the operator the ability to look, not a notification they did not ask for.

## The nightly problem report

Everything the four bots showed anybody in the last day, in one message, at 22:00 Suwon time (midnight until 2.1.0): asked for as a full answer to the report command, sent automatically at the end of the day.

It is laid out by how much each line is worth reading, which is what the
levels are for:

```text
urgent     listed one by one, with their incidents. These have already
           been messaged as they happened -- this is the recap, and seeing
           the same three incidents again is the point, not a duplicate.
faults     grouped by bot and code with a count, because "DL-ALL-ROUTES
           x9" is the useful shape and nine separate lines is not.
explained  the bot was right and said why. Until 2.1.0 this was one line,
           "Refused — 2", which told the operator somebody had been turned
           down when what had happened was two people forgetting to
           swipe-reply. Now each KIND (problems.KINDS) is named for what
           it is, with its codes, their titles and their counts -- never
           their incidents, which nobody needs for these.
```

Then the feedback, when it has been asked for here rather than as it
arrives (/feedback).

A day with nothing in it still sends, briefly. "Nothing broke today" is
information when it arrives every day at the same time; it is only noise
when it is the only thing that ever arrives, and the two lines above make
sure it is not.

_At `MAX_CODES_PER_KIND = 6`:_

Codes listed under one kind before the rest are summed into a line.

### `_row_icon()`

🚨 / ⚠️ for the first two levels; for the third, its kind's own.

### `_group_problems()`

{level: {(bot, code): [rows]}}, newest first within each group.

_At `ROUTES_PREFIX = "routewatch:"`:_

DownloaderBot's daily route check (downloader_bot/routewatch.py) keeps what it
found in family.settings: "routewatch:last_run" = when, and
"routewatch:state:&lt;platform>" = "&lt;state>|&lt;since>|&lt;alerted>". It pushes only a
platform's trouble starting, and never the unadvertised site's; every platform's standing, that site's included, was asked for in the 22:00 report instead (2026-10-07).

_At `ROUTES_STALE_S = 2 * 86400`:_

The check runs once a day; past this, the line says the check itself stopped.

### `routes_section()`

The report's download-routes lines, from the route check's settings. Pure.

### `_brief_line()`

The code's own plain-words line, under it: what the person ran into.

### `build_problem_report()`

The message. Pure, so tests/test_reporting.py can read it without a
database or a Telegram. `feedback` is what to show in full, and
`feedback_note` a line to say instead when it has already gone out.
`routes` is the route check's settings (see routes_section); None leaves
the section out. `people` is the lines of people_section, first under the
title; None leaves them out.

_At `PEOPLE_AT_LAST_REPORT = "nightly:people"`:_

The people lines at the top of the nightly report (2026-10-28: the change in the total number of unique users, and the number of unique users in the last 24 hours). The
all-time figure is db.unique_people, the same count /users and the gift use; what the last
scheduled report counted is kept in ManagerBot's settings as two plain numbers, so the change
is "since the last nightly report". Nothing about who is kept.

### `people_section()`

The report's people lines. Pure. `before` is (total, last 24h) at the last scheduled
report, or None before the first one.

### `_people_report()`

people_section for now. `remember` (the scheduled report only) keeps tonight's numbers
for tomorrow's change; /nightly on demand only reads them.

### `nightly_problem_report()`

The report, then the night's reserve copy (2026-11-03: the nightly report also makes a backup copy).

### `_nightly_report()`

- At `feedback, note = [], None`:
  Whatever feedback nobody has been sent yet goes out here, in either
  mode: in "nightly" that is all of it, and in "instant" it is only what
  arrived while alerts were muted, which this is the place to catch.
- At `text = build_problem_report(rows, NIGHTLY_REPORT_HOURS, routes=routes, people=people)`:
  A long day of feedback goes in messages of its own rather than
  pushing the problems past one message, where the cut could land
  inside a tag and lose the whole report.
- At `await notify_owner(context, text[:TELEGRAM_MAX_CHARS])`:
  A very bad day can outgrow one message. The head still arrives as a
  message rather than only as an attachment, because a day that bad is the
  one where a file nobody opens is the worst possible outcome.

## Feedback

What people send with /feedback in the four public bots (shared_features),
delivered here: each one as it arrives, or all of them with the 22:00
report. 2026-10-03: a notification for each piece of feedback, and a switch between that and all of it grouped in the nightly report. Instant is the default.

A piece of feedback carries the words, the bot and the language -- nothing
about who wrote it, under the standing rule about what reaches the operator unasked. Which makes it one-way, on purpose: offered a way to reply, the decision was feedback that goes one way only -- just the text, copied and sent by the bots -- and is said to be read-only wherever it is mentioned. So every place it shows here says "read only", and every place a
public bot mentions /feedback says nobody can write back.

_At `FEEDBACK_SHOWN_CHARS = 3000`:_

A message holds 4096 characters; one piece of feedback can be that long by
itself, so what is shown of one is capped and /feedback &lt;id> shows the rest.

_At `FEEDBACK_LIST_ROWS = 10`:_

Rows /feedback lists, and how far back /feedback &lt;n> looks for one piece.

### `_feedback_mode()`

"instant" or "nightly", read at most once a minute -- the event pump asks
on every sweep, and the answer changes when a button here is tapped,
which updates this as well.

### `_feedback_block()`

One piece of feedback as HTML: where from, when, then the words.

### `_send_feedback()`

Items packed into as few messages as fit, never split inside one.

### `_deliver_feedback()`

Each new piece as it arrives, in "instant" mode. Called from the event
pump after the events, so it is held while alerts are muted (and goes out
with the nightly report if they still are).

### `feedback_command()`

/feedback -- the latest, and the switch. /feedback instant|nightly sets
it; /feedback &lt;id> shows one piece in full.

### `feedback_mode_button()`

The switch under /feedback.

_At `PROBLEMS_MAX_HOURS = 168`:_

The furthest back /nightly will look, in hours (a week).

### `nightly_command()`

/nightly [hours] -- the report that arrives at 22:00, on demand.

- At `note = None`:
  On demand it only counts the feedback: taking it here would mark it as
  sent, and the 22:00 report would then leave out what it was asked for.

### `decode_command()`

/decode &lt;code> -- what an error code means, from problems.py, the same
registry the public bots code their messages from. Any unambiguous part of
a code works: /decode reddit. A code renamed in 2.2.0 is read under its old name too
(problems.RENAMED).

## Alert toggles

## The watching itself

### `_alerts_on()`

- `return True` — if the setting can't be read, err towards telling the operator

### `startup_rollcall()`

One message, once, a few seconds after ManagerBot starts: who is up.

This answers "the family has just started -- did it work?" -- a question that previously had no answer except typing /status. The watchdog only
speaks on \*changes\*, and deliberately says nothing on its first pass
(announcing four bots as up every time ManagerBot restarts would be noise);
a roll-call is the one moment where the current state, changed or not, is exactly what is wanted.

Deliberately not five messages. Each bot could announce itself -- they
already write a startup event -- but starting the family would then mean
five notifications arriving in a random order over ten seconds. Waiting
once and reporting the whole family together is the same information read
in one glance.

Runs on heartbeats, not on process management: ManagerBot never starts,
stops or supervises the other four, and does not need to in order to say
what happened. That is what makes this work identically whether the four
are on this laptop, on Railway, or split across both.

- At `for bot in CHILDREN:`:
  Seed the up/down table with what was just reported. Without this the
  watchdog's first pass treats every bot as newly discovered and can
  repeat the bad news twenty seconds later; from here on it only speaks
  when something actually changes, which is its job.

_At `_db_failures = 0`:_

Consecutive failures of ManagerBot's own database connection. The operator is told once, not once a minute, and told again when it recovers -- this is
the one failure that would otherwise be completely silent, since every
other alert path in this file runs through that same database.

### `_is_redeploying()`

True while a bot's own goodbye note is still fresh.

### `_stopped_on_purpose()`

True when the bot's last words were a soft shutdown (its note is
cleared when it starts again: lifecycle.on_start).

### `watchdog()`

- At `await asyncio.to_thread(db.set_known_state, bot["id"], is_up)`:
  First sighting -- record it silently. Announcing "StickerBot is
  up" the first time ManagerBot ever runs is noise, not news.
- At `await asyncio.to_thread(db.set_known_state, bot["id"], False)`:
  Down because it was asked for: said once, as what it is, not as an outage.
- At `logger.info("%s is stale but was shut down on purpose -- redeploying.", bot["id"])`:
  Deliberate, and recent. Leave known_state alone so that the
  "it is back" message is not announced either -- a redeploy started on purpose should be silent at both ends. If it does not
  come back, the note ages out and the next pass alerts.

### `event_pump()`

Forwards what the bots themselves flagged. Info-level events are
recorded but not pushed, except payments -- a donation arriving is the
one piece of good news worth interrupting someone for.

- At `return`:
  Deliberately before claiming anything: take_unnotified_events marks
  rows as sent, so claiming while muted would silently eat them.
- At `family_link.mark_bus_active()`:
  A crashing bot tends to report several things at once; stay on the
  fast cadence long enough to catch the rest.
- At `await _deliver_feedback(context)`:
  Feedback rides the same sweep, after the events and under the same mute.

_At `_last_pumped = {"result": 0.0, "event": 0.0}`:_

How long ago each pump actually swept. The interval is chosen from whether
the bus is active (family_link.bus_is_active(), set when ManagerBot queues a
command): fast while an answer could be coming back, slow once things have
gone quiet -- the same adaptive poll the child bots run for the command
queue, and the whole delivery mechanism now that there is no push.

_At `_pump_tasks: dict[str, "asyncio.Task"] = {}`:_

The sweep runs as a task of its own, so the 1 s tick that decides \*whether\* to
sweep returns at once. Awaited inside the tick, a sweep of two database round
trips (events, then feedback) that outlasted one second made the scheduler
skip every following tick with "maximum number of running instances" -- about
2,800 warning lines a day in production, and the same line a real stall
prints. Now a skip can only mean the tick itself is stuck, and a sweep that
really is stuck is named here instead.

_At `SWEEP_WINDOW_SECONDS = 900`:_

How long each sweep took, for the /status board: the slowest in the last
15 minutes and the slowest since start, so a sweep that is slow every time
(the database round trip from Railway) can be told from a one-off. In memory
only -- a restart starts it again, nothing is written to the database. Per
pump key, (finished at, seconds) on the monotonic clock. Only the event loop
touches these (the done callback and \_sweep_line run on it), which is why
the board gets its line passed in rather than reading these from its thread.

### `_note_sweep()`

Done callback of a sweep task: it ended, however it ended (finished,
raised or was cancelled), so it is counted. Never raises.

### `_sweep_line()`

The /status line about how long the sweeps take. Called on the event
loop, not in a thread: the deque is mutated by the loop.

### `_pump_if_due()`

- At `if (now - _pump_started[key] > PUMP_STALL_WARN_SECONDS`:
  Still sweeping: nothing to add, never a second sweep on top of it.
- At `_pump_tasks[key] = context.application.create_task(pump(context), name=f"{key}-sweep")`:
  The application owns the task: it keeps it alive, waits for it on
  shutdown and sends an exception from it to the error handler.

### `result_pump()`

Delivers whatever the child bots wrote back, and gives up on commands
nobody claimed.

- At `family_link.mark_bus_active()`:
  More may still be trickling in (a /ping to everything answers over
  a few seconds); keep sweeping at the fast cadence for a bit.
- At `await _after_a_stop(context, result)`:
  An accepted /shutdown also asks for a copy of the database (shutdown_copy).
  A failure there is logged and the answer is still delivered.
- At `credit = _CREDIT_NOTES.pop(result["id"], None)`:
  An answer to a credit note goes to \_deliver_credit_note, never to the chat as a raw result.
- At `trace = _PING_TRACES.pop(result["id"], None)`:
  A /ping is answered by rewriting the message that announced it,
  with the whole round trip broken down -- not by a second message
  underneath. Checked before reply_chat_id because the trace already
  holds the message it is going to become.
- At `group = _FANOUTS.pop(result["id"], None)`:
  A command asked of every bot at once rewrites one message rather
  than sending four, for the same reason a /ping does.
- At `target = resolve_bot(result["target_bot"])`:
  Display name, not the raw queue key: the acknowledgement this is
  answering said "StickerBot", and following it with "stickerbot"
  reads as a different thing having replied.
- At `if not result["ok"] and body.startswith("Unknown command"):`:
  The pre-flight check in \_dispatch catches the version gap for every
  command in COMMAND_SINCE. This catches the rest: a command added to
  the bus without being listed there, or a bot whose version string
  could not be read. "Unknown command" on its own reads as a fault;
  with the two version numbers beside it, it reads as a deploy.

## /start, /help

_At `HELP = f"""👪 <b>ManagerBot</b> — the family's manager.`:_

An f-string for the numbers it quotes; {{bots}} stays a placeholder for start_command.

## /help &lt;name> and the buttons under /help: a page per command

2026-10-17: each command that is complex or has options gets a page, with a detailed explanation and one simple example. 2026-10-18: it works the way the ping command does -- so /help carries one button per page, a
tap sends the page as a new message, and /help pause is the same page.
Every number on a page is read when the page is shown, from the code that
enforces it, so a page cannot drift from the command it describes. Plain
text, because the pages contain < and >, and with link previews off.

_At `REPORTS_LINE = ("/reports [n] lists the latest problems, newest first, each with its "`:_

One sentence on /reports, for the nightly page of /help: the same facts as its line in HELP.

### `_help_page_nightly()`

- At `daily = (f"The same report arrives by itself every day at {NIGHTLY_REPORT_AT_UTC} UTC."`:
  MBOT_NIGHTLY_UTC left blank turns the daily report off.

_At `HELP_PAGES = {`:_

Name -> function() -> text, in the order /help lists them (a dict literal with
string keys: tests read it with ast). An alias is a name that also answers:
the old and the paired commands show the page of the one they belong with.

### `help_page_name()`

The page `/help <word>` asks for, or None. Only the first word is read:
lower case, a leading "/" and anything from "@" on dropped, then a leading
"help\_" (so /help help_pause and /help pause@Bot both work), then the aliases.

### `help_page_text()`

One page, then the way back to /help.

### `_help_keyboard()`

One button per page, two to a row, under /help and /start -- the way
/ping has one per bot. A tap sends the page (help_page_button).

### `help_page_button()`

A tap on one of those buttons: the page arrives as a new message, so the
help above it stays to be tapped again.

- At `await query.answer("That help page no longer exists. Send /help.", show_alert=True)`:
  A button from before a redeploy that dropped the page.

### `crashtest_command()`

Proves the crash path, the errors.log and the family alert all really
fire -- for ManagerBot itself, or for any child bot by name.

Naming a bot is the more useful direction and the one that was missing.
ManagerBot crashing shows that ManagerBot's own reporting works, which is
visible as it happens. What is actually worth knowing, when a bot has gone
quiet, is whether \*that\* bot would have said so -- and that is the one test
that could not be run from here.

## Running an update without ambushing anybody

Four commands, in the order they are meant to be used:

```text
/pause           the bots stop starting work a restart would throw away,
                 and tell whoever asks how long they expect to be. Anyone
                 turned away is written down.
/warn            everyone already mid-something hears that it is about to
                 be reset -- before the deploy, not as the door closes.
...deploy, as many times as it takes...
/finishupdates   the bots reopen, and everyone who was turned away is
                 told they can try again.
```

/broadcast is the odd one out and belongs here anyway: it is the same
"speak as the bot they actually talk to" mechanism, for anything the operator wants to say that these four sentences do not cover.

Nothing here is on a timer. An update is usually several deploys, and a
pause that expired on its own would let the bots reopen between two of
them -- which is the exact window this is meant to close.

### `_targets()`

The bots a command applies to. No name, or "all", means every child --
ManagerBot is never a target: it is the one doing the asking, and it has
no users to announce anything to.

### `_fan_out()`

Queue one family command to several bots and say so once.

Deliberately not a progress report: each bot answers in its own time and
its answer arrives on its own, the way every other /run result does.

### `pause_command()`

/pause [bot|all] [minutes] — stop taking new long work, and say why.

### `warn_command()`

/warn [bot|all] — tell whoever is mid-something that it will be reset.

### `finish_updates_command()`

/finishupdates [bot|all] — reopen, and go back to everyone turned away.

Explicitly the operator's call and never automatic: one update is usually
several deploys, and only the person doing them knows which one was the
last.

_At `BROADCAST_PENDING = "broadcast_pending"`:_

---- /broadcast, which asks before it speaks ------------------------------
Every other command here affects people who are already mid-something with
a bot. This one reaches everyone the bot has ever met, cannot be recalled,
and is one fat-fingered bot name away from going to the wrong audience. So
it is the one command in ManagerBot that asks twice.

### `broadcast_command()`

/broadcast &lt;bot|all> &lt;text> — one message to everyone, as that bot.

- At `aimed = text.split(" ", 1)[0] == "--active"`:
  "--active" first in the text aims it at recent users. The child bot reads
  the flag, so it stays in what is queued; the card shows the words without it.

_At `GIFT_PENDING = "gift_pending"`:_

---- /gift, which counts before it gives ----------------------------------
Bonus credit for everyone a bot knows, once per person across the family
(family_link.grant_bonus_once). Like /broadcast it reaches everyone and cannot
be taken back, and it is money: so the first step only asks each bot to count,
and the giving waits for a button.

Only the latest /gift waits, and its buttons carry its own nonce
(gift:go:&lt;nonce>): a tap on an older card -- another key, another amount --
must never give the newer gift, nor cancel it.

- `GIFT_DONE = "gift_done"` — the nonce of the card last given or cancelled

### `gift_command()`

/gift &lt;bot|all> &lt;key> &lt;credits> &lt;last day YYYY-MM-DD>

### `gift_button()`

- At `action, _, nonce = query.data.removeprefix("gift:").partition(":")`:
  The check and the pop are one synchronous stretch, so two taps cannot both
  give. A card that is not the waiting one's (an older /gift, or a card from
  before the nonce) leaves the waiting gift alone.
- At `return`:
  A second tap on the card just given or cancelled: its text already
  says what happened, and "no longer waiting" over "Giving" would read
  as if nothing had been given.
- At `text += (f"\n\n⚠️ Could not ask {', '.join(failed)} (a database error, see "`:
  Safe to repeat: a bot gives a key only to people who have not had it.

_At `PROMO_COUNT_SECONDS = 15 * 60`:_

---- the 220 ⚡ gift: the count, the operator's notice and /promo -------------
2026-10-18: a unique-user counter across all the bots, a message to the operator when it reaches the mark, and one short command that starts the gift. This one gift is not general (it is used once and then it is gone): family_link keeps the start
as a row written once, ever, and everything below only asks it. A job counts
the people every 15 minutes and tells the operator the first time the mark is reached; /promo shows where the gift stands and, once, starts it; /promo stop
ends the claiming early. Every state /promo shows is read fresh from the
database, never from a cache, because the operator acts on it.

- `PROMO_PENDING = "promo_pending"` — the nonce of the card waiting for a tap

- `PROMO_DONE = "promo_done"` — the nonce of the card last started or cancelled

_At `_promo_count: dict = {"count": None, "at": None}`:_

What the 15-minute job last counted, {"count", "at"}, for anything that wants
the figure without a database trip. /users and /promo count afresh instead.

### `_promo_last_day()`

The last day somebody can claim, from the midnight claiming closes at.

### `count_unique_people()`

The 15-minute job: count the people across all the bots, and the first
time the count reaches the mark, tell the operator once.

The notice is sent first and the milestone written only after it went, so a
failed send is tried again at the next count. (Two ManagerBots overlapping
during a deploy could both send it; that is harmless, and the milestone is
still written once.) Any error is a warning naming the kind and nothing
more: the next count tries again.

- At `started = await asyncio.to_thread(family_link.promo_window, True)`:
  Already started (the count was seen in /users and nobody waited for this): "start it with /promo" would be stale, so only note it.

### `_promo_card_text()`

The card, in HTML. Everything on it is a number or a date from the code
except the ad itself, which is HTML on purpose (its last paragraph is in
italics), so the rest is escaped and the ad is not.

### `promo_command()`

/promo -- where the 220 ⚡ gift stands, and once the mark is reached a
card to start it; /promo stop -- end the claiming early.

### `promo_button()`

A tap on the card. Like gift_button, only the waiting card's nonce counts,
and the check and the pop are one synchronous stretch, so two taps cannot
both start it; family_link.start_promo is once-only in the database besides.

- At `return`:
  A second tap on the card just started or cancelled: its text already
  says what happened.
- At `try:`:
  Look again now: the card may be minutes old, and the gift may have been
  started from another card, or the count may be what the card said.

_At `BOT_COMMANDS = [`:_

The menu the operator sees, in the order HELP lists them: watching, reaching
into a bot, shipping an update, then the family-wide reads. It is a private
bot with one user, so a command left out of the menu is a command that has to
be remembered instead -- which is how /probe and /stars went a version
without being typed once, and how /providers went missing again in 2.1.0
(2026-09-29: "/pro" typed, and /problems alone offered).

What is left out on purpose is only what is a second name for something in
the list, or would do harm when tapped by accident: see UNLISTED in
tests/test_menu.py, which holds this list to that.

A description is kept under 50 characters. Telegram's limit is 256, but the
menu is a popup, and a long line is cut where the window ends -- a screenshot of /problems read "...the midnight report" with the rest gone
(the report has been at 22:00 since 2.1.0).

_At `BotCommand("status", "Every bot: up, version, errors, users"),`:_

Grouped by what the operator is in the middle of, because that is how it is
read at four in the morning: is everything alright, do something to a
bot, talk to somebody, money, the database.

Gone from here, still working, because they are second names: /me (its
own status, which /status already carries), /errorlog, /botlog and
/problemlog (/logs asks which), /finishupdates (/resume), and since 2.2.0
/problems, the nightly report's name from 1.7.0 (/nightly is its name
again: it fits better). Not /reports, which was looked for in the menu and not found (2026-10-10): it is its own command, the
raw list of recent problems with their incident codes, which /report
&lt;code> then opens. It was written off here as "/problems now" and so left
out of the menu for three versions, while /report beside it was offered
(found 2026-10-10). And /crashtest, which makes a
bot raise on purpose and page the operator -- a command a stray tap on the
menu should not be able to send. /help names every one of them.

/providers, /probe and /stars are here: each reaches the one bot that
has it (/run reaches them too, but only by naming the bot and the
command), and they were dropped in 1.7.0 as "reachable through /run",
which is how a command becomes one nobody is offered.

_At `STRANGER_COMMANDS = [`:_

And what anybody else gets, which is the truth: one command, and it says no.
The list above was going up at the default scope, so a stranger who found
the private bot was handed a menu reading /sql, /backup, /restart. Every one
of them refuses them -- guard() is the thing that decides, not the menu --
but a list of the levers is an invitation to lean on them, and there is no
reason to publish it.

### `_publish_commands()`

The full menu in each admin's chat, and one line for everybody else.

Never raises. set_my_commands for a chat Telegram has never seen fails,
and an admin who has not opened the bot yet is exactly that chat --
a cosmetic call is not a reason to fail a deploy. Same shape as
shared_features.publish_commands() in the four public bots; separate
because ManagerBot's default scope is a refusal rather than a shorter menu.

- At `from telegram import (BotCommandScopeAllChatAdministrators, BotCommandScopeAllGroupChats,`:
  Scopes nothing here writes, cleared so they cannot outrank the ones it
  does -- see \_UNOWNED_SCOPES in the public bots' shared_features.py.

_At `SHORT_DESCRIPTION = "A private bot. It looks after one person's other bots."`:_

The two lines a stranger meets before they press anything: the blurb beside
the bot in search results, and what an empty chat shows above the Start
button. The four public bots have had these since v1.4.0 and ManagerBot was
skipped, on the reasoning that a private bot with one user has no audience
for them. That is the wrong way round -- the operator is the one person who never reads them, and the stranger who has just found a bot called
"mumu manager" is exactly who they are for. Untranslated, like everything
else here: whoever is reading them is not a user of this bot.

### `_description()`

Read off USERNAME_ALIASES rather than typed out again, so a bot that
gets renamed is renamed here too. ManagerBot itself is left out: pointing
a stranger at the bot they are already looking at helps nobody.

### `_publish_profile()`

Never raises, same reasoning as the menu above.

### `main()`

- At `raise SystemExit(`:
  Every other bot treats an empty admin list as "disable the admin
  commands". Here that would leave the entire bot open, so it is a
  hard stop instead.
- At `app.add_handler(TypeHandler(Update, track_activity), group=-3)`:
  Its own group -- see the note in the child bots' main(): a TypeHandler
  on Update matches everything, so anything sharing a group with it never
  runs. ManagerBot has nothing else up here today; the numbering is what
  keeps that true when it does.
- At `app.add_handler(CommandHandler(["nightly", "problems"], nightly_command))`:
  /nightly is the name (again, since 2.2.0); /problems was its name from
  1.7.0 until then, and still works. /reports is not a name for it: it is
  the raw list of recent problems with their incident codes.
- At `app.add_handler(CommandHandler("providers", _shortcut("providers", 0, "", default_bot="d …`:
  The bot-specific ones. Each defaults to the only bot that has it, so
  the command centre does not require remembering which bot has what --
  /providers and /stars are unambiguous, and typing the bot name is
  still allowed for when that stops being true.
- At `app.add_handler(CommandHandler(["resume", "finishupdates"], finish_updates_command))`:
  /pause stops the bots for an update, so its opposite should be called
  /resume rather than /finishupdates -- which is also what every child bot
  calls it on the bus. The old name keeps working.
- At `raise SystemExit(`:
  Every other bot degrades quietly without one (family_link just stays
  off). ManagerBot cannot: the watchdog, the alert pump and the command
  results are all jobs, and without them it would sit there looking
  healthy while watching nothing at all.
- At `app.job_queue.run_once(startup_rollcall, when=STARTUP_ROLLCALL_SECONDS)`:
  Before the watchdog's first pass, so the roll-call is what seeds
  family.bot_state and the watchdog has nothing left to announce.
- At `app.job_queue.run_repeating(count_unique_people, interval=PROMO_COUNT_SECONDS,`:
  The 220 gift's counter: the operator is told once when the bots reach the mark.
- At `app.job_queue.run_once(result_pump, when=0, job_kwargs=family_link.RUN_LATE)`:
  One sweep of each at startup -- however late the job queue actually
  starts (see family_link.RUN_LATE) -- to clear anything a previous run
  left queued before the first tick falls due.
- At `family_link.attach(app, BOT_NAME, DISPLAY_NAME, START_TIME)`:
  ManagerBot rides the same bus it runs: it heartbeats like everyone else,
  so a second ManagerBot (or a plain SQL query) can see whether it is alive.
- At `app.run_polling(**lifecycle.polling_kwargs(`:
  A 30-second long poll is the same latency as the default 10 -- Telegram
  answers the moment an update exists -- for a third of the HTTP requests.
  ManagerBot has one user, so nearly every request it makes all day is an
  empty poll.
