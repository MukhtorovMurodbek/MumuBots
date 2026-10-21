# v2.2.0 -- OCR and the album buttons, moved out of 2.1.0 (HANDOFF)

**Why this folder exists.** On 2026-10-10 the owner decided: *"Delay the features that are being built right
now to 2.2.0. Only do fixes"* for 2.1.0. This folder (and `MumuBots2.2.0C`) is the whole of 2.1.0's tree as it
stood at that moment, **with** the two features that were taken out of 2.1.0:

1. **OCR in ConvertBot** -- text from a photo or a scanned PDF, to TXT or a searchable PDF.
2. **DownloaderBot's album buttons** -- 📄 PDF and 🗜 ZIP under a delivered album.

Everything else in here is also in 2.1.0. Nothing is committed (the git state is 2.1.0's: uncommitted on top of
`v2.0.0`), and the version strings still say 2.1.0: they change only when the owner finalises 2.2.0
(memory `botfamily-version-numbers`). `CHANGES_v2.1.0.md` in this folder is a stale copy from the moment of the
copy; the real one is in `MumuBots2.1.0`. **Read this file first when resuming 2.2.0.**

**Then read `C:\Bitura\.Projects\MumuBots\PLAN_2.2.0_REMAINING.md`** (2026-10-13): what is left for 2.2.0
(link stripping, duplicate merge, the file-ID store, the ⚡ gift promotion, release finishing) and the owner's
open decisions. Work resumes after the weekly reset, Fri 2026-10-16 12:00 KST.

## Session 2026-10-18, second (Opus, the owner present) -- HANDOFF: state and the exact next step

