# Contributing

Read [docs/architecture.md](docs/architecture.md) first: the way `main.py` loads the bots shapes
everything below.

## Where code goes

| You are changing | Put it in |
|---|---|
| What one bot does | `bots/<bot>_bot/` (handlers in `bot.py`, tables in `db.py`, texts in `i18n.py`) |
| Something every public bot does | `bots/shared/` (each bot gets its own copy) |
| A new module for one bot | `bots/<bot>_bot/<name>.py`, then `import <name>` from that bot's code |
| A new shared module | `bots/shared/<name>.py`; add it to `NOT_FOR` in `main.py` for bots that must not get it |
| How the bots are started or supervised | `main.py` |
| A one-off operator script | `scripts/` |

To add a sixth bot: create `bots/<name>_bot/bot.py` with a `main()` that builds a
`telegram.ext.Application` and calls `run_polling()`, add `db.py` / `i18n.py` as the others do, and
add a line to `BOTS` in `main.py` with its display name and env prefix.

## Rules that keep the bots apart

- Inside bot code, import bot modules by bare name (`import db`, `import family_link`). Never
  `from sticker_bot import ...` and never `sys.path` tricks.
- Read settings with `os.environ` / `os.getenv` inside the bot module; main.py gives each bot its
  own view (`CBOT_X` overrides `X` for ConvertBot).
- Every user-facing text goes in that bot's `i18n.py`, in all three languages (en, uz, ru).
- A bot's tables live in its own schema (`DB_SCHEMA`); cross-bot state goes through `family_link`.
- Anything that identifies a person is sealed with `vault` before it is stored.

## Before opening a pull request

```bash
python main.py --check      # every bot loads; worker, OCR and ffprobe self-tests
pytest tests                # with `docker compose up -d` running (see README)
```

CI runs both, and builds the Docker image. Files use LF line endings (`.gitattributes`).
