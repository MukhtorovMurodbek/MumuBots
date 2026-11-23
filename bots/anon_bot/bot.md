# `bot.py`

[↑ anon_bot](README.md) · [code](bot.py)

AnonBot -- one-sided anonymous inbox, 4th bot in the family (see
ARCHITECTURE.md). Anyone can grab a permanent personal link (/link) and
post it somewhere public; anyone who taps it can send them anonymous
messages here without the inbox holder seeing who they are. The inbox holder's identity is never hidden -- it is their own bot chat -- only the person asking is.

```text
The two things this bot is built around (both explicitly requested):
  1. Every message either side sends says which conversation it answers, by
     being a real Telegram reply to a real message in that conversation --
     so with a dozen different people messaging the inbox holder in the same chat,
     there is never any doubt about where an answer is going. Exactly one
     message is exempt: a guest's opening one, which has nothing above it to
     answer. Every incoming message carries a "Reply" button, which explains
     the gesture; it is not a second way of sending, and there is no way to
     send a message that names nothing.
  2. When the inbox holder replies, the answer is delivered as a genuine Telegram reply on
     the follower's end too (quoting their own earlier message) -- so it
     visibly looks like a real back-and-forth, not a wall of disconnected
     bot messages. See anon_logic.relay_content() for how.
```

Tapping the SAME link again always starts a brand-new conversation (a fresh
conv_number, a fresh reply-chain anchor) -- it does not continue whatever
was last open with that inbox.

A reaction on a message in a conversation is put on the same message in the
other chat, by the bot -- see relay_reaction(). It shows beside the bot's
picture, so it is as anonymous as everything else here.

```text
Commands:
  /start, /help     - greeting + how it works, the short version, with a
                       button for the full one (/help full); /start also
                       handles /start q_<token>, i.e. someone tapping a link
  /feedback         - a suggestion or a report, for whoever runs the bots
  /link             - get a permanent inbox link
  /newlink          - reset the link (invalidates the old one)
  /pause / /resume  - stop/resume NEW conversations (open ones keep working)
  /conversations    - list the inbox's threads, revisit one from its start,
                       archive the finished ones
  /which            - sent as a reply: which conversation is that message in
  /blocked          - review + undo blocks
  /stats            - quick counts for the inbox
  /donate           - support hosting costs (voluntary)
  /en, /uz, /rus    - switch language (English/Uzbek/Russian); also asked
                       once, trilingually, on first /start
```

```text
Requires: python-telegram-bot[job-queue]>=21.3, python-dotenv>=1.0
Env vars: ABOT_TOKEN, ABOT_USERNAME (no @), ABOT_ADMIN_ID (optional,
          comma-separated, gates /messageas, /dbdump and /status),
          DATABASE_URL and DB_SCHEMA (the shared family database, and this
          bot's schema in it), SIBLING_BOTS (see shared_features.py)
```

_At `try:`:_

main.py gives each bot its settings; this only reads a .env if one is present.

_At `NO_PREVIEW = {"link_preview_options": LinkPreviewOptions(is_disabled=True)}`:_

The inbox link is sent as a plain, tappable link rather than as a code
block, so it reads and shares like a link -- but Telegram turns the first
link in a message into a preview card, and a preview of this bot's own
start page under a two-line message with buttons is noise. Every message
carrying an inbox link opts out.

_At `BOT_USERNAME = (os.environ.get("ABOT_USERNAME") or "").strip().lstrip("@") or None`:_

Without the @, which is also taken off if it was written with one -- the
one-process edition always allowed it, and a link built from "@name" is broken.

- `BOT_NAME = "anonbot"` — this bot's id within SIBLING_BOTS

_At `ADMIN_IDS = {int(x) for x in os.environ.get("ABOT_ADMIN_ID", "").split(",") if x.strip()}`:_

Operator-only admin tools (/messageas, /dbdump, /status) -- comma-separated
Telegram user ids. Empty/unset means disabled for everyone.

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

### `_reply()`

Works whether this came from a command or a button tap -- the first-run
language picker means a brand-new user can reach any of these paths from
a callback query, which has no message of its own to reply to.

The button path evolves the tapped message in place, but only while it is
still the last thing in the chat; once the user has typed anything since,
a rewrite would land above their own message and out of sight, so a new
message is sent instead. See live_message.py.

### `build_help_text()`

The short version unless asked for the long one -- the family's help
template (shared_features, "/help in two lengths"). The short one is what
/start and /help show: how the inbox works, that reactions travel, and
/feedback. The long one ("help_text", which tests/test_menu.py holds to
naming every command) is a tap away, and lists every reaction a bot is
allowed to pass on. The per-command pages (HELP_PAGES below) are the
buttons under the help, not a line in it.

_At `def _help_page_link(lang: str) -> str:`:_

---------- /help &lt;name> and the buttons under /help: a page for each command ----------
The mechanism is in shared_features ("A page for each command"). Every
number on a page is read from the module that owns it when the page is shown
-- by attribute, never copied -- so a page says what the code does now.

### `_help_page_which()`

- At `return i18n.t(lang, "helppage_which", example=i18n.t(lang, "which_owner", conv_number=4))`:
  The bot's own answer, as the inbox holder gets it.

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

/newlink, /pause, /resume and /stats are gone from here and still work.
The /link screen has been showing New link and Pause/Resume as buttons
since v1.1, and now carries the counts /stats printed -- so four menu
entries pointed at things already on one screen. /pause and /resume were
the worst of them: the same two words mean "stop the bots for an update"
in ManagerBot, which is a different thing entirely.

/archive is gone and still works. It files the conversation being replied to, and /conversations has an Archive button on every row.

_At `ADMIN_COMMANDS = [`:_

