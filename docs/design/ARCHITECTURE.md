# How the family is put together, and why

Five Telegram bots. Four of them public — StickerBot, ConvertBot,
DownloaderBot, AnonBot — and one private one, ManagerBot, that watches the
other four and can reach into any of them.

The question this document answers is the one that had to be settled before
ManagerBot could exist at all: **five processes sharing one database, or one
process doing everything?**

---

## The decision

**Five separate processes. One Postgres database. One schema per bot.**

```
                    ┌──────────────────────────────┐
                    │   one Postgres database      │
                    │                              │
   StickerBot ─────▶│  sticker_bot.*               │
   ConvertBot ─────▶│  convert_bot.*               │
   DownloaderBot ──▶│  downloader_bot.*            │
   AnonBot ────────▶│  anon_bot.*                  │
   ManagerBot ──────▶│  manager_bot.*                │
        │           │                              │
        └──────────▶│  family.*  ◀─── all five     │
          reads all │    heartbeats                │
          schemas   │    events                    │
                    │    commands                  │
                    └──────────────────────────────┘
```

Each bot connects with `search_path` set to its own schema, so every line of
SQL any of them had already been written stays exactly as it was — the four
bots' identically-named tables (`user_settings`, `star_transactions`,
`activity_events`) no longer collide, without a single query being rewritten.

`family.*` is the shared part, and the only thing the bots have in common:

| table | what it carries |
|---|---|
| `heartbeats` | one row per bot, restamped every 30s — this is uptime monitoring |
| `events` | crashes, startups, donations — anything the owner should hear about |
| `commands` | ManagerBot's requests, and the answers, going back the other way |
| `bot_state` | which bots ManagerBot last saw up, so it alerts on changes only |
| `settings` | ManagerBot's own toggles (alerts on/off) |

`family_link.py` — one file, in `shared/`, used by all five bots (until
2.0.0, a byte-identical copy in each bot folder) — is the only code that
touches any of this.

---

## Why not one process for everything

That was the real alternative, and it isn't a bad idea. python-telegram-bot
can run several `Application`s in one event loop; one Railway service instead
of five would cost less, and a single process could hold one connection pool
instead of five sets of short-lived connections.

It lost on three counts:

**Blast radius.** DownloaderBot runs yt-dlp and ffmpeg on user-supplied URLs.
That is the most memory-spiky, most likely-to-be-OOM-killed thing in the
family. In one process, a container hitting its memory ceiling takes the
sticker packs, the file conversions and the anonymous inbox down with it. In
five, one bot dies and ManagerBot sends a message about it. The whole point of
building a watchdog is undermined by an architecture where everything fails
together.

**Deploys.** Four repos already exist, one per bot, each wired to its own
Railway service. Merging them into one process means one repo, one service,
and every change to any bot restarting all of them. That is a real
regression, and it would have thrown away work that was already done.

**It doesn't actually save much.** The expensive part of the old setup was
never the bot processes — a polling PTB bot idles at around 80–120 MB. It was
running *four separate Postgres instances*, each one reserving memory around
the clock whether anyone used that bot or not. Collapsing four databases into
one removes most of the bill. Collapsing five small Python processes into one
removes much less, and costs everything above.

So: consolidate the databases, which is where the money and the capability
both are, and leave the processes alone.

### "But if Railway goes down, all five go down anyway"

True, and it does not change the answer. The two are different failure
classes and only one of them is common.

A platform-wide outage — Railway down, the region down, Supabase down — takes
everything with it whatever the process count is, and there is nothing in the
architecture that can help. It is also rare, it is somebody else's to fix,
and while it lasts Telegram is queueing every message for 24 hours and will
hand them all over when the bots come back. The user-visible cost is latency.

The failures that actually happen here are the other kind, and they are
per-bot by nature:

- **An OOM kill.** DownloaderBot holding a video while ffmpeg muxes it is the
  only thing in the family that gets near a memory ceiling. In five
  processes it dies alone. In one, it takes the sticker packs, the
  conversions and the anonymous inbox with it — and *those three were not
  doing anything wrong*.
