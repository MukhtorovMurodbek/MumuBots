# Tests

Two sets, and the difference between them is what they need to run.

**`python tests/run.py`** needs nothing at all -- no Postgres, no network,
no ffmpeg, and not even the bots' own dependencies, because
python-telegram-bot, psycopg, Pillow and httpx are stubbed in `tests/stubs`.
It runs on any Python from 3.9 up. It covers the machinery added in v1.0.1:
evolving messages, state across restarts, and the `/ping` report -- plus,
for all five bots, that every module imports and that `main()` wires up both
with and without persistence. It is the one to run first, and the one to run
on a machine that has nothing set up.

`test_startup_jobs.py` joined it in v1.1.2, for the same reason and with the
same shape. Every `run_once(..., when=0)` in the family is scheduled before
the job queue starts, and APScheduler silently drops a job more than a second
late — which is how the old LISTEN listener stayed dead on every bot for two
months without a single error. That listener is gone as of v1.1.4, but the
hazard is not: the surviving immediate jobs (ManagerBot's startup result and
event sweeps) would fail the same silent way. The test asserts every
immediate `run_once` is told to run however late it is.

`test_resolvers.py` joined it in v1.2.1, when DownloaderBot stopped having
one way to fetch a link and started having a chain of them. What it checks is
behaviour under failure, which is the state nothing exercises on a good day
and everything depends on during a bad one: that a dead provider does not end
the chain, that repeated failures move a provider to the back rather than
removing it, that a chain whose every member is resting still tries every
member -- the difference between "Instagram is slow today" and "the bot is
broken" -- and that yt-dlp is last in every chain, because it is the one route
that goes out from the container's own address, which is the thing the sites
object to. No network: the providers are two local functions, one that always
works and one that never does.

`test_pause_gate.py` joined it in v1.1.1 and is the odd one out: it reads the
source rather than running it, and asks one question of each child bot --
does `bot.py` call `refuse_new_work()` anywhere at all. AnonBot's answer had
been no since the command shipped, which meant `/pause` reached it, set the
flag correctly, and stopped nothing. That is invisible in review and in the
logs, and shows up only as a message somebody still received.

---

## The simulator, and the AnonBot scenarios

**`python tests/anon_scenarios.py`** — 213 checks, and the only suite that
drives a bot the way a person does. Needs Postgres; no network.

It runs on `tests/sim.py`, which is a Telegram server inside the test
process. `sim.SimBot` is a real `telegram.Bot` with its one network seam
(`_do_post`) pointed at `sim.FakeTelegram`, which keeps chats, message ids,
reply anchors and inline keyboards the way the real service does — message
ids counted **per chat**, which is the property AnonBot's whole routing
table rests on. AnonBot's own `main()` builds the real `Application`, real
`Update` objects go in the top, and what comes out is what somebody's chat
would actually contain, in order, with the buttons.

That is the difference from `anon_flow.py`, which stubs Telegram one call at
a time and calls the handler it wants to test. Everything that lives
*between* handlers is invisible to that approach: handler order, a `pattern=`
that does not match, a group that swallows an update, a reply anchored to an
id that is correct in one chat and meaningless in the other. On its first
full run this suite found that python-telegram-bot runs **one handler per
group**, and that three handlers in the family had been unreachable for
months because they shared a group with a `TypeHandler` on `Update`. Both
suites are kept: `anon_flow.py` pins the routing rules, this one pins what
the user sees.

Groups A–P, runnable one at a time (`python tests/anon_scenarios.py c f`):

| | |
|---|---|
| A | first contact — `/start`, the language picker, `/link`, unknown commands |
| B | one conversation, both reply routes, both directions |
| C | three people at once — two Reply prompts open, answered out of order |
| D | one guest writing to two different inboxes |
| E | the reply requirement — what happens when a message names nothing |
| F | media, captions, albums, an overlong message |
| G | block and unblock |
| H | edits, a deleted reply anchor, a guest who blocked the bot |
| I | pause, resume, new link |
| J | flood control, both ceilings |
| K | `/cancel` |
| L | hostile input — markup, SQL-shaped tokens, forged callback data |
| M | the three handlers that were never reached |
| N | a link tapped, then a reply to something else |
| O | `/conversations`, archiving, going to the start of one, `/which` |
| P | which bubble a delivered answer hangs under, for every content type |

