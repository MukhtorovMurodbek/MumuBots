# Shipping an update without anybody noticing

Five bots, five Railway services, one Supabase database. Pushing a change
replaces a running container, and until now that meant four separate things
went wrong at once. This is what each of them was, and what now handles it.

Nothing here needs a special deploy command. A push is still the whole
procedure — see [The procedure](#the-procedure) at the end.

---

## 1. Two bots holding one token

Telegram allows exactly one `getUpdates` consumer per bot token. If the new
container starts polling before the old one has stopped, the second one gets
**409 Conflict** and updates are split unpredictably between them — some
users answered by the old code, some by the new, some not at all.

**A Postgres advisory lock** (`lifecycle.hold_the_lease`) is what stops it.
Each bot takes `pg_try_advisory_lock` on a key derived from its own name, on
a dedicated connection, in `post_init` — *before* its first `getUpdates`. A
starting container that finds the lock held waits up to
`DEPLOY_LEASE_WAIT_SECONDS` (240) for the old one to let go.

`overlapSeconds` in `railway.json` used to be `0` — stop the old container,
then start the new one — because before the lock existed that was the only
thing preventing two pollers. It is `30` as of v1.2.2R, and the reason is
[the gap](#5-the-gap-itself) below.

The lock is session-level, so Postgres releases it when the holder's
connection ends — including when the old container was killed rather than
asked. This also covers the case the platform knows nothing about: a copy
running on your laptop against the same database.

If the lease cannot be checked at all (database unreachable), the bot logs a
warning and **starts anyway**. A monitoring mechanism must never be able to
stop the thing it monitors.

## 2. Everyone's half-finished work, forgotten

This was the one users actually felt. `user_data` and every
`ConversationHandler` state lived in the process's memory. Someone three
stickers into a pack sent the fourth thirty seconds after a deploy and got
*"I don't recognize that"* — from a bot that had known exactly what they were
doing.

`lifecycle.PostgresPersistence` is a `BasePersistence` writing both into a
`runtime_state` table in each bot's own schema:

| what | when it is written |
|---|---|
| open conversation states | as they change (single small rows, and being one state behind is the difference between resuming and dropping) |
| `user_data` | at shutdown, and every `DEPLOY_PERSIST_SECONDS` (180) as insurance against an OOM kill |

It stores **JSONB, not pickle** — readable through ManagerBot's `/sql`,
prunable with plain SQL, and unable to execute anything on the way back in.
The price is that values which are not JSON are dropped rather than stored.
That is deliberate and it is safe:

- **A live `telegram.Message` parked in `user_data`** is dropped, and so is
  a **tuple** — refused rather than silently returned as a list. AnonBot's
  held message used to be both at once, and it is the one value in this
  family whose loss a user actually *sees*: *"that message expired"*, on a
  Send-it-anyway button they were looking at seconds earlier. So it is not
  parked that way any more — it is stored as `Message.to_dict()` inside a
  dict, which is plain JSON, and rebuilt with `Message.de_json()` on the
  other side. Anything that is not that shape reads as nothing held, which
  is the answer that path gave before any of this.
- **A filesystem path to a staged upload** (ConvertBot) is dropped. The file
  was on the old container's disk and is gone regardless; that code already
  checks `os.path.exists` and refunds.

Rows are loaded once at startup, only if younger than
`DEPLOY_STATE_TTL_HOURS` (12), and anything older is deleted on the way past.

## 3. Work that was in flight

A video encode or a download running when SIGTERM arrives does not survive,
and no amount of engineering makes it survive — the container is going away.
What it can do is *say so*.

`lifecycle.busy()` marks the slow stretches:

```python
async with lifecycle.busy(chat_id, "converting your video"):
    ...
```

When a stop signal arrives, everyone inside one gets a message —
"🔄 I'm being updated right now, so converting your video stopped part-way.
Send it again in a few seconds and it'll go through." — before the process
exits. Handlers also check `lifecycle.is_draining()` before *starting*
anything long, and decline with the same explanation rather than beginning
work that cannot finish.

For this to work the bot has to hear SIGTERM before python-telegram-bot acts
on it, which is why `run_polling` is called through
`lifecycle.polling_kwargs(...)`: on POSIX it passes `stop_signals=()` and
takes the signals itself. On Windows it passes nothing and PTB's own handling
stands.

`drainingSeconds: 120` in `railway.json` is the other half — the time Railway
allows between SIGTERM and SIGKILL. It is comfortably above
`FFMPEG_TIMEOUT_SECONDS` (120s ceiling per encode is per-attempt, and a drain
does not start new ones).

**Updates sent while the container is between versions are not lost.**
Telegram holds undelivered updates for 24 hours and hands them over on the
next `getUpdates`. Nothing calls `run_polling(drop_pending_updates=True)`.
The gap costs those users latency and nothing else.

## 4. Being paged for your own deploy

A redeploy makes a heartbeat stale in exactly the way a crash does.
`lifecycle.mark_expected_restart()` leaves a timestamp in `family.settings`
on the way out; ManagerBot's watchdog treats a stale heartbeat with a fresh
note beside it as a redeploy and stays quiet — at both ends, so there is no
"it's back!" either. If the bot does not come back, the note ages out after
`MBOT_REDEPLOY_GRACE_SECONDS` (300) and the ordinary "it is down" alert
fires. A deploy that never came back is exactly what is worth being told
about.

---

## 5. The gap itself

Everything above is about not losing anything. This is about the minute in
which nothing answers.

With `overlapSeconds: 0`, Railway stopped the old container and *then* built
and started the new one. The bot was away for all of that — commonly one to
three minutes. Nothing was lost: Telegram holds undelivered updates for 24
hours and hands them over on the next `getUpdates`, and nothing here calls
`drop_pending_updates=True`. What it cost was a person typing into what
looked like a dead bot and getting an answer two minutes later.

`overlapSeconds: 30` lets the old container keep serving while the new one
builds and boots. The new one reaches `post_init`, blocks on the poll lease
before its first `getUpdates` and before its first heartbeat, and waits.
Railway stops the old one 30 seconds in; the old one drains, its connection
closes, Postgres releases the session-level lock, and the new one has it
within a second. **The gap shrinks from build-and-boot to drain alone.**

The two numbers are one decision. `DEPLOY_LEASE_WAIT_SECONDS` has to outlast
`overlapSeconds` (30) plus a worst-case `drainingSeconds` (120), or the new
container gives up waiting and starts polling next to the old one — which is
the 409 the lease exists to prevent. It is 240. Change either of the first
two and check this one.

### What is left, and the standby that would cover it

Two seconds of drain is not zero, and there is a worse case the overlap does
nothing for: **a deploy that never comes up**. A syntax error, a bad
migration, a missing environment variable — the new container crash-loops,
the old one is already gone, and the bot is simply silent until ManagerBot's
watchdog says so five minutes later. That is the outage that actually costs
users, and it is minutes to hours rather than seconds.

The answer to both is a standby, and the shape it has to take is fixed by the
same rule as everything else here: **Telegram allows exactly one `getUpdates`
consumer per token.** A standby cannot poll alongside the real bot. It can
only poll when the real bot is definitely not — which is exactly the gap, and
the poll lease is already the primitive that knows when that is.

The design, not built as of v1.2.2R:

- **One extra Railway service for all four public bots**, not one each. It
  runs no ffmpeg, no yt-dlp and no handlers — it needs about 40 MB and it has
  no blast radius worth isolating, which is the whole reason
  [ARCHITECTURE.md](../design/ARCHITECTURE.md) separates the real ones.
- For each token it tries `pg_try_advisory_lock` on that bot's lease key
  every second. Normally the real bot holds it, and the standby does nothing
  but retry. This costs one query per bot per second against a database the
  bots are already connected to.
- The moment it gets the lock — which means no real container holds it — it
  starts polling that token and answers everything with one sentence:
  *"🔧 I'm being updated, back in a minute — send that again shortly."* In
  the user's own language, read from the shared `user_settings` table it can
  already see, and recording them through `lifecycle.hold_for_update` so
  `/finishupdates` goes back to them when the real bot returns. That
  machinery exists and is used by `refuse_new_work` today.
- **The handover has to be automatic in both directions.** Coming back, the
  real container's `hold_the_lease` would find the lock held by the standby
  and wait the full 240 seconds — worse than no standby at all. So the
  standby must not hold the lock continuously: it releases and re-acquires
  every couple of seconds, and skips the re-acquire when
  `family.settings`'s `deploy:expected_restart` note says a real container is
  on its way. Handover back is then bounded by that cycle, not by the lease
  timeout.

What it buys, honestly stated: it does not make the bot work during a deploy
— it cannot, the code that does the work is what is being replaced. It
replaces silence with a sentence. That is worth much more during a failed
deploy (minutes to hours of nothing) than during a healthy one (seconds), and
the failed deploy is the case that has no other answer.

What it costs: one more service to pay for and to keep deployed, a second
place that holds the bot tokens, and a genuine new failure mode — a standby
with a bug in its lease handling can take the *real* bot's token hostage.
That last one is why the release/re-acquire cycle above is the design and not
an optimisation, and why this is written down before it is built.

---

## When a release goes out

The bots are deployed and in public use, so *when* is now part of the
procedure rather than a matter of convenience.

**A major or minor bump — the `X` or the `Y` in `x.y.z` — ships at 04:00
Asia/Tashkent (UTC+5), and is announced before it does.** Active users hear
about it first (ManagerBot's `/pause` writes the announcement everybody who
turns up is shown, and `/finishupdates` goes back to them afterwards); the
push itself waits for the window. The window is fixed rather than chosen from
an activity count: 04:00 is the reliably empty hour for this user base, and a
release scheduled for it does not need anybody to judge whether "quiet
enough" is quiet enough.

**A patch bump is the same by default and the exception when it is urgent.**
An ordinary `x.y.Z` is better placed in the 04:00 window like everything
else. A hotfix that cannot wait goes out immediately — that is what a hotfix
is — and what it costs is the gap in [section 5](#5-the-gap-itself): seconds,
now that the deploy overlaps, and delivered-late rather than lost either way.

**The activity check still runs**, but it answers a different question now.
It is not "is it safe to ship" — the window answers that — it is "is anybody
mid-something right now", which is the difference between a clean release and
interrupting somebody's download. Count `DISTINCT user_id` from each bot's
`activity_events` over the last few hours, **with the admin ids excluded**,
and wait while anybody is — `scripts/nightly_release.py` does exactly that.
Until 2.0.0 one busy bot could be held back on its own (`publish.ps1
-Skip`); since the bots share `shared/`, and every release changes it, a
release rebuilds all five, so it waits for all five to be quiet.

## The procedure

```powershell
python scripts\nightly_release.py      # in the 04:00 window, with the pause and resume around it
git push origin HEAD:main v<version>   # the same push, by hand
```

That is genuinely all of it. Since 2.0.0 this folder is a checkout of one
repository, MumuBots, and each bot's Railway service builds from it with the
bot's own Dockerfile, rebuilding when that bot's folder or `shared/` changes
(`<bot>/railway.json`). Until 2.0.0 it was `.\publish.ps1`, which laid each
bot folder into a public repository of its own, one commit each. What still
needs thought is a change to the shared files:

1. **The shared modules live once, in `shared/`** (since 2.0.0; until then
   each bot carried a byte-identical copy, and a change had to be copied to
   all five). A change there reaches every bot at its next deploy -- each
   bot's Railway service watches `shared/**` as well as its own folder, so a
   push that touches `shared/` rebuilds all five.

   **`python tests/test_shared_copies.py` fails if a bot folder grows a file
   of the same name**, which would quietly win over the shared one for that
   bot. It is part of `tests/run.py`, and it also covers the one copy that is
   still a copy: ManagerBot's `monitoring.py` carries 18 functions
   character-for-character identical to `shared/shared_features.py`, under a
   different filename, so a fix applied to one and not the other would be
   invisible otherwise.
2. **A change to the family bus reaches all five at once.** The bus is
   `shared/family_link.py`, so the push rebuilds every bot, and for the
   minute the builds take any of them may be on either side of the change.
   Until 2.0.0 the advice was to deploy ManagerBot last, so the watcher was
   the one on the older code; one push cannot order its builds, and it does
   not have to: a bot answers a family command it does not know with
   "Unknown command", and ManagerBot names the version a newer command
   needs (`COMMAND_SINCE`) rather than calling the bot broken.
3. **Watch it land**: `/status` in ManagerBot, then `/ping` — the round trip,
   leg by leg, is the fastest confirmation that a bot is not just up but
   reachable through the database.

### Rolling back

Railway's own rollback puts the previous image back. Persisted state is
forward- and backward-compatible by construction (unknown keys are ignored,
missing keys read as absent), so a rollback does not need the table cleared.

### Turning it off

`DEPLOY_SAFETY=off` disables the lease, the persistence and the drain
announcement in one go, and the bots behave exactly as they did before any of
this existed. `ConversationHandler(persistent=...)` follows the same switch,
so nothing has to be edited to go back.
