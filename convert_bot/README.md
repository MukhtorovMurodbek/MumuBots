<img src="logo.svg" alt="ConvertBot" width="72" align="right">

# ConvertBot

A Telegram bot that converts files between formats: pictures, video, audio,
documents, e-books, spreadsheets and subtitles — around seventy-five formats
in, forty out, and some eight hundred combinations, up to 2000 MB. Small
files are free; larger ones are priced in ⚡ credit, bought with Telegram
Stars in the chat.

Runs as its own process and its own deployment, from one repository with
its four siblings: the modules every bot shares live once, in `../shared`,
and each bot's Dockerfile builds from the repository root to take them with
it. It shares a Postgres database with four sibling
bots only in the sense that its tables live in a schema of their own inside
it (`DB_SCHEMA`); no other bot reads or writes them. The one shared area is
`family.*`, where the bot posts a heartbeat and any crash so a monitoring bot
can watch it — `FAMILY_BUS=off` disables that entirely.

---

## Commands

| command | what it does |
|---|---|
| `/convert` | Convert a file. Sending a file directly does the same thing; the command exists for people who look for one. |
| `/formats` | Every format the bot reads and writes, a category at a time. `/formats heic` answers for one format directly. |
| `/balance` | The ⚡ credit balance, shared by the family's bots, and everything it was spent on. `/mystars` still works. |
| `/recharge` | Buys ⚡ credit with Telegram Stars. `/donate` still works. |
| `/cancel` | Asks which of the things the bot is waiting on should stop, one button each, and stops nothing until one is chosen. |
| `/paysupport` | How to reach the operator about a payment that went wrong. |
| `/start` | Instructions. The first `/start` from a new user asks which language to use, once. |
| `/language`, `/en`, `/uz`, `/rus` | Switch language. Each reprints the instructions in the language chosen. |
| `/help` | The instructions on their own. |
| `/privacy` | What the bot holds about the person asking, who else sees it, and how long it stays. |
| `/terms` | What the bot may be used for, and where the money stands. |
| `/deletemydata` | Erases what the bot holds about the person asking, after one confirmation. |

Restricted to the account ids in `CBOT_ADMIN_ID`, and answering everyone else
exactly as a misspelt command does: `/stars` (the whole Stars ledger — paid,
free and refunded), `/messageas <user_id> <text>`, `/dbdump` and `/status`.

---

## Privacy and terms

The bot holds personal data from the first message it receives: a Telegram
user id is the only thing a bot can address a person by, so there is no
opting out of that one while the bot is in use. `/privacy` says what else is
kept, who else sees it and how long it stays; `/terms` says what the bot may
be used for; `/deletemydata` erases it, immediately and without a form.

All three speak whichever of the three languages the person has chosen.
The long forms are [PRIVACY.md](PRIVACY.md) and [TERMS.md](TERMS.md), which
is also what the privacy-policy link in Telegram's own bot settings points
at.

---

## What it converts

| | reads | writes |
|---|---|---|
| **Pictures** | PNG, JPEG, WEBP, BMP, TIFF, ICO, HEIC/HEIF, AVIF, JPEG 2000, PSD, TGA, PCX, DDS, PPM, SGI, XBM, XPM, ICNS, QOI, BLP, SVG | PNG, JPEG, WEBP, **PDF**, ICO, TIFF, BMP, GIF, AVIF, HEIC, JPEG 2000, TGA, PPM, QOI, ICNS |
| **Video** | MP4, MOV, M4V, AVI, WEBM, MKV, FLV, WMV, TS, M2TS, MPEG, 3GP, OGV, ASF, GIF, APNG | MP4, WEBM, MKV, MOV, AVI, GIF, animated WEBP, APNG — and the audio on its own, in any audio format below |
| **Audio** | MP3, WAV, OGG, Opus, M4A, AAC, FLAC, AIFF, WMA, AMR, AC3, MP2, AU, CAF, W64, VOC | MP3, M4A, AAC, OGG, Opus, WAV, FLAC, AIFF, WMA, AC3 |
| **Documents** | PDF, DOCX, EPUB, MOBI, FB2, CBZ, XPS, Markdown, HTML, plain text | PDF, PNG, JPEG, WEBP, TIFF, SVG, plain text, HTML, Markdown |
| **Data** | CSV, TSV, JSON, YAML, XLSX, XML | JSON, CSV, TSV, YAML, XLSX, Markdown table, HTML table, PDF |
| **Subtitles** | SRT, VTT, ASS/SSA | SRT, VTT, ASS — or the transcript with the timings removed |

`/formats` says the same thing inside the chat, and answers for one format at
a time. What is offered is what the running host can actually do: the
document, e-book, spreadsheet and HEIC backends are optional dependencies,
and a format whose library is absent is left off the menu rather than failing
when somebody picks it.

