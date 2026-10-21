<img src="logo.svg" alt="DownloaderBot" width="72" align="right">

# DownloaderBot

A Telegram bot that downloads media from a pasted link and sends it back. No
command is needed — pasting a supported link is the whole interface.

| platform | what it returns |
|---|---|
| Instagram | Reels, photo posts and carousels |
| TikTok | Videos without the watermark, and photo slideshows |
| Twitter / X | Video and photos; a text-only post is rendered as an image card |
| Pinterest | The pin's image at full resolution, or its video. Video pins and idea pins both work, and an idea pin of several pages arrives as several files. Any country domain (`co.`, `in.`, `br.` …) and `pin.it` shortlinks are recognised. |
| Reddit | Media posts directly; text and link posts as an image card |

Runs as its own process and its own deployment, from one repository with
its four siblings: the modules every bot shares live once, in `../shared`,
and each bot's Dockerfile builds from the repository root to take them with
it. It shares a Postgres database with four sibling
bots only in the sense that its tables live in a schema of their own inside
it (`DB_SCHEMA`); no other bot reads or writes them. The one shared area is
`family.*`, where the bot posts a heartbeat and any crash so a monitoring bot
can watch it — `FAMILY_BUS=off` disables that entirely.

---

## Where a download actually comes from

This is the part of the bot worth understanding before reading any of it.

Several of these sites decide what to serve partly from **where the request
comes from**. Every cloud host's addresses sit in published datacenter
ranges, and those ranges are served a login page where a residential
connection is served the post. A downloader that makes its requests from
inside its own container therefore works on a laptop and fails in
production, for reasons that look like bugs and are not.

So the bot does not have one way to fetch a link. Each platform has a
**chain of independent providers**, tried in order, and the chain remembers
what it learns:

- A provider that fails does not end the attempt; the next one is tried.
- Repeated failures move a provider to the back of its chain and rest it for
  a while, with the delay growing each time — rather than removing it, since
  most of these outages are temporary.
- A chain whose every member is resting still tries every member. "The site
  is slow today" and "the bot is broken" are different answers and only one
  of them is worth showing.
- Requests that go out from the container's own address are tried **last**,
  because that is the address these sites treat differently.
- yt-dlp, which is the download itself rather than a lookup, is given time for
  one -- 90 seconds, and more for a large file (`DBOT_YTDLP_TIMEOUT`) -- where a
  lookup gets 20. A download that is given up on is stopped and what it wrote
  is removed.

