# MumuBots

A family of five Telegram bots that share one codebase, one Docker image and one Postgres database:

- **StickerBot**: turns pictures, GIFs, videos and other stickers into Telegram sticker packs, in a private chat or
  in a group (reply to a picture with `/add`).
- **ConvertBot**: converts files between formats: images, RAW photos, documents, presentations (PowerPoint, through
  LibreOffice), Word and Excel files, spreadsheets, archives, video, and text recognition (OCR).
- **DownloaderBot**: downloads media from Instagram, TikTok, Twitter/X, Pinterest and Reddit links, in a private chat
  or, added to a group, from the links posted there. For some videos a private chat first gets a card: a button per
  quality, with its size, and one for just the sound; nothing is downloaded or counted until a tap.
- **AnonBot**: anonymous-question inboxes.
- **ManagerBot**: the operator's control panel: status, usage, problem reports, broadcasts, backups, restarts.

The public bots speak English, Uzbek and Russian, and take optional Telegram Stars payments for heavy jobs.

## The tree

| Where | What |
|---|---|
| [bots/](bots/README.md) | one folder per bot: its handlers, tables, texts and own modules |
| [shared/](shared/README.md) | code every bot gets its own copy of; [shared/security/](shared/security/README.md) holds the keys and the walls |
| [scripts/](scripts/README.md) | operator scripts, run by hand |
| [tests/](tests/README.md) | the test suite |
| [main.py](main.py) · [notes](main.md) | the launcher: loads every bot, runs them, supervises processes |
| [.env.example](.env.example) | every setting, with what it does |
| [LEGAL.md](LEGAL.md) | privacy policy and terms; every public bot's `/privacy` and `/terms` link here |
| [LICENSE](LICENSE) · [LICENSING.md](LICENSING.md) | AGPL-3.0-or-later, and why |
| [Dockerfile](Dockerfile) · [notes](Dockerfile.md), [start.sh](start.sh) · [notes](start.md) | the image, and what it starts: the volume handed to the bots' user, then the bots without privileges |
| `docker-compose.yml`, `railway.json` | a local Postgres, the Railway service |

Every code file has a same-named `.md` beside it: the notes that explain it (why it is written the way it is,
and the requests behind it), in the same order as the code, under the function or class they belong to. Each
folder's `README.md` lists its files and links one layer up and down. `.env.example` keeps its notes inline,
because they are the settings' documentation.

## How it fits together

The bots run from one process by default. They share one Postgres database, with a schema per bot plus a
`family` schema for what they share (the bus between ManagerBot and the others, the ⚡ wallet, heartbeats,
usage, redeploy state).

The bot code is written as if each bot were alone: `bot.py` does `import db`, `import i18n`,
`import family_link`. main.py makes that work for five bots in one interpreter:

- **One package per bot.** `register(bot)` creates an empty package named after the folder (`sticker_bot`,
  ...). `_BotFinder` resolves `sticker_bot.db` to `bots/sticker_bot/db.py`, and `sticker_bot.family_link`
  to `shared/family_link.py`: every bot gets **its own copy** of each shared module, with its own globals,
  pool and state. Shared modules form one flat name space, `shared/security/` included; two with the same
  name stop the start.
- **Bare imports stay inside the bot.** `import db` becomes `import sticker_bot.db` for any name the bot
  owns (its files plus the shared ones). Third-party imports pass through unchanged.
- **Settings per bot.** Inside a bot, `os.environ` / `os.getenv` is a view: `CBOT_FOO` overrides `FOO` for
  ConvertBot only, and `DB_SCHEMA` is the folder name.
- **A data folder per bot.** Each module's `__file__` is `data/<bot>/<name>.py`, so what a bot writes next
  to "itself" (logs, a per-bot `.env`) lands in `data/<bot>/`, not in the code.
- `NOT_FOR` in main.py lists shared modules a bot does not get (ManagerBot has no `shared_features`).

Heavy or risky work runs in a child process, `python main.py --module <key> <json job>`, with that bot's
imports and settings: `convert_bot.convert_worker` (every conversion, memory-capped and walled in),
`big_files` (transfers over MTProto), `<bot>_bot.db_merge` (one-off merges, by hand).

`MUMU_PROCESSES` decides how the bots are split: `one` (default) runs them all in one asyncio loop;
`separate` gives each its own process; `manager;convert` gives those two a process each and the rest one
together. With more than one process the parent supervises and restarts a crashed child while the others
carry on. `BOTS=convert,sticker` limits which bots run at all (for splitting across services).

## Quick start (local)