- **A bad deploy.** One repo and one service means every change to any bot
  restarts all of them, and a syntax error in DownloaderBot's resolver chain
  is an outage for AnonBot. Five services can still be built and fail
  independently -- until 2.0.0 `publish.ps1 -Skip` could even hold one bot
  back -- precisely because they are five.
- **An unhandled exception in a handler.** This one is the exception to the
  rule, and worth being exact about: python-telegram-bot catches whatever a
  handler raises and routes it to the error handler, so a crash in one bot's
  handler does **not** kill the process even today, and would not kill a
  shared one either. What a shared process would share is everything below
  that line — the event loop, the memory ceiling, the file descriptors, the
  interpreter — and those are what OOM kills and container restarts act on.

So the honest summary is: one process is not *much* worse for crashes in
handler code, and considerably worse for everything that kills a container.
The bot in this family most likely to have its container killed is also the
one whose work is most valuable to the others' users, which is the argument
in one sentence.

The thing genuinely worth doing about a platform outage is not architectural
— it is that the deploy no longer waits for a build with the bot switched
off (see [UPDATES.md](../operations/UPDATES.md) §5), and that ManagerBot says so when a bot
does not come back.

---

## What each bot is called

A bot is named in six places. All six derive mechanically from one
canonical family name, `<Role>Bot`, with one deliberate exception. (Until
2.0.0 there was a seventh: each bot's own GitHub repository, `StickerBot` for
a public bot and a dashed name for a private one. Since then the bots are
folders of one repository, MumuBots, and what carries a folder's name to
Railway is its `Dockerfile` and `railway.json`.)

| layer | form | StickerBot | AnonBot |
|---|---|---|---|
| family name -- docs, `/status`, ManagerBot | `<Role>Bot` | `StickerBot` | `AnonBot` |
| bus id (`BOT_NAME`, `family.*` keys) | lowercased | `stickerbot` | `anonbot` |
| folder and Postgres schema | `snake_case` | `sticker_bot` | `anon_bot` |
| environment prefix | initial + `BOT` | `SBOT_TOKEN` | `ABOT_TOKEN` |
| Telegram `@username` | `mumu_<role>_bot` | `@mumu_sticker_bot` | **`@mumu_chat_bot`** |

`tests/test_naming.py` asserts all of the above against the source, and
that each folder's deploy files build that folder and no other, so a sixth
bot cannot be added half-named.

### The exception, and why it stays

**A `@username` is an address, not a label.** AnonBot's whole product is a
permanent personal link that people post publicly:
`https://t.me/mumu_chat_bot?start=q_<token>`. Changing that username does not
rename the bot -- it breaks every link anybody has ever shared, permanently,
with no redirect and no way to reach the people holding them. It is frozen
for as long as the bot has users, and the mismatch is much the cheaper of
the two costs.

There used to be a second exception. The private bot was ParentBot at
`@mumu_manager_bot`, and the plan was to move the username to
`@mumu_parent_bot`. v1.6.0 did the opposite and renamed the bot instead: an
address is the one layer with anything depending on it, and the other six
layers were free to move. It is ManagerBot now, at the username it always
had, and the family has one exception rather than two.

So rather than pretend it lines up, ManagerBot knows about it.
`USERNAME_ALIASES` in `manager_bot/bot.py` maps every bot's real username --
and the words that read naturally from it -- onto its canonical id, and
`resolve_bot()` matches on any of them. `/logs anon`, `/logs chat`,
`/logs anonbot` and `/logs @mumu_chat_bot` all reach the same bot.

One name is still worth changing when it is convenient, and it is not urgent:

- **The `ParentBot` GitHub repository -> `manager-bot`**, so the dash rule
  reads true and the repository stops carrying the old name. This one has to
  be paired with reconnecting the Railway service that builds from it, which
  is why it is not done in passing. `DEPLOY.md` §8c has the steps.