**Only the public internet.** Every address the bot fetches came from
somebody else: a link, a page a link led to, a URL in a resolver's answer. So a
name that resolves to this machine, a private network or a link-local address
(where a cloud host's metadata service lives) is refused, on every redirect too,
and the connection is made to the address that was checked. A link is
recognised as a platform's only if its host is that platform's.

`/providers` prints what the chain currently believes, per platform, with
each provider's success and failure counts and its last error. `/probe`
actively tries every route against known-good sample posts and reports what
happened, which answers "is it broken, or was that one post private".

---

## Commands

| command | what it does |
|---|---|
| *(paste a link)* | Downloads it and sends it back. The link is first cut down to the address of the post: share-tracking parts such as `?igsh=`, `?utm_source=` or `?si=` are removed before anything else sees it, so one post pasted in two shapes is one link. |
| *(paste the same link again while the first is still queued or downloading)* | No second job: the repeat gets a 👌 reaction and nothing else, and it is not counted as a download. Same person, same chat, same post only. |
| *(paste a link for a post the bot has already sent to anybody)* | Sent again by the Telegram file IDs the bot kept, without downloading the post. The references are filed under a scrambled name of the post, not under who asked (no user id, chat, username, link or caption). One confirmed less than 12 hours ago (`DBOT_FILEID_CONFIRM_HOURS`) is used as it is; an older one only after a lookup shows the post still public. If the post is gone or the lookup is inconclusive, nothing old is sent and the bot goes to the platform as usual; after a send by stored file ID fails with any BadRequest the stored reference is dropped; the post is fetched in the same job only when Telegram says the ID itself is invalid, otherwise the person is told the send failed. References unused for 90 days are deleted (`DBOT_FILEID_KEEP_DAYS`). It counts as one download. `DBOT_SENT_FILES` (`0` switches it off). Large files, PDF/ZIP merges, Reddit pictures, rendered text cards and deliveries of more than 10 files are not stored. |
| *(tap "get it as a file" / "get it compressed" under a download, or paste the same link again)* | Answered from the files the bot kept after the last download, without fetching the post again (the same link pasted again is usually answered from the stored file IDs first, then from the kept copy): a kept copy lives five minutes from its last use and never past 12 hours after the download, a different link deletes it at once, and `/deletemydata` deletes it too. It still counts as one download. One delivery over 100 MB is not kept, nor are Reddit pictures and rendered text cards. `DBOT_CACHE_SECONDS` (`0` switches it off), `DBOT_CACHE_ENTRY_MAX_MB`, `DBOT_CACHE_MAX_MB`. |
| *(tap 📄 PDF or 🗜 ZIP under an album)* | One PDF of the album's photos, a page each, or one ZIP of its files: fetched again when tapped (the kept download above does not serve it), sent, and nothing kept. At most 20 files and 48 MB in all (`DBOT_MERGE_MAX_ITEMS`, `DBOT_MERGE_MAX_MB`), a result under 50 MB, and for the PDF still JPEG, PNG, WEBP or GIF photos of up to 20 megapixels, one PDF built at a time. A tap counts as one download. |
| `/settings` | The caption, the file quality and large files on one screen, each a button. |
| `/lossless on\|off` | Send downloads as files rather than as photos and videos. Telegram re-encodes anything sent as media — that compression is what makes it play inline, and there is no way to have both — so with this on the bytes arrive exactly as the source had them, at the cost of a tap to open. Off by default, remembered per user. A button under every finished download gets that one file the other way round without changing the setting. |
| `/caption on\|off` | Toggle the credit caption. |
| `/cancel` | Asks which of the things the bot is waiting on should stop, one button each. |
| `/donate` | Voluntary contribution towards hosting, paid in Telegram Stars. |
| `/balance` | The ⚡ credit balance — one balance shared by the family's four bots — and everything it was spent on. |
| `/paysupport` | How to reach the operator about a payment that went wrong. |
| `/start` | Instructions. The first `/start` from a new user asks which language to use, once. |
| `/language`, `/en`, `/uz`, `/rus` | Switch language. Each reprints the instructions in the language chosen. |
| `/help` | A short guide; the buttons under it open each command's page (also `/help <command>`), and "📖 Full guide" shows everything (also `/fullhelp`, `/help full`). |
| `/fullhelp` | The full guide in one message, the same as `/help full`. |
| `/feedback` | A suggestion or a report about anything, for whoever runs the bots: sent as one message, or asked for and typed as a reply. It carries the words, which bot and the language — not who wrote them — and is deleted after 30 days. It is one-way: only the operator reads it, and nobody can write back. |
| `/privacy` | What the bot holds about the person asking, who else sees it, and how long it stays. |
| `/terms` | What the bot may be used for, and where the money stands. |
| `/deletemydata` | Erases what the bot holds about the person asking, after one confirmation. |

Restricted to the account ids in `DBOT_ADMIN_ID`, and answering everyone else
exactly as a misspelt command does: `/providers`, `/probe`,
`/messageas <user_id> <text>`, `/dbdump` and `/status`.

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

## Limits

This is the bot in its family with real resource spikes — a fetch, an
`ffmpeg` mux and an upload, all at once — so it is bounded on three axes:

- **Two downloads at a time** across the whole process, which is what stops
  ten links pasted in one minute from becoming ten simultaneous downloads.
- **One of those slots per person**, so nobody can hold both and leave
  everyone else queueing behind a stranger.
- **A rolling hourly and daily allowance per person**, counted in the
  database so a redeploy does not reset it. Anyone who has contributed
  through `/donate` gets a larger allowance, permanently and for any amount.

All three are configurable; see `.env.example`. Size and duration ceilings
stop a mis-pasted link to a three-hour stream before the first byte is
fetched.

### Large files, over 48 MB

A bot may send 50 MB through the Bot API, so downloads stop at 48. Past that
the bot can still fetch a file -- up to 2000 MB, Telegram's own limit for a
bot -- and send it over MTProto, logged in as this same bot, in a private
chat. It costs real egress, so it is **paid, and off until you switch it on**:

- **Off (the default):** a file over 48 MB is refused with what it would cost
  and a button that switches large files on. Nothing past 48 MB is fetched,
  and nothing is charged.
- **On** (`/settings`): the bot says before it starts that this one costs
  credit, fetches it, and charges by the 100 MB begun -- **⚡7 for the first,
  and less for each after: ⚡23 for 500 MB, ⚡76 for 2000** -- once the file
  has arrived and its size is known. If the balance does not cover it,
  nothing is charged and the file is deleted; if sending fails, the credit
  goes back.

Needs the Telegram app's API id and hash (`TELEGRAM_API_ID` and
`TELEGRAM_API_HASH` in `shared/.env`, or `DBOT_API_ID` / `DBOT_API_HASH` here);
without them there is no switch, and 48 MB is the limit.
`DBOT_LARGE_MAX_MB` lowers the ceiling. The price is the family's pricing
rule (`shared/pricing.py`) over what sending the file costs, and
`tests/test_pricing.py` checks it still covers the machine.

