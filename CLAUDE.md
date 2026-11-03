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

## The owner's standing rules

These apply in every session, local or cloud. A decision the owner makes in the current chat
outranks one written here.

- **Production is untouchable without the owner's word.** `main` is what the bots are deployed from:
  never push to it, merge into it, or retag it. Never deploy, change Railway, or write to the
  production Supabase database (a connector may give you that access: use it read-only).
- **Work on branch `2.2.0`** (the next release) or on branches off it, and push them freely.
  A cloud session starts from the default branch, which is still the old 2.1.0 layout: switch to
  `2.2.0` first.
- **No pull requests** until the owner confirms Railway's PR environments are off: one would start a
  second copy of the bots with production's tokens.
- **Nothing deployed today may be lost.** Every command, button, job, setting and text of the
  deployed version must keep working, and the code that migrates existing data stays.
- YouTube works but is never mentioned in user-facing or public text.
- Quality first. Say plainly when an idea isn't worth doing, and why; say when a request collides
  with something already built.
- Before a deploy, the owner asks for the differences list: write it like
  `docs/releases/2.1.1CH-to-2.2.0.md`, from the real code diff against the deployed branch.
- Keep `main`'s tree clean: no scratch, backups or work-in-progress files in the repository.
- Commit as the owner's git identity (the repository's own config).

## Testing in a cloud session

The tests need a Postgres they may create and drop a database on. If none is running:

```bash
apt-get update && apt-get install -y postgresql ffmpeg libarchive-tools
service postgresql start
su postgres -c "psql -c \"ALTER USER postgres PASSWORD 'postgres'\""
pip install -r requirements-dev.txt
export TEST_ADMIN_URL=postgresql://postgres:postgres@localhost:5432/postgres
```

(prefix with `sudo` where you are not root). Then the checks above. CI runs the same on every
pushed branch (`.github/workflows/ci.yml`).
