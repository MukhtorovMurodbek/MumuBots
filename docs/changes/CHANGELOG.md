# Changelog

Newest first; commit hashes refer to the root repo's history (`git log`).

This file doubles as the project's session-tracking log: new sections get
added above the most recent `CHECKPOINT` marker as work happens, grouped by
session/topic with commit hashes. It keeps the versions currently being
worked on in full and everything older as one line each -- see "Where the
older history is" below.

## One bot changed its name

The private bot was **ParentBot** until v1.6.0, when it became **ManagerBot**
-- the name moved to fit the Telegram username `@mumu_manager_bot` rather than
the other way round. `CHANGES_v1.6.0.md` has the reasoning.

**Entries below this line still say ParentBot, and are left that way on
purpose.** That is what it was called when they were written, and a changelog
edited to agree with the present is a changelog that cannot be trusted about
the past. Same for the `CHANGES_v*.md` files in the older version folders.

## Versioning policy

`0.MINOR.PATCH`, staying at major version 0 for as long as the bots are in
active development/testing -- one shared version number across all five bots
(individual entries still call out which bot(s) changed).

**A version is incremented only when the owner finalises.** Not when a feature
lands, not when a session ends, not because of what kind of work it was. If
"finalise" has not been said, the number does not move, however much has been
completed or however cleanly it tests. The number is the owner's call and is
confirmed with them at finalise time.

**`N.0` means everything for `N-1.0` is finished, with nothing left to fix.**
It is always assigned looking backwards at closed work, never claimed for work
still in progress.

**1.0.0**: the owner's own trigger is "when I push everything to Railway or
basically any other hosting" -- major version 0 means local-only. Not declared
until a real deploy is up and working.

Each finalised version gets three things, and needs all three:

1. **A `CHECKPOINT` entry here**, above the sections it covers. New sections
   are added above the most recent checkpoint as work happens.
