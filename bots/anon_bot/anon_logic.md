# `anon_logic.py`

[↑ anon_bot](README.md) · [code](anon_logic.py)

Pure-ish logic for AnonBot -- StickerBot-family sibling #4 (see
ARCHITECTURE.md). Same split as image_utils.py / convert_utils.py / video.py
in the other bots: this file owns the parts that don't need to know about
ApplicationBuilder/handlers, and bot.py owns the Telegram wiring.

relay_content() delivers one message's content into another chat as a plain
(non-forwarded) message, optionally as a native reply-to and/or with a header
line, and optionally with buttons. It is the one piece that makes a reply
"look like they actually replied" on the receiving end -- and it is also what
lets either side's chat thread several simultaneous conversations via
Telegram's native reply-quote instead of one flat, confusing feed.

Everything a \*person\* wrote is relayed in italics; everything the bot says
for itself (headers, confirmations, prompts) stays upright. In a chat where
both arrive in the same bubble stream, that one difference is what shows at a glance which words are somebody else's.

_At `TEXT_LIMIT = 4096`:_

Telegram's own ceilings, and a little room. The limits are on the \*parsed\*
text, so measuring the escaped-and-tagged string over-counts -- which is the
direction to be wrong in: an occasional early split costs a second bubble,
and getting it wrong the other way costs the message entirely.

### `italicize()`

Someone else's words, marked as theirs. Escaped first: a guest who
types &lt;b>hi&lt;/b> should see &lt;b>hi&lt;/b> arrive, not bold text, and should
certainly not be able to close the tag this wraps them in.

### `split_to_fit()`

`body` cut into pieces that will each survive escaping, preferring to
break at a newline and then at a space.

A header costs characters the sender never sees, so a guest who filled
their message box exactly to Telegram's limit used to have the whole
thing rejected -- and be told "Couldn't deliver that message: Message is
too long", which reads as their fault. Splitting says everything they
wrote, in the order they wrote it.

- At `cut = budget`:
  Walk back from the largest slice that could possibly fit until the
  escaped form does, then back further to a sensible break.

## Flood control

An inbox link is meant to be posted somewhere public, and until now nothing
at all bounded how fast a stranger could write into it: Block was the only
defence, and Block is per Telegram account. This is deliberately in memory
rather than in the database -- one process holds the polling lease, so there
is exactly one counter to keep, and paying a round trip to a database in
another continent on every message to enforce a per-minute limit would cost
more than the limit saves. A redeploy forgets it, which is the right way to
fail: the worst case is one burst getting through after a restart.

### `message_allowed()`

0 if this person may send now, otherwise the whole seconds to wait.

### `new_conversation_allowed()`

0 if this person may open another conversation now, otherwise the
whole seconds to wait. Tapping a link is cheap for the tapper and costs
the inbox holder a row and a notification each time.

## Reactions

A reaction on one bubble of a conversation is put on its twin in the other
chat, by the bot -- so it shows beside the bot's picture and says nothing
about who reacted. Asked for on 2026-10-03 (a reaction put on a message in AnonBot should be visible to the other person), after a probe on TestBot showed that
Telegram does tell a bot about reactions in its own private chats, whatever
the Bot API's wording about administrators suggests.

What a bot may react with is narrower than what a person may: one reaction
per message, from the standard set below, never a custom emoji that is not
already on the message (a Telegram Premium one, in practice), never a paid
one. The list is Telegram's (Bot API 10.3, ReactionTypeEmoji) and is shown
in /help; Telegram still has the last word, because a reaction it has added
since is tried rather than refused, and one it has dropped comes back as
REACTION_INVALID. Nothing matches against this list -- what is passed on is
the exact emoji Telegram sent -- which is why it does not matter that four
of these are written without the variation selector (U+FE0F) that
python-telegram-bot's own list carries.

_At `REACTION_NOTE_SECONDS = float(os.environ.get("ABOT_REACTION_NOTE_SECONDS", "8"))`:_

How long the note about a reaction that could not be passed on stays up.
Telegram's own pop-up -- the one a button tap can show -- only exists as an
answer to a button, so a reaction cannot have one; a silent message that
takes itself down again is the nearest thing a bot has. Eight seconds, not
the few of a pop-up: it is two lines, it opens with what the reader has lost
("The other person won't see that reaction"), and somebody who reacted far
up the chat has to scroll down to it first. The requirement: the most important information has to be readable.

_At `REACTION_NOTE_EVERY_SECONDS = int(os.environ.get("ABOT_REACTION_NOTE_EVERY_SECONDS", "30 …`:_

And how often one person can be shown it, so a run of Premium reactions is
one note rather than a note per tap.

### `pick_reaction()`

What to put on the twin, and which note the reactor needs, if any.

`old` and `new` are the person's reactions before and after, as Telegram
sends them. The answer is a list of at most one emoji -- the bot shows
one -- and an empty list means take the bot's reaction off. Of several
standard reactions, the one just added is the one passed on, which is
what the person was looking at when they tapped; taking one off passes on
whichever standard one is left.

The note is "reaction_not_passed" when what was just added cannot be shown
at all (a custom emoji, a paid reaction), "reaction_one_only" when it can
but the person now has more than one, and None otherwise.

### `reaction_note_allowed()`

True at most once per REACTION_NOTE_EVERY_SECONDS for one person.

### `relay_content()`

Delivers `message`'s content into `to_chat_id`, preserving whatever it
is (text, photo, video, voice, sticker, document, ...) with no "Forwarded
from" tag -- copy_message handles every content type Telegram lets a bot
copy, so there is no need to branch on message.photo / message.video /
etc. here, the way convert_bot's file handling does.

Plain text gets its own path (send_message, not copy_message) purely so
`header` can be prepended into the SAME bubble -- copy_message has no way
to alter a text message's body. A captioned media message keeps one
bubble too: copy_message \*can\* replace a caption, so the header and the
italicised caption go together underneath it. Only media with no caption
of its own needs the header as a separate line above.

`reply_to_message_id` is what makes the delivered message show up as a
genuine Telegram reply on the receiving end. If that anchor message was
since deleted, Telegram rejects the whole send -- so this retries once
without it rather than losing the message entirely.

Returns the list of Message/MessageId objects actually sent, in send
order (normally one; two for uncaptioned media with a header).

#### `relay_content._compose()`

Bot's own header upright, the person's own words in italics.

- At `text = _compose(part) if n == 0 else italicize(part)`:
  The header goes on the first bubble only, and the buttons on the
  last, so a message split in three still reads as one thing with
  one Reply button under the end of it.
- At `parts = _pieces(message.caption, TEXT_LIMIT)`:
  Too long to ride along under the picture. The words go above it as
  their own bubbles and the media follows, carrying the buttons --
  which is the same shape uncaptioned media already uses, and beats
  either truncating what somebody wrote or refusing the whole thing.
- `reply_to_message_id = None` — already visually anchored by the header line right above
