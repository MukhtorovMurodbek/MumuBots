# `bot.py`

[↑ convert_bot](README.md) · [code](bot.py)

Standalone file converter bot -- images/video/audio, priced in Telegram Stars.

Split out of the original combined sticker+convert bot (see
ARCHITECTURE.md). This bot is its own process, its own folder and its own
deployment, with no file handoffs from its siblings -- so someone coming from
@StickerBot's "convert this" pointer just re-sends the file here directly
rather than it arriving pre-loaded. The five bots do share one Postgres
database, with a schema each; family_link.py is the only code that touches
anything outside this bot's own schema. What's new here past the original
combined bot is a shared /donate flow (Telegram Stars), merged into this
bot's existing precheckout/payment handlers alongside the "convert:" ones,
plus full English/Uzbek/Russian translation of every end-user-facing string.

```text
Commands:
  /start           - greeting + menu
  /convert         - convert a file to another format
  /formats         - every format this bot reads and writes
  /mystars         - the sender's own Stars spending history
  /stars           - (admin only) global Stars ledger
  /recharge        - top up ⚡ credit for conversions (/donate still works)
  /en, /uz, /rus   - switch language (English/Uzbek/Russian); also asked
                     once, trilingually, on first /start
```

```text
Requires: python-telegram-bot[job-queue]>=21.3, Pillow>=10.0, ffmpeg on PATH.
          Everything past images and ffmpeg -- PDFs, e-books, Word documents,
          spreadsheets, HEIC -- is an optional dependency in requirements.txt,
          and formats.py stops offering a format whose library is missing
          rather than failing when somebody picks it.
Env vars: CBOT_TOKEN, CBOT_USERNAME (no @), CBOT_ADMIN_ID (optional,
          comma-separated, gates /stars, /dbdump, /messageas and /status),
          DATABASE_URL and DB_SCHEMA
          (the shared family database, and this bot's schema in it),
          SIBLING_BOTS (see shared_features.py), CBOT_API_ID /
          CBOT_API_HASH (files over 20 MB, see big_files.py),
          LOCAL_BOT_API_URL / LOCAL_BOT_API_MODE / CONVERT_MAX_FILE_MB (see
          convert_utils.py).
          Every performance/cost knob has a working default and is documented
          in .env.example.
```

_At `try:`:_

main.py gives each bot its settings; this only reads a .env if one is present.

_At `BOT_USERNAME = (os.environ.get("CBOT_USERNAME") or "").strip().lstrip("@") or None`:_

Without the @, which is also taken off if it was written with one -- the
one-process edition always allowed it, and a link built from "@name" is broken.

_At `ADMIN_IDS = {int(x) for x in os.environ.get("CBOT_ADMIN_ID", "").split(",") if x.strip()}`:_

Operator-only admin tools (/stars, /dbdump, /messageas, /status) -- comma-separated
Telegram user ids. Empty/unset means disabled for everyone.

- `BOT_NAME = "convertbot"` — this bot's id within SIBLING_BOTS

_At `POLL_TIMEOUT = int(os.environ.get("POLL_TIMEOUT", "30"))`:_

Telegram's long-poll window -- see the note in main().

### `_deny()`

How an operator-only command answers everybody else: exactly the way a
misspelling does.

The intent was always to avoid confirming the command exists, and a bare
`return` looked like the way to do that. It is not: a bot that answers
every command it has and ignores precisely one has just pointed at the
interesting one, and to the person who typed it the bot simply looks
broken. Giving back the same sentence a typo gets makes the operator-only commands indistinguishable from commands that were never there.

## Where an upload waits between "here is a file" and "convert it to X"

It used to wait in context.user_data, i.e. in this process's memory, as a
bytes object of up to MAX_FILE_MB. python-telegram-bot keeps user_data for
the lifetime of the process, so anyone who uploaded a file and never picked
a format left their whole file resident forever. With a local Bot API
server raising the ceiling to 250 MB, a handful of abandoned uploads was
the entire container.

Now the upload goes to disk -- which on any host is both cheaper and more
plentiful than RAM -- and user_data keeps only the path. A sweeper removes
anything nobody came back for, including files orphaned by a restart.

- `PENDING_TTL_SECONDS = int(os.environ.get("CONVERT_PENDING_TTL_SECONDS", "900"))` — 15 min

_At `_EXT_RE = re.compile(r"[^a-z0-9]")`:_

An extension reduced to something that is certainly one path component.

