# Planned, and planned but dropped

The owner, 2026-09-28: *"Keep a list of planned stuff and add OCR to it."*

This is the list of what is going to be built, in the order it is expected,
with what each needs. Each version's `CHANGES_v*.md` still ends with a *Not
done yet* list: that is where the loose ends of that version go -- things
found and not yet fixed, questions put to the owner. Work that is a feature
of its own lives here, and moves out of this file into a CHANGES file when it
is built.

---

## Next patch

### Archives: ZIP, TAR, 7z -- and RAR, read only

The owner: *"Elaborate on archives and camera RAW. Will do them next
patch."*

**What it does.** ConvertBot gets an *Archive* category:

- **From one archive format to another**: ZIP, TAR, TAR.GZ, TAR.BZ2,
  TAR.XZ and 7z, both ways. RAR and RAR5 are read and never written -- the
  format's own tools are not free software, and nobody needs a RAR made for
  them. The common case this serves is a phone that cannot open a `.7z` or a
  `.rar` somebody sent: it comes back as a ZIP.
- **Out of an archive**: "files" as a target. Up to ten files come back as
  one message, as an album where they are pictures or videos; more than ten
  come back as a ZIP (which is what the bot does already for many results).
- **Into its contents' conversion**: an archive of pictures to one PDF, the
  way an album already becomes one. This reuses the batch path in
  `jobs.py`, with the archive's entries as the batch.

**What makes it dangerous, and the guards.** The size of what comes out of an
archive is unbounded by the size of what went in -- a 45 KB ZIP can expand to
petabytes -- so every limit is on the *expanded* side, checked from the
archive's own index before anything is written, and again while writing:

- total expanded size at most the send limit the price covers (and never past
  2000 MB); at most 10,000 entries; no single entry expanding more than 200
  times over (the classic bomb ratio is far past that); nesting refused -- an
  archive inside an archive is sent back as a file, not opened;
- every entry's name goes through the same untrusted-name handling as an
  upload (`tests/test_untrusted_names.py`): absolute paths, `..`, drive
  letters and device names are refused, links are skipped, never followed;
- an encrypted archive is refused with a sentence saying so and nothing is
  charged (a password would be personal data the bot has no business
  holding);
- the expanded files live in the job's own work directory and are deleted
  with it, like every other staged file.

**How.** The standard library's `zipfile` and `tarfile` for most of it;
`py7zr` (LGPL, pure Python, a wheel) for 7z; `bsdtar` from libarchive
(BSD licence, one Debian package, `libarchive-tools`) for reading RAR and
anything else libarchive understands. `formats.py` gains the category, the
pairs and the menu's order; `/formats` lists them.

**Price.** The family's rule (`shared/pricing.py`) over the job's limits, as
every conversion is -- with the work measured on the *expanded* size, which
is what the time is actually spent on.

**Tests.** A bomb (built in the test, a few kilobytes), a path-traversal
entry, an encrypted ZIP, a nested archive, ten thousand and one empty
entries, a RAR5 read, each format both ways, and that nothing is left on
disk after any of them.

### Camera RAW: CR2, CR3, NEF, ARW, DNG, RAF, ORF, RW2

**What it does.** A photo straight off a camera -- or a phone: the owner's
S23 Ultra shoots 50 and 200 MP DNG in Expert RAW -- becomes JPG, PNG, WEBP,
AVIF, HEIC or TIFF. TIFF comes out at 16 bits per channel, for somebody who
means to edit it; everything else at 8.

**How.** `rawpy`, which is LibRaw (LGPL/CDDL, compatible with the AGPL here)
in a wheel for Linux and Windows, including CR3 (LibRaw 0.20 and later). The
decode is the camera's own white balance, no auto-brightening, sRGB out,
AHD demosaicing -- what a person who did not ask for anything expects -- and
Pillow takes it from there, so every existing target works unchanged.

**The limit that matters is memory.** A demosaiced picture is three 16-bit
channels: 24 MP is about 140 MB, 100 MP about 600 MB, 200 MP about 1.2 GB --
past what a job is allowed. So:

- up to 100 MP, the full picture;
- past that, LibRaw's half-size decode (a quarter of the pixels, a quarter of
  the memory, no demosaicing at all), said in the quote: *"converted at half
  size -- 50 MP -- because the full picture needs more memory than this bot
  has"*;
- the megapixel count is read from the file's own metadata before anything
  is decoded, so the quote and the menu know it, the way they know a JPEG's.

**Price and time.** A RAW decode is real work: its own entry in the work
table, about twice a JPEG's per byte; a 25 MP decode takes 2 to 5 seconds on
two cores.

**Tests.** A small sample DNG with a licence that allows it (raw.pixls.us
publishes its samples CC0), each target, the half-size path forced by lowering the limit, a
truncated file refused with a sentence and nothing charged. The images added
to the Docker image: about 30 MB.

### The standby -- and what "backup hosting" would be

The owner: *"If by standby service you mean backup hosting, then this will
also be done next patch or independently from main bots."*

They are two different things, and both are worth having:

- **The standby** (designed in `docs/operations/UPDATES.md` §5, never built)
  is a tiny service -- about 40 MB, no ffmpeg, no handlers -- that holds each
  bot's token only while the real bot is *not* running, and answers everybody
  with one sentence: *"🔧 I'm being updated, back in a minute"*, remembering
  them for when it is back. It covers the deploy that never comes up, which
  is minutes to hours of silence today. It does not run the bots.
