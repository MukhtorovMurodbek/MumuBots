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

Five joined it in the review of 2026-09-29, each written by sending the thing
that hurt and each checked to fail on the code as it was:

- `test_public_only.py` -- DownloaderBot fetches the public internet and
  nothing else. The address table, which links count as Pinterest, the client's
  transport against a mock (Host, SNI, IPv6, a redirect to an internal address,
  the next address when one does not answer), and a real socket to a server on
  this machine, which the old client fetched.
- `test_ytdlp_stop.py` -- a download nobody waits for stops and leaves nothing
  on the disk: the provider's cancellation, what a yt-dlp route is given, and a
  real yt-dlp against a server that serves a video slowly.
- `test_hostile_files.py` -- a YAML alias bomb, deep JSON and XML, a
  self-referencing anchor, a CSV cell over 128 KB, blank rows, a page 125 inches
  across: each ends in a `ConversionError`, quickly, and the ordinary
  neighbours of each still convert. It stops at seven levels of bomb on purpose
  -- at twenty, a regression is a machine out of memory, not a failure.
- `test_orientation.py` -- a photo comes out the way up it was taken: all seven
  EXIF turns pixel by pixel against the specification's table, not against
  Pillow, and HEIC, TIFF and AVIF not turned twice.
- `test_sticker_ffmpeg.py` -- StickerBot's ffmpeg is capped before `-i` as well
  as after, in the shape `test_conversions.py` pins ConvertBot's.

`test_reactions_feedback.py` joined it on 2026-10-03, with AnonBot's
reactions and the family's `/feedback`: what a change of reaction passes on
and which note the reactor gets, every string either feature shows in all
three languages of every bot that shows it, that an answer is feedback only
when it answers the question (in AnonBot anything else is somebody's
conversation), and that the nightly report's default is 22:00 in Korea.
`test_reporting.py` grew the other half of that day: no line of the nightly
report says "Refused" any more, and every problem the bots explain to people
sits in exactly one kind (`problems.KINDS`).

`test_conflict_alert.py` (2026-10-10) drives the real error handlers -- the shared one and ManagerBot's own --
with a fake clock: one getUpdates `Conflict` is a warning with numbers only, three within 120 s are recorded once
(a second copy polling the same token), none again until 600 s pass quietly, three spread over more than 120 s are
nothing, and any other error is recorded exactly as before. Broken 14 ways on purpose, every one caught.

Four joined it on 2026-10-07, each run against its own deliberately broken
code (12, 10, 22 and 18 breakages, every one caught -- and several of those runs
found checks that had passed because a *second* guard caught the same input,
which were then made to isolate the guard they name):

- `test_routewatch.py` -- DownloaderBot's daily check and what hangs off it. When
  the owner is told (a platform going down, not it staying down; a recovery is
  not pushed; YouTube never; a deleted sample is not an outage), yt-dlp's
  versions, the swap that replaces yt-dlp while the bot runs (never under a
  download that is using it; a copy that does not import as the release asked
  for is refused, in a child process), the page cap (one answer that says it is
  too big, refused before a byte is read, and one that never says, cut off at the
  cap), the cobalt route against a mock transport, and cookies pasted into a
  variable.
- `test_bounded_bodies.py` -- every body DownloaderBot reads is counted *after*
  decoding (2026-10-08, after a review found 421 bytes of brotli decoding to
  512 MB): brotli, zstd and stacked encodings refused unread; gzip and deflate
  bombs stopped at each reader's cap with the process's peak measured; honest
  gzip, zlib and raw deflate byte for byte; a redirect's own body never read
  (httpx would decode it whole before following); the cobalt POST, Reddit and X
  answers capped; pasted cookies with spaces for tabs refused with a warning that
  quotes none of them; routewatch's platforms side by side with one yt-dlp at a
  time, and a forced run answering in time while the check carries on; yt-dlp's
  newest release read from PyPI.
- `test_video_fit.py` -- the rules for which videos are brought down to 1080p,
  and real ffmpeg on small clips to see the size that comes out (and the colour
  curve: tonemapped to BT.709). The memory numbers behind them are measurements,
  in the test's docstring, not checks.