Both extensions this file builds a path out of come from the other side of
the wire: the upload's comes from `document.file_name`, which the sending
client chooses, and the output's from callback data, which a modified
client can put anything in. Neither is Telegram's to sanitise on the bot's behalf, and `UPLOAD_DIR / f"...{ext}"` treats a "/" in `ext` as a directory
separator and a ".." as a level up -- so a file_name of `a.b/../../../x`
would have written the upload wherever the process could reach.

Eight characters of ASCII alphanumerics covers every real extension
("jpeg", "webm", "tar" -- and this only ever sees the part after the last
dot). Everything else becomes "", which the callers turn into a refusal or
into "bin".

### `_discard_pending()`

Clears the conversion state and deletes whatever it was holding.

A list rather than one path since v1.5.0: an album of pictures on its way
to becoming a single PDF is one pending conversion holding several files,
and abandoning it has to take all of them.

- At `busy = jobs.active_paths()`:
  A new upload abandons the pending menu, not the queue: with "keep file"
  on, the same file can be waiting on the menu AND in a queued conversion,
  and deleting it here would fail that conversion when it reached the
  front.

### `_sweep_uploads()`

Anything older than the TTL is gone: either the user never chose a
format, or the process that was handling it died. Blocking (it stats a
directory), so it runs through asyncio.to_thread.

Folders go the same way as files. A conversion that is KILLED (its time
limit, Stop, the out-of-memory killer) never reaches the `finally` that
removes the folder an archive was unpacked into, and jobs.\_finish removes
it only for a job this process knows about -- not for one a previous
process was running when it died. A 2.7 MB .tgz left 629 MB (review,
2026-10-07), on a disk of 1 GB.

At startup it is called with max_age_seconds=0 to clear the directory
outright: whatever is in there was staged by a process that no longer
exists, and the user_data pointing at it went with that process.

`busy_stems` (jobs.active_stems) are queued or running jobs: whatever is
named after one is theirs and is never swept, however old it looks -- an
unpacked folder carries the date the archive gave its `./`, which can be
1970 (review, 2026-10-08). The only folders swept are those unpacked
folders (`*-contents`): CONVERT_UPLOAD_DIR may be pointed somewhere that
holds folders of its own.

- At `sweep_all = max_age_seconds <= 0`:
  A cutoff of "now" is not the same as "everything": a file written
  milliseconds ago can carry an mtime at or past it, since filesystem
  timestamp granularity is coarser than time.time(). Zero means all.
- At `job_cutoff = time.time() - max(max_age_seconds, jobs.TIME_LIMIT_S + 300)`:
  What a running conversion makes for itself -- the folder an archive is
  unpacked into, the padded copy bsdtar reads -- is not in `keep`, so it is
  given the longest a conversion can run, not the upload TTL: a time limit
  raised past the TTL must not have the sweeper empty a job under its feet.
- At `continue`:
  A queued or running conversion's input. Its mtime is when it
  was uploaded, which says nothing about whether it is still
  needed -- a job at the back of a queue can be older than the
  TTL and still be about to start.
- `continue` — a running job's own folder, copy or result
- At `convert_utils.remove_tree(path)`:
  remove_tree makes a read-only or unreadable entry writable and
  retries, and a link inside is removed as a link, never followed.

### `_conversions_in_flight_job()`

Keeps this process's paid conversions stamped, and refunds those of a
process killed before it could -- see jobs.HOLD_STALE_S.

### `build_help_text()`

The short version unless asked for the long one -- the family's help
template (shared_features, "/help in two lengths"). The long one carries
the price tiers and every command. Both end with the sibling-bots
pointer; the per-command pages (HELP_PAGES below) are the buttons under
the help, not a line in it.

_At `def _help_page_convert(lang: str) -> str:`:_

---------- /help &lt;name> and the buttons under /help: a page for each command ----------
The mechanism is in shared_features ("A page for each command"). Every
number on a page is read from the module that owns it when the page is shown
-- by attribute, never copied -- so a page says what the code does now.

### `_help_topup_ladder()`

(the ladder as lines, the sentence about the bonus) -- walked the way
family_link.quote_credit prices a payment, from family_link.TOPUP_LADDER as
it is now. No ladder: one flat line, and no bonus to talk about.

- At `per_star = family_link.TOPUP_MULTIPLIER`:
  The multiplier itself, not credit_for_stars(1): that rounds to whole credits,
  so a FAMILY_TOPUP_MULTIPLIER of 1.5 would print as 1 while quote_credit pays 1.5.

### `_help_page_recharge()`

- At `return text if bonus_rule else text.replace(ladder + "\n\n", ladder + "\n", 1)`:
  With no bonus sentence the template leaves an empty line before the bullets.

_At `BOT_COMMANDS = [`:_