**Built today, uncommitted, nothing deployed** (sections at the end of this file): help pages behind buttons like
/ping, and the 220 ⚡ gift (unique-user counter, one-time `/promo`, claim by writing to ConvertBot/DownloaderBot,
AnonBot's ad). `FAMILY_VERSION` default set to 2.2.0. Every test file touched passes (see the two sections).

**Fresh Opus review of `docs/changes/REVIEW_2026-10-18.patch`: 0 critical, 3 should-fix, 4 minor -- all fixed**
(2026-10-18 and 2026-10-21, Opus; each with a test, and a mutant that the test catches):
- **S1** the gift notice went out in English to people new to the bot (whom the ad sends): now
  `shared_features._notice_language` uses the language chosen in that bot, else the Telegram app's
  (`language_code`, mapped to en/uz/ru), else English, and never caches one (`promo_claim_scenarios.py` 62 per bot).
- **S2** `start_promo`/`stop_promo` raised after a committed write if the read-back failed, so the card said
  "nothing was started" when it had been (`_refresh_promo_after_write`; `test_promo.py` 87).
- **S3** AnonBot's ad took get_lang's cached "en" for a choice: it reads the database only (`promo_ad_scenarios.py` 104).
- **M4** the milestone notice no longer says "start it with /promo" when the gift already started (`test_promo_manager.py` 149).
- **M5** accepted: a claim in the instant before a stop commits still pays (docstring says so).
- **M6** no ad under `/deletemydata`, `/privacy` or `/terms`.
- **M7** `/gift` refuses the promo's key.

**State (2026-10-21).** Ported to `MumuBots2.2.0C` (`bundle_c.py --status` clean, `--lint` 0, `main.py --check` passes,
`core.py` says 2.2.0; README and LEGAL hand-edited). Full suite (`tests/run.py`, ffmpeg on PATH, at below-normal
priority while the owner was gaming): every suite passed but `test_jobs.py`'s "a conversion that was stopped stops
updating too", a timing check that then passed 3 runs of 3 alone (its code is untouched since 2026-10-17). Every
Postgres scenario file passes (credit 190, promo claim 62 x2, promo ad 104, promo counter 21, erase, help, anon 305,
sent files, taps). `docker build -t mumucombined-220c .` passed with the image's own check ("All checks passed"),
image 95819d7a05d7. **Staged:** both trees committed and tagged locally, `v2.2.0` here and `v2.2.0C` in
`MumuBots2.2.0C`; nothing pushed, nothing deployed.

**Next (the owner's go-ahead only):** `python scripts
ightly_release.py --edition C` from this folder. It pauses the
bots, waits for the 04:00 Tashkent window, pushes `MumuBots2.2.0C` to MumuCombined (Railway deploys on that push),
waits for the new version and resumes. Before it, the owner may want to read the uz/ru texts of `gift_ad` and
`gift_claimed`, and confirm Claude's readings of the gift (in its section). After it: `/help` in each bot, `/users`
for the count, and a day of logs without the "maximum number of running instances" line.

**State after the third session (2026-10-18, Opus; the owner away): everything planned for 2.2.0 is built.** Help per command (C6/D8), the last item, is built in `MumuBots2.2.0` (section "Help per command (C6/D8)" at the end of this file), reviewed by a fresh Opus (0 critical; 1 should-fix and 3 minor, all fixed), and ported to `MumuBots2.2.0C` (`--status` clean, `--lint` 0, 50 sections and `main.py` compiled under 3.11.9, `main.py --check` passes, the Docker image rebuilt with its own check passing). Full suite 85/85. Uncommitted, nothing deployed. Left for the owner: D1 (the gift's last day and notice text), D7 (deploy), and any objection to the `/help_<command>` form; then the release finishing in `PLAN_2.2.0_REMAINING.md`.

The paragraphs below are the state at the end of the second session.

**State at the end of the second session of 2026-10-17 (Opus, fresh context):** everything below is built, reviewed
twice by a fresh Opus, fixed, and **ported to `MumuBots2.2.0C`** (bundle `--status` clean, `--lint` 0, 51 sections
compiled under 3.11.9, `main.py --check` passes, the Docker image `mumucombined-220c` rebuilt with its in-image check
passing). Uncommitted, nothing deployed. Last full suite after every change: 83/84. The one failure was "archives,
both ways, and a hostile one", a Windows file lock (WinError 32) on a temp `.7z` while Docker Desktop was running;
that suite passed alone straight after and in both earlier full runs, and its code was not touched today. Also
`credit_scenarios.py` 164/164 on the scratch Postgres. **Left for the next window:
help per command (C6/D8)** -- the facts for every page are gathered in `C:\Bitura\.Projects\MumuBots\HELP_FACTS_2.2.0.md`.

**Built in the first session** (in `MumuBots2.2.0`; the first session's product-code diff is
`docs/changes/REVIEW_2026-10-17.patch`):

1. **A1-A3, DownloaderBot and the same link again** -- spec `C:\Bitura\.Projects\MumuBots\SPEC_REPEAT_LINKS_2.2.0.md`
   (Opus), built by Sonnet; its notes are `docs/changes/BUILD_NOTES_A1-A3.md`. A1 `platforms.canonical_link` at
   `detect_platform` (128 checks); A2 a duplicate link while the first is in flight gets a 👌 reaction, no job, no
   charge (65 checks); A3 re-send by Telegram file ID: `downloader_bot/sent_files.py`, table `sent_files` (HMAC key from
   the bot token, no user data), `resolvers.lookup` for the 12-hour "still public?" check, wiring in `bot.py` (282
   checks + `tests/sent_files_db_scenarios.py` against the scratch Postgres). Mutants 109/109 caught. Builder's
   deviations: the store's own `sent_files.usable` (so `DBOT_CACHE_SECONDS=0` does not turn it off); the ID send and
   the stale lookup run inside `lifecycle.busy`, no slot; `lookup` honours a route's `skip` list.
2. **B, the ⚡ gift** (Opus wrote it; Sonnet wrote the tests): `family_link.grant_bonus_once` (claim, credit and an
   expiring bonus lot in one transaction; the claim is shared with `grant_stars_once`; at most `GIFT_MAX_CREDIT` 1000),
   family command `gift <key> <credits> <last day> [--go]` (counts unless `--go`; gives everyone the bot knows; only
   the claim's winner is told, in their language), ManagerBot `/gift <bot|all> <key> <credits> <last day>` (queues the
   count at once, gives on a "Give it" button). `gift_granted` texts in all four public bots, en/uz/ru (a draft).
   `tests/credit_scenarios.py` 162 checks (real Postgres), `tests/test_gift.py` 280 checks (registered), 42/42 mutants.
   Example for the owner: `/gift all gift-100-users 100 2027-01-15`.
3. **Measured (production, read only):** C4 closed, resident memory came back from 200 MB (10-13 07:49) to ~117 MB the
   same evening and has stayed 113-115 MB since. C2: `cobalt:youtube` delivered in production on 10-14 (1 ok, 0 fail);
   `ytdlp:youtube` still fails "Sign in to confirm you're not a bot" (last 10-10), and cobalt is tried first.

**Decisions made in the owner's absence** (each is one place in the code; overturn freely):
- D2: unused file-ID rows are deleted after 90 days (`DBOT_FILEID_KEEP_DAYS`).
- D3: the answer to a merged duplicate is a 👌 reaction (`_answer_duplicate` in `downloader_bot/bot.py`).
- D4: a re-send by file ID **counts** against the daily allowance, like the five-minute cache's hit does today (the
  plan suggested not counting). Why: the store needs no download slot, so the 40-a-day allowance is what bounds a
  paste storm; real use is ~5 downloads a week, so nobody honest notices.
- Privacy design of the store: no new secret; key = HMAC(sha256("dbot-sent-files|" + bot token), version|platform|link|form).
- The gift: amount, last day and key are the command's arguments, so D1 is decided when the owner types it.

**Owner's decisions, 2026-10-17 evening:**
- Budget: the 5-hour window is capped at **80%** (not 90%) for this work.
- D1 amount: the gift is **200 ⚡** (Claude: fine for the budget, at most ~$0.30 of hardware per person fully spent;
  the only cost is that 200 lasts most people for months, so fewer reach the point of buying). Still open in D1: the
  last day (Claude suggests 90 days after the release) and the notice text (the draft `gift_granted`, which for 200
  says "about 20 paid conversions").
- D5: the new privacy and terms wording (A3's file references, the "within 12 hours" sentence, the gift terms) is
  **approved**. The "within 12 hours" sentence is true only once S3 below is fixed.
- C6/D8 help per command: build it **last** in 2.2.0 (low priority), before the C port. Each page: a detailed
  explanation and one simple example. Claude's default form: `/help_<command>` (tappable in a message, readable), with
  `/help <command>` also accepted and `/fullhelp` for everything; the owner may still say otherwise. Commands that get a
  page (complex, or with options):
  - ManagerBot (English only): `/pause` (with `/warn` and `/resume`: the update procedure), `/gift`, `/broadcast`
    (`--active`), `/balance` (`[id] [±n|=n]`), `/addcredit`, `/run`, `/logs` (`[bot] [n] [bot|problems]`), `/problems`
    (`[hours] [mine]`), `/events`, `/feedback` (`instant|nightly`), `/sql`, `/say`.
  - ConvertBot: `/convert` (what is free under 3 MB, the paid prices by size, text recognition), `/recharge` (the
    first-500-Stars ladder, bonus expiry), `/balance` (bonus, gift, expiry, where credit went).
  - DownloaderBot: `/settings` (captions, file quality), plus a page for sending a link (the free 40 a day up to 50 MB,
    large files cost ⚡, the buttons under a delivery, the album PDF/ZIP buttons).
  - StickerBot: `/newpack`, `/addsticker`, `/import`.
  - AnonBot: `/link` (the inbox link and its settings), `/export`, `/which`.
  - Prices and limits on every page are computed from the code that charges (as `price_tiers_text` does), never typed in.

**Done in the second session (2026-10-17, Opus with Sonnet/Haiku agents):**
- The review's S1, S2, S3 and all its minors are fixed, each with tests and mutants (15/15 caught); then a **second
  fresh Opus review** of those fixes found 0 critical, 0 should-fix, and 5 minors. Three were fixed (5/5 mutants caught):
  a second tap on a /gift card given or cancelled leaves it alone (`GIFT_DONE`), a bot that cannot be asked is named
  on the card while the rest are still asked, and the stale-card wording is exact. Accepted: any BadRequest from a
  stored-ID send drops the row even when the IDs were fine (an album's follow-up message failing, a deleted link
  message): it costs one later download.
- **The 12-hour promise made true:** a records review found the five-minute kept copy could be renewed forever by
  re-sending the link. Now `delivery_cache.MAX_LIFE_SECONDS` (12 h from keeping) caps it, with 3 mutants caught. The
  TERMS.md sentence now says the bot stops within 12 hours "once neither the platform nor the download services the
  bot asks still serve it", because a route may still serve a removed post and the bot cannot know.
- Records: the gift paragraph is in all four TERMS.md (rewritten clearer: a gift *is* bonus credit, ends at the end
  of the last day, UTC). All four PRIVACY.md now have "Gifts given": `/deletemydata` does not remove it, verified in
  code. The uz/ru `privacy_stored` file-ID bullets were corrected (one word had served for both "link" and
  "reference", and "encrypted" was wrong). Also `tests/README.md`, `.env.example`, DEPLOY.md, the downloader README,
  and the three detailed sections at the end of this file. A fresh Opus reviewer read all of today's records.
- C7 built: `/status` shows the slowest event sweep in the last 15 minutes and since start (section at the end).
- Ported to `MumuBots2.2.0C` (see the top of this handoff and `MumuBots2.2.0C\CHANGES_v2.2.0C.md`).

**Next, in this order:**
1. ~~Help per command (C6/D8)~~ -- done 2026-10-18 (section "Help per command (C6/D8)" at the end).
2. The owner: D1's last day and the gift notice text (amount 200). The draft `gift_granted` says "good until {date}";
   a reviewer suggests "good through {date} (to the end of that day, UTC)". D7 (deploy). An objection to
   `/help_<command>`, if any.

**Known, not fixed:** a refund of a job paid with gift (or top-up bonus) credit comes back as ordinary credit that never
expires (`move_stars(..., "refund")`); pre-existing for the top-up bonus. Fix before a bigger promotion: the spend
remembers how much was bonus, and the refund puts that back as bonus with its expiry.

**Fresh Opus review of the first session's patch (2026-10-17, 195k tokens, 11 min): 0 critical, 3 should-fix, 5
minor. All fixed in the second session** (above; the findings are kept here as they were written):
- **S1, money.** `manager_bot/bot.py` `gift_command`/`gift_button`: every card's button is `gift:go`, and
  `GIFT_PENDING` holds only the latest /gift, so tapping "Give it" on an older card gives the *newer* gift (another
  key or amount, to everyone, irreversibly); "Cancel" on an old card cancels the new one. Fix: a nonce in the
  callback data (`gift:go:<nonce>`, `gift:cancel:<nonce>`) kept in the pending entry; a mismatch answers "That card is
  no longer the one waiting; send /gift again." and queues nothing; the edited text names the key and the amount.
  Update `tests/test_gift.py` (it asserts `gift:go` and the pending dict) and add a mutant.
- **S2.** `sent_files.is_invalid_file_id` matches wordings only; a wording it misses (e.g. an MTProto name with
  underscores) leaves a row that fails every send of that post, for everyone, until the 90-day sweep (`last_used`
  moves only on success). Fix in `bot.py` `_resend_by_file_id`: on **any** `BadRequest` from a stored-id send, drop
  the row; re-fetch in the same job only when `is_invalid_file_id` says so (as now). Test + mutant.
- **S3, the 12-hour promise.** `bot.py:1792` stores IDs after a **disk-cache hit** with `confirmed_at = now()`, but a
  disk-cache entry can live on (every `note_link`/`take` restarts it) long after the post was deleted, so a deleted
  post can be put back and served to everyone for another 12 hours, repeatedly. Fix: do not call `_store_sent_files`
  from the disk-cache hit path (only after a fresh resolve + download, `bot.py:1861`). Flip the spec's test "a
  disk-cache hit's send stores IDs" to "does not store"; mutant.
- Minor (fix with the above): the gift ledger detail says "bonus until" the expiry moment (one day after the last
  day; write the last day); after the one-hour limit a newer A2 mark can be popped by the older job's `finally` (pop
  only if the value is still this job's timestamp); stale lookups take no slot (consider the global slot, or a small
  semaphore, for `resolvers.lookup`); the gift's debug log line names the user id (drop it). Accepted: honouring a
  route's `skip` list in `lookup`.
- Found sound: `grant_bonus_once` atomic, no double credit with `grant_stars_once`, no deadlock; dry run writes
  nothing; A2 check-and-mark has no await between; album sends all-or-nothing; Forbidden/RetryAfter/TimedOut never
  re-fetch; no user data in `sent_files` or the new logs; `canonical_link` regexes linear and host-safe; no SQL from
  names; Python 3.11 fine; large/merge/Reddit-image/card paths untouched.

## Fixed in 2.2.0 (2026-10-10): the ManagerBot sweep that "skipped: maximum number of running instances"

After the 2.1.0C release (08:00 KST) the log showed `_event_backstop` "skipped: maximum number of running instances"
every ~30.5 s (~2,800 warning lines a day), and only that job. Cause: the 1 s tick awaited the idle event sweep, which
now does two database round trips in a row (`take_unnotified_events`, then `_deliver_feedback`); when together they
outlasted a second, the scheduler skipped the next tick. Harmless, but it is the very line a real stall prints (the
2026-10-09 incident). **Fix** (`manager_bot/bot.py`, `_pump_if_due`): the tick now only decides whether a sweep is due and
starts it as a task of the application (`context.application.create_task`), then returns at once; a tick that finds the
sweep still running does nothing (never a second sweep on top), and a sweep running past `MBOT_PUMP_STALL_WARN_SECONDS`
(60) is named once in the log, numbers only, so a real stall is no longer hidden. The two pumps (results, events) are
independent. `tests/test_pump_tasks.py` (15 checks, 4 mutants caught: inline await, no running guard, stall never
logged, stall logged every tick). Not measured: the two queries' round trips from Railway (no access from here); the
sweep's cadence is unchanged, so the cost is what it was.

## DownloaderBot: true video shape and the five-minute delivery cache (2026-10-12)

**What.** (A) A video sent as a video now carries its real width, height and duration, so a 9:16 reel is no longer drawn
as a 1:1 square (the owner's friend saw exactly that; `_send_media` passed none of the three). (B) The last download is
kept five minutes for the person it was for: the button under it ("get it as a file" / "get it compressed") and the same
link sent again are answered from the kept files, without going to the site a second time. Each use restarts the five
minutes; a different link, `/deletemydata`, a restart or a full disk deletes the files sooner. The spec was
`SPEC_DL_CACHE_2.2.0.md` (Opus), built by Sonnet.

**A -- `downloader_bot/video.py`.** `probe(path)` runs `ffprobe` once (an argument list, never a shell; stdin and stderr
to nowhere; 5 s, then killed and reaped; output over 64 KB ignored) and `shape_from_ffprobe` turns its JSON into a
`VideoShape(width, height, duration)`: sizes must be ints in 1..16384; a non-square pixel (`N:D`) widens the width first;
a quarter turn (display matrix first, the old `rotate` tag only if there is none) swaps the sides; the length is whole
seconds if finite and non-negative. `_send_media` measures every file it sends *as a video* (the single `reply_video`
and each `InputMediaVideo`), never a document (`/lossless`) or a photo; after one ffprobe has timed out the rest of that
delivery is not measured (a hostile album costs one wait, not one per file). No `supports_streaming`, no thumbnail. ffprobe
is found with `shutil.which`, the way yt-dlp finds ffmpeg here (nothing in this bot configures an ffmpeg location); in the
image it is `/usr/bin/ffprobe`, from the same Debian `ffmpeg` package both Dockerfiles already install (the package's file
list on packages.debian.org names it; the Docker daemon was not running here to ask the image itself). Missing, slow or
confused: the video goes exactly as before.
Small additions to the spec, all on hostile input: the file is named `file:<path>` and `-protocol_whitelist file` is
given, because ffprobe goes by a file's contents and a downloaded "video" that is really a playlist must not make the
server fetch anything; a length over 2^31-1 s (a file can claim anything) and a rotation are read with `round()` (ffprobe
prints 89.99999 sometimes: `int()` would miss it); a widened width is held at 16384.

**B -- `downloader_bot/delivery_cache.py`** (new; no Telegram, pure, fake clock). One entry per person, keyed by
`(platform, url)` as `_resolve_and_send` gets them; files moved (`os.replace`) into `<work dir>/dbot-cache/<random>/`.
`DBOT_CACHE_SECONDS` 300 (0 = off entirely), `DBOT_CACHE_ENTRY_MAX_MB` 100, `DBOT_CACHE_MAX_MB` 400 (least recently used
out first). Decisions, all as specified: every recognised link (`handle_link`, before any refusal) and every button tap
(`redeliver_callback`, before the allowance) calls `note_link`, which drops an entry of any other link at once and
restarts the life of this one; a finished download is kept only if its link is still the person's latest (A then B pasted
while A downloads: A is delivered and deleted, B is kept); only a successful send is kept; a hit takes the person's slot,
the global slot and the restart note, looks up *before* the free-disk check, never asks a route anything, counts as a
download exactly as a fetch would (the allowance code is untouched), and remembers a new redo token for the form it sent;
a hit whose send fails tells the person and keeps the entry; an entry is kept only if, after evicting what is needed, the
disk still has `MIN_FREE_MB + MAX_CONCURRENT_DOWNLOADS x DBOT_MAX_DOWNLOAD_MB` free (444 MB by default), and the cache gives
way: kept files are deleted first whenever a download needs the space (`make_room`, see the review fixes below). A file is never deleted under a send: `take` pins, `release` unpins, and drop,
replacement, expiry and `/deletemydata` of a pinned entry only mark it dead (deleted at the last release). The cache folder
is wiped at start (after the lease is held) and swept every 15 s (`_post_init`, next to the provider-health flush). The
merge buttons do not use it (follow-up), and Reddit direct images and the rendered text cards are not kept (their redo goes
through `_resolve_and_send` and is kept from then on). The owner's `/status` (admin-only) gains one line: counts only.
Deviations from the spec's mechanism, same guarantee: eviction to make room never takes a *pinned* entry (it frees nothing
now and would only lose the person's hit); `/deletemydata` is a small wrapper in `bot.py` around the shared handler (the
shared module is the same for all five bots) which also forgets the person's latest link, so a download still in flight
is not kept when it lands, and clears `user_data["redeliver"]`.

**Records.** `i18n.py` `privacy_stored` (en/uz/ru; the old "link you send" bullet is replaced by the two new ones, the last
line changed) and `delete_data_consequences` (the same "nothing you downloaded is affected" sentence); `PRIVACY.md` (held
list, what is not held, retention table, erasing); `TERMS.md` (a paragraph, and "stores nothing" became "holds nothing but
the last download, for five minutes"); `README.md` (a row; the album row now says the kept download does not serve it);
`.env.example`; `docs/operations/DEPLOY.md` (two rows). In the C edition: `LEGAL.md` (privacy: both bullets, what is not
held, erasing; terms: a paragraph and the "stores nothing" sentence), `.env.example`, `README.md` (a paragraph). The Uzbek
and Russian texts follow the spec's wording; the new `delete_data_consequences` sentence and the shortened TERMS/LEGAL
sentence are mine, and want the owner's eye.

**"Dropped after 12 hours" -- verified, not assumed** (real python-telegram-bot, real `PostgresPersistence`, the scratch
Postgres, `shared_features._prune_user_data`): the redo links live in `user_data["redeliver"]`, which is persisted. The
hourly `_maintenance_job` drops a person idle for `USER_DATA_TTL_HOURS` (12) from memory; the persist job (every 180 s)
then deletes the database row; a restart deletes every row last written over `DEPLOY_STATE_TTL_HOURS` (12) ago without
loading it. So they are gone *after* 12 idle hours and at most about an hour and three minutes later (the prune runs
hourly), never before: the sentence "dropped after 12 hours without using the bot" is true as written; "within 12 hours"
would not be.

**Tests.** `tests/test_video_shape.py` (120 checks) and `tests/test_delivery_cache.py` (164), registered after
`test_pump_tasks.py`; full suite `python tests/run.py` with the scratch Postgres and `TESSDATA_PREFIX`: **78/78 green**
(the 76 before, plus these two). Broken on purpose 48 ways, one at a time, in a private copy of the bot in `tests/.mirror`:
47 caught, 1 equivalent (the `paths.clear()` after the cache takes the files is belt and braces: the finally would find
nothing to delete). The spec's guards each have mutants: latest-link rule, pin before delete (three ways), entry cap, disk
floor (three ways), life restart (by a hit and by the same link), the rotation swap (and truncating instead of rounding), the
timeout skip (and the timeout not being noticed). The rotated clip is made with ffmpeg's `-display_rotation 90` input
option and `-c copy`, which writes a display matrix without touching the picture.

**Not done.** The merge buttons (PDF/ZIP) still fetch again. The Docker image was not rebuilt (daemon not running); ffprobe's
place is from Debian's file list. No real Instagram/TikTok video has been through `probe` here, only ffmpeg-made clips; a
video whose container has no duration (live streams) is sent without one, as before.

**Fixes after the fresh Opus review of this work (2026-10-12).** Seven items, each with tests and a mutant the suite catches:
1. *The cache gives way.* `delivery_cache.make_room(mb, min_free_mb)` deletes kept deliveries, least recently used first
   (never one a send is reading), until the disk has `MIN_FREE_MB + mb` free or nothing deletable is left; `bot._make_room`
   calls it with `large_files.LARGE_MAX_MB` just before a large download fetches (after its refusals, so a refused person
   costs nobody their kept files) and with `MERGE_MAX_MB * 2` at the start of a merge, before the free-space check. The claim
   "the cache never makes a download refuse for space" is gone from `bot.py`, the module, `.env.example` and above: it now
   says the cache gives way.
2. *No paths in logs or messages.* The module, the sweep job and the cache-hit path log `type(exc).__name__` only, never
   `exc_info` or the text; the person is shown `download_failed` with the kind of failure (the ordinary path's `{error}` stays).
3. *Slow downloads.* `sweep` forgets a person's latest link after `LATEST_KEEP_SECONDS` (60 minutes) or when it is replaced,
   not after the cache's five minutes, so a link that queued and downloaded for longer is still kept when it lands.
4. *Shared sender ids.* 1087968824 (anonymous group admin), 136817688 (sent as a channel), 777000 (Telegram) are never cached,
   taken or noted: `SHARED_SENDER_IDS`, decided in one place (`usable`).
5. *`DBOT_CACHE_SECONDS` that cannot be read* ("off", "2.5") means 0 = off, not the default; negative is 0. (The two size
   settings keep falling back to their defaults.)
6. *Probe budget.* One delivery gets `PROBE_BUDGET_SECONDS` (10 s) of probing in all: `ProbeRun.spent` adds up, a probe may take
   at most what is left, and past it the rest are sent without probing. `_send_large` makes one `ProbeRun` and passes it to every
   `_send_media` call.
7. *Real ffprobe.* `tests/test_video_shape.py` runs 16 checks on real clips made with the machine's ffmpeg (none skipped here).
   `MumuBots2.2.0C/main.py --check` now makes a 64x36, two-frame clip with ffmpeg, measures it with `video.probe` and fails unless
   it is 64 wide and 36 high (skipped with a printed note without ffmpeg/ffprobe on PATH), so the Docker build proves the ffprobe
   arguments work on the image's ffmpeg 5.1. Not yet run inside the image (no rebuild here); it passed against ffmpeg 9.0 here.

## Button-tap counts and /buttons (2026-10-12)

**What.** The owner: "implement the measuring for any buttons that we might need info about". Every public bot now counts how
many times each button is tapped, per day for the whole bot, and ManagerBot's new `/buttons [days]` (owner only) shows the counts.
DownloaderBot also counts the numbers that decide how long the delivery cache should keep a download. Counts only: no user id, chat
id, token, link, job id, pack name, conversation id or amount is ever part of a key, a log line or a row. ManagerBot's own buttons are
not counted.

**How.** `shared/shared_features.py`: `track_activity` (a TypeHandler that sees every update before any handler) calls `_count_tap`,
which calls `note_tap(tap_key(callback_data))` (no I/O, cannot raise). `tap_key` keeps the first part of the data if it looks like a
button's name (`[a-z][a-z0-9_]{0,23}`, else `other`) and the second part only for the buttons whose second part is a choice from a
short list, and only if it matches exactly (`help`, `setlang`, `erasedata`, `donatefiat`, `donatecustom`, `caption`, `lossless`,
`large`, `merge`, `convfmt`, `fmtcat`, `aq_export`, `anonlink`, `confirmremove`; `donate` only for the Stars menu amounts).
Checked against every real callback_data of all five bots (read out of their sources by the test) and every real format target,
category and language: **no pattern had to be widened**. `note_tap(key)` is public: at most 150 distinct keys between two flushes
("other" included), the rest counted as `other`. The usage sample job swaps the dict out on the event loop (`take_taps`) and the
same thread hop writes it (`family_link.record_taps`) into `family.button_taps (bot_id, day, button, taps)`, primary key
`(bot_id, day, button)`, created with `CREATE TABLE IF NOT EXISTS` next to `usage_samples` (no ALTER, no lock on an existing table),
upsert `taps = taps + EXCLUDED.taps`, day = the UTC day (`now() AT TIME ZONE 'UTC'`). **Retention:** the usage samples' own
(`FAMILY_USAGE_RETENTION_DAYS`, 45), pruned by the same per-bot housekeeping (`_prune_family_rows` now returns four numbers).
**A failed write** follows the jobs counters' precedent: the counts were taken before the write and are not put back, one window is
lost, and the log says only the kind of failure. **A restart** loses the current window (as it does the usage row's). **C edition:**
every bot loads its own copy of `shared_features`, so `_taps` is per bot exactly as `_usage_jobs_ok` is (checked: four distinct
dicts; ManagerBot has no `shared_features` and its own `track_activity`, which does not count).

**DownloaderBot's keys** (`bot.py`, through `note_tap`): `redo.to_file` / `redo.to_compressed`, one of `redo.after.lt1m`, `1-2m`,
`2-5m`, `5-15m`, `15-60m`, `gt60m` (a bucket is [from, to)) or `redo.after.unknown` (`_remember_delivery` now stores `"at"`),
`redo.expired`, `redo.spent`, `merge.expired`; while the cache is on: `redo.cache.hit` / `redo.cache.miss` (only for a redo tap's
look-up), `cache.kept` / `cache.not_kept` (where `keep()` is called), `link.again.cached` / `link.again.not_cached` (`note_link` now
returns what it found; its behaviour is unchanged). Not counted for the shared sender ids.

**`/buttons [days]`** (`manager_bot/bot.py`): 1..90, default 7; anything else answers a usage line and reads nothing. Per bot in the
family's order (Sticker, Convert, Downloader, Anon, then unknown ids), keys by taps descending, at most 25 key lines per bot then
"+N more" (DownloaderBot's four-line fixed block is shown first and does not count against the 25). In the menu ("Button taps per
bot, counts only"), in `/help`. Taps older than the retention (45 days) are gone, so `/buttons 90` shows at most 45.

**Records.** `privacy_stored` (en/uz/ru) of the four public bots, each bot's `PRIVACY.md` (bullet and retention row),
`MumuBots2.2.0C/LEGAL.md` (bullet and row); DownloaderBot's Russian `delete_data_consequences` wording.

**Tests.** `tests/test_button_taps.py` (86 checks; `... manager` 31 more, for `/buttons`), the downloader's event keys inside
`tests/test_delivery_cache.py` (it has the harness), `tests/taps_db_scenarios.py` against the scratch Postgres (19 checks in each of
three session time zones; it is not in `run.py`, like the other database suites). Mutants in `tests/.mirror`, all caught: a second part
kept without the list, a 64-character name, no 150-key cap, ManagerBot counted, the flush clearing after the write, the swap moved
into the worker thread, a delay edge, the 25-line cap. Full suite (`python tests/run.py`, 80 suites with the two new ones): green; the one failure of the first run, `test_shared_copies`'s list of deliberate differences between ManagerBot's monitoring.py and shared_features.py, now names the three tap-related ones.

**Not done.** Nothing is written at shutdown (a redeploy loses up to one window of counts). Reddit direct images and text cards never
reach `keep()`, so they are neither `cache.kept` nor `cache.not_kept`. The database suite's SQL has no mutants (it runs against the
real tree).

## Brought in from 2.1.0 (done 2026-10-10)

`MumuBots2.2.0` was diffed against the committed `v2.1.0` tree (the git history here was re-pointed at it: `git fetch`,
then `git reset --mixed origin/main`, no file touched). `git status` now lists only the two features, their records and
tests, and the pump fix above; nothing from 2.1.0 is missing. `tests/test_redeliver.py` stays out on purpose (it tests
2.1.0's redo code, which lost the album buttons; this tree's redo is covered by `test_merge.py`). Snapshots of both 2.2.0
folders as they were before this session are kept in the session scratchpad only.
Full suite: `python tests/run.py` = 76 checks green (re-run after the review fixes; 21 Postgres suites green before them, the fixes touch no database code), with the Tesseract language data present (`TESSDATA_PREFIX` ->
`TempTools\tessdata`) so the OCR tests read real text; the Postgres suites are listed in the next section.

## Third fresh review (Opus, 2026-10-10): the parts the first two did not read

Read: OCR's rotated/cropped text layer, the scanned-page count vs the conversion, the missing-language-data path, the
describe deadline and its child, the text-language callbacks and i18n, and the whole album-button path. No defect in any
of those. Three found and **fixed**, each with a test that fails on the old code:
1. **Should-fix:** a progressive JPEG with thousands of scans held the PDF gate for minutes (libjpeg has no scan limit;
   0.26 ms a scan at 19 MP, so 666 KB of 17-byte scans = 7.9 s, 48 MB ~ 13 min, in a thread nobody can stop, with a
   download slot held by each waiter). Now `merge._count_scans` refuses more than `MAX_JPEG_SCANS` (200; a photo has ~10)
   `FF DA` markers over the whole file, in C, before Pillow opens it (`NotAPhoto`: left out of the PDF). `test_merge.py
   scan_checks`: at the cap passes, one over is refused, a 30,000-scan file in 15 ms, a marker split across the 1 MiB
   read boundary, `build_pdf` keeps the real photos; 3 mutants caught.
2. A delivery saved by 2.1.0 with `"spent": True` crashed the tap (`action in True`): `_spend_action`, `_unspend_action`
   and `_remembered_kb` now read it as "redo already spent". `legacy_state_checks`; 2 mutants caught.
3. Asking for TXT / Searchable PDF on an old menu after an album joined told the person `".ocrpdf" isn't a format I
   convert`: now `not_for_this_file` (en/uz/ru), checked in `test_ocr_wiring.py`.
Not changed, on purpose: a PDF merge takes a download slot before it waits for the PDF gate, so a long build holds one of
the three slots; with finding 1 closed a build is bounded to seconds, so it stays as designed.

## ManagerBot's menu: /reports and /nightly (2026-10-10, found by the owner)

`/reports` (the list of recent problems with their incident codes, which `/report <code>` opens one row of) and
`/nightly` (the 22:00 report under its old name) were handled but not in the Telegram menu, while `/report` was.
Cause: `/reports` had been written off as "an alias of /problems" in the menu's comment and in `tests/test_menu.py`'s alias
table, which is how that test came to agree with the gap; it is a handler of its own. Fixed in `manager_bot/bot.py`
(both added, descriptions under 50 characters) and the test (neither is an alias any more; each is checked in the menu,
`/reports` is checked to be a different handler from `/problems`, `/nightly` the same one, every alias is a handled
command; removing the line fails 3 checks). The audit of the other four bots' menus (handlers vs advertised, every
advertised one handled, translated menus uz/ru vs the list): no mismatch. Still handled and deliberately not in the
ManagerBot menu: `/me`, `/errorlog`, `/botlog`, `/problemlog`, `/finishupdates` (second names of listed commands) and
`/crashtest` (a lever, not for a stray tap); the owner can say if he wants any of them listed.

## 1. OCR (ConvertBot)

**What it is.** `convert_bot/ocr_convert.py`: the Tesseract engine inside PyMuPDF (no tesseract binary), with
only the language data added to the image (Debian bookworm `tesseract-ocr-eng/-rus/-uzb/-uzb-cyrl`, data only,
`--no-install-recommends`, ~16 MB; `TESSDATA_PREFIX=/usr/share/tesseract-ocr/5/tessdata`). Targets: picture ->
TXT / searchable PDF (`ocrpdf`, a pseudo-target whose file is `<stem>.pdf`); PDF -> TXT reads the scanned pages;
PDF with scanned pages -> searchable PDF, every page looking as before (the text layer is laid over the original
page, rotation-correct). A per-person text language (English / Russian / Uzbek Latin+Cyrillic in one pass), a row
shown only next to an OCR button, default from the person's language.

**Limits and money (the owner's pricing rule).** Measured 2026-10-08: A4 at 300 dpi 1.1-2.0 s and ~240 MB; 17-19 MB
a megapixel; worst case a 12 MP noise image 94 s. So: photos decoded at reduced size (`draft`) to <= 12 MP; a page
drawn at <= 300 dpi to stay <= 12 MP and not read below 50 dpi (posters); MuPDF's store shrunk after every page;
non-JPEG pictures refused over 64 MP; multi-page TIFF refused (an MPO phone photo is read). A page is "scanned" when
it has a picture on it and fewer than 20 characters of its own text (`is_scanned`, ONE predicate for the count and
the conversion). The scanned pages are counted in a child process (`jobs.describe_pdf`, `convert_worker`), page
count first, a 5 s deadline (`CONVERT_OCR_DESCRIBE_SECONDS`), stopped past 30. Time limit
`jobs.ocr_time_limit` = the ordinary limit of the file + 15 s a scanned page, capped (ONE function, through
`convert_utils.ocr_limit`, for the quote and `_enqueue`); price from that limit; free only under the free size and
<= 1 scanned page; an album is never OCR; the worker refuses a file with more scanned pages than were priced. More
than 30 scanned pages: no searchable PDF, TXT as the ordinary conversion, and a note says so.

**State.** Built (Sonnet, Opus specs), **fresh Opus review done** (no critical; three should-fix items: the count
held the bot up to 30 s on a 2 KB glyph-bomb PDF; blank pages made ordinary PDFs cost money; the time limit ignored
the file's size) and **all fixed** (+ album race, multi-page TIFF, 16-bit grey memory), with tests that fail on
the old code: `tests/test_ocr.py` (~100 checks), `tests/test_ocr_wiring.py` (~180), 43 + 18 mutants caught. Opus read
the money path of the fixes line by line (quote and queue take the same inputs; free stays free). `main.py --check`
of `MumuBots2.2.0C` reads a test page (TESSDATA_PREFIX set) and passed on 2026-10-10.

**Still to do for OCR:**
- ~~Prove the image on Linux~~ DONE 2026-10-10: `docker build` of `MumuBots2.2.0C` passed (apt packages, tessdata path,
  `main.py --check` with the OCR self-test) and OCR read English, Russian, Uzbek Latin and Cyrillic inside the image. Still
  worth doing: the GitHub CI runs (`check.yml`, `tests.yml`) once the owner allows a push.
- **Checked 2026-10-10, left as is:** the other describe jobs that 2.1.0 already runs in the same handler
  (`jobs.describe_image`, the document describers) may wait up to 30 s, so the 5 s OCR deadline makes this no worse than
  what is deployed; moving every describe out of the update handler is a change to shipped 2.1.0 behaviour and belongs in its
  own release with its own tests.
- **The real fix for the menu wait**: a single file's menu is drawn inside ConvertBot's update handler, and updates
  are handled one at a time, so the describe child's wait (<= 5 s) holds the whole bot for everyone. Albums already
  go through a job (`_schedule_batch`); draw the single-file menu the same way (or in a task), then the deadline can
  stay generous. Needs a look at the per-user menu state first.
- A fresh look at the OCR fix diff beyond the money path (Opus reviewed money; the rest was checked by tests).
- Minor (from the review): an ICO's embedded PNG is decoded inside `Image.open` before the 64 MP ceiling (the
  ordinary conversion path has the same gap); `/cancel` forgets the text-language choice as it does keep-file;
  the `/help` line is static (shown even on a host without the language data); describe children have no memory cap
  of their own (`WORKER_MAX_MEMORY_MB` is 0 = none).
- Records: README/PRIVACY/LICENSING are written in this folder; the public channel text is not.

## 2. The album buttons (DownloaderBot)

**What it is.** Under a delivered album: 📄 PDF (its photos, one page each, written by hand in `downloader_bot/
merge.py`, no new dependency) and 🗜 ZIP (everything, stored, not compressed). The post is fetched again on the tap
(the five-minute delivery cache of 2026-10-12 does not serve this tap yet; the merge keeps nothing). Limits `DBOT_MERGE_MAX_ITEMS` 20, `DBOT_MERGE_MAX_MB` 48, a
picture up to 20 MP; one PDF built at a time in the whole process. A tap counts as one download (a rate limit, not
money). A tap removes only its own button (design B; the owner: "do whatever you prefer"); a tap refused for lack of
downloads gives the button back.

**State.** Two fresh Opus reviews, both fixed: (1) memory/animation/ICO-in-JPEG/double taps; (2) **Pillow parses a
file's header inside `Image.open()` before any check** -- a 41 MiB JPEG of 6 million APP5 segments cost 1.32 GB and
8.9 s, a GIF of 1-byte comment blocks did not finish -- so `_check_header` now walks JPEG (to SOS), PNG and GIF (to the
end) and WEBP in constant memory with caps (1,000 parts; 50,000 IDATs; 300,000 GIF sub-blocks; a second GIF frame is
an animation); the PDF gate stays shut until the build thread really ends, even when cancelled; corrupt WEBP left out;
a side over 65,500 px refused. `tests/test_merge.py` 386 checks, 24 + 25 + 22 mutants caught.

**Still to do for the album buttons:**
- Minor (review 2): a JPEG/PNG/GIF cut short in its header is `PhotoCutShort` -> `merge_failed` with a Report (by
  design: a photo was cut); a static GIF with trailing junk after its picture is left out; up to 4,096 junk bytes
  between JPEG segments tolerated. A FetchError's English text reaches the person in the merge path (pre-existing).
- Nothing has met real Instagram/X/Reddit albums from the datacenter address.

## The C edition (`MumuBots2.2.0C`)

Regenerated on 2026-10-10 from `MumuBots2.2.0` with `scripts\bundle_c.py --base MumuBots2.2.0` after putting
`"ocr_convert"` (convert.py, after `raw_convert`) and `"merge"` (download.py, after `routewatch`) back into BUNDLES (and on 2026-10-12 `"delivery_cache"`, after `merge`):
all eight files rewritten, `--status` clean (every section the same program as base), `--lint` 0 findings, every
section compiles under 3.11, and `main.py --check` passes including "text recognition read its test page"
(TESSDATA_PREFIX set). Hand-edited, not generated (kept): Dockerfile (packages, TESSDATA_PREFIX, build self-test),
`main.py --check`, `.env.example` (CONVERT_OCR_*, DBOT_MERGE_*), README/LEGAL, `.github/workflows/check.yml`.
Compared with the committed `v2.1.0C`, the C tree differs by exactly: the two features, the pump fix, and those
hand edits. Version strings still say 2.1.0 until the owner finalises 2.2.0.

## Owner decisions on record (2026-10-09/10)
- CI throwaway-branch push: OK (2026-10-10, in session).
- Supabase Pool Size: the owner raises it later (needed before Railway overlap is set).
- Conflict alert: 3 within 2 minutes (built in 2.1.0).
- Merge keyboard: design B, "do whatever you prefer".

## DownloaderBot: the same link again (A1-A3) (2026-10-17)

**What.** Three parts of one spec (`SPEC_REPEAT_LINKS_2.2.0.md`, Opus; built by Sonnet; the build's notes are
`BUILD_NOTES_A1-A3.md`; the owner was away, so the spec made the open decisions D2-D4). (A1) One post is one link: the
trackers a share sheet adds (`?igsh=`, `?si=`, `?utm_source=`, a `#fragment`) are removed where a link enters the bot.
(A2) The same person pasting the same link in the same chat while the first job is queued or running gets a 👌 reaction
on the copy: no second job, no second share of the day's allowance, no second "Fetching...", no "busy". (A3) A post
delivered once, to anybody, is sent again by the file IDs Telegram gave, with no download, after a check that the post
is still public (at least every 12 hours). A1 changes nothing a person sees; the owner's `/status` gains one line of counts.

**A1 -- `downloader_bot/platforms.py`.** `canonical_link(platform, url)`, applied in `detect_platform` (the one place a link
enters the bot), so `handle_link`, the delivery-cache keys, the redo memory, A2 and A3 see one string per post. An allow-list
per platform: Instagram `https://www.instagram.com/<p|reel|tv>/<CODE>/` (`reels` becomes `reel`, a username is dropped);
YouTube `https://www.youtube.com/watch?v=<ID>` from `watch?v=`, `youtu.be/`, `/shorts/`, `/live/` (ID = exactly 11 characters
of `[A-Za-z0-9_-]`, otherwise the input is returned unchanged); X `https://x.com/<user>/status/<digits>`; Pinterest
`/pin/<digits>/` on `www.pinterest.com`; TikTok, Reddit, `redd.it`, `pin.it`: host and path, no query. Trailing prose
punctuation is stripped, the scheme becomes `https`, path case is kept. It never raises and is idempotent.

**A2 -- `downloader_bot/bot.py`.** `_in_flight` maps `(chat_id, user_id, platform, url)` to `time.monotonic()`. `handle_link`
checks it right after `delivery_cache.note_link`, before the maintenance refusal, the queue limit and the allowance, and marks
in the same synchronous stretch (nothing awaited between check and mark), so two copies handled at once make one job. A link
refused before a job starts removes its mark; a spawned job removes it in the `finally` of `_spawn_download` (new optional
`in_flight` argument, so redo and merge callers are unchanged). A mark older than `IN_FLIGHT_MAX_SECONDS` (3600) is ignored.
`_answer_duplicate(update)` is the whole answer: `set_reaction("👌")`, any exception swallowed, logged at debug by type name.

**A3 -- the file-ID store** (`downloader_bot/sent_files.py`, table `sent_files`, `resolvers.lookup`, `bot.py`). *Privacy.* A
row is `post_key`, `items` (JSON `[[kind, file_id], ...]`, 1 to 10, kind video or photo), `confirmed_at` (post last seen
public) and `last_used` (a day). `post_key` = HMAC-SHA256 of `STORE_VERSION|platform|canonical url|form` under
`sha256(b"dbot-sent-files|" + bot token)`: no new secret (file IDs work only for this bot); a rotated token makes old rows
unreachable and the sweep deletes them. `form` is "file" (lossless) or "media", as an ID cannot be re-sent in the other form.
No user id, chat id, name, URL, platform name or caption is stored; `/deletemydata` leaves the table alone. *Database*
(`db.py`, literal SQL): `sent_files_get/put/confirmed/used/drop`; `prune_old_data` deletes rows unused for
`DBOT_FILEID_KEEP_DAYS` days; a database error on the read path counts as "no row". *Flow:* the canonical link and A2 live in
`detect_platform` and `handle_link`, not in `_resolve_and_send`; in `_resolve_and_send` it is `_resend_by_file_id`, the
five-minute disk cache, a normal fetch. It runs after the status message and before any
slot, disk check or route, inside `lifecycle.busy`; `sent_files.usable` = store on and sender not in
`delivery_cache.SHARED_SENDER_IDS`. A fresh row (confirmed less than `DBOT_FILEID_CONFIRM_HOURS` ago) is sent by its IDs and
only `last_used` is touched, never `confirmed_at`. A stale row asks `resolvers.lookup`: "present" (sent, `confirmed_at`
renewed), "missing" (row deleted, normal fetch) or "unknown" (row kept, normal fetch: an unconfirmed post is never sent).
`lookup` walks the platform's routes in order, skips `ytdlp:*` and any route's `skip` list, never touches route health, says
"present" on the first item, "missing" only if every route asked said so, else "unknown" (Reddit: always). `_send_media`
returns the sent IDs, or None if a message lacks what its form expects; `_store_sent_files` upserts after a successful send
from a fresh fetch (1 to 10 items), and a failed write is logged by type and never touches the delivery. The large-file,
merge, Reddit-image and text-card paths never touch the store. `/status`: `_sent_files_status_line`, counts only.
*Settings* (`downloader_bot/.env.example`, `docs/operations/DEPLOY.md`): `DBOT_SENT_FILES` 1 (0, or a value that is not a
whole number, = off), `DBOT_FILEID_CONFIRM_HOURS` 12 (unreadable or under 1 = 12; the number the privacy and terms state),
`DBOT_FILEID_KEEP_DAYS` 90 (same rule). `test_delivery_cache.py` and `test_large_files.py` run with `DBOT_SENT_FILES=0`.

**Decisions in the owner's absence.** D2: unused rows go after 90 days. D3: a merged duplicate gets a 👌 reaction, not text (it
cannot spam back in a paste storm and needs no translation; `_answer_duplicate` is the one place to change). D4: a re-send by
file ID counts against the daily allowance like any download (the plan suggested not counting): `record_download` already runs
before the job, the five-minute cache's hit counts the same way, and with no download slot the 40 a day is what bounds a
paste storm; real use is about 5 downloads a week. The owner, 2026-10-17: D5, the new privacy and terms wording, is approved on the condition that "within
12 hours" is true. S3 below and the 12-hour cap on a kept delivery (further below) are what make it true, and `TERMS.md` now
says it for a post removed at the platform: once neither the platform nor the download services the bot asks still serve it, the
bot stops sending it within 12 hours.

**Fixes after the fresh Opus review of today's patch** (0 critical, 3 should-fix, 5 minor; S1 is in the next section).
*S2.* Any `BadRequest` from a send by stored file ID now drops the row; before, only the wordings `is_invalid_file_id` knows did,
so an unknown wording failed every send of that post for everyone until the 90-day sweep. The same job fetches again only when
`is_invalid_file_id` says so; any other `BadRequest` is an ordinary failure. *S3.* The disk-cache hit no longer stores file IDs:
a kept entry can outlive a deleted post, and its IDs would have been dated "seen public just now" and served for another 12
hours, repeatedly. Only a fresh fetch stores; this is what makes TERMS.md's "within 12 hours" true. *Minor.* A job that outlives
the one-hour mark limit no longer clears a newer copy's mark (`_clear_in_flight(key, stamp)` pops only its own timestamp).
Stale-row lookups take one of `MAX_CONCURRENT_LOOKUPS` = 2 places (`_lookups()`, a semaphore), so many old posts at once
cannot run the routes into their rate limits.

**Tests.** `test_canonical_links.py` (128 checks), `test_link_dedupe.py` (67), `test_sent_files.py` (291), in `tests/run.py`
after `test_delivery_cache.py`; `tests/sent_files_db_scenarios.py` (26 checks in each of 3 time zones, scratch Postgres).
Mutants: A1 15/15, A2 16/16, A3 63/63 plus 15/15 on the SQL (109/109 at the build, no survivors, no equivalents), then 15/15
for today's fixes (both sections). `run.py` was 84/84 at the handoff, before the fixes; no count after them is recorded here.

**The kept delivery's 12-hour cap.** `delivery_cache.MAX_LIFE_SECONDS` (12 hours) caps the whole life
of a kept delivery, however often it is used: an entry records `kept_at`, and `_renewed` gives it the five minutes again from now
but never past `kept_at + MAX_LIFE_SECONDS`. Reusing a copy restarts its five minutes, so without a cap a link pasted every few
minutes would have kept a copy alive indefinitely, past the 12 hours the terms state. `test_delivery_cache.py`
covers it (a link pasted every four minutes stops being served at 12 hours); 3 mutants of the cap were caught. `PRIVACY.md`,
`privacy_stored` (en/uz/ru) and the `README.md` rows now say so. *S2's cost, accepted by the review:* a `BadRequest` that is not
about the file ID itself, such as an album's follow-up message or a link message the person deleted, still drops a good row; the
price is one later download of that post.

**Records.** Written by Sonnet today, read by a fresh Opus reviewer, and its findings fixed: `i18n.py` `privacy_stored` (en/uz/ru),
`PRIVACY.md`, `TERMS.md`, `README.md` (A1-A3 rows), `.env.example`, `docs/operations/DEPLOY.md`. **Not done.** The C edition: `sent_files` in
`scripts\bundle_c.py`'s BUNDLES for `download.py` (after `delivery_cache`), the bundle, lint and 3.11 compile, and its
`.env.example`, `LEGAL.md`, `README.md` by hand; the Docker image was not rebuilt.

## The ⚡ gift (B) (2026-10-17)

**What.** One-time bonus credit for everyone the family knows, given by the owner when the family passes a milestone (the
first: 100 users). The owner types in ManagerBot `/gift <bot|all> <key> <credits> <last day YYYY-MM-DD>` (the amount he chose is
200 ⚡). Each target bot is at once queued a count (the family command `gift`, without `--go`) and reports back how many people
it knows, how many would get the gift now, the total, and the notice they would read (English here); ManagerBot's card has
**Give it** and **Cancel**, and Give it queues the same command with `--go`. Each bot then gives every person it knows who
does not yet hold the key `amount` ⚡ of bonus credit that runs out at the end of the last day (UTC), and sends the notice in
that person's language, from the bot that won the claim: someone on two bots is given it once and told once. The person reads
`gift_granted` (en/uz/ru in all four public bots; a draft): "We have passed 100 users, thank you! You get {amount} ⚡ of free
credit, good until {date}. It pays for about {conversions} paid conversions in {convert} or big-file downloads in {download}.
Your balance: /balance" -- the date as dd.mm.YYYY, conversions = amount // 10 (at least 1; `GIFT_CREDITS_PER_CONVERSION`), the
bots as tappable handles from `SIBLING_BOTS` with the plain names as the fallback. For 200 it says "about 20". `/balance` shows
the bonus and when it runs out, as for any bonus. All four `TERMS.md` say that free promotional credit is bonus credit: it has
no cash value, cannot be transferred or refunded, and expires at the end of the last day stated (UTC); it is spent first, like
other bonus credit. The four `PRIVACY.md` list "Gifts given" (the claim `stars:granted:<key>:<user>` in `family.settings`: which
gift, to which id, when) and a retention row for it: kept like the ledger, and `/deletemydata` does not remove it (checked in
the code: `erase_user` clears only the bot's own tables, and neither that claim nor `star_bonus_lots` is among them).

**`shared/family_link.py`.** `grant_bonus_once(user_id, amount, key, expires_at, detail=None)` returns the new balance, or None
if the key was already given to that person. It refuses an amount outside 1..`GIFT_MAX_CREDIT` (1000: a slipped digit is
multiplied by every account, so past this the code has to change, not the command), an expiry without a time zone, and one
already past. In one transaction: lock the wallet, insert the claim `stars:granted:<key>:<user>` in `family.settings` (the same
claim `grant_stars_once` uses, so one key never pays twice through the two; a lost claim rolls back and returns None), expire
what is due, credit the amount as a "grant" ledger row, and add a lot to `star_bonus_lots` with the expiry. It is bonus, not
ordinary credit: spent before ordinary credit and taken back when it runs out. `_gift_args` is the one parser the family
command and ManagerBot share: the key is 3-40 lowercase letters, digits or dashes (`^[a-z0-9][a-z0-9-]{2,39}$`, so no LIKE
wildcard and nothing that could be read as part of a claim's name), the amount ASCII digits 1..1000, the last day a real date from
tomorrow (UTC) to `GIFT_MAX_DAYS` = 400 days ahead, the moment it turns into the end of that day, UTC. `_cmd_gift` reads
`db.list_all_users`, keeps positive integers that are not 1087968824, 136817688 or 777000 (anonymous admins, a channel, Telegram),
without duplicates, and takes out those with a claim for the key (`_gift_already_given`). Without `--go` it grants and sends
nothing. With `--go` it calls `grant_bonus_once` per person in a thread at `BROADCAST_PER_SECOND`, tells only the winner of the
claim, counts a failed send as unreachable (the credit stays), logs and goes on past a failure by exception type only, and says
to run the same command again to finish failures. The code comment asks for `--go` once per bot at the moment of the
announcement: a later run also reaches whoever arrived in between, and a fixed moment is what stops farming with new accounts.

**`manager_bot/bot.py`.** `gift_command` checks four arguments, the target (`_targets`) and `family_link._gift_args`, queues the
count for each bot (`db.queue_command`), keeps `{bots, args, nonce}` in `user_data["gift_pending"]`, and sends the card.
`gift_button` is one synchronous stretch from the check to the pop, so two taps cannot both give.

**Decisions.** The owner: the amount is 200 ⚡ (Claude's note at the time: fine for the budget, at most about $0.30 of hardware
per person fully spent, and the only cost is that 200 lasts most people for months, so fewer reach the point of buying); D5, the
gift terms among the wording, is approved. Still the owner's (D1): the last day (Claude suggests 90 days after the release) and
the notice text. Amount, last day and key are arguments of the command, so they are decided when the owner types it.

**Fix after the fresh Opus review.** *S1, money.* Every card's buttons were `gift:go` and `gift:cancel` and only the latest `/gift`
waited, so "Give it" on an older card gave the newer gift (another key or amount, to everyone, irreversibly), and "Cancel" on an
old card cancelled the new one. Now the buttons are `gift:go:<nonce>` and `gift:cancel:<nonce>` (`secrets.token_hex(4)`), the
nonce is kept in the pending entry, and a tap whose nonce is not the waiting one's (or has none: a card from before the fix)
edits that card to "That card is no longer the one waiting: a newer /gift replaced it. Nothing was given. Use the newest card,
or send /gift again." and leaves the newer gift waiting; a tap with nothing waiting says "That gift is no longer waiting. Send
/gift again." The cards that do act name the amount and the key ("Cancelled -- nothing was given (200 ⚡, key ...)", "Giving
200 ⚡ (key ...), through ..."). *Minor.* The ledger line says "bonus through <last day>" (it was the moment after it, one day
later); the debug line for an undelivered notice names the key, not the user id.

**Fixes after a second fresh review.** `gift_button` remembers the card it just gave or cancelled (`GIFT_DONE`, the nonce, in
`user_data`), so a double tap on the same card no longer overwrites "Giving ..." with another text. A bot that cannot be asked
(its `--go` command could not be queued when Give it is tapped) is named on the card and the rest are still asked. The stale-card text now reads "That card
is not the one waiting (a newer /gift replaced it, or the card is from before an update). Nothing was given. Use the newest
card, or send /gift again." 5 mutants of these fixes were caught.

**Tests.** `tests/credit_scenarios.py` 164 checks against a real Postgres (the claim shared with `grant_stars_once`, six threads
racing on one person credit once, another process holding the claim, out-of-bounds amounts refused with nothing written, a
failure half way leaving nothing, the ledger line); `tests/test_gift.py` 295 checks, no database (`_gift_args` at its edges with
the clock frozen at 2026-10-17 12:30 UTC, `_cmd_gift` on a fake database and Telegram, `gift_notice`, the three languages in
all four bots, ManagerBot's command and buttons including stale cards), registered in `tests/run.py`. Mutants: 42/42 at the build
and 15/15 for today's fixes, shared with the previous section, then 5/5 for the second review's fixes.

**Known, not fixed.** A refund of a job paid with gift (or top-up bonus) credit comes back as ordinary credit that never expires
(`move_stars(..., "refund")`); pre-existing for the top-up bonus. Fix before a bigger promotion: the spend remembers how much was
bonus, and the refund puts that back as bonus with its expiry. **Not done.** The Uzbek and Russian notices are drafts; nothing is
deployed and the C edition is not ported. (The gift paragraph in all four `TERMS.md` is done.)

## ManagerBot /status: how long the event sweep takes (C7) (2026-10-17)

**What.** The owner's `/status` board gains one line about the event sweep (the 1 s tick's task from `test_pump_tasks.py`'s fix of 2026-10-10), so that after the deploy it is plain whether a sweep ever outlasts the tick: `⏱ Event sweep: slowest X s in the last 15 min (n sweeps) · Y s since start`; or, when none finished in the window, `⏱ Event sweep: none in the last 15 min · slowest since start Y s`; or, before any has finished, `⏱ Event sweep: none finished yet since start`. It is kept in memory only (nothing is written to the database, and a restart starts it afresh). Where: `manager_bot/bot.py`. `_note_sweep` is the done callback of each sweep task and records how long it took (`SWEEP_WINDOW_SECONDS` = 900); `_sweep_line` builds the text; `_build_board(sweep_line)` takes it as an argument, and it is computed on the event loop by the callers, because the board itself is built in a thread and the recorded sweeps are changed by the loop.

**Tests.** `tests/test_pump_tasks.py` went from 15 to 38 checks (the new ones check the sweep-timing line of `/status`); 15 mutants were caught.

**What to look for after the deploy.** The "maximum number of running instances" line (about 2,800 a day before the sweep fix) should be gone from the log. In `/status`, a slowest sweep near or over 1 s in every window means the database round trip is the cost; a single such value among low ones means a one-off.

## Help per command (C6/D8) (2026-10-18)

**What.** Every command on the owner's list (2026-10-17 evening) has a page of its own: a detailed explanation and
one example, with every price and limit read from the code that charges or limits it at the moment the page is shown.
The spec, with every text (Opus): `C:\Bitura\.Projects\MumuBots\SPEC_HELP_PAGES_2.2.0.md` (a few texts were corrected
in the code afterwards; the code is what ships).
- Public bots (en/uz/ru): ConvertBot `/help_convert`, `/help_recharge`, `/help_balance` (aliases `/help_donate`,
  `/help_mystars`); DownloaderBot `/help_download` (sending a link: the free allowance and what counts against it,
  large files, the buttons under a delivery) and `/help_settings`; StickerBot `/help_newpack`, `/help_addsticker`,
  `/help_import`; AnonBot `/help_link`, `/help_export`, `/help_which`.
- `/help <name>` gives the same page (any case, with or without the slash or "help_"); `/fullhelp` is the full guide
  in one tap (`/help full` stays); the short and the full help end with "In detail: /help_..."; each page ends with
  the other pages and `/fullhelp`. Pages are plain text with link previews off. No help command is in any menu.
- ManagerBot (English, owner-only like every ManagerBot command): `/help_pause` (also `/help_warn`, `/help_resume`,
  `/help_finishupdates`), `/help_gift`, `/help_broadcast`, `/help_balance`, `/help_addcredit`, `/help_run`,
  `/help_logs`, `/help_problems` (also `/help_nightly`), `/help_events`, `/help_feedback`, `/help_sql`, `/help_say`;
  `/help` ends with the "In detail:" line.

**Where.** `shared/shared_features.py`, section "A page for each command" (`help_pages_line`, `help_page_name`,
`help_page_text`, `help_page_command`, `full_help_command`; also `BALANCE_RECENT_ROWS`); each public bot's `bot.py`
(`HELP_PAGES`, `HELP_ALIASES`, the page functions, registration right after `/help`) and `i18n.py` (`helppage_*`,
`help_pages_line`, `help_page_others`, `help_page_everything`); ManagerBot's pages are in `manager_bot/bot.py`.
READMEs: a row each for `/fullhelp` and the pages.

**Fixed on the way, so the pages are true:**
- ConvertBot's price list printed "0–3 MB:" and "over N MB:" in English in every language: now the keys
  `price_free_upto` and `price_over` (English unchanged, byte for byte).
- ManagerBot: HELP said `/problems [hours] [mine]`, but `mine` belongs to `/reports`, which now has its own line;
  `/logs`' usage is `<bot> [lines] [bot|problems]`; `/broadcast`'s usage and HELP name `[--active]`, and its
  confirmation card says "to everyone active in the last N day(s)" for `--active` (the queued text is unchanged; a
  bare `--active` with no words is refused); `/pause`'s reply and the resume usage say `/resume`, not `/finishupdates`.
- Limits that were literals are constants with the same values (no behaviour change): `family_link.LOG_TAIL_DEFAULT`,
  `LOG_TAIL_MAX`, `GIFT_KEY_RULE`; ManagerBot `BALANCE_OVERVIEW_ROWS`, `BALANCE_HISTORY_ROWS`, `PROBLEMS_MAX_HOURS`,
  `EVENTS_DEFAULT`, `EVENTS_MAX`, `FEEDBACK_LIST_ROWS`, `FEEDBACK_SEARCH_ROWS`; `manager_bot/db.py`
  `SQL_TIMEOUT_SECONDS`, `SQL_MAX_ROWS`; StickerBot `PACK_TITLE_MAX`.

**Tests.** `tests/help_scenarios.py` drives all four bots end to end: every page by both forms, the aliases,
`/fullhelp`, the links line, all three languages, at most 4096 UTF-16 units, no unfilled placeholder, and every
computed value changed at runtime showing on its page (StickerBot 90, ConvertBot 109, DownloaderBot 74, AnonBot 87
checks). `tests/test_menu.py` has `check_help_pages` and `/fullhelp` in UNADVERTISED (so every full guide must name
it). New `tests/test_manager_help.py`, 491 checks, registered in `run.py`. `test_reactions_feedback.py` and
`test_db_timeouts.py` follow the new strings. Over 90 mutants across the two builders and the review, all caught.
Full suite 85/85.

**Review.** A fresh Opus rendered every page in three languages and checked them against the code: 0 critical.
Fixed: a check in help_scenarios left red by a late text fix; a fractional `FAMILY_TOPUP_MULTIPLIER` shown rounded on
`/help_recharge` (it now prints the multiplier itself, and a new check compares the first step with `quote_credit`;
mutant caught); one English sentence about large-file prices; one Russian agreement. Text fixes found by the
builders against the code: only an album shares one ConvertBot menu (not "pictures in a row"); the uz/ru caption is
"@bot orqali" / "через @bot"; a /gift count arrives as each bot's own message, not on the card; a long /problems,
/events or /sql answer comes as its start plus the file.

**Known, accepted:**
- DownloaderBot's page says "It's free." when only one of its two download limits is set to 0 by env (both are on by
  default).
- `/balance`'s "1 ⭐ buys N ⚡" line rounds a fractional `FAMILY_TOPUP_MULTIPLIER` (pre-existing; default is 2).
- `/help_pause` states ManagerBot's own `lifecycle.DEFAULT_MAINTENANCE_MINUTES`; each bot uses its own default (the
  same value in the C edition, one process).
- Pages quote "✅ Convert", the start of the paid button "✅ Convert · N ⚡".

**Replaced the same day** by buttons under /help (next section): the owner asked for help to "work like the ping
command works". The `/help_<command>` commands and the "In detail" / "Other pages" lines are gone.

## Help pages behind buttons, like /ping (2026-10-18, second session)

**What.** The owner: "For the detailed help, make it work like the ping command works." `/help` (and `/fullhelp`,
`/start` where it shows the help keyboard, both directions of the full/short toggle) now carries one button per help
page, two to a row, above the "📖 Full guide" / "↩️ Short version" button: the items first, "everything" last, as
under ManagerBot's `/ping`. A tap sends the page as a **new message** (plain text, no keyboard; the help message is
never edited). `/help <command>` still gives the page directly. The `/help_<command>` commands and the "In detail" and
"Other pages" lines are removed; a page ends with "The full guide: /fullhelp". Labels are `/<command>`, except
DownloaderBot's "🔗 Sending a link" (`help_btn_download`). A forged or outdated `helpdet:<name>` answers
`help_page_gone` as an alert and sends nothing. ManagerBot: the same under `/help` and `/start`, guarded like every
ManagerBot callback; its pages end "Everything: /help". `/buttons` counts each page (`_TAP_SECOND["helpdet"]`).
Spec: `C:\Bitura\.Projects\MumuBots\SPEC_HELP_BUTTONS_2.2.0.md`. Built by Sonnet; 5/5 mutants caught.

