# MumuBots

A family of five Telegram bots that share one codebase, one Docker image and one Postgres database:

- **StickerBot**: turns pictures, GIFs, videos and other stickers into Telegram sticker packs.
- **ConvertBot**: converts files between formats: images, RAW photos, documents, spreadsheets, archives, video, and text recognition (OCR).
- **DownloaderBot**: downloads media from Instagram, TikTok, Twitter/X, Pinterest and Reddit links.
- **AnonBot**: anonymous-question inboxes.
- **ManagerBot**: the operator's control panel: status, usage, problem reports, broadcasts, backups, restarts.

The public bots speak English, Uzbek and Russian, and take optional Telegram Stars payments for heavy jobs.

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
   `<PREFIX>_TOKEN` / `<PREFIX>_USERNAME` for each bot you want to run.
4. Set the data key as a sealed `DATA_KEY` variable, or put it on a volume and point `DATA_KEY_FILE`
   at it. See [docs/data-key-options.md](docs/data-key-options.md). Keep a copy somewhere safe:
   sealed data cannot be read without it.

Run a single replica: two copies polling the same token split its updates between them.
To split the bots across services, give each service the same image and its own `BOTS=` list.

> Railway stops reading `railway.json` on 2026-12-01. `.railway/railway.ts` is the draft
> replacement; read its header before applying it.

## Documentation

- [Architecture](docs/architecture.md): how one process runs five bots, settings, processes, database.
- [Data key options](docs/data-key-options.md): protecting user data if something leaks.
- [Release notes](docs/releases/): what changed in each version and how to deploy it.

## Development

```bash
pip install -r requirements-dev.txt
docker compose up -d               # the local Postgres the tests use by default
pytest tests
```

The tests create and drop a database named `mumu_tests` on that server; point `TEST_ADMIN_URL` elsewhere
to use another.
See [CONTRIBUTING.md](CONTRIBUTING.md) for how the code is organised and how to add to it.

## Licence

[AGPL-3.0-or-later](LICENSE): anybody may run, change and share these bots, and anybody who runs a
changed version as a public service has to publish their changes. [docs/legal/LICENSING.md](docs/legal/LICENSING.md)
explains why (in short: ConvertBot depends on PyMuPDF, which is AGPL). What the bots keep about
people, and their terms of use, are in [LEGAL.md](LEGAL.md).

## Security

Never commit `.env`, cookie files or the data key; `.gitignore` excludes them. To report a vulnerability,
see [.github/SECURITY.md](.github/SECURITY.md): privately, never in a public issue.