2. **A git tag** (`git tag v<version>`), as a permanent anchor in history.
3. **Its own folder**, `C:\.prog\.TelegramBots\BotFamily<version>\`, holding
   that version's full working copy -- `.git` and the per-bot `.env` files
   included, so any one folder is a complete, runnable checkout on its own.

The folder is not optional and not a label. The owner reads the version list
from the folder list, so a version that exists only as a tag does not exist.
See the `v0.7.1` notes (in `BotFamily0.7.1/`, and in this file's history) for
what happens when the three records drift apart.

## One changelog per folder, and it grows

A version folder holds **exactly one** `CHANGES_v<version>.md`: its own. Not
its predecessors'. Those stay where they were written, which is what makes a
version folder a complete record of that version rather than an accumulating
pile — `BotFamily1.2.2` had twelve of them, eleven describing versions it
was not.

**That file covers everything between the previous version and this one, and
it is appended to rather than replaced.** Work that lands in an existing
folder — a session that fixes something without cutting a new version — adds
a section to the file already there. A second file is only ever created
alongside a new folder.

**A bigger version shift aggregates.** `2.0.0`'s changes file covers
everything since `1.0.0`, not just since `1.9.x`; by the same rule a minor
bump (`1.3.0`) covers everything since `1.2.0`. A patch or a suffix release
(`1.2.3`, `1.2.2R`) covers only the step immediately before it. The reason
is what each number is for: a patch is read by whoever is about to deploy
it, and a major is read by somebody asking what changed since the last time
they looked.

The same one-folder-one-copy rule applies to `backups/` — see
`backups/README.md`.

---

## Where the older history is

This file used to carry every session note from `v0.3` onwards, which by
`v1.2.2` was 126 KB and about 2,400 lines, of which the newest 110 were
current. From `v1.2.2R` it carries the versions still being worked on in
detail and everything before them as one line each.

Nothing is lost. Every released version has its own complete folder --
`C:\.prog\.TelegramBots\BotFamily<version>\` -- and the copy of this file
in `BotFamily1.2.2` is the last one holding the full text. The commit history
in this repository has all of it as well; `git log -p CHANGELOG.md` is the
whole record. Per-version `CHANGES_v*.md` files work the same way from here
on: the folder for a version holds its own, and does not carry its
predecessors'.

---

## 🚩 CHECKPOINT: v2.0.0

Full detail in `CHANGES_v2.0.0.md`, which as a major's file carries every
change since 1.0.0. Worked on as `BotFamily1.7.1` and finalised as a major:
two editions of every version (MumuBots, five programs sharing `shared/`, and
MumuCombined, all five in one process), twelve repositories become three,
files up to 2000 MB, and prices worked out from what a job costs. Production
still runs the one-process 1.7.0 from BotFamily until its move to MumuBots.

## Railway, measured, and 2.0.0 cut again (2026-09-29)

Same folder; full detail in `CHANGES_v2.0.0.md`. The real ConvertBot image,
built from MumuBots on Railway in a project of its own, converting on the
plan production runs on (2 vCPU, 1 GB). **ffmpeg's thread cap reached only
the decoder**: the encoder sized itself from the 48 cores the container
reports and a 19 MB 1080p video was killed for memory before its first frame
-- in 1.7.0, which production runs, too. The cap now reaches the encoder and
the filters, in both editions; the same video converts in 5 s at 288 MB, a
218 MB one in 52 s at 269 MB. A 4K HDR video still does not fit in 1 GB; at
1080p it does -- which of the two is the owner's call. `railway_move.py`
sent a setting Railway's API refuses and did not know the plan allows 5
services in a project: fixed, and it takes `--project`. All five images
build and start on Railway from MumuBots. The owner asked for the fixes to be
2.0.0 rather than 2.0.1, which nobody had yet received, so `v2.0.0` and
`v2.0.0C` were cut again with them. New test `test_railway_move.py`, and
`test_conversions.py` holds the cap.

## Published: three repositories (2026-09-29)

MumuBots and MumuCombined public, MumuBotsHistory private; BotFamily untouched
and still production's source. MumuCombined carries BotFamily's history and
one unmerged branch found there (a cobalt route for YouTube and a rethought
Report dialog, 2026-09-25), which is not in 2.0.0. The base folder's old
`.git` made way: every ref of it is in `BotFamily1.7.0\.git`.

## Ready for GitHub (2026-09-29)

Same folder, uncommitted; full detail in `CHANGES_v2.0.0.md`. MumuBotsHistory
lives on GitHub only: the local build went, after a second build matched it
commit for commit, and `scripts/build_history.py` now builds into the temp
folder for the push. The owner's two new `.env` files (`shared/` and the C
folder) are ignored by both repositories, and no secret value from any `.env`
on this laptop is in any file either would publish. `ADMIN_ID`, Railway's one
line for all five, is now read by the base bots as it already was by C
(`shared/envfiles.py`); new test `test_envfiles.py`.

## Prices from cost, a last look over everything, and the move prepared (2026-09-28, later)

Same folder, uncommitted; full detail in `CHANGES_v2.0.0.md`. Prices are now
cost plus a markup that is the cost itself for small jobs and grows more
slowly after (`shared/pricing.py`), the cost being the most a job's own time
and send limits allow at Railway's rates: the owner's 224 MB clip to MP4 went
from ⚡2,100 to ⚡36, nothing costs more than ⚡84, DownloaderBot's 2 GB from
⚡200 to ⚡76, and every price still covers 1.65 times its worst case at the
deepest credit rate. An API id and hash that are set but wrong are found by a
login at startup, and the bots fall back to 20/48 MB instead of failing a
user -- checked against Telegram itself. The live audit of every menu found
nothing; fifteen unused ConvertBot texts went, usernames with an @ work in
both editions, and `/idle`'s sleep figure no longer caps at 67%.
`docs/changes/PLANNED.md` holds what is planned (OCR added; archives and
camera RAW worked out for the next patch; sleeping the quiet bots measured and
specified) and what was dropped (music; conversion services, whose terms
forbid it). The C edition is nine files of sections instead of about fifty,
the same program. Production's move from BotFamily to MumuBots -- five
services taking their secrets by reference -- is ready in
`scripts/railway_move.py` and waits for the owner: the assistant's safety
check would not change the production Railway project on its own. The release
moved to the next night's window, as the owner said it should if the night ran
out.

## One repository, shared modules, 2000 MB, and the plan for GitHub (2026-09-28)

Same folder, uncommitted; full detail in `CHANGES_v2.0.0.md`. The modules the
five bots carried as byte-identical copies live once, in `shared/`, with
`big_files.py` and `envfiles.py`; settings come from the environment, then the
bot's own `.env`, then `shared/.env`, and the examples split to match. Each bot
deploys from the one repository with its own Dockerfile and `railway.json`
(all five images build); the documents moved into `docs/` by kind; the licence
is at the root too, so it covers `shared/`; U is now C. `publish.ps1` is
retired -- a bot folder no longer runs alone -- and `nightly_release.py`
pushes instead, refusing the wrong repository and the old history before it
pauses anybody. TestBot is local only, keeps its own wallet in
`testbot_family`, sandboxes Stars, runs one at a time, and on the shared
database only as a read-only `testbot` login (`testbot/supabase_role.sql`),
which it checks before starting. ConvertBot takes and returns files up to
2000 MB, with parallel transfers, a stall guard, an early stop that refunds a
conversion that cannot finish in time, the whole process group killed on
stop, and HDR video tone-mapped; the owner's 4K60 HDR10 clip converted to MP4
in 61 s. DownloaderBot fetches and sends past 50 MB, off until switched on in
`/settings`, told before it costs, ⚡10 per 100 MB. The GitHub move from twelve
repositories to three (MumuBots, MumuBotsHistory, MumuCombined) is planned
for after finalising, with MumuBotsHistory built and checked for secrets here
by `scripts/build_history.py`. New tests `test_release_push.py`,
`test_build_history.py`, `test_large_files.py`, `testbot_role_scenarios.py`.

## TestBot on either database, and hosted; /help lists everything (2026-09-27, later)

Same folder, uncommitted; full detail in `CHANGES_v2.0.0.md`. TestBot runs on
the local or the shared database. On the shared one the documented separation
(its own schemas and bus id) was not enough: lifecycle keyed the poll lease,
the maintenance flag, the waitlist and the restart marker on each bot's real
`BOT_NAME`, so a test run could mark the real bot's restart as expected or,
through the test ManagerBot, pause it -- `switch.py` now gives every bot its
test id, and `run.ps1 <bot>` on the shared database goes through it. A switch
now changes only the tester's chat menu, after Telegram answered full menu
rewrites with waits of 285 and then 1941 seconds. `testbot/Dockerfile` and
`railway.json` host it (checked in Docker: 64 MB idle, clean SIGTERM); not
deployed, the CLI's Railway account being inactive. Every public bot's `/help`
now names every command a user can send, in all three languages, and
`test_menu.py` holds it to that.

## A live status card, TestBot as any bot, every command checked (2026-09-27)

Same folder, uncommitted; full detail in `CHANGES_v2.0.0.md`. ConvertBot's
status message is a card kept up to date while the person waits -- stage,
bar, time so far and left, cost -- fed by progress the conversions now report
(ffmpeg's position, pages, album files) through the worker; found on the way,
a stderr line over 64 KB could hang the wait for a worker or a big-file
transfer. `.\testbot\run.ps1` runs all five bots on TestBot's token at once,
switched in the chat with `/asConvert` and the rest; buttons, replies and
payments go back to the bot that made them, only the current bot's menu is up,
Telegram's rate limit is waited out, and it refuses real tokens and any
database but the local one. `run.ps1 audit` compared every handled command
with every live menu: all clean; AnonBot's `/help` was missing
`/conversations` and `/which`, now there, and `test_menu.py` checks `/help`
too. New tests `test_card.py`, `test_switch.py` (in `run.py`) and
`tests/progress.py`.

## 2.0.0: two editions, tested locally (2026-09-26)

The working folder `BotFamily1.7.1` is now `BotFamily2.0.0` -- a major, the
owner's number, because production is hosted differently (one process), the
API id went in, and every version now comes in two editions. Full detail in
`CHANGES_v2.0.0.md`, which as a major's file also carries every change file
since 1.0.0. Tested without touching production (which runs the one-process
service, 1.7.0): every suite passes on a local Postgres, two pre-existing test
faults fixed; each bot and the runner ran live on the TestBot token; MTProto
logged in as TestBot. New beside this folder: `BotFamily2.0.0C`, the
one-process edition as its own code -- arranged by function, squeezed from
33,740 to 24,232 lines with every file proven the same program, one README,
one LEGAL.md -- a checkout of the public `BotFamily` repository. The build that
generated `BotFamily` from here (`unified/`, `scripts/build_unified.py`,
`tests/test_unified.py`, the step in `publish.ps1`) is gone from this folder.
ConvertBot's public README, privacy and terms caught up with the bot. Nothing
committed.

## Every bot in one process, for when memory is what runs out (2026-09-25)

Worked on in `BotFamily1.7.1` (now `BotFamily2.0.0`), uncommitted; full detail in `CHANGES_v2.0.0.md`.
Why nothing runs on Railway: all five deployments were removed together on
2026-09-21 when the workspace's trial ended (`plan: "trial"`, state
`INACTIVE`) -- a plan to choose, not a bug. New: `unified/bot.py` runs all
five bots in one process, each loaded as its own package so `import db`
still means its own; `scripts/build_unified.py` builds the public
`BotFamily` repository from this one (shared modules once, requirements
merged, per-bot deploy files dropped), and `publish.ps1` pushes it on every
release. Measured idle: 302 MB as five processes, 77 MB as one. The bots'
own code is unchanged. `tests/test_unified.py` builds it and proves the
isolation, and fails when either half of the import redirection is removed.
Then one settings file: `unified/.env.example` is all 159 settings the bots
read, each tagged required / required for one bot / optional, with only
twelve lines to fill in -- one `ADMIN_ID` for all five, `SIBLING_BOTS`
written from the usernames, `POLICY_BASE_URL` for every policy link. Found
on the way: per-bot settings read after startup (the payment token) never
reached their bot; each bot now reads the environment through a view of its
own, so a prefix applies whenever the setting is read. The first Railway
deploy then failed on the file's pre-filled example `DATABASE_URL`, and the
log blamed the tokens: required lines are now empty, and the settings and
the database are checked before any bot loads, with the problem said in one
sentence on stderr.

## ConvertBot takes files over 20 MB (2026-09-26)

Same folder, still uncommitted; full detail in `CHANGES_v2.0.0.md`. With
`CBOT_API_ID` and `CBOT_API_HASH` set, files over the Bot API's 20 MB (in)
and 50 MB (out) move over MTProto (`convert_bot/big_files.py`, Telethon), in
private chats, up to 100 MB; unset -- every deployment today -- nothing
changes. Chosen over a local Bot API server, which on Railway would have to
live inside the container, logs the bot out of the cloud, and silences it
when down. A result may be sent at up to twice what it was priced on, not
the 2000 MB the route allows, because `test_pricing.py` shows the cheap tiers
could not pay for that egress. Albums over the ceiling are refused before
they are downloaded. New codes CV-GROUP-LIMIT and CV-BIG-UNAVAILABLE.
`tests/test_big_files.py`, mutation-proven. Waiting on the owner for the API
id, then a real transfer on TestBot.

## 🚩 CHECKPOINT: v1.7.0

Full detail in `CHANGES_v1.7.0.md`. Written in a folder called
`BotFamily1.6.1` and finalised as a minor, because the export was rebuilt,
pricing changed shape, Word documents are new, and errors grew levels.

## Two broken commands, and a menu that had grown (2026-09-20)

`/idle` had never worked — it read `SLEEP_AFTER_SECONDS` from `family_link`,
which has never had one; it is `monitoring`'s. `/stars`, `/probe` and
`/providers` were refused by ManagerBot, because the bot that owns each
registers it in its own process: every bot now advertises what it answers in
its heartbeat, and `_dispatch` reads that instead of its own copy. Every menu
re-sorted to one shape — `/start`, `/language`, what the bot does, `/cancel`,
`/help`, money, policies — and cut: 19→16, 16→12, 15→11, 23→15, and
ManagerBot 37→27. Eleven commands left the menus and none stopped working:
`/en` `/uz` `/rus` (all in `/language`), ConvertBot's `/mystars`,
DownloaderBot's `/caption` and `/lossless` (both now on a new `/settings`),
AnonBot's `/newlink` `/pause` `/resume` `/stats` `/archive` (buttons on
`/link` and `/conversations`, and `/link` gained the counts), and ManagerBot's
`/me` `/errorlog` `/botlog` `/problemlog` `/reports` `/nightly`
`/finishupdates` — renamed to `/problems`, `/logs` and `/resume`. The five
test files that compared two values now call it `equals()`, after the two
conventions hid seven vacuous assertions.

## Errors that know how loud they are (2026-09-13)

Every problem now has a level: urgent reaches the owner at once (nine codes of
ninety-nine — crashes, money, a message accepted and not delivered), faults go
to a report ManagerBot sends at midnight in Suwon, refusals are only counted.
A crash loop alerts once per code per fifteen minutes and says how many it
stood in for; fifteen of one fault in an hour escalates by itself. Volunteered
details are cleared after 30 days and the records after 180, with
`/deletemydata` doing it at once. AGPL-3.0 on all five bots, settling a
question open since v1.5.0. ManagerBot was published early, on the owner's
request, as `ParentBot 1.6.1` (`c321a9a`).

## What a user waits for, reports that log themselves, a price that knows what it is pricing, and an export that stops asking (2026-09-13)

`/ping` sorts fastest-first and measures what a user actually waits for — one
indexed read plus one trip to Telegram — rather than only the admin bus;
`/errors` with no bot named asks all four. Every problem is recorded the
moment it is shown, with nothing about anybody, and says so; the Report button
became "Add my details" and names all four fields before sending. ConvertBot
prices a conversion after the format is chosen, from both sizes and how heavy
the pair is, with a binding quote; results are named with the date and time;
PDF, EPUB, MOBI, FB2, XPS, HTML, TXT and Markdown convert to Word. AnonBot's
`/export` no longer asks anybody to forward their own chat back at it: pick a
conversation or all, then either Telegram copies the messages (the bot reads
nothing) or the bot reads each one once and deletes what it read.

`run.py` 48/48, pyflakes clean, shared copies byte-identical.

---

## 🚩 CHECKPOINT: v1.6.0

## Report buttons where they help, every problem logged, and the logs from ManagerBot (2026-09-11)

Full detail in `CHANGES_v1.6.0.md`, same heading. The last section of 1.6.0.

Simple refusals — an unknown command, a limit reached, an expired button — keep
their code but no longer offer a Report button (`problems.SIMPLE`, 61 of 100);
every problem shown, pop-ups included, is logged with its time, code and
incident. ManagerBot gains `/errorlog`, `/botlog` and `/problemlog` in its menu,
each answering with a button per bot, and `/logs` reads memory where a host
writes no log files, which is Railway. The disclaimer's last part is now "No
personal info is sent." `nightly_release.py` gained `--at` and `--skip`, and
refuses anything but the tagged release. The four public bots were published at 19:00
KST and ManagerBot at 19:16, after `scripts/rename_to_managerbot.py` moved its
schema; Railway's `DB_SCHEMA` still named the old schema, so ManagerBot started on
a new, empty one. Nothing was lost or deleted. A full backup is pinned in the
owner's ManagerBot chat.

`run.py` 46/46; AnonBot 233, credit 78, policy 61, no-storage 15; reports, smoke, erase, functional and media pass.

## A code on every problem, a Report button, and word while a conversion runs (2026-09-11)

Full detail in `CHANGES_v1.6.0.md`, same heading. Lands in `BotFamily1.6.0`,
uncommitted and unpublished.

A conversion still running after two minutes says so, again at five, and the
ending comes by ten. Every message telling somebody their request did not
happen ends with a code (`problems.py`, 100 of them, each with what it means,
why, and what to check) and a "🐞 Report the issue" button. A report says
exactly what it sends, which is nothing personal, is stored in
`family.problem_reports` and is messaged to the owner. A crash now tells the
person, with a code. ManagerBot gains `/reports` and `/decode`.

`run.py` 45/45 with `test_problems.py`; AnonBot 227, credit 78, policy 61, no-storage 15; reports, smoke, erase, functional and media pass.

## ⚡ that goes back, `/recharge`, and Uzbek rewritten (2026-09-11)

Full detail in `CHANGES_v1.6.0.md`, same heading.

ConvertBot's NOTE is the owner's sentence, and every statement of the payment
policy uses its capitals. ⚡ that comes back "goes back to your balance";
"refund" is said only of Stars, which are not refunded. ConvertBot warns about
the 50 MB send limit, says `/recharge` wherever credit is bought and keeps
`/donate` as a silent alias; the other bots still say donate, and their
thank-you names the ⚡ the donation put in ConvertBot. All 362 Uzbek strings
were rewritten.

## After testing on TestBot (2026-09-11)

Full detail in `CHANGES_v1.6.0.md`, section *After testing on TestBot*. Lands in
`BotFamily1.6.0`, uncommitted and unpublished.

Nothing sent to the owner names a user any more: ConvertBot's failure alert,
the top-up and donation events in all four bots, and ManagerBot's stranger
alert dropped ids, usernames and balances. The "payments are final" warning
appears once in the flow — as a ⚠️ NOTE in ConvertBot's reply to a received
file — and once in every bot's `/help`, instead of on every payment message.
Conversions are free up to 3 MB (it was 1 MB) and the paid tiers rose by a
fifth to a third; the price list and the too-large refusal say better prices
and bigger files are coming.

`run.py` 44/44, credit 78, policy 61, AnonBot 213, no-storage 15; smoke, erase,
functional and media pass.

## The owner's answers, applied (2026-09-11)

Full detail in `CHANGES_v1.6.0.md`, section *The owner's answers, applied*.
Lands in `BotFamily1.6.0`, uncommitted and unpublished.

Pricing is a lifetime ladder: a person's first 500 Stars earn 6 ⚡ each, the
next 500 earn 4, then 2, counted across all their payments. Everything above
2 ⚡ per ⭐ is bonus credit that expires 90 days after the payment; ordinary
credit never does, and spending uses the bonus first. This replaces the
first-payment multiplier and the volume bonus in the section below. Payments
are final, and every place a person pays says so. `/paysupport`, which Telegram
requires of bots that take Stars, was missing from all four and is added. The
in-bot `/terms`, `/privacy`, `/deletemydata` and ConvertBot's description still
promised refunds in places, and are rewritten.

ManagerBot: `/addcredit <id> <amount>` replaces the automatic 10k seed, and
`/backup` sends the restorable `family_db.py` archive, pinned and never
unpinned. AnonBot: `/export` builds one HTML transcript of one or several
conversations from messages forwarded to it, held in memory only, behind a
disclaimer of what it reads and keeps; `anon_relay` records when each bubble was
written and by which side. Command menus follow the language a person chooses,
per chat, and scopes the code does not own are cleared at every start. The
Pinterest HLS video pin is fixed. Third-party conversion was not built:
CloudConvert, ConvertAPI and Zamzar all forbid using their APIs for a raw
conversion service.

`run.py` 44/44, credit 78, policy 61, AnonBot 213, no-storage 15; smoke, erase,
functional and media pass.

## Conversions that cannot lose credit, a queue, and paying more for less (2026-09-10)

Full detail in `CHANGES_v1.6.0.md`. Lands in `BotFamily1.6.0`, uncommitted and
unpublished.

Two TestBot conversions of the owner's 200 MP photo spent 120 ⚡ and produced
nothing. Both had converted; the results were over the 50 MB a bot may send,
Telegram answered 413 "Request Entity Too Large", PTB raised it as a
`NetworkError`, and the family error handler filed it as a dropped long poll —
no message, no refund, no alert. Conversions also ran inside their handler, and
with updates handled one at a time a long one froze the bot for everybody.
Along the way: every invoice-paid conversion since v1.5.0 was refunded and
never converted, because the handler looked for `path` in a state that had
become `paths`.

The error handler (four `shared_features.py`, and `monitoring.py`) now only
treats update-less failures as blips, and never a payload that is too large.
ConvertBot conversions run in a queue (`jobs.py`), each in a killable child
process with a real time limit. Credit is taken when a conversion starts, not
refunded when the person stops it, and refunded — with the reason, and a
direct message to the owner — whenever the bot is at fault. Formats that would
come out too big, and pictures over 210 MP, are refused before anything is
charged. A "keep file" consent lets one file go to several formats. Storage is
checked before download. DownloaderBot handles several links at once.

Topping up more buys more: +10/25/40/50 % from 50/150/500/1,000 ⭐, printed on
the `/donate` buttons, and `tests/test_pricing.py` proves the deepest discount
still covers a worst-case conversion 2.3 times over. ⭐ now means money paid and
⚡ means credit everywhere, including the published terms and privacy notices.

`run.py` 42/42, and every database suite passes.

## A reply landed on the wrong message (2026-09-10)

Full detail in `CHANGES_v1.6.0.md`, section *The reply that landed on the
wrong message*. AnonBot only; lands in `BotFamily1.6.0`, uncommitted and
unpublished.

Reported as a sequence: a guest writes, the owner answers, the guest sends a
sticker, the owner answers that and the quote is right, the guest writes
again -- and then the owner answers the sticker, and the guest sees that
answer hanging under their own latest message. A real bug, in both
directions, present since the relay was written.

A conversation exists twice, once in each chat, and Telegram numbers messages
per chat -- so the message being answered has a different id on each side and
something has to translate. Nothing did: both delivery paths anchored the
outgoing copy to `last_owner_msg_id` / `last_follower_msg_id`, the newest
bubble of that conversation in the receiving chat. Right for as long as
everybody answers the last thing said, which is why it survived; wrong on the
one gesture a threaded inbox is for. **Not a leak** -- the anchor was always
inside the same conversation, so nothing could surface in the wrong person's
chat. What was lost was which message, not which person.

`anon_relay` gains `peer_message_id`: for each row, the message in the other
chat that it is the same message as. Free to fill in, because
`record_relay_and_touch` already receives both sides' ids and both anchors in
one call and an anchor is by definition the bubble the other side would
quote, so the insert stays one statement and a reply still costs one read.
Not backfillable and deliberately not backfilled: the two chats hold
different numbers of bubbles for one conversation -- an uncaptioned sticker is
a header line plus a sticker against one message -- so pairing off what is
already there would be wrong from the first sticker onwards. Old rows stay
NULL and fall back to the old behaviour.

`tests/anon_scenarios.py` grows group P, 40 checks; reverting the fix fails
14 of them. It runs for every content type a person can send, which is what
`sim.py` could not do -- everything that was not text was sent as a photo, so
the case that breaks was unreachable in a test. There is a
`sim.MEDIA_PAYLOADS` and a `Person.media(kind)` now. One check is SQL over
the whole table rather than a scenario: no pairing points outside its own
conversation, which is the version of this that would have been a leak.

Five assertions across `anon_scenarios.py` and `anon_flow.py` were failing on
correct behaviour -- v1.5.0's refusal pass rewrote three messages and the
tests were still looking for the old phrasing. They compare against `i18n.t()`
now where the claim is the message's identity. A suite with permanent
failures teaches everybody who runs it to skim the last line.

## ParentBot is ManagerBot (2026-09-10)

Full detail in `CHANGES_v1.6.0.md`. Lands in `BotFamily1.6.0`, uncommitted and
unpublished. It began as a `1.5.1` folder and was folded into the minor when
the folder was renamed, which is the family's rule for micro steps that never
shipped on their own.

The family carried a pending rename in three files and as a `todo` line on
every test run: the private bot was **ParentBot** at **@mumu_manager_bot**, so
move the username and the seven naming layers line up again. The owner flipped
it — rename the bot, keep the address — and that is the right way round. A
username is the one layer with anything outside this repository depending on
it; the other six are text in files. Renaming the six risks nothing, and it
leaves the family with one naming exception (AnonBot's frozen
`@mumu_chat_bot`) instead of two.

663 replacements across 70 files, plus `git mv parent_bot manager_bot`.
`ParentBot` → `ManagerBot`, `parentbot` → `managerbot`, `parent_bot` →
`manager_bot`, `PBOT_` → `MBOT_`. `tests/test_naming.py` is what turns that
from a sweep into a rule, and it passes on all seven layers.

Two of those layers are not text. The bus id and the Postgres schema are rows
and objects in the shared database, and `scripts/rename_to_managerbot.py` is
new for them: one `ALTER SCHEMA`, then `parentbot` → `managerbot` in every
family table that names a bot, with the columns discovered from
`information_schema` rather than listed. It has **not** been run — it changes
the live database and nothing is being deployed yet. `DEPLOY.md` §8 has the
order for when it is: migrate, set the Railway variables, then redeploy.

What was deliberately not renamed: this file's historical entries, the text of
old `family.events` rows, and `parent`/`parentbot` as aliases, so `/logs
parent` still works. The GitHub repository is still `ParentBot` and is the one
piece left; it needs pairing with a Railway check, so it waits for a morning.

`DESIGN.md` also caught up on three things owed from the session that wrote
it: the five profile photos are uploaded, item 5 is this flip, and the
"link labels instead of bare URLs" item is withdrawn — `_policy_footer()` had
already decided the other way on purpose, and its reason was the better one.

**And then the rest of that plan was built, so its list is empty.** The slash
menu is trilingual: `publish_commands()` sends one list per language, English
from `BOT_COMMANDS` and the other two from a new `COMMAND_MENU` in each
`i18n.py`, 96 strings. `/language`, `/en`, `/uz` and `/rus` are deliberately
left untranslated — they are the way back for somebody who chose the wrong
language. Every bot also has a `hero.png`, the 640×360 description picture
that sits above the Start button and was blank on all five; same ground, same
glyph, generated from the same geometry, no text. Making the renderer work on
a frame that is not square turned up a bug that had shipped twice: `to_png`'s
ring branch unpacks a stroke width into a local called `width`, so a frame
dimension by that name was silently overwritten and two logos regenerated at
32 pixels wide.

The third item, a spoiler for AnonBot's `/conversations` excerpts, is
**withdrawn**: there are no excerpts. AnonBot stores no message contents,
which is a published claim rather than an oversight, so building it would have
meant storing the thing the bot promises not to. Two of that list's three
proposals withdrawn on contact with the code is the lesson about the file — a
design list written from the outside is guesses until each one is checked.

## 🚩 CHECKPOINT: v1.5.0

## Pinterest, the silent-failure audit, and two repository files (2026-09-09)

Full detail in `CHANGES_v1.5.0.md` sections 14-15. Closes the v1.5.0 line.

Asked simply whether DownloaderBot supports Pinterest video, the answer on
paper was yes and the answer in practice was no. Pinterest stopped emitting
`og:video`, so the page-scraping provider found no video, fell through to
`og:image` -- which is present, because it is the video's own cover frame --
and *succeeded*. A provider that succeeds ends the chain, so yt-dlp was never
reached. Nobody would have reported it as a failure. A new `pinterest_api`
provider asks Pinterest's own read endpoint, which is the only source that
tells a plain video pin, a story pin and a photo pin apart. Country
subdomains (`co.`, `in.`, `br.`) were not recognised as Pinterest links at
all; they are now.

Every other provider was then audited the same way, against live posts.
Nothing else was mismatching, but `tnktok` had the same structure waiting for
TikTok to change one tag; it refuses now instead of guessing.
`tests/test_pinterest.py` carries the audit as a standing rule.

`SECURITY.md` and `CONTRIBUTING.md` were added to the four public repos -- the
second inverted, to say that they are mirrors and a pull request cannot be
merged. The other four items on GitHub's community checklist are argued down
in `LICENSING.md`, which also raises the one that is not a checkbox: **v1.5.0
adds PyMuPDF, which is AGPL-3.0, and the repos have no licence at all.** No
licence file has been added, because that grant is irrevocable for the version
it ships with. It is the one thing to settle before publishing.

## Knowing what it costs, and two shortcuts (2026-09-09)

Full detail in `CHANGES_v1.5.0.md` sections 9-13. Same v1.5.0 folder, not
finalised.

**What the containers cost, over time.** `family.usage_samples` takes one row
per bot per 15-minute window -- resident memory, its peak, the container's
ceiling, CPU seconds, updates handled, distinct people, jobs succeeded and
failed -- and raises a `family.events` warning on three separate questions:
memory close to the limit it gets killed for crossing, a window far busier
than the recent norm, and monthly reach passing the point where the plan stops
being the right one. Counts only: no user ids, no chat ids, no text. ParentBot
reads it back with `/usage`.

**Sleeping, measured rather than guessed.** The plan was to let idle bots
sleep. Railway detects inactivity from *outbound* packets and its own docs
name the blocker: an open database connection pool. With the family bus
polling every few seconds and a heartbeat every thirty, the flag would have
changed nothing and said nothing. So nothing sleeps yet; instead every bot now
keeps a gap clock and records the part of each quiet stretch that would
actually have been sleepable, and `/idle` turns that into a per-bot verdict
with the cold-start cost beside the saving. AnonBot is ruled out by hand --
it relays messages between two people and a cold start is the one place a few
seconds shows.

**AnonBot says what did not happen.** "Nothing was sent" became "your message
was not sent" in five refusals, and the message that opened "I don't have that
message on record" now says what is true about the user's chat instead of
about the bot's bookkeeping. Behind the wording was a bug: that string was
also answering `/which`, which never sends anything. New `/archive` files a
conversation from a reply, with one confirmation, instead of four steps
through `/conversations`.

**DownloaderBot: the other way round, on a button.** Every finished download
carries a button that re-delivers that one file compressed or uncompressed,
without touching the `/lossless` preference. Same resolve, same download, same
allowance. And it counts its outcomes now, so every provider failing at once
raises an alarm instead of being noticed by a user.

**A sweep.** One real bug, in code from this version: DownloaderBot's send
path read `update.message`, which is `None` on a callback-query update, so the
new button would have raised inside the download. Six call sites moved to
`effective_message`. Three dead things removed. Every command a bot handles is
now published in a menu, in the right scope.

## Eight hundred conversions, and a bot that can say so (2026-09-09)

Full detail in `CHANGES_v1.5.0.md`. Lands in the new v1.5.0 folder; the number
has not been finalised by the owner yet, so nothing is tagged.

ConvertBot converted images, video and audio: three hard-coded lists, fifteen
target formats. It now converts around seventy-five formats into forty-three,
some eight hundred pairs, across six categories -- pictures, video, audio,
documents, structured data and subtitles. The additions are four optional
backends (PyMuPDF, mammoth, openpyxl, pillow-heif) plus Markdown, PyYAML and
defusedxml, about 45 MB of image between them and not one of them needing a
system package, so `nixpacks.toml` is unchanged.

The pairs that are the reason to use this bot rather than a website: a HEIC
from an iPhone into a JPG; a whole album of pictures into one PDF; a PDF or an
EPUB into pictures or plain text; a Word document into a PDF; a CSV, JSON or
XML export into a spreadsheet; subtitles between SRT, VTT and ASS or down to
the transcript.

`formats.py` is new and holds the whole matrix, and the matrix is **declared
in full and then filtered by what actually imports**. Every backend past
Pillow is optional, and a bot that advertised a format it could not produce
would take somebody's Stars and hand back an exception. `probe()` runs once at
startup -- module lookups, Pillow's feature flags, and one `ffmpeg -encoders`
-- and the menu, `/formats` and the invoice all read the filtered view. A
build that fails to install one line of `requirements.txt` is a bot with a
shorter format list.

`/formats` is the helper the owner asked for. Three counts, six examples, a
button per category; `/formats heic` answers for one format, and so does
typing "heic" at the bot. It is offered in the slash menu, in `/help`, and --
the one that matters -- as a button on the refusal somebody reads when their
file is not a format the bot takes.

Two long-standing defects went with it. An album of five photos produced five
format menus, four of them already pointing at a file the next arrival had
deleted; albums are now collected into one conversion, which is also what
makes "these pictures, one PDF" expressible. And `filters.ANIMATION` was
missing from the file handler, so a GIF sent from Telegram's own GIF panel was
answered with "that's not a file I can convert".

The price is still by file size, and size stopped being a good proxy for work
the moment the bot learned to read documents -- a 900 KB PDF can be four
hundred pages. Rather than an elaborate price table nobody could predict from
their own file, there are ceilings on the work: pages, frames, spreadsheet
rows and cells, files in a batch. A job over one is refused before an invoice
is sent rather than after.

`tests/test_conversions.py` is new and asserts the one thing eight hundred
pairs cannot be read for: every pair the matrix declares has a backend that
claims it, and every backend's claim is reachable from the matrix. Plus the
invariants -- no alias shadowing a real format, callback data inside
Telegram's 64 bytes, the short menu being the head of the long one, and
removing a requirement removing exactly the formats that need it. The
conversions themselves were run by hand against fixtures of every source
format: 348 pairs, all producing a non-empty file.

## v1.4.1 — the commands nobody was offered, and a design file (2026-09-08)

Full detail in `CHANGES_v1.5.0.md` §0. Landed after `v1.4.0` was tagged and
published, so it is `1.4.1` and part of the 1.5.0 line — not an addition to a
finished version.

Every bot handles owner-only commands it never advertises — `/status`,
`/dbdump`, `/messageas`, plus `/whois`, `/stars`, `/providers`, `/crashtest`.
Keeping them out of a stranger's menu is right; the family was doing it with
the *default scope*, which was the only scope it used, so they were hidden
from the owner too. A command nobody is reminded of stops being used, and
`/probe` and `/stars` had gone a version without being typed.

`BotCommandScopeChat` is what that is for. `publish_commands()` in
`shared_features.py` now sends the public list at the default scope and the
public list plus the owner-only ones into each admin's own chat, marked 🔒.
The guards are untouched: a scope decides what is offered, `_is_admin` decides
what is allowed, and the menu must never be mistaken for the second.

ParentBot is the same rule inverted. It was publishing `/sql`, `/backup`,
`/restart` and `/broadcast` to anyone who found the private bot — all refused
by `guard()`, but a list of the levers is an invitation with no upside. The
full list is now the owner's and the default scope holds `/start` alone. Its
list was four commands short as well, and it now has profile text, which it
had been skipped for on the grounds that a private bot has no audience for it
— backwards, since the owner is the one person who never reads it.

`tests/test_menu.py` had checked one direction for four bots and did not cover
ParentBot at all, which is how those four went missing. It now reads both
command lists by assignment name, checks that every owner-only command *is* in
the owner's menu, and covers ParentBot with the inverted rules.

`DESIGN.md` is new and internal: the four surfaces a Telegram bot actually has,
what each is doing, and what is left — the logos still not uploaded, the
description picture nobody uses, and a trilingual family reading an English
slash menu. It also records why Unicode pseudo-fonts were argued down rather
than built: screen readers, in-chat search, old clients, and, decisively, the
alphabets being Latin-only in a bot whose other two languages are Russian and
Uzbek.

## 🚩 CHECKPOINT: v1.4.0

## An inbox you can see, and a number the strangers could (2026-09-08)

Full detail in `CHANGES_v1.4.0.md` §7.

One AnonBot chat holds every conversation an inbox has, interleaved, and the
tools for that were a header on each incoming message and scrolling.
`/conversations` is the owner's view of their own inbox: a count of what is
open, a button per thread, a card for any one of them, a button that goes to
where a conversation starts, and a button that archives it.

Going to the start is a reply-quote, because a private chat with a bot has no
message links — `t.me/c/...` is supergroups and channels. The bookmark it
leaves belongs to that conversation, so replying to it writes into the
thread, which is a way in that does not require finding a message from it
first. That is the same thing the line announcing a new conversation has
always been, not a way past the reply rule.

Archiving is filing and nothing else: nothing is refused because of it, the
guest is never told, and anything either side writes into an archived thread
brings it straight back. An enforced close was the other option and it would
have been protection in name only — one tap on the link opens a fresh
conversation, and `/blocked` is what stops a person writing. Conversations
whose reply anchors the pruner has discarded are archived in the same pass,
because at that point neither side can write into them, which is what keeps
"open" meaning reachable rather than ever-existed.

`/which`, sent as a reply, says which conversation a message belongs to — for
the things a header cannot label: the owner's own outgoing messages, the
later photos of an album, the later bubbles of a split one.

The thing worth knowing beyond the feature: §5 removed the owner's
conversation number from the guest-facing strings that carried it and missed
the most visible one. Every reply an owner sent arrived headed `Conversation
#N`, and N is the owner's index for their own inbox — so a stranger who got
one answer learned they were somebody's Nth correspondent, a running count of
the owner's other conversations, delivered to whoever tapped a publicly
posted link. The header now names nothing, and two suites assert it.

