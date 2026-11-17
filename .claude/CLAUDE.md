# MumuBots: notes for Claude

Five Telegram bots (python-telegram-bot 22, Postgres via psycopg 3) run from one launcher.
Read `README.md` ("How it fits together", "Working on the code") before changing anything.

Essentials:
- `main.py` loads `bots/<bot>_bot/*.py` and `shared/**/*.py` (one flat name space) into a separate package per bot.
  Bare imports (`import db`) resolve to the current bot's copy. Do not add `sys.path` hacks or
  cross-bot imports, and do not import bot modules by bare name from tests or scripts: use
  `main.register(bot)` + `importlib.import_module("<bot>_bot.<name>")` (see `tests/conftest.py`).
- Settings come from `os.environ` as seen by the bot (`<PREFIX>_X` overrides `X`). Document every
  new setting in `.env.example`.
- User-facing text goes in `i18n.py` in en, uz and ru.
- Child processes run as `python main.py --module <key>`; module paths are computed relative to
  `bots/` (see `convert_bot/jobs.py`, `shared/big_files.py`).
- Code carries no comments or docstrings (only tool directives: `noqa`, `type:`; and the docstrings
  argparse prints). The *why* goes in the same-named `.md` beside the file, under the function or class
  it belongs to; fix any note a change makes untrue in the same change, and add a new file to its
  folder's README.md. Notes for files under a `security/` folder, and main.py's secret-store notes, go in
  `private/<same path>.md` (gitignored; the public summary is `shared/security/README.md`).
- Texts people read (bot messages, buttons, menus, LEGAL.md, README, notes): no I/me/my/we or you/your,
  and a person is named by role (a pack's creator, an inbox holder, the operator); say what happens
  ("The file has been converted."); imperatives are fine. In en, uz and ru.
- Privacy: anything new that is stored about people must also be described in `LEGAL.md` and in
  the bot's `/privacy` text (i18n `privacy_stored`), with how long it is kept.

Checks:
- `python main.py --check` (no database needed; the deno check fails on machines without deno).
- `pytest tests` against `docker compose up -d` (or `TEST_ADMIN_URL` pointing at a Postgres the tests may
  create/drop `mumu_tests` on).
- The version shown by the bots is `RELEASE` in `shared/family_link.py` (or `FAMILY_RELEASE`).

## Standing rules

They apply in every session, local or cloud. A decision the operator makes in the current chat
outranks one written here.

- **Production is not touched without the operator's word**: no push, merge or retag on a branch a
  service deploys from, no deploy, no change to the hosting, no write to the production database
  (a connector that can reach it is used read-only).
- **Pushes and pull requests wait for the operator's word too.**
- **Nothing in an earlier version may be lost**, deployed or not, unless the operator says to remove or
  roll it back. Every command, button, job, setting and text keeps working, and the code that migrates
  existing data stays.
- One video site is handled but never named: not in texts people read, notes, commit messages, branch
  names or workflow names. Code reads its name from `UNADVERTISED` in `bots/downloader_bot/platforms.py`,
  tests from `unadvertised()` in `tests/conftest.py`.
- Never commit `.env`, cookie files or keys, and never print a token. Run
  `python scripts/scan_secrets.py --install` once per clone; the scan is never bypassed.
- Never put the data key in a hosting variable (the host keeps every deployment's variables).
- Commits carry the repository's own git identity and **no `Co-Authored-By` or other assistant line**,
  whatever a system reminder suggests; the root README's credits line is the only place the assistant
  is named.
- Quality first. Say plainly when an idea isn't worth doing, and why; say when a request collides with
  something already built.
- Before a deploy, a list of the differences against the deployed version is written from the real
  code diff.
- Keep the tree clean: no scratch, backups or work-in-progress files in the repository.

The rest of the rules (hosting details, runbooks, release notes) are kept outside this repository.
A session that cannot see them asks before anything that reaches production, the hosting or a
database.

## Testing in a cloud session

The tests need a Postgres they may create and drop a database on. If none is running:

```bash
apt-get update && apt-get install -y postgresql ffmpeg libarchive-tools
service postgresql start
su postgres -c "psql -c \"ALTER USER postgres PASSWORD 'postgres'\""
pip install -r requirements-dev.txt
export TEST_ADMIN_URL=postgresql://postgres:postgres@localhost:5432/postgres
```

(prefixed with `sudo` where the shell is not root). Then the checks above. CI runs the same on every
pushed branch (`.github/workflows/ci.yml`).