Public command menu (the "/" button in Telegram's chat UI) -- set on
startup via set_my_commands() below instead of pasting into @BotFather by
hand. Operator-only admin commands (/stars, /dbdump, /status) are left off.
Language-switch commands are described in the language they switch to
(self-explanatory by script/language, since Telegram's command menu itself
isn't per-user).

_At `BotCommand("start", "What this bot does, and how to start"),`:_

Order is deliberate: what this is, how to change language, what the bot
does, how to stop it, help, money, then the policies. /language sits
second on purpose -- somebody reading a menu in the
wrong language needs the way out before anything else.

/en, /uz and /rus are NOT here and still work. They are the way back for
somebody who cannot read the menu at all, so they must never stop
working -- but three lines for what /language already does is three
lines of noise for everybody who can read it.

/mystars is gone from here and still works. It listed Stars paid for
conversions, which are not paid in Stars any more -- it has called
/balance since credit arrived, and one command showing the whole history
beats two showing halves of it.

_At `ADMIN_COMMANDS = [`:_

The same menu, in the operator's own chat, with the commands only the operator can run. Kept out of BOT_COMMANDS on purpose -- a stranger should not be offered
/dbdump -- but hidden from the operator too, which was the accident. See
publish_commands() in shared_features.py. English, like the rest of the
admin output: the only person who sees this list wrote the bot.

### `help_rows()`

What sits under /start and /help besides the full-guide button.

_At `async def _reply(update: Update, text: str, **kwargs):`:_

---------- /start ----------

### `_reply()`

Works whether the update came from a command or a button tap. A tap
evolves the tapped message in place -- so choosing a language turns the
picker itself into the instructions rather than leaving a stale picker
sitting above them -- while a command has no earlier bot message to reuse
and so always sends fresh.

"In place" only while that message is still the last thing in the chat:
once the user has said anything since, the answer is sent fresh rather
than written above what they are looking at. See live_message.py.

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

### `help_command()`

The instructions on their own. Every other bot in the family has had
a /help since the beginning; this one only ever printed its help as part
of /start, which stopped being a way to reach them once /start became
the language picker.

### `_apply_language()`

The single path every language change goes through -- /en, /uz, /rus
and a tap on the picker alike -- so all four end the same way: with the
instructions, printed in the language just chosen.

A conversion already waiting on a format choice is deliberately left
alone. Changing language is not cancelling anything, and the format
buttons on that earlier message keep working.

- At `await refresh_chat_menu(context, update.effective_user.id, lang)`:
  The menu follows the choice too -- Telegram otherwise shows it in the
  language of the phone.

### `language_chosen()`

A tap on the picker -- same effect as /en, /uz or /rus.

_At `def _ext_of(filename: str | None, fallback: str) -> str:`:_

---------- file converter (Telegram Stars) ----------

### `_ext_of()`

The part after the last dot, normalised, or `fallback`.

Sanitised, not trusted: see \_safe_ext. A file_name whose tail is not a
plausible extension is treated as having none at all, which is the
honest answer -- the bot cannot tell what it was sent.

- `return compound` — photos.tar.gz is a tgz, not "a .gz"

### `_ext_of_file()`

`_ext_of` on the file's name; a file sent with no name (a GIF from Telegram's own picker, say) falls back to its MIME type, then to `fallback`.

## /formats -- the answer to "what can this thing actually do"

Where it is offered, and why there:

```text
as a command       in the menu and in /help, for somebody deciding
                   whether this bot is worth keeping.
as a button        under the refusal when a file's format is not one the bot takes, and under /convert. That refusal is the single
                   most valuable place for it: it is the one moment the
                   user has demonstrated they want something converted
                   and has just been told no.
as a bare word     "heic" typed into the chat is answered with what a
                   HEIC becomes, rather than with a form letter. It costs
                   one lookup in the catch-all handler that was already
                   running.
```

What it does NOT do is print eighty formats in one message. The first screen
is six categories and six examples; a category is one tap.

### `_format_lookup()`

What one named format becomes, or None if that was not a format.

None rather than a refusal, so the catch-all handler can fall through to
its own answer for text that was never about a format at all.

## cancel

### `_cancel_items()`

Everything this bot could be waiting on: an uploaded file still waiting for a format to be picked, and the shared donation prompt. Reads
state only -- nothing is stopped until a button says which.

- At `for job in jobs.jobs_for(update.effective_user.id):`:
  Each conversion in the queue is its own item, labelled with what
  stopping it costs: nothing if it has not started, and the credit if it
  has. The label is the warning.

### `cancel_command()`

Asks which of the things it is waiting on to stop, then stops that
one. The Cancel button under the format list stays as it is: there is
only one thing it can possibly mean, so asking there would be noise.

### `cancel_choice_callback()`

A tap on one of /cancel's buttons. Re-reads what is pending rather
than trusting the menu: it may have been sitting on screen a while, and
the file it offers to discard has a TTL.

### `cancel_conversion_callback()`

The Cancel button under the format list -- the same thing /cancel does,
without making anyone type it.

## a file arrives

Telegram sends an album as N separate messages that share a media_group_id,
a fraction of a second apart. Before v1.5.0 each one was handled on its own,
so five photos produced five format menus of which only the last still had a
file behind it -- every earlier one had been discarded by the next arrival.

They are now collected instead. The first file of a group schedules a job
BATCH_WAIT_SECONDS out; every further file of the same group cancels and
re-schedules it, so the menu is drawn once, after the last one lands. That
also makes "these pictures, one PDF" expressible, which is the conversion
most people mean by "image to PDF" and which no amount of per-file handling
can offer.

### `_too_large_text()`

Why a file this size is refused here, which depends on whose limit it
is: Telegram's (the Bot API's 20 MB, when nothing lifts it), this chat's
(over MTProto big files come in private chats only), or this bot's own.