- **Backup hosting** runs the bots themselves somewhere else when the main
  host is down. Since 2.0.0 there is a natural one: MumuCombined, all five in
  one process. Deployed on a second host (or a second Railway project),
  *stopped*, it can be started when MumuBots' services fail -- the poll lease
  makes the handover safe in both directions, as it did when production moved
  between the two shapes. What it needs: a place to run it, the same settings,
  and a one-line procedure for switching; `/status` already shows which shape
  answers.

Either can be built independently of the bots' own releases.

---

## Planned

### OCR

Text out of a picture or a scanned PDF: "text" and "searchable PDF" as
targets for images and documents. Measured in v1.5.0: a local `tesseract`
costs nothing while idle, and the constraint is a job's peak memory, not
money -- so it needs a page cap (the one documents already have) and the
one-at-a-time queue ConvertBot already has, not a bigger plan. Uzbek,
Russian and English language data, chosen from the person's language and
overridable in the quote. About 40 MB added to the image.

### Sleeping the quiet bots

The owner: *"See and come up with the optimal conditions to put idle bots to
sleep."* From the family's own tables, read on 2026-09-28 (the owner's
accounts left out):

| bot | uses in a month | days with any | separate sessions (quiet over 10 min before) |
|---|---|---|---|
| AnonBot | 119 | 18 | 35 |
| DownloaderBot | 25 | 12 | 17 |
| StickerBot | 11 | 5 | 4 |
| ConvertBot | 8 | 3 | 2 |

Within a session the median gap between messages is one to four minutes;
between sessions it is hours to days. Nobody uses any bot between 03:00 and
05:00 Tashkent time, and the busiest hours are 06-07 and 19.

**The conditions**, from that:

- **asleep after 15 minutes without an update** -- long enough never to split
  a session (almost no gap inside one is over ten), short enough that a bot
  used twice a week is asleep 99% of the time;
- **never while it has work in flight**: a conversion, a download, a sticker
  pack being built, a relay mid-conversation;
- **ManagerBot never sleeps** -- it is what notices the others;
- **woken by the next message**, which arrives by webhook. That is the part
  that needs building: the bots poll today, and a bot that polls can never be
  asleep. Railway's own sleeping stops a service with no outbound traffic for
  ten minutes and starts it on the next request -- so asleep also means the
  bus poll, the heartbeat and the usage samples stop, and ManagerBot reads a
  missing heartbeat from a sleeping bot as *asleep*, not *down*;
- the first message after a sleep waits for the container to start: 5 to 15
  seconds, about 35 times a month for AnonBot and twice for ConvertBot.

**What it would save**, honestly: memory is what the host bills for an idle
bot (their CPU at idle, measured, is under a thousandth of a core), and the
four public bots hold about 300 MB -- about $3 a month, of which sleeping
would save most. Worth building when the $5 a month the host includes is
close to being used up, one bot at a time, starting with ConvertBot, whose
sessions are rarest.

### DownloaderBot merging files

Pictures of a carousel into one PDF, a video with its subtitles, an album
into one ZIP. Self-contained, no new dependency (ffmpeg and PyMuPDF are both
already there).

### A website for the services

The owner, 2026-09-28: *"make those services more accessible via making a
website"*. For people who do not live in Telegram. It must keep the promise
every `PRIVACY.md` makes -- files never leave the machine the bot runs on --
so the site is a front for the same containers, not a new service somewhere
else; and it needs its own privacy and terms before it opens.

### Reels and TikToks in fewer taps

The owner, same message: *"making downloading reels/tiktoks easier with
buttons."* Under a delivered video: *audio only*, *as a file*, *without
caption*, *share* -- one tap each, instead of a setting or a command. The
lossless button that is there today is the pattern.

### Heavy conversion on a second machine the owner runs

Legal, as ordinary hosting: the bot's own converter, on another host the
owner controls, taking the jobs this container should not. Low priority --
at today's volume a heavy conversion costs this host fractions of a cent --
and it needs `PRIVACY.md` to change first, in the same release: files would
then leave the machine the bot runs on for another of the operator's.

### Prices from the clip, not the bytes

A video's work is its duration, resolution and frame rate, which ffprobe
reads in a second; a picture's is its pixel count. Quoting from those
instead of from megabytes would bring the price of a big phone video down
further, because it is priced today on the worst its size allows.

### Reports carrying the person's own words or a screenshot

For the Report button. `problems.py`'s disclaimer promises *"not what you
wrote"*, so that text has to change in the same release that makes it
untrue.

### Railway's config-as-code, before 2026-12-01

Railway announced (seen in its CLI, 2026-09-28) that `railway.json` is
deprecated in favour of Infrastructure as Code (`.railway/railway.ts`), and
that existing files keep working until **2026-12-01**. MumuBots' five
services are configured by `<bot>/railway.json`; they need moving before
then, or a push after that date may build with default settings -- no watch
paths, no overlap, no drain.

---

## Planned but dropped

### Music downloads

The owner, 2026-09-28: *"Music downloads go straight to planned but dropped
because there is already a perfectly good bot and music requires license."*
The assessment from 2026-09-15 (Spotify's audio is encrypted and every
"downloader" takes it from YouTube; Yandex is region-locked and needs an
account; music is the most enforced category, and a takedown would cost the
bot its @username) is in `CHANGES_v2.0.0.md`, v1.7.0's backlog.

### Heavy conversion through a conversion service

Checked 2026-09-28. The services that sell conversion forbid exactly this:
CloudConvert's terms -- *"you agree to not offer a raw file conversion
service as stand-alone product using the CloudConvert API"* -- and every
other one checked earlier said the same (v1.7.0's backlog, section 9 of the
old list). Free CI compute is not a way round
it: GitHub's terms keep Actions to building and testing the repository's own
software, and name "serverless application" as abuse. The legal route is the
bot's own converter on a machine the owner runs, above.
