# `bot.py`

[↑ downloader_bot](README.md) · [code](bot.py)

Media downloader bot -- 4th bot in the family (see ARCHITECTURE.md).

```text
Commands:
  /start, /help   - greeting + how it works
  /caption on|off - toggle the "via @thisbot" credit caption
  /lossless on|off- send downloads as uncompressed files
  /donate         - support hosting costs (voluntary)
  /en, /uz, /rus  - switch language (English/Uzbek/Russian); also asked
                    once, trilingually, on first /start
  /providers      - operator-only: which download route is currently working
```

Paste a link at any time (no command needed). Instagram, TikTok, Twitter/X,
Pinterest and Reddit all go through resolvers.py, which tries several
independent ways of getting the media and remembers which ones work -- read
that file's docstring first, because the reason it exists (a datacenter IP
gets a login page where a phone gets the post) is the single most important
thing to know about this bot.

Reddit and Twitter/X keep one thing resolvers.py does not do: a post with no
media in it is rendered as a clean card image (cards.py) instead of a wall of
text, which is the actual point of "downloading" one of those.

One video site is deliberately not mentioned in /help, /start, or the bot's command
menu (BOT_COMMANDS below) -- its links are handled if pasted but never advertised, and
its name is not written out anywhere in the public tree (platforms.UNADVERTISED). It
is also the site most likely to be refusing this server on any given day; advertising
it would be promising something the bot cannot keep. It is not to be added to the help
text without the operator's decision.

```text
Requires: python-telegram-bot[job-queue]>=21.3, yt-dlp>=2024.1, httpx,
          Pillow, python-dotenv>=1.0
Env vars: DBOT_TOKEN, DBOT_USERNAME (no @), DBOT_ADMIN_ID (optional,
          comma-separated, gates /messageas, /dbdump, /status, /providers),
          DATABASE_URL and DB_SCHEMA (the shared family database, and this
          bot's schema in it), SIBLING_BOTS. Download limits, concurrency and
          the per-site cookie/proxy settings are documented in .env.example.
```

_At `try:`:_

main.py gives each bot its settings; this only reads a .env if one is present.

_At `NO_PREVIEW = {"link_preview_options": LinkPreviewOptions(is_disabled=True)}`:_

For any message whose text is an error. yt-dlp's failures quote their own
documentation URLs, and Telegram turns the first link in a message into a
preview card -- so a one-line "couldn't download that" arrived as a
full-width GitHub repository with a logo, which looks far more like the bot
breaking than like the site saying no.

_At `BOT_USERNAME = (os.environ.get("DBOT_USERNAME") or "").strip().lstrip("@") or None`:_

Without the @, which is also taken off if it was written with one -- the
one-process edition always allowed it, and a link built from "@name" is broken.

- `BOT_NAME = "downloaderbot"` — this bot's id within SIBLING_BOTS

_At `sent_files.configure(BOT_TOKEN)`:_

The key the stored file ids are filed under comes from the token (sent_files.py).

_At `ADMIN_IDS = {int(x) for x in os.environ.get("DBOT_ADMIN_ID", "").split(",") if x.strip()}`:_

Operator-only admin tools (/messageas, /dbdump, /status) -- comma-separated
Telegram user ids. Empty/unset means disabled for everyone.

### `_deny()`

How an operator-only command answers everybody else: exactly the way a
misspelling does.

The intent was always to avoid confirming the command exists, and a bare
`return` looked like the way to do that. It is not: a bot that answers
every command it has and ignores precisely one has just pointed at the
interesting one, and to the person who typed it the bot simply looks
broken. Giving back the same sentence a typo gets makes the operator-only commands indistinguishable from commands that were never there.

_At `POLL_TIMEOUT = int(os.environ.get("POLL_TIMEOUT", "30"))`:_

Telegram's long-poll window -- see the note in main().

## How many downloads may be in flight at once

This is the bot in the family with real resource spikes: yt-dlp holding a
video, ffmpeg muxing it, and the finished file being uploaded back, all at
once. There was no limit at all, so ten people pasting links in the same
minute meant ten simultaneous downloads -- and on a container sized for
one, that is the OOM kill that ARCHITECTURE.md gives as the reason this bot
runs in a container of its own.

Three at a time -- two at most for any one person, see MAX_PER_USER below --
keeps the box responsive and costs a busy user a few seconds of queueing, which the "Downloading..." message already accounts for. The
semaphore is created lazily because it must belong to the running loop.

Three is for a small container. On a larger one (2.2.0) it follows the machine: about
400 MB of the bot's share of the memory for each (yt-dlp holding a video, ffmpeg muxing
it), and two for every vCPU, from three up to eight. DBOT_MAX_CONCURRENT_DOWNLOADS says
otherwise; with the memory unknown (a laptop) it is three.

### `log_capacity()`

Said once, when the bot starts.

_At `MAX_CONCURRENT_LOOKUPS = 2`:_

The "is it still public?" question for a stored file id (resolvers.lookup, in
\_resend_by_file_id) downloads nothing and takes no download slot, but it asks
the same routes the downloads use: a few at a time, so that many old posts sent
at once cannot run those routes into their rate limits.

_At `MAX_PER_USER = max(1, int(os.environ.get("DBOT_MAX_PER_USER") or 2))`:_

...and no more than MAX_PER_USER of them to any one person.

This was one each, behind a Lock, and it was never the thing that made
somebody wait. Every bot in the family has python-telegram-bot process
updates one at a time, and handle_link ran the whole download before it
returned -- so a second link was not even read until the first had been
fetched, muxed and uploaded, and nor was anybody else's. The per-person
lock only ever guarded a queue that could not form.

Each link is now its own task (see \_spawn_download), so the handler returns
at once and the limits below are what actually decide. Three slots for the
box, two for any one person: somebody pasting a batch gets two moving at
once, and the third slot is always there for the next person to arrive.

_At `MAX_PENDING_PER_USER = max(1, int(os.environ.get("DBOT_MAX_PENDING_PER_USER") or 5))`:_

Links one person may have waiting or in flight before a new one is refused.
Past that it is a backlog rather than a queue, and each waiting link is a
status message sitting in their chat saying nothing is happening.

_At `MIN_FREE_MB = int(os.environ.get("DBOT_MIN_FREE_MB") or 300)`:_

The temporary storage limit is the product of two numbers already here:
every download holds at most DBOT_MAX_DOWNLOAD_MB on disk (video.py's
ceiling, 48 MB by default), and only MAX_CONCURRENT_DOWNLOADS hold one at a
time -- a link waiting for a slot has written nothing. So the work directory
never holds more than about 3 x 48 = 144 MB, however many links arrive.
What that cannot know is whether the disk has that much left, so a
download also refuses to start with less than MIN_FREE_MB free.

_At `delivery_cache.configure(resolvers._WORK_DIR)`:_