_At `_PROGRESS_EVERY_S = 3.0`:_

How often a download's progress card is redrawn. Every edit is a request,
and Telegram rate-limits edits to one message; every few seconds is enough
to show it moving.

_At `_CARD_FROM_BYTES = 2 * 1024 * 1024`:_

Below this a file arrives in a moment, and a card that is drawn and deleted
again within a second is flicker rather than information.

### `_download_progress()`

A progress(done, total) that redraws the download card, at most every
\_PROGRESS_EVERY_S. Never 100% until the file is actually there.

_At `GET_FILE_READ_S = float(os.environ.get("CONVERT_GET_FILE_READ_S") or 90)`:_

Most likely cause of the "Timed out" a first try used to end in, where the second try worked
(2.1.7): getFile on a file the Bot API server has not seen yet makes that server fetch the whole
file from Telegram's storage before it answers, which can take longer than the library's 5 s
default. The report kept no traceback, so the log now says which step failed.

### `_Interrupted`

A streamed download that broke off; worth another try.

### `_worth_another_try()`

A timeout or a dropped connection, not a refusal: the next try usually just works.

### `_download_bot_api()`

A file within the Bot API's 20 MB, with up to DOWNLOAD_TRIES tries when the network is
what failed.

### `_download_bot_api_once()`

One try: getFile, then the file itself, streamed when it is big enough for the card to move.

### `_fetch_file()`

The file itself. The URL carries the bot's token, so no error that could quote it is let
out as it is -- the message a person sees includes the error text.

- At `read_s = max(60.0, declared_size / 100_000)`:
  About 100 KB/s at worst, and never under a minute.

### `_download_big()`

A file over the Bot API's 20 MB, fetched over MTProto -- big_files.py.
Raises what big_files.download raises.

### `convert_receive_file()`

- At `if msg.animation:`:
  An animation comes with `document` set too (Bot API backward compatibility), so it is
  looked at first: a Telegram GIF is an MP4 with no sound and often has no file name.
- At `await msg.reply_text(`:
  Said before the download, not after it: nothing is fetched that can't be converted.
- At `async with jobs.storage_gate():`:
  Room on disk, checked against the size Telegram declares, before a byte
  is written. With files kept for more conversions and several queued at
  once, "the disk filled up half-way through a download" stopped being
  hypothetical.

  Updates of different people run at the same time, so the room is also promised
  (jobs.reserve_storage) in the same breath it is found, under a lock: otherwise two
  people's downloads are both told there is room for what only one of them fits.

### `_receive_with_room()`

The rest of convert_receive_file, once the room for the file has been promised. The
promise is given back as soon as the download ends (the file is then on disk, where the
next question counts it), and by the caller whatever happens.

- At `_discard_pending(context)`:
  Whatever was waiting from a previous upload is dead the moment a new
  file arrives -- delete it now rather than leaving it for the sweeper.
- At `await msg.reply_text(i18n.t(lang, "batch_mixed_formats"))`:
  An album of a photo and a video is two conversions, not one.
- At `state["declared"] = state.get("declared", state["size"]) + declared_size`:
  Already past the limit: the rest of the album is not worth fetching.
- At `state["declared"] = state.get("declared", state["size"]) + declared_size`:
  The album as a whole is priced, and refused, on its total -- which
  used to be found out only after every file was on disk. With files
  of up to 100 MB that is a lot to download for a refusal.