Every group starts from empty tables and a fresh `Application`, so a failure
cannot cascade and message ids start at 1 in each chat — which makes the
transcripts (`person.transcript()`) readable and the anchors checkable by
eye. The suite also collects everything the error handler is asked to report
and asserts at the end that the list is empty: an `AttributeError` raised
deep in a handler is otherwise invisible to a test, because PTB catches it
and the user simply gets no answer.

Group P joined in v1.6.0, with the bug it was written for. An answer to a
message that was not the newest one in the conversation arrived on the other
side attached to whatever *had* been said last, because the only anchor the
relay had was "the newest bubble of this conversation in that chat". It is
checked for every content type a person can send, because the number of
bubbles one relayed message becomes depends on whether the type can carry a
caption — a sticker cannot, so it arrives as a header line plus a sticker,
and from then on the two chats are different lengths. `sim.py` grew
`Person.media()` and `sim.MEDIA_PAYLOADS` for it: before, everything that
was not text was sent as a photo, so the case that breaks was unreachable.

`sim.py` is not AnonBot-specific. Any bot in the family can be driven through
it with `sim.build(bot_module, tele)`.

**`python tests/credit_scenarios.py`** — 78 checks on the family's only
money arithmetic, against a real Postgres: the lifetime ladder (a person's
first 500 Stars at three times the ordinary rate, the next 500 at twice it,
priced piece by piece across a step, and the same whether paid at once or in
pieces), two payments at the same moment not both priced as somebody's first,
bonus credit expiring while ordinary credit never does, spending taking the
bonus first, corrections capping the bonus, that nothing takes a payment back,
and the ledger adding up from zero row by row. Needs Postgres; uses three
scratch user ids in the shared family tables and removes them afterwards.

It pins the multiplier, the ladder and the expiry in its own environment rather than
reading them from the deployment, because the assertions are about the
arithmetic: a suite whose expected numbers move with a pricing change is a
suite that tells you nothing.

**The scripts below** are the older suite and they need a real Postgres.
They cover the bots' own logic, which `run.py` deliberately does not.

---

## The rest

No framework, no dependencies past what the bots already need.
They exist because v0.6.0 rewrote every database call in the family onto a
connection pool and collapsed a lot of one-query-per-bot loops into single
statements — the kind of change that is easy to get subtly wrong and hard to
notice until a user hits it.

Run them from the repo root, in this order or individually:

```bash
python tests/smoke.py
```

Every bot's `db.py` and `family_link.py` against a real Postgres: schema
creation, every query, the housekeeping pruner, the CSV export. Drops and
recreates its schemas first, so it is repeatable and its assertions can be
exact. **Uses its own scratch database** (`botfamily_test` by default —
override with `TEST_DATABASE_URL`), never the one the bots run against.

`tests/test_shared_copies.py` is part of `run.py` and needs nothing but the
source. Until 2.0.0 it held the copies of `family_link.py`, `live_message.py`,
`lifecycle.py`, `problems.py` and `shared_features.py` to being byte-identical
in every bot folder. Since they live once, in `shared/`, it holds the other
end: every shared module is there, no bot folder has a file of the same name
that would quietly win over it, and every bot finds `shared/` as it starts.
The test mirror (`mirror39.py`) copies `shared/`'s modules into each mirrored
bot folder, so every other test still sees one self-contained bot.

`tests/test_railway_move.py` is part of `run.py` and needs nothing but the
source. It holds `scripts/railway_move.py` to what Railway's API accepted when
it was first run against it (2026-09-29): only the service settings the API
took, DB_SCHEMA on every service, references to BotFamily only in BotFamily's
own project, the list of what to paste in a project of its own, and a refusal
-- before anything is created -- when the five would not fit the plan's
services per project or its projects.

`tests/test_envfiles.py` is part of `run.py` and needs nothing but the source.
It holds `shared/envfiles.py` to its rules against made-up files: the
environment over the bot's own `.env` over `shared/.env`, an empty line
hiding nothing, one bot's settings ignored in `shared/.env`, `ADMIN_ID`
standing in for each bot's `<PREFIX>_ADMIN_ID` (the bot's own wins),
`ENV_FILES=off` reading nothing, and `merged()` agreeing with `load()`.

It also covers the fifth copy nobody was checking: ManagerBot's
`monitoring.py`, which holds 22 functions from `shared_features.py`, 18 of
them character-for-character identical. Four differ on purpose and are listed
with a reason each; the test fails both if a nineteenth drifts and if one of
the four documented exceptions stops being a real difference.