TestBot could not test AnonBot at all: the poll lease is keyed on the bot's
own name rather than `FAMILY_BOT_ID`, and advisory locks are per database, so
every run queued four minutes behind the live AnonBot. Nothing was at risk —
the lock cannot be taken from its holder — but nothing could be tested
either. `testbot/run.ps1` sets the wait to zero.

## A sweep for what was left behind (2026-09-08)

Full detail in `CHANGES_v1.4.0.md` §6.

One of these was a real bug and the rest were tidying. `restarting_send_again`
was defined in three bots and missing from AnonBot, so during every ordinary
redeploy anyone who wrote to it was answered with the literal word
`restarting_send_again` — `i18n.t()` falls back to the key itself, which is
silent everywhere except on the user's screen.

Six dead functions removed across the shared files, a dead parameter, and
enough unused imports that `pyflakes` is now clean across the repository.

The structural find: ParentBot's `monitoring.py` holds 22 functions from
`shared_features.py`, 18 of them character-for-character identical, under a
filename the byte-identity check in `UPDATES.md` does not look at. The four
that differ do so on purpose. `tests/test_shared_copies.py` now guards both
the four copied files and that fifth one, and the manual PowerShell check has
somewhere to point.

Several things that looked dead were not — six i18n keys reached by variable
lookups, two environment variables read by PowerShell and by an f-string — and
the audit was rewritten to read call sites as syntax rather than as text after
its first version reported them.