- At `async with lifecycle.busy(msg.chat_id, i18n.t(lang, "restarting_send_again")):`:
  A file on its way in is work in progress: a soft stop waits for it
  (lifecycle.usage_here), and a redeploy in the middle of it says so.
- At `await _download_bot_api(context, file_obj, path, declared_size, status, lang)`:
  Streamed to the file either way; download_as_bytearray built the
  whole thing in memory first.
- At `_unlink(path)`:
  The route for big files is down, not the file. Said as such, with
  what still works, rather than as a download error.
- At `context.user_data["convert"] = {`:
  Stage it, then wait: the rest of the album is milliseconds behind.

### `_join_album()`

A file joins the pending conversion of its album.

The menu already on the screen may have been drawn for the first file alone
(the rest of the album is slower than the wait before the menu), with that
file's page count and its scanned pages in the state. Until the menu is
drawn again for all of them, a tap on it must find an album: neither
priced nor timed as a text-recognition job, nor as a document of the
first one's pages. So both are forgotten here, not at the redraw.

### `_schedule_batch()`

(Re)arm the job that draws the menu once the album has stopped arriving.

### `_present_batch_job()`

The album stopped arriving; draw one menu for all of it.

- At `context.user_data.pop("convert", None)`:
  user_data is rebuilt by \_present_conversion_options, which is the one
  place that decides what a pending conversion looks like.
- At `for path in paths:`:
  Refused whole, as it would have been once downloaded -- converting
  the part that did arrive is how a PDF comes out quietly short.

### `_format_rows()`

Three formats to a row, then whatever navigation the list needs.

A long list is cut to POPULAR_COUNT with a "More formats" button under it
rather than shown whole: an image now has fourteen targets, and fourteen
buttons is a menu somebody has to read instead of a menu somebody can use.

`ocr` is the language text is read in ("eng", "rus" or "uzb"), given only
when a button in `targets` reads text: it becomes the row that changes it.

- At `rows.append([InlineKeyboardButton(`:
  The consent to hold a file past its first conversion, asked where
  it matters and remembered. Off unless somebody turns it on: the
  default is the promise PRIVACY.md makes, that a file is deleted as
  soon as it has been converted.

### `human_size()`

A size somebody can read. "12288 KB" is a number to work out; "12 MB"
is a size.

### `_options_text()`

The format menu. It does not quote a price, because until a format is
chosen there is no price to quote.

It used to open with one, taken from the size of the upload, and that
number was wrong in both directions for the same file: a 78 KB .docx
became a 1.6 MB PDF and was billed as 78 KB, while a 12 MB video billed
at 12 MB whether it was being turned into a 1 MB MP3 or a 70 MB GIF. The
quote now happens where the answer exists -- see \_quote_text.

### `_quote_text()`

What this conversion will cost, and what it is costing for.

Both sizes are on it because both are in the price, and the estimate is
called an estimate. The line that matters most is the one after it: the
number quoted is the number charged. An estimate somebody is asked to
agree to, which can then be revised upwards once they have agreed, is not
a quote -- it is a deposit, and nobody signed up for one.

- At `lines.append("")`:
  Said where the money decision is made, which since 1.7.0 is here rather
  than on the format menu: taken when the conversion starts, kept if the job is stopped, returned if the bot fails, and never turned back into Stars.
- At `lines.append("")`:
  A converted file can come out over what a bot may send, and whoever is
  paying should know before they pay that the ⚡ comes back if it does.

### `_ocr_withheld()`

The targets that read text which THIS file must not be offered, though
the host can write them (formats.targets_for says only that it can).

An album is converted a picture at a time and each would be read for the
price of one, so neither is offered for it. A PDF is offered a searchable
PDF only when it has pages to read, and not when it has more than
ocr_convert.OCR_MAX_PAGES: one conversion reads no more than that. Its TXT
is still offered, and is the ordinary one (convert_utils.ocr_job_pages
reads no page of such a PDF), as it is for a PDF whose pages could not be
counted.

### `_offered_targets()`

What the menu offers for a pending conversion, computed one way for
every redraw: the menu, More/Less, the keep toggle and the text language.

### `_ocr_choice()`

The language text is read in: the person's choice, remembered for their
next file, or until they make one, the language they use the bot in.

### `_ocr_row()`

The text language for the menu's toggle row -- None unless one of the
buttons on the screen being drawn reads text: the row changes how those are
read, so it is not on a screen that has none (a picture's TXT and searchable
PDF are under More formats, so its first screen has no row). Asked of
convert_utils.ocr_job_pages, the function the price and the queue ask, so
the row is there exactly when a conversion that reads text is.