`tests/test_jobs.py` is part of `run.py` and needs nothing but the source.
It drives ConvertBot's conversion queue (`convert_bot/jobs.py`) with a fake
worker and a fake Telegram side, and asserts what happens to the credit
however a conversion ends: taken when it starts rather than when it is
queued, kept when the person stops a running one themselves, returned
whenever it ends for the bot's reasons, and the owner alerted when that
reason is a fault rather than a declined file. It also pins the two things
decided before anything is charged -- which formats would come out too big to
send, and whether a person is over their share of temporary storage. It
exists because two conversions of a 200 MP photo succeeded, failed to upload,
and kept the credit without a word to anybody.

`tests/test_pricing.py` is part of `run.py` and needs nothing but the source.
A person's first 500 Stars buy credit at three times the ordinary rate and the
next 500 at twice it; the owner's condition for any discount was that it "at
least covers the hardware usage", so it is arithmetic here rather than a
promise. Since 2.0.0 prices are `shared/pricing.py`'s rule -- cost plus a
markup that is the cost itself while it is small and grows more slowly after
-- and this holds the rule to its promises: the markup is the cost up to the
knee, keeps rising after it, and falls as a share of the cost; every pair of
formats at every size from 3 to 2000 MB brings in at least 1.5 times the
worst its job can cost (its own time limit on two cores and 1.5 GB, and
everything it may send back) at the deepest rate a Star ever buys credit at;
a bigger file of a kind never costs less, and costs less per megabyte; the
least paid conversion could *not* pay for a flat 2000 MB send, which is why a
job's send limit follows its estimate; DownloaderBot's large files, the same;
the prices the READMEs and terms quote are the ones the code charges; and a
free conversion stays under half a cent. It also holds the ladder to what it
promises: paying in pieces earns exactly what paying at once does, and one
more Star never buys less.

`tests/test_big_files.py` is part of `run.py` and needs nothing but the
source -- not even Telethon: a fake one goes into `sys.modules` before the bot
is imported, which also proves nothing imports it at startup. It drives
ConvertBot's receive handler at the edges that matter since files over 20 MB
go over MTProto (`big_files.py`): exactly 20 MB still takes the Bot API and
one byte more does not; exactly 100 MB is taken and one byte more gets the
bot's own sentence; a group gets the private-chat sentence and no MTProto
attempt; the route being down refuses the file, keeps nothing, and is not
retried for every file that follows; a message that is gone, in somebody
else's chat, of a different size or cut short is never used; an album over
the ceiling is refused before the rest of it is downloaded; and without
`CBOT_API_ID` the limit is 20 MB whatever `CONVERT_MAX_FILE_MB` says. Then
the way out: 50 MB exactly goes through the Bot API, one byte more over
MTProto, and a result one byte over its own send limit is refused. Each of
those was broken on purpose once and the test failed. Since 2.0.0 also: an
API id and hash that are set but wrong are found out by a login as the bot
starts (`big_files.verify`), after which the bot takes 20 MB in a private chat
too, its price list says so, and no connection is tried; a network that does
not answer turns nothing off; and asking again changes nothing.

`tests/test_card.py` is part of `run.py` and needs nothing but the source.
Since 2.0.0 a conversion keeps one status message up to date while the person
waits: the stage, a bar, the time so far, the time left once there is enough
to go on, and what it costs. This pins how that card reads and behaves: the
bar never shows 100% before the file is there, no time left is guessed from
the first 3% or the first three seconds, an unchanged card is not edited
again, a card that something newer has pushed up moves to the bottom and the
old copy goes, and there is no Stop button once the result is on its way.
Where the numbers come from is `tests/progress.py`, below.

```bash
python tests/progress.py
```

The other half of the card: that the conversions report how far they have
got, with the real tools -- ffmpeg by its position against the clip's
duration, a document page by page, an album file by file, all of it through
the real worker process into the job the bot reads. Not in `run.py`, because
it needs ffmpeg, ffprobe, Pillow and PyMuPDF installed; it needs no database
and no network.