The last deliveries are kept a while (delivery_cache.py). They live in the
work directory too, so they are kept only while the disk still has what the
downloads themselves need. And they give way: a download that needs more than
that (a large file, a merge) first deletes kept files, least recently used first
(\_make_room), so a kept delivery does not make a download refuse for space.

### `_make_room()`

A download is about to take up to `mb` MB: kept files go first, until the
disk has that and MIN_FREE_MB more, or none are left.

_At `_in_flight: "dict[tuple[int, int, str, str], float]" = {}`:_

The same link again while the first is still going (handle_link): the links
a person has queued or running, as (chat id, person id, platform, canonical
link) -> when it was marked (time.monotonic). The copy gets a 👌 and no job:
no allowance check, no record, no status message. A mark is made when the
link is accepted and cleared when its job ends (\_spawn_download's finally) or
when the link is refused before a job starts, so the dict never holds more
than the jobs in flight. IN_FLIGHT_MAX_SECONDS is the belt and braces: a mark
older than that is ignored and overwritten, in case one was somehow never
cleared, so that a link can never be stuck for good.

### `_clear_in_flight()`

Take a link's mark down, only if it is still the one this job made: a job
that ran past IN_FLIGHT_MAX_SECONDS may find its mark replaced by a newer copy
of the link, and that mark is the newer job's to clear.

### `_answer_duplicate()`

The one answer to a link that is already being fetched: a 👌 on the copy.
A reaction rather than a line of text because it cannot spam back during a
paste storm and needs no translation (the first link's own status message is
already on screen). Switching to text later is a change to this function
only. Never raises: a reaction that cannot be set (an old message, a chat
that does not allow it) is nothing the person needs to hear about.

### `_user_slot()`

- At `if slot._value >= MAX_PER_USER and not slot._waiters:  # noqa: SLF001 - no public API`:
  Dropped as soon as it is idle: one dict entry per person who has
  ever pasted a link is a slow leak on a process that runs for months.

### `_spawn_download()`

Run one link's whole download in a task of its own.

`work` is a coroutine function. The update travels with the task so that
anything escaping it reaches the error handler attributed to the person
it happened to -- which, since the handler stopped filing network errors
with an update as blips, means it is reported rather than swallowed.

`in_flight` is the mark handle_link made for this link (see \_in_flight),
cleared in the same finally that lowers the pending count: whatever the
job did, the next copy of the link is a job of its own. The redo and merge
buttons pass none.

### `build_help_text()`

The short version unless asked for the long one -- the family's help
template (shared_features, "/help in two lengths"). Both end with the
sibling-bots pointer; the per-command pages (HELP_PAGES below) are the
buttons under the help, not a line in it.

_At `def _help_large_prices(lang: str, key: str) -> str:`:_

---------- /help &lt;name> and the buttons under /help: a page for each part with options ----------
The mechanism is in shared_features ("A page for each command"). Every
number on a page is read from the module that owns it when the page is shown
-- by attribute, never copied -- so a page says what the code does now.
"download" is not a command: it is the page for sending a link.

### `_help_page_download()`

- At `if db.DOWNLOADS_PER_DAY > 0 and db.DOWNLOADS_PER_HOUR > 0:`:
  Looked at when the page is shown: large_files.CONFIGURED can turn off at runtime.
- At `quota = i18n.t(lang, "helppage_download_quota",`:
  The same arithmetic as db.download_allowance.

_At `HELP_LABELS = {"download": "help_btn_download"}`:_

"download" is not a command, so its button says what the page is about
rather than "/download"; "settings" is one, and keeps its name.

_At `BOT_COMMANDS = [`:_

Public command menu (the "/" button in Telegram's chat UI) -- set on
startup via set_my_commands() below instead of pasting into @BotFather by
hand. The operator-only /dbdump is deliberately left off. Language-switch
commands are described in the language they switch to (self-explanatory
by script/language, since Telegram's command menu itself isn't per-user).

_At `BotCommand("start", "What this bot does, and how to start"),`:_

Order is deliberate: what this is, how to change language, what the bot
does, how to stop it, help, money, then the policies. /language sits
second on purpose -- somebody reading a menu in the
wrong language needs the way out before anything else.

/en, /uz and /rus are NOT here and still work. They are the way back for
somebody who cannot read the menu at all, so they must never stop
working -- but three lines for what /language already does is three
lines of noise for everybody who can read it.

/caption and /lossless are gone from here and still work. Both are one
switch each, and "lossless" is a word for people who already know what
it means; /settings says what it is in a word everybody knows and shows
both switches with their current state on them.

_At `ADMIN_COMMANDS = [`:_

The same menu, in the operator's own chat, with the commands only the operator can run. Kept out of BOT_COMMANDS on purpose -- a stranger should not be offered
/dbdump -- but hidden from the operator too, which was the accident. See
publish_commands() in shared_features.py. English, like the rest of the
admin output: the only person who sees this list wrote the bot.

### `_reply()`

Works whether the update came from a command or a button tap. A tap
evolves the tapped message in place -- so choosing a language turns the
picker itself into the instructions rather than leaving a stale picker
sitting above them -- while a command has no earlier bot message to reuse
and so always sends fresh.

"In place" only holds while that message is still the last thing in the
chat; once the user has said anything since, the answer is sent fresh
instead of being written somewhere they have scrolled past. See
live_message.py.

### `start()`

/start prints the instructions -- except the very first one, which
asks for a language first.

v0.7.0 made every /start the language picker, which put a three-button
detour in front of the one command every Telegram user types by reflex,
for the sake of a choice that is made once. The 0.6.0 shape is back, and
the picker has moved to a command of its own:

```text
* No language on record -- a brand-new user -- and /start does exactly
  what /language does: greet in all three languages and ask. Picking
  one prints the instructions (see _apply_language), so the first
  /start still ends where every later one begins. This is the only
  time /start asks.
* A language on record, and /start prints the instructions in it.
  Anyone who wants the picker back asks for it by name: /language.
```

### `language_command()`

/language -- the picker on demand, which is what /start used to be.

Same trilingual greeting and same three buttons, with a tick on the
language in force so a returning user can see which one they are on
before deciding to change it. The tap that follows runs through
\_apply_language exactly as /en, /uz and /rus do, so it ends where they
end: at the instructions, in the language just chosen.

- At `if lang:`:
  Keep the cached language warm even though the picker itself is
  trilingual -- the next handler this user hits would otherwise pay for
  a database read that /language had already done.

### `_cancel_items()`

Everything this bot could be waiting on -- which is only ever the
shared donation prompt. Spelled out anyway, and with the same signature
the other three bots use, so that the day this bot grows a state of its
own there is somewhere obvious for it to go.

### `cancel_command()`

This bot asks the fewest questions in the family -- a link needs no
follow-up, and /caption and /lossless answer themselves with buttons --
so the only thing /cancel usually has to catch is a donation amount it
asked for. It exists anyway, because "does /cancel work here?" should not
depend on which of the five bots is open, and
because a forced reply left over from /donate is exactly the kind of
thing that needs a way out. Downloads already in flight are not
affected: those are the bot working, not the bot waiting on a reply.

With one thing pending the question below is a formality here, and it is
asked anyway: /cancel means the same thing in all four bots, and "it
confirms except in DownloaderBot, where it just does it" is precisely
the kind of per-bot exception this family keeps trying to stop growing.

### `cancel_choice_callback()`

A tap on one of /cancel's buttons.

### `_apply_language()`

The single path every language change goes through -- /en, /uz, /rus
and a tap on the picker alike -- so all four end the same way: with the
instructions, printed in the language just chosen.

- At `await refresh_chat_menu(context, update.effective_user.id, lang)`:
  The menu follows the choice too -- Telegram otherwise shows it in the
  language of the phone.

### `language_chosen()`

A tap on the picker -- same effect as /en, /uz or /rus.

### `caption_toggle()`

- At `lang = await i18n.get_lang(update.effective_user.id, context)`:
  /caption on|off still works directly for muscle memory, but bare
  /caption now offers buttons instead of just stating the syntax.

### `lossless_toggle()`

/lossless [on|off] -- send downloads as files instead of as media.

Telegram re-encodes anything a bot sends as a photo or a video: that
compression is what makes it play or preview inline in the chat, and
there is no way to ask for one without the other. Sent as a \*document\*
the bytes arrive exactly as they left the source -- the original
resolution, bitrate and container -- at the cost of a tap to open, and
no preview in the chat list.

So this is off by default and per-user, the same shape as /caption:
almost everyone wants the version that plays, and the people who do not
want it badly enough to say so. The bot's upload ceiling is unchanged
either way (20 MB, or 50 MB behind a local Bot API server), and a file
too big is too big in either form.

### `large_toggle_callback()`

The large-files switch, from /settings, or from the button under a
refusal for a file over the free ceiling (`large:offer`). Off by default:
these cost credit, and credit is never spent without a yes.

### `settings_command()`

Both switches this bot has, on one screen, with their state on them.

/caption and /lossless still work and still do exactly this one at a
time. What they were not was findable: a menu entry saying "lossless" is
a word for somebody who already knows what it means, and the two of them
were 2 of the 15 lines in a menu whose other 13 are about the bot or the
law. One entry called settings is what a person looks for when they want
to change something.

- At `if large_files.CONFIGURED:`:
  The third switch only where it can do anything: with the MTProto route
  set up (large_files.py).

### `dbdump_command()`

/dbdump -- exports every table in this bot's own database as one
zip of CSVs. Operator-only.

### `messageas_command()`

/messageas &lt;user_id> &lt;text> -- sends a message to that user as this
bot. Only works if the user has messaged the bot before (Telegram
doesn't let bots cold-message anyone). Operator-only for obvious reasons.

### `status_command()`

/status -- uptime, hosting environment, any crashes since this
process started, and active-user counts. Operator-only, same reasoning as
/dbdump: this is operational info, not something every user should see.

### `_cache_status_line()`

One line for the operator: counts only, never whose or what.

### `_sent_files_status_line()`

The file-id store (sent_files.py), for the operator: counts since the bot started,
never whose or what.

_At `PROVIDER_HEALTH_FLUSH_SECONDS = int(`:_

How often the in-memory provider scores are written to Postgres. A minute
is short enough that a crash loses nothing that matters and long enough that
a busy hour is sixty writes rather than a write per download. See
resolvers.take_dirty().

### `_flush_provider_health()`

- At `logger.warning("couldn't save provider health", exc_info=True)`:
  Scores are advice, not user data. If the database is unreachable the
  bot carries on choosing providers from memory rather than failing a
  download over a bookkeeping write.

_At `PROBE_TIMEOUT_S = float(os.environ.get("DBOT_PROBE_TIMEOUT", "12"))`:_

How long one route gets during a bus probe. Shorter than a real download's
PROVIDER_TIMEOUT, because ManagerBot calls a family command failed after 90
seconds and a probe nobody can run from their phone is not worth having.

### `_bus_probe()`

`probe [url|platform ...]` over the family bus.

THE POINT OF THIS COMMAND. Every route in resolvers.py was verified from
the developer's laptop, on a residential connection -- and the entire
reason those routes exist is that a residential address and a datacenter
address get different answers from the same site. So a laptop test proves
the code and proves nothing about production. This runs the same checks
from inside the container that actually serves users, which is the only
place the answer means anything.

It asks EVERY route rather than stopping at the first that works, and it
deliberately leaves the health scores alone: a probe is a question, not
traffic, and an operator's curiosity should not reorder the chain real
users get.

Defaults to the sample links in resolvers.SAMPLES. Name platforms
("probe instagram tiktok") to narrow it, or paste real links to probe
those instead -- one per platform, since the later one wins.

### `_bus_routewatch()`

`routewatch` over the family bus: the daily check, now.

Where `probe` asks every route of every platform and leaves the scores
alone, this is the same thing routewatch.py does by itself once a day --
first working route per platform, the state it puts each platform in, the
operator told if that changed, and yt-dlp compared with its newest release.
Asking is not neutral: it updates what the daily check remembers, so a
problem found now will not be announced again tonight.

The whole check can outlast the 90 seconds ManagerBot gives a command
(yt-dlp alone is allowed 90), so past routewatch.REPLY_WITHIN_S this
answers with the platforms finished so far and goes on in the background.

### `providers_command()`

/providers -- which download route is working and which is not.

Operator-only, same reasoning as /dbdump and /status: operational detail, and
left in English like the rest of the admin output. This is the command
that answers "Instagram broke again, what happened" without reading logs.

### `_bus_providers()`

The same report, asked for from ManagerBot (`/providers downloader`).

Every operator-only command in this family should be reachable from
ManagerBot, and this one was not -- which mattered more than most, because
the question it answers ("which route is broken right now") is one asked when something is already wrong, usually away from this bot's chat.

It reads the in-process health table, so it answers for the container
that is actually serving downloads. `probe` beside it is the active
version of the same question: this says what has been happening, probe
goes and finds out.

## "the other way round", on a button

Getting the same download in the other quality meant three steps: /lossless,
pick the other setting, then find the link and send it again. All the user
was ever saying was "that one, but as a file" -- and the /lossless setting
is a \*preference\*, so changing it to get one file the other way leaves it
changed for every file after.

So the button does the one delivery and touches nothing else. Nothing about
the backend changes: it is the same resolve, the same download, the same
send, and it counts against the same daily allowance -- because it is a
second download, exactly as re-pasting the link would have been.

The URL cannot ride in the callback data (64 bytes, and these are share
links with tracking parameters), so it is held in user_data under a short
token. Bounded, because a tab left open for a week should not be a leak:
the last few downloads keep their button and the older ones answer "that
one has expired, send the link again", which is one step worse than the
button and no worse than before it existed.

_At `MERGE_PREFIX = "merge:"`:_

"merge:pdf:&lt;token>" and "merge:zip:&lt;token>": 20 bytes (10 + a 10-character token), against Telegram's 64.

## A chosen quality travels inside the link

The quality picker's choice is part of what was asked for: the 720p and the 1080p of one
video are two deliveries, and the delivery cache, the file-id store and the redo button all
key on the link. So the height rides on the link -- "#q720" for the one height the person
picked, "#upto480" for a group's cap -- and comes off only where the link is fetched
(\_split_height). A capped copy is then never sent to somebody who asked for the best, nor
the other way round.

### `_split_height()`

("&lt;link>", 720, True) from "&lt;link>#q720", ("&lt;link>", 480, False) from
"&lt;link>#upto480", and (url, None, False) for a link with neither.

_At `_GROUP: contextvars.ContextVar = contextvars.ContextVar("group_settings", default=None)`:_

The settings of the group chat a download is for (groups.py), or None in a private chat.
group_router sets it around handle_link, and the download task handle_link starts copies it
(asyncio tasks take the context they were created in), so the whole path -- the quality, video
or file, the credit line, no buttons -- reads it here instead of taking it as one more argument
through every function.

### `_remember_delivery()`

- At `return None`:
  A group gets no buttons (\_send_media), so there is nothing for them to read back,
  and the link a member posted there is not kept with them for it.
- At `memory[token] = {"platform": platform, "url": url, "lossless": lossless, "at": time.time …`:
  "at" is when it was delivered, to count how long people take to tap the
  button (REDO_DELAY_BUCKETS); a number, so it survives being saved as JSON.

_At `REDO_DELAY_BUCKETS = (`:_

How long after the delivery the redo button was tapped, as the counts name it:
[from, to) in seconds. A delivery saved by an older version has no "at": unknown.

### `_spend_action()`

True the first time `action` ("redo", "pdf" or "zip") is taken from this
delivery, False every time after. Per action, not per delivery: the PDF of
an album and its ZIP are two different things to ask for, and each can be
asked for once -- what must not happen is the same one twice, which fetches
the album again against the allowance. The mark is made in the same breath
as the look, with no await between them, so a double tap (two updates in
flight at once) cannot get both through. A list, because user_data is saved
as JSON.

- At `spent = remembered["spent"] = ["redo"]`:
  2.1.0 saved a bare True once its redo button was used; user_data
  outlives an upgrade, so that delivery's redo is still spent.

### `_unspend_action()`

Give `action` back: the tap that spent it came to nothing (the person
was out of downloads), so the button is theirs to tap again.

### `_remembered_kb()`

The keyboard of a delivered album as it stands now: the same buttons it
was first sent with (the counts are kept in `remembered` by \_send_media),
less every action already taken. A tap takes away its own button and no
other.

### `_nudge_kb()`

- At `rows = []`:
  Point at ConvertBot too -- e.g. someone might want just the audio, or
  a gif, out of what they just downloaded. No file handoff between bots
  anymore (each bot is fully independent) -- they re-send it there.
- At `if "redo" not in spent:`:
  The label says what tapping it \*gives\*, not which mode is on.
  "Lossless: off" is a setting; "get it as a file" is a thing that
  happens, and only one of the two reads as a button.
  `spent`: the actions already taken from this delivery, whose buttons are gone.
- At `merge_row = []`:
  An album, in one file. PDF only where there are pages to make (two
  photos; a post of one photograph and a video has nothing to bind),
  ZIP wherever there is more than one thing to carry. Same token as the
  button above -- merge_callback finds the link the same way.

### `_take_button_off()`

Put the delivery's keyboard under the message as it now stands (see
\_remembered_kb): the buttons of the actions spent are gone, the rest stay.

### `redeliver_callback()`

The button under a finished download. Runs the ordinary path with the
quality flipped for this one delivery, and leaves /lossless alone.

- At `note_tap("redo.spent" if remembered else "redo.expired")`:
  Counts only (note_tap): an old button, or one already used.
- At `note_tap("redo.to_compressed" if remembered.get("lossless") else "redo.to_file")`:
  Which way, and how long after the delivery: the numbers that say whether the
  five-minute delivery cache is the right length (counts only).
- At `delivery_cache.note_link(update.effective_user.id, remembered["platform"], remembered["u …`:
  This link is now the person's latest, whatever else they were looking at:
  a kept delivery of any other link is dropped, one of this link keeps going
  (delivery_cache.note_link). Before the allowance below, like handle_link.
- At `await _take_button_off(query, lang, token, remembered)`:
  The button is spent (above, before any await: a double tap is two updates
  in flight, and the markup below is only edited once Telegram answers):
  tapping it twice would download the same file twice against the same
  allowance, and the second tap is always a mis-tap. The markup is edited
  rather than the text, because the message it is under is a photo or a
  video and edit_message_text does not work on those. Only this button goes:
  the album's PDF and ZIP buttons stay, they are other things to ask for.
- At `_unspend_action(remembered, "redo")`:
  Nothing was started, so nothing is spent: the button comes back.

## an album as one file

📄 PDF and 🗜 ZIP, on the line that follows a delivered album. They are the
redo button's sibling and work the same way: the token in the callback data
finds the link in user_data, the tap counts as one download, and the post is
resolved and fetched again. The delivery cache (delivery_cache.py) keeps the
files of the last download for five minutes, but a merge does not use them yet
(a follow-up): it fetches, builds, sends, and deletes what it touched. What is
built is sent as one document, and everything it touched is deleted, whatever
happened.

### `_MergeStop`

The merge ends here, with this message (an i18n key) to the person.

### `_merge_text()`

- At `return i18n.t(lang, key, items=MERGE_MAX_ITEMS, mb=MERGE_MAX_MB, mp=merge.MAX_MEGAPIXELS)`:
  Every merge message gets every number any of them might mention.

### `merge_callback()`

The 📄 PDF and 🗜 ZIP buttons under a delivered album. Mirrors
redeliver_callback: answer, spend the buttons, count one download, and run
the work in a task of its own.

- At `remembered = context.user_data.get("redeliver", {}).get(token) if kind in ("pdf", "zip") …`:
  This action is spent here, by the first tap, before any await: its button
  is only taken off further down, and until Telegram has done that a double
  tap (or a second device) delivers a second tap of the same button. It
  would fetch the album again and count a second download. The second tap
  says what any spent button says. The other action on the same album (the
  ZIP after the PDF) is a different thing to ask for and still works, and
  its button stays.
- At `await _take_button_off(query, lang, token, remembered)`:
  Only the tapped button goes (a second tap of it would fetch the same album
  a second time against the same allowance); the other merge button, the
  redo button and the nudge row stay.
- At `_unspend_action(remembered, kind)`:
  Nothing was started, so nothing is spent: the button comes back.
- At `await asyncio.to_thread(record_download, update.effective_user.id, remembered["platform"])`:
  Counted here, before anything is fetched, on purpose: it is a rate limit
  and not money, so a merge that is then refused (merge_no_photos, say)
  still counts, exactly as a download that fails does.

### `_merge_and_send()`

Fetch the post again, build one PDF (its photos) or one ZIP (all of it),
send it as a document, delete everything. True if it was sent.

The same functions and the same limits as \_resolve_and_send: the route
chain, resolvers.download() with its per-file ceiling, the free-space check,
and the same three kinds of slot. On top of those, the two limits of this
feature -- MERGE_MAX_ITEMS files, MERGE_MAX_MB in all -- and neither is
checked after the fact: a post with too many files is refused before the
first is fetched, and the fetching stops at the file that takes the total
past the MB limit.

- At `out_path = os.path.join(resolvers._WORK_DIR, f"{uuid.uuid4().hex}.{kind}")  # noqa: SLF001`:
  Under the work directory like every download, with a name nobody can
  guess or collide with; the person is shown the proper one (merge.output_name).
- At `_make_room(MERGE_MAX_MB * 2)`:
  The inputs and the output it builds: kept deliveries give way first.
- At `try:`:
  One at a time across the whole process (merge.pdf_gate): a
  decode is a few hundred MB. Whoever waits sees the same
  "working" message as whoever builds.
- At `raise _MergeStop("merge_no_photos")`:
  build_pdf leaves out what is not a still photograph, as
  this leaves out a video; with fewer than two left it is
  the same ending as a post that had fewer to begin with.
- At `raise _MergeStop("merge_too_big")`:
  Possible for a PDF (a PNG that is smaller than the JPEG
  it becomes); the Bot API would refuse it, so say so first.
- At `await message.reply_document(`:
  As a document, and with the content-type sniffing off, which
  is what keeps Telegram from deciding a ZIP of videos is a
  video (see \_send_media).

### `_after_send()`

- `return` — a donation nudge is for the person, never for a whole group

### `_image_extension()`

Name a file after what it actually is.

Telegram shows a document by its filename, and there is nothing in a
download's bytes to say what the source called it -- so a .jpg that is
really a .webp is exactly the sort of small lie that has people
believing the bot mangled their picture. Four magic numbers cover
everything these platforms serve.

### `_reply_image_with_button()`

An image, with the same "the other way round" button the media path
puts under a video. Reddit and Twitter reach the chat through here rather
than through \_send_media, and a button that appeared under a downloaded
video but not under a downloaded picture would read as a bug.

_At `COPYRIGHT_URL = (os.environ.get("TERMS_URL") or "").strip()`:_

Where the copyright note sends a rights holder: this bot's terms in LEGAL.md, whose top line is the
operator contact. main.py gives every bot its own TERMS_URL.

### `_media_caption()`

What goes under every file the bot sends of its own (2026-11-18, shipped first in MumuCombined 2.2.0):
a short note that the material may be protected by copyright, with a link for a rights holder to
report it, and then the credit line for whoever keeps it on. HTML, so every send that carries it
says so (parse_mode), the MTProto route included (big_files.send_document's `html`). Asked for
"just in case", the way other download bots mark what they send. A channel's post keeps its own
text: what is put into it is the channel's, not the bot's.

### `_reply_image()`

One image back, compressed as a photo or verbatim as a file -- see
lossless_toggle for which and why.

`lossless=None` means "whatever this person's setting says", which is
every ordinary call. The button passes the opposite of what was just
delivered, for that one delivery only.

- At `if lossless:`:
  effective_message rather than message: every send path here is also
  reachable from a button, and on a callback-query update `message` is
  None while `effective_message` is the message the button is on. Getting
  this wrong is an AttributeError inside a download, which the user sees
  as the bot going quiet.

### `_media_source()`

What goes to Telegram for one file: its stored file id (sent_files.StoredFile),
or the file on disk, opened to be streamed.

### `_file_id_of()`

The file id Telegram gave the message `sent`, or None when it does not carry
the kind of file the form expects: a clip with no sound that Telegram turned
into an animation, say. Then nothing is stored for the delivery.

### `_send_media()`

Put what was resolved into the chat.

One file goes as a photo or a video so it plays inline; several go as an
album, in Telegram's own maximum of ten per group. Under /lossless every
one of them goes as a document instead -- see lossless_toggle for the
whole argument, and note `disable_content_type_detection`, without which
the Bot API sniffs an uploaded .mp4, decides it is really a video, and
delivers it re-encoded, which is the entire thing that branch exists to
avoid.

A video sent as a video is measured first (video.probe) and goes with its
real width, height and duration: left out, Telegram draws it as a square.
Documents and photos are not measured. `probing` is the delivery's probe
budget (video.ProbeRun); a delivery sent in several calls (\_send_large)
passes the same one to each, so the budget is one per delivery.

An item may carry a stored `file_id` instead of a file on disk
(sent_files.StoredFile; its path is None): it is sent by that id, with the
same captions, keyboard and album chunks, and is not measured -- Telegram
already has its shape.

Returns the file id Telegram gave each file, as (kind, file id) in the order
the items were given, so that the delivery can be stored (sent_files.py); None
if any of them is missing or is not the kind of file the form expects.

#### `_send_media.shape_of()`

The keyword arguments that give a video its shape; none when it could
not be measured. After one ffprobe has been killed for taking too long the
rest of the delivery is not measured at all, so that an album built to
stall it costs one wait, not one per file.

- At `remembered = context.user_data.get("redeliver", {}).get(redeliver_token)`:
  What the keyboard was built from stays with the delivery, so that a tap can
  rebuild it without the button it spent (\_remembered_kb).
- At `keyboard = (None if group is not None`:
  No buttons in a group chat: they act on the sender's own memory of the delivery, and
  a group has a dozen people who could tap them.
- At `message = update.effective_message`:
  See \_reply_image: this is also reached from the redeliver button.
- At `with _media_source(item, path) as f:`:
  Streamed from disk by python-telegram-bot rather than read into
  memory here first.
- At `sent_ids: "list[tuple[str, str]] | None" = []`:
  An album. The caption rides on the first item of the first group, which
  is where Telegram shows an album's caption.
- `if not isinstance(handle, str):` — a stored file id has nothing to close
- At `if keyboard is not None:`:
  Telegram allows no inline keyboard on a media group, so the button that
  would have been under the album goes on one line after it.

### `_store_sent_files()`

After a delivery from bytes: keep the file ids Telegram gave (sent_files.py),
so the next person who sends this post gets it without a download. Only when the
store is on, the sender is one person's, and the delivery is 1 to 10 files with an
id each (`sent`, from \_send_media: None if any is missing). Never raises: a
failed write is a line in the log and nothing else, and never touches the delivery.

### `_resend_by_file_id()`

Send this post again from the file ids stored when it was last delivered
(sent_files.py), if there are any and the post is known to be public still.

None: nothing was sent, carry on with the normal path (no row, a row that cannot
be read, a post that is gone or cannot be confirmed, an id Telegram refused).
True: it was delivered. False: the send failed and the person has been told.

A row confirmed within the last DBOT_FILEID_CONFIRM_HOURS is sent as it is. An
older one is first checked with resolvers.lookup: present, and it is sent and the
confirmation renewed; missing, and the row is deleted; unknown, and the row is
kept but an unconfirmed post is never sent. The send mirrors the delivery-cache
hit in \_resolve_and_send, step for step.

- At `row = sent_files.recall(key)`:
  A post many people send within minutes is read from the database once
  (sent_files.recall); trust is still decided below, from the stored time.
- At `logger.warning("The sent-file store could not be read (%s)", type(exc).__name__)`:
  A database that hiccups costs a normal fetch, never a failed delivery.
- At `await _forget_sent_files(key)`:
  Any refusal of the request itself drops the row, not only the wordings
  is_invalid_file_id knows: a row Telegram refuses would otherwise fail
  every send of this post, for everyone, until the 90-day sweep. A row
  dropped for a reason that had nothing to do with the ids costs the
  next person one ordinary download.
- At `sent_files.note("invalid")`:
  Telegram no longer knows these ids. Nothing was delivered (one file, or
  one chunk of at most ten), so: fetch the post as usual in this same
  job -- the status message is still up.
- At `logger.warning("Delivering %s from the stored file ids failed (%s)", platform,`:
  The kind of failure only, in the log and to the person: the text of an
  error from these ids could carry one. The row stays: a blocked bot or a
  flood wait says nothing about the ids.
- At `if not fresh:`:
  Used today. Seen public just now only if a lookup said so: a fresh row is
  trusted, not renewed, which is what makes "gone within 12 hours" true.
  A fresh row this process already marked today needs no round trip at all.

### `_resolve_and_send()`

The whole download path: pick a route that is working, fetch, send.

Returns True if something was delivered. On failure the user has already
been told, with one exception: `quiet_if_missing` says the caller has a
better answer than an error for a post with no media in it -- a text
tweet, which becomes a card instead.

Reuses an existing status message if one is passed in, instead of stacking
a second "Fetching..." on top of whatever the caller already showed.

`from_redo` says the caller is the redo button: its look-up in the delivery
cache is counted as a hit or a miss (counts only), a pasted link's is not.

- At `refusal = await refuse_new_work(`:
  A download outlasts the shutdown grace period, so starting one now would
  end in silence. A few seconds' wait beats a lost file.
- At `resent = None if sound else await _resend_by_file_id(`:
  Sent before, to anybody? Then it goes again by the file ids Telegram gave
  (sent_files.py): no slot, no disk, no route asked for media. Before the slots
  on purpose -- it uploads nothing, and the flood gate, the waiting limit and
  the allowance already bound how many can ask. None: carry on as usual.
- At `logger.warning("Delivering %s from the kept files failed (%s)", platform,`:
  The kind of failure only, in the log and to the person: the text
  of an error from these files would carry their path.

### `_large_download()`

A file over the free ceiling (large_files.py): refused with its price
and the switch, or -- for somebody who has switched large files on --
fetched, charged once it is here, and sent. Returns whether it was sent.

- At `_make_room(large_files.LARGE_MAX_MB)`:
  About to fetch up to LARGE_MAX_MB: kept deliveries give way first. After the
  refusals above, so that a person who is refused costs nobody their kept files.

### `_credit_wanted()`

The "via @bot" line: the group's setting in a group chat, the person's own elsewhere.

### `_handle_reddit()`

- At `title = post.get("title", "")`:
  Text or link post -- render a card instead of just dumping the title.
  Card meta label ("upvotes") stays English -- Pillow's built-in bitmap
  font (cards.py) has no Cyrillic glyphs, so translating it would just
  draw missing-glyph boxes.
- At `await _reply_image_with_button(`:
  The cards go through the same switch as the downloads. A card is text
  rendered as an image, which is the content photo compression damages
  most visibly, so "lossless" meaning "except the ones with words on"
  would be the wrong kind of surprising.

### `_handle_twitter()`

- At `user = tweet.get("user") or {}`:
  Card meta label ("likes") stays English -- see the Reddit card
  comment above re: Pillow's built-in font having no Cyrillic glyphs.
- At `await status.set(context.bot, i18n.t(lang, "twitter_fetch_failed_link", url=url))`:
  X blocked the fetch entirely -- don't error, just hand back the link
  rather than pretend this failed outright.

### `_allowance_message()`

What somebody who has run out is told.

Three things, in this order: that there is a limit and it is not
personal, when it lifts, and -- only for people who have not already
given -- that donating moves it. Asking a donor to donate is how a
thank-you turns into a nag, and the ledger already knows.

### `handle_link()`

- At `group = _GROUP.get()`:
  A group's quality cap (groups.py), on the platforms that go straight down the
  download path; Reddit and X read the post first, from the link as it is.
- At `found = delivery_cache.note_link(update.effective_user.id, platform, url)`:
  Another link: the person's last delivery is no longer wanted, and goes now.
  The same link instead gives it its time again (delivery_cache.LIFE_SECONDS,
  10 minutes by default). Before every check below,
  so that a link refused for any reason still counts as the latest one.
- `note_tap("link.again.cached")` — counts only: how often the same link comes back
- At `key = (update.effective_chat.id, update.effective_user.id, platform, url)`:
  The same person, in the same chat, sending the same link while a job they
  started from a link message for it is still queued or running: no new
  job, no allowance check, no record, no status message and no "busy" -- one
  👌 on the copy (\_answer_duplicate). The check and the mark are one
  synchronous stretch, nothing awaited between them, so that two copies
  handled at the same moment cannot both get through. Before everything
  below, which would each answer or charge for it.
- At `lang = await i18n.get_lang(update.effective_user.id, context)`:
  Asked once, here, for every platform. The gate used to live inside the
  video path only -- which covers Instagram, TikTok and the unadvertised site, and
  misses the paths that fetch an image and send it without ever reaching
  that function: a Pinterest pin, a Reddit image post and a Twitter photo
  set all sailed straight through an announced update. They are quicker
  than a video download, but "quick" is not the test: a redeploy lands
  whenever it lands, and a fetch that started ten seconds before it ends
  in silence exactly the same way.
- At `if _user_pending.get(update.effective_user.id, 0) >= MAX_PENDING_PER_USER:`:
  A share of the day, per person. MAX_CONCURRENT_DOWNLOADS above stops
  the box falling over; this is the different problem of one person
  holding both of its slots all afternoon while everybody else queues
  behind them. Checked here rather than inside the download so a refused
  link costs one query instead of a fetch -- and here rather than in each
  of the three branches below, because the Reddit and Twitter paths cost
  the same fetches and used to be the way past a limit that only the
  video path enforced.
  Checked before the allowance, so a link refused for being one too many
  does not also count against the day's share.
- At `_spawn_download(context, update, work, in_flight=key)`:
  Returns straight away: the next link, and everybody else's, is read now
  rather than after this one has finished uploading.
- At `if not spawned:`:
  Every way out that did not start the job -- the maintenance refusal,
  the full queue, the allowance, an exception, a create_task that
  raised -- takes the mark back. A job that started clears its own
  (\_spawn_download's finally).

## The quality picker

Asked for twice. On 2026-10-28: when a video comes in several qualities, ask which one, as
another bot does with a list of heights and their sizes; for MP3, trimming and the rest, a
button to ConvertBot is enough. On 2026-11-15, for 2.1.7: a card like a third-party bot's --
the video's picture, a button per quality, and one for the sound -- in private chats only
(confirmed 2026-11-17); groups keep the default. 2.1.7 shipped that card; this is it, on
2.2.0's footing.

- **Where.** A private chat, for the sites in `resolvers.PICK_PLATFORMS` (the one whose routes can
  list a video's versions). A group never gets a card (`handle_link`'s `picking`).
- **The card.** The picture, the title and length, a button per quality with its size, two to a row,
  "🎵 Sound only" with its size when the sound alone can be had, and the ConvertBot button (an MP3,
  trimming and the rest: the sound comes as the site's own track, M4A or MP3 depending on the route). Qualities that fit the free limit are 🎬; over it but within the
  large-file limit they are ⚡, paid, and only where large files are set up, with a line saying so.
- **Nothing is counted until a tap.** The link that gets a card is not recorded against the daily
  allowance; each tap is checked against it and recorded, as a download. The buttons stay, so
  another quality can be taken later (each one counted); the same tap twice at once is one download.
- **The pick travels on the link** (`#q720`), so it is fetched exactly by every route, and its kept
  file and stored file id are its own. The sound travels as `#sound`; a sound file is sent as audio,
  with the video's title and length and a file name made from the title, and -- as 2.1.7 deployed it --
  is never sent as a document, never kept or resent from a stored file id, and has no buttons under
  it: none of those knows what a sound file is.
- **When the versions can't be listed** in `PICK_LIST_TIMEOUT_S`, or the routes that list them are
  resting after failures, the link is counted and downloaded the usual way at once: the card is never
  the reason a download fails. From a server address the site mostly refuses, so the card will mostly
  not appear there.
- **An old card** (its token gone after five newer ones, or a restart) says so in an alert. A refusal,
  a full queue or a spent allowance after a tap is said as a message: alerts are cut at 200
  characters. `/deletemydata` forgets the cards' memory.

### `_split_choice()`

(link, height, exact, sound) from a link that may carry a pick: `#q720` exactly that quality,
`#upto480` a group's cap, `#sound` just the sound. `_split_height` is the same without the sound.

### `_audio_filename()`

A sound file's name: its video's title, with nothing a file name can't hold.

### `_remember_pick()`

The link, the title and length, and the qualities a card offered, under a short token the buttons
carry. The last PICK_MEMORY cards per person.

### `_offer_choices()`

List the versions (`resolvers.choices`), then send the card -- with the picture when Telegram can
fetch it, as text otherwise -- or, when nothing is worth a choice, count the link and download it.

### `pick_callback()`

A tap on a card: the same checks a pasted link gets (a pause for a redeploy, the queue, the daily
allowance), then that quality, downloaded and sent in reply to the card.

## Group chats

Added to a group, the bot downloads the links people post there and sends what it gets to
the chat, following the group's settings (groups.py). It sees those links only if it may
read the group's messages: as an admin, or with privacy mode turned off for it in
@BotFather. When it can do neither, its greeting says so.

Everything from a group chat goes through group_router, in its own handler group before
the private-chat handlers, and stops there: none of the private chat's answers ("that
doesn't look like a link", the feedback prompt, ...) is ever said to a whole group.

### `_group_command()`

The command a group message is ("settings"), "" for another bot's command
(/settings@OtherBot), or None for a message that is not a command.

### `_sees_group_messages()`

Whether the links people post here reach the bot. True when it cannot tell.

### `_group_hello()`

The greeting when the bot is added, and the answer to /start and /help in a group.

### `group_settings_callback()`

A tap on a group's settings. Checked again here, not only when the screen was drawn:
whoever taps may not be whoever sent /settings.

### `group_router()`

Every message from a group chat: /settings, /start and /help, links -- and silence for
everything else. Always ends the update here (ApplicationHandlerStop). An error is handed
to the error handler as a task of its own, so that it is reported like any other while
the update still stops here.

- At `await asyncio.to_thread(move_group_settings, message.migrate_from_chat_id, chat.id)`:
  The group became a supergroup, which has an id of its own: its settings come along.
- At `note_active(context, user)`:
  Counted as having used the bot only for what they send to it: in a group the
  bot reads everybody's messages (shared_features.track_activity).

### `bot_membership_changed()`

Added to a group: say hello and what it needs. Removed: forget the group's settings.
A channel: \_channel_membership_changed.

## Channels

An admin of a channel, the bot puts what a link holds into the post itself (channels.py says how,
and why that way). Everything from a channel goes through channel_router, first in its handler
group, and stops there: a channel post carries no person, which every private-chat and group
handler assumes, and nothing the bot says anywhere else may ever land in a channel. Nor is a
failure said there -- every subscriber would read it: it is logged, and an unexpected one reaches
the error handler, which tells only private chats. The settings live in a private chat with the
bot: the admin who adds it gets them there at once, any admin later with /channels.

_At `CHANNELS_LISTED = 50`:_

How many of the bot's channels /channels asks Telegram about, newest settings first: one
getChatMember each, to show a person only the channels they run.

### `_channel_settings()`

The channel's settings, and whether they are stored (False: the defaults, never saved).

### `_bot_rights()`

(edit, post, delete): what the bot may do with the channel's posts; all False when Telegram
cannot say.

### `_link_post()`

(platform, link) for a post the bot may put media into: text only -- a photo, video or file
of its own would be replaced -- not forwarded, which Telegram lets nobody edit, and with a link
to a site the bot knows.

### `channel_router()`

Every update from a channel. A new post with a link gets its media, in a task of its own
(\_channel_job); an edited post, and everything else, gets nothing. Always ends the update
here; an error goes to the error handler as a task of its own, as in group_router.

- `post = update.channel_post` — None for an edit: edited posts are left alone
- At `await asyncio.to_thread(set_group_settings, post.chat_id, settings)`:
  Added before 2.2.0, when nothing was saved: saved now, so that /channels lists it.

_At `_channel_jobs = 0`:_

Channel posts being fetched now: work in progress for the soft stop (lifecycle.usage_here, a work
source registered in main()), which would not see them otherwise -- nobody is behind a channel post
to count as active -- and which a stop signal waits for (SIGTERM_WAIT_SECONDS).

### `_channel_job()`

One link post: what the link holds, within the free size, put into the post or posted anew
(\_put_into_channel). A link that gives nothing, or too much, leaves the post as it is.

### `_video_shape()`

A video's width, height and duration, so that Telegram does not draw it square; {} when it
could not be measured (see \_send_media's shape_of).

### `_channel_media()`

One file, as the post's new media or as a member of an album.

### `_post_one()`

One file posted on its own (an album needs two or more), silently.

### `_put_into_channel()`

What channels.plan says: the media edited into the post ("edit"), or posted anew, silently,
under the post's text, and the link post deleted ("repost"). None: the post stays as it is.

- At `entities = list(post.entities or ())`:
  The post's own formatting and links stay. The credit line's @mention gets its entity here:
  Telegram finds none by itself in a text sent with entities.
- At `parts = []`:
  (item, path, open file, video shape, caption, entities): the caption on the first file only.

### `_channel_membership_changed()`

Added to a channel (a bot can only be added to one as an admin), its rights there changed,
or removed. Added or changed: the settings are saved, and whoever did it gets them in a private
chat -- if they have started the bot before, since Telegram lets a bot write only to them.
Nothing about them is kept. Removed: the settings go.

### `channels_command()`

/channels in a private chat: the settings of the channels the bot is in that this person runs.

### `channel_settings_callback()`

A tap on a channel's settings, in a private chat. Checked with Telegram on every tap, not
only when the screen was drawn: admins come and go.

### `unrecognized_message()`

Catches anything that isn't a recognized link and isn't a known
command -- registered last, so it only fires when nothing else already
handled the update. Silence here would just look like the bot ignored
them.

### `_delete_my_data_chosen()`

The shared erase button, and what only this bot holds on top of what it
erases: the person's kept delivery (delivery_cache.py), and the memory the
redo buttons read their links from. Their old buttons then answer "expired",
as they do after any restart. Done on the tap of "yes", before the erase
itself, so that even an erase that fails halfway leaves no file of theirs
here. forget_user also stops a download still in flight from being kept
when it lands.

### `_post_init()`

- At `try:`:
  Which download routes were working when this container's predecessor
  stopped. Without it a bot that redeploys several times a day re-learns
  that a dead service is dead every single time, at a user's expense.

_At `DELIVERY_CACHE_SWEEP_SECONDS = 15`:_

How often a kept delivery that has run out of time is found and deleted. A
delivery lives DBOT_CACHE_SECONDS from its last use; this is only how late
after that its files may still be on the disk. take() looks at the time too, so
a late sweep never serves an expired one.

### `_check_big_files()`

In the background, as the bot starts: an API id and hash that are set
but wrong turn large files off here -- no switch, 48 MB, as if they were
not set -- instead of failing the first person who asks for one.

### `_post_stop()`

- At `await _flush_provider_health()`:
  Before the pool closes: this is the one write that would otherwise lose
  a whole flush interval of "this route just started failing".
- At `await lifecycle.on_stop(application)`:
  Before flush_on_shutdown, which closes the pool lifecycle writes through.

### `main()`

- At `app.add_handler(TypeHandler(Update, track_activity), group=-4)`:
  ---- the handlers that run before everything else ----
  ONE GROUP EACH, and that is the whole point. python-telegram-bot runs
  at most ONE handler per group: the first whose filter matches wins and
  the rest of that group is skipped. track_activity is a TypeHandler on
  Update, so it matches every update there is -- which meant that for as
  long as these shared a group, it won every time and nothing else here
  ever ran. Separate groups, in the order they have to happen in.
- At `attach_flood_gate(app, ADMIN_IDS)`:
  Counted first, then throttled: somebody who floods is still somebody
  who was here, and /status is supposed to say so.
- At `attach_promo_claim(app)`:
  The 220 ⚡ gift: the first message while it is open claims it (see
  attach_promo_claim). Its own group, after the flood gate's.
- At `app.add_handler(MessageHandler(filters.ChatType.CHANNEL, channel_router), group=-1)`:
  Runs after track_activity but before every other handler -- a no-op
  unless /feedback or a "Custom" donate button has just asked for
  something to be typed, in which case it takes the answer and stops it
  from also being treated as a link (see awaited_text_received's
  docstring).
  Group chats first in the same handler group: python-telegram-bot runs the first
  handler that matches, so a group message reaches group_router and never the
  private-chat prompt-taker below it (see group_router).
  Channels before groups, and for the same reason: a channel's posts reach channel_router and
  stop there (see channel_router).
- `app.add_handler(CommandHandler("dbdump", dbdump_command))` — operator-only
- `app.add_handler(CommandHandler("messageas", messageas_command))` — operator-only
- `app.add_handler(CommandHandler("status", status_command))` — operator-only
- `app.add_handler(CommandHandler("providers", providers_command))` — operator-only
- At `app.add_handler(CommandHandler("en", set_language_en))`:
  ---- language: /en, /uz, /rus -- pick at first /start, change anytime ----
- At `app.add_handler(CommandHandler("privacy", privacy_command))`:
  ---- what the bot keeps, what it may be used for, and the erase button.
  Standalone for the same reason the language switches are: somebody who
  wants to know what is held on them, or wants it gone, should not have to
  finish whatever they were doing first. ----
- At `app.add_handler(CommandHandler("balance", balance_command))`:
  ---- donations (Telegram Stars) -- this bot's only Stars usage ----
- At `app.add_handler(MessageHandler(filters.COMMAND, unknown_command))`:
  Both registered last, so every real handler above gets first shot.
- At `family_link.attach(app, BOT_NAME, "DownloaderBot", START_TIME)`:
  ManagerBot's link: heartbeats, crash/donation events, and the queue it
  uses to run this bot's operator-only commands remotely. Never raises --
  with no shared database reachable the bot just runs on its own.
- At `family_link.COMMANDS["probe"] = _bus_probe`:
  One extra bus command, registered here rather than in family_link.py:
  only this bot has download routes to probe, and family_link.py has to
  stay byte-identical across all five. The dispatcher looks COMMANDS up
  per command, so adding to it after import is enough.
- At `app.run_polling(**lifecycle.polling_kwargs(`:
  A 30-second long poll is the same latency as the default 10 -- Telegram
  answers the moment an update exists -- for a third of the HTTP requests.
  allowed_updates lists every kind this bot has a handler for, so Telegram
  stops sending the rest rather than this process parsing and dropping it.
