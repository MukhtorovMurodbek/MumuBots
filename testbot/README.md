# TestBot

A spare Telegram bot running the family's real code, so a change can be tried
against a real Telegram client before it reaches anybody.

`tests/anon_scenarios.py` and the rest cover the logic in seconds, and they
should stay the first thing you run. What they cannot answer is what a
*client* does: whether a `ForceReply` is honoured on iOS the way it is on
desktop, whether every part of an album really carries `reply_to_message`,
what a 49 MB file does on the way out. That needs a real bot and a real
phone, and this is it.

---

## Setup, in full

**1. Have a token.** You are reusing `@Personal_Sticker_Creator_bot`. Open
[@BotFather](https://t.me/BotFather) → `/mybots` → pick that bot → **API
Token**. It looks like:

```
8123456789:AAH0pQ7-abcdefGHIJklmnOPqrstUVwxyz0
```

If that token has ever been in a chat, a screenshot or a commit, hit **Revoke
current token** first and use the new one.

**2. Make your copy of the settings file.** From the repo root:

```powershell
Copy-Item testbot\token.env.example testbot\token.env
notepad testbot\token.env
```

**3. Paste the token.** In `testbot\token.env` there is exactly one line to
fill in, and it is marked:

```
TESTBOT_TOKEN=8123456789:AAH0pQ7-abcdefGHIJklmnOPqrstUVwxyz0
```

No quotes, no spaces around the `=`. While you are there, put your own
numeric Telegram id on `TESTBOT_ADMIN_ID=` so the owner-only commands answer
you — `/me` in ManagerBot tells you what it is.

That is the whole edit. Everything else in the file already works.

**4. Start the local database**, if it is not already running:

```powershell
docker compose up -d
```

**5. Run it:**

```powershell
.\testbot\run.ps1
```

That is all five bots at once. In the chat, `/asConvert`, `/asSticker`,
`/asDownloader`, `/asAnon` and `/asManager` switch between them (or the other
way round: `/convertbot`, `/stickerbot`...), and `/as` says which one it is
and lists the rest. It carries on as whichever it was last time;
`.\testbot\run.ps1 all anon` starts it as AnonBot. Ctrl-C stops it.

To run a single bot's code on its own, the way it always worked:

```powershell
.\testbot\run.ps1 anon
```

`sticker`, `convert`, `downloader` and `anon` all work that way.

---

## All five at once

`testbot\switch.py` loads every bot's real code into one process — the same
files, each as a package of its own, so ConvertBot's `import db` is still
ConvertBot's db — and each keeps its own `testbot_<bot>` schema and its own
id on the family bus (`testbot_convert`, ...). All five stay running;
switching only changes where your next message goes. A conversion keeps
going, and keeps updating its card, while you talk to AnonBot.

Telegram allows one poller per token, so `switch.py` is the poller, and it
hands each update to one bot:

- a **button** goes to the bot that drew it, whichever one it is now;
- a **reply** to one of the bot's messages goes to the bot that wrote it —
  which is how answering an AnonBot message still works after you switched;
- a **payment** goes to the bot that sent the invoice;
- **everything else** goes to the current bot.

The `/` menu, the description and the short description belong to the token,
so only the bot it starts as publishes them. A switch then changes one thing:
the menu in **your** chat (`TESTBOT_ADMIN_ID`), which outranks every other
menu, so the `/` button always shows the bot you are talking to, with `/as`
at the end. Everybody else keeps the menu of the bot it started as.
Rewriting everything on every switch is what Telegram punishes: it answered
a few quick rounds of that with a 285-second wait, then 1941 seconds with the
wrong menu showing. If it still asks for a wait, `switch.py` waits and `/as`
says for how long.

ManagerBot looks after the four test bots, not the real ones: `/status`,
`/logs convert` and the bus all work on them.

**`.\testbot\run.ps1 audit`** starts all five and, for each, compares the
commands it really handles — read off the running bot, not its source — with
every menu it publishes, in every scope and language, as it asked Telegram for
them. It prints what is in a menu with nothing behind it, what differs between
languages, and what is handled but in no menu. Then, switched to each in turn,
it asks Telegram what your chat actually shows, and checks each bot runs under
its test id. Everything the bots send on the way up arrives silently.

It refuses to start on a token that is one of the real bots'. From the bot
folders' own `.env` files and `shared\.env` it takes the Telegram app's API id
and hash -- `TELEGRAM_API_ID`/`_HASH`, or ConvertBot's and DownloaderBot's own
`CBOT_`/`DBOT_API_ID` and `_HASH` -- and nothing else; they are what moves
files over 20 MB, and they can go in `token.env` instead.

---

## What it is, and what it is not

**It is the real code.** `switch.py` loads each bot's own files, and
`shared/`'s, from this version folder — the same files a deployment runs. Not
a copy, not a cut-down version, and nothing to port: a function added to a bot
or to `shared/` is in TestBot the next time it starts. That is the point: a
test against a simplified bot tests the simplification.

**It is called TestBot on the bus.** Each bot runs as `testbot_<bot>`
(from `FAMILY_BOT_ID=testbot`) and is labelled "TestBot as <Bot>". Without that
a test run would stamp its heartbeat under `anonbot`, and ManagerBot would
cheerfully report a laptop process as the live AnonBot.

**It writes to its own tables.** Each bot gets its own `testbot_<bot>`
schema, on the local Postgres from `docker-compose.yml` or on the shared
database — see "Either database" below for what else keeps a run on the
shared one out of the real bots' way.

**It does not advertise the real bots.** `SIBLING_BOTS=` is empty, so the
cross-promotion row under `/start` is not drawn. A tester tapping through to
production is how a test ends up somewhere real.

**The bots' own `.env` files are not read at all.** They hold the real
tokens, and `shared/.env` the production database; `switch.py` sets
`ENV_FILES=off`, so the bots take their settings from `token.env` alone —
apart from the Telegram app's API id and hash, which it borrows because they
are an app's credentials, not a bot's. It also refuses to start on a token
that is one of the real bots'.

---

## Everything it does stays its own

TestBot runs on this machine only -- the owner, 2026-09-28: "I will use
testbot only to test the new version practically before pushing to railway.
Only hosting locally is needed for testbot." And one at a time: a second
`run.ps1` refuses to start while one is running (the lock is `testbot/.lock`),
because two pollers on one token split its updates between them.

On either database it keeps to its own names and tables:

- each bot's tables are in its `testbot_<bot>` schema, and it is
  `testbot_<bot>` on the family bus;
- what the real bots keep in the shared `family` schema -- heartbeats, the
  bus, settings, **the ⚡ wallet** -- it keeps in `testbot_family`, so its
  credit is test credit;
- **every bot's `BOT_NAME` is replaced with its test id before it starts**,
  because lifecycle keys the poll lease, the maintenance flag, the update
  waitlist and the expected-restart marker on it. Under the real name, a test
  bot stopping would mark the real bot's restart as expected, and a `/pause`
  through the test ManagerBot would pause the real one. `run.ps1 audit` checks
  both on the running bots;
- **Stars are sandboxed** (`FAMILY_STARS_SANDBOX`): a top-up is credited
  without Telegram charging anybody, and every message about one says so.
  `FAMILY_STARS_SANDBOX=0` in `token.env` turns that off, for the day a real
  payment has to be tested.

---

## Either database

`TESTBOT_DATABASE_URL` in `token.env` is the local database from
`docker-compose.yml`, or the shared one the real bots use. Any setting in the
shell wins over the file, so one run can go elsewhere without editing it:

```powershell
$env:TESTBOT_DATABASE_URL = "postgresql://postgres:postgres@localhost:5432/botfamily"
.\testbot\run.ps1
```

**On the shared one, TestBot is read-only by the database's own rules.** It
runs there only as a login of its own, `testbot`, that can read what the real
bots keep and cannot write anywhere but the `testbot_*` schemas -- and before
any bot loads, it asks the database what the login it was given could change.
As the `postgres` user, which can change everything, it refuses to start and
says so. A rule enforced by the code it protects against would be as good as
that code; this one is as good as Postgres.

Making the login, once:

1. Open `testbot/supabase_role.sql`, replace `CHANGE-ME` with a password of
   your own, and run it in Supabase -> SQL Editor. It makes the login, gives
   it the six `testbot_*` schemas (taking over any an earlier TestBot made as
   `postgres`), lets it read the real bots' schemas, caps it at 24
   connections -- so it can never crowd the real bots out -- and cancels any
   statement of its over a minute.
2. In `token.env`, the real `DATABASE_URL` with two things changed: the user
   `postgres.<project ref>` becomes `testbot.<project ref>`, and the password
   becomes yours.

`tests/testbot_role_scenarios.py` runs that same file against a local
Postgres and checks what the login can and cannot do.

---

## The rule about the token

`token.env` is gitignored, so a push cannot carry it. `token.env.example` is
the tracked one and must never contain a real token.

Nothing else needs the token — not this README, not a commit message, not a
chat. If it ends up somewhere it should not be, `@BotFather` → **Revoke
current token** takes about ten seconds and invalidates the old one
completely.
