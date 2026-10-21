# v2.1.0 -- a review of everything, and what it found

A minor version. The owner, 2026-09-29: *"Do a broad code review to improve
anything. Literally anything."* -- and, once it was done, *"Revert changes in
2.0.0, create a new folder 2.1.0 and apply the changes there."* So this folder
is `BotFamily2.0.0` as it stood when the review began, plus what is below.
`BotFamily2.0.0` was put back to that state; the review's work left it.

A minor's file covers everything since the previous minor, which is 2.0.0
(published as MumuBots `v2.0.0` and MumuCombined `v2.0.0C`; production runs
`v2.0.0C` since 2026-09-28 21:15 UTC). Three things are in it -- the third
added on 2026-10-03, see the last section before *Not done yet*:

1. **The review** -- what it found by reading the code and then by *sending
   things at it*, each fix pinned by a new test that fails on the code as it was.
2. **One thing carried over** from `BotFamily2.0.0`'s working tree, where an
   earlier session left it uncommitted for the 2.0.0 cut: a conversion killed
   for memory is told as that (`CV-NO-MEMORY`). It is written up below so that
   this file is complete however 2.0.0 ends, and it is why a 4K HDR video sent
   to a 1 GB host now ends with a sentence about memory rather than one about
   an unreadable file.
3. **Reactions, /feedback, and a 22:00 report that says what happened** --
   AnonBot passes reactions between the two sides, its help comes in two
   lengths with a one-time note for existing users, every public bot has
   `/feedback` with ManagerBot delivering it as it comes or at night, and the
   nightly report names what the bots explained to people instead of calling
   it "Refused".

4. **Everything on the planned and offered lists that did not need the owner**
   (2026-10-07, after the Windows reinstall): a daily route check, yt-dlp kept
   current, a second YouTube route, 4K video brought down to what a 1 GB host
   can hold, archives, camera raw photos -- and a short list of what was left
   undone on purpose, with the argument. See the section before *Not done yet*.

5. **Reviews of all of it, and the fixes** (2026-10-07 to 2026-10-10): a fresh reading of everything
   above found stranger-controlled ways to take the process down, a release script that could leave
   the bots paused, and database waits without a limit -- all fixed. OCR and DownloaderBot's album
   buttons were built in the same weeks and moved to 2.2.0. See the section before *Not done yet*.

From the reinstall on the folders are named `MumuBots2.1.0` and
`MumuBots2.1.0C` (the owner, 2026-10-06: "name them MumuBotsX.Y.Z"); text below
that says `BotFamily2.1.0` was written before that and is left as it was.

The C edition of this version is `BotFamily2.1.0C` (now `MumuBots2.1.0C`),
ported with `scripts\bundle_c.py --base MumuBots2.1.0`. Nothing here is
committed or published.

Code and documents in this folder that point at `CHANGES_v2.0.0.md` mean the
record in `BotFamily2.0.0` (and at the `v2.0.0` tag): a version folder holds
only its own change file, so 2.0.0's is not carried here. The backlog at the end
of this file is 2.0.0's, carried forward with this version's additions.

---

## Carried over from 2.0.0: a conversion killed for memory said the wrong thing

- **A conversion killed for memory said the wrong thing.** The owner, on the
  4K question: *"Its fine as long as killed conversion gives the user correct
  info about what happened. Its not like theres going to be that many users
  who need to convert 4k hdr right now."* It did not: ffmpeg killed by the
  kernel counted as a file the converter could not read, and the person got
  *"I couldn't convert that file:"* followed by ffmpeg's stream listing --
  which read as if their file were at fault. Now it is an ending of its own,
  `no_memory` (`convert_utils.OutOfMemory`, and a `MemoryError` in the
  worker likewise): *"This one needs more memory to convert than this server
  has -- that happens with 4K and HDR video. Your file is fine: a version at
  1080p or lower will convert."* -- in all three languages, with the credit
  back, its code `CV-NO-MEMORY`, and no Report button (like `CV-TOO-SLOW`:
  the host's size is known, a smaller file is the person's to send), and no
  alert to the owner. Checked end to end: the real worker process with an
  ffmpeg that ends as a killed one does, through the queue, to the text in
  each language. `tests/test_conversions.py` (exit -9 and 137 on both ways
  ffmpeg is started, and an ordinary failure still a refusal),
  `test_jobs.py`, `test_problems.py`.

---

## A review of all of it, and what it found (2026-09-29, later)

The owner: *"Do a broad code review to improve anything. Literally
anything."*

It was done by reading, and then by **sending things at the code**, because
most of what is below reads as correct: a hostile YAML file, a PDF whose page
is 125 inches across, a phone photo with its orientation tag, a real yt-dlp
against a server that serves a video slowly, a real `httpx` against a server on
this machine. `pyflakes` over every folder is clean. The offline suite was
green before (57 checks) and is green after (62); every new test was run
against the code as it was and **fails there** -- what it failed on is under
each heading -- so none of them is a test that would have passed anyway.

Nothing here is committed.

### 1. DownloaderBot could be aimed at any address (security)

`PINTEREST_RE` ended in `pinterest\.[a-z.]+` -- any domain that begins
"pinterest." -- so `https://pinterest.attacker.example/pin/1` was a Pinterest
link. The pin resolvers fetch what they are given and follow redirects, read
`og:image` and `og:video` out of the page that comes back, and then fetch
*that*; and the client would connect to anything, `127.0.0.1` and the private
network the container sits on included. What it fetched was sent to the person
as their file -- as the file itself under the lossless setting. It was found by
reading `platforms.py` against `resolvers._pin_og`, and shown by asking the
old `net.client()` for a page on a local server: the server logged the hit.

Two changes, because either alone leaves something:

- **The pattern** now ends in a top-level domain and nothing else: `com`, a
  country code, `co.uk`, `com.au`. What it still allows is a domain a stranger
  registers under a country code Pinterest does not use (`pinterest.tk`), which
  reaches only public addresses now.
- **`net.py` refuses any name that resolves off the public internet** -- this
  machine, private ranges, link-local (a cloud metadata service lives there),
  shared address space, multicast, IPv6 unique-local (Railway's private
  network), and an IPv4 address written as IPv6 -- on every request and every
  hop of a redirect. The connection is made to the address that was checked,
  not to what the name resolves to a moment later, with the name kept in `Host`
  and as the name TLS verifies (httpcore's `sni_hostname`, read in its source),
  so a DNS server built for the purpose cannot answer the check one way and the
  connection another. A name that answers with a mixture is refused whole, and
  an address that does not answer is followed by the next one -- IPv4 first, and
  an address with another behind it gets 4 s to answer rather than the whole
  connect timeout, because the client this replaced raced a host's addresses
  and this one tries them in turn. `DBOT_ALLOW_PRIVATE_HOSTS=1`
  turns it off for a developer's own machine.

yt-dlp does its own networking and is not covered; what reaches it is a link to
a site the bot names. `tests/test_public_only.py`, 74 checks (10 fail on the old code): the address
table, the links, the transport against a mock (Host, SNI, IPv6, redirects to
an internal address, fallback), and a real socket. On the old code the real
socket check fetches `127.0.0.1`.

### 2. A download nobody was waiting for kept going

`resolvers._resolve` gives every route `DBOT_PROVIDER_TIMEOUT` (20 s) and
cancels it when it runs out. Cancelling a coroutine that awaits
`asyncio.to_thread` stops the *waiting*; the thread runs on. For a lookup that
is nothing. For yt-dlp, whose route is the download itself, it was a video
fetched to the last byte in the background, a file left in the work directory
until the container's next deploy (a download that fails part-way leaves its
`.part` and the two halves of a merge as well -- nothing removed those), and a
route marked failed for running long. Shown with the real yt-dlp: the old code
carried on for 31.5 MB after the caller stopped waiting, and left its `.part`.