### `_present_conversion_options()`

- At `animated = gif_is_animated(paths[0]) if src_ext in formats.ANIMATED_CAPABLE else None`:
  A one-frame GIF is a picture and should be offered PDF and ICO; an
  animated WEBP is a video and must not be offered a target that would
  throw its frames away. The extension cannot tell them apart, so the
  file is asked, once, and the answer travels with the pending conversion.
- At `price = price_for_size(size)`:
  Still the upload's own size that decides whether the file is takeable at
  all: that ceiling is Telegram's, and belongs to what arrived rather than
  to an estimate of what would come out.
- At `megapixels = None`:
  A picture's pixel count decides two things before anybody pays: whether
  it can be converted at all, and which formats would come out too big
  for a bot to send back. Read from the header, not decoded.
- At `raw_info = await jobs.describe_raw(paths[0], src_ext)`:
  A camera raw file's size comes from LibRaw, not Pillow, and it is the
  size of what comes OUT -- half of each side past raw_convert.FULL_MP
  -- that the estimates of what is sendable are about.
  In a child process (jobs.describe_raw): LibRaw parses a stranger's
  file in C, and this is the bot's own process.
- At `megapixels = await jobs.describe_image(paths[0])`:
  In a child process too (jobs.describe_image): some of Pillow's
  header reads decode, and this is the bot's own process.
- At `pages = None`:
  How many pages a document has, read from its index rather than by
  rendering it. It is what stops a 900 KB PDF of four hundred pages being
  quoted as though four hundred PNGs would weigh 900 KB.
- At `ocr_pages = 0`:
  ...and how many of a PDF's pages are pictures of text, which are read when
  it is converted to TXT or a searchable PDF and are what that is priced by.
  A picture is one such page. A PDF is looked at in a child process too
  (jobs.describe_pdf), whose answer is None when it could not be read: then
  the PDF converts as it did before text was read, and its TXT is that one.
- At `if formats.met("ocr") and counted["scanned"] is not None:`:
  "scanned" is None when the pages could not be looked at in the
  few seconds the menu may wait (or the host cannot read text): the
  PDF is priced by its page count and its TXT is today's.
- At `if len(paths) == 1 and formats.category_of(src_ext, animated) == formats.VIDEO:`:
  A video too big for this server's memory to convert at its own size is
  said so here, where the choice of format is, rather than after the fact.

### `convert_keep_callback()`

The keep-file toggle under the format menu.

Asks permission rather than assuming it. Converting one file to several
formats means holding the file between conversions, and the privacy
notice promises a file is deleted once it has been converted -- so
holding it longer is something a person turns on, for themselves, and
can see is on. The choice is remembered for their next file too.

### `convert_ocr_callback()`

The text-language toggle under the format menu: English, Russian,
Uzbek, and round again. It is the language the words in a picture or a
scanned PDF are read in, so it only matters to the formats that read them.

Remembered for the person's next file, as the keep-file choice is, and
fixed for a conversion when it is queued (\_enqueue). The menu is redrawn
as it was -- still expanded if More formats was open, because that is
where a picture's text formats are.

### `convert_show_more()`

The same message, redrawn with every target on it -- or back to the
short list. The menu is one message that changes, not a second one.

### `_result_caption()`

Say what actually came back.

Three of these are new in v1.5.0 and all three exist because the button
said one thing and the file is another: a zip instead of forty PNGs, a
PDF holding six pictures, a Word document whose layout was rebuilt rather
than copied. A caption that ignored any of them would be a small lie in
the one message the user actually keeps.

- At `relaid = ("md", "html", "txt") if formats.met("office") else ("docx", "md", "html", "txt")`:
  LibreOffice lays a Word document out by Word's own rules; only without it is the page re-laid.
- At `if target_ext == "docx" and src_ext not in ("doc", "odt", "rtf"):`:
  The note is about a PDF's text made editable; a Word file from LibreOffice is a copy, not a rebuild.
- At `caption += "\n\n" + i18n.t(lang, "converted_docx_note")`:
  What a PDF is made of is marks on a page, so the thing that comes
  back is the document's words rather than its pages. Said here, on
  the file itself, because that is the message somebody keeps.

### `_runner()`

One runner per application. Built lazily because the application does
not exist when this module is imported.

### `_touch()`

Restart a kept file's 15 minutes: the sweeper goes by modification
time, and a file somebody is still converting is not abandoned.

### `_enqueue()`

Hand a chosen conversion to the queue and return straight away.

The handler that calls this used to run the whole conversion before it
returned, and every bot here handles updates one at a time -- so one
large conversion froze the bot for everybody. Now the button is answered
in milliseconds and the work happens in jobs.py.