Some combinations worth knowing about:

- An **iPhone photo** (HEIC) becomes a JPEG or a PNG.
- **Several pictures at once** become a single PDF, one to a page. Sending
  them as one album is enough; the bot collects the album and offers PDF for
  the set.
- A **PDF or an e-book** becomes pictures — one page as an image, several
  pages as a zip of them — or plain text.
- A **Word document** becomes a PDF, HTML or Markdown. The headings,
  emphasis, lists and tables carry; the page layout is re-flowed rather than
  copied exactly, and the bot says so on the file it sends back.
- A **CSV, JSON or XML export** becomes an XLSX spreadsheet, and a workbook
  becomes a CSV per sheet.
- **Subtitles** convert between SRT, VTT and ASS, or reduce to the transcript.

---

## How it works

**The flow.** A file arrives; the bot offers the formats it can be converted
to as buttons; one tap starts the conversion. Long lists open on the six most
asked-for and expand on request. Files under the free threshold convert
immediately. Above it, the chosen format is quoted in ⚡ credit — priced on the
file and on what it is estimated to become — and the credit is taken when the
conversion starts. From ⚡6; a 100 MB video to MP4 is ⚡22, and nothing costs
more than ⚡84. An invoice for Stars is sent only when the balance does not
cover it.

**More than one file at a time.** A Telegram album arrives as separate
messages a fraction of a second apart. The bot waits for the rest of the album
before drawing a menu, so a set of pictures is one conversion — into a single
PDF, or into a zip of individually converted files — rather than one prompt
per picture.

**More than one file back.** A forty-page PDF converted to PNG is forty
files, and a Telegram message carries one document. The result is a zip in
that case, and the caption says how many are inside.

**Payment.** Telegram Stars, which need no payment provider and no merchant
account, buy ⚡ credit. Every payment, charge and return is recorded in a
ledger, so `/balance` and `/stars` are reads rather than reconstructions. A
conversion that fails for the bot's reasons gives its credit back
automatically.

**Conversion.** Pillow for still pictures, `ffmpeg` for video, audio and
subtitles, PyMuPDF for documents and e-books, and the standard library with
`openpyxl` for structured data. Everything runs under a timeout, so a
pathological input cannot occupy the process indefinitely. The staged upload
is deleted whether the conversion succeeded or not.

**Price.** Since 2.0.0 a price is the job's cost plus a markup: the markup is
the cost itself while the cost is small, and grows more slowly after it, so a
bigger file costs less per megabyte. The cost is the most the job is allowed
to cost at the host's published rates -- its time limit on two cores, and
everything it may send back -- valued at the cheapest rate credit is ever
bought at, so no price is ever below what its job can cost
(`shared/pricing.py`, held to it by `tests/test_pricing.py`). A job's time
limit is two minutes plus a second per megabyte of work, up to ten; what it
may send back is three times its estimated result, 50 MB at least and 2000 MB
at most.

**Limits.** Size is a poor proxy for how much work a conversion is: a small PDF can be hundreds of pages and a
small GIF thousands of frames. So there are ceilings on the work itself —
pages rendered, frames encoded, spreadsheet rows and cells, files in one batch
— and a job over one of them is refused before an invoice is sent, not after.
A ceiling on updates per minute applies per account, before any handler runs,
so a script cannot hold the process busy. All configurable; see
`.env.example`.