A bot's *display name* in Telegram -- the bold line at the top of the chat,
as opposed to the `@username` -- is free to change and should match the
family name. `@BotFather` -> *Edit Bot* -> *Edit Name*.

## Why not keep four separate databases and have ManagerBot poll each

This was the third option, and it is the one that would have kept the old
structure completely intact. It fails on the thing that was actually asked
for. "See any other bot" and "run their admin-only commands" means ManagerBot
needs a way to reach a process that may be on Railway while ManagerBot is on a
laptop, or the reverse. Without a shared database, that means either opening
an HTTP endpoint on every bot (a public port per bot, plus authentication,
plus a way to find each other) or a message broker (another service, another
bill). A shared database the bots already had a connection to does the same
job with no new moving parts and no new attack surface.

It also makes "download the database" mean one file instead of four.

---

## How ManagerBot reaches into another bot

Not over the network. Through the same database:

1. `/run sticker dbdump` inserts a row in `family.commands` addressed to
   `stickerbot`.
2. StickerBot's `family_link` poller — a PTB job that checks about once a
   second while the bus is busy and every ten seconds once it is quiet —
   claims it with `FOR UPDATE SKIP LOCKED` and runs the handler **in its own
   process, as its own bot**.
3. The answer (text, or a file as bytes) goes back into the same row.
4. ManagerBot's result pump picks it up and forwards it to the owner.

Three properties fall out of this for free:

- **It works across hosts.** A laptop-run ManagerBot can drive a
  Railway-deployed StickerBot and vice versa; neither needs to be reachable
  from the other.
- **A down bot is obvious rather than confusing.** Nothing claims the
  command, ManagerBot times it out after 90 seconds and says so.
- **`/say sticker <user> hello` arrives from StickerBot**, because StickerBot
  is what actually sends it. The user sees the bot they were already talking
  to.

The same reasoning drives what ManagerBot *doesn't* do: it never writes into
another bot's tables. It reads them — that's what one database is for — but
anything that changes state goes through the command queue so the owning bot
does it with its own code.

---

## Detecting that something is wrong

Three independent paths, because the interesting failures are the ones that
break the reporting mechanism too:

- **Heartbeat goes stale** → the bot is down. Covers crashes, OOM kills,
  network loss, and never-started. ManagerBot alerts on the *transition*, both
  directions, so a bot that stays down doesn't repeat itself.
- **A bot writes an event** → an unhandled exception (hooked into the
  existing `shared_features.record_error`, so every crash already being
  counted for `/status` now also gets forwarded), a startup, a donation.
- **ManagerBot's own database connection fails** → it says so directly over
  Telegram, once, and again when it recovers. Without this, the failure that
  blinds the watchdog would be the one failure it reported nothing about.

What ManagerBot deliberately does *not* do is alert on info-level events. They
are all recorded and `/events` shows them; only warnings and above, plus
donations, interrupt anyone.

---

## What it costs to run, and why

The architecture above decides the shape of the bill. This section is about
the other half: what the five processes actually *do* all day, and what was
removed.

### One connection, not one per query

Every database function in every bot used to open a Postgres connection, run
one statement, and throw the connection away. That is a TCP round trip, a TLS
handshake and a forked backend process on the server, to run an INSERT that
takes microseconds — and it happened on **every Telegram update**, plus once
per heartbeat, once per command poll, and two or three times per completed
action.

At the family's idle baseline that alone was on the order of 60,000
connections a day across the five bots, almost all of them to find an empty
queue or record that a user was active. Measured against a local Postgres,
where the network cost is nearly zero and the number therefore flatters the
old code, replacing it with a pool made the same query **140× faster**
(32.9 ms → 0.23 ms). Over a cloud network with TLS the gap is wider.

Now each process holds one warm connection open, opens a second or third only
while several things genuinely overlap, and hands those back after two
minutes. Five idle bots cost the database five backends instead of a
continuous churn of them, which is what lets the whole family sit on the
smallest Postgres plan that exists.