The same menu, in the operator's own chat, with the commands only the operator can run. Kept out of BOT_COMMANDS on purpose -- a stranger should not be offered
/dbdump -- but hidden from the operator too, which was the accident. See
publish_commands() in shared_features.py. English, like the rest of the
admin output: the only person who sees this list wrote the bot.

### `_incoming_keyboard()`

Under a message arriving in the inbox holder's chat.

### `_follower_keyboard()`

Under a message arriving in the GUEST's chat. Reply only -- blocking
is the inbox holder's tool, and a guest who wants out just stops
answering.

### `_follow_cancel_keyboard()`

Under the line a link tap opens with, for a guest who changes their
mind. It comes off once their first message has gone.

_At `async def _continue_start(update: Update, context: ContextTypes.DEFAULT_TYPE, lang: str, …`:_

---------- /start (also handles someone tapping an inbox link: /start q\_&lt;token>) ----------

### `_continue_start()`

- At `await _knows_reactions(context, update.effective_user.id)`:
  The short help says reactions travel, so whoever has just read it has
  been told -- the one-time note would only say it again.

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

A deep link (/start q\_&lt;token>, someone opening an inbox link) still goes
straight to that inbox, and a brand-new user who arrives on one picks a
language first and is carried there afterwards by pending_start_args.

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

### `_apply_language()`

The single path every language change goes through -- /en, /uz, /rus
and a tap on the picker alike -- so all four end the same way: with the
instructions, printed in the language just chosen.

Answering a language change with nothing but "Language set" (which is all
/en, /uz and /rus used to do for anyone who was not brand new) leaves
someone looking at a bot whose manual they have just made themselves
unable to reach. pending_start_args carries a brand-new user who arrived
on an inbox link through to that inbox once they have picked.

- At `await refresh_chat_menu(context, update.effective_user.id, lang)`:
  The menu follows the choice too -- Telegram otherwise shows it in the
  language of the phone.

### `language_chosen()`

A tap on the picker. Carries any pending /start q\_&lt;token> through, so
someone whose very first action was opening a link lands in the
conversation it points at rather than back at the greeting.

### `start_follow_link()`

- At `owner_id, is_paused, blocked = await asyncio.to_thread(`:
  One query for "whose token is this, is that inbox paused, and is this guest blocked" -- it used to be three, run one after the other.
- At `wait = new_conversation_allowed(follower.id)`:
  Opening a conversation writes rows the inbox holder is then notified about,
  and it is one tap to do. A short cooldown is the difference between a
  misfire and somebody filling an inbox with empty threads.
- At `refusal = await refuse_new_work(lang, follower.id, update.effective_chat.id)`:
  An announced update can span several deploys, and a
  conversation opened into one starts a thread the database may be in the
  middle of migrating underneath. Being told to come back in ten minutes,
  once, beats finding out afterwards -- and refuse_new_work writes this
  person down so /finishupdates goes back to them.
- At `opening = await context.bot.send_message(`:
  Sent rather than edited in place, because this bubble is the
  conversation's permanent anchor -- the thing every swipe in this thread
  eventually points back at -- and a brand-new user who arrives on a deep
  link reaches here from a button tap, where \_reply would have rewritten
  the language picker into it instead of leaving something of its own.

  It used to carry a ForceReply, so that the guest's first message was a
  real reply to it without their having to know that. That made the
  opening message arrive by the same route as every other message, with
  the exemption below as a fallback. The ForceReply is gone -- see the
  reply requirement further down -- so the exemption is now the ordinary
  path for an opening message rather than the fallback for one, which is
  why it is worth being exact about: see handle_message step 2.

  What has not changed, and is what keeps this recoverable, is the relay
  row registered just below. Whatever else happens in this chat, this
  bubble stays a valid thing to reply to, so a guest who loses the
  exemption can always swipe it and open the conversation that way.
- At `text=i18n.t(lang, "follow_link_started"),`:
  No conv_number here either -- the string stopped using it in
  v1.4.0 and the argument was left behind, which is how a number
  that is not supposed to reach a guest stays one edit away from
  doing so again.
- At `await asyncio.to_thread(`:
  Registered as a relay target so the guest's \*first\* message has
  something to reply to. Every later message in this chat is a real message from the inbox holder; this one stands in for them until then. Not
  marked as a prompt: a prompt is scaffolding that is deleted once
  answered, and this is the opposite -- it is what the thread hangs on.

### `follow_cancel_callback()`

The opening line's Cancel button. The line changes at once; the
conversation is taken away behind it, and if a first message beat the tap
to it the line is put back as it was.

### `help_command()`

/help is the short version; /help full is the long one.

- At `await update.message.reply_text(help_page_text(lang, HELP_PAGES, page),`:
  A page says nothing about reactions, so it does not count as told.

### `fullhelp_command()`

/fullhelp: what /help full gives -- which lists every reaction, so
whoever reads it has been told about reactions too.

_At `def _link_message(token: str, paused: bool, lang: str, note: str = "",`:_

---------- inbox holder: inbox link management ----------

### `_link_message()`

Shared by /link and its Pause/Resume/New-link buttons, so tapping
one edits the same message back into an up-to-date version of itself
instead of /pause, /resume, or /newlink having to be typed by hand.

Since 1.7.0 it also carries the two counts /stats printed. They are about
this link -- how many people have used it, and how many threads came out
of that -- so the page about the link is where somebody looks for them,
and /stats was a menu entry for two numbers.

### `anonlink_newlink_callback()`

- At `token, is_paused = await asyncio.to_thread(regenerate_anon_link, owner_id)`:
  A new link does not un-pause the inbox, and the screen has to keep
  saying so -- the paused flag comes back from the same statement rather
  than costing a second query.

