# Keeping user data safe when something leaks

The owner, 2026-11-09: *"In case of another outage, data key contents can also be leaked. We got to think through this."*

This replaces `data-key-options.md`. That page recommended the key as a sealed Railway variable, which is the risk below.

## What happened, and what it means

- **2026-10-21: Railway's breach.** An attacker decrypted customers' environment variables (bot tokens, `DATABASE_URL`, anything else set there). Railway told everyone to rotate them.
- **Railway keeps every deployment's variables.** A rollback or a redeploy of an old deployment brings back its image *and its variables*. So a variable you delete is gone from the service, not from Railway's records.
- **So the data key must never be a Railway variable.** That means neither `DATA_KEY` nor `DATA_KEY_INIT`, even once. 2.1.x took the key through `DATA_KEY_INIT`, so a key set up that way is in Railway's deployment history: treat it as known and replace it once (step 4 below).

## What each kind of leak would expose

"Sealed" = encrypted with the data key. "Plain" = readable by whoever has the copy.

| What leaks | A key that went through a variable, copies sealed with it | 2.2.0, set up as below |
|---|---|---|
| **Railway variables** (like 2026-10-21) | Bot tokens and `DATABASE_URL`: the attacker can read the whole database. And the data key, through an old deployment's variables: then everything sealed opens too. | Tokens and `DATABASE_URL`: the database, with its sealed parts still sealed. The key was never in a variable. |
| **The Railway volume** | The key file, and a reserve copy sealed with that same key: the whole database. | The key file; reserve copies and erasure notes that only your offline key opens. Without the database, the key opens nothing. |
| **Supabase** (a dump, a stolen backup, a breach) | Everything, sealed parts sealed. | The same. |
| **Railway variables + volume together** (Railway fully compromised) | Everything. | Everything the bots can read. See "What is left". |
| **A nightly copy in Telegram** (a forward, a stolen account) | The whole database, sealed parts sealed. | Nothing: only your offline key opens it. |

What "sealed parts" covers: everything AnonBot keeps about people and who wrote to whom, usernames and names in every bot, saved conversation state. What is plain: Telegram user ids with times and payment amounts in Sticker, Convert and DownloaderBot (the allowances and payments need them), pack names, feedback text.

## The two keys

| | The data key | Your offline key |
|---|---|---|
| What it does | Seals personal data in the database. The bots use it all day. | Encrypts the backups and erasure notes. The bots never use it. |
| Where it lives | A file on each service's volume (`DATA_KEY_FILE`), and in your password manager. **Never in a Railway variable.** | Private half: your password manager, nowhere else. Public half: `BACKUP_PUBLIC_KEY` on every service (not a secret). |
| Lose it | Sealed data is gone for good. | Backups can't be opened. Make a new pair; only new backups use it. |

## Setting it up (once, before or with the 2.2.0 deploy)

1. **Make your offline key.**
   ```bash
   python scripts/backup_key.py
   ```
   - Save the `AGE-SECRET-KEY-1...` line in your password manager.
   - Set the `age1...` line as `BACKUP_PUBLIC_KEY` on **both** services (MumuCombined and MumuManager).
   - The format is the standard `age` one, so `age -d -i key.txt copy.zip.age > copy.zip` also opens a copy, without this code.
2. **Put the data key on each volume without a variable.** On your laptop, with the Railway CLI logged in, and the key in your password manager:
   ```bash
   railway ssh -s MumuCombined
   ```
   Then, inside, using the path in that service's `DATA_KEY_FILE` variable:
   ```bash
   umask 077; cat > /data/mumu_data_key
   ```
   Paste the key line, press Enter, then Ctrl-D. Then run `chown bots /data/mumu_data_key; exit`. Do the same for `-s MumuManager` with its path.
3. **Delete `DATA_KEY` and `DATA_KEY_INIT`** from both services' variables, if they're there. ManagerBot's `/status` shows a ⚠️ line while a key came through a variable.
4. **Replace the key once, if it ever went through a variable** (`DATA_KEY_INIT` included). With the bots stopped:
   ```bash
   python scripts/rotate_data_key.py --database-url-env DATABASE_URL --key-file old.key --new-key-file new.key --dry-run
   ```
   ```bash
   python scripts/rotate_data_key.py --database-url-env DATABASE_URL --key-file old.key --new-key-file new.key
   ```
   - It makes `new.key` if it doesn't exist. Save it in your password manager.
   - It re-seals everything under the new key in one transaction, and never writes anything readable to the database.
   - Then put `new.key` on both volumes (step 2), start the bots, and delete `old.key` and `new.key` from the laptop.
   - Keep the old key in the password manager for 30 days. A reserve copy taken before the rotation still holds its seals: after restoring one, run the rotation again on the restored database.
5. **Check** ManagerBot's `/status`: `Backups: encrypted to your offline key …`, and no ⚠️ about the data key.

## If Railway or Supabase announces a leak

In this order. The first two matter most, because a stolen token lets someone read new messages to the bots while it works.

1. **Pause the bots** (ManagerBot's pause command, or stop the deployments).
2. **New bot tokens.** In @BotFather, `/revoke` each of the five, then set the new ones on Railway.
3. **New database password.** Supabase → Project Settings → Database → reset the password, then update `DATABASE_URL` on both services.
4. **The data key**, if the volume or the key could have been reached: rotate it (step 4 above). Not needed for a variables-only leak, as long as the key was never a variable.
5. **Other secrets** that were variables:
   - Cookies (`DBOT_*_COOKIES`): log that throwaway account out everywhere.
   - `TELEGRAM_API_HASH` can't be rotated, and isn't dangerous alone.
6. **Redeploy**, then check `/status`.

## What is left, plainly

- **Whoever controls the running container sees what the bots see**, as it happens: messages arriving, the key in memory. No design that keeps the bots working without you defends against that.
- **The database and the data key together open AnonBot's who-wrote-to-whom.** The key now lives only on the volume and in your password manager, so this takes a breach of both Railway's volume and the database. The next section removes even that.
- **Plain ids in Sticker, Convert and DownloaderBot.** A database leak shows that a Telegram id used a bot, when, and what they paid. This is what allowances and refunds run on.

## Next step, not built: AnonBot keys that live in Telegram

**Idea.** Today the database row that says "this anonymous message came from X" is sealed with the data key, so database + key = de-anonymised. Instead, each relayed message could carry a random token in its own buttons. The row saying where a reply goes would be encrypted with *that token*, and the token is stored only in the Telegram message. Tapping Reply (or replying to the message) hands the token back, and only then can the bot read the row. A leak of the database and the key together would no longer link anyone.

**What it costs.**
- About a third of AnonBot's storage code changes, with a migration for open conversations.
- Someone writing to you keeps their "current conversation" in memory only, so after a redeploy they reopen the link or reply to your last message.
- It hides who talks to whom, not how busy a chat is.

**Recommendation.** Worth doing if AnonBot grows or its users' safety is at stake. It is the one change that protects against a full server breach. It's a release of its own, not a patch.