**File size.** Telegram's Bot API lets a bot download files of up to 20 MB
and send back up to 50 MB. With `CBOT_API_ID` and `CBOT_API_HASH` set (from
[my.telegram.org](https://my.telegram.org), *API development tools*), files
past those limits move over MTProto instead — Telegram's own protocol, logged
in as the same bot — in private chats, up to 2000 MB, Telegram's own limit for
a bot (`CONVERT_MAX_FILE_MB` lowers it). A download keeps four 512 KB requests
in flight, so a host far from Telegram's servers is not held to one round
trip per part. A result may be sent back at up to three times its estimated
size, which is what its price covers. Without the two settings (or `TELEGRAM_API_ID` and
`TELEGRAM_API_HASH` in `shared/.env`) the bot takes 20 MB, as the Bot API
allows, and nothing else changes. See `shared/big_files.py`.

What keeps a 2000 MB file from being a problem is not its size but the limits
on what it costs: disk is checked against the size Telegram declares before
anything is downloaded; a transfer is stopped when it stalls, not at a fixed
time; and a conversion that is on course to miss its ten minutes is stopped
within its first minute and its ⚡ given back, with a sentence saying roughly
how long it would have taken -- rather than run to the limit and then
refunded. A worker's ffmpeg goes with it when it is stopped.

**HDR video.** A phone video shot in HDR -- HDR10 or HLG, on a curve an SDR
file does not have -- plays grey and washed out if it is copied into an 8-bit
target as it is. So an HDR source going to MP4, MKV, MOV, AVI, WebM, GIF,
WebP or APNG is tonemapped on the way (ffmpeg's `zscale` and `tonemap`) and
labelled as the BT.709 it becomes. The owner's own 15-second 4K60 HDR10 clip,
224 MB, converts to MP4 in about a minute on two threads.

**Untrusted files.** Every backend here parses a file a stranger chose. XML is
read with `defusedxml`, which refuses the entity expansion that turns a
fourteen-line upload into gigabytes of memory or a read of a local file; YAML
is read with `safe_load`, never `load`, which would construct arbitrary Python
objects named in the document; and images are decoded under a pixel ceiling
that turns a decompression bomb into a caught error rather than an
out-of-memory kill.

---

## Running it

```bash
pip install -r requirements.txt
cp .env.example .env          # fill in CBOT_TOKEN and CBOT_USERNAME
python bot.py
```

`CBOT_TOKEN` and `CBOT_USERNAME` come from
[@BotFather](https://t.me/BotFather). `CBOT_ADMIN_ID` is optional and takes
one or more numeric account ids. `CBOT_API_ID` and `CBOT_API_HASH` are
optional too, and lift the 20 MB limit (see *File size* above).

The bot needs a Postgres database (`DATABASE_URL`, with `DB_SCHEMA`
defaulting to `convert_bot`) and `ffmpeg` on `PATH`. The schema and its
tables are created on first run.

Everything in `requirements.txt` past `Pillow` and the database driver is
optional. Dropping one of those lines removes the formats it provides from
the menu and from `/formats`; it does not break the bot. None of them needs a
system package — the native code is bundled in the wheels — so `ffmpeg` is
still the only thing that has to be installed outside pip.

### Deploying

Set the same values as environment variables on the host and run
`python bot.py`. `railway.json` and `nixpacks.toml` configure a Railway
deployment; neither is required elsewhere.

A deployment replaces the running container, and the bot is built so that
costs nothing visible. The new process waits on a Postgres advisory lock
until the old one has stopped polling, so Telegram never sees two consumers
of one token. State is restored from a `runtime_state` table. A conversion
that was running when the signal arrived is announced rather than left
silent, and a file staged on the old container's disk is detected as missing
and refunded. Updates sent during the gap are held by Telegram and delivered
on the first poll — nothing is lost. `DEPLOY_SAFETY=off` disables all of it.

### Keeping two databases in sync

`db_merge.py` reconciles a local database with a remote one additively — it
never deletes or overwrites.

```bash
python db_merge.py --from local --into cloud --dry-run
python db_merge.py --from local --into cloud
```

---

## Files

| file | |
|---|---|
| `bot.py` | Handlers, the format menu, `/formats`, and the payment flow |
| `formats.py` | What converts to what, and what this host can actually do |
| `convert_utils.py` | Pictures, video, audio and subtitles; the pricing table |
| `doc_convert.py` | PDF, e-books, Word documents, Markdown and HTML |
| `data_convert.py` | CSV, TSV, JSON, YAML, XLSX and XML |
| `db.py` | This bot's schema, the Stars ledger, and the connection pool |
| `i18n.py` | English, Uzbek and Russian strings |
| `family_link.py` | Heartbeats, crash reporting, and the command queue a monitoring bot uses |
| `lifecycle.py` | Surviving a redeploy: one poller at a time, state in Postgres |
| `live_message.py` | When a bot message may keep evolving in place |
| `shared_features.py` | Donations, logging, activity tracking, flood control |
| `db_merge.py` | Reconciles two databases, additively |

`family_link.py`, `lifecycle.py`, `live_message.py` and `shared_features.py`
are shared with the sibling bots by being copied rather than imported: each
bot is a separate deployment, so nothing crosses a repository boundary.

## Requirements

Python 3.11 or newer, Postgres 16 or newer, and `ffmpeg`. The optional
converters need PyMuPDF, pillow-heif, mammoth, Markdown, openpyxl, PyYAML and
defusedxml; all are in `requirements.txt` and all install from wheels.

## Licence

AGPL-3.0-or-later — see [LICENSE](LICENSE).

This is the licence the AGPL'd PyMuPDF asks for, and §13 of it is the reason:
anybody who interacts with this software over a network must be offered its
source. A Telegram bot is exactly that case, since nobody using it ever holds
a copy. The source is here, which satisfies §13 for this deployment; anybody
running a modified version as a service has to publish their changes too.