### Doing less, rather than doing it faster

- **Active-user counts** were one `INSERT` per update. Both queries that read
  that table are `COUNT(DISTINCT user_id)` over a window, so recording a user
  once per minute is exactly as accurate and writes a fraction as much. Ids
  collect in a set and go out as one statement per flush.
- **The `/status` board** ran one query per bot for its user count, plus one
  each for heartbeats, the alerts setting and the database size — eight
  connections to draw one screen. It is one query now. `/users`, `/donations`,
  `/mystars`, `/stars` and the co-edit screen got the same treatment.
- **Bulk sticker imports** called `getStickerSet` after every single add, to
  learn a file id used only by the "send an emoji to retag that" feature.
  Importing 100 stickers meant 200 API calls; nobody retags sticker #37 of
  100, so it is read once at the end.
- **Video stickers** walked a fixed ladder of up to eight full VP9 encodes of
  the same clip until one came in under 256 KB. Each attempt now measures how
  far off it was and derives the next quality setting from that ratio, so two
  attempts cover almost everything and four is the hard ceiling. On a
  worst-case clip (35 MB of pure noise, the hardest thing a codec can be
  handed) that is 6 encodes and 15.2 s down to 2 encodes and 6.1 s — and the
  *sticker is better*, 169 KB of the 256 KB budget instead of 73 KB, because
  a ladder that steps in fixed jumps overshoots the limit and throws away
  quality it did not need to.
- **Nothing grows forever.** `activity_events`, `anon_relay`, `family.events`
  and `family.commands` (which carries file payloads) are all pruned by a
  six-hourly job. Each bot deletes only rows keyed to itself, so no
  coordination between the five is needed.

### Memory

- ConvertBot held every uploaded file in `context.user_data` — up to
  `CONVERT_MAX_FILE_MB` per user, kept for the life of the process, whether or
  not they ever chose an output format. With a local Bot API server raising
  that ceiling to 250 MB, a handful of abandoned uploads was the entire
  container. Uploads live on disk now, in a directory swept on a timer and
  cleared at startup, and the conversion streams file-to-file rather than
  through two `bytes` objects.
- Since 1.7.1 that ceiling is 100 MB without a local Bot API server: files
  past the Bot API's 20 MB come over MTProto (`convert_bot/big_files.py`),
  each transfer in a child process of its own, like each conversion:
  Telethon costs 27 MB to import and Python never gives an import back, so
  the bot's own process never imports it. A local Bot API
  server was the other way and lost on shape rather than cost: its limits
  lift only in `--local` mode, which hands back paths on its own disk, so on
  Railway it would have to be a second process inside ConvertBot's
  container; moving to it logs the bot out of Telegram's cloud for ten
  minutes; and while it is down the bot hears nothing. The MTProto route
  failing costs files over 20 MB and nothing else.
- python-telegram-bot never evicts `user_data`. Anyone idle for twelve hours
  has theirs dropped; all that is lost is a cached language.
- DownloaderBot had no concurrency limit at all, so ten pasted links meant ten
  simultaneous yt-dlp runs. Three at a time (two for any one person), with size and duration ceilings
  checked against the *metadata* before a byte is fetched. `yt_dlp` — 30-40 MB
  of resident memory in extractor modules — is imported on the first download
  rather than at startup, and since v1.2.1 most downloads never import it at
  all: the routes ahead of it in the chain are plain HTTP.
- Pillow's decompression-bomb guard only warns by default. Both bots that
  decode user images set a real ceiling, so a malicious file is a caught
  exception instead of an OOM kill.

### Where a download actually comes from

The single most consequential fact about DownloaderBot is not in its code: the
sites it downloads from decide what to serve partly from **where** the request
comes from, and Railway's addresses are a published datacenter range. The same
Instagram reel is public from a phone and a login page from a container. This
is not a bug that can be fixed in the bot, and v1.1.3 correctly concluded
there were only two answers — cookies from a throwaway account, or a paid
residential proxy — both of which need something the bot cannot supply itself.