`tests/test_switch.py` is part of `run.py` and needs nothing but the source.
It covers `testbot/switch.py`, which runs all five bots on TestBot's one token
and switches with `/asConvert` and the rest: that only those switch, and
never for somebody who is not the tester; that a button goes to the bot that
drew it, a reply to the bot that wrote the message and a payment to the bot
that sent the invoice; that only the current bot's menu and profile reach
Telegram, with `/as` added, and that a switch changes only the menu in the
tester's chat (rewriting all of them is what Telegram's rate limit punishes);
that a rate limit is waited out rather than given up on; that every bot's
`BOT_NAME` becomes its test id, which is what keeps a run on the shared
database off the real bots' lease, maintenance flag and restart marker; that
what the real bots keep in `family` -- the wallet among it -- TestBot keeps in
`testbot_family`; that Stars are sandboxed unless `token.env` says otherwise;
that settings come from `token.env`, the environment winning; that a second
TestBot on the same machine refuses to start; that a real bot's token is
refused; and that on the shared database a login that could write outside
`testbot_*` is refused, as is a database it cannot ask. The half that needs
the real bots, the test token and a database is `.\testbot\run.ps1 audit`,
which compares every command each running bot handles with every menu it
publishes, checks the menu your chat really shows after each switch, and
checks each runs under its test id.

`tests/test_logos.py` is part of `run.py` and needs nothing but the source.
It reads the shapes out of `scripts/make_logos.py` rather than the images, and
checks the two things that actually break a logo: whether it survives being
cropped to a circle, and whether any part of it is too thin to read at the
34 pixels a chat list gives it. It also re-renders both files and compares
them, so a logo edited by hand in one format and not the other is caught.

```bash
python tests/policy_scenarios.py
```

`/privacy`, `/terms` and `/deletemydata` driven end to end, on the same
harness as the AnonBot scenarios and against the same scratch database.
AnonBot is the bot it runs against because it has the most to erase and the
most to break by erasing it: a link other people already hold, a conversation
with somebody else on the other end, a block list.

What it is really there for is the half a unit test cannot reach. `erase_user`
is SQL, and SQL that has never been executed is a guess. It also covers the
taps nobody was meant to make -- the same confirmation tapped twice, an older
confirmation tapped after the first erase, and callback data that was never on
a button.

```bash
python tests/no_storage_scenarios.py
```

AnonBot's privacy notice says message content is never stored. This is what
holds it to that. It runs with `DEPLOY_SAFETY=on`, so persistence is live the
way it is on Railway — the other scenario suites run with it off, where
"nothing was written to the database" would be true for the wrong reason.

Every message carries a nonce; after each one the process is made to flush and
every text, varchar and json column in the whole schema is searched for it,
asked of `information_schema` rather than from a list. It checks first that
something the bot *does* keep is findable, so that a negative means something.

Nine paths: delivered, refused, ambiguous, edited, captioned, both sides, and
somebody with nothing open.

```bash
python tests/erase_scenarios.py
```

The same erase, in all four bots, each in a throwaway schema built by its own
`init_db()` and dropped afterwards. The assertion is not a list of tables: it
asks the database which tables have a user column and requires that none of
them mentions the erased id afterwards, with `star_transactions` as the one
documented exception. A table added in a later version is therefore covered on
the day it is created rather than the day somebody remembers it.

`tests/test_release_push.py` is part of `run.py` and needs git. Since 2.0.0
`scripts/nightly_release.py` releases the base edition by pushing this folder
to MumuBots, and the C edition by pushing its folder to MumuCombined (or
BotFamily, the name GitHub still redirects), instead of running
`publish.ps1`. Against throwaway repositories, with a bare one standing in
for GitHub: each origin that is right is accepted and each that is not --
`telegram-bot-family` above all -- is refused, as is a folder whose history
still begins with that repository's, whatever its origin (a push carries
every commit HEAD descends from, and its early ones hold tokens); both are
refused before anything is paused or the database is touched; the push
carries the branch and the
version's tag, and a dry run pushes nothing; and `--skip`, which held one
bot back when each had a repository of its own, is refused.

`tests/test_build_history.py` is part of `run.py` (git for its last part).
`scripts/build_history.py` builds MumuBotsHistory -- every version folder
before 2.0.0 -- and is all that stands between that push and the tokens
0.1.1 and 0.3.0 hard-code. Against a made-up laptop: settings files, dumps,
SQLite files with their `-wal`/`-shm`, logs, Telethon sessions, nested git
and a `.claude` worktree are left out, and a version left with nothing says
so rather than vanishing; tokens, database passwords and API hashes are
redacted where code or notes keep them, and the documentation's example
token is not; 2.0.0 and its C edition stay out; a secret no pattern knows
the shape of is found by its value and stops the build, and the report names
where it came from, never the secret; `--replace` deletes only an earlier
build of its own; and `--git` makes one commit per version, oldest first,
with nowhere to push to.