### The 220 ⚡ gift

While the family's 220 ⚡ gift is open (the owner starts it once, and it can be
claimed for 14 days), the first message a person sends this bot or ConvertBot
gives them 220 ⚡ of bonus credit, once across the two, and a notice in their
language. Bonus credit is spent before ordinary credit and runs out 28 days
after the day it was claimed. The claim runs beside the bot's own answer and
never holds it up (`attach_promo_claim`, in `shared/shared_features.py`).

---

## Running it

```bash
pip install -r requirements.txt
cp .env.example .env          # fill in DBOT_TOKEN and DBOT_USERNAME
python bot.py
```

`DBOT_TOKEN` and `DBOT_USERNAME` come from
[@BotFather](https://t.me/BotFather). `DBOT_ADMIN_ID` is optional and takes
one or more numeric account ids.

The bot needs a Postgres database (`DATABASE_URL`, with `DB_SCHEMA`
defaulting to `downloader_bot`) and `ffmpeg` on `PATH`. The schema and its
tables are created on first run.

Two optional settings materially change how well Instagram works from a
datacenter: `DBOT_IG_COOKIES_FILE` and `DBOT_IG_PROXY`. Both are documented
in `.env.example`.

### Deploying

Set the same values as environment variables on the host and run
`python bot.py`. `railway.json` and `nixpacks.toml` configure a Railway
deployment; neither is required elsewhere.

A deployment replaces the running container, and the bot is built so that
costs as little as possible. The new process waits on a Postgres advisory
lock until the old one has stopped polling, so Telegram never sees two
consumers of one token. What each provider had learned is written to the
database, so a restart does not go back to hammering a route that has been
dead for a week. A download that was running when the signal arrived cannot
survive — the container is going — so the person is told, rather than left
watching a message that will never finish. Updates sent during the gap are
held by Telegram and delivered on the first poll. `DEPLOY_SAFETY=off`
disables the deploy machinery.

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
| `bot.py` | Handlers, the send path, and the per-person limits |
| `resolvers.py` | The provider chains, their health, and the probe |
| `platforms.py` | Which links are recognised, and the platform-specific fetches |
| `net.py`, `video.py` | Bounded HTTP, and the video download |
| `cards.py` | Rendering a text post as an image |
| `db.py` | This bot's schema, provider health, allowances, connection pool |
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

Python 3.11 or newer, Postgres 16 or newer, and `ffmpeg`.

## A note on scope

This bot downloads publicly accessible posts on behalf of the person asking
for them. It is not a way around a private account, and it does not attempt
to be. Whoever runs it is responsible for how it is used where they run it.

## Licence

AGPL-3.0-or-later — see [LICENSE](LICENSE).

This is the licence the AGPL'd PyMuPDF asks for, and §13 of it is the reason:
anybody who interacts with this software over a network must be offered its
source. A Telegram bot is exactly that case, since nobody using it ever holds
a copy. The source is here, which satisfies §13 for this deployment; anybody
running a modified version as a service has to publish their changes too.
