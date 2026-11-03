# Reserve copies of the database

Supabase's free plan keeps no backups, so the bots keep their own. They are for emergencies only: a lost or broken database.

## What is kept, and where

| Copy | What | Where | When |
|---|---|---|---|
| **Nightly** | the whole database | your Telegram chat with ManagerBot, pinned, never deleted | right after the 22:00 nightly report (`MBOT_NIGHTLY_UTC`) |
| **Nightly, sealed** | the same | the Railway volume, `nightly.mumu-reserve` (only the newest) | the same moment |
| **Current** | everything except logs and counters: settings, packs, links, payments, ⚡ credit, feedback, erasure notes | the Railway volume, `latest.mumu-reserve` | within about 2 minutes of any change to those tables |
| **Erasure notes** | who used `/deletemydata`, the id sealed | the `family.erasures` table, and `erasures.jsonl` on the volume (written at once) | at each erase; dropped after 30 days |

- `/backup` in ManagerBot sends the same whole-database copy whenever you ask.
- *Sealed* means encrypted with the data key. The Telegram copy is not sealed as a whole, so it can still be restored if the key is ever lost. The personal parts inside it are sealed column by column already.
- **No copy older than 30 days is ever restored.** LEGAL.md promises this, and the restore script refuses older copies. The erasure notes only need to live that long.
- Nothing is kept on your laptop.

## Why the erasure notes exist

Say someone sends `/deletemydata` at 15:00 and the database is lost at 17:00. Last night's copy still has them. The restore puts the data back, then erases again everyone whose note is newer than the copy, using each bot's own `/deletemydata` code.

The note goes to the volume journal before the person is told their data is gone. So even an erase seconds before the loss is covered.

Known limit: if someone erased and then used the same bot again before the emergency, what they did after coming back is erased too. Restoring the newest copies keeps that window under a day.

## Setting up the volume (Railway, once)

Without a volume, the nightly Telegram copy still works. Only the "Current" copy and the journal need it.

1. Railway → the bots' service → **+ New → Volume**. Mount path `/data`.
   - A Railway service can have only one volume. If yours already has one for the data key file, use a folder on it, or better, move the key into the sealed `DATA_KEY` variable first (`docs/data-key-options.md`). A key on the same volume as the sealed copies would open them for anyone who got the volume.
2. Variables → `RESERVE_DIR` = `/data/reserve`.
3. Deploy. Within a minute ManagerBot writes the first `latest.mumu-reserve`.
   - If it can't write, it tells you once in Telegram. Railway mounts volumes as root and the bots run as their own user. The fix is the variable `RAILWAY_RUN_UID=0`, which runs the container as root (a little less contained, but the bots hold the same data and key either way).
4. Side effect: Railway doesn't overlap two deployments of a service that has a volume, so each deploy has a short gap (seconds) where no bot answers. Updates people send meanwhile wait at Telegram and are answered after.
5. Check: the next nightly copy's caption says "Volume: sealed copy kept; the current one is N min old".

`MBOT_RESERVE_MIN_SECONDS` (default 120) is the shortest gap between two "Current" copies.

## Restoring, step by step

Only in an emergency. Practise once on a scratch database first (for example the local one in `TempTools/postgres`).

1. **Stop the bots** (Railway → the service → remove the active deployment, or set `BOTS` to a bot name that doesn't exist).
2. **Get the files.**
   - The newest nightly copy: from Telegram (`botfamily_<date>.zip`) or the volume (`nightly.mumu-reserve`).
   - From the volume, if it survived: `latest.mumu-reserve` and `erasures.jsonl`. With the Railway CLI: `railway ssh`, then copy them out, or attach the volume to a temporary service.
3. **A working, empty database.** If the old one is gone, create a new Supabase project and put its session-pooler URL (port 5432) in `DATABASE_URL`. Start the bots once so they create their tables, then stop them again.
4. **Restore.** With the target address in an environment variable (never on the command line):
   ```bash
   python scripts/restore_reserve.py --database-url-env DATABASE_URL --key-file <key file> --copy botfamily_<date>.zip --latest latest.mumu-reserve --erasures erasures.jsonl
   ```
   - `--latest` and `--erasures` are optional. Leave them out if the volume is gone too.
   - With the key in a password manager, use `--key-env DATA_KEY` instead of `--key-file`.
   - It refuses while any bot has sent a heartbeat in the last 3 minutes.
   - It prints table names, row counts and how many people were erased again. Nothing personal.
5. **Start the bots**, then check `/status` and `/people` in ManagerBot.