`tests/test_large_files.py` is part of `run.py` and needs nothing but the
source. DownloaderBot's downloads over 48 MB, driven through the real
`_resolve_and_send` with the routes, the wallet and Telegram faked: off by
default, and then refused with the price and a button to switch it on, with
nothing past 48 MB fetched or charged; on, a notice that it costs credit
before anything is fetched, then ⚡7 for the first 100 MB and less for each
100 MB after, charged once the size is known, and anything past 50 MB sent
over MTProto; a balance too small for the least it costs fetches nothing, one
too small for this file is charged nothing, a failed send is refunded, a group
is sent to a private chat, and nothing is ever left on disk; and when Telegram
refuses the API id and hash, large files are off whatever the person chose.
`test_pricing.py` holds the price to covering the worst case of every size up
to 2000 MB.

```bash
python tests/testbot_role_scenarios.py
```

TestBot's own login on the shared database -- `testbot/supabase_role.sql`,
run as written against production-shaped schemas in a scratch database on the
local Postgres, dropped afterwards with the role. As that login: every table
the real bots keep can be read, including one made after the script, and
nothing there or in `public` can be inserted, updated, deleted, truncated,
created, altered or dropped; what every bot runs as it starts works in its
own `testbot_*` schemas; a `testbot_*` schema an earlier TestBot made as
`postgres` is handed over with its tables; and `switch.py`'s check finds
nothing the login could write outside `testbot_*`, the one schema a stray
grant opens, and everything for a superuser. Running the script twice
changes nothing.

```bash
python tests/inflight_scenarios.py
```