## AnonBot keeps nothing anybody writes (2026-09-08)

Full detail in `CHANGES_v1.4.0.md` §5.

The privacy notice added a day earlier said message content was never stored.
It was not true: a message that did not say which conversation it answered was
held whole — text included — in `user_data`, behind a *Send it anyway* button,
and `user_data` is written to Postgres. The button existed to spare somebody a
swipe and it cost the only sentence in the notice that people actually care
about.

There is no way past the reply requirement now. A message that names nothing
is refused, with nothing kept, including where exactly one destination was
possible — one destination is still a guess. ForceReply is gone with it, so
there is one route into a conversation rather than two; the ↩️ button stays
and teaches the swipe, which is all it can honestly do.

The refusal points at the message to reply to, and is sent as a reply to it.
Saying "reply to the message you are answering" without saying which one is
the frustrating half of the rule, and the guest's conversation number turned
out to be the owner's index — two different conversations both labelled #1 in
one chat — so that is gone from the guest's side too.

Two bugs on the way: `/cancel` sent as a reply to a delivered message deleted
it, and `edited_message`'s `try/finally` was swallowing every exception it
raised, including a real one.

`tests/no_storage_scenarios.py` holds the notice to its word with persistence
switched on, and goes red if the hold is put back.

## What the bots hold, said out loud (2026-09-07)

