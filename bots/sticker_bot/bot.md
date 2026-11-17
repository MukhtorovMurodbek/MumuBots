# `bot.py`

[↑ sticker_bot](README.md) · [code](bot.py)

fstik-style sticker pack manager bot.

Part of a small bot family (see ARCHITECTURE.md): generic file conversion
(/convert, /mystars, /stars) lives in @ConvertBot, and Instagram/TikTok
downloading lives in @DownloaderBot. Each bot is its own process, its own
folder and its own deployment, with no shared files or file handoffs between
them -- so a clip that's too complex to compress into a video sticker just
dead-ends here with a pointer to @ConvertBot; the user re-sends it there.
The five do share one Postgres database, with a schema each; family_link.py
is the only code that touches anything outside this bot's own schema.

```text
Commands:
  /start        - greeting + menu (also handles co-edit share links)
  /newpack      - create a new sticker set
  /addsticker   - add to an existing pack
  /mypacks      - list the sender's packs; tap one for Add/Rename/Co-edit
  /import       - (while editing a pack) bulk-import from another Telegram
                  pack or a WhatsApp sticker pack .zip/.wastickers file
  /done         - optional: close the pack being edited and get its link
                  (every sticker is saved as it is added, so nothing asks for it)
  /cancel       - stop whatever the bot is waiting for
  /whomade      - look up who created a pack (packs made through this bot)
  /donate       - support hosting costs (voluntary)
  /en, /uz, /rus - switch language (English/Uzbek/Russian); also asked
                    once, trilingually, on first /start
```