v1.2.1 took the third answer: **do not make the request from here.** Each
platform now has a chain of independent routes (`downloader_bot/resolvers.py`),
mostly public services that fetch on their own infrastructure and hand back a
direct CDN link or a copy proxied through themselves. yt-dlp stays in every
chain but last, because it is the only route that goes out from this
container's own address — and the only one that improves the moment a cookie
file or proxy is configured.

The architectural cost is a dependency on services this project does not
control and cannot monitor, each of which will eventually be bought,
rate-limited, sued or quietly switched off. That is paid for with scoring
rather than with vigilance: every attempt is recorded per route in the bot's
own `provider_health` table, a route that keeps failing is moved to the back
of its chain and retried on an exponential backoff, and one success restores
it. A chain where everything is resting still tries everything, so a bad hour
never leaves a platform with nothing to try. `/providers` (owner-only) reports
what the live bot has learned; `python downloader_bot/probe.py` asks every
route on demand.

This is the only place in the family where a user-facing feature depends on a
third party other than Telegram and Supabase, and it is worth being deliberate
about: the alternative was a feature that did not work.

### Idling more cheaply

A polling bot's day is mostly empty `getUpdates` calls. Two settings change
what that costs and neither costs any latency, because Telegram answers the
instant an update exists:

- **A 30-second long-poll window** instead of the default 10 — one third of
  the HTTP requests for identical responsiveness.
- **`allowed_updates`**, listing only what each bot has a handler for.
  Telegram stops sending edited messages, channel posts, reactions and chat
  member updates entirely, rather than this process downloading and parsing
  them to drop them.

In the cloud the rotating log files are off (the filesystem is ephemeral and
the platform captures stdout anyway), `httpx`'s per-request INFO line is
silenced, and the thread pool is capped at four rather than sizing itself to
the host machine's core count.

---

## The one thing to watch

One database is a single point of failure for all five bots' *data*. It isn't
a single point of failure for the bots themselves — `family_link.attach()`
never raises, and a bot whose database is unreachable keeps serving whatever
it can — but it does mean backups matter more than they did.

That is what `db_backup.ps1 save` is for, and why it's a one-liner. There is
also `FAMILY_BUS=off`, which drops any bot back to standalone behaviour
without a redeploy.

---

## If Railway's free credit runs short

The cost lever, in order of how much it saves:

1. **Already pulled:** four Postgres instances → one.
2. **Already pulled:** a connection pool, batched activity writes, a longer
   poll window and a filtered `allowed_updates` — see the section above.
   Together these are most of what an idle family costs.
3. Set `MBOT_DOWN_AFTER_SECONDS` higher and `FAMILY_HEARTBEAT_SECONDS` to
   60–120. Fewer wakeups, less database traffic, slower detection.
4. Run ManagerBot on the laptop rather than on Railway. It only needs the
   public database URL, and it is the one bot whose downtime costs nothing —
   it has one user, and that user will notice.
5. If it still doesn't fit: the four public bots are what earn their keep.
   ManagerBot is the first thing to move off, not the last.
6. **The emergency lever: all five in one process** -- the `BotFamily`
   repository, deployed as a single service; since 2.0.0 its own code, in
   `BotFamily<version>C` (`DEPLOY.md` §9). It is the
   biggest saving on this list by far: measured idle, 302 MB as five
   processes against 77 MB as one, because four of the five copies of
   Python and its libraries go away. It is last rather than first because
   it costs everything the top of this document is about -- see "Why not
   one process for everything": one OOM kill or one deploy takes every
   bot. Nothing about it is permanent; the lease that keeps two pollers off
   one token works across the two shapes, so moving back is one more
   deploy.

Until v1.7.1 this list said merging the processes was deliberately not on
it. The reasoning above still holds for the family in normal times; what
changed is that the owner wanted the option ready for the times that are
not, and building it turned out not to need a single change to the bots.