ConvertBot's paid conversions in flight, since 2.0.0. The graceful stop always
refunded a running conversion; a container killed outright (out of memory, a
host failure) did not, although `TERMS.md` promises the credit back when "the
bot was restarted in the middle of it". Each paid conversion now has a row its
process stamps every 30 seconds; a row unstamped for two minutes is claimed by
another process, exactly once, refunded, and the person told. This checks the
claim (only others' rows, only stale ones, only once), how a conversion's own
ending reads the row so the one presumed dead does not refund a second time,
`/deletemydata`, pruning, and one recovery end to end against the real ledger.
It removes the test wallet from the family tables afterwards, because
`credit_scenarios.py` counts every wallet there.

```bash
python tests/startup.py
```

Runs each bot's real `main()` with `run_polling` stubbed out. That covers
imports, `init_db`, every handler registration, `family_link.attach`, the
maintenance jobs, and the polling arguments — everything except the network.

```bash
python tests/functional.py
```

The family command queue driven the way ManagerBot drives it (every command
in `family_link.COMMANDS`, plus an unknown one), the buffered activity
tracker including what happens when a flush fails, the idle-user-data
sweeper, that the language keyboard's `callback_data` matches the pattern
each bot registers, and that every bot actually registers `/language` — the
only route to that keyboard now that `/start` prints the instructions.

```bash
python tests/start_flow.py
```

`/start` and `/language` in all four child bots, with Telegram stubbed: that
a user with no language on record gets the picker (once), that everyone else
gets the instructions in the language on record, and that `/language` is the
picker every time with a tick on the language in force. All three checked in
English, Uzbek and Russian, because the failure this guards against is one
bot out of four keeping the old shape. Also the two ways an in-place edit
goes wrong, both of them through `live_message.py`: tapping the same language
button twice asks Telegram for the same edit twice, which used to come back
as an unhandled `BadRequest`; and a tap answered after the user has typed
something of their own is sent as a new message rather than edited into one
that has scrolled off the screen. Needs Postgres, nothing else.

```bash
python tests/cancel_flow.py
```

`/cancel`'s ask-first menu in all four child bots, with Telegram stubbed:
that nothing pending still answers immediately and without buttons, that
something pending asks and stops nothing yet, that "keep going" leaves it
that way, that picking one item stops that item **and leaves the others
alone**, and that "all of it" is offered only when there is more than one
thing it could mean. The middle one is the point of the whole feature, so it
is checked against what is still pending afterwards rather than against what
the bot said. Needs Postgres, nothing else.

```bash
python tests/lossless.py
```

DownloaderBot's `/lossless` switch on every path out of the bot — the video,
a Pinterest image, a Reddit image and a Reddit card, one Twitter photo, a
Twitter album, and a tweet card — off and on, with the fetches and the sends
faked and the routing real. A switch honoured on four paths out of five is
the bug this exists to catch. Also that a document is named after what its
bytes actually are. Needs Postgres, nothing else.

It was rewritten in v1.2.2R. It had been calling
`bot._download_and_send_video` and `bot._handle_pinterest`, neither of which
had existed since the v1.2.1 resolver rewrite, so the run died on its first
case and the five that still worked were never reached — and `twitter_card`
had been passing without ever rendering a card, because the chain now answers
before syndication is consulted. The fakes moved to the `resolvers.resolve` /
`resolvers.download` seam, where the code actually is.

```bash
python tests/anon_flow.py
```

AnonBot's two-way relay, with Telegram stubbed out: that a guest's swipe-reply
resolves to the right conversation, that a guest message replying to nothing is
held rather than guessed at, that the Send-it-anyway button delivers the held
one, that the owner's reply still reaches the guest, and that nothing on either
side prints the old guest pseudonym. Needs Postgres, nothing else.

```bash
python tests/errors.py
```

Which exceptions count as a crash and which are just the network. Pins down
that `NetworkError`, `TimedOut` and `RetryAfter` are transient and raise no
alert, that `BadRequest` — which *subclasses* `NetworkError` in PTB — still
does, that a sustained run of blips warns exactly once, and that recovery
clears it. Needs neither Postgres nor a network: the modules are imported
directly and their event hook is replaced with a list.

```bash
python tests/media.py
```

The real pipelines with real ffmpeg and Pillow: video-sticker encoding
against Telegram's 256 KB limit, static-sticker resizing, every conversion
pair ConvertBot offers, that an unsupported target is rejected, and that the
ffmpeg timeout is actually honoured rather than merely configured. Needs
`ffmpeg` on `PATH`; skip it if you do not have one.

Each prints `FAILURES: 0` when it passes.

---

## What the simulator cannot answer, and the staging bots that can

`sim.py` is a faithful model of the Bot API's *shape* — ids, anchors,
keyboards, what `copyMessage` returns and what an edit does to a keyboard.
It is not Telegram, and it cannot answer questions about what a **client**
does. Three of those matter to AnonBot specifically:

- **Does a client actually honour `ForceReply` on the opening bubble?** The
  guest's first message being a real reply is what routes it (see
  `start_follow_link`). Every client is supposed to; the order-based
  fallback exists because "supposed to" is not a test.
- **Does every part of an album carry `reply_to_message`?** The album map
  makes this not matter — it routes by `media_group_id` before it looks at
  anchors — but the answer changes how surprising the code is to read.
- **Telegram's own limits**: per-chat send rate, the 48-hour ceiling on
  deleting a message (`_retire_prompt` is best-effort because of it),
  file-size limits on the download paths.

That is what `testbot/` is, as of v1.2.3. One spare Telegram token, the bots'
real code, the local database, and one line to fill in:

```powershell
Copy-Item testbot\token.env.example testbot\token.env
notepad testbot\token.env      # paste the token on the TESTBOT_TOKEN= line
docker compose up -d
.\testbot\run.ps1              # all five; /asConvert, /asAnon ... in the chat
.\testbot\run.ps1 anon         # or one bot's code alone
```

**[testbot/README.md](../testbot/README.md) is the full instructions**,
including why it points at a local database rather than at Supabase with a
different schema, and why it calls itself TestBot on the family bus rather
than borrowing the real bot's name.

**The token never needs to be pasted anywhere but `testbot/token.env`.**
Nothing in this repository or in a session transcript should ever contain a
real one — that file is gitignored for exactly that reason, and a token that
has been in a chat log should be revoked in `@BotFather` (`/mybots` → the bot
→ API Token → Revoke).

The order that gets the most out of both: `tests/run.py` and
`tests/anon_scenarios.py` on every change, because they are seconds and they
cover the logic; a staging bot before a release, because it covers the
clients; the 04:00 window (see [../UPDATES.md](../docs/operations/UPDATES.md)) for the real
one.

## What they need

A reachable Postgres. Everything else is generated: `media.py` synthesises its
own test clips and images with ffmpeg, and `startup.py` uses dummy bot tokens
because it never reaches the network.