- `test_archives.py` -- every archive format into every other, byte for byte;
  then archives built to hurt: a ZIP bomb and a gzip tar bomb, 10,001 entries,
  `..` and absolute and drive-letter and device names in ZIP and TAR, an
  encrypted ZIP, links and devices; a RAR assembled by hand in the test (so no
  RAR program is needed) including the one libarchive reads as its inner ZIP.
  The "each guard on its own" block exists because the first version of this file
  caught 9 of 22 breakages. 7z and RAR need `bsdtar` and skip without it; on
  Windows they use an ASCII-only tree (see CHANGES, *Not done yet*).
- `test_raw.py` -- camera raw photos, from DNGs written by hand in the test (no
  camera sample is kept): developed, the right way up (the EXIF turn read from the
  file), half size past `FULL_MP`, refused past `MAX_MP`, damaged and mislabelled
  files, a MemoryError reported as that; the 16-bit TIFF is read back by an
  independent parser and compared with LibRaw's own output value for value.
- `test_ocr.py` -- text read from pictures and scanned PDFs (`ocr_convert.py`), on
  inputs drawn at run time with PyMuPDF's own Helvetica: the four languages
  read back; a PDF of text pages, scans, a turned scan and a page whose invisible
  text differs from its picture (text pages come out verbatim, in order); the
  searchable PDF (same pages, every page identical at 50 dpi, the text layer on
  the words -- turned pages too -- and at most 20 KB a page bigger); a 48 MP JPEG
  decoded at half size and a 24 MP one brought down, with the engine never handed
  more than 12 MP (hooks on Pillow's decode and on the engine); MuPDF's cache
  shrunk once a page; progress; the dpi floor; the refusals; what a scanned page
  is (a picture on it and under 20 characters of its own text: a report with two
  blank pages and a deck of title slides count none); the count given in steps
  (the page count first, none looked at, stopped past the most a conversion
  reads); a TIFF of several pages refused while an MPO photo is read; a 16-bit
  grey picture taken to 8 bits as before without a 32-bit copy of the whole of it.
  Needs PyMuPDF and the
  language data (`TESSDATA_PREFIX`, defaulted to `TempTools\tessdata` where it
  exists) and says SKIP without them.
- `test_ocr_wiring.py` -- the same, wired into the bot, driven with the fakes of
  `test_batching.py`: which formats the menu offers and when (a PDF with scanned
  pages, one with none, one the child could not read, one counted in pages but not
  in scanned pages, pages with nothing on them, more than 30 -- TXT stays, as the
  ordinary one -- an album, a host with no language data); the language row (default from the person's
  language, the cycle, More formats staying open); one page under 3 MB free, two
  priced, the time limit as one function that the quote and the queue both take
  (`jobs.ocr_time_limit`, the file's own part included: a 90 MB PDF with one
  scanned page is given the TXT limit of the same PDF without one, plus the page's);
  the language and the page count in the worker's
  payload, and a job never reading a page it was not sold; the pages counted by a
  child process on a 5 second deadline (the page count comes first and survives a
  kill; a PDF of two million glyphs does not hold the menu; a crash and nonsense
  give None, and nothing opens the PDF in the bot's process); a file joining an
  album after the menu was drawn (the real upload handler); `scan.pdf` coming back as a `.pdf` without touching
  the upload; the texts in all three languages, and no label that says `ocrpdf`.
  Broken on purpose 43 ways, one at a time, in a private copy of the tree; the
  review fixes of 2026-10-10 18 more ways, in place.

`test_pump_tasks.py` joined them on 2026-10-10, with the ManagerBot sweep fix: the 1 s tick that decides
whether to sweep hands the sweep to a task and returns at once (it used to await it, so a sweep that outlasted one
second made the scheduler skip every tick inside it, ~2,800 "maximum number of running instances" lines a day),
never starts a second sweep over a running one, names a sweep stuck past a minute once, keeps the results and
events pumps apart, and lets a sweep that raised be followed by the next (it also checks the /status sweep-timing
line, 38 checks in all). Driven with a fake context and clock; four
mutants (inline await, no running guard, stall never named, stall named every tick) each fail it.

`test_merge.py` joined them the same day, with DownloaderBot's 📄 PDF and 🗜 ZIP
buttons under an album. The PDF is written by hand (`downloader_bot/merge.py`, no
new dependency), so it is checked against readers that did not write it: a
parser in the test for the cross-reference table's byte offsets and the trailer,
and PyMuPDF for the page count, the aspect ratios, the colours and which way up
each page is. What goes in untouched (RGB and gray JPEGs, baseline and
progressive: byte for byte) and what is re-encoded (PNG with alpha onto white,
palette, 16-bit gray, CMYK, all eight EXIF turns against Pillow's own
`exif_transpose`); an image past 20 megapixels refused from its header, with no
pixel decoded; one decoded image in memory at a time, and the file already
growing on disk while the later images are read; the ZIP's entries named by
position and the downloaded file's extension, never a name from the source. The
real `merge_callback` and `_merge_and_send` are driven with the routes, the
database and Telegram faked: the allowance and the download record before
anything is fetched, a PDF fetching the photos and not the video, both limits
with small fakes (3 files, 1 MB -- the fetching stops at the file that passes
it), each refusal's message and code, and every file deleted on every ending,
a failed send and a file a route had already put on disk included. The wiring is
read from the syntax tree, as `test_payment_once.py` does: the handler is
registered, the `finally`, no `.filename` read, both limits from the environment
and in `.env.example`. Run against 26 deliberately broken copies of the code (in
`tests/.mirror`: alpha flattening off, the `finally` dropped, the MB cap skipped,
the item cap skipped, offsets off by one, every JPEG re-encoded, the EXIF turn
ignored, CMYK embedded as RGB, a remote name in the ZIP, the megapixel check
skipped, a decoded image kept, ...), every one is caught; the first run found one
that crashed the test instead of failing it, which was fixed. It needs Pillow,
and PyMuPDF for the rendering checks (each says so and skips without).

`test_video_shape.py` and `test_delivery_cache.py` joined them on 2026-10-12, with DownloaderBot's true video shape
and its five-minute delivery cache. The first: ffprobe's JSON read into a width, height and length (landscape and portrait,
a quarter turn from the display matrix and from the old tag, a non-square pixel, sizes and lengths that are missing, zero,
huge, text, NaN), the real `probe` on clips made with the ffmpeg here (one turned by a display matrix), a fake ffprobe that
never answers / says too much / fails (killed and reaped within the limit), and the real `_send_media` with Telegram faked:
the single video and every album video carry the shape, documents and photos never do, and after one timeout the rest of
the album is not measured. The second: the cache module on its own (fake clock, temp folder: life restarted by a use and by
the same link, yet a kept delivery goes 12 hours after it was kept however often it is used, a different link dropping at once, the latest-link rule, the two caps with least-recently-used eviction, the
disk floor, nothing deleted under a send, a failed move leaking nothing, 0 = off) and then the real `handle_link`,
`redeliver_callback` and `_resolve_and_send` with the routes faked: download then button, one fetch and a document of the
same bytes (photo and album too), the same link within and after the life, a different link, A-then-B, a failed send,
`/deletemydata`, the startup wipe and the sweep, `/status`'s line, logs without the link or the id. Together 284 checks;
48 deliberately broken copies (in `tests/.mirror`), 47 caught and 1 equivalent.

Four joined them on 2026-10-17, with DownloaderBot's "the same link again" work and the one-time ⚡ gift. None needs
a database or a network; `run.py` prints each under the title in quotes:

- `test_canonical_links.py` -- "one post is one link, however it was shared" (128 checks). `platforms.canonical_link`,
  applied at `detect_platform`, the one place a link enters the bot: the share-sheet trackers (`igsh`, `si`, `utm_*`,
  a fragment) dropped by each platform's allow-list, the several YouTube and Instagram forms of one post coming out as
  one link, a YouTube link with no 11-character ID left as it was, prose punctuation off the end, idempotence.
- `test_link_dedupe.py` -- "the same link twice while the first is going is one job" (67 checks). The same person
  sending the same link in the same chat while the first job is queued or running gets one 👌 reaction, no job and
  no charge; any refusal clears the mark; two copies handled at once make one job; a mark older than an hour is
  ignored; a job that outlives the hour leaves a newer copy's mark alone. Driven with the real `handle_link` and
  `_spawn_download`.
- `test_sent_files.py` -- "a post sent before is sent again by its file ids, and the table knows nobody" (291
  checks). A post delivered once is re-sent by Telegram file ID without a download; the table holds no user data;
  stale rows are re-confirmed with `resolvers.lookup`, at most 2 lookups at a time; any `BadRequest` from a stored-id
  send drops the row; only a fresh fetch stores IDs, never a disk-cache hit. The five database functions are an
  in-memory fake.
- `test_gift.py` -- "the one-time gift: counted first, given once, told only to who got it" (295 checks). The family
  command `gift` counts unless `--go`, gives once per person and tells only who got it; ManagerBot's `/gift` and its
  Give it / Cancel card, whose buttons carry a nonce so an older card can never give or cancel a newer gift.
- `test_manager_help.py` -- "ManagerBot's page per command" (559 checks, 2026-10-18). Twelve pages and four
  aliases: owner-only, plain text, out of the menus, every number read from the code that enforces it (each
  changed at runtime must show on its page); `/help <name>`; the page buttons under `/help` and `/start` (a tap
  by the owner sends the page as a new message, a stranger's is refused by `guard`, a forged name gets an alert,
  the handler sits before any catch-all callback, no `/help_<name>` command); the `--active` broadcast card; the
  corrected usage strings.

Broken on purpose, one way at a time, in a private copy of the code: 15, 16, 63 (plus 15 on the SQL) and 42 ways for
the four, and 15 more for the fixes after the fresh review of the same day; every one caught.

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
| Q | problems: always logged, a Report button only where it helps |
| R | reactions: passed to the twin bubble as the bot's own, both ways, and what cannot be |
| S | `/help` in two lengths, and the one-time note that reactions now travel |
| T | `/feedback`, and the reply to a guest it must never take |

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

Groups R–T joined on 2026-10-03. `sim.py` grew reactions for them: the
server keeps the bot's own reaction on a message and refuses what Telegram
refuses a bot (a second one, a custom emoji, an emoji outside the standard
set, with the real errors' names), and `Person.react()` sends a
`message_reaction` update with the old reactions remembered, the way Telegram
reports a change. Group J was rewritten the same day: its burst was sent
unaddressed, every message was refused for not being a reply before the rate
limit was ever reached, and "told to wait" passed on the word "waiting" in
the old long `/help`. It sends the burst as replies now and reaches the limit.

`sim.py` is not AnonBot-specific. Any bot in the family can be driven through
it with `sim.build(bot_module, tele)`.

**`python tests/help_scenarios.py`** — the family's `/help` template, in all
four public bots: `/start` and `/help` show the bot's own short help with one
button per command page (in the order of `HELP_PAGES`, two to a row, above the
full-guide button), the full-guide button turns it into the full guide in place
and back with the page buttons kept, and `/help full` and `/fullhelp` go
straight there. Since 2026-10-18 also the page per command, in all three
languages: a tap on each page button sends exactly one new message (the page,
plain text, previews off, no keyboard) and leaves the help message as it was, a
forged `helpdet:` name gets the "no longer exists" alert and sends nothing,
`/help <name>` and the aliases give the same page, a typed `/help_<name>` is an
unknown command, StickerBot's page tap works in the middle of a pack, the 4096
limit, and every price and limit on a page changed at runtime to prove it is
computed, not typed. Each bot runs its real `main()` on the simulator in a
process of its own (their modules share names), with `ENV_FILES=off` so no
bot's `.env` -- real tokens -- is read. It runs a bot from `tests/.mirror` when
there is one (to run a broken copy). Needs Postgres.

**`python tests/feedback_scenarios.py`** — ManagerBot's half of `/feedback`,
on the same simulator: each piece reaching the owner as it arrives and naming
nobody, muted alerts holding it back until the 22:00 report, the switch
moving it there, a long piece cut with a way to the rest, a long night of
feedback in messages of its own without costing the report, and a stranger
getting nothing. Needs Postgres.

**`python tests/credit_scenarios.py`** — 164 checks on the family's only
money arithmetic, against a real Postgres: the lifetime ladder (a person's
first 500 Stars at three times the ordinary rate, the next 500 at twice it,
priced piece by piece across a step, and the same whether paid at once or in
pieces), two payments at the same moment not both priced as somebody's first,
bonus credit expiring while ordinary credit never does, spending taking the
bonus first, corrections capping the bonus, that nothing takes a payment back,
and the ledger adding up from zero row by row. Since 2026-10-17 it also covers
the one-time ⚡ gift, `family_link.grant_bonus_once`: the claim, the credit and
an expiring bonus lot are one transaction; the claim is the one
`grant_stars_once` uses, so one key never pays twice; six threads racing on one
person credit once; and the ledger line names the gift's last day. Needs
Postgres; uses scratch user ids in the shared family tables and removes them
afterwards.

It pins the multiplier, the ladder and the expiry in its own environment rather than
reading them from the deployment, because the assertions are about the
arithmetic: a suite whose expected numbers move with a pricing change is a
suite that tells you nothing.

**`python tests/sent_files_db_scenarios.py`** — the `sent_files` table's SQL in
`downloader_bot/db.py`, against a real Postgres (the scratch cluster,
`TEST_DATABASE_URL`; it refuses a database that is not on this machine): 26
checks in each of 3 session time zones (Pacific/Kiritimati, Pacific/Pago_Pago
and UTC, because the row's day is the session's day). The table has exactly
`post_key`, `items`, `confirmed_at` and `last_used` and nothing about a person;
`init_db()` twice changes nothing; the upsert replaces the items and renews both
times; `sent_files_used` touches the day and not the confirmation, and neither it
nor `sent_files_confirmed` makes a row that is not there; `sent_files_drop` of a
missing row is no error; the stored time makes a row fresh and the same row 13
hours older stale; housekeeping (`prune_old_data`) deletes rows unused for more
than `DBOT_FILEID_KEEP_DAYS` days and nothing else; `/deletemydata` leaves the
table alone. `test_sent_files.py` checks the flow with these five functions
faked in memory; this is the SQL a fake cannot check. Needs Postgres; makes a
schema of its own and drops it.

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

The second half of the same file (2026-10-09) is what happens once the bots
*are* paused, against a fake `db` that records what is queued for them and
written for the owner. A crash anywhere after the pause -- before the push,
during it, after it, in the pause itself, Ctrl-C, even a resume that cannot
be queued -- still resumes the bots the way the normal path does and tells the
owner; a crash before the pause touches nothing. A git command that times out
is a failed step (a refusal before the pause, the failed-publish road after it),
never an unhandled crash, and a real push that hangs -- with a helper process
that holds its pipes, as `git-remote-https` does -- is stopped at the timeout
rather than when the helper lets go. Every git command runs without stdin and
with `GIT_TERMINAL_PROMPT=0`, `GCM_INTERACTIVE=never` and a timeout, and none
bypasses `run_git`. A folder that is not the release (uncommitted changes, the
wrong tag, none) is refused before anything is paused, as well as at the
window. And `scripts/run_release.cmd` names the TempTools python and refuses,
exit 2, to run with no arguments.

`tests/test_db_timeouts.py` is part of `run.py` and needs nothing but the
source. It reads every `ConnectionPool(...)` with `ast`: each carries
`connect_timeout` 10, `tcp_user_timeout` 30000, `statement_timeout=60s` and
`idle_in_transaction_session_timeout=120s` (search_path still first), so that one
query on a dead network path cannot hold one of the combined process's eight
threads for minutes (the incident of 2026-10-09 08:40 UTC); the poll lease's
connection has the keepalives and the unacknowledged-data limit and *neither*
server-side timeout, because a server that cut it for being idle would drop the
lease; and any other `psycopg.connect` in shipped code has to be listed with a
reason. **`python tests/db_timeouts.py`** is the half that asks a real Postgres
(scratch database): a pooled connection reports `1min` and `2min`, libpq was
handed the connection settings, a statement past a lowered limit is cancelled,
ManagerBot's `/sql` still runs under its own ten seconds and is cancelled at
ten, and the lease connection reports `0` for both. Needs Postgres.

**`python tests/init_locks.py`** -- every `ALTER TABLE` takes an ACCESS
EXCLUSIVE lock until the transaction ends, even one that changes nothing, and
`init_db()` is one transaction; so a start that only wanted to check used to
queue every reader and writer of the table behind it, during a deploy overlap
the old instance's. Now each ALTER is issued only when the catalog says it is
needed. For each bot that migrates its own tables, and for the `family` schema
through `family_link.init_family_schema()`: a fresh database has every column
with the right type and default; a second start sends no ALTER (the SQL is
recorded) but does look in the catalog; a second start finishes while a
transaction holds an ACCESS SHARE lock on every table (under an unconditional
ALTER it waits); and a database from before the migrations is brought forward
by exactly the ALTERs it needs, after which the next start is quiet again.
Needs Postgres; uses a schema of its own per bot and drops it.

`tests/test_ci_workflow.py` is part of `run.py` and reads the workflow as text:
every workflow that runs `tests/run.py` apt-installs `libarchive-tools`
(bsdtar) after `apt-get update` and before the tests, so the 7z and RAR
archive checks do not skip on CI.

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
