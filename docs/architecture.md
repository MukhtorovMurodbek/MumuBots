# Architecture

MumuBots is five Telegram bots that run from one codebase, one Docker image, and (by default)
one process. They share one Postgres database, with a schema per bot plus a `family` schema for
what they share.

| Bot | Folder | Env prefix | What it does |
|---|---|---|---|
| StickerBot | `bots/sticker_bot` | `SBOT_` | Makes Telegram sticker packs from pictures, GIFs, videos and other stickers |
| ConvertBot | `bots/convert_bot` | `CBOT_` | Converts files between formats (images, RAW, documents, spreadsheets, archives, video, OCR) |
| DownloaderBot | `bots/downloader_bot` | `DBOT_` | Downloads media from Instagram, TikTok, Twitter/X, Pinterest and Reddit links |
| AnonBot | `bots/anon_bot` | `ABOT_` | Runs anonymous-question inboxes |
| ManagerBot | `bots/manager_bot` | `MBOT_` | The operator's bot: status, usage, problem reports, broadcasts, backups, restarts |

## Layout

```
main.py                 the launcher: loads every bot, runs them, supervises processes
bots/
  shared/               modules every bot gets its own copy of
    family_link.py        the cross-bot bus, wallet (⚡ credit), heartbeats, usage records
    lifecycle.py          safe redeploys: saving and handing over in-flight work
    shared_features.py    commands the public bots share (/donate, /balance, /privacy, ...)
    problems.py           problem codes, user-facing error texts, owner alerts
    live_message.py       progress messages that keep updating
    big_files.py          files past the Bot API's 50 MB limit (MTProto via Telethon)
    pricing.py            ⚡ prices of paid jobs, from Railway CPU/memory/egress costs
    resources.py          cgroup memory/CPU limits and use, shared fairly between the bots
    vault.py              sealing personal data with the data key
  <bot>_bot/
    bot.py                handlers and main(); main() builds the Application and calls run_polling()
    db.py                 that bot's tables, in its own schema
    i18n.py               its texts in English, Uzbek and Russian
    db_merge.py           one-off: merge two databases (run with --module)
    ...                   its own modules (converters, providers, ...)
  manager_bot/
    monitoring.py         ManagerBot's version of the monitoring that shared_features gives the others
    family_db.py          ManagerBot's read view across every bot's schema
scripts/encrypt_personal_data.py   one-off migration that seals existing personal data
tests/                  pytest suite (needs a Postgres it may create/drop a database on)
docs/                   architecture, operator docs, licensing, release notes
LEGAL.md                privacy policy and terms; every public bot's /privacy and /terms link here
docker-compose.yml      a local Postgres for development and tests
```

## How main.py loads the bots

The bot code is written as if each bot were alone: `bot.py` does `import db`, `import i18n`,
`import family_link`. main.py makes that work for five bots in one interpreter:

- **One package per bot.** `register(bot)` creates an empty package named after the folder
  (`sticker_bot`, ...). `_BotFinder` (on `sys.meta_path`) resolves `sticker_bot.db` to
  `bots/sticker_bot/db.py`, and `sticker_bot.family_link` to `bots/shared/family_link.py`: every
  bot gets **its own copy** of each shared module, with its own globals, pool and state.
- **Bare imports stay inside the bot.** Each loaded module gets a private `__builtins__` whose
  `__import__` turns `import db` into `import sticker_bot.db` for any name the bot owns (its
  files plus the shared ones). Third-party imports pass through unchanged.
- **Settings per bot.** Inside a bot module, `os.environ` / `os.getenv` is a view (`BotEnviron`):
  `CBOT_FOO` overrides `FOO` for ConvertBot only; `DB_SCHEMA` is the folder name; a few keys
  (`PER_BOT_ONLY` in main.py) are never shared between bots.
- **Per-bot data folder.** Each module's `__file__` is `data/<bot>/<name>.py`, so anything a bot
  writes next to "itself" (logs, a per-bot `.env`) lands in `data/<bot>/`, not in the code.
- `NOT_FOR` lists shared modules a bot does not get (ManagerBot has no `shared_features`).

Because of this, **never import a bot module by its bare name from outside a bot** (tests and
scripts go through `main.register()` + `importlib.import_module("<bot>_bot.<name>")`, as
`tests/conftest.py` does).

## Running modules as child processes

Heavy or risky work runs in a child process started as
`python main.py --module <key> <json job>`, which executes one module as `__main__` with that bot's
imports and settings:

- `convert_bot.convert_worker`: every conversion (memory-capped, killable).
- `big_files`: large-file transfers over MTProto.
- `<bot>_bot.db_merge`: one-off database merges, run by hand.

Keys are `name` for `bots/shared/name.py` and `<bot>_bot.name` for `bots/<bot>_bot/name.py`.

## Processes

`MUMU_PROCESSES` decides how the bots are split: `one` (default) runs them all in one asyncio
loop (`Family`); `separate` gives each its own process; `manager;convert` gives those two a
process each and the rest one together. With more than one process, the parent is a
`Supervisor` that restarts a crashed child with backoff while the others carry on.
`BOTS=convert,sticker` limits which bots run at all (for splitting across Railway services).

Within one process a bot that fails to start, or asks to restart (`os._exit` inside a bot is
caught), is reloaded alone; the others keep polling.

## Database

- One Postgres (`DATABASE_URL`, e.g. Supabase session pooler).
- A schema per bot, created by that bot's `db.init_db()`; the `family` schema holds the bus
  (commands/events between ManagerBot and the others), the shared wallet, heartbeats, usage,
  redeploy state.
- Personal data (AnonBot's conversations, usernames, saved state) is sealed with the **data key**
  (`vault.py`), given as `DATA_KEY` or `DATA_KEY_FILE`. See `docs/data-key-options.md`.

## Commands

```bash
python main.py                  # run every bot that has a token
python main.py convert sticker  # run only these
python main.py --list           # which bots exist and which have tokens
python main.py --check          # load every bot without starting it, run self-tests (no DB needed)
python main.py --separate       # one process per bot
```

The Docker build runs `main.py --check`, so an image that cannot load every bot is never built.