### `pause_command()`

- `await asyncio.to_thread(get_or_create_anon_link, owner.id)` — idempotent

### `dbdump_command()`

/dbdump -- exports every table in this bot's own database as one
zip of CSVs. Operator-only: this is every user's data, not something to
hand out on request.

### `messageas_command()`

/messageas &lt;user_id> &lt;text> -- sends a message to that user as this
bot. Only works if the user has messaged the bot before (Telegram
doesn't let bots cold-message anyone). Operator-only for obvious reasons.

### `status_command()`

/status -- uptime, hosting environment, any crashes since this
process started, and active-user counts. Operator-only, same reasoning as
/dbdump: this is operational info, not something every user should see.

### `_blocked_list_text_and_kb()`

- At `if conv_numbers:`:
  Conversation numbers instead of a pseudonym: the inbox holder has seen these in their own chat, and unlike a stable hash they say nothing
  about the guest beyond "a conversation happened, in this thread".

  The button is addressed by conversation id, never by the guest's
  Telegram user id. Callback data travels to the client as part of
  the keyboard, so a user id in it is a user id published -- see
  list_anon_blocks_with_conversations.

_At `CONVS_PREFIX = "aq_convs:"`:_

---------- /conversations: an inbox's own list of its threads ----------
One chat holds every conversation an inbox has, interleaved, and until now
the only way to see what was in there was to scroll. Three questions this
answers, in order of how often they are asked: how many are open, where does this one start, and which of these are finished.

It is a list, a card and one action, in that order, editing a single
message in place. The alternative -- every conversation drawn as a line of
text with its own row of buttons underneath -- puts twenty-odd buttons in
somebody's chat and still has nowhere to say anything about one thread.

- `CONVS_PREFIX = "aq_convs:"` — &lt;archived 0|1>:&lt;page>

- `CONV_CARD_PREFIX = "aq_conv:"` — &lt;conversation id>:&lt;archived 0|1>:&lt;page>

- `CONV_ARCH_PREFIX = "aq_arch:"` — &lt;conversation id>:&lt;archive 0|1>:&lt;archived 0|1>:&lt;page>

- `CONV_JUMP_PREFIX = "aq_jump:"` — &lt;conversation id>

### `_ago()`

How long ago, in whole units, with no date and no clock time in it.

Every timestamp in this schema is an ISO string in UTC and the bot has no
idea which timezone the person reading is in, so a clock time would be
wrong for nearly everybody and a date needs month names in three
languages. "3 h ago" needs neither, and is the question being asked.

### `_conversations_view()`

The list message: what it says and what is under it.

The conversations themselves are buttons rather than lines of text.
Every one of them is something to tap, and printing each as a line and
then repeating it as a button says the same thing twice in a chat that
is mostly somebody else's words already.

### `_conversation_card()`

One conversation, and the two things that can be done to it from
here. Blocking is deliberately not one of them: it is available under
every message the guest has sent, and a list is the wrong place to put
an action a mis-tap cannot take back.

### `_owned_conversation()`

The conversation a card button names, if it is this user's to act on.

Every button below is drawn only in the inbox holder's own chat, so a tap
naming somebody else's conversation is either a stale keyboard or a
crafted one. Both get the same answer, which says nothing about whether
the conversation exists.

### `conversations_page_callback()`

Page turns and the Open/Archived switch, both of which are just
"draw the list again with different arguments".

### `conversation_archive_callback()`

Filing, and nothing more. Nothing is refused because a conversation
is archived and the guest is never told -- an archived thread that
somebody writes into comes straight back, which is what makes this safe
to press on a thread that only looks finished.

_At `ARCH_CMD_PREFIX = "aq_archcmd:"`:_

/archive -- the same filing /conversations does, without the walk to it.

Archiving already existed, but only as a button on a conversation's card
inside /conversations: open the list, page to the thread, open its card,
press Archive. Four steps to file the thread whose message is already on
screen. Replying to that message and sending /archive is one, and the
backend is the same call.

It asks first. Every other action in this bot is reversible in place and
this one is too -- an archived thread comes straight back the moment
anything is relayed into it -- but a command typed under a message is much
easier to aim at the wrong message than a button on a card that names the
conversation, so the confirmation is where the naming happens.

- `ARCH_CMD_PREFIX = "aq_archcmd:"` — &lt;conversation id>:&lt;archive 0|1>

### `archive_command()`

- At `if conv["owner_user_id"] != user_id:`:
  A guest has no list to file anything out of: conv_number is the inbox holder's index for their own inbox, and archiving is an operation on that index.

### `archive_command_callback()`

The answer to that question. Re-reads the conversation rather than
trusting the button: it may have been on screen a while, and the thread
could have been archived from /conversations in the meantime.

### `_send_bookmark()`

A message pointing at an older one, and a relay row saying which
conversation it belongs to.

Tapping the quote is what scrolls to the older message: a private chat
with a bot has no message links -- t.me/c/... is supergroups and
channels -- so a reply-quote is the only way to point at something
further up.

Registering it as a relay row is deliberate, and it is not a hole in the
reply rule. The rule is that a message has to say which conversation it
answers by being a Telegram reply to a message in that conversation;
this is a message in exactly one conversation, so replying to it says
that. It is the same thing the line announcing a new conversation has
always been. What it adds is a way into a thread without having to find
a message from it first.

If the anchor has since been deleted Telegram refuses the whole send, so
this retries without it -- the same shape relay_content uses. A bookmark
with no quote is worth less, but it still names the conversation and is
still a valid thing to reply into.

### `conversation_jump_callback()`

- At `await context.bot.send_message(`:
  Everything from this conversation has been pruned, so there is
  nothing left in the chat to point at -- and nothing left to reply
  to either, which is why prune_old_data archives it at the same
  time. Saying so beats pointing somewhere else.

### `which_command()`

/which, sent as a reply: which conversation is that message in?

Every message this bot delivers already carries a "Conversation #N"
header. What it cannot label is the rest of what is in the chat -- the inbox holder's own outgoing bubbles, the second and third photo of an album,
the later bubbles of a message too long for one -- and this answers for
any of them, because it asks anon_relay rather than reading the screen.

The inbox holder is told the number. A guest is not: conv_number is the inbox holder's index for their own inbox, so "#3" in a guest's chat means
"this inbox's third correspondent", which is meaningless to them and is
a fact about somebody else. They are pointed at that conversation's own
opening line instead, which identifies it without labelling it.

- At `await message.reply_text(i18n.t(lang, "which_unknown"))`:
  `reply_stale` used to answer here, and it ends by saying the message was not sent -- which is a confusing thing to read after asking a question.
  /which sends nothing and can fail for its own reason, so it says so
  in its own words.

_At `def _callback_ints(query, count: int) -> "list[int] | None":`:_

---------- the Reply / Block buttons under an incoming message ----------

### `_callback_ints()`

The `count` numeric fields after the prefix in a button's
callback_data, or None if they are not all there and not all numbers.

Every one of these buttons was drawn by this bot, so in ordinary use the
tail is always numbers. Callback data is still client-supplied: a
crafted client can send `aq_reply:notanumber` against any message it can
see, and int() on that raised straight through the handler into the error
handler -- which reports a crash to ManagerBot. That turns a malformed
string anybody can send into a way to page the operator at will. Refusing
it here answers the tap the same way an id for somebody else's
conversation is answered, and stays out of the crash log.

### `_conversation_arg()`

The conversation id out of a single-field button, or None.

### `reply_button_callback()`

Works for whichever side tapped it. Both chats now carry relay rows,
so a conversation has two legitimate participants and the only question
is whether this user is one of them.

- At `await query.answer(i18n.t(lang, "reply_button_hint"), show_alert=True)`:
  This button used to send a prompt carrying a ForceReply, which aimed the
  reply box at the right conversation. That was the second of the
  two routes into a conversation, and the two of them together are what
  made this bot's routing hard to be sure about -- so there is one route
  now, and it is the one Telegram already gives everybody: reply to the
  message.

  The button stays, and says how. It cannot aim the reply box itself --
  nothing except ForceReply can, and ForceReply can only aim at a message
  the bot is sending right now, never at an earlier one -- so what is left
  for it to do is teach the gesture once to whoever has not met it. That
  is worth a button: the alternative is a rule with no visible way to obey
  it, which is the shape of every bot people give up on.

### `block_callback()`

- At `await query.answer(i18n.t(lang, "blocked_answer"), show_alert=False)`:
  One tap, one answer. This used to fire the toast AND send a separate
  "Conversation #N is blocked" message underneath -- two notifications
  for one deliberate action, in a chat this bot already writes into more
  than it needs to. The toast says it happened; the button now says it is
  still true, which is the part the inbox holder comes back and looks for.

### `unblock_from_list_callback()`

The /blocked list's own Undo. Addressed by conversation, and the guest
behind it is looked up here rather than carried in the button.

_At `async def _refuse_unaddressed(update, context, lang: str, routing: dict) -> None:`:_

---------- the reply requirement ----------
Every message either side sends has to say which conversation it answers, by
being a real Telegram reply to a real message in that conversation. One
message is exempt and only one: a guest's opening message, which has nothing
above it to answer.

There used to be a way past it. A message that named nothing was HELD -- the
whole message, text and all, parked in user_data -- and offered back with a
"Send it anyway" button. It was written to spare somebody a swipe, and it
cost more than it saved:

```text
It stored what people wrote. user_data is persisted to Postgres by
lifecycle.PostgresPersistence, so the held message's text went into the
database and sat there until the button was tapped or the row expired.
Every version of this bot's privacy notice says message content is never
stored. That sentence was not true while this existed, and no amount of
"only briefly" makes a stored message unstored.
```

```text
It was a second router. The button guessed a destination -- the one
conversation the message could unambiguously have had -- which is the
guess the reply requirement exists to prevent, made by a button instead
of by the router.
```

So there is no way past it now. A message that names nothing is refused, and
refused is all: nothing is sent, nothing is kept, and the person is told
which message to reply to instead of being told merely that they must.

Pointing at it is the whole difference between a rule and an obstacle, which
is why the refusal is sent AS a reply to the message it is asking about --
see active_anchor in db.get_routing_context.

### `_refuse_unaddressed()`

Say which message to reply to. Send nothing, keep nothing.

- At `opening = active.get("follower_first_msg_at") is None`:
  One possible destination, so the refusal can name it and point at
  it. Whether the conversation has been opened yet changes what the
  person is being asked for: their first message, or an answer.
  No conversation number in either: conv_number is the inbox holder's index
  for their own inbox, so a guest with two conversations open has two
  of them both called #1. It is the right label for the inbox holder and a
  misleading one for a guest, who never needs to name a conversation
  anyway -- they name it by replying to it.
- At `text = i18n.t(lang, "must_reply_ambiguous")`:
  An inbox of their own, with or without a session of their own open:
  several people could be on the other end, and naming one would be
  the guess this rule exists to stop.
- At `pass`:
  The message it wanted to point at has been deleted. Quoting the
  person's own message still says which one did not go.

_At `_ALBUM_WINDOW = 20`:_

---------- the actual message routing: inbox holder replies vs. follower questions ----------

_At `_ALBUM_WINDOW = 20`:_

An album is several updates, not one. Telegram sends each photo of a media
group as its own message with a shared media_group_id, so a three-photo
album used to arrive as three headed bubbles, three Reply/Block rows and
three "Sent" confirmations. The first part of a group says all of that; the
rest ride in quietly underneath it. Kept in memory and only for a moment,
because that is exactly how long the parts take to arrive.

- `_ALBUM_WINDOW = 20` — seconds

_At `_albums: "dict[str, tuple[int, float]]" = {}`:_

media_group_id -> (conversation_id, when the first part was seen)

### `_album_route()`

The conversation an earlier part of this album already went to.

The map is not only about saying "Conversation #6" once. Which
conversation an album belongs to is decided by its FIRST part and by that
part alone, because the parts after it cannot be routed the ordinary way:

```text
* the guest's opening album spends the one-message exemption on part
  one, so parts two and three were held with "reply to something
  first" -- two photos of a three-photo message silently not delivered;
* the inbox holder's reply album anchors every part to the Reply prompt, and
  answering the prompt is what takes the prompt down, so parts two and
  three came back "that message is too old to reply to".
```

Both are the same mistake: treating an album as several messages when a
person sent one. Consulted before any other routing, which is what makes
every part land where the first part did.

### `_album_remember()`

Note where this album is going. Returns True if an earlier part has
already been announced, i.e. this one should arrive quietly.

### `_refuse_if_updating()`

Whether an announced update means this message should not be relayed
now. Says so and writes the person down when it does.

A relay is quick, but it is still a write into a database an update may
be in the middle of migrating -- and the point of announcing an update in advance is that people hear about it before it lands rather
than afterwards. AnonBot had none of these checks at all: /pause reached
it, set the flag correctly, and nothing ever asked.

### `_anchor_for()`

Which message in the receiving chat the delivered copy hangs under.

The peer of whatever was replied to, when there is one. Otherwise the
newest message of this conversation in that chat, which is what the
whole family of these anchors used to be and is still the right answer
when nothing more specific is known -- a first message, an album's
later parts, a reply to a line that exists in one chat only.

Both can be None, and that is a valid answer too: it means the copy goes
in unanchored, which is what the very first message of a conversation
has always done.

### `_deliver_follower_message()`

`message` is normally the one that just arrived; the Send-it-anyway
button passes the earlier, held one instead.

- At `blocked, inbox_exists, owner_lang = await asyncio.to_thread(`:
  "Is the sender blocked, does the inbox still exist, and what language
  does the inbox holder read?" -- one query, where it used to be three separate
  round trips on the hot path of every anonymous message.
- At `note_stat("anon_relay", "to_inbox")`:
  Both anchors move: the inbox-side copy anchors the inbox holder's next reply chain, and the follower's own message anchors what the inbox holder's reply will quote on the way back (see \_deliver_owner_reply below). The
  guest's own message is written down on their side for the same reason the inbox holder's is on theirs -- so an edit to it can be answered, and so a
  guest quoting themselves resolves rather than being refused.
- At `if not quiet:`:
  One confirmation per album, not one per photo.

_At `async def _deliver_owner_reply(update: Update, context: ContextTypes.DEFAULT_TYPE, conve …`:_

No donation nudge on this side. A guest did not choose this bot -- they
tapped somebody's link -- and asking them to fund it is asking the
wrong person. The inbox holder is nudged instead, once per conversation; see
\_deliver_owner_reply.

### `_deliver_owner_reply()`

- At `await message.reply_text(i18n.t(owner_lang, "reply_no_match"))`:
  anon_relay rows for a chat are only ever written for that chat's
  real inbox holder, so this shouldn't normally trip -- but never guess and
  risk sending a reply to the wrong chat.
- At `header=None if quiet else i18n.t(follower_lang, "incoming_header_follower"),`:
  No conversation number on this side. conv_number is the
  inbox holder's index for their own inbox, so this header was telling
  every guest that they were somebody's forty-seventh
  correspondent -- a fact about the inbox holder, delivered to a stranger, with no way for the inbox holder to stop it. v1.4.0 took
  the number out of the other guest-facing strings for the
  weaker reason that it meant nothing to them; this one was
  missed, and it is the one that leaked.
- At `note_stat("anon_relay", "reply")`:
  The inbox holder's own sent message is a valid anchor too, so the NEXT
  incoming message in this conversation threads off of it in the
  inbox holder's chat, keeping the back-and-forth visually connected there too.
  And the delivered copies on the guest's side, without which a guest
  who swipe-replies to the answer they just received is told their reply
  matches no conversation. Both sides, one statement.
- At `if conv.get("last_owner_msg_id") is None:`:
  Once per conversation, on the inbox holder's first answer in it -- not once per
  message. maybe_donation_nudge counts the times it is called, and in the
  other bots one call is one whole conversion or download. Here it was one
  chat message, so a pair holding an ordinary back-and-forth were shown a
  donation pitch three messages in and then every few days.

### `export_command()`

**/export -- removed. The command only says so; it reads and writes nothing.**

### `_cancel_items()`

Everything this bot could be waiting on.

The open ask session -- what tapping someone's inbox link leaves behind
-- is the one that matters most, because it lives in the database rather
than in memory: every ordinary message sent keeps going to that
person until something clears it. It is also the one most worth being
asked about rather than swept up, since someone who typed /cancel to
escape a Reply prompt did not mean to walk out of the conversation.

### `cancel_command()`

Asks which of the things it is waiting on to stop, then stops that
one -- see \_cancel_items for what they are.

- At `await finish_cancel(update, context, lang, [], stored_only=True)`:
  stored_only: every message this bot sends into a chat is part of a
  conversation, so "delete whatever /cancel replies to" would delete
  somebody's message and leave its relay row pointing at nothing.

### `cancel_choice_callback()`

A tap on one of /cancel's buttons.

### `edited_message()`

Editing a message that has already been relayed changes nothing on the
other side, and saying nothing let people believe it had.

A bot cannot edit a message it copied into somebody else's chat back into
a new shape -- copy_message makes an independent message, and the other
person has very likely already read it. So the honest answer is to say
what happened, once, and point at the thing that does work.

Registered in group -1 and always ends by stopping the update, because
asking Telegram for edits at all (allowed_updates) means every handler
below this one starts seeing them: python-telegram-bot matches a
MessageHandler -- and a CommandHandler -- on effective_message, which is
the edited message, while update.message is None. Nothing downstream is
written for that, and nothing downstream has any use for an edit.

- At `logger.exception("Could not answer an edited message")`:
  Logged, not swallowed. `raise` in a `finally` REPLACES whatever was
  already propagating, so with only a finally here every failure in
  this handler -- including a TypeError from a signature that had
  changed underneath it -- became ApplicationHandlerStop and vanished.
  The user got silence and the operator got no crash report; the suite
  was the only thing that noticed.

Since 2.2.1 (2026-11-23, after a channel poll said yes) the edit is carried over instead, for whoever
/editing allows (`_edits_allowed`): the bot edits its own copy in the other chat, which it may do, and
the person sees a short note that removes itself. Whoever it leaves out keeps the old answer.

### `_edits_mode()`

Who may carry an edit or a deletion over: a family setting (`anon:edits`) the operator changes with
/editing, without a redeploy, read at most once a minute. "everyone" is also what a missing or
unreadable setting means; "admins" is ABOT_ADMIN_ID only (how it was tried first); "off" is nobody.

### `_menus()`

/delete is in the public menu once it is for everyone, and in the admin's own menu while only the
admins have it.

### `_twin_dressing()`

The header and buttons a relayed message carries on the recipient's side, so an edit or a deletion
note looks like what was sent. The Block button matters most: an owner must always be able to block.

### `_relay_edit()`

An edit reaches the other side only where its copy is one message (anon_logic.edit_relayed); a long
text sent in parts, or a file sent after a separate header, keeps the old answer. A blocked guest's
edits go nowhere, like their messages. The relay row does not record whether the copy was a later item
of an album (sent without header and buttons), so every copy is rebuilt with them. "Message is not
modified" is not an error.

### `_delete_twins()`

Telegram lets a bot delete a message only within 48 hours of sending it; a copy already gone counts as
deleted.

### `delete_command()`

/delete, as a reply to one's own message: Telegram tells a bot nothing when somebody deletes a message
in its chat, so this is the only way. The other side's copies go first; the person's own message and
the command go only once that worked, so a refusal leaves everything where it was. When a guest
deletes, the owner keeps a line in its place with the usual buttons, recorded as part of the
conversation, so the guest can still be blocked and answered. To whoever /editing leaves out, /delete
does not exist.

### `_editing_text()`

The admin's switch, in English like every admin command.

### `editing_callback()`

Saves the mode and republishes the menus so /delete appears or goes at once.

_At `async def relay_reaction(update: Update, context: ContextTypes.DEFAULT_TYPE):`:_

---------- reactions: passed across, as the bot's own ----------

### `relay_reaction()`

Somebody reacted to a bubble of a conversation: the bot puts the same
reaction on its twin in the other chat, where it shows beside the bot's
picture rather than theirs. Changing it changes the twin's; taking it off
takes the twin's off.

Edits cannot do this -- a copy is an independent message, and its text is
the other person's to keep (see edited_message) -- but a reaction is not
part of the message. It is a mark beside it, and a bot may set its own.

What cannot travel is a Telegram Premium custom-emoji reaction, a paid
one, or more than one per message (a bot gets one); the person is told,
briefly, by a note that removes itself. Nothing about a reaction is
stored, and nothing is said when there is simply nowhere to send it -- a
reaction on the bot's own notices, on a blocked conversation, or in a
chat Telegram will not deliver to.

- At `await _knows_reactions(context, reactor)`:
  Somebody who reacts has plainly found out reactions exist.
- At `note = "reaction_not_passed"`:
  An emoji Telegram does not let a bot use after all.
- At `logger.debug("Could not pass a reaction on: %s", exc)`:
  The twin deleted, or a reaction already exactly as asked.

_At `_taking_down: set = set()`:_

Notes that are about to take themselves down, held so the tasks are not
garbage-collected half-way through their sleep.

### `_show_briefly()`

A silent message that deletes itself after REACTION_NOTE_SECONDS.

Telegram's own floating notice was asked for -- the one that fades
after a few seconds. A bot can only show that as the answer to a button
tap (answerCallbackQuery), and a reaction is not one; Bot API 10's
ephemeral messages are for groups. This is the nearest thing: no sound,
no badge left behind, gone again before the next message. A restart in
those seconds leaves it in place, which costs one stray line.

_At `KNOWS_REACTIONS_KEY = "knows_reactions"`:_

The one-time note that reactions now travel, for people who were using the
bot before they did. Who is owed it is a column, user_settings.knows_reactions
(see db.init_db): NULL for everybody who was here before 2.1.0, TRUE for
everybody since and for everybody once told.

### `_knows_reactions()`

Mark this person as told, without telling them. True if they had not
been -- the caller decides whether that means a note. Cached, so it is one
database round trip per person, ever.

### `reactions_update_note()`

After whatever somebody came to do: if they were here before reactions
travelled, tell them once. Asked for: a one-time short note about the update, the next time they use the bot.

In a group of its own after every other handler, so the note comes after
the answer it is not part of. Not while a prompt is waiting for its answer
-- a note between a question and its reply box is in the way -- which
only puts it off to the next message.

- At `context.user_data[NOTE_UPDATE_KEY] = update.update_id`:
  So that promo_ad_note, which runs right after this in the next group,
  leaves this message alone: one note per message.

_At `PROMO_AD_KEY = "_promo_ad"`:_

The 220 gift's ad (2.2.0; family_link, "The 220 ⚡ gift"): every person who
writes to this bot while the gift is open and who has not had it is shown the
ad once, with a button to each of the two bots that give it. "Once per
person" is a row in promo_seen, written before the ad is sent -- so a failed
send is not retried, and two updates at once cannot both show it -- and
PROMO_AD_KEY remembers in this process that the question is settled, so the
next message costs no database trip at all.

_At `NOTE_UPDATE_KEY = "_note_update"`:_

The update the reactions note answered; the ad waits for the next message.

### `_no_ad_after()`

Is this one of the commands the gift's ad never follows?

_At `PROMO_TEASER_FROM = datetime(2026, 11, 18, 2, tzinfo=timezone.utc)`:_

Before the gift starts, from 11:00 KST on 2026-11-18 (first in MumuCombined 2.2.0): each person is
told once that 2.2.0 is out and a giveaway is coming, with the channel's link ("gift_teaser"). The
operator asked for it not to wait for the gift itself. PROMO_TEASER_KEY is its own row in
promo_seen, so the gift's ad still comes once the gift opens.

### `promo_ad_note()`

After whatever somebody came to do, and after the reactions note: if the
gift is open and they have not had it nor been shown this ad, show it once.
Before it starts (from PROMO_TEASER_FROM), the teaser instead, once.

Quiet by design: a new message in a private chat only, never in the middle
of a question or an inbox link's first message, and any failure here just
means no ad (nothing is recorded unless the ad is about to be sent).

- At `words = (message.text or "").split(None, 1)`:
  /start with something after it is somebody opening an inbox link, about
  to write their anonymous message: the ad waits for the one after. A bare
  /start may show it, after the welcome.
- At `if _no_ad_after(message):`:
  Nor under the privacy pages or a request to be forgotten: an ad there,
  and a row saying it was shown, is the wrong answer to a request to delete data.
- At `if not window[0] <= now < window[1]:`:
  Nothing is remembered when it is not open: it may open (again) later.
- At `row = (sibling_bots_keyboard_row(BOT_NAME, only="downloaderbot")`:
  One row, DownloaderBot first as in the text. An ad with no way to
  reach the bots is not worth the person's one chance at it.
- At `lang = await asyncio.to_thread(get_user_language, user.id)`:
  Not before they have chosen a language: a first /start shows the
  picker, and an ad in English under it would spend their one chance
  in what may be the wrong language. It comes with their next message.
  From the database only: get_lang caches "en" in user_data for somebody
  who never chose, which is not a choice.
- At `logger.debug("The gift ad was not shown (%s)", type(exc).__name__)`:
  No user id in the log; before the claim nothing was recorded and the
  next message tries again, after it the one chance is spent.

### `_route_into()`

Deliver this message into one named conversation, whichever side sent
it. The sender's own id is what says the direction -- inbox holder one way, guest the other -- and a sender who is neither is told the same thing a
reply to something untracked is told, rather than being guessed at.

`peer_anchor` is where the delivered copy should hang on the receiving
side: the other chat's counterpart of the message this one replied to.
None means there is nothing better to say, and the conversation's newest
anchor is used -- which is the right answer for the two callers that
pass nothing. An album's second and third parts arrive with no reply of
their own and belong under the first part, and a message replying to the
line that announced the conversation is replying to something that
exists in one chat only.

### `handle_message()`

- At `album = _album_route(message)`:
  ```text
  0. A later part of an album goes exactly where the first part went,
     before any other question is asked. See _album_route: the parts
     after the first cannot be routed on their own merits, because the
     first part is what spends the opening exemption and what takes the
     Reply prompt back down. Sending three photos is one message from
     where the person is sitting, and it has one destination.
  ```
- At `if message.reply_to_message:`:
  ```text
  1. Is this a reply landing on a tracked message? That is an inbox holder answering one specific conversation -- via the Reply button's
     forced reply, or an ordinary swipe-reply, both resolve identically.
     Checked FIRST and unconditionally, so it's correct even for someone
     who is simultaneously an inbox holder (of their own link) and a follower
     (of someone else's) -- explicit reply intent always wins.
  ```

  ```text
  Crucially: a reply that DOESN'T match anything tracked stops right
  here instead of falling through to step 2 below. Using Telegram's
  reply feature is an explicit, deliberate signal -- "this is meant for the conversation being replied to" -- so if that conversation
  can't be found, the right answer is to say so, never to silently
  reinterpret the message as something else (e.g. a fresh anonymous
  message to whatever OTHER inbox this user happens to still have an
  open session with, which is exactly how a stray reply on old chat
  history could otherwise land somewhere unintended).
  ```
- At `conv = await asyncio.to_thread(get_conversation, conversation_id)`:
  Both chats carry relay rows now, so a matched row says \*which\*
  conversation, not which direction -- \_route_into settles that
  from the sender's own id.

  A guest replying used to be allowed only into the one
  conversation anon_follower_state happened to point at -- which
  every link tap overwrites, so tapping any inbox link silently
  ended the guest's ability to answer everything they already
  had. The Reply buttons under those older messages stayed on
  screen and kept failing, and the message they got back blamed
  the age of the message rather than the tap. The relay row is
  the authority on which conversation a reply belongs to, it
  resolved correctly the whole time, and the inbox holder was never held to this rule. Now neither side is: anon_follower_state says
  where an UN-replied message goes, and nothing more.
- At `return await _route_into(update, context, conversation_id, conv,`:
  The bubble on the other side that this one is the same
  message as, so the delivered answer quotes what was
  actually answered rather than whatever was said last.
- At `routing = await asyncio.to_thread(get_routing_context, user.id, chat_id)`:
  2. A message that says nothing about where it is going.

  ```text
  Exactly one message in a conversation is allowed to: the guest's
  opening one, which has nothing above it to answer. Everything after
  it, from either side, has to name what it replies to -- an inbox
  link is posted somewhere public and one person ends up answering
  several, so "whichever thread was most recently touched" is not a
  guess worth making. It is also not a guess this bot got right: an
  inbox holder answering a new arrival, while still holding an open session
  from a link they had tapped themselves, had their answer delivered
  to that other conversation entirely -- and told "Sent".
  ```
- At `if (active and active.get("follower_first_msg_at") is None`:
  The exemption is spent on the conversation, and only while the
  conversation that was just opened is still the newest thing in the
  chat.

  It used to rest on follower_first_msg_at alone, which made it a
  property of the newest conversation rather than of the chat -- so a
  guest who was mid-conversation with one person, tapped a second link,
  and then typed an answer to the first had it delivered to the second as
  an opening message, and was told "Sent". Closing that with "is there
  any other thread in this chat at all" was too blunt in the other
  direction: it is a yes for every returning user, so from their second
  inbox onwards the ordinary flow -- tap a link, type a message -- was
  answered with a request to reply to the message being answered, pointing at a
  chat where the only thing to reply to was the line that had just told
  them to write.

  Order is what tells the two apart, and the ordering is free: relay
  message ids climb within a chat, so the newest relay row IS the last
  thing the bot put on screen. If that is this conversation's own
  opening line, nothing has happened since the tap and there is nothing
  else the message could be answering. If something from another thread
  arrived in between, there is, and the reply rule applies.
- At `lang = await i18n.get_lang(user.id, context)`:
  3. Neither -- generic nudge instead of silently swallowing the message.

### `_post_init()`

- At `await lifecycle.on_start(BOT_NAME)`:
  Before the first getUpdates -- see lifecycle.py: two containers polling
  one token is 409 Conflict and split updates, not a graceful overlap.

### `_post_stop()`

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
- At `app.add_handler(MessageHandler(filters.UpdateType.EDITED_MESSAGE, edited_message), group …`:
  Answered here and stopped here -- see edited_message. Registered after
  track_activity so an edit still counts as activity and still moves
  live_message's watermark, and before everything else so no handler
  written for update.message ever receives an update that has none.
- At `app.add_handler(MessageHandler((filters.TEXT | filters.REPLY) & ~filters.COMMAND,`:
  Runs after track_activity but before every other handler (including the
  anonymous-relay handle_message below) -- a no-op unless /feedback or a
  "Custom" donate button has just asked for something to be typed, in
  which case it takes the answer and stops it from also being relayed as
  an anonymous message (see awaited_text_received). Replies of any kind
  reach it, so a photo sent as the answer to /feedback is asked about
  rather than relayed; anything that is not answering it passes through.
- `app.add_handler(CommandHandler("dbdump", dbdump_command))` — operator-only
- `app.add_handler(CommandHandler("messageas", messageas_command))` — operator-only
- `app.add_handler(CommandHandler("status", status_command))` — operator-only
- At `app.add_handler(CommandHandler("language", language_command))`:
  ---- language: /en, /uz, /rus -- pick at first /start, change anytime ----
- At `app.add_handler(CommandHandler("privacy", privacy_command))`:
  ---- what the bot keeps, what it may be used for, and the erase button.
  Standalone for the same reason the language switches are: somebody who
  wants to know what is held on them, or wants it gone, should not have to
  finish whatever they were doing first. ----
- At `app.add_handler(CommandHandler("balance", balance_command))`:
  ---- donations (Telegram Stars) -- this bot's only Stars usage ----
- At `app.add_handler(MessageReactionHandler(`:
  A reaction on a bubble of a conversation goes to its twin -- see
  relay_reaction. Nothing else in this group matches a reaction: every
  handler above is for a message or a button.
- At `app.add_handler(MessageHandler(filters.ChatType.PRIVATE & ~filters.COMMAND, handle_messa …`:
  Everything else non-command in a private chat is either an inbox holder's reply or a follower's question -- handle_message() tells them apart.
- At `app.add_handler(MessageHandler(filters.COMMAND, unknown_command))`:
  Registered last so every real command above gets first shot -- only
  reached by a slash-command this bot doesn't actually have.
- At `app.add_handler(TypeHandler(Update, reactions_update_note), group=1)`:
  After everything, in a group of its own: the one-time note that
  reactions now travel, for whoever was here before they did.
- At `app.add_handler(TypeHandler(Update, promo_ad_note), group=2)`:
  And after that note, the 220 gift's ad -- a group of its own for the same
  reason: one handler runs per group, and the ad must come last.
- At `family_link.attach(app, BOT_NAME, "AnonBot", START_TIME)`:
  ManagerBot's link: heartbeats, crash/donation events, and the queue it
  uses to run this bot's operator-only commands remotely. Never raises --
  with no shared database reachable the bot just runs on its own.
- At `app.run_polling(**lifecycle.polling_kwargs(`:
  A 30-second long poll is the same latency as the default 10 -- Telegram
  answers the moment an update exists -- for a third of the HTTP requests.
  allowed_updates lists every kind this bot has a handler for, so Telegram
  stops sending the rest rather than this process parsing and dropping it.
  Reactions have to be asked for by name: an empty list means everything
  except them.