With "keep file" on, the menu message stays exactly as it is, so the
same file can be sent to another format with one more tap, and the
status goes in a message of its own. With it off, the menu becomes the
status and the file belongs to the job from here on.

- At `reading = convert_utils.ocr_job_pages(conv_state["src_ext"], target_ext, conv_state.get( …`:
  A conversion that reads text is given the time limit its price was made
  from (ocr_limit, the same function the quote used), the language
  chosen now, and the number of pages the price counted: the worker reads
  no more than that. Anything else is timed as it always was. An album is
  never one that reads (ocr_job_pages is given the number of files).
- `ahead = jobs.ahead_of(job)` — not queued yet: every job of this person counts, the others' up to their turn

### `convert_stop_callback()`

The Stop button under a conversion's status.

A running conversion stopped this way is not refunded -- the button says
so on its face, and the status the person was watching said so when it
started. A queued one costs nothing, because credit is only taken when a
conversion starts. Either way the runner writes the ending into the
status, so this only has to ask.

### `convert_format_chosen()`

- At `if target_ext not in formats.targets_for(conv_state["src_ext"], conv_state.get("animated …`:
  Callback data is whatever the client sends back, not necessarily what
  was put on a button -- so the format is checked against the same list
  the buttons were built from rather than taken on trust. Without this,
  a format nobody was offered reached \_new_upload_path and ffmpeg.
  targets_for() rather than the declared table: a format this host cannot
  actually produce must be refused here too, not only left off the menu.
- At `await query.answer(i18n.t(lang, "not_for_this_file"), show_alert=True)`:
  Left off this file's menu for a reason that is not the host's (an
  album cannot be read for text, a PDF with no scanned page has no
  searchable PDF to make): refused here as well, whatever was sent back.
- At `price, est_out = price_for_job(src_ext, target_ext, conv_state["size"],`:
  The price, at last: both sizes and how hard the pair is to run.
- At `if price == 0:`:
  A free conversion is not a decision anybody needs to confirm -- there is
  nothing to agree to -- so it starts on the tap that chose the format,
  exactly as it did before. A paid one stops here and asks.

### `convert_quote_back()`

"Another format" on a quote: back to the menu, nothing charged.

### `convert_quote_go()`

The tap that agrees to the quoted price.

### `_start_chosen()`

Everything between "yes, that one, at that price" and the job being on
the queue: the refusals, the balance, the invoice if there is not enough.

Reached from two places -- a free conversion, where choosing the format is
the whole decision, and the Convert button on a quote.

- At `refusal = await refuse_new_work(lang, user.id, query.message.chat_id)`:
  Refused before anything is queued or charged: an announced update is a request for quiet, and a job queued now would be interrupted.
- At `await asyncio.to_thread(family_link.topup, user.id, stars,`:
  Nothing is charged, and the ordinary queue then runs for real.
- `provider_token="",` — empty string is required for Telegram Stars payments

_At `async def precheckout_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):`:_

