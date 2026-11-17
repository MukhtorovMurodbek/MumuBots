# `group_mode.py`

[↑ sticker_bot](README.md) · [code](group_mode.py)

StickerBot in group chats, and retagging a sticker by replying to it (2.2.0).

2026-10-28: added to a group chat, the bot lists the sticker packs the members of that group
may edit; to add an image or sticker, a member replies to that media with a command. And, so
that nobody's packs are shown against their will, each pack has a switch: private (the
default) or public. A private pack is answered in a group with a no-access reply; for others to
edit it, its co-edit link can be posted to the group, and everyone who opens it can edit there.

```text
How that works here:
  * A pack is private unless its creator turns it public (/mypacks -> the pack -> 🌐).
  * The Bot API cannot list a group's members, so the bot learns them as they use it: each
    command sent to it in a group notes the sender as a member (db.group_members, pruned
    after ACTIVITY_RETENTION_DAYS, forgotten when they leave or the bot does).
  * /packs lists the public packs whose creator or a co-editor is such a member, numbered.
  * Reply to a picture, GIF, video or sticker with /add <number> (or /add <pack link>): it
    goes into that pack, if the sender may add to it -- its creator or a co-editor anywhere,
    anybody in a group where the pack is listed. Anyone else is told they have no access.
    /add on its own uses the pack the sender last added to in this group.
  * A private pack's co-edit link, posted in the group, makes everybody who opens it a
    co-editor: from then on they can add to it there too.
  * Nothing is ever deleted from a group (2026-10-28: the pack's creator can delete through Telegram's own interface, so deleting stays off in groups). /add on a
    sticker that is already in the pack asks whether to add it again, and says that only the
    pack's creator deletes stickers (ask_add_again). In a private chat the same question
    answers anybody who sends such a sticker; whoever may delete there (the creator, or a co-editor the creator allows) gets "Remove it" in place of "Don't add".
  * In a group, /emoji 😎 as a reply to a sticker changes that sticker's emoji; a reply to the
    bot's "Added" message needs only the emoji. /emoji alone, as a reply (a tap in the command
    menu sends it at once), is answered with a request for the emoji as a reply to that
    answer. In a private chat an emoji alone, as a reply to a sticker, does it too.
```

In privacy mode (the default) a bot in a group sees only commands, replies to its own
messages and service messages, which is all of the above needs. It does not see a reply to
somebody else's sticker, which is why a sticker there is pointed at with /emoji.

Everything from a group chat goes through group_router, in a handler group of its own
before the private chat's, and stops there: nothing meant for a private chat is ever said
to a whole group.

### `_command()`

("add", ["2"]) from "/add@ThisBot 2"; ("", []) for another bot's command; None for a
message that is not a command.

### `_target()`

The message `message` replies to -- not a forum topic's first message, which every
message in a topic replies to without anyone choosing it.

### `_note_member()`

Note the sender as a member of this group, and turn any invitation of their
@username into co-editing (db.claim_invites): somebody invited who only ever uses the
bot in a group has to be let in there too. Also counts them as having used the bot: in a
group only what somebody sends to the bot does (shared_features.note_active).

_At `async def _may_retag(message, lang: str, pack_name: str, user_id: int, chat_id: "int | N …`:_

---------- changing a sticker's emoji (private chats and groups) ----------

### `_may_retag()`

Whether this person may change emoji in `pack_name`; told why not when they may not.

### `change_emoji()`

Retag the sticker `target` stands for (sticker_ops.sticker_of) with the emoji in
`text`, if the sender may edit its pack. `chat_id` is the group, for public packs.

### `ask_for_emoji()`

/emoji with no emoji, as a reply to a sticker -- which is how a tap in the command menu
sends it. Answers with a request for the emoji, as a reply to that answer: a reply to the
bot's own message is what the bot sees in a group, and the answer is remembered as standing
for the sticker (sticker_ops). False when `target` stands for no sticker.

_At `async def _packs_text(lang: str, chat_id: int) -> str:`:_

---------- the group commands ----------

### `hello()`

The greeting when the bot is added, and /start and /help in a group: what it does
here, then the packs this group can add to.

### `_chosen_pack()`

The pack an /add means: a number from /packs, a pack link or name, or -- with
nothing -- the one this person last added to here, or the group's only pack.

- At `packs = await asyncio.to_thread(group_packs, chat_id)`:
  Listed again rather than remembered: the same order /packs showed, and
  still right after a restart.

### `add_command()`

- At `refusal = await refuse_new_work(lang, user.id, chat.id)`:
  An encode outlasts a shutdown's grace period; see bot.py's receive_video_media.

### `add_media()`

Download, convert and add one picture, clip or sticker to `pack_name`, on its creator's behalf. `working` is the bot's own "⏳" message, which becomes the result: "Added as #N", or
why not. It and the message the media came in (`source_id`) are remembered as standing
for the new sticker. Returns the new sticker's file id, or None when it failed.

_At `DUP_PREFIX = "dup:"`:_

---------- a sticker that is already in the pack ----------

2026-10-28: a sticker that is already in the pack is answered with that fact and a question --
add it again or not -- and, in a group chat, a note that only the creator deletes stickers. The
same goes by default for somebody filling someone else's pack, unless the creator lets the link
grant delete rights.

### `ask_add_again()`

Answer a sticker that is already in `pack_name` with a question and two buttons only the
sender can use: "Add again" and "Remove it" for whoever may delete (`can_remove`: the creator, or a co-editor the creator allows -- the same day: a question with a button each for duplicating and removing); "Add again" and "Don't add", with the reminder that only the pack's creator deletes, for anyone else, and for everybody in a group. bot.py's
already_in_pack_chosen answers the taps.

### `add_again()`

"Add again" was tapped (bot.py's already_in_pack_chosen, which checked who tapped): the
access is checked again, for somebody removed from the pack since, and the question
becomes the "Added" reply.

- At `context.user_data["last_sticker_id"] = new_id`:
  The pack being filled in this chat: an emoji sent next retags this copy.

### `emoji_command()`

/emoji 😎, as a reply to a sticker or to an "Added" message (/emoji alone: ask_for_emoji).

_At `async def group_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:`:_

---------- the router, and joining and leaving ----------

### `group_router()`

Every message from a group chat. Always ends the update here
(ApplicationHandlerStop); an error is handed to the error handler as a task of its own,
so that it is reported like any other while the update still stops here.

- At `await asyncio.to_thread(move_group, message.migrate_from_chat_id, chat.id)`:
  The group became a supergroup, which has an id of its own: who uses the bot
  there comes along.
- At `target = _target(message)`:
  An emoji alone counts only as a reply to the bot's own message. A group that
  makes the bot an admin shows it every message, and an emoji under somebody's
  sticker there is a reaction, not a request (/emoji is how to ask for that).

### `bot_membership_changed()`

Added to a group: say hello, with its packs. Removed: forget the group.
