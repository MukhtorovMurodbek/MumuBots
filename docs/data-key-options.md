# Protecting user data if something leaks

## What a leak would expose today

Sealed with the data key (unreadable without it):
- everything AnonBot keeps about people and conversations;
- saved conversation state and the update waitlist;
- since 2.2.0, usernames and names in Sticker, Convert and DownloaderBot.

Plain:
- Telegram user ids in the Sticker, Convert and Downloader tables, with timestamps and payment amounts;
- pack names and titles;
- feedback text (it names nobody);
- usernames attached to problem reports.

The three ways it could leak, most likely first:

1. **The database.** A Supabase incident, a leaked `DATABASE_URL`, or a stray dump or backup.
   - The sealed parts stay safe as long as the key isn't stored with the data.
2. **Your laptop.** `MumuBots2.2.0/.env` holds the production bot tokens and `DATABASE_URL` in plain text, and the key file, if one is there.
3. **The host.** Whoever controls the Railway project can read the key at runtime. Nothing short of option C below defends against that.

## A. The key as a sealed Railway variable (built in 2.2.0, off until you set it)

Railway → the service → Variables → `DATA_KEY` = the key (the same base64 line that's in the key file) → ⋮ → **Seal**.

What it does:
- A sealed value is never shown again in the UI, the API, `railway variables` or `railway run`. It still reaches every deployment. It is not copied into duplicated or PR environments.
- At startup, `main.py`:
  - copies the key into a private file in memory-backed `/dev/shm`, for that run only;
  - removes `DATA_KEY` from the environment, so ffmpeg, yt-dlp, the conversion workers and the MTProto helper never inherit it.
- Code running as the bots' own user can still read it. Linux keeps a process's starting environment in `/proc/<pid>/environ`. This is the same exposure a key file on a volume has: any key the bots can use, their user can read.

Pros:
- Redeploys need nothing from you.
- Separate Railway services need no volumes: set the same sealed variable on each.
- A database leak doesn't come with the key.

Cons:
- Railway can't show the key back to you. **Keep a copy in a password manager**: lose both and the sealed data is gone for good.
- Doesn't help if the Railway account itself is taken over.

Switching over:
1. Add and seal `DATA_KEY`, then deploy.
2. Check the log for two lines:
   - "The data key comes from DATA_KEY"
   - "Data key checked: it is the key this database is sealed with"
3. Remove `DATA_KEY_FILE` and the key volume, and delete the key file from the laptop once it's in the password manager.
4. If the value is wrong:
   - AnonBot shows its maintenance note and the other bots keep running.
   - Correct the variable; Railway redeploys, and the bots pick up the new value.

If you move to `.railway/railway.ts`, keep `DATA_KEY: preserve()` in the service's `env`; the draft already has it. `railway config apply` deletes every variable the file doesn't list, and a sealed value can't be recovered.

The encryption script takes the key the same way: `--key-env DATA_KEY` instead of `--key-file`. Set the variable without it landing in your shell history, e.g. `read -s DATA_KEY; export DATA_KEY` in bash.

## B. Envelope encryption with a cloud KMS (not built; the upgrade path)

How it works:
- The data key is stored encrypted ("wrapped") by a master key that never leaves Google Cloud KMS or AWS KMS.
- Railway holds only the wrapped key and a credential that may unwrap it.

Gains over A:
- You can kill every copy at once by disabling the KMS key.
- Every unwrap is logged.
- A stolen environment dump is worthless once the credential is revoked.

Costs:
- About $0.06 (Google) or $1 (AWS) a month.
- One more account.
- A network call at startup. If the KMS is down the bots start in key-problem mode, which they now survive.
- Roughly 50 lines of code.

Worth it if the bots grow or the data gets more sensitive.

## C. Owner unlock (not recommended for you)

How it works:
- The bots start in key-problem mode.
- ManagerBot asks you for a passphrase.
- The key is derived from it and only ever held in memory.

This is the strongest protection: no disk, variable or database copy ever holds the key. But every deploy and every crash restart would wait for you, which doesn't fit frequent redeploys.

## D. Harden the database (do regardless)

- **Rotate the Supabase password.** The production `DATABASE_URL` sits in plain text in the project folder.
- **Give the bots their own Postgres role.** It should have rights only on their schemas, not `postgres`. A leaked URL then can't reach anything else in the project.
- **Restrict who can connect.**
  - Turn on Supabase's *Enforce SSL*.
  - If you move to Railway Pro, its static outbound IPs let Supabase's network restrictions accept only Railway.

## E. Keep less (mostly done; two easy next steps)

Done in 2.2.0:
- usernames and names are sealed;
- download rows are deleted after 2 days;
- activity is written at most once per person per hour;
- the `/people` history holds no ids.

Next steps:
- **Shorter activity retention.** `ACTIVITY_RETENTION_DAYS` could go from 90 to 35, since `/people` now keeps the long-term counts. The monthly-users alarm needs 30 days.
- **No usernames in problem reports.** The user id is enough to follow one up.

## Recommendation

1. **Now:** A. It's built, needs only the sealed variable, and removes the redeploy friction.
2. **This week:** the password rotation and least-privilege role from D.
3. **Later:** B, if you want a kill switch.
