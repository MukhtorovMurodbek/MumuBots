# The bot family

Five Telegram bots that share one Postgres database and one working copy.
Four of them serve the public; the fifth watches the other four and answers
only to their owner.

| | bot | what it does | folder |
|---|---|---|---|
| <img src="sticker_bot/logo.svg" width="28"> | **StickerBot** | Turns images, GIFs, videos and stickers into Telegram sticker packs. Co-editing by share link, bulk import from another pack or a WhatsApp export. | [`sticker_bot/`](sticker_bot) |
| <img src="convert_bot/logo.svg" width="28"> | **ConvertBot** | Converts files between about 75 formats: pictures, video, audio, documents, e-books, spreadsheets and subtitles. Free under 3 MB, priced in ⚡ credit bought with Telegram Stars above that. | [`convert_bot/`](convert_bot) |
| <img src="downloader_bot/logo.svg" width="28"> | **DownloaderBot** | Paste an Instagram, TikTok, Pinterest, Reddit or X link and get the media back. Text posts come back as a rendered card. Files over 50 MB, up to 2000 MB, for ⚡ credit -- only for somebody who switched that on in `/settings`. | [`downloader_bot/`](downloader_bot) |
| <img src="anon_bot/logo.svg" width="28"> | **AnonBot** | A permanent personal link anyone can use to send you anonymous messages, with real two-way replies threaded per conversation. | [`anon_bot/`](anon_bot) |
| <img src="manager_bot/logo.svg" width="28"> | **ManagerBot** | Private. Watches the other four, forwards their crashes and donations, and can run any of their owner-only commands remotely. | [`manager_bot/`](manager_bot) |

All four public bots speak English, Uzbek and Russian, and have a voluntary
`/donate` paid in Telegram Stars (or a real currency, if a payment provider is
connected). Each also answers `/privacy`, `/terms` and
`/deletemydata` — what it keeps about somebody, what it may be used for, and
the command that erases it — with the long forms in `PRIVACY.md` and
`TERMS.md` in each bot's folder.

---

## The documents

- **[ARCHITECTURE.md](docs/design/ARCHITECTURE.md)** — why five processes and one
  database rather than one process or four databases, how ManagerBot reaches
  into a bot it cannot address over the network, and what the whole thing
  costs to run.
- **[DEPLOY.md](docs/operations/DEPLOY.md)** — getting it running, locally and on Railway.
- **[UPDATES.md](docs/operations/UPDATES.md)** — how an update ships without a user
  noticing: the single-poller lease, state that outlives the process,
  in-flight work that gets an explanation instead of silence, and why a
  redeploy no longer pages you. Read before touching a deploy.
- **[HOSTING.md](docs/operations/HOSTING.md)** — what the family costs to run, what was
  done about it, how to see it (`/status`, `/ping`), and the changes that
  were deliberately *not* made — including the one-process edition, which
  was later built as a fallback: all five bots in 77 MB instead of 302 MB.
  Since 2.0.0 that edition is its own code in its own folder,
  `BotFamily<version>C` ([DEPLOY.md](docs/operations/DEPLOY.md) §9).
- **[CHANGELOG.md](docs/changes/CHANGELOG.md)** — what changed, when, and why. Also the
  version record: every finalised version gets a `CHECKPOINT` entry there, a
  matching git tag, and its own `BotFamily<version>` folder holding a complete
  working copy — plus, from 2.0.0, a `BotFamily<version>C` folder holding the
  one-process edition of the same version. All three, every time — see the versioning policy at the top
  of that file.

Each bot folder has its own README for that bot's commands and quirks.

---

## Running it locally

```bash
docker compose up -d
```

That is the shared Postgres — one database, `botfamily`, with a schema per
bot. Then copy `shared/.env.example` to `shared/.env` for what every bot
shares, and each bot's `.env.example` to `.env` beside it for its token and
your numeric Telegram id (see *Two `.env` files per bot* below), and:

```powershell
.\start_all.ps1
```

Message ManagerBot `/status`; all five should be listed as up within a minute.
[DEPLOY.md](docs/operations/DEPLOY.md) covers the rest, including bringing data over from an
older per-bot database.

## Layout