Requirements: Python 3.11, `ffmpeg` on PATH, a Postgres database (`docker compose up -d` starts one), and bot
tokens from [@BotFather](https://t.me/BotFather).

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # then fill in DATABASE_URL, ADMIN_ID and the tokens
python main.py --check             # loads every bot without starting it
python main.py                     # runs every bot that has a token
```

`python main.py convert sticker` runs only those two; `python main.py --list` shows which bots have tokens.

## Deploying on Railway

1. Create a project with a Postgres database (or use Supabase and copy its session-pooler URL).
2. Add a service from this repository. It builds from the `Dockerfile` (`railway.json` sets this up).
3. Set the variables from `.env.example` on the service: at least `DATABASE_URL`, `ADMIN_ID`, and
   `<PREFIX>_TOKEN` / `<PREFIX>_USERNAME` for each bot that should run.
4. Put the data key in a file on a volume and point `DATA_KEY_FILE` at it, never in a variable
   (Railway keeps every deployment's variables). Set `BACKUP_PUBLIC_KEY` to the offline key's public
   half. Keep a copy of both private keys somewhere safe: sealed data cannot be read without the data
   key, nor backups without the other. [shared/security/](shared/security/README.md) says what each key does.

Run a single replica: two copies polling the same token split its updates between them.
To split the bots across services, give each service the same image and its own `BOTS=` list.

> Railway stops reading `railway.json` on 2026-12-01. `.railway/railway.ts` is the draft
> replacement; read its header before applying it.

## Working on the code

| What changes | Where it goes |
|---|---|
| What one bot does | `bots/<bot>_bot/` (handlers in `bot.py`, tables in `db.py`, texts in `i18n.py`) |
| Something every public bot does | `shared/` (each bot gets its own copy) |
| Keys, sealing, walls around strangers' files | `shared/security/` |
| A new module for one bot | `bots/<bot>_bot/<name>.py`, then `import <name>` from that bot's code |
| A new shared module | `shared/<name>.py`; add it to `NOT_FOR` in `main.py` for bots that must not get it |
| How the bots are started or supervised | `main.py` |
| A one-off operator script | `scripts/` |
| Why a piece of code is the way it is | the same-named `.md` beside it, under that function or class |

Rules that keep the bots apart:

- Inside bot code, import bot modules by bare name (`import db`, `import family_link`). Never
  `from sticker_bot import ...` and never `sys.path` tricks. Tests and scripts go through
  `main.register()` + `importlib.import_module("<bot>_bot.<name>")`, as `tests/conftest.py` does.
- Read settings with `os.environ` / `os.getenv` inside the bot module; main.py gives each bot its own view.
- Every user-facing text goes in that bot's `i18n.py`, in all three languages (en, uz, ru), and says what
  happens rather than who does it ("The file has been converted.").
- A bot's tables live in its own schema (`DB_SCHEMA`); cross-bot state goes through `family_link`.
- Anything that identifies a person is sealed with `vault` before it is stored, and anything new that is kept
  about people is described in `LEGAL.md` and the bot's `/privacy` text, with how long it is kept.

To add a sixth bot: create `bots/<name>_bot/bot.py` with a `main()` that builds a `telegram.ext.Application`
and calls `run_polling()`, add `db.py` / `i18n.py` as the others do, and add a line to `BOTS` in `main.py`
with its display name and settings prefix.

Before pushing:

```bash
pip install -r requirements-dev.txt
docker compose up -d               # the local Postgres the tests use by default
python main.py --check             # every bot loads; worker, OCR and ffprobe self-tests
pytest tests                       # creates and drops a database named mumu_tests (TEST_ADMIN_URL to move it)
python scripts/scan_secrets.py --install   # once per clone: every commit is scanned for secrets first
```

CI runs the same, audits the dependencies and builds the Docker image. Files use LF line endings
(`.gitattributes`).

## Licence

[AGPL-3.0-or-later](LICENSE): anybody may run, change and share these bots, and anybody who runs a
changed version as a public service has to publish their changes. [LICENSING.md](LICENSING.md)
explains why (in short: ConvertBot depends on PyMuPDF, which is AGPL). What the bots keep about
people, and their terms of use, are in [LEGAL.md](LEGAL.md).

## Security

Never commit `.env`, cookie files or the data key; `.gitignore` excludes them. A vulnerability is reported
privately, never in a public issue: [.github/SECURITY.md](.github/SECURITY.md) says how.

## Credits

Much of this code was written with Claude, Anthropic's AI assistant, directed and reviewed by the operator.
