# Contributing

Thank you for looking — but read the first line before writing any code.

## This repository is a mirror

StickerBot's source is developed in a private monorepo alongside its sibling bots,
which share several files byte-for-byte. This repository is **overwritten
wholesale on every release**: each publish replaces its contents with the
current state of that folder in one commit.

Two consequences, and they are the reason this file exists:

- **A pull request here cannot be merged.** It would be overwritten by the
  next release even if it were. Please do not spend an evening on one.
- **Commits here are not the project's history.** One commit per release, not
  one per change. The reasoning behind a change lives in the monorepo's
  version notes.

None of that is meant to keep anybody out. It is how five bots that share
their plumbing stay identical to each other without a submodule.

## What is useful instead

**Open an issue.** Bug reports, a platform that stopped working, a translation
that reads wrong, a feature that would earn its place — all of it is read, and
issues are the one thing on this repository that is not overwritten.

Useful in a bug report: what was sent, what came back, and roughly when. Never
include a token, and never include somebody else's message.

**Say so in the bot.** Every bot answers `/help`, and the operator reads what
arrives.

**Security bugs go elsewhere.** See `SECURITY.md` — privately first, not as an
issue.

## Running it yourself

`README.md` has the whole of it: install the requirements, copy
`.env.example` to `.env`, fill in a bot token from
[@BotFather](https://t.me/BotFather), point `DATABASE_URL` at a Postgres, and
run `python bot.py`. It runs standalone; the sibling bots and the shared
family bus are optional and switch off with one environment variable.

Forking and running your own copy is the intended way to build on this.