Full detail in `CHANGES_v1.4.0.md`, in `BotFamily1.4.0`. This landed after
`v1.3.0` was tagged, so it is `1.4.0` material, and it shipped in that bump
along with everything above it.

The four public bots answer `/privacy`, `/terms` and `/deletemydata`, in all
three languages, and each carries a long-form `PRIVACY.md` and `TERMS.md`
that `publish.ps1` pushes to its public repository — which is what Telegram's
own privacy-policy field in @BotFather wants a URL for.

The point of the third command is that it makes the first two answerable. A
notice describing a filing cabinet nobody can open is a description, not a
promise. `/deletemydata` asks once and erases in that moment, and each bot's
confirmation says what erasing costs it: StickerBot forgets packs it cannot
delete, AnonBot invalidates a link that other people have already posted.

The one thing the bots keep is the Stars ledger, minus the username on it. A
refund is issued against those rows, so they have to outlive the payer asking
to be forgotten, and the notice says so rather than implying the erase is
total.

AnonBot's notice says plainly that anonymity is towards the recipient and not
towards the operator: the owner↔sender pairing is stored, because it is what
makes a reply possible. What is not stored, and the notice says this too, is
a single word anybody wrote.

Nothing was added to collect less, because the pruning that would have done it
was already there — `db.prune_old_data`, on `family_link.py`'s housekeeping
job, since before this session.
## 🚩 CHECKPOINT: v1.3.1H