And twenty seconds for the *whole* of a video is a limit no video is sure to
meet, and one no large download (up to 2000 MB, 2.0.0's headline) could meet at
all -- and yt-dlp is the only route for YouTube and for Reddit's video.

- The provider tells its thread to stop when it is cancelled; the thread stops
  at its next chunk (yt-dlp's progress hook raises `DownloadCancelled`) and
  removes everything it wrote (`video._remove_leftovers`). With the fix: stopped
  0.6 s after the flag, nothing left on disk. `download_video` now leaves only
  the file it returns, whatever ends it.
- A yt-dlp route is given time for a download: `DBOT_YTDLP_TIMEOUT` (90 s), and
  for a large one `DBOT_YTDLP_SECONDS_PER_MB` (0.5) more for each megabyte over
  the free ceiling -- 1,066 s at 2000 MB. Every other route keeps 20 s. **This
  is a change of policy, not only a fix** -- see *Put to the owner*.
- Beside it: the InstaFix probe asked for one byte with `Range` using `.get()`,
  which holds the whole body in memory whenever a server ignores `Range`; it
  streams now and reads the headers.

`tests/test_ytdlp_stop.py`, 20 checks, the last five against real yt-dlp.

### 3. ConvertBot: files built to hurt were refused as faults, or not refused

The worker turns a `ConversionError` into *refused* and anything else into
*crash*, and a crash is refunded **and reported to the owner**. Sent to
`data_convert` and `doc_convert`:

| file | before | now |
|---|---|---|
| a CSV with a cell over 128 KB | `_csv.Error`: a crash | refused, with a sentence |
| JSON nested 100,000 deep | `RecursionError`: a crash | refused |
| XML nested 5,000 deep | `RecursionError`: a crash | refused |
| YAML with an anchor that contains itself | `ValueError`: a crash | refused |
| YAML alias bomb, 7 levels (under 500 bytes) | about 570 MB and 5 s -- and **each level more is nine times that** | refused in 0.2 s |
| a CSV of 3 M blank lines | read whole (207 MB peak), then refused | refused at the limit (21 MB peak) |
| 5,000 rows of 900 columns under a one-column header | accepted: the cell limit counted the *header's* width | refused |
| a spreadsheet Excel calls a million rows | every row read before any was counted | streamed, blanks dropped as they pass |
| a PDF page 125 inches square (a file of 520 bytes) | drawn: 352 MP, a gigabyte | refused: `MAX_PAGE_MEGAPIXELS` (100) |

The alias bomb is the one that matters: `yaml.safe_load` keeps one object however
often an alias uses it, and writing it out as JSON expands every use. It is now
counted as it would be written -- by a walk that does not remember what it has
seen -- and a document over `MAX_CELLS` values (the limit a table already has)
is refused; the ordinary use of anchors, a shared block in a config file, is
unaffected (tested). The oversized page: MuPDF draws up to about 9,000 points
a side and refuses past 9,500, so it is this guard or nothing between 100
megapixels and a gigabyte; an A0 poster is 35 and still renders. A picture
embedded in a PDF over 50 megapixels is left out of a Word export, like every
other picture the writer will not take.

`tests/test_hostile_files.py`, 19 checks, 10 of which fail on the old code. It
deliberately stops at seven levels of bomb: at twenty, a regression would not be
a failed test but a machine out of memory -- which this session learnt by running
that level against the old code, and stopping it at 7.6 GB.

### 4. A phone photo sent as a file came out on its side

Nothing in the family applied EXIF orientation. A phone stores a portrait shot
as the sensor saw it, lying down, with a tag saying how to turn it; Pillow reads
the tag and does not act on it, and PNG, WEBP and a PDF page drop it. A photo sent
as a *photo* is turned by Telegram itself, which is why nobody saw it; sent as a
file -- which is how ConvertBot is used -- it came out lying down, in ConvertBot
(PNG, WEBP, JPG, PDF) and in StickerBot (the sticker). The owner's Galaxy S23
Ultra writes the tag.

`convert_utils.upright()` and StickerBot's `_upright()` apply it, once, on the
way in, and only when there is a turn to make. Found by grepping for
`exif_transpose` (there were none) and shown on a JPEG tagged 6. HEIC and TIFF
are turned by their decoders and say so in the tag (checked: not turned twice),
and HEIF/AVIF carry their own orientation in the container and are left alone.
Peak memory on a 60 MP rotated JPEG is unchanged (509 MB to PNG, 618 MB to WEBP,
before and after), because the copy is made and the original closed before the
next one.

`tests/test_orientation.py`, 24 checks, 14 of which fail on the old code: all seven turns **pixel by pixel**
against the EXIF specification's own table rather than against Pillow, an
untagged picture untouched, JPEG to four formats, a PDF page, HEIC, TIFF and
AVIF, and the sticker. Fails on the old code for every turn.

### 5. StickerBot's ffmpeg was capped on the encoder only

`_encode` put `-threads` after `-i`. ConvertBot found on 2026-09-29 that ffmpeg
sizes each pool from the host's cores -- 48 on Railway -- and that the cap has to
reach the decoder and the filters too; StickerBot was not looked at. A 4K clip
through its own command, measured: **528 MB** as shipped on this 16-core laptop,
**1,338 MB** with the input sized as a 48-core host sizes it (the container has
1 GB), **172 MB** with the cap before `-i`. The output is the same sticker (VP9,
512x288, 3 s, 46 KB). `tests/test_sticker_ffmpeg.py`, 5 checks, 2 of which fail on the old code.

### 6. `/sql` was read-only for one statement

`run_readonly_query`'s docstring said Postgres itself rejects a write, so nothing
depended on parsing the statement. It depended on one thing: that the string is
one statement. psycopg sends a query with no parameters over the simple
protocol, whose source says "it can execute more than one statement in a single
query" -- so `SELECT 1; COMMIT; DROP TABLE x` ended the read-only transaction on
its second statement and ran the third outside it. `prepare=True` sends it over
the extended protocol, where Postgres refuses more than one statement. Owner-only
behind `guard()`, so it is a mistake-and-hijack guard, not a hole. **Checked
against psycopg's source, not against a running Postgres** (none on this machine,
Docker being off): `python tests/smoke.py` against the local database is the
check that remains. `test_sql_safety.py` pins it.

### 7. The small ones

- **`big_files`: a login that could not be cached was a login that failed.** A
  full or read-only `/tmp` raised out of `_save_session`, which `_connect` reported
  as "the route is down" -- every big file refused for five minutes. Now logged
  and carried on. The `.partial` file also had one name for everybody, so two
  transfers logging in at once could rename each other's away; it is per process
  now, and created 0600 rather than chmod-ed afterwards. `test_big_files.py` has
  the case, and fails on the old module.
- **`lifecycle.PostgresPersistence.flush` forgot who was dirty before it had
  written them.** A database that was away for one pass lost every waiting
  user's saved conversation until they next did something -- the users most worth
  keeping, since a redeploy restores exactly them. Kept for the next pass now,
  without resurrecting anyone dropped in the meantime. `test_lifecycle.py`.
- **The poll lease's connection had no connect timeout**, and its deadline is only
  looked at between attempts, so a database that does not answer cost the
  operating system's several minutes rather than 15 seconds.

### Corrected on the way

Two figures this session put in writing and then took back, recorded so nobody
repeats them: deeply nested YAML is not slow (10,000 levels is refused in 0.6 s --
the 77 s was `tracemalloc`'s overhead on the recursion), and MuPDF does not draw a
900-megapixel page (it refuses from about 9,500 points; the dangerous range is
below that, and it is what the guard is sized for). A hostile-ffmpeg check was
also run and came back empty: a playlist named `x.mp4` is not followed by this
ffmpeg, so no `-protocol_whitelist` was added.

### Looked at and deliberately not changed

- **A Stars payment delivered twice would be credited twice.** `_credit_topup`
  marks the row paid and calls `topup()`, neither checks `charge_id` against what
  it has credited, and `grant_stars_once` already shows the shape of the answer (a
  claim in `family.settings`, taken in the same transaction as the credit). It is
  rare -- python-telegram-bot confirms an update before its handler runs, so a
  kill mid-handler loses the update rather than repeating it -- and it is money
  code that cannot be run here (no Postgres): a mistake in it is people paying
  and not being credited, which is worse than what it prevents. Left, with the
  design, in *Not done yet*.
- **Dependencies are `>=` everywhere**, so any build can pull a new major
  release; the Dockerfile's `import bot` catches an import that breaks and
  nothing else. A `constraints.txt` from the versions running in production would
  close it; that needs the versions.
- **There is no CI.** MumuBots and MumuCombined are public, `tests/run.py` needs
  no database, and a workflow that runs it on every push costs nothing.
- **`_get()` reads a whole response body**, so a page a stranger controls could be
  large. After the Pinterest pattern is fixed the pages are the platforms' own.

### Put to the owner

1. **The yt-dlp time limit is a policy change** (§2): 20 s to 90 s, and longer for
   large downloads. It is what makes 2.0.0's large downloads work through yt-dlp
   at all, but it is your rule about how long a person waits. `DBOT_YTDLP_TIMEOUT`.
2. **`/sql`'s `prepare=True` is checked against psycopg's source, not a running
   Postgres** (§6): `python tests/smoke.py` against the local database is what
   remains.

---

## The price of a 4K HDR conversion, and why it failed (2026-09-29, from a screenshot)

The owner sent ConvertBot `20260928_011213.mp4` -- 224 MB; probed: 3840x2160,
HEVC Main 10, PQ / BT.2020, 59.94 fps, 15.7 s, 120 Mbps -- to MOV. The quote:
*"224 MB in · about 235 MB out · Cost: 39 ⚡ ... The converted file can be up to
705 MB."* Then the conversion failed with `CV-REFUSED`: *"Conversion failed:"*
and ffmpeg's stream listing, the 39 ⚡ back on the balance. Two questions: why so
cheap, and why did it fail.

**Why 39 ⚡** (reproduced with `pricing.py` and `jobs.py`, not estimated). The
price is the job's worst case plus a markup. The time limit for a job this size
is capped at 600 s, and the most it may send back is 3x its estimate, 705 MB:

| | |
|---|---|
| 600 s of two cores and 1.5 GB (Railway's published rates) | $0.0127 |
| up to 706 MB sent, at $0.05 a GB | $0.0345 |
| worst-case cost, in ⚡ at the cheapest a ⚡ is ever bought (6 a Star) | 22.3 ⚡ |
| markup, 5·(22.3/5)^0.8 | 16.5 ⚡ |
| **price** | **39 ⚡** |

MOV is not special. MOV, MP4 and MKV share a work factor of 2.0 and a size ratio
of about 1.05; the target only enters through those two numbers. **Resolution,
frame rate and HDR do not enter at all** -- the quote is from bytes, which is
the item *Prices from the clip, not the bytes* in `PLANNED.md`. And because the
time limit is already at its 600 s ceiling from about 200 MB, a bigger file only
raises the price through what it may send back: MP4 to MOV is 7 ⚡ at 10 MB, 14
at 50, 24 at 100, 39 at 224, 70 at 500, and 84 from 1000 MB to 2000.

**Is that below what it costs?** No. The bot's own ffmpeg command on this file,
run here with the thread cap on: 65 s, and a **37 MB** result. That is about
$0.0014 of compute and $0.0018 of egress, $0.003 in all. 39 ⚡ is $0.084 if it
was bought at the cheapest rate and $0.25 at the ordinary 2 ⚡ a Star -- 26 to
80 times the machine's cost. It is low because Railway's compute and egress are
cheap, not because 4K or MOV were overlooked (4K, 60 fps and HDR were, but they
would move the price by cents). Whether 39 ⚡ is a *fair* price for the work is
the pricing rule's question, which is the owner's.

What is off is the estimate: *"about 235 MB out"* against 37 MB. The size
ratios are per category, and a 120 Mbps HDR10 source becomes about 20 Mbps of
H.264, so the quote's 705 MB limit was about nineteen times the real result --
and it is the price's largest term, since the worst case is priced. The source's
bitrate is one more field in the ffprobe call the bot already makes for the
duration, and would put the estimate far nearer.

**Why it failed.** The job needs more memory than the host has. Peak memory of
the conversion, measured here with ffmpeg's threads capped at two as the bot
does:

| | peak | time | result |
|---|---|---|---|
| as the bot runs it: 4K, HDR tonemapped | 1,449 MB | 60 s | 38 MB |
| 4K, no tonemap (the SDR path) | 1,143 MB | 50 s | 29 MB |
| scaled to 1080p first, then tonemapped | 607 MB | 48 s | 9 MB |

Through the bot's real path (its process, the worker and ffmpeg) the peak was
1,490 MB. The container has 1 GB. So the kernel ends ffmpeg with SIGKILL, and a
killed process has no last words but what it had already printed: the stream
listing -- which is exactly what the screenshot shows, and exactly the wording
problem the `CV-NO-MEMORY` change (carried over above) fixes. **4K60 at 10 bits
does not fit in 1 GB even without the HDR tonemap; the tonemap adds about
300 MB; at 1080p it fits with room.** This machine finishes the job because it
has memory to spare; a Windows job limit set to 1 GB did not enforce here, so
the kill itself was not reproduced -- the evidence is the measured need against
the container's size, and Railway's own earlier measurement that 4K HDR is killed
and 1080p fits.

The screenshot's wording says production is not yet running this code. It
should, once deployed, say: *"This one needs more memory to convert than this
server has -- that happens with 4K and HDR video. Your file is fine: a version at
1080p or lower will convert."* (`CV-NO-MEMORY`, in all three languages, credit
back, no Report button). Production is, by these notes, still 1.7.0, which also
sizes ffmpeg's encoder from the host's 48 cores -- there an ordinary 1080p video
was killed too (*Railway, measured*). (Corrected 2026-10-03: production moved
to 2.0.0C at 2026-09-28 21:15 UTC. Neither 1.7.0 nor 2.0.0C has `CV-NO-MEMORY`,
so the screenshot reads the same from either.)

Nothing was built for this. The choices, each the owner's:

- **Leave it**, as decided on 2026-09-29: the person is told, the credit is back.
- **Say so before charging.** The bot already runs ffprobe on every video; 3840
  pixels wide, 10-bit or HDR is visible at the quote, and the quote could say the
  host cannot take it -- no minute of waiting, no price shown for a job that
  will be killed, no charge to refund.
- **Tonemap at 1080p** when the host cannot take 4K (607 MB, measured), with the
  result saying so. It changes the output's resolution, which is the trade the
  owner was asked about on 2026-09-29.
- **A plan with more memory** a service.

---

## ManagerBot's menu had lost `/providers` (2026-09-29, from a screenshot)

The owner typed `/pro` in ManagerBot and was offered `/problems` and nothing
else; `/providers` -- the command whose answer is on the screen behind the
popup -- was not in the menu. And `/problems`' description was cut at "...the
midnight report".

**Why.** The 2026-09-20 pass over the menus (*Two broken commands, and a menu
that had grown*, in 2.0.0's record) took ManagerBot's from 37 entries to 27 at the
owner's request to "converge" the list, and dropped `/providers`, `/probe`,
`/stars` and `/crashtest` on the reasoning that `/run` reaches them. It does not,
in the way a second name does: `/run` needs the bot and the command spelled out,
and the shortcuts exist so that nobody has to. Worse, `tests/test_menu.py` was
told they were "aliases of `/run`" and exempted them -- in a docstring three
lines above the exemption, the test says that exactly this ("`/probe`,
`/providers`, `/stars` and `/crashtest` handled and unlisted") was the failure it
was written for. And the comment over the menu said *"Every command ManagerBot
handles is here"* while the list beneath it said otherwise.

**What changed** (`manager_bot/bot.py`, `tests/test_menu.py`):

- `/providers`, `/probe` and `/stars` are in the menu again, beside the commands
  they belong with. `/crashtest` stays out **on purpose, and says why**: it makes
  a bot raise and page the owner, and a tap on a menu entry sends the command at
  once, so a stray thumb would send an alert. It is now an explicit exemption with
  that reason and a check that it is still handled and still unlisted, not a
  false "alias".
- `/problems` and `/idle` were 54 and 53 characters and are 39 and 43. A
  description is kept under 50: Telegram's limit is 256, but the menu is a popup
  that cuts a long line where the window ends -- at about 50 in the owner's
  window. Every other description in the family, in all three languages, was
  already under it except `/language` (51: three languages in one line, on
  purpose, and exempt).
- The comments over the menu are true now, and name the recurrence.

The four public bots were checked the same way and were already right: every
command each handles is in its menu or is a language switch or a renamed
command, with a reason on file, and every owner-only command is in the owner's
menu. `test_menu.py` now has 185 checks (was 175), and 5 fail on the old menu:
that ManagerBot offers everything it handles (naming `probe`, `providers` and
`stars`), each of those three on its own, and that its descriptions fit the
popup (naming `/problems` and `/idle`).

**It appears after ManagerBot restarts.** `set_my_commands` runs at startup, so
the menu changes when the new code is deployed -- for production, that is the C
edition, where nothing ships alone -- and a chat may need reopening to show it.

---

## Reactions, /feedback, a 22:00 report that says what happened (2026-10-03)

The owner, in one message before going out: AnonBot should pass reactions from
one side of a conversation to the other; its help "is already too long. Make a
brief short one, and another one with full information"; a one-time note for
everybody who was already using it; a floating notice for a reaction that
cannot travel; a `/feedback` command, with "a switch for me to decide if i want
each feedback to arrive to me or only grouped in a nightly report"; the
nightly report at 22:00 "instead of 24.00"; and the wording of "Refused" fixed,
"this and every other similar case". "Do everything locally only for now."

Nothing is committed. Production (`v2.0.0C`) is unchanged.

### Can a bot even see a reaction in a private chat? Yes -- tested, not read

The Bot API says a bot receives `message_reaction` only "where it is an
administrator", which a private chat has no notion of, and a write-up online
reported getting nothing in one. So it was tried before anything was built: a
probe on TestBot (`allowed_updates` = message + message_reaction) with the owner
reacting. Telegram delivered the reaction within a second (chat type
`private`, user and both lists set), and `setMessageReaction` putting the same
emoji on the owner's own message returned ok. That is the whole mechanism.

### AnonBot passes reactions on

A reaction on a bubble of a conversation is put, by the bot, on its twin in the
other chat (`relay_reaction`). It appears beside the bot's picture, never the
reactor's -- as anonymous as the messages. Changing it changes the twin's;
taking it off takes the twin's off. Both directions. The twin is already known:
every relayed bubble has had its counterpart in `anon_relay.peer_message_id`
since v1.6.0, so the lookup is one query (`db.get_reaction_route`: the twin,
both sides, and the block, in one join -- the database is on another
continent). Nothing about a reaction is stored. `allowed_updates` now asks for
reactions by name: an empty list means every kind *except* them.

What cannot travel is Telegram's limit, not a choice: a bot sets one reaction
per message, from the 73 standard emoji (`anon_logic.RELAYABLE_REACTIONS`,
shown in `/help full`), never a custom emoji that is not already on the message
(a Telegram Premium one, in practice) and never a paid one. Of several, the one
just added is the one passed on. Not passed on: a blocked conversation, the
bot's own notices, a row from before twins were recorded.

### The floating notice the owner asked for does not exist for reactions -- the nearest thing does

Telegram's fading pop-up is `answerCallbackQuery`: it exists only as the answer
to a button tap, and a reaction is not one. Bot API 10's ephemeral messages
are for groups and supergroups. So the note is a **silent message that deletes
itself** after `ABOT_REACTION_NOTE_SECONDS` (8 -- it was 6 until the owner's
answer below): "⚠️ The other person won't see that reaction — only standard
emoji are passed on." or "⚠️ The other person sees only your newest reaction —
one per message is passed on." At most one per person per 30 s
(`ABOT_REACTION_NOTE_EVERY_SECONDS`), so a run of Premium taps is one note. It
carries its code line (`AN-REACTION-NOT-PASSED`) under the standing rule that
every refusal shows its code; the owner, asked whether to drop it: "Being one
line short is not a strict requirement, just make sure the users will be able
to read the most important info" -- hence the sentence that opens with what the
reader has lost, and the two extra seconds for somebody who reacted far up the
chat and has to scroll down to it.

### Help in two lengths, and the note that reactions are new

`/help` and `/start` show a short version (about a third of the old length):
the inbox, the reply rule, the guest's side, and that reactions travel --
standard emoji only, not Premium ones -- with a **Full guide** button. The full
guide (`help_text`, still the one `test_menu.py` holds to naming every
command) gained a Reactions paragraph listing all 73 and a "⚠️ NOTE: Telegram
Premium reactions (custom emoji) can NOT be passed on" in the warnings' style.
`/help full` goes straight to it; a **Short version** button goes back. The
Russian full guide had lost the reply rule in translation ("easy not to mix up
conversations" where the others say a reply is *required*, and nothing about
the guest replying after the first message); rewritten from the English while
there.

The one-time note -- "🆕 You can now react to messages here — the other person
sees your reaction, shown as mine. Send /help to see more." -- is the owner's
"check box in the database": `user_settings.knows_reactions`. Two ALTERs in a
fixed order: the column added without a default, so every existing row is NULL
(owed the note), then `SET DEFAULT TRUE`, so everybody from now on starts out
knowing. `db.claim_reactions_note` reads and ticks it in one statement, so two
updates at once cannot both send it. It comes after whatever the person came
for (a handler in group 1), never between a question and its reply box, and
not after `/help` or `/start`, which say it already. A reaction ticks it too.

### /feedback, in every public bot

`/feedback <text>` sends it at once; `/feedback` alone asks, with a ForceReply.
**The answer is taken only when it is a reply to that question.** That rule
exists for AnonBot, where nearly every message is a reply: an owner who taps
`/feedback` and then answers a guest before writing must have the answer reach
the guest, not the developer -- tested, and proven by mutation (a version that
takes any message while waiting fails three checks). A photo sent as the answer
is asked about again, in words; `/cancel` offers the pending question. Five a
day per person (`FEEDBACK_PER_DAY`, `FM-FEEDBACK-LIMIT`); a database that is
away says so with a Report button (`FM-FEEDBACK-FAILED`).

What is kept is `family.feedback`: the words, the bot, the language, the time,
and when the owner was sent it -- **no user id and no username**, on the
standing rule that nothing reaching the owner unasked identifies anybody. Gone
after 30 days (`FEEDBACK_RETENTION_DAYS`), pruned by every bot's housekeeping.
Because nothing in it says who wrote it, `/deletemydata` cannot find it to
erase it sooner, and every bot's `/privacy`, `PRIVACY.md` and (in C) `LEGAL.md`
now say exactly that. AnonBot's said "what anybody writes is never stored"; it
now names `/feedback` as the one exception.

The awaited-input handler every bot registers in group -1 became
`awaited_text_received` (feedback, then the custom donation amount): one handler
for both, because python-telegram-bot runs one handler per group and both match
the same text. Its filter grew `| filters.REPLY`, so a non-text answer to the
feedback question is caught; anything else still passes straight through.

### ManagerBot: the switch, and the report

Feedback arrives **as it comes** by default -- "💬 Feedback · ConvertBot · uz ·
time · #id" and the words, escaped -- on the event pump's sweep, under the same
mute as alerts. `/feedback` shows the latest ten and the switch
(🔔 As it arrives / 🌙 With the 22:00 report, `family.settings` key
`feedback_delivery`); `/feedback instant|nightly` sets it by hand and
`/feedback <id>` shows one in full. In nightly mode the report carries them in a
section of its own; in instant mode it carries whatever a mute held back, and
otherwise one line counting the day's. A night with more than fits goes in
messages of their own, because cutting one HTML message at 3,900 characters can
land inside a tag and lose the whole report.

The report goes out at **13:00 UTC, 22:00 in Korea** (`MBOT_NIGHTLY_UTC`; no
daylight saving there since 1988). Production sets no override, so it moves
with the deploy. "Midnight" is gone from the menu, `/help` and comments.

### "Refused", and every case like it

The owner's report said "Refused — 2 · AnonBot 2 · people told no by a bot that
was working correctly" about two messages sent without a swipe-reply. Nobody
had been refused anything. Level 3 holds five different things, and each is
now named for what it is (`problems.KINDS`, every one of the 70 codes in
exactly one, held by `test_reporting.py`):

| kind | in the report |
|---|---|
| 💡 guided | **Shown how to use it** — sent something the bot couldn't take, and were told what to do instead |
| 📏 limit | **Hit a limit** — too big, too many, too fast, not enough ⚡, or something not offered — each by design |
| ⌛ gone | **Out of date, or not theirs** — an old button or link, a record that had gone, or somebody else's button |
| 🚫 other side | **Blocked or unavailable** — the other person blocked them, paused or deleted their inbox, or never started the bot |
| ⏸ paused | **Turned away during an update** — came while /pause was on |

Each kind lists its codes with their titles and counts ("AnonBot ·
AN-NEEDS-REPLY ×2 — Message did not say which conversation"), six at most
before the rest are summed; never their incidents. `/reports` and `/report`
show the kind in place of the level's bare name, and the level itself is
"explained" where it was "refused". Stored levels are untouched -- the column
still says 3, so last month's reports read the same.

### Tested

- **A real Postgres, for the first time this version.** Postgres 17 is
  installed on this laptop but its Windows service needs admin rights to
  start, so a throwaway cluster was made from the same binaries in the session
  scratchpad (`initdb`, port 5433). Everything that needs a database ran
  against it: `anon_scenarios.py` (305 checks, groups R–T new), the new
  `feedback_scenarios.py` (23), `smoke.py`, and every older database script.
- **`/sql`'s `prepare=True` is now checked against Postgres** (the item *Put to
  the owner* #2 below, open since the review): `smoke.py` sends two statements,
  a write hidden behind a COMMIT, and a plain write; all three are refused and
  nothing gets out. Without `prepare=True` the check fails.
- **The migration order is checked the way it runs live**: a table from before
  the column, two people in it, `init_db` -- they are owed the note once,
  somebody new never. With the default given in the same ALTER it fails.
- Offline: `test_reactions_feedback.py` (new, in `run.py`), `test_reporting.py`
  (new checks fail cleanly on the old report code), 63 of 63.
- **A broken test found on the way**: group J's "the sender is told to wait"
  had been passing on the word "waiting" in the old long `/help`, while every
  message of its burst was refused for not being a reply before the rate limit
  was reached. It sends the burst as replies now and reaches the limit.

### Put to the owner

1. ~~**The note that removes itself shows its code**~~ -- **answered**: keep
   it, as long as the important part can be read (see the note above).
2. **Telegram has no floating notice for a reaction** -- only for a button tap.
   The silent, self-deleting message is the nearest thing; it does reach the
   chat list for those eight seconds.
3. ~~**A reaction on your own message** is passed to its twin, which in the
   other chat is the bot's copy -- so the bot reacts to its own message.~~
   **Settled the same day**, on Telegram itself with TestBot (one silent message
   to the owner's TestBot chat, deleted after): a bot may react to its own
   message in a private chat; the same reaction twice is fine; an empty list
   takes it off, twice too; a custom emoji and an emoji outside the bot's set
   both answer `REACTION_INVALID` (the words `relay_reaction` looks for); two at
   once answer `REACTIONS_TOO_MANY`; a missing message, "message to react not
   found"; and ❤‍🔥 is taken with or without the variation selector.

### The owner's answers, built (2026-10-03, later)

1. **The reaction note** -- kept its code; opens with what the reader has lost;
   8 s (above).
2. **Feedback is one-way.** "Make the feedback one sided. I can only see the
   text copied and sent by the bots. Make sure its mentioned anywhere that the
   feedback is read only for me." No reply route, and the opt-in reply offered
   that day is settled as declined. Every place feedback is mentioned now says
   that only the person who runs the bots reads it and nobody can write back:
   the question, the thank-you, the `/feedback` line in all four full guides,
   the bullet in all four short ones, `/privacy` (all four bots, three
   languages), the four `PRIVACY.md` and `LEGAL.md` ("one-way"), the READMEs,
   and on ManagerBot's side the message, the overview, the nightly section and
   `/help` ("read only: it names nobody, so there is nobody to answer").
3. **One help template, every bot.** "Yes all bots should be similar or share a
   similar template." The toggle is `shared_features` now
   (`help_keyboard`, `help_toggle`, `wants_full_help`, `HELP_TOGGLE_PATTERN`
   `^help:(full|brief)$`); StickerBot, ConvertBot and DownloaderBot have a
   `help_brief` in AnonBot's shape -- "How it works:", a few bullets, the same
   `/feedback` bullet in every bot, "Main commands:", and the full guide a tap
   away -- in three languages, and AnonBot moved onto the same helper. Each bot
   keeps its own buttons under either version (StickerBot's New pack / My packs,
   and the sibling bots on `/start`); StickerBot's old Help button is the
   full-guide button, and a Help button on a start menu sent before this still
   opens the full guide. Found on the way: StickerBot's welcome line still said
   it downloads Instagram and TikTok videos, which moved to DownloaderBot long
   ago -- removed in all three languages.
4. **Not finalised**, on the owner's word.

The "Hit a limit" line now says what it covers: "too big, too many, too fast,
not enough ⚡, or something not offered — each by design".

Tested: the new `help_scenarios.py` drives all four bots' `/start`, `/help`,
the toggle both ways and `/help full` on the simulator, each in its own process
(11 checks a bot); `test_reactions_feedback.py` grew the template in every bot
and the one-way wording in every place, every language. All 63 offline suites,
305 AnonBot scenarios, 23 feedback scenarios, `smoke.py` and every older
database script pass; the C edition is rebuilt, `--status` clean, `--lint`
clean, `main.py --check` passes and all nine files parse under Python 3.11.

---

## "Do everything that does not need me" (2026-10-07)

The owner, 2026-10-06, after the Windows reinstall (`MumuBots2.1.0` and `MumuBots2.1.0C` were recreated from his backup and found complete: 63 of 63 offline checks, every item of the 2026-10-03 list present, uncommitted on top of `v2.0.0` / `v2.0.0C`): *"start implementing anything from the planned and offered lists that does not require me personally ... skip website ... Anything you think is good for the project, do it, when you are absolutely sure it's beneficial, even if it was never asked. Also try to think outside the scope of what I ask for ... real critical thinking ... criticizing my ideas if they are not beneficial, or unrealistic."*

Everything below is in `MumuBots2.1.0` and ported to `MumuBots2.1.0C` (`scripts\bundle_c.py --base MumuBots2.1.0`: nothing lags, lint 0, `main.py --check` passes with every setting blanked; every C section compiles under Python 3.11, the image's version). Nothing is committed or deployed. The offline suite is **68 of 68**, up from 63: `test_routewatch.py`, `test_video_fit.py`, `test_archives.py`, `test_raw.py` and `test_payment_once.py` are new; `test_ytdlp_stop.py`, `test_conversions.py` and `test_untrusted_names.py` were adjusted for the new categories and the cobalt route. Each new test file was run against its own deliberately broken code (**12/12, 10/10, 22/22 and 18/18** breakages caught), and several of those runs found tests that passed for the wrong reason -- two guards masking each other -- which were then sharpened.

### DownloaderBot: knowing before a user does

- **A daily check from inside the container** (`downloader_bot/routewatch.py`, `/run downloaderbot routewatch`). A few minutes after start and then at most once a day (a redeploy loop does not mean a probe per deploy), each platform's chain is walked in order until a route both resolves *and serves its media* (64 KB are pulled back). That is one or two requests a platform, not a survey: yt-dlp, which costs 40 MB to load, is reached only when everything ahead of it failed. A platform is `up`, `last_resort` (only the final route works), `down`, or `sample_gone` (every route says the sample post is missing -- a deleted sample is not an outage). The owner hears about **changes**: `down` and `last_resort` when they begin, `down` again after three days, a recovery is recorded and not pushed. **YouTube is checked and never pushed**: it is unadvertised, and "YouTube refuses a datacenter" is its normal state, not news.
- **yt-dlp, the route that rots.** The same check asks GitHub for yt-dlp's newest release and says, once per release, when the container is 14 or more days behind. `DBOT_YTDLP_AUTOUPDATE=1` lets the bot fetch it itself -- `pip install --no-deps --only-binary` into a folder of its own, imported in a child process to prove it is the release asked for, then swapped in while no download is running (a download holds a slot; the swap waits for none and gives up after five minutes) -- once the release is `DBOT_YTDLP_COOLDOWN_DAYS` (3) old, which is also the window in which somebody else would notice a bad or hijacked release. **Off by default**: it is code arriving from PyPI without a deploy, which is the owner's to allow; turning it on is one variable.
- **A second route for YouTube**: the cobalt route from the unmerged cloud branch `claude/gifted-dirac-yvlsgy` (2026-09-25), ported with its error-code table and tests. **Honest about what it is**: probed 2026-10-07 from a residential address, of the three default public instances **one** answered, one said `error.api.youtube.login` and one `no_valid_content`. Every one is somebody's hobby server. It is an extra route with the chain's ordinary fallback, not a fix; a cobalt of the owner's own (a small container) put first in `DBOT_COBALT_INSTANCES` is the robust version. The branch's reworked Report dialog was **not** taken -- see *Not built, and why*.
- **Cookies as a variable** (`DBOT_YT_COOKIES` and the other four, `DBOT_COOKIES`): the cookies.txt pasted whole, written once to a file only the process can read -- on Railway a file is not an option and a variable is. Ported from the same branch.
- **`yt-dlp[default]>=2025.11.12`**: the extras yt-dlp itself recommends since 2025.11 (`pycryptodomex`, `curl-cffi` for TLS impersonation, `yt-dlp-ejs`, brotli, websockets, mutagen); licences recorded in `LICENSING.md`. **Not done**: a JavaScript runtime (deno is a 41 MB wheel and about 100 MB of image) which `yt-dlp-ejs` needs to solve YouTube's challenges. YouTube is the least reliable platform and unadvertised, and the benefit cannot be checked from a residential address; one line to add if it is wanted.
- **`resolvers._get()` is capped** (`DBOT_PAGE_LIMIT_MB`, 8): a provider is somebody else's server, and an answer past the cap -- declared, or never declared and streamed -- is refused instead of being read whole. Both guards are tested on their own.
- **CI**: `.github/workflows/tests.yml` runs `tests/run.py` on every push on Python 3.11 and 3.12 with read-only permissions and no secrets. **It has not run on GitHub**: the suite had only ever run on Windows. If the first run is red, read it before believing either side.

### ConvertBot: what a small machine can and cannot do

- **A 4K video is brought down to 1080p when it must be.** Measured 2026-10-07 on a synthetic 3840x2160 clip with the bot's own ffmpeg arguments (peak resident memory of ffmpeg): **HDR to MP4 1,334 MB, HDR to WEBM 1,655 MB, SDR to WEBM 1,182 MB** -- a kill on a 1 GB container, which is `CV-NO-MEMORY`, and the owner's S23 Ultra films 4K60 HDR. An HDR video over 2.9 megapixels, and any such video going to WEBM, is now scaled into a 1920x1920 box *before* the tonemap, whose cost is the picture's area: **558 / 667 / 430 MB**, and 2 to 4 times faster. A 4K SDR video to MP4, MKV or MOV keeps its size (887 MB: it fits, narrowly -- measured, and left). The menu says so *before the price*, in three languages ("converted at 1920x1080 -- the full picture needs more memory than this server has"). This is the fallback the 2.0.0 notes named ("converted at 1080p with the result saying so"). `CONVERT_VIDEO_FIT_PIXELS=0` turns it off.
- **Archives**: ZIP, TAR, TAR.GZ, TAR.BZ2, TAR.XZ and 7z in and out, RAR in only (`convert_bot/archive_convert.py`; format names `zip tar tgz tbz2 txz 7z rar`, and `photos.tar.gz` is read as `tgz`). ZIP and TAR are the standard library's; 7z and RAR are libarchive's `bsdtar` (one system package, `libarchive-tools`, added to both Dockerfiles; without it 7z and RAR are simply not offered). The guards are the ones `PLANNED.md` designed, each checked on its own: 10,000 entries, 1,000 MB expanded (counted on the bytes written as well as on the index), 200-fold per ZIP entry, every name checked (`..`, absolute paths, drive letters, device names and control characters refuse the whole archive), links and devices dropped and never followed, an encrypted archive refused ("the bot never asks for a password"), an archive inside an archive carried as bytes, owner and permissions of the sender's files not carried over, the work folder removed on every path, and for 7z and RAR a watcher that stops `bsdtar` mid-extraction at the limit. **Found while testing and fixed**: libarchive's ZIP reader takes *any* file with a ZIP end-marker in its last 16 KB, so a RAR (or 7z) whose last stored file is a ZIP -- which RAR does all the time, a ZIP not being compressible -- is read **as that ZIP**: no error, a clean listing of the inner archive, everything else silently gone. Measured on a RAR5 file built for the purpose. The file's own signature must now match its extension, and `bsdtar` reads a copy with more than 16 KB of padding after the archive's end. Also found: ZIPs written by Python's `writestr` carry permissions without a file-type bit, which a first version of the symlink check took for "not a plain file" and dropped.
- **Camera RAW**: CR2, CR3, NEF, ARW, DNG, RAF, ORF and RW2 to JPG, PNG, WEBP, AVIF, HEIC and a **16-bit TIFF** (`convert_bot/raw_convert.py`; rawpy, LibRaw, MIT; it brings numpy, about 40 MB of image, imported only when a raw photo arrives). The camera's white balance, no auto-brightening, sRGB, AHD demosaicing, the camera's own orientation. **`PLANNED.md` was wrong about memory** ("the full picture up to 100 MP"): measured on synthetic DNGs, a full decode takes about **13 MB a megapixel** -- 24 MP 346 MB, 50 MP 684 MB, **100 MP 1,334 MB** -- and a half-size decode 147 / 271 / 509 MB (200 MP: 984 MB). So: full up to 40 MP, half size up to 110 MP with the size said in the menu before the price, a refusal past it with nothing charged. The TIFF is written by the module (Pillow cannot write 16-bit RGB) and read back by an independent reader against LibRaw's own output, value for value. Pixel-based size estimates know that a raw TIFF is six bytes a pixel, not the 0.6 an ordinary TIFF is estimated at. The six formats are never offered when rawpy is missing.

### A Stars payment delivered twice is credited once

The owner read this offer as "enabling USD or UZS" and said to skip it; it is not that. It is the guard from *A review of all of it* ("Looked at and deliberately not changed"): nothing checked Telegram's `telegram_payment_charge_id` against what had been credited, so a `successful_payment` update Telegram delivers again -- rare, since python-telegram-bot acknowledges an update before its handler runs, but possible after a restart in the middle -- would have credited the same payment twice. Now `family_link.topup(..., charge_id)` stakes a claim `stars:paid:<charge_id>` in `family.settings` **in the same transaction as the credit**, so a claim cannot outlive a failed credit (the thing `grant_stars_once` has to undo by hand) and two deliveries at once cannot both win; a repeat returns `None`. Both real-payment paths use it -- the shared donation path and ConvertBot's paid-conversion path, where ignoring a repeat would have started the conversion a second time -- and tell the owner once ("delivered twice and credited once", no user in it). The sandbox, which charges nobody, passes no charge id and credits as before. **Tested against a real Postgres** (`credit_scenarios.py`: a repeat credits nothing and moves nothing, six deliveries at the same moment credit once, a failure between claim and credit leaves no claim and the redelivery then credits) and by `test_payment_once.py` (every caller passes the id and returns from its `is None` branch before reading the result -- structurally, from the syntax tree); 7 of 7 breakages caught. It needed a scratch Postgres 17.6 in `TempTools\postgres`, which also re-ran all eleven database suites: green, same counts as 2026-10-03.

### Not built, and why (the owner asked for criticism)

- **Sleeping the quiet bots.** The family's own table says it would save about $3 a month, in exchange for webhooks on every service, a 5 to 15 second wait for whoever messages a sleeping AnonBot (the busiest bot, whose entire promise is "one message reaches one person"), and a new failure mode where a stuck wake-up looks exactly like a dead bot. The C edition already did the real work: 77 MB against 302 MB. Recommended **dropped**, not deferred.
- **The standby service.** A second service holding every bot's token to say "back in a minute" is a second place for secrets and a way for a lease bug to hold the real bot's token hostage -- for a failure (a deploy that never comes up) that Railway may already be able to contain: its documentation says a deploy whose HTTP health check does not pass in time "will be marked as failed" (default 300 s), and does not say outright that the old one keeps serving -- that needs confirming on a test project. The bots poll and expose no port, so they have no health check. A `/health` endpoint would give some of the protection with no new service, but it cannot mean "polling has begun" (the new container waits for the old one's poll lease; the old one stops only once the new is healthy: a deadlock) -- only "the process is up and the database answers", which proves less than a standby would. That is an idea, not something built here: it changes how production deploys and cannot be tried from this laptop.
- **AnonBot becomes `@mumu_anon_bot`.** Not built (it needs BotFather and a second bot), and **argued against**: the benefit is a name that matches a pattern; the cost is every inbox link ever posted dying unless a second bot claims the freed name in the same minute, and Telegram does not promise to hold it. The existing alias table already makes `/logs anon` work.
- **Reports carrying the person's own words or a screenshot** (`PLANNED.md`) is **already answered** by `/feedback`, and the cloud branch's reworked Report dialog (link and per-route results attached to a report) was left unmerged: it changes the promise `problems.py` makes ("not what you wrote") and the privacy text of all four bots, which is a decision, not a port. Recommended: close the planned item.
- **Heavy conversion on a second machine** stays low priority: it needs `PRIVACY.md` to change first, and at today's volume the cost it would save is cents.
- **A website** was skipped at the owner's request. **Archives "to files" and "archive of pictures to one PDF"** were not built: Telegram delivers a result as one document, and the one real need (a phone that cannot open a `.7z`) is a RAR or 7z becoming a ZIP.
- **Pricing from the clip's duration and bitrate** was left: it is money code that changes what people pay, and the owner's rule is that every price covers the worst case of its job -- a change to the estimate deserves his eyes on the numbers.

---

## Reviews of all of it, and what they found (2026-10-07 to 2026-10-10)

The owner, 2026-10-08: *"continue with the most accuracy, we're not in a hurry"*, then *"prepare to finalize
2.1.0"*; 2026-10-10: *"Prepare to finalize. Delay the features that are being built right now to 2.2.0. Only do
fixes."* The work of 2026-10-07 had been built on Sonnet by accident; every part of it was reviewed again by a fresh
Opus reader, and each review found something a green test suite had not. Each fix was built to an Opus spec and
pinned by tests that were run against the old code, deliberately broken one way at a time (well over a hundred such
breakages in all, every one caught). **Moved to 2.2.0**: ConvertBot's OCR (text from a photo or a scanned PDF) and
DownloaderBot's 📄 PDF / 🗜 ZIP buttons under an album -- both built and reviewed, kept whole in `MumuBots2.2.0`
(its `CHANGES_v2.2.0.md` is the handoff), and taken out of this version.

Tests at the end: the offline suite **TODO_OFFLINE of TODO_OFFLINE** (`python tests/run.py`), and all **21**
Postgres-backed suites green (the 19 of before, plus `db_timeouts.py` and `init_locks.py`). The C edition: ported,
`--status` empty, `--lint` clean, `main.py --check` passes, every section compiles under Python 3.11.9. CI on Linux:
TODO_CI.

### DownloaderBot: a stranger's server can no longer take the process down

In the C edition the five bots share one process and one 1 GB container, so one runaway read is all five bots down.

- **Compressed answers are decoded by the bot, with a ceiling** (`net.body_chunks`, `read_page`). `yt-dlp[default]`
  (2026-10-07) brought brotli, and httpx then decoded `Content-Encoding: br` with no limit: **421 bytes became
  512 MB**, and a read capped at 8 MB peaked at **1,069 MB**. Now the raw bytes are read and gzip/deflate inflated
  with `zlib`'s `max_length`; br, zstd and compress are refused whether asked for or not, two encodings stacked are
  refused, and a token that names no encoding ("none", "utf-8") is read as plain bytes, as httpx did.
- **A redirect's own body** was read whole by httpx before it followed the redirect: a 302 carrying a 200 MB gzip
  bomb peaked at 400 MB. Redirects are passed on with an empty body: under 20 MB.
- **Capped everywhere a stranger answers**: the cobalt POST (a 200 MB body without a length peaked at 400 MB),
  resolvers' pages, Reddit's and X's reads, the probe's fetch (now also 20 s).
- **Two event-loop freezes from regular expressions** (second review): yt-dlp's error text was tidied with a pattern
  that backtracked cubically on whitespace (1,000 spaces 1 s, 10,000 about 17 minutes), and a hostile cobalt
  instance chooses that text; `<meta[^>]+>` was quadratic on a page of "<meta" with no ">" (8 MB about an hour). Both
  are linear scans now, identical in output on 30,000 and 20,000 random strings; every other pattern in
  `resolvers.py` was timed on 2 MB of hostile input (all under 0.5 s).
- **A body sent a byte at a time** cost about 6 µs of the event loop a piece (2 MB: 12.7 s; 48 MB: five minutes).
  More pieces than one per KB of the ceiling plus a thousand is refused (`net.Trickle`).
- **At most 40 files a post** (`DBOT_MAX_ITEMS_PER_POST`): every file of a delivery is on disk before the first is
  sent, and a resolver is somebody else's server. A TikTok slideshow carries 35.
- An encoding the bot does not read, or a trickle, is one route failing (the next alternative is tried), not the end
  of the download.
- **Pasted cookies** (`DBOT_YT_COOKIES` and the others) are checked field by field before yt-dlp sees them: a domain
  whose flag disagrees with it, or an expiry that is not digits, made yt-dlp's loader print the whole line, secret
  and all, into the logs (confirmed with its real loader). The warning names the line and the rule, never a field.
  A byte-order mark is dropped, and the file gets the `# Netscape HTTP Cookie File` first line when the paste has
  none: without it yt-dlp silently reads no cookie at all (confirmed the same way).
- **The daily check** probes the platforms side by side with one yt-dlp at a time, so `/run downloaderbot
  routewatch` answers within 75 s; yt-dlp gets its own 90 s in a probe instead of 12 s (which produced false "down"
  alerts); the freshness check reads PyPI's JSON instead of GitHub's unauthenticated API (60 requests an hour per
  shared address) and logs a failure as a warning; `DBOT_YTDLP_AUTOUPDATE` and the other switches accept only an
  explicit yes or no.
- The redo button: a tap refused for lack of downloads gives the button back.

### ConvertBot: archives, raw photos and video, reviewed

- **A killed job left its unpacked archive behind** (SIGKILL skips `finally`; the sweeper removed files, never
  folders): a 2.7 MB `.tgz` left 629 MB. `jobs.remove_job_leftovers` runs from `_finish`, and the sweeper clears old
  `-contents` folders -- never a running job's.
- **Unpacked size 300 MB**, not 1,000 (that was the whole disk). **Tar long-name and pax header bodies** were read
  whole: a 611 KB `.tgz` peaked at 718 MB; refused over 1 MB now.
- **Refusals instead of crashes**: a ZIP flagged UTF-8 with invalid names, a cp1251 tar name written to ZIP, a file and
  then a folder of the same name; permissions opened up after `bsdtar` (a mode-000 file crashed, a 0500 folder could
  not be deleted, a 000 folder dropped files silently); bsdtar's errors go to a file; device names (`aux.c`) are only
  refused on Windows.
- **A stranger's picture is no longer parsed in the bot's own process**: the raw photo's size (LibRaw) and the menu's
  picture size (an ICO inside a `.jpg` hid a 13,000 x 13,000 PNG that Pillow decoded inside `open()`) are read in a
  child, at most two at once; a raw header is read without unpacking the picture.
- **Video past 4K** (8K, 5.7K) to MP4, MKV or MOV was never scaled, so it always ran out of memory and was refunded;
  it is brought into the 1080p box now, and the menu says so. A portrait video's size is no longer given the wrong
  way round.

### Every bot speaks formally

The owner, 2026-10-07: always polite -- вы in Russian, siz with the -ing forms in Uzbek, in every bot. Russian:
AnonBot 28 texts, StickerBot 24, ConvertBot 47, DownloaderBot 32 changed from ты; the greeting is "Здравствуйте!"
instead of "Привет!" in all four. Uzbek: two of AnonBot's export buttons ("bering", "yozing"); everything else was
already formal. Only the form of address changed: placeholders, markup and line breaks were compared before and after,
and a scan for leftover informal forms finds none.

### What a deploy could break, reviewed (2026-10-09)

A fresh Opus review of everything that meets production at release time found no blocker. Fixed:
- **The release script could leave the bots paused**: anything that failed after the pause (a crash, a push stuck on a
  credential prompt under Task Scheduler, a database error) skipped the resume, and the people told "an update is
  coming" were never messaged. Now every way out after the pause resumes exactly as the normal path does and tells
  the owner whether the push happened; every git command runs without a prompt and with a timeout (its output through
  files: on Windows a timed-out push otherwise waited on its helper process, 25 s for a 2 s timeout); an uncommitted
  folder or a missing tag is refused **before** anybody is paused. `run_release.cmd` uses the TempTools python and
  refuses to run without arguments (it would otherwise release the base edition by default).
- **Every database connection has limits** (see the incident below): 10 s to connect, 30 s for unacknowledged data,
  60 s a statement, 120 s idle in a transaction; the poll lease's own connection gets the network limits only (cutting
  it would drop the lease). `/sql` keeps its own 10 s. The C edition's shared threads go from 8 to 16.
- **No table lock on a normal start**: every `ALTER TABLE` at startup takes an exclusive lock even when it changes
  nothing (measured on Postgres 17), and they all ran at every start, in one transaction, on tables the old container
  was still serving during a deploy. Each now runs only when the catalog says something is missing, in every bot and
  the family tables.
- **A single `Conflict` is no longer a crash alert** (the owner, 2026-10-10): one "terminated by other getUpdates
  request" is logged as a warning; three within two minutes -- what a real second copy polling the same token produces
  -- alert once, and again only after ten quiet minutes.
- CI installs `bsdtar`, so the 7z and RAR tests run on Linux instead of skipping.

Checked and sound: the schema changes are safe while 2.0.0C and 2.1.0C overlap and for a rollback; the "you can now
react" note is claimed on a person's next visit, never pushed to everyone; every new setting is safe unset; the code
compiles on Python 3.11. Production has none of the variables that would override the new defaults (checked by name).

### `versions`

`/run <bot> versions` lists every installed distribution as `name==version` (a file), or the lines matching a name:
production's real set, to pin requirements from after the deploy.

### The incident of 2026-10-09 (production, 2.0.0C)

At 08:40 UTC every bot's once-a-second database job stopped for about a minute and a half; AnonBot then reported a
`Conflict` to the owner as a crash, and everything recovered by itself in the same container. Nothing from the
laptop was running. Either a network stall on the host or **Supabase's session pooler** explains it: in session mode
(port 5432) every client of the role shares one cap, the pool size (15 by default); one C container holds about 10
idle and up to 20 at peak (five poll leases and five pools of up to three); the 16th waits up to a minute, then is
refused. With the new connect timeout the next one will say which. **Before Railway's overlap is set, the pool size
must be raised**: two containers at once would be over 15 even when idle (the owner will do it later).

---

## Not done yet

### New on 2026-10-10

- **OCR and the album buttons are 2.2.0's** (`MumuBots2.2.0`, `CHANGES_v2.2.0.md` there): built and reviewed, taken
  out of this version by the owner's decision. That folder must first take in this version's later fixes.
- **Supabase's pool size before Railway's overlap** (see the incident above): raise it from 15 first; the owner will.
  Then `RAILWAY_DEPLOYMENT_OVERLAP_SECONDS=30` and `RAILWAY_DEPLOYMENT_DRAINING_SECONDS=120` (unset today: the old
  container may be killed without its SIGTERM grace).
- **Requirements are lower bounds**, so a rebuild installs that day's newest of everything. After the deploy,
  `/run <bot> versions` and pin them (as 2.1.1). To roll back, redeploy the previous deployment in Railway (it reuses
  its image); pushing the old commit would rebuild it with new packages.
- The first routewatch run (about 10 minutes after the deploy) may send a few real "last route" / "down" alerts: it has
  only ever met local fakes. Read them before believing either side.
- With overlap set, the release script may see 2.1.0 in a heartbeat while the old container still polls (the heartbeat
  is written before the lease), then a harmless "CAVEAT".
- The new 60 s statement limit has not been timed against production's `/backup` export or the 90-day prune; both are
  small today.
- `CREATE INDEX IF NOT EXISTS` on an existing index still takes a share lock until the start's transaction commits
  (writers wait, readers do not).
- `net.fetch_bytes` peaks at about twice the body (98.7 MB for 47 MB); concatenated gzip members are cut to the first,
  and a truncated gzip is accepted, as httpx did.
- 40 files of 48 MB is still up to 1.9 GB on disk for one delivery: a total-bytes limit in the delivery loop is the
  real bound.
- A failed route's log line can carry an httpx error's URL (pre-existing).
- A pasted cookie expiry with decimals (`123.5`) is refused, although the newest yt-dlp reads it: older ones print
  the line.
- yt-dlp's automatic update stays off: the swap holds a pool thread up to 300 s and cancelled downloads keep theirs
  (in C the shared pool stalls); it does not survive a restart; a folder that failed verification blocks that release
  for good; a hung import probe is not killed; no rollback, and old copies are never removed.
- `DBOT_COBALT_API_KEY` set with the default instance list would send the key to strangers.
- In C, StickerBot sets `Image.MAX_IMAGE_PIXELS` to 40M and ConvertBot to 80M at import; the last import wins for every
  bot. `CONVERT_MAX_MEGAPIXELS` 210 lets a conversion decode about 840 MB of RGBA on a 1 GB container.

### New on 2026-10-07

- **Nothing built on 2026-10-07 has met the real hosts.** The daily check, the cobalt route, yt-dlp's
  update, 7z and RAR (libarchive 3.6 on Debian bookworm, not the 3.8.8 this laptop's `tar.exe` is) and the
  raw decode were all tested here, against local fakes and synthetic files. First deploy: run
  `/run downloaderbot routewatch` and read what each platform says from the datacenter address.
- **7z and RAR with non-ASCII names are untested on Linux.** This laptop's `bsdtar` dies (access violation)
  writing a 7z with a Cyrillic folder name and cannot create such a name reading a RAR; the tests use an
  ASCII-only tree for those two there. The image runs in a UTF-8 locale, where it is expected to work. Try
  one (a 7z with a Cyrillic file name, both ways) the first time Docker or CI is available.
- **CI has not run** (`.github/workflows/tests.yml`; the suite has only run on Windows). Push it to a throwaway
  branch of MumuBots first.
- **A 4K SDR MP4, MKV or MOV keeps its size and peaks at 887 MB** (measured) on a 1 GB host: it fits, narrowly,
  next to a bot of 100 to 300 MB. If it ever turns out not to, `CONVERT_VIDEO_FIT_PIXELS` cannot help (it is
  about HDR and WEBM); the rule in `video_needs_fit` is the place.
- **A raw photo between 40 and 110 MP is converted at half size** -- a 50 MP phone DNG becomes 12.5 MP -- because
  a full decode of 50 MP is 684 MB before it is encoded to anything. The way to do better is to take the
  preview JPEG most DNGs embed (`rawpy` `extract_thumb`), which is full-size on some phones; not checked on a
  real S23 Ultra file (no sample is kept here).
- **Archives to "files", and an archive of pictures to one PDF** (both in `PLANNED.md`'s design): not built, for
  the reason given above. **RAR is never written.**
- **A JavaScript runtime for yt-dlp** (deno, 41 MB wheel): see DownloaderBot above.
- **yt-dlp's automatic update is written and off.** Turning it on (`DBOT_YTDLP_AUTOUPDATE=1`) is the owner's
  call; the recommendation is to wait for the first "yt-dlp is N days behind" message, which shows the check
  works from the container, and then to turn it on.
- **`railway.json` stops being read after 2026-12-01** (still open; see the last section of this file's
  *Not done yet*, and `PLANNED.md`). Production's `MumuCombined` service builds from the repository's root
  `railway.json`; whether its settings are also stored on the service is the thing to read first, after login.

### New in 2.1.0

- **Production's Railway account is on a trial that ends about 2026-10-25**
  (checked 2026-10-03: `isTrialing` true, 22 days left, $4.58 of the $5 credit
  left at about $0.05 a day). The credit outlasts the date; the date is the
  cliff -- the first account's five deployments went `REMOVED` the day its
  trial ended (2026-09-21). Choosing the Hobby plan before then is the owner's.
- ~~**A bot reacting to its own message in a private chat**~~ -- **checked on
  Telegram** 2026-10-03 with TestBot: it may (*Reactions, /feedback*, Put to the
  owner #3).
- ~~**NEXT -- the owner's answers of 2026-10-03**~~ -- **built** the same
  night (*The owner's answers, built*). Not finalised: "Not yet. I'll tell you
  when to finalize."
- ~~**How long the download routes last**~~ **All three offers built on 2026-10-07** (routewatch, yt-dlp, cobalt) -- (the owner asked, 2026-09-29; probed
  live from this machine that day, residential address, through the new guarded
  client). Every platform had at least one working route: Instagram
  downloadgram and yt-dlp up, InstaFix returning no media; TikTok tikwm and
  tnktok up, yt-dlp's TikTok extractor failing ("Unexpected response from
  webpage request", on yt-dlp 2026.8.19); X fxtwitter and yt-dlp up, vxtwitter
  behind a challenge; Pinterest's API and page routes up. Nothing runs out by
  use: a route is rested after three failures in a row for 2 minutes doubling
  to an hour, and still tried if everything ahead of it fails; the only
  counters are per person (12 an hour, 40 a day, x4 for a donor). What ends a
  route is the service behind it (vxtiktok, ddinstagram and kkinstagram were
  already gone by v1.2.1) or the datacenter address being refused, which this
  probe cannot see. Offered, unanswered: (1) **yt-dlp is `>=2024.1` and nothing
  ever updates it** -- it is whatever the image build installed, and Docker's
  cached `pip install` layer probably keeps it there across deploys -- so the
  safety net under every platform rots as the sites change (TikTok's extractor
  already has); a scheduled rebuild, or `-U` at start, fixes it. (2) **A daily
  probe from inside the container** (`probe` over the family bus already does
  it) with an alert to ManagerBot when a platform has no working route, so that
  a chain ending is heard about before a user reports it. (3) **YouTube and
  Reddit video have one route each, yt-dlp**, from an address YouTube's bot
  check is known to refuse in bulk; `DBOT_YT_PROXY` / `DBOT_YT_COOKIES_FILE`
  are unset, and BotFamily's unmerged branch has a cobalt route for it.
- ~~**The quote for a video the host cannot convert**~~ **Built on 2026-10-07**, as a note at the menu and a 1080p fit rather than a refusal -- (*The price of a 4K HDR
  conversion*): warn, or refuse, at the quote when ffprobe says 3840 wide and
  10-bit or HDR on a 1 GB host, rather than after a minute of conversion; or
  tonemap at 1080p. Offered to the owner 2026-09-29, unanswered.
- **A video's output size from its bitrate**, not a ratio per category: *"about
  235 MB out"* was 37 MB. It lowers the price of every big phone video (the
  send limit is the price's largest term) and belongs with *Prices from the
  clip, not the bytes* in `PLANNED.md`.
- ~~**A Stars payment delivered twice would be credited twice**~~ **Built on 2026-10-07**, see *A Stars payment delivered twice is credited once* -- (*A review of all
  of it*). The design, not built because it is money code that cannot be run
  without Postgres: in `family_link.topup`, take a claim
  `stars:paid:<telegram_payment_charge_id>` in `family.settings` with the same
  `INSERT ... ON CONFLICT DO NOTHING RETURNING key` that `grant_stars_once`
  uses, **in the same transaction as the credit** (so a crash cannot leave one
  without the other), and return `None` for a payment already credited;
  `_credit_topup` then sends nothing. Test it in `credit_scenarios.py`, which
  needs the local Postgres.
- ~~**`python tests/smoke.py` against a local Postgres**, for `/sql`'s
  `prepare=True` (*A review of all of it*, §6): checked against psycopg's
  source only.~~ **Done** 2026-10-03 against a scratch Postgres 17: two
  statements, a write behind a COMMIT and a plain write are all refused, and
  the check fails without `prepare=True` (*Reactions, /feedback*, Tested).
- **Pin the dependencies.** Every `requirements.txt` line is `>=`. A
  `constraints.txt` (or exact pins) from the versions running in production
  would stop a build pulling a new major release; it needs those versions, from
  a `pip freeze` in a running container.
- ~~**A workflow that runs `tests/run.py` on every push**~~ **Written 2026-10-07, not yet run** -- to MumuBots and
  MumuCombined (both public; the suite needs no database, no network and, for
  the checks that need a library, skips without it). Not added here because it
  cannot be tried from this machine.
- ~~**`resolvers._get()` reads a whole response body.**~~ **Capped on 2026-10-07.** A page is small when it is
  a platform's own; a cap (stream, stop at a few MB) would make it small when it
  is not.

### New in 2.0.0

- **4K HDR video does not convert on a 1 GB host** (*Railway, measured*).
  Left so by the owner, since few people send it: the person is told what
  happened and gets the credit back (`CV-NO-MEMORY`). If it becomes common:
  a Railway plan with more memory a service (Railway lists 8 GB and 8 vCPU
  for a paid Hobby plan, and more services a project), or such video
  converted at 1080p with the result saying so.
- ~~**Production has the ffmpeg thread bug** (1.7.0, from BotFamily): the fix
  reaches users with the move to MumuBots, or as a hotfix to BotFamily if
  the move waits.~~ **Superseded**, checked 2026-10-03: production has run
  `v2.0.0C` -- the Railway service `MumuCombined`, building MumuCombined
  `main` at `3f5a9d0` -- since 2026-09-28 21:15 UTC (all five heartbeats say
  2.0.0, one container), and 2.0.0C carries the ffmpeg cap. The notes above
  that say production was still 1.7.0 on 2026-09-29 were wrong when written.
- **Transfer speed over MTProto on Railway**, for files past 20 MB: not
  measured -- it needs the token and the API id in the container.
- **The API id is set locally only** (`convert_bot/.env`, `unified/.env`,
  now `BotFamily2.0.0C/.env`). Production needs `TELEGRAM_API_ID` and
  `TELEGRAM_API_HASH` (or the `CBOT_`/`DBOT_` pair) on the Railway service,
  or ConvertBot stays at 20 MB there and DownloaderBot's large downloads have
  no switch.
- **The big-file speed on the real host.** From the laptop MTProto moved
  0.3–0.5 MB/s (260 ms from Telegram's DC2) one part at a time; transfers now
  run four parts at once, and Railway is milliseconds from Telegram. Time a
  real 1-2 GB transfer in production: the stall guard and the size-scaled
  time limit say what happens if it is slow, not how often.
- **The disk on the real host.** A 2000 MB conversion needs about twice that
  free while it runs -- the file and what it becomes -- and a person's share
  is 4,050 MB. Checked on Railway (*Railway, measured*): the container
  reports the host's 2 TB, not its 1 GB limit, and a 3 GB write went
  through; on this plan memory, not disk, is what stops a file.
- ~~**Prices for big phone videos read steep.**~~ **Done** the same night: the
  prices were revised (*Prices: cost, plus a markup...*); the clip's MP4 is
  ⚡36, its GIF ⚡84. Pricing from the clip's duration rather than its bytes
  would bring them down further -- in [PLANNED.md](PLANNED.md).
- ~~**DownloaderBot's sends over 50 MB.**~~ **Done** on 2026-09-28: for
  credit (now ⚡7 for the first 100 MB and less after), only once switched
  on.
- ~~**The GitHub move**~~ **done** on 2026-09-29 (*Published*). **Production's
  move to MumuBots** waits for the owner: the runbook is *Production moves
  to MumuBots*, `scripts/railway_move.py` does it, and DEPLOY.md §4 is the
  same by hand. ~~After it, BotFamily can be deleted~~ -- **BotFamily and the
  old per-bot repositories are gone** (checked 2026-10-03), and production
  builds the C edition from **MumuCombined `main`**: a push there redeploys
  production.
- **BotFamily's unmerged branch `claude/gifted-dirac-yvlsgy`** (2026-09-25,
  a cloud session): a cobalt route for YouTube (`DBOT_COBALT_INSTANCES`, deno
  in the image for yt-dlp's challenge solving, cookies pasted as a variable)
  and a Report dialog that lists the details related to a problem and never
  a username. Written against the C edition's old layout, never reviewed or
  merged, and not in 2.0.0. Kept as a branch of MumuCombined; worth porting
  to base as its own piece of work -- YouTube's bot checks on a datacenter
  address are the reason it was written.
- **Three old hard-coded tokens are still live** (TestBot's, and production
  AnonBot's and DownloaderBot's, in 0.1.1 and 0.3.0). Never public, and
  redacted in MumuBotsHistory; reissuing them ends the question.
- **Railway reads `railway.json` only until 2026-12-01.** The move sets each
  service's settings directly, so production does not depend on the files;
  they stay as the description of how a bot is built -- see PLANNED.md.
- **Reading 0.3.0's old SQLite file** to see what it held touched its
  `fstik.db-shm`, SQLite's index beside it, which now carries 2026-09-28 as
  its date. Nothing in the database changed; the file is rebuilt from the
  database whenever it is opened.
- **`testbot/supabase_role.sql` has not been run on Supabase.** Until it is,
  TestBot runs only on the local database; the steps are in
  `testbot/README.md`, "Either database".
- **The owner's ideas for later** -- the website, the reel buttons -- and
  everything else planned are in [PLANNED.md](PLANNED.md) now.
- **Which edition production runs decides which release reaches users.** It
  runs C today; release it with `scripts/nightly_release.py --edition C`.
- **The ConvertBot, StickerBot, DownloaderBot and AnonBot `/privacy` texts in
  `i18n.py`** were not compared line by line with `LEGAL.md`; they are the
  base texts, unchanged.
- ~~**Whether 100 MB can go to 200.**~~ **Superseded** on 2026-09-28: the
  limit is 2000 MB, with a conversion that cannot finish in its ten minutes
  stopped at 30 seconds and refunded.
- **Files over 20 MB in groups** are declined with a sentence pointing to a
  private chat. Supporting them needs the group's channel access hash, which
  a bot can get, but a group is not where files get converted -- left until
  somebody asks.

- **`/run <bot> restart` says "Restarting now"** in the one-process shape too,
  where it restarts all five. Correct, and the README says so, but the reply
  does not. Changing it means `family_link` knowing which shape it is in,
  which nothing in the bots does today.
- **`PRIVACY_URL` and `TERMS_URL` are not set on the five per-bot services**
  (read from the variable names, 2026-09-25), so on the base edition
  `/privacy` and `/terms` show no link to the long forms. The C edition links
  to `LEGAL.md` by default. One variable per bot if the five services return.
- ~~**The `ParentBot` repository is public**~~ and ~~**which is ManagerBot's
  repository meant to be**~~ -- **superseded** on 2026-09-28: ManagerBot is a
  folder of MumuBots, `ParentBot` retires with the other per-bot
  repositories, and `.github/SECURITY.md` covers the whole repository.

### Carried from v1.7.0 (and 1.7.1)

Reviewed with the owner on 2026-09-13, item by item. What they settled is in
the last section; what is left is below, with the reason it is still here.

#### Deferred during 1.7.0

- **Music downloads in DownloaderBot — assessed 2026-09-15, not started.**
  The owner asked whether mass downloads like `C:\.prog\MusicDownloader`
  (yt-dlp over a YouTube playlist → tagged MP3s, paced 2–6 s apart so YouTube
  does not flag it) or Spotify / Yandex Music links are possible. Checked, not
  guessed:
  - **Spotify audio cannot be downloaded** — it is encrypted, and yt-dlp has
    no Spotify extractor. Every "Spotify downloader" reads the song names and
    fetches the audio from YouTube. The names are free: Spotify's public
    embed page for a playlist returned all 50 tracks with artists, no API key.
    The audio is then YouTube's, so the wrong version (live, cover, sped-up)
    is the thing to guard against — match on duration as well as title.
  - **Yandex Music failed outright** here: yt-dlp's `yandexmusic` extractor
    crashes inside its API call. The service is region-locked, Railway is in
    Europe, and full tracks generally need a paid account — which would mean
    the owner's account serving strangers.
  - **YouTube, SoundCloud and Bandcamp** are the easy part: yt-dlp is already
    DownloaderBot's YouTube route, and audio-only is a format choice.
  - **Volume is the hard part.** YouTube's bot check trips on bulk requests
    from a datacenter IP, and `DBOT_YT_PROXY` / `DBOT_YT_COOKIES_FILE` exist
    but neither is set. The allowance is 12 an hour and 40 a day, so a
    50-song playlist needs its own rule.
  - **The real risk is the bot, not the code.** Music is the most enforced
    category, and a copyright complaint that gets a bot removed takes its
    @username with it. Recommended to the owner: music in a separate bot, so
    a takedown cannot cost `@mumu_downloader_bot`.
  Proposed order: single YouTube/SoundCloud/Bandcamp links as audio, then
  Spotify tracks and playlists via names → YouTube, then playlists with a
  queue, pacing and progress updates; Yandex last or not at all. Waiting on
  three answers: same bot or a separate one, how a playlist counts against
  the allowance, and whether to set up a YouTube proxy or cookies account.

- ~~**ParentBot's repository is ahead of the monorepo.**~~ **Resolved**:
  checked on 2026-09-28, every file in `ParentBot` (and in the four other
  per-bot repositories) is identical to this monorepo at `efe3dc4`; the 1.7.0
  publish superseded `c321a9a`.

- **Section 9 of the old backlog, planned but not started.** The owner:
  *"9 should be addressed soon. Maybe this update batch or next one."* It is
  eight separate pieces of work and they are not the same size, so ordered by
  what each one unblocks:

  1. ~~**A local Bot API server.**~~ **Done differently for ConvertBot on
     2026-09-26** -- MTProto for the big files only, see *ConvertBot takes
     files over 20 MB*; the claim here that a local server was "not a code
     change" did not hold on Railway. DownloaderBot's half is still open:
     its sends are capped at the Bot API's 50 MB, and `big_files.py` is the
     pattern to copy when it is picked up.
  2. **DownloaderBot merging files** — pictures into one PDF, a video with
     its subtitles, an album into one archive. Self-contained, no new
     dependency (ffmpeg and PyMuPDF are both already there), and the most
     asked-for-looking thing on the list.
  3. **Sleeping the quiet bots.** `/idle` already measures whether it would
     be worth it; nothing acts on it. Needs webhooks, a pool that closes when
     idle, and ManagerBot waking a child rather than the child polling. Worth
     doing one bot at a time, and not AnonBot.
  4. **OCR**, which is the nichest and probably the most wanted. Measured
     rather than guessed in v1.5.0: a local `tesseract` costs nothing
     resident, and the constraint is peak memory rather than money — so it
     needs a one-at-a-time lock and a page cap, not a bigger plan.
  5. **Archives** (ZIP/TAR/7z). Deliberately left out so far because the
     expansion ratio of an uploaded archive is unbounded and it needs its own
     uncompressed-size guard rather than the ceilings in `formats.py`.
  6. **Camera raw** (CR2, NEF, ARW, DNG) — `rawpy` is a wheel and the pairs
     are obvious; another 30 MB for a format most people do not send over
     Telegram.
  7. **Heavy conversion off the bot's container.** Every conversion API
     checked forbids a raw conversion service in its terms, so the remaining
     route is the bot's own converter on free compute the owner controls.
     `PRIVACY.md` has to change in the same release — it currently promises
     files never leave the machine the bot runs on.
  8. **Reports carrying the person's own words or a screenshot**, for 1.7.0.
     Note that 1.7.0 has just written a disclaimer promising *"not what you
     wrote"*, so that text has to change in the same release that makes it
     untrue.

- **Replies are not preserved by either export mode.** `copyMessage` makes an
  independent message with no reply link, and the local reply structure was
  never recorded: `anon_relay` knows which *conversation* a message is in and
  which bubble on the *other* side it corresponds to, not what it answered in
  its own chat. Recording it would be one more column written on every relay.
- **The size-ratio table is judgement, not measurement.** Every entry in
  `_RATIO_BY_PAIR` is reasoned from what the format pair usually does, with one
  number (`docx → pdf`, 20×) taken from a real file. `star_transactions.item`
  records every pair converted, so after a few weeks of real use the table can
  be replaced with what actually happened.
- **`CONVERT_BILLABLE` defaults to `sum`.** Charging on input plus estimated
  output makes expanding conversions dearer than they were. `max` is one
  environment variable away if that turns out to be the wrong call.
- **The burst window is per process.** A redeploy forgets how many faults have
  happened recently, and two bots hitting the same code do not add up. Both
  are deliberate: the alternative is a database query per problem, on the path
  that already exists to survive the database being the thing that broke.
- ~~The fan-out only serves `/errors`.~~ **Not a gap after all**, checked on
  2026-09-20: `/status`, `/users`, `/usage` and `/idle` read the shared
  database directly rather than going over the bus, which is better than a
  fan-out — they answer for a bot that is currently down.
- **PDF → DOCX does not reconstruct layout.** Columns come out sequentially, a
  table comes out as its text. Doing better needs a computer-vision stack.
- **A pop-up still cannot hold a Report button.** It is now at least recorded
  and levelled like every other problem.
- ~~Two `check()` conventions across the test suite.~~ **Done** on
  2026-09-20: the five files that compare two values call it `equals()`, so a
  comparison written in the other shape no longer passes on a truthy value.

#### Found and fixed on 2026-09-20

- **`/idle` had never worked** — it read `family_link.SLEEP_AFTER_SECONDS`,
  which does not exist in that module and never has.
- **`/stars`, `/probe` and `/providers` were refused by ManagerBot**, because
  a bot registers those in its own process and ManagerBot was checking its own
  copy of the list. Bots now advertise what they answer in their heartbeat.

#### Carried, and still true

- **The standby service** (`UPDATES.md` §5) is designed and not built. Listed
  unbuilt in 1.2.2R, 1.2.3 and 1.3.0. It does not keep a bot working during a
  deploy — nothing can, the code doing the work is what is being replaced — it
  replaces silence with a sentence, which matters for the *failed* deploy
  (minutes to hours of nothing) rather than the healthy one (seconds).
- **ManagerBot has no `sim.py`-driven test.** Its flows go through the command
  queue and a second bot answering, which is a bigger fixture than any release
  so far has needed. Listed in 1.2.3 and 1.3.0.
- **`DONATION_STEPS`, the download allowances and `FLOOD_UPDATES_PER_MINUTE`
  are judgements, not measurements** — and, per the owner's answer on card
  payments, there is not yet a customer base to measure against. Left alone
  until there is, rather than tuned from noise. The nightly report is now the
  thing that will surface the numbers as they accumulate.
- **`ACTIVITY_RETENTION_DAYS` is still 90.** Published, never argued down.
- **No data export for a person's own data.** `/deletemydata` reports a count
  and `/privacy` lists the categories; a dump of six settings rows would be
  ceremony rather than a right anybody is short of.
- **The five `hero.png` description pictures are not uploaded**, and the bot
  profile photos are a manual BotFather step (*Edit Bot* → *Edit Description
  Picture*). The owner: *"8 is for tomorrow or whenever during the day."*
- **The empty `parent_bot` schema** — two tables, no rows — is still in the
  database. Now that ManagerBot's `DB_SCHEMA` is `manager_bot`, it can be
  dropped, but only when the owner says so ("Lets not delete anything from db
  yet").
- ~~**The GitHub repository is still `ParentBot`**~~ and ~~**ManagerBot has no
  `SECURITY.md`**~~ -- superseded by MumuBots (2026-09-28).
- **`testbot/token.env` points TestBot at the production database.** "Leave as
  is for now, but also save it for later." Since 2026-09-28 TestBot refuses
  that login -- `postgres` can write everywhere -- so the line has to become
  the `testbot` login (`testbot/README.md`) or the local database.
- **Progress updates are ConvertBot's alone**; nothing else in the family works
  silently for minutes.
- ~~**Jobs live in memory.**~~ **Done** in 2.0.0: a conversion lost to a hard
  kill is refunded by the next process that notices (`conversions_in_flight`).
- **Background conversions do not send the donation nudge.**
- ~~**Nothing checks the menus a token is actually serving.**~~ **Done** in
  2.0.0: `scripts/check_live_menus.py`, read-only, all five matched.
- **AVIF is multithreaded** — 187 CPU-seconds in 15 wall-seconds locally — and
  CPU is billed per core-second.
- **An album's later parts chain off the first one** rather than off the
  counterpart of what the album replied to. Left alone deliberately.
- **ConvertBot's Railway memory.** A 200 MP decode peaks around 1.5 GB.

#### Settled, so they are not asked again

- **Replying to feedback** -- not offered: feedback is one-way, read only for
  the owner (2026-10-03: "Make the feedback one sided").
- **The code line on AnonBot's self-removing reaction note** -- kept: "Being
  one line short is not a strict requirement, just make sure the users will be
  able to read the most important info."

- **The licence** — AGPL-3.0-or-later on all five, v1.7.0. *"Just do what you
  think is the best."* See the section above for what that commits to.
- **Card payments are built and deliberately frozen.** The owner: *"was
  designed this way bcs there is no real customer base yet."* `FIAT_CURRENCIES`
  has USD with tiers and `/donate 5 usd` is wired; setting
  `PAYMENT_PROVIDER_TOKEN_USD` is the whole of turning it on, whenever there is
  a reason to.
- **ManagerBot's Railway `DB_SCHEMA`** — checked by the owner on 2026-09-13 and
  already set to `manager_bot`. It is reading its own schema.
- **`/starbalance`** — proposed twice, never answered, and superseded: the
  credit ledger and `/balance` answer the question it was for.
- **UZS** — dropped in `cf8fb00`; Stars and USD only.
- **DownloaderBot's paid override** — dropped: "forget about 2".
- **Refunds of Stars** — not offered: "lets just not do refunds for now".
- **The Supabase password** is not being rotated: "10 - not".
- **Third-party conversion services** — not built, because their terms forbid a
  bot reselling them.
