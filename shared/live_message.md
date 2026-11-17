# `live_message.py`

[↑ shared](README.md) · [code](live_message.py)

One bot message that keeps evolving -- for as long as it is still the
last thing in the chat.

Editing a message in place is the right shape for anything that changes:
a menu the user is walking through, a "converting..." that becomes
"added", a pack status line that counts up. It is how fstik and BotFather
read, and it beats a fresh message per step.

It has one failure mode, and it is invisible from the bot's side. The edit
lands wherever that message already is. If the user has typed anything
since -- another photo, "ok", a stray sticker -- the message being edited
is no longer at the bottom of the chat, so the user is looking at their own
message while the bot silently rewrites something above it. On a phone that
is off-screen. The bot answered; nobody saw it.

So every evolving message here carries a watermark: the id of the newest
message the \*user\* had sent in that chat at the moment the bot last wrote
it. Before each edit that watermark is compared with what has arrived
since:

```text
nothing new        -> edit in place, exactly as before
the user spoke     -> send a new message, and evolve that one from now on
```

Only the user's own messages count. A button tap does not (the message they
tapped \*is\* the live one), and neither does the bot's own chatter -- except
where a bot deliberately sends something underneath a live message, which
is what bump() is for.

The watermark lives in this module, keyed by chat, in a bounded dict. It is
deliberately not persisted: a process that has just started has no idea what
the chat looks like, and the safe assumption there is "something happened in the meantime", which is what an empty watermark already means -- the
first write after a restart sends a new message instead of editing a message
the user may have scrolled far past. save()/restore() carry the \*handle\*
across a restart (see lifecycle.py), not the watermark.

This file is copied byte-identically into every bot, like shared_features.py
and family_link.py -- each bot is its own repo and its own deployment, so
nothing is imported across folders.

_At `_CONTENT_REFUSED = re.compile(`:_

Refusals that mean the message itself is wrong -- never a passing state of the chat.

_At `MAX_TRACKED_CHATS = 2048`:_

Watermarks are per chat and tiny (two ints), but a bot that has met a lot
of people would still grow one entry per chat forever. The oldest are
dropped once there are more than this many; losing one costs a single
unnecessary new message, which is the safe direction to fail in.

_At `_SEND_ONLY_KWARGS = frozenset({`:_

Message-only kwargs that edit_message_text has no equivalent for. Passing
one to an edit is a TypeError, so they are dropped there rather than made
every caller's problem -- a caller that sets do_quote is describing the
first send, and there is nothing sensible for an in-place edit to do with
it anyway.

## The watermark

### `note_incoming()`

Remember that the user has spoken in this chat. Telegram's message
ids climb within a chat, so the largest one seen is the newest.

### `note_update()`

Called for every update from a private chat, from the same place each
bot already counts active users (track_activity, before any real handler).
Not for group chats, where a progress message is edited where it is.

Deliberately only update.message: a callback query carries the \*bot's\*
own message -- the one holding the button that was tapped -- and counting
that would mean every tap immediately declared the menu stale and
replaced it with a fresh copy of itself below the old one.

### `bump()`

Same effect as the user having spoken: whatever live message this
chat had is now buried and the next write to it starts fresh.

For the case the watermark cannot see -- the bot itself sending
something \*underneath\* a live message. Most bots here have no such
place (the live message is the only thing they send), which is why this
is an explicit call rather than a wrapper around every send.

## The handle

### `LiveMessage`

A handle on one evolving message.

Hold it for as long as the thing it is reporting on lasts -- a local
variable for a single conversion, or user_data via save()/restore() for
something that spans several messages, like a pack-editing session.

#### `LiveMessage.__init__()`

- At `self.watermark = _last_incoming.get(chat_id, 0) if watermark is None else watermark`:
  Where the chat stood when this message was written. Normally this
  is redundant -- it never exceeds what note_incoming() has already
  recorded -- and it earns its place in exactly one case: a handle
  restored into a process whose watermark table is empty, where it
  is the only evidence left of how far the chat had got.

- At `@classmethod`:
  ---- making one ----

#### `LiveMessage.reply_to()`

Start a live message as a reply to the one that triggered it --
the ordinary "user sent a video, bot says converting..." case.

#### `LiveMessage.adopt()`

Take over a message the bot already sent -- typically the one
holding the button that was just tapped, so a menu keeps evolving in
the same place instead of starting a new one below itself.

- At `def save(self) -> dict:`:
  ---- carrying one across handlers, and across restarts ----

#### `LiveMessage.save()`

A plain dict for user_data. JSON-safe on purpose: this is what
lets an editing session survive a redeploy (see lifecycle.py).

#### `LiveMessage.restore()`

The other half of save(). None for anything that isn't one, so a
caller can hand it whatever user_data.get() returned.

The stored watermark and the live one are combined with max(), so a
handle carried across a restart is judged against the later of "how
far the chat had got when this was written" and "how far it has got
since this process started".

- At `async def set(self, bot, text: str, **kwargs) -> "LiveMessage":`:
  A note on restarts, since it looks like a gap and is not one: a process
  that has just come up has an empty watermark table, so on the face of
  it a restored handle could edit a message the user has since scrolled
  far past. It cannot, because Telegram holds undelivered updates for 24
  hours and hands them over on the first getUpdates -- so anything said
  during the downtime is recorded by note_update() before any handler
  that might write to this message runs.
- At `async def set(self, bot, text: str, **kwargs) -> "LiveMessage":`:
  ---- evolving it ----

#### `LiveMessage.set()`

Show `text`, in place if this message is still the last one in the
chat and as a new message otherwise. Returns self, re-pointed at
whichever message now carries the text, so callers can chain or just
ignore the result.

Raises only when Telegram refuses the message itself (\_CONTENT_REFUSED:
a button's data over 64 bytes, broken formatting, text too long) --
the bot's own bug, which error_handler then tells the person about.
Anything else is swallowed: a live message is a progress report, and
failing the operation it is reporting on because its own edit did not
land would be the wrong trade every time.

- At `return self`:
  Same text and same markup as last time. Nothing to do,
  and nothing wrong.
- At `if _CONTENT_REFUSED.search(str(exc)):`:
  Telegram refused the message itself -- a button's data over 64 bytes, broken
  formatting, text too long: the bot's own bug. Swallowed, the person saw nothing at all
  (TestBot, 2026-11-14), so it goes up to error_handler, which tells them.

#### `LiveMessage.finish()`

The last thing this message will ever say.

#### `LiveMessage.delete()`

Take it away entirely -- for a progress message whose result
arrives as a file or a photo of its own, where leaving "Downloading
..." above the thing it was waiting for reads as a second, stuck
request.

## The one-liner

### `edit_in_place()`

Rewrite one of the bot's own messages -- typically the one holding the
button that was just tapped -- or send a new one if the user has said
anything since. The single most common use of this file, and the drop-in
replacement for `await message.edit_text(...)`.

Returns the handle, which anything that keeps evolving the same message
should hold on to; anything that is finished with it can ignore the
result. Raises only on a message Telegram refuses outright (see
LiveMessage.set): a menu that could not be redrawn for any other reason is
not a reason to fail whatever the tap was actually asking for.

## The user_data form

Most bots keep exactly one live message per user (the status line of
whatever they are in the middle of), so the two calls below are the whole
API for that case: show() writes it, drop() forgets it.

### `show()`

Write this user's live message, creating it on first call.