## AnonBot's inbox link stops being monospace (2026-09-07)

Full detail in `CHANGES_v1.3.1H.md`. **H for hotfix**, cut from `1.3.0`
rather than from the `1.4.0` work in progress, so none of that shipped with
it. A micro step, so the notes cover only `1.3.0 → 1.3.1H`.

`/link` and `/newlink` printed the inbox link inside a `<code>` block. Grey
monospace is the one thing on a Telegram screen that does not look like
something you can act on, and a code span carries no URL entity, so
forwarding the message passed on text rather than a link. It is now sent as
plain text and auto-detected as a real link.

Two things moved with it. The instruction described the old entity — "tap it
to copy" is what a code block does, and tapping a link opens it — so all
three languages now say to copy it and post it. And a link draws a preview
card, which under a two-line message with two buttons would have been a
full-width advertisement for the bot the owner is already talking to;
AnonBot picks up the `NO_PREVIEW` constant DownloaderBot has carried since
`1.2.1`.

Tapping the link now opens the bot instead of copying. For its owner that is
their own link, which `follow_link_own` has always answered with a sentence
telling them to share it instead.

## 🚩 CHECKPOINT: v1.3.0

## Naming, frozen payments, and documentation for people (2026-09-05)

Full detail in `CHANGES_v1.3.0.md`, which -- being a minor bump -- covers the
whole line back to `1.2.0` rather than only the last step.