---------- Stars payment plumbing -- handles BOTH "convert:" and "donate:" ----------
Only one PreCheckoutQueryHandler / SUCCESSFUL_PAYMENT handler should be
registered per bot (see shared_features.py's big comment on this), so this
bot's versions branch on the payload prefix instead of stacking handlers.

### `successful_payment_callback()`

- At `result = await credit_stars_payment(update, context, user, sp.total_amount, lang, payloa …`:
  The Stars are credit whatever happens next. This branch used to refund a
  payment whose staged file had gone -- and looked for that file under
  conv_state["path"], a key that became "paths" when albums arrived in
  v1.5.0. It could never find it, so every invoice-paid conversion since
  then was refunded and never converted. Keeping the payment as credit is
  both the fix and the better answer: nothing is lost and nobody waits on
  a refund. (A payment that cannot be credited at all is refunded: see
  credit_stars_payment.)
- At `return`:
  A payment Telegram has already delivered: credited the first time, and
  the conversion it paid for was started the first time -- or one that
  could not be credited, which was refunded or handed to the operator, the
  payer told. Either way, starting the conversion would be wrong.

### `mystars_command()`

/mystars, kept working for everybody who learned it.

It listed the Telegram Stars paid for conversions, and a conversion is not
paid in Stars any more: it is paid in credit, and every movement of that
credit is in the family ledger that /balance reads. Two commands showing
two halves of one history would be worse than one showing all of it.

_At `async def stars_admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):`:_

---------- admin-only (English only, not part of this i18n rollout) ----------

### `_bus_stars()`

The Stars ledger, asked for from ManagerBot (`/stars convert`).

ManagerBot's own `/donations` reads every bot's `star_transactions` out of
the shared database and totals them. This is the other half of that
picture: ConvertBot is the only bot in the family that charges for
anything, so it is the only one whose ledger holds paid, free and
refunded \*conversions\* rather than donations alone, and this is the view
that shows them.

### `dbdump_command()`

/dbdump -- exports every table in this bot's own database as one
zip of CSVs. Operator-only (reuses the same ADMIN_IDS as /stars).

### `messageas_command()`

/messageas &lt;user_id> &lt;text> -- sends a message to that user as this
bot. Only works if the user has messaged the bot before (Telegram
doesn't let bots cold-message anyone). Operator-only (reuses the same
ADMIN_IDS as /stars, /dbdump, and /status).

### `status_command()`

/status -- uptime, hosting environment, any crashes since this
process started, and active-user counts. Operator-only (reuses the same
ADMIN_IDS as /stars and /dbdump).

### `unrecognized_message()`

Catches anything that isn't a file and isn't a known command --
registered last, so it only fires when nothing else already handled the
update. Silence here would just look like the bot ignored them.

One word typed at a converter is usually a format ("heic", ".epub",
"webm?"), and answering it costs a dictionary lookup. Anything else gets
the same answer as before, with a button to the full list.

### `_check_big_files()`

In the background, as the bot starts: an API id and hash that are set
but wrong turn the big-file route off here -- 20 MB, as if they were not
set -- instead of failing the first person who sends a big file.

### `_post_init()`

- At `await lifecycle.on_start(BOT_NAME)`:
  Before the first getUpdates -- see lifecycle.py: two containers polling
  one token is 409 Conflict and split updates, not a graceful overlap.

### `_post_stop()`

- At `await lifecycle.on_stop(application)`:
  Before flush_on_shutdown, which closes the pool lifecycle writes through.

### `main()`

- At `formats.probe()`:
  Which backends this host actually has. Once, before the first menu is
  ever built, so nothing is offered that cannot run -- see formats.py.
- At `lifecycle.add_work_source("conversion(s)", lambda: len(jobs._jobs))`:
  Conversions, queued or running, are work in progress for the soft stop.
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
- At `app.add_handler(MessageHandler((filters.TEXT | filters.REPLY) & ~filters.COMMAND,`:
  Runs after track_activity but before every other handler -- a no-op
  unless /feedback or a "Custom" donate button has just asked for
  something to be typed, in which case it takes the answer and stops it
  from also being treated as a file or a format name (see
  awaited_text_received's docstring).
- `app.add_handler(CommandHandler("dbdump", dbdump_command))` — operator-only
- `app.add_handler(CommandHandler("messageas", messageas_command))` — operator-only
- `app.add_handler(CommandHandler("status", status_command))` — operator-only
- At `app.add_handler(CommandHandler("en", set_language_en))`:
  ---- language: /en, /uz, /rus -- pick at first /start, change anytime ----
- At `app.add_handler(CommandHandler("privacy", privacy_command))`:
  ---- what the bot keeps, what it may be used for, and the erase button.
  Standalone for the same reason the language switches are: somebody who
  wants to know what is held on them, or wants it gone, should not have to
  finish whatever they were doing first. ----
- At `app.add_handler(CommandHandler("recharge", donate_command))`:
  ConvertBot sells credit, so here it is "recharge"; the bots that only
  take support say "donate". /donate keeps working for anyone who learned
  it, and stays out of the menu -- see UNADVERTISED in tests/test_menu.py.
- At `app.add_handler(MessageHandler(filters.COMMAND, unknown_command))`:
  Both registered last, so every real handler above gets first shot.
- At `family_link.attach(app, BOT_NAME, "ConvertBot", START_TIME)`:
  ManagerBot's link: heartbeats, crash/donation events, and the queue it
  uses to run this bot's operator-only commands remotely. Never raises --
  with no shared database reachable the bot just runs on its own.
- At `family_link.COMMANDS["stars"] = _bus_stars`:
  One extra bus command, registered here rather than in family_link.py:
  only this bot sells anything, and family_link.py has to stay
  byte-identical across all five. Same pattern DownloaderBot uses for
  probe/providers -- the dispatcher looks COMMANDS up per command, so
  adding to it after import is enough.
- At `app.run_polling(**lifecycle.polling_kwargs(`:
  A 30-second long poll is the same latency as the default 10 -- Telegram
  answers the moment an update exists -- for a third of the HTTP requests.
  allowed_updates lists every kind this bot has a handler for, so Telegram
  stops sending the rest rather than this process parsing and dropping it.