```
shared/                   the modules every bot uses, once (since 2.0.0)
    family_link.py        the family bus, the ⚡ wallet, heartbeats
    lifecycle.py          surviving a redeploy
    live_message.py       when a bot message may keep evolving
    problems.py           every error's code and what it means
    shared_features.py    /donate, logging, activity tracking (the four public bots;
                          ManagerBot has its own monitoring.py instead)
    big_files.py          files past the Bot API's limits, over MTProto
    envfiles.py           reads the two .env files -- see below
    pricing.py            what a job costs the machine, and what it is sold for
    .env.example          the settings every bot shares

sticker_bot/  convert_bot/  downloader_bot/  anon_bot/  manager_bot/
    bot.py                handlers and flows -- finds ../shared as it starts
    db.py                 that bot's own schema, queries, and connection pool
    i18n.py               English / Uzbek / Russian strings
    Dockerfile            built from the repository root, so it can take shared/
    railway.json          that bot's Railway service: this Dockerfile, and rebuild
                          only when its folder or shared/ changes
    .env.example          that bot's own settings
    README.md, PRIVACY.md, TERMS.md    what its users read

docs/
    changes/              CHANGELOG.md and each version's CHANGES file
    design/               ARCHITECTURE.md, DESIGN.md
    operations/           DEPLOY.md, HOSTING.md, UPDATES.md
    legal/                LICENSING.md
.github/SECURITY.md       the security policy, for all five

tests/                    run.py needs nothing; the rest need Postgres -- see tests/README.md
testbot/                  all five bots' real code on a spare token, switched from the chat
scripts/                  the unattended release, the live-menu check, the logos
docker-compose.yml        the shared local Postgres
LICENSE                   AGPL-3.0-or-later, for everything here; each bot folder carries a copy
db_backup.ps1 / db_sync.ps1   whole-database backup and restore; laptop <-> cloud sync
start_all.ps1 / stop_all.ps1
```

**Shared, not copied.** Until 2.0.0 every bot folder carried its own
byte-identical copy of `family_link.py`, `lifecycle.py`, `live_message.py`,
`problems.py` and `shared_features.py` -- each bot was its own repository --
and a fix had to be copied into five places. Now they live once, in
`shared/`, and each bot's `bot.py` puts `../shared` on its path before it
imports anything. A module of a bot's own with the same name would win over
the shared one, so `tests/test_shared_copies.py` fails if one appears.

**Two `.env` files per bot.** A bot reads its settings from, first match
wins: the real environment (Railway's variables); its own `.env`, next to its
`bot.py`; and `shared/.env`, for what every bot shares -- the database, the
Telegram app's API id, the owner's contact, the running-cost dials. So a
setting all five need is written once, and a bot that needs it different
says so in its own file. One bot's settings -- its schema, its policy links,
its payment tokens -- are ignored in `shared/.env`, with a warning.
`shared/envfiles.py` does the reading; each folder's `.env.example` says
what goes where.

## Checking a change

```bash
python tests/run.py
```

Needs nothing — no Postgres, no network, no ffmpeg, not even the bots' own
dependencies, and it runs on any Python from 3.9 up. It covers evolving
messages, state across restarts and the `/ping` report, and that all five
bots import and wire up.

```bash
python tests/smoke.py
```

```bash
python tests/anon_scenarios.py
```

122 checks driving AnonBot's real `Application` against a Telegram server
that runs inside the test process ([tests/sim.py](tests/sim.py)) — every way
a person can use the bot, in both chats at once, with the buttons and the
reply anchors. This is the one to run after touching AnonBot.

Then the older scripts, which need a real Postgres and cover the bots' own
logic: every database call, every `main()` with the network stubbed out, the
family command queue end to end, `/start` and `/language` in all four child
bots, `/cancel`'s menu in all four, DownloaderBot's `/lossless` on every send
path, AnonBot's two-way relay, which errors count as a crash, and the media
pipelines with real ffmpeg — see [tests/README.md](tests/README.md).

## Requirements

Python 3.11+, Postgres 16+, and `ffmpeg` on `PATH` for StickerBot,
ConvertBot and DownloaderBot.

## Licence

AGPL-3.0-or-later -- see [LICENSE](LICENSE). It covers the whole repository:
the five bots and `shared/`, which all five run. Each bot folder carries the
same file, as it did when each bot was a repository of its own, so a folder
taken on its own still says what it may be used for. Why this licence:
[docs/legal/LICENSING.md](docs/legal/LICENSING.md).