Every layer of a bot's name now derives from one canonical `<Role>Bot`, with
`tests/test_naming.py` asserting it against the source. The one exception is
the Telegram `@username`, which is an address rather than a label: AnonBot's
`@mumu_chat_bot` is embedded in every inbox link anybody has posted, so it is
frozen and recorded as an alias instead. ParentBot resolves any form.

Real-currency donations are frozen behind one flag -- complete code, doing
nothing, unfreezable with two settings rather than one.

ParentBot tells a version gap from a fault: it is private, so it ships first
and is routinely ahead of the bots it asks, and a command a bot is too old to
know about is now refused before the round trip with both version numbers and
what to publish.

The four public bots' READMEs are rewritten as product documentation rather
than as one half of a conversation.

---

## Earlier versions

One line each. The full session notes for these are in this file's history
and in each version's own folder.

| version | date | what it was |
|---|---|---|
| `v1.2.3` | 2026-09-05 | A command centre, an ask that runs out, and a bot to test on |
| `v1.2.2R` | 2026-09-05 | AnonBot gone over end to end, and three handlers that never ran |
| `v1.2.2` | 2026-09-04 | Asking from where the answer counts |
| `v1.2.1` | 2026-09-04 | DownloaderBot stops asking from here |
| `v1.2.0` | 2026-09-04 | The database move, finished and signed off |
| `v1.1.4` | 2026-09-04 | The family bus stops pretending it has a push |
| `v1.1.3` | 2026-09-03 | An Instagram reel, answered with yt-dlp's FAQ |
| `v1.1.2` | 2026-09-03 | The listener that was never started |
| `v1.1.1` | 2026-09-03 | What a minute of watching AnonBot was worth |
| `v1.1.0` | 2026-09-02 | The 1.0 line, closed |
| `v1.0.5` | 2026-09-02 | A /ping that took thirty seconds to answer |
| `v1.0.4` | 2026-09-02 | A YouTube download that arrived without its sound |
| `v1.0.3` | 2026-09-01 | /lossless was being undone by Telegram, not by the bot |
| `v1.0.2` | 2026-09-01 | Announcing an update instead of apologising for one |
| `v1.0.1` | 2026-09-01 | Two 1.0.1s, and the one that shipped |
| `v1.0.0` | 2026-08-30 | The release that goes public |
| `v0.8.0` | 2026-08-30 | The database leaves the laptop |
| `v0.7.4` | 2026-08-29 | /cancel asks which one, and DownloaderBot learns /lossless |
| `v0.7.3` | 2026-08-28 | /start says hello again, and the picker gets its own command |
| `v0.7.2` | 2026-08-27 | A dropped long poll is not a crash |
| `v0.7.1` | 2026-08-27 | Straightening out the version numbers |
| `v0.7.0` | 2026-08-25 | Making /start mean one thing, and unsticking the language switch |
| `v0.6.0` | 2026-08-25 | Making it cheap enough to actually deploy |
| `v0.5.0` | 2026-08-25 | ParentBot, and one database for the whole family |
| `v0.4.1` | 2026-08-25 | GitHub publishing + database sync tooling |
| `v0.4.0` | 2026-08-24 | Admin tools + sticker_bot bug fixes |

`v0.4.0` (PreRelease) is where the detailed history began; everything at or
below it was pre-release work under the `BotFamily0.3PostgressMigration` and
`BotFamily0.4PreRelease` folder names.
