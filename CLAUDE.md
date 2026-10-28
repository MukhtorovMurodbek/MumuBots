# MumuBots: notes for Claude

Five Telegram bots (python-telegram-bot 22, Postgres via psycopg 3) run from one launcher.
Read `docs/architecture.md` before changing anything; `CONTRIBUTING.md` says where code goes.

Essentials:
- `main.py` loads `bots/<bot>_bot/*.py` and `bots/shared/*.py` into a separate package per bot.
  Bare imports (`import db`) resolve to the current bot's copy. Do not add `sys.path` hacks or
  cross-bot imports, and do not import bot modules by bare name from tests or scripts: use
  `main.register(bot)` + `importlib.import_module("<bot>_bot.<name>")` (see `tests/conftest.py`).
- Settings come from `os.environ` as seen by the bot (`<PREFIX>_X` overrides `X`). Document every
  new setting in `.env.example`.
- User-facing text goes in `i18n.py` in en, uz and ru.
- Child processes run as `python main.py --module <key>`; module paths are computed relative to
  `bots/` (see `convert_bot/jobs.py`, `shared/big_files.py`).
- Comments explain *why*. Code written after 2026-10-24 (much of 2.2.0's later work) has few; add
  them where they help, and fix any comment a change makes untrue in the same change.
- Privacy: anything new that is stored about people must also be described in `LEGAL.md` and in
  the bot's `/privacy` text (i18n `privacy_stored`), with how long it is kept.

Checks:
- `python main.py --check` (no database needed; the deno check fails on machines without deno).
- `pytest tests` against `docker compose up -d` (or `TEST_ADMIN_URL` pointing at a Postgres the tests may
  create/drop `mumu_tests` on; on the owner's laptop: `postgresql://postgres@localhost:5433/postgres`, the
  scratch cluster in `TempTools/postgres`).
- The version shown by the bots is `RELEASE` in `bots/shared/family_link.py` (or `FAMILY_RELEASE`).

Never commit `.env`, cookie files, or the data key.