Once a pack is open for "editing" (after /newpack, tapping Add on a pack, or
opening someone else's co-edit link), images, GIFs, videos, or static/video stickers are simply sent one after another -- each is added immediately with a
default 😭 emoji, and answered with a short reply of its own. Send emoji right
after a sticker to retag it, or reply with emoji to any sticker (or to the
bot's reply about it) to retag that one. Other commands work in the middle of
a session without ending it. In group chats see group_mode.py.
GIFs/videos are auto-converted (via ffmpeg) into Telegram's video-sticker
format; Lottie/.tgs animated stickers still aren't supported anywhere in
this bot family (needs a separate rendering pipeline neither bot has).

```text
Requires: python-telegram-bot[job-queue]>=21.3, Pillow>=10.0, ffmpeg on PATH
Env vars: SBOT_TOKEN, SBOT_USERNAME (no @), DATABASE_URL and DB_SCHEMA
          (the shared family database, and this bot's schema in it),
          SIBLING_BOTS (see shared_features.py). Every performance/cost knob
          has a working default and is documented in .env.example.
```

_At `try:`:_

main.py gives each bot its settings; this only reads a .env if one is present.

_At `BOT_USERNAME = (os.environ.get("SBOT_USERNAME") or "").strip().lstrip("@") or None`:_

Without the @, which is also taken off if it was written with one -- the
one-process edition always allowed it, and a link built from "@name" is broken.

_At `ADMIN_IDS = {int(x) for x in os.environ.get("SBOT_ADMIN_ID", "").split(",") if x.strip()}`:_

Operator-only admin tools (/whois, /messageas, /dbdump, /status) -- comma-separated
Telegram user ids, e.g. "111,222" for more than one account.
Empty/unset means those commands are disabled for everyone.

_At `LOCAL_BOT_API_URL = os.environ.get("LOCAL_BOT_API_URL")`:_

Optional: point at a self-hosted Bot API server (https://github.com/tdlib/telegram-bot-api)
instead of Telegram's cloud one. This is what actually breaks the 20 MB download / 50 MB
upload ceiling -- a local server raises those to (practically) unlimited download and
~1.9 GB upload. See https://github.com/tdlib/telegram-bot-api for setup.

- `LOCAL_BOT_API_URL = os.environ.get("LOCAL_BOT_API_URL")` — e.g. "http://localhost:8081"

_At `BOT_NAME = "stickerbot"`:_

This bot's id within the family (matches a key in SIBLING_BOTS, see shared_features.py)

_At `POLL_TIMEOUT = int(os.environ.get("POLL_TIMEOUT", "30"))`:_

Telegram's long-poll window. Higher means fewer requests for identical
latency; 30s is comfortably inside every proxy's idle timeout.

_At `PACK_TITLE_MAX = 64`:_

The most characters Telegram takes in a pack's title; a longer one is cut.

_At `TITLE_REFRESH_MAX = 50`:_

/mypacks asks Telegram for the current titles of at most this many packs, all
at once, and stops waiting after this many seconds.

### `build_help_text()`

The short version unless asked for the long one -- the family's help
template (shared_features, "/help in two lengths"). Both end with the
sibling-bots pointer; the per-command pages (HELP_PAGES below) are the
buttons under the help, not a line in it.

_At `def _help_page_newpack(lang: str) -> str:`:_

---------- /help &lt;name> and the buttons under /help: a page for each command ----------
The mechanism is in shared_features ("A page for each command"). Every
number on a page is read from the module that owns it when the page is shown
-- by attribute, never copied -- so a page says what the code does now.

_At `HELP_LABELS = {"groups": "help_btn_groups"}`:_

There is no /groups command, so its button says what it is rather than "/groups".

_At `BOT_COMMANDS = [`:_

Public command menu (the "/" button in Telegram's chat UI) -- set on
startup via set_my_commands() below instead of pasting into @BotFather by
hand. Operator-only admin commands (/whois, /messageas, /dbdump) are
deliberately left off -- no reason to advertise them to every user.
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

_At `ADMIN_COMMANDS = [`:_

The same menu, in the operator's own chat, with the commands only the operator can run. Kept out of BOT_COMMANDS on purpose -- a stranger should not be offered
/dbdump -- but hidden from the operator too, which was the accident. See
publish_commands() in shared_features.py. English, like the rest of the
admin output: the only person who sees this list wrote the bot.

_At `async def reply(update: Update, text: str, **kwargs) -> LiveMessage:`:_

---------- small helpers ----------

### `reply()`

Works whether the update came from a command or a button tap. A
button tap evolves the tapped message in place instead of sending a new
one -- so menu navigation (start -> packs -> a pack -> ...) reuses a
single message, fstik/BotFather-style, rather than piling up a fresh one
per tap. A command has no prior bot message to reuse, so it always sends
fresh.

### `slugify()`

Telegram sticker-set names must start with a \*letter\* (not a digit or
underscore) -- a title like "2007" would otherwise slugify to "2007",
which Telegram rejects outright with 'Invalid sticker set name is
specified'. Prefixing with a letter when needed avoids that.

Stripping stray underscores happens both before \*and\* after truncating
to 30 chars -- truncating mid-run of underscores (e.g. a long title
with a space/symbol landing right at the cutoff) can otherwise leave a
trailing "\_" that collides with the "\_" separator appended after this
in bot.py, producing "\_\_" in the final name, which Telegram also rejects.

### `help_rows()`

New pack and Packs: under /help, and under /start with the
sibling bots after them. The Help button that used to be here is the
family's full-guide button now (shared_features.help_keyboard).

### `packs_keyboard()`

One button per pack (tap opens its menu), fStik-style. Packs shared by somebody else come after the person's own, marked 👥, and a tap opens them for adding straight away: their
menu (rename, sharing, deleting) is their creator's.

### `pack_detail_keyboard()`

- At `[InlineKeyboardButton(i18n.t(lang, "btn_pack_public" if public else "btn_pack_private"),`:
  Private (the default) or public in groups -- see group_mode.py.

### `_status_text()`

- At `text = i18n.t(lang, "status_line_open", verb=verb, title=title)`:
  No running count since 2.2.0: each sticker is answered on its own (sticker_ops), so
  this message is only refreshed for imports and stays where it was otherwise.
- At `text = f"{text}\n{intro}"`:
  The pack's name first (2026-11-13: tapping a pack should answer "Editing
  {pack}"), then what to do in it.

_At `STATUS_KEY = "status"`:_

The one message that opens an editing session and carries the long notes
(an import's progress and summary). Until 2.2.0 it was the only thing the
bot said while a pack was open, every "added ✅" folded into it; several pictures sent quickly were found to read as if only one had been added,
so each sticker now gets a short reply of its own (\_add_and_confirm), which
is also what a later emoji reply points at.

When the user does speak over it -- another photo, a stray "ok" -- the
handle notices and moves the whole status down to the bottom as a new
message instead of rewriting one they have scrolled past. See
live_message.py.

### `_start_status()`

Sets up the session's status message. `send_kwargs` go with a new one
(the pack buttons, say).

When `adopt` is a saved handle (typically the prompt that led here), that
message becomes the status display instead of a new one being sent -- so
"What should the pack title be?" turns into "📝 Creating "X" -- 0
sticker(s)..." in place.

- At `if attempt == 1 and "ConnectError" in str(exc):`:
  No connection was made (TestBot, 2026-11-13: "httpx.ConnectError" at the TLS
  handshake), so nothing was sent and sending again cannot double it. Once.

_At `async def _refresh_status(context: ContextTypes.DEFAULT_TYPE, lang: str, note: str = ""):`:_

Anything else goes up to error_handler, which tells the person (FM-CRASH): a pack that opened
without a word is what happened on TestBot, 2026-11-13 (left on "seen").

### `_refresh_status()`

- At `chat_id = getattr(context, "_chat_id", None)`:
  No status message to put it in (it could not be sent, or the session's state is
  gone): a failure -- a note ending in a problem code -- still reaches the person, as a
  message of its own. Progress lines would only be noise.
- At `context.user_data[STATUS_KEY] = live.save()`:
  Saved back because set() may have moved it: a status the user talked
  over is re-sent at the bottom, under a new message id.

### `_owns_pack_or_deny()`

True if user_id owns pack_name. Packs can only be managed (renamed,
shared, or opened via the /mypacks menu) by their creator -- co-editors
get the editing session only (adding, and taking a sticker out by
sending it again), through a valid share link or an invitation of their
@username.

Async because it is a database round trip, and this runs on the first
line of six different button handlers: doing it inline would stall every
other user's update for the duration.

- `return False` — a button for a pack that is not theirs, or not there any more (\_pack_of)

_At `async def whomade_command(update: Update, context: ContextTypes.DEFAULT_TYPE):`:_

---------- /whomade ----------

_At `def _is_admin(user_id: int) -> bool:`:_

---------- operator-only admin tools ----------

### `_deny()`

How an operator-only command answers everybody else: exactly the way a
misspelling does.

The intent was always to avoid confirming the command exists, and a bare
`return` looked like the way to do that. It is not: a bot that answers
every command it has and ignores precisely one has just pointed at the
interesting one, and to the person who typed it the bot simply looks
broken. Giving back the same sentence a typo gets makes the operator-only commands indistinguishable from commands that were never there.

### `whois_command()`

/whois &lt;user_id> -- looks up a Telegram user's profile (name,
username, bio) plus any packs of theirs on record, so a numeric id
(e.g. from /whomade or the packs table) can be turned back into "which acquaintance is this". Operator-only: this surfaces info about other
people, not something every user should be able to query.

### `messageas_command()`

/messageas &lt;user_id> &lt;text> -- sends a message to that user as this
bot. Only works if the user has messaged the bot before (Telegram
doesn't let bots cold-message anyone). Operator-only for obvious reasons.

### `dbdump_command()`

/dbdump -- exports every table in this bot's own database as one
zip of CSVs. Operator-only: this is the whole user table, not something to
hand out on request.

### `status_command()`

/status -- uptime, hosting environment, any crashes since this
process started, and active-user counts. Operator-only, same reasoning as
/dbdump: this is operational info, not something every user should see.

### `crashtest_command()`

/crashtest -- operator-only, deliberately raises to confirm that
/status's error tracking + logs/errors.log actually catch something
without needing a real bug. PTB's own dispatcher catches whatever this
raises and routes it to error_handler (shared_features.py) -- polling
keeps running either way. Safe to leave in permanently (gated same as
every other admin command), or removed once testing is done.

_At `async def _claim_invites(update: Update, context: ContextTypes.DEFAULT_TYPE, lang: str) …`:_

---------- /start and menu buttons ----------

### `_claim_invites()`

Packs whose creator invited this person's @username: they co-edit them from now on,
and are told so once. Never in the way: a failure is a line in the log.

_At `def pack_ref(pack_name: str) -> str:`:_

---------- pack references in buttons (2.1.6) ----------
A pack's name is its title's slug, a random part and "\_by\_&lt;bot username>": up to 61 characters
with mumu_sticker_bot, and more with a longer username. A button carries at most 64 bytes, so
"packdelconfirm1:&lt;name>" could not be sent at all, and the message carrying it was lost without a
word (TestBot, 2026-11-14: Co-edit and Delete did nothing). Buttons carry pack_ref instead; a
full name, on a button drawn before, still works (\_pack_of).

### `_pack_of()`

The pack a button means, among this person's own packs and the ones shared with them: by
its reference, or by its full name. None when it is neither (any more) -- and every handler
then refuses it, as for somebody else's pack.

_At `PACK_BUTTON = "📦 "`:_

---------- pack buttons under the message box (2.1.6) ----------
2026-11-13: a menu of buttons for each pack. Two a pack: "✏️ Edit Memes"
opens it for adding ("✏️ Editing “Memes”"), so the next picture goes straight in, and "👁 View
Memes" sends its link; "➕ New pack" under them. Private chats only. The buttons are worked out
again from the person's packs on every tap, so they need no state: the same titles in the same
order give the same labels (a repeated title gets " · 2", counted oldest first, so a new pack
does not renumber the old ones). They ride along on the bot's plain answers (\_with_buttons,
\_reply_to_item), so they come back by themselves if anything took them away -- on TestBot, the button menu had disappeared after a while of use.

_At `KEEP_ACROSS_SESSIONS = ("lang", "_last_seen", HELD_KEY, HELD_OFFER_KEY, KB_TITLES)`:_

What a new session keeps (\_fresh_session): the language; \_last_seen, without which the person
looks gone to the soft stop's count and to the hourly prune (which dropped a session opened a
minute before it ran); what is held for a pack not yet picked; and the labels on the keyboard
last sent, so a button still works after its pack is renamed.

### `_fresh_session()`

user_data cleared for a new session -- but KEEP_ACROSS_SESSIONS stays, and `keep` on top.

### `_pack_labels()`

(button label, pack name) for each pack, in the order given (newest first).

### `pack_reply_keyboard()`

Two buttons a pack -- "✏️ Edit Memes" opens it for adding, "👁 View Memes" shows it -- and
"➕ New pack".

### `_plain()`

Text with emoji variation selectors dropped: "✏️" and "✏" compare equal, whichever a
client sends.

### `_pack_button_action()`

("edit" | "view", pack name) for a pack button's text, in any language -- the buttons stay
readable when the person changes language before the keyboard is redrawn.

- At `labels += [(label, name) for name, label in (remembered or {}).items() if name in owned]`:
  the labels on the keyboard last sent, for a pack renamed since (still one of theirs)

### `_LooksLikePackButton`

Text shaped like a pack button -- "✏️ Edit …" / "👁 View …" in any language -- and nothing
else. A bare "^(✏|👁)" took emoji such as "👁" or "✏️" (and a pack title starting with one)
for a button: no retag, no title, a "pack not found" answer (review of 2026-11-14).

### `_current_titles()`

`packs` with the titles Telegram has now (\_refresh_pack_titles, which saves them), and an
open pack's title following its own: a pack can be renamed in Telegram's own sticker
settings, which the bot is never told about (TestBot, 2026-11-13).

- At `logger.debug("Couldn't read the packs' titles from Telegram", exc_info=True)`:
  Telegram not answering the way it should: the stored titles will do this time.

### `_pack_keyboard_for()`

The pack buttons as they are now (`sync`: titles read from Telegram first). The labels sent
are kept (KB_TITLES), so a button still works after its pack is renamed.

### `_with_buttons()`

reply_markup for a plain answer in a private chat: the pack buttons. Never on a message
that is edited later -- an edit cannot carry them.

### `_open_pack()`

The pack is open for adding: "✏️ Editing “Memes”", and anything held goes in.

- At `same = context.user_data.get("target_pack") == pack_name`:
  Opening the pack that is already open keeps its last sticker, so plain emoji still retag it
  (TestBot, 2026-11-13: the button tapped mid-session, then emoji -> "Add a sticker first").

### `pack_button()`

A tap on "✏️ Edit Memes" or "👁 View Memes" under the message box.

- At `await update.message.reply_text(`:
  One tap on the link opens the pack itself; whatever session is open stays open.

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

A deep link (/start s\_&lt;token>, a co-edit invite) still goes straight
where it points, and a brand-new user who arrives on one picks a
language first and is carried there afterwards by pending_start_args.

- At `if _has_status(context) and lang is not None:`:
  Since 2.2.0 a bare /start in the middle of a pack shows the menu and
  leaves the pack open (anything else should be possible while new stickers are being added). Returning None keeps the
  conversation in the state it was in; the next picture still goes in.

### `language_command()`

/language -- the picker on demand, which is what /start used to be.

Same trilingual greeting and same three buttons, with a tick on the
language in force so a returning user can see which one they are on
before deciding to change it. The tap that follows runs through
\_apply_language like /en, /uz and /rus do, and so ends where they end:
at the instructions, in the language just chosen.

Deliberately \*not\* a conversation entry point. Asking to see the picker
is not a reason to tear down a pack someone is halfway through editing;
the tap that actually changes language is an entry point, because the
instructions it prints have to leave the state machine at the top level.

- At `if lang:`:
  Keep the cached language warm even though the picker itself is
  trilingual -- the next handler this user hits would otherwise pay for
  a database read that /language had already done.

### `_apply_language()`

The single path every language change goes through -- /en, /uz, /rus
and a tap on the picker alike. Two things all of them have to do, which
is why they share this:

```text
1. Show the instructions. Answering a language change with nothing but
   "Language set" leaves someone looking at a bot whose manual they have
   just made themselves unable to reach; printing the full help in the
   language they picked is the point of having picked it.
2. Leave the user at the top level. This used to strand people: change
   language halfway through /newpack and the pack-editing conversation
   stayed open behind the start menu that appeared, so /newpack got
   the unknown-command answer and the New pack button did nothing
   at all. Clearing user_data and returning END puts the state machine
   where the screen says it is.
```

pending_start_args is read back before the clear so a brand-new user who
arrived on a co-edit link still lands in that pack once they have picked
a language.

- At `await refresh_chat_menu(context, update.effective_user.id, lang)`:
  The menu follows the choice too -- Telegram otherwise shows it in the
  language of the phone. After the clear, so the signature survives it.

### `language_chosen()`

A tap on the picker. Registered as a conversation entry point as well
as a plain handler, because the very first thing a brand-new user does
can be opening someone's co-edit link -- and that has to be able to put
them straight into an editing session. Being an entry point is also what
lets the END \_apply_language returns actually close a conversation the
tap interrupted: a plain handler's return value is thrown away.

### `menu_start()`

'⬅️ Back' from deeper in the menu -- edits back to the start view
instead of leaving a trail of old menu messages behind.

### `start_coedit_link()`

Handles /start s\_&lt;token> -- someone opening a co-edit share link.

Replies through reply() rather than update.message, because a first-time
user reaches this by tapping a language button, and a button tap has no
message of its own to reply to.

- At `outcome, pack_name = await asyncio.to_thread(join_by_token, token, user.id)`:
  The link's own limits (2026-10-28): how many editors it lets in, and until
  when. Somebody already in comes back in whatever the link's state.

### `menu_help()`

The Help button on a start menu sent before 2.1.0: the full guide.

### `help_command()`

/help is the short version; /help full is the long one.

### `show_packs()`

- At `changed = await _refresh_pack_titles(context.bot, (list(packs) + list(shared))[:TITLE_RE …`:
  Shown first from what is stored, then corrected in place if a title has
  changed. Inline rather than in a task: updates from one person run one at
  a time, so a tap on the list cannot be overwritten by this edit.

### `_refresh_pack_titles()`

The titles Telegram has now, for the (name, title) pairs given; saves and
returns the ones that differ. A pack can be renamed outside this bot (the
"edit" in Telegram's sticker panel, @Stickers), and the stored title used to
stay the old one for good. A pack Telegram can't find, or an answer that
doesn't come in time, leaves the stored title as it is.

_At `async def pack_detail(update: Update, context: ContextTypes.DEFAULT_TYPE):`:_

---------- pack detail menu (Add / Rename / Co-edit) ----------

### `pack_public_toggle()`

The 🔒/🌐 button: a pack private (only its creator and co-editors add to it) or
public (also anybody in a group its creator or a co-editor uses the bot in). Creator only.

_At `CO_PREFIX = "co:"`:_

---------- co-editing menu ----------

_At `CO_PREFIX = "co:"`:_

2026-10-28: a link may be shared, or leaked, to too large a group, so a link comes with options:
a maximum of editors (10 by default, the creator not counted), editors added by Telegram
username (by hand), and an activity period (1 week by default). So the screen shows the link
with its limits, and buttons that cycle each (db.MAX_EDITORS_CHOICES, db.VALID_DAYS_CHOICES),
invite people by @username, and list everybody with a ✖ each. And the same day: the creator
picks whether the link grants delete rights -- the 🗑 switch, off by default, lets every
co-editor of the pack delete stickers.

Its buttons carry the pack's number ("co:&lt;action>:&lt;id>"): a pack's name can be 48
characters, and a button's data 64 bytes. The cotoken_reset:&lt;name> buttons of messages sent
before this still work.

### `_send_coedit_message()`

- At `view = await asyncio.to_thread(get_coedit_view, pack_name)`:
  One trip to the database for everything the screen shows (get_coedit_view).

### `_editor_name()`

How the creator's list names a co-editor: their name as Telegram gives it now, or
their number when Telegram will not say.

_At `CO_LIST_MAX = 30`:_

The list shows this many people at most; the rest are summed up in a line.

_At `INVITE_MARK_CHARS = 16`:_

How much of an invitation's mark (64 hex characters) its ✖ button carries.

### `_send_coedit_list()`

- At `names = await asyncio.gather(*(_editor_name(bot, user_id) for user_id in editors[:CO_LIS …`:
  Asked of Telegram all at once: one at a time, thirty names took seconds to draw.
- At `rows.append([InlineKeyboardButton(f"✖ @{username}",`:
  The start of the invitation's mark, not its place in the list: the list can change
  (somebody takes up an invitation) between drawing it and the tap.

### `_co_pack()`

(pack name, pack number, action, the rest of the button's data) for a co-edit button
whose pack the tapper owns, or None once they have been told why not.

### `coedit_button()`

Every button of the co-edit screen and its list, except "➕ Add by @username"
(invite_start), which waits for typing and so belongs to the conversation.

_At `USERNAME_RE = re.compile(r"^@?([A-Za-z][A-Za-z0-9_]{4,31})$")`:_

A Telegram username: 5 to 32 letters, digits and underscores, starting with a letter.

### `invite_start()`

"➕ Add by @username": asks for the names, as a message. An open pack stays open
behind the question, as behind a rename.

_At `async def rename_start(update: Update, context: ContextTypes.DEFAULT_TYPE):`:_

---------- rename ----------

### `rename_start()`

- `return None` — a pack being filled stays open, as behind a rename that goes ahead
- At `session = _session_of(context)`:
  A pack being filled stays open behind the rename (receive_new_title goes
  back to it); anything else that was waiting is dropped, as before.
- At `prompt = await edit_in_place(`:
  The new title arrives as a plain text message, not a button tap --
  stashing this prompt's handle lets receive_new_title turn it into the
  result instead of sending a separate reply. By then the user's own
  title message is underneath it, so the handle will (correctly) send a
  new message rather than rewrite one they have already scrolled past.

### `receive_new_title()`

- `new_title = update.message.text.strip()[:PACK_TITLE_MAX]` — Telegram title limit

_At `async def delete_pack_start(update: Update, context: ContextTypes.DEFAULT_TYPE):`:_

---------- delete pack (creator-only, two taps to confirm) ----------

### `delete_pack_start()`

First tap of 'Delete pack' -- creator-only (co-editors never even see
this button, since it only appears in the /mypacks detail menu, but the
check is repeated here too since callback_data can be replayed).

### `delete_pack_confirm1()`

Second tap -- one more explicit confirmation before anything actually happens.

### `delete_pack_confirm2()`

Third tap -- actually deletes the set from Telegram and wipes local records.

- At `if not await _owns_pack_or_deny(pack_name, user.id):`:
  Awaited since 2.2.0: without it the coroutine itself was the answer, always truthy, so
  this check never refused anybody -- a forged button could reach the delete below.
- At `await asyncio.to_thread(delete_pack_records, pack_name)`:
  Telegram already has no record of it (e.g. auto-deleted earlier
  after its last sticker was removed) -- just clean up the bot's side.

_At `async def newpack_start(update: Update, context: ContextTypes.DEFAULT_TYPE):`:_

---------- /newpack (and "New pack" button) ----------

### `newpack_start()`

- At `context.user_data["title_prompt"] = msg.save()`:
  So receive_title can turn THIS message into the status display below,
  instead of sending a separate one once the title arrives -- assuming
  it is still the last thing in the chat by then, which is exactly what
  the handle is for.

### `receive_title()`

- `if len(title) > PACK_TITLE_MAX:` — Telegram's hard limit on set titles

_At `async def addsticker_command(update: Update, context: ContextTypes.DEFAULT_TYPE):`:_

---------- /addsticker (and per-pack "Add" button) ----------

### `pack_chosen()`

- At `await query.answer(i18n.t(lang, "not_your_pack"), show_alert=True)`:
  None, not END: a refused tap (a 👥 pack whose creator has since removed this person) leaves an open pack as it was.
- At `deletes = owner_id == user.id or access["can_delete"]`:
  The tip says what sending a sticker that is already in it does for this person:
  take it out (the creator, or a co-editor allowed to delete), or ask about adding it again.

_At `async def _add_input_sticker(`:_

---------- the editing loop ----------

### `_add_input_sticker()`

Creates the pack (first sticker of a /newpack session) or adds to the
existing target pack. Returns the newly-added sticker's file_id and its
place in the pack, or (None, None) with want_file_id=False.

Always acts on Telegram's behalf of the pack's \*creator\* (owner_id), even
if a co-editor is the one physically sending the sticker through the
bot -- Telegram's API ties set ownership to that id, not the caller.

That file_id costs an extra getStickerSet call -- the API has no way to
return it from the add itself -- and it exists only so the next message
can retag the sticker with different emoji. During a bulk import nobody
is going to retag sticker #37 of 100, so want_file_id=False skips a
hundred round trips and the whole set is read once at the end instead.

- At `for attempt in range(1, sticker_ops.NAME_TRIES + 1):`:
  A random suffix (not the creator's Telegram user id) keeps the pack
  name -- and therefore the public https://t.me/addstickers/... link
  everyone sees -- from doubling as a permanent, unrevokable pointer
  to the creator's numeric user id. Ownership is still tracked
  server-side via add_pack()/get_pack_owner(), so nothing else here
  depends on what's actually in the name.
- At `try:`:
  Uploading a sticker (especially a converted video one) can take
  longer than PTB's default 5s read/write timeout, which was
  showing up as a spurious "Timed out" failure even when the
  upload was going through fine -- just slowly.
- At `if attempt == sticker_ops.NAME_TRIES or not sticker_ops.name_refused(exc):`:
  "Shortname_occupy_failed" (2026-10-29): another random name usually works.
- `return file_id, number or 1` — a new pack holds this one sticker

### `NoLongerEditor`

The pack's creator removed this co-editor while their session was open.

_At `ACCESS_RECHECK_SECONDS = 60`:_

How long a co-editor's access, once checked, is trusted within a session: a bulk import
adds up to a hundred stickers, and one question a minute is enough to stop a removed one.

### `_session_of()`

The keys of an open editing session, to carry it past something else
(a rename) that starts from a clean user_data. Empty when none is open.

### `_behind_question()`

`handler` for a picture sent while a rename or an invitation waits for typing. With a
pack open behind the question (since 2.2.0 it stays open there), the picture goes into the
pack and the question keeps waiting; with none open, it is held and the person asked which
pack (\_hold_item), as anywhere else since 2.1.6.

#### `_behind_question.run()`

- At `await _hold_item(context, await i18n.get_lang(update.effective_user.id, context), item)`:
  Since 2.1.6 a picture with no pack open is held, and the person asked which pack.

_At `BATCH_MEDIA = (filters.PHOTO | filters.Document.IMAGE | filters.Sticker.STATIC | filters …`:_

---------- a run of pictures ----------
Several pictures at once (an album, or a quick run of pictures, videos or stickers): each reply
also counts the run -- "⏳ 5/7" while more are waiting, then "✅ 7/7", or "⚠️ 7/7 — 1 not added"
-- so that nobody reads the replies so far as all the bot saw. 2.1.4's status message did this
(2026-11-07: several jobs queued in one chat are shown in the message, and "5/7" is enough); since 2.2.0 each sticker has a reply of its own, so the
count rides on those. The waiting pictures are this person's updates queued behind the one being
made (shared_features.PerUserUpdateProcessor), or, with that switched off, python-telegram-bot's
own queue, as in 2.1.4.

### `_queued_items()`

Pictures, videos and stickers from this chat waiting behind the one being made.

### `_run_line()`

This item's place in its run: "⏳ 5/7" while more wait; at the last one "✅ 7/7", or how
many were not added; "" outside a run of several.

### `_with_run()`

A reply with its run line: after a confirmation, before an error, whose problem code stays
last, where the Report button looks for it.

### `_batch_item()`

One picture's handler inside its run: the first of a run opens it, and the run ends with the
one after which nothing from this chat waits.

### `_add_and_confirm()`

Add one sticker to the session's pack and answer that one item: added as #N, or
why not. Both the person's message and the answer are remembered as pointing at the
new sticker, so a reply to either with emoji retags it (sticker_ops). `fit`: what was
done to a clip to make it fit (\_offer_video_fit), said in the answer.

- At `seen = sticker_ops.seen_title(pack_name)`:
  The title Telegram gave when the pack was read back: one renamed in Telegram's own
  sticker settings is called by its new name from the next sticker on (TestBot, 2026-11-13).
- At `text = sticker_ops.added_text(lang, title, number, DEFAULT_EMOJI, how=_fit_label(fit, la …`:
  Named every time: with nothing asking for /done any more, a pack can stay open for
  days, and the reply is where the person sees which one a picture went into. The
  first sticker of a new pack also carries its link -- what /done used to be for.
- At `context.user_data["nudge_counted"] = True`:
  One "finished something" per session for the donation cadence, as /done was
  until 2.2.0 (shared_features: not shown every time).

### `_remember_last_sticker()`

Run once after a bulk import so "send emoji to retag" still points at
the last sticker added -- one getStickerSet instead of one per sticker.

### `_is_own_pack_sticker()`

True if an incoming sticker message is a sticker that's still
actually in the pack currently being edited (as opposed to a fresh
image, or a sticker from some \*other\* pack, which still gets added
normally).

`sticker.set_name` alone isn't enough to decide this: it reflects
whatever set the message's \*sender\* last saw the sticker belong to, and
a sender's sticker panel can lag behind a removal the bot itself just
made in this same session (e.g. remove it, then immediately re-send the
same sticker from their panel before Telegram's client has refreshed
it). Trusting set_name alone in that case would try to delete an
already-deleted sticker and fail with STICKERSET_NOT_MODIFIED instead of
doing what the user clearly wants: re-add it. So once set_name points at the pack being filled, confirm against the pack's actual current contents (sticker_ops.in_pack, which
group chats use too).

### `_sticker_already_in_pack()`

A sticker sent that is already in the pack being filled: asked about, never acted on at
once (group_mode.ask_add_again, where the request behind it is). Whoever may delete -- the creator, or a co-editor the creator allows (the co-edit screen's 🗑) -- chooses between
adding it again and removing it; anybody else between adding it again or not, and is told
that only the creator deletes. Until 2.2.0 it was removed at once, whoever sent it.

- `context.user_data["batch_handled"] = True` — in a run, an answer of its own: not a failure

### `already_in_pack_chosen()`

A tap on that question: add it again (y), don't (n), remove it (r) -- and, for the
pack's last sticker, the creator's "delete the whole pack?" (R) or "keep it" (c). Only by
whoever was asked, once. A conversation entry point as well as a plain handler, like
cancel_choice_callback: deleting the pack this way has to be able to close the session
that was filling it. Returns None (the conversation stays as it was) otherwise.

### `_remove_asked()`

"Remove it": the right to delete is checked again (the creator may have switched it off since). The pack's last sticker takes the whole pack with it -- Telegram deletes an empty
set -- so the creator is asked once more, and a co-editor is never let do it.

- `await asyncio.to_thread(delete_pack_records, pack_name)` — Telegram auto-deleted the empty set

### `confirm_remove_last_sticker()`

The "delete the whole pack?" buttons sent before 2.2.0, still answered for messages already
in people's chats; since then the question is already_in_pack_chosen's.

- `await asyncio.to_thread(delete_pack_records, target_pack)` — Telegram auto-deleted the empty set

### `receive_media()`

Static images / static stickers -> PNG sticker, counted in a run of several (\_batch_item).

### `_receive_media()`

Static images / static stickers -> PNG sticker.

- `item["kind"] = "static"` — what this handler's filter took it for

### `receive_video_media()`

GIFs / videos / video stickers -> WEBM video sticker, counted in a run of several (\_batch_item).

### `_receive_video_media()`

GIFs / videos / video stickers -> WEBM video sticker (via ffmpeg).

_At `def _item_of(msg) -> "dict | None":`:_

---------- items: one picture, clip or sticker the person sent (2.1.6) ----------
What a message holds is turned into a plain dict before anything is done with it, because it may
have to wait: held, with no pack open (below), or for the person's choice on a clip that does not
fit (\_offer_video_fit) -- and what waits in user_data has to be JSON. Every item is answered as a
reply to its own message, wherever the chat has got to since.

### `_item_of()`

chat, msg (its message id), kind ("static" | "video"), file_id, set and uid (a sticker's own
pack and unique id, to notice one already in the pack), at (when it came). None for anything
that cannot become a sticker, an animated sticker included (sticker_ops.media_of).

### `_reply_to_item()`

A new message answering `item`, as a reply to it. In a private chat the pack buttons ride
along -- unless `buttons` is False: an answer that is edited later ("Converting…") cannot keep
them -- and never on an answer that carries a problem code, where the details button may have
to go (note_problem adds it only beside an inline keyboard or none).

### `_say()`

`working` (the "⏳ Converting..." reply, or the message holding a clip's choices) turned
into `text`, or a new reply to the item when there is none -- or when the person deleted it.
Returns the message that now says it.

### `_add_item()`

One item into the pack being filled, answered as a reply to it.

- At `context.user_data["batch_handled"] = True`:
  Sent with no pack open, then this pack picked for it: that is no request to take it
  out (2.1.6's review found a held sticker deleted from the pack picked for it).

### `_add_static_item()`

Static images / static stickers -> PNG sticker.

### `_add_video_item()`

GIFs / videos / video stickers -> WEBM video sticker (via ffmpeg).

- At `refusal = await refuse_new_work(lang, update.effective_user.id, item["chat"])`:
  An encode takes longer than the platform's shutdown grace period, so
  starting one into a landing update would only end in silence. Saying so
  costs the user a few seconds; not saying so costs them the file.
- At `logger.warning("Couldn't fetch a clip to make a sticker of", exc_info=True)`:
  Tried again already (sticker_ops.telegram_call): a note, not the crash reply.
- At `if await _offer_video_fit(context, lang, item, None, {}, "long", duration):`:
  Not cut short any more (2026-11-13: a GIF "got cut down somehow"): the person
  picks a speed, or trims it themselves. Waiting on a choice is not a failure in the run's
  count; refused (no speed fits) it is.

### `_make_video_sticker()`

Encodes the clip with the person's choices (`fit`) and adds it. Its answer -- "Converting…",
then the result -- is one reply beside the clip: `answer` when there already is one (the message
that held the last choices), a new one otherwise. A clip still too heavy gets the size choices
there.

- At `record_error(exc)`:
  The raw clip itself is still fine, so point at @ConvertBot for a plain (non-sticker)
  conversion instead of just dead-ending here; the person re-sends the file over there.

### `_needs_session()`

A handler of an open pack session. The session's state can be gone while the conversation
is still open -- dropped after USER_DATA_TTL_HOURS idle, or lost in a restart. A picture or clip
is then held and the person asked which pack it is for (\_hold_item); anything else is told the
session closed. Either way the conversation ends instead of failing on a missing key.

_At `HOLD_SECONDS = int(os.environ.get("SBOT_HOLD_SECONDS", "600"))`:_

---------- held items: sent with no pack open (2.1.6) ----------
2026-11-13: media sent without a pack picked first is held for 5 to 10 minutes, with buttons offering the packs. Someone who thinks
a pack is still open sends pictures; the bot has no pack for them (none was picked, or the
session was dropped). They are held for HOLD_SECONDS and the person is asked which pack they go
to -- one question for a whole album, its count kept up to date.

### `_hold_item()`

- At `await _reply_to_item(context, item, i18n.t(lang, "held_full", n=HOLD_MAX))`:
  Never dropped without a word (2.1.6's review found the oldest silently lost past 30).
- `packs = await _current_titles(context, packs[:PACK_BUTTONS_MAX])` — a new question: names as Telegram has them

### `media_without_pack()`

A picture, clip or sticker in a private chat with no pack open: held, and the person asked
which pack.

### `held_pack_chosen()`

A pack tapped under the held-items question: that pack opens, and what was held goes in.

### `held_new_pack()`

"➕ New pack" under the held-items question: the usual title prompt; what was held goes in
once it is named (receive_title).

### `_add_held()`

Whatever was held, into the pack that was just opened, each answered beside itself.

- At `logger.exception("A held item could not be added")`:
  One item failing must not stop the rest -- and is never left without an answer.

_At `VIDEO_FIT_KEY = "video_fit"`:_

---------- a clip that does not fit a video sticker (2.1.6) ----------
2026-11-13: when adding a GIF fails, the person is told, with options: lower resolution (recommended), a lower frame rate, a speed-up, or trimming. Too long (over
MAX_DURATION_S): speeds that fit it, never above video_sticker.MAX_SPEED. Too heavy (every
attempt over MAX_BYTES): lower resolution (recommended) or a lower frame rate. Trimming is the
person's to do, for now. The choice waits in user_data under a short token, since callback_data
holds 64 bytes; the newest VIDEO_FIT_KEEP are kept.

### `_offer_video_fit()`

Says why the clip does not fit and offers what would make it fit -- as a reply to the clip,
or in place of the earlier answer to it (`edit`). True when there was something to offer.

### `video_fit_stale()`

A fit button tapped once its pack session is over (/done, /cancel, a lost session).

### `video_fit_chosen()`

A tap on one of \_offer_video_fit's buttons.

### `_fit_label()`

What was done to make it fit, for the line that says it was added: "2.2×, lower resolution".

_At `async def import_command(update: Update, context: ContextTypes.DEFAULT_TYPE):`:_

---------- bulk import (Telegram pack or WhatsApp zip) ----------

### `import_command()`

/import &lt;link or name> -- copies stickers from another public
Telegram pack into the pack currently being edited. Re-uses each source
sticker's file_id directly, so quality and emoji tags carry over as-is.

- At `refusal = await refuse_new_work(lang, update.effective_user.id, update.effective_chat.id)`:
  A bulk import is the longest thing this bot does -- dozens of stickers
  added one at a time, paced to stay under Telegram's rate limit -- and it
  was the one long path with no check that an update was coming. Starting
  one into a landing deploy leaves a half-filled pack and no explanation.
- At `await _refresh_status(context, lang, i18n.t(lang, "import_fetching", source=pack_source))`:
  Progress and result both land in the session's status message -- see
  \_start_status. A bulk import is the longest thing this bot does, so it
  is also the one most likely to have the user typing over it; the
  handle moves the whole report down to the bottom when they do.
- `await asyncio.sleep(0.3)` — be gentle with Telegram's API during bulk adds

### `import_standalone()`

Top-level /import fallback for when the user isn't currently editing a pack.

### `done_standalone()`

Same idea for /done. Both commands sit in Telegram's command menu, so
both are one tap away at any moment, including when there is no editing
session for them to act on -- answering that with the unknown-command reply is the bot disowning something it is itself advertising.

### `receive_whatsapp_import()`

A .zip/.wastickers file sent while editing -- treated as a WhatsApp
sticker pack export and bulk-imported.

- At `refusal = await refuse_new_work(lang, update.effective_user.id, msg.chat_id)`:
  Same reasoning as /import, which this is the other half of: a zip of
  stickers added one by one outlasts a shutdown grace period easily.

### `maybe_emoji_override()`

- At `return EDITING`:
  Nothing is asked for here since 2.2.0. It used to answer "Send an
  image/GIF/video/sticker to add, emoji to retag the last one, or
  /done", and it was removed: anything else should be possible while new stickers are being added to a pack.
- At `await update.message.reply_text(i18n.t(lang, "emoji_target_unknown"), **buttons)`:
  A reply with emoji whose sticker emoji_reply could not find (the bot
  restarted since, or it is not a sticker of a pack made here):
  retagging the \*last\* sticker instead would be the wrong one.

### `_EmojiReply`

A message of only emoji that replies to a sticker, or to a message remembered as
standing for one (sticker_ops.sticker_of).

### `emoji_reply()`

Emoji, as a reply to a sticker or to the bot's "Added" reply: retag that sticker. In
the middle of a session or not -- it is registered ahead of the conversation, and leaves
its state where it was.

### `emoji_command()`

/emoji 😎 -- as a reply, that sticker; otherwise, in a session, the last one added.
/emoji alone as a reply asks for the emoji (group_mode.ask_for_emoji).

### `done()`

- At `_fresh_session(context)`:
  Nothing asks for /done since 2.2.0 -- every sticker is saved as it is added, and the
  first one's reply carries the link -- so the donation checkpoint moved to the first
  sticker of a session (\_add_and_confirm). This only closes the pack and sums it up.

### `convert_redirect_command()`

/convert lives in @ConvertBot now -- keep the command here as a
friendly redirect for anyone still typing it out of habit.

_At `PACK_CANCEL_KEYS = ("rename", "editing", "newpack", "invite")`:_

The three pack states, as opposed to the donation prompt: cancelling one
of these is what closes the ConversationHandler, and cancelling anything
else must not.

_At `OVER_PACK_KEYS = {"rename": ("rename_pack", "rename_prompt"), "invite": ("invite_pack", …`:_

Questions asked on top of an open pack (since 2.2.0 a rename or an invitation keeps it):
cancelling one of them goes back to the pack instead of closing it.

### `_cancel_items()`

Everything this bot could be waiting on, named well enough to choose
between. The three pack states are mutually exclusive by construction --
newpack_start and rename_start both clear user_data before setting their
own keys -- so at most one of them is ever on offer, and the real choice
this menu exists for is "the pack, or the donation prompt, or both".

### `_apply_cancel()`

Stop the chosen things; report what they were and whether a pack went
with them.

That second answer is what decides whether the conversation ends, and it
is the whole reason this is not one indiscriminate reset any more:
cancelling a donation prompt has no business closing an editing session
the user never mentioned. The reset that a pack cancellation does still
need is given back anything not chosen -- in practice the donation
prompt, the only other thing that can be live at the same time.

- At `for key in (STATUS_KEY, "sticker_count", "status_intro", *BATCH_KEYS):`:
  finish_cancel says what stopped; a second "❌ Cancelled." under it, in the
  status message, was noise (2.1.6's review). Its keys go with the session.

### `cancel_command()`

Asks which of the things it is waiting on to stop, and stops nothing
until the answer comes back.

"Cancelled." on its own used to leave a guess about which of several
half-finished things it had caught; v0.7.0 fixed that by naming them
afterwards. This is the other half: being asked first, so that a pack three stickers in is not collateral damage of wanting out of a
donation prompt.

Returns None while the question is on screen -- the conversation is still
open at that point, and it is the button tap that ends it, if the answer
turns out to be a pack. With nothing pending there is nothing to ask, so
this answers immediately, exactly as it always has.

- At `buttons = await _with_buttons(context, update.effective_chat, update.effective_user.id, …`:
  The pack buttons go back up with the answer: a bare removal took them away (TestBot,
  2026-11-13: "after using it for a while the button menu disappeared").

### `cancel_choice_callback()`

A tap on one of /cancel's buttons.

Registered as a conversation entry point as well as a plain handler, for
the same reason language_chosen is: a plain handler's return value is
thrown away, and this one has to be able to close the conversation the
/cancel it answers was sent from.

- At `if stopped and _has_status(context) and any(key in OVER_PACK_KEYS for key in keys):`:
  A rename or an invitation cancelled on top of an open pack: back to the pack. Cancelling
  only something else (the donation prompt) leaves the conversation where it was.

### `unrecognized_message()`

Only ever reached with no active /newpack or pack-editing session --
while one's active, the conversation's own state handlers (e.g.
maybe_emoji_override) claim the update first. Registered last, so
every real handler above gets first shot. Emoji with no pack open is somebody
trying to change a sticker's emoji, and is told how.

### `_post_init()`

- At `await lifecycle.on_start(BOT_NAME)`:
  Before a single getUpdates goes out: if the container this one is
  replacing is still polling, two processes would be splitting this
  bot's updates between them and getting 409 Conflict for their trouble.

### `_post_stop()`

- At `await lifecycle.on_stop(application)`:
  Order matters: lifecycle writes the in-progress state out, and
  flush_on_shutdown closes the connection pool it writes through.

### `main()`

- At `lifecycle.install(app, BOT_NAME, ADMIN_IDS)`:
  Before the handlers, because ConversationHandler(persistent=...) below
  asks lifecycle whether persistence actually came up.
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
- At `app.add_handler(MessageHandler(filters.ChatType.GROUPS, group_mode.group_router), group= …`:
  Runs after track_activity but before every other handler (ConversationHandler
  included) -- a no-op unless /feedback or a "Custom" donate button has
  just asked for something to be typed, in which case it takes the answer
  and stops it from also being treated as, e.g., a pack title (see
  awaited_text_received's docstring).
  Group chats first in the same handler group: python-telegram-bot runs the first
  handler that matches, so a group message reaches group_mode's router and never the
  private-chat prompt-taker below it (see group_mode.group_router).
- At `app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & _EmojiReply(), emoji_re …`:
  Ahead of the conversation, so a reply with emoji retags the sticker it replies to in
  any state, and the conversation never takes it for "retag the last one".
- At `behind_question = [MessageHandler(static_media, _behind_question(receive_media)),`:
  While a rename or an invitation waits for typing, a picture still goes into the pack
  open behind it (\_behind_question).
- At `MessageHandler(filters.ChatType.PRIVATE & PACK_BUTTON_TEXT, pack_button),`:
  The pack buttons under the message box (2.1.6), and the held-items question.
- At `CallbackQueryHandler(language_chosen, pattern="^setlang:"),`:
  The three language switches are entry points purely so that the
  END they return is honoured. A handler registered only outside
  the conversation has its return value discarded, which is how a
  language change used to leave the state machine sitting in
  TITLE/EDITING behind a start menu.
- At `CallbackQueryHandler(cancel_choice_callback, pattern="^cancelpick:"),`:
  Same reasoning: /cancel's buttons have to be able to close the
  session the /cancel that raised them was sent from.
- At `CallbackQueryHandler(already_in_pack_chosen, pattern="^" + group_mode.DUP_PREFIX),`:
  And the question a sticker already in the pack gets: deleting the pack's last
  sticker there closes the session (already_in_pack_chosen).
- At `CommandHandler("deletemydata", delete_my_data_command),`:
  And /deletemydata, for the same reason and one more. Its END has
  to be honoured, or an erase leaves the state machine in EDITING
  holding a pack this bot has just been told to forget -- the next
  sticker then fails on state that is gone. The command itself
  returns None and moves nothing; only the confirmation ends it.
- At `EDITING: [`:
  Each behind \_needs_session: the session's state can be gone while the
  conversation is still open (dropped when idle, or lost in a restart).
- At `name="stickerbot_pack_editing",`:
  Named and persisted, so someone who was three stickers into a pack
  when the bot was redeployed sends the fourth and it just works.
- At `allow_reentry=True,`:
  Without this, entry points are ignored for as long as a conversation
  is open, and every one of them is a dead end mid-session: /newpack
  fell through to unknown_command (the unknown-command answer)
  and the New pack / Add stickers / Rename buttons did nothing at all
  when tapped, with no feedback to say why. Re-entry restarts the flow
  the user actually asked for, which is what tapping any of them means.
- At `app.add_handler(CallbackQueryHandler(video_fit_stale, pattern="^vfit:"))`:
  A clip's choice tapped once its session is over, and "don't add" under held items.
- At `app.add_handler(CallbackQueryHandler(language_chosen, pattern="^setlang:"))`:
  ---- language: /language for the picker, /en, /uz, /rus to switch
  directly -- offered at the first /start, available anytime after.
  Standalone (not part of the ConversationHandler above) so they still
  work mid pack-editing session -- conv's per-state handlers don't match
  a bare "/en" etc, so the update falls through to these. ----
- At `app.add_handler(CommandHandler("privacy", privacy_command))`:
  ---- what the bot keeps, what it may be used for, and the erase button.
  Standalone for the same reason the language switches are: somebody who
  wants to know what is held on them, or wants it gone, should not have to
  finish whatever they were doing first. ----
- At `app.add_handler(CommandHandler("fullhelp", full_help_command(build_help_text, help_rows, …`:
  Outside the conversation, like /help, so they work in the middle of a pack.
- At `app.add_handler(CallbackQueryHandler(help_page_button(HELP_PAGES), pattern=HELP_PAGE_PAT …`:
  A page tap sends a new message and edits nothing, so it is fine here, outside
  the conversation: it answers in the middle of a pack and does not end it.
- `app.add_handler(CommandHandler("whois", whois_command))` — operator-only
- `app.add_handler(CommandHandler("messageas", messageas_command))` — operator-only
- `app.add_handler(CommandHandler("dbdump", dbdump_command))` — operator-only
- `app.add_handler(CommandHandler("status", status_command))` — operator-only
- `app.add_handler(CommandHandler("crashtest", crashtest_command))` — operator-only, for testing /status
- At `app.add_handler(CommandHandler("import", import_standalone))`:
  Both only fire outside an active editing session -- inside one, the
  conversation's own EDITING handlers claim them first.
- `app.add_handler(CommandHandler("cancel", cancel_command))` — standalone fallback
- `app.add_handler(CommandHandler("convert", convert_redirect_command))` — points to @ConvertBot now
- At `app.add_handler(CommandHandler("balance", balance_command))`:
  ---- donations (Telegram Stars) -- this bot's only Stars usage, so these
  register directly with no payload-prefix branching needed ----
- At `app.add_handler(MessageHandler(filters.COMMAND, unknown_command))`:
  Both registered last, so every real handler (and the conversation's
  own in-state handlers) above gets first shot.
- At `app.add_handler(MessageHandler(filters.ChatType.PRIVATE & BATCH_MEDIA, media_without_pac …`:
  A picture, clip or sticker with no pack open: held, and the person asked which pack.
- At `family_link.attach(app, BOT_NAME, "StickerBot", START_TIME)`:
  ManagerBot's link: heartbeats, crash/donation events, and the queue it
  uses to run this bot's operator-only commands remotely. Never raises --
  with no shared database reachable the bot just runs on its own.
- At `app.run_polling(**lifecycle.polling_kwargs(`:
  A 30-second long poll instead of the default 10 is the same latency --
  Telegram answers the moment an update exists -- for a third of the HTTP
  requests, which on an idle bot is most of what it does all day. The
  allowed_updates list is every kind this bot has a handler for; anything
  else (edited messages, channel posts, reactions, other people's
  membership changes) Telegram now stops sending at all rather than this
  process downloading and parsing it only to drop it. Its own membership
  (MY_CHAT_MEMBER) is in since 2.2.0: being added to a group or removed.
  polling_kwargs() is what lets this process hear SIGTERM before
  python-telegram-bot shuts it down, so a redeploy can tell anyone
  mid-conversion what happened instead of leaving them waiting.
  drop_pending_updates stays off (its default): updates sent while the
  container was being replaced are still on Telegram's side, and this is
  the poll that collects them.