**Where.** `shared/shared_features.py` (`HELP_PAGE_PREFIX`, `HELP_PAGE_PATTERN`, `help_page_buttons`,
`help_page_button`; `help_keyboard`, `help_toggle` and `full_help_command` take `pages`/`labels`); each public bot's
`bot.py`, `i18n.py` and README; `manager_bot/bot.py`.

**Tests.** `help_scenarios.py` (StickerBot 220, ConvertBot 245, DownloaderBot 185, AnonBot 214 checks),
`test_menu.py` 235, `test_manager_help.py` 597 (13 pages), `test_button_taps.py` 87; `anon_scenarios.py` 305 (two
checks updated for the page buttons).

## The 220 ⚡ gift: a unique-user counter, /promo, a claim by writing, AnonBot's ad (2026-10-18, second session)

**The owner's decisions (2026-10-18).** "Each person claims their gift automatically with the first message they send
to convert/downloader bot." AnonBot shows an ad to anyone who writes to it, with a button for each bot and a small
note that writing to either claims it. Then, after Claude measured 135 unique people in production (so "passed 200
users" would have been false): "Add a unique user counter that includes unique users from all bots. Make the credit
amount 220, when the UUC reaches 220, i should get a notification about this. Make this one gift not general, so it
dies after first usage. After i get the notification from UUC, i want one short command that will send the proper
message like planned before." "2 weeks to claim, 4 weeks to use." The ad: "once per person".

**Claude's readings of those (overturn freely; each is one constant or one line):**
- "4 weeks to use" is per claim: a gift claimed on day D can be spent until the end of D + 28 (UTC).
- "send the proper message" means the command opens the promotion: AnonBot then shows the ad to each person on their
  next message, and the claims open. Nothing is pushed to everybody at once (that would be `/broadcast`).
- `/promo` refuses to start below 220 (live count) unless the milestone was already reached once, so the ad's "passed
  220" is always true.
- The counter counts the bots ManagerBot manages (not ManagerBot itself), from tables that already exist; nothing new
  is stored about people for it. It can under-count (activity rows are pruned after 90 days), never over-count.
- The general `/gift` tool stays as it was; this gift does not use it.

**The flow.** (1) ManagerBot counts unique people every 15 minutes; the first time the count is 220 or more it
messages the owner once ("... start it with /promo"). (2) `/promo` shows a card with the ad as people will read it;
"▶️ Start the gift" starts it, **once ever** (a row in `family.settings` written with `ON CONFLICT DO NOTHING`;
`start_promo`). (3) From the start to the end of the 14th day after it (midnight UTC), the first **message** somebody
sends to ConvertBot or DownloaderBot gives them 220 ⚡ of bonus credit, once across the family (`claim_promo_gift` ->
`grant_bonus_once`: the claim and the credit in one transaction), and a notice (`gift_claimed`, en/uz/ru). (4) AnonBot
shows its ad (`gift_ad`, HTML, buttons to DownloaderBot and ConvertBot) once to each person who writes to it, after
the bot's own answer: never on an inbox link's `/start` (that person is about to write their message), never while a
prompt waits, never on the message the reactions note answered, never before the person has chosen a language, never
to someone who already has the gift. (5) `/promo` shows started, closes, claimed by and ad shown to; `/promo stop`
ends the claiming early (credit already given stays).

**Fail-closed rules.** Every promo read and write goes to the database itself, never `get_setting`'s in-process
fallback (a process whose family bus did not come up would otherwise "start" the gift in its own memory). A claim and
every ManagerBot read are `fresh` and raise on a database error; only the cached check a bot makes per message answers
"closed" on a failure, and tries again a minute later. A claim never trusts a cached "open": it re-reads the start and
the stop first. Ids that are not people never get it.

**Where.** `shared/family_link.py` section "The 220 ⚡ gift" (Opus): `PROMO_KEY` `gift-220-users`, `PROMO_USERS` 220,
`PROMO_CREDITS` 220, `PROMO_CLAIM_DAYS` 14, `PROMO_SPEND_DAYS` 28, `promo_window`, `promo_open`, `promo_closes_for`,
`promo_expiry`, `claim_promo_gift`, `promo_given_to`, `promo_claim_count`, `start_promo`, `stop_promo`,
`note_promo_milestone`, `promo_milestone_at`, `promo_notice`, `promo_ad`, and the `_PLAIN` fallbacks.
`shared/shared_features.py` `attach_promo_claim` (Opus), registered in ConvertBot and DownloaderBot in group -2 (after
the flood gate); the claim and the notice run as a task, so the bot's answer is never held up. AnonBot (Sonnet):
`promo_ad_note` in group 2, table `promo_seen(user_id, promo, seen_at)`, erased by `/deletemydata`. ManagerBot
(Sonnet): `db.unique_people`, `db.promo_seen_count`, the 15-minute job, `/users`' new line, `/promo` and its buttons
(nonce and done-marker as `/gift`'s), the `promo` help page; `notify_owner` now returns whether anyone got it. Spec:
`C:\Bitura\.Projects\MumuBots\SPEC_PROMO_220_2.2.0.md`. Records: AnonBot PRIVACY.md ("Announcements you were shown",
kept until /deletemydata); the READMEs of the four bots. TERMS.md unchanged (its gift paragraph covers this gift).
Version: `FAMILY_VERSION` default is now 2.2.0.

**Tests.** `test_promo.py` 82 (no database), `credit_scenarios.py` 190 (real Postgres; four starts at once start it
once, six claims at once credit once), `promo_claim_scenarios.py` 50 per bot, `promo_ad_scenarios.py` 95,
`promo_counter_scenarios.py` 21 (real Postgres), `test_promo_manager.py` 147, `erase_scenarios.py` (promo_seen).
Mutants: 11/11 on the family_link core, 8/8 on the builder's parts, 1/1 on the language rule.

**Found on the way, not fixed (pre-existing):** an edited text message makes ConvertBot's and DownloaderBot's
`unrecognized_message` fail on `update.message` being None (the error handler catches it). AnonBot's `db_merge.py`
does not know `promo_seen` (only matters for a database merge).
