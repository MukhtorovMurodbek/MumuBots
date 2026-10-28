"""StickerBot in group chats, and retagging a sticker by replying to it (2.2.0).

The owner (2026-10-28): "when added to a group chat, it should list the stickerpacks that
members of that group has access to edit. For example to add a certain image/sticker they
have to reply to that media and send a command." And, so nobody's packs are shown against
their will: "add a switch to each sticker pack - private (default) or public. If the pack
is private, the bot in group chat will reply with something like 'you dont have access to
this pack'. For others to enable editing, each person can send their co-edit link to the
group, everyone will click it and start editing in the gc."

How that works here:
  * A pack is private unless its owner turns it public (/mypacks -> the pack -> 🌐).
  * The Bot API cannot list a group's members, so the bot learns them as they use it: each
    command sent to it in a group notes the sender as a member (db.group_members, pruned
    after ACTIVITY_RETENTION_DAYS, forgotten when they leave or the bot does).
  * /packs lists the public packs whose owner or a co-editor is such a member, numbered.
  * Reply to a picture, GIF, video or sticker with /add <number> (or /add <pack link>): it
    goes into that pack, if the sender may add to it -- its owner or a co-editor anywhere,
    anybody in a group where the pack is listed. Anyone else is told they have no access.
    /add on its own uses the pack the sender last added to in this group.
  * A private pack's co-edit link, posted in the group, makes everybody who opens it a
    co-editor: from then on they can add to it there too.
  * Nothing is ever deleted from a group (the owner, 2026-10-28: "The owner of the pack can
    delete it through telegram's own UI so leave deleting off for groups"). /add on a
    sticker that is already in the pack asks whether to add it again, and says that only the
    pack's owner deletes stickers (ask_add_again). In a private chat the same question
    answers anybody who sends such a sticker; whoever may delete there (the owner, or a
    co-editor the owner allows) gets "Remove it" in place of "Don't add".
  * In a group, /emoji 😎 as a reply to a sticker changes that sticker's emoji; a reply to the
    bot's "Added" message needs only the emoji. /emoji alone, as a reply (a tap in the command
    menu sends it at once), is answered with a request for the emoji as a reply to that
    answer. In a private chat an emoji alone, as a reply to a sticker, does it too.

In privacy mode (the default) a bot in a group sees only commands, replies to its own
messages and service messages, which is all of the above needs. It does not see a reply to
somebody else's sticker, which is why a sticker there is pointed at with /emoji.

Everything from a group chat goes through group_router, in a handler group of its own
before the private chat's, and stops there: nothing meant for a private chat is ever said
to a whole group.
"""
import asyncio
import logging

from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ApplicationHandlerStop, ContextTypes

import i18n
import lifecycle
import sticker_ops
from db import (
    claim_invites,
    forget_group,
    forget_group_member,
    group_packs,
    move_group,
    note_group_member,
    pack_access,
)
from emoji_utils import DEFAULT_EMOJI, looks_like_emoji_message, split_emoji
from import_utils import parse_telegram_pack_source
from shared_features import NO_PREVIEW, note_active, record_error, refuse_new_work, sibling_bots_keyboard_row
from video_sticker import ConversionError

logger = logging.getLogger(__name__)

BOT_NAME = "stickerbot"
GROUP_TYPES = ("group", "supergroup")
GROUP_COMMANDS = [
    BotCommand("packs", "Packs you can add to here"),
    BotCommand("add", "Reply to a picture: add it to a pack"),
    BotCommand("emoji", "Reply to a sticker: change its emoji"),
    BotCommand("help", "How I work in a group"),
]


def _command(message, bot_username: "str | None") -> "tuple[str, list[str]] | None":
    """("add", ["2"]) from "/add@ThisBot 2"; ("", []) for another bot's command; None for a
    message that is not a command."""
    text = (message.text or "") if message is not None else ""
    if not text.startswith("/"):
        return None
    words = text.split()
    name, _, addressee = words[0][1:].partition("@")
    if addressee and (not bot_username or addressee.lower() != bot_username.lower()):
        return "", []
    return name.lower(), words[1:]


def _target(message):
    """The message `message` replies to -- not a forum topic's first message, which every
    message in a topic replies to without anyone choosing it."""
    reply = getattr(message, "reply_to_message", None)
    if reply is None or getattr(reply, "forum_topic_created", None) is not None:
        return None
    return reply


def _sent_by(message, user_id: int) -> bool:
    return getattr(getattr(message, "from_user", None), "id", None) == user_id


async def _note_member(context, chat_id: int, user) -> None:
    """Note the sender as a member of this group, and turn any invitation of their
    @username into co-editing (db.claim_invites): somebody invited who only ever uses the
    bot in a group has to be let in there too. Also counts them as having used the bot: in a
    group only what somebody sends to the bot does (shared_features.note_active)."""
    note_active(context, user)
    try:
        await asyncio.to_thread(note_group_member, chat_id, user.id)
        await asyncio.to_thread(claim_invites, user.id, user.username)
    except Exception:
        logger.warning("Could not note a group member", exc_info=True)


def _convert_keyboard():
    row = sibling_bots_keyboard_row(BOT_NAME, only="convertbot")
    return InlineKeyboardMarkup([row]) if row else None


# ---------- changing a sticker's emoji (private chats and groups) ----------

async def _may_retag(message, lang: str, pack_name: str, user_id: int, chat_id: "int | None") -> bool:
    """Whether this person may change emoji in `pack_name`; told why not when they may not."""
    access = await asyncio.to_thread(pack_access, pack_name, user_id, chat_id)
    if access is None:
        await message.reply_text(i18n.t(lang, "emoji_not_ours"))
        return False
    if not access["allowed"]:
        await message.reply_text(i18n.t(lang, "group_no_access", title=access["title"]))
        return False
    return True


async def change_emoji(update: Update, context: ContextTypes.DEFAULT_TYPE, target, text: str,
                       chat_id: "int | None" = None) -> None:
    """Retag the sticker `target` stands for (sticker_ops.sticker_of) with the emoji in
    `text`, if the sender may edit its pack. `chat_id` is the group, for public packs."""
    message, user = update.effective_message, update.effective_user
    lang = await i18n.get_lang(user.id, context)
    found = sticker_ops.sticker_of(target)
    if found is None:
        await message.reply_text(i18n.t(lang, "emoji_target_unknown"))
        return
    pack_name, file_id = found
    if not await _may_retag(message, lang, pack_name, user.id, chat_id):
        return
    emojis = split_emoji(text)
    try:
        await context.bot.set_sticker_emoji_list(sticker=file_id, emoji_list=emojis)
    except Exception as exc:
        await message.reply_text(i18n.t(lang, "retag_failed", error=exc))
        return
    await message.reply_text(i18n.t(lang, "retagged_success", emojis=" ".join(emojis)))


async def ask_for_emoji(update: Update, context: ContextTypes.DEFAULT_TYPE, target,
                        chat_id: "int | None" = None) -> bool:
    """/emoji with no emoji, as a reply to a sticker -- which is how a tap in the command menu
    sends it. Answers with a request for the emoji, as a reply to that answer: a reply to the
    bot's own message is what the bot sees in a group, and the answer is remembered as standing
    for the sticker (sticker_ops). False when `target` stands for no sticker."""
    found = sticker_ops.sticker_of(target)
    if found is None:
        return False
    message, user = update.effective_message, update.effective_user
    lang = await i18n.get_lang(user.id, context)
    if await _may_retag(message, lang, found[0], user.id, chat_id):
        sent = await message.reply_text(i18n.t(lang, "emoji_send_reply"))
        sticker_ops.remember(sent.chat_id, sent.message_id, *found)
    return True


# ---------- the group commands ----------

async def _packs_text(lang: str, chat_id: int) -> str:
    packs = await asyncio.to_thread(group_packs, chat_id)
    if not packs:
        return i18n.t(lang, "group_packs_none")
    lines = [i18n.t(lang, "group_packs_head")]
    lines += [f"{n}. {title} — t.me/addstickers/{name}" for n, (name, title) in enumerate(packs, 1)]
    return "\n".join(lines) + "\n\n" + i18n.t(lang, "group_packs_tail")


async def packs_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat, user = update.effective_chat, update.effective_user
    lang = await i18n.get_lang(user.id, context)
    await _note_member(context, chat.id, user)
    await update.effective_message.reply_text(await _packs_text(lang, chat.id), link_preview_options=NO_PREVIEW)


async def hello(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """The greeting when the bot is added, and /start and /help in a group: what it does
    here, then the packs this group can add to."""
    chat, user = update.effective_chat, update.effective_user
    lang = await i18n.get_lang(user.id, context) if user is not None else "en"
    if user is not None:
        await _note_member(context, chat.id, user)
    text = i18n.t(lang, "group_hello") + "\n\n" + await _packs_text(lang, chat.id)
    await context.bot.send_message(chat.id, text, link_preview_options=NO_PREVIEW)


async def _chosen_pack(context, chat_id: int, user_id: int, args: "list[str]") -> "str | None":
    """The pack an /add means: a number from /packs, a pack link or name, or -- with
    nothing -- the one this person last added to here, or the group's only pack."""
    if args:
        wanted = " ".join(args).strip()
        if wanted.isdecimal():
            # Listed again rather than remembered: the same order /packs showed, and
            # still right after a restart.
            packs = await asyncio.to_thread(group_packs, chat_id)
            number = int(wanted)
            return packs[number - 1][0] if 1 <= number <= len(packs) else None
        return parse_telegram_pack_source(wanted)
    last = (context.chat_data.get("last_pack") or {}).get(user_id)
    if last:
        return last
    packs = await asyncio.to_thread(group_packs, chat_id)
    return packs[0][0] if len(packs) == 1 else None


async def add_command(update: Update, context: ContextTypes.DEFAULT_TYPE, args: "list[str]") -> None:
    message, chat, user = update.effective_message, update.effective_chat, update.effective_user
    lang = await i18n.get_lang(user.id, context)
    await _note_member(context, chat.id, user)
    target = _target(message)
    found = sticker_ops.media_of(target) if target is not None else None
    if found is None:
        animated = target is not None and target.sticker is not None and target.sticker.is_animated
        await message.reply_text(i18n.t(lang, "animated_not_supported" if animated else "group_add_need_reply"))
        return
    pack_name = await _chosen_pack(context, chat.id, user.id, args)
    if pack_name is None:
        await message.reply_text(i18n.t(lang, "group_add_which" if not args else "group_pack_unknown"))
        return
    access = await asyncio.to_thread(pack_access, pack_name, user.id, chat.id)
    if access is None:
        await message.reply_text(i18n.t(lang, "group_pack_unknown"))
        return
    if not access["allowed"]:
        await message.reply_text(i18n.t(lang, "group_no_access", title=access["title"]))
        return
    context.chat_data.setdefault("last_pack", {})[user.id] = pack_name

    kind, file_id = found
    if target.sticker is not None and await sticker_ops.in_pack(context.bot, pack_name, target.sticker):
        await ask_add_again(update, context, lang, pack_name, access["title"], kind, file_id, in_group=True)
        return
    if kind == "video":
        # An encode outlasts a shutdown's grace period; see bot.py's receive_video_media.
        refusal = await refuse_new_work(lang, user.id, chat.id)
        if refusal:
            await message.reply_text(refusal)
            return
    working = await message.reply_text(
        i18n.t(lang, "converting_video" if kind == "video" else "group_adding"))
    await add_media(context, lang, chat.id, working, kind, file_id, pack_name, access["owner_id"],
                    access["title"], source_id=target.message_id)


async def add_media(context, lang: str, chat_id: int, working, kind: str, file_id: str, pack_name: str,
                    owner_id: int, title: str, source_id: "int | None" = None) -> "str | None":
    """Download, convert and add one picture, clip or sticker to `pack_name`, on its owner's
    behalf. `working` is the bot's own "⏳" message, which becomes the result: "Added as #N", or
    why not. It and the message the media came in (`source_id`) are remembered as standing
    for the new sticker. Returns the new sticker's file id, or None when it failed."""
    try:
        raw = await sticker_ops.download(context.bot, file_id)
        if kind == "video":
            async with lifecycle.busy(chat_id, i18n.t(lang, "restarting_send_again")):
                input_sticker = await sticker_ops.video_sticker(raw, lang)
        else:
            input_sticker = await sticker_ops.static_sticker(raw)
        new_id, number = await sticker_ops.add_to_pack(context.bot, owner_id, pack_name, input_sticker)
    except ConversionError as exc:
        record_error(exc)
        await working.edit_text(i18n.t(lang, "video_convert_failed_redirect", error=exc),
                                reply_markup=_convert_keyboard())
        return None
    except Exception as exc:
        logger.exception("Adding a sticker failed")
        record_error(exc)
        await working.edit_text(sticker_ops.error_note(exc, lang))
        return None
    await working.edit_text(i18n.t(lang, "sticker_added_to", title=title, n=number, emoji=DEFAULT_EMOJI))
    sticker_ops.remember(chat_id, working.message_id, pack_name, new_id)
    if source_id is not None:
        sticker_ops.remember(chat_id, source_id, pack_name, new_id)
    return new_id


# ---------- a sticker that is already in the pack ----------
#
# The owner (2026-10-28): "When someone sends an existing sticker, bot should say that this
# image is already present and if they want to duplicate it or not, and that only the owner
# can delete a sticker in group chats. Same applies when someone edits someone else's pack by
# default, but let the owner pick if they want to grant delete rights to the link."

DUP_PREFIX = "dup:"


async def ask_add_again(update: Update, context: ContextTypes.DEFAULT_TYPE, lang: str, pack_name: str,
                        title: str, kind: str, file_id: str, in_group: bool, can_remove: bool = False) -> None:
    """Answer a sticker that is already in `pack_name` with a question and two buttons only the
    sender can use: "Add again" and "Remove it" for whoever may delete (`can_remove`: the owner,
    or a co-editor the owner allows -- the owner, same day: "ask to duplicate or remove, provide
    buttons for each"); "Add again" and "Don't add", with the reminder that only the pack's
    owner deletes, for anyone else, and for everybody in a group. bot.py's
    already_in_pack_chosen answers the taps."""
    message, user, chat = update.effective_message, update.effective_user, update.effective_chat
    can_remove = can_remove and not in_group
    token = sticker_ops.hold({"user": user.id, "chat": chat.id, "group": in_group, "pack": pack_name,
                              "title": title, "kind": kind, "file_id": file_id})
    second = (InlineKeyboardButton(i18n.t(lang, "btn_remove_it"), callback_data=f"{DUP_PREFIX}{token}:r")
              if can_remove else
              InlineKeyboardButton(i18n.t(lang, "btn_dont_add"), callback_data=f"{DUP_PREFIX}{token}:n"))
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton(i18n.t(lang, "btn_add_again"), callback_data=f"{DUP_PREFIX}{token}:y"), second]])
    key = "already_in_pack_group" if in_group else "already_in_pack_owner" if can_remove else "already_in_pack"
    await message.reply_text(i18n.t(lang, key, title=title), reply_markup=keyboard)


async def add_again(update: Update, context: ContextTypes.DEFAULT_TYPE, lang: str, asked: dict) -> None:
    """"Add again" was tapped (bot.py's already_in_pack_chosen, which checked who tapped): the
    access is checked again, for somebody removed from the pack since, and the question
    becomes the "Added" reply."""
    query, user = update.callback_query, update.effective_user
    access = await asyncio.to_thread(pack_access, asked["pack"], user.id, asked["chat"] if asked["group"] else None)
    if access is None or not access["allowed"]:
        refusal = (i18n.t(lang, "coedit_pack_gone") if access is None
                   else i18n.t(lang, "group_no_access" if asked["group"] else "coedit_removed_access",
                               title=asked["title"]))
        await query.message.edit_text(refusal)
        return
    if asked["kind"] == "video":
        refusal = await refuse_new_work(lang, user.id, asked["chat"])
        if refusal:
            await query.message.edit_text(refusal)
            return
    await query.message.edit_text(i18n.t(lang, "converting_video" if asked["kind"] == "video" else "group_adding"))
    new_id = await add_media(context, lang, asked["chat"], query.message, asked["kind"], asked["file_id"],
                             asked["pack"], access["owner_id"], access["title"])
    if new_id and not asked["group"] and context.user_data.get("target_pack") == asked["pack"]:
        # The pack being filled in this chat: an emoji sent next retags this copy.
        context.user_data["last_sticker_id"] = new_id
        context.user_data["sticker_count"] = context.user_data.get("sticker_count", 0) + 1


async def emoji_command(update: Update, context: ContextTypes.DEFAULT_TYPE, args: "list[str]",
                        chat_id: "int | None") -> None:
    """/emoji 😎, as a reply to a sticker or to an "Added" message (/emoji alone: ask_for_emoji)."""
    message, user = update.effective_message, update.effective_user
    target = _target(message)
    text = " ".join(args)
    if target is not None and not text and await ask_for_emoji(update, context, target, chat_id):
        return
    if target is None or not looks_like_emoji_message(text):
        lang = await i18n.get_lang(user.id, context)
        await message.reply_text(i18n.t(lang, "emoji_usage"))
        return
    await change_emoji(update, context, target, text, chat_id)


# ---------- the router, and joining and leaving ----------

async def group_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Every message from a group chat. Always ends the update here
    (ApplicationHandlerStop); an error is handed to the error handler as a task of its own,
    so that it is reported like any other while the update still stops here."""
    message, chat, user = update.effective_message, update.effective_chat, update.effective_user
    try:
        if message is not None and message.migrate_from_chat_id:
            # The group became a supergroup, which has an id of its own: who uses the bot
            # there comes along.
            await asyncio.to_thread(move_group, message.migrate_from_chat_id, chat.id)
        elif message is None or user is None:
            pass
        elif message.left_chat_member is not None:
            left = message.left_chat_member
            if left.id == context.bot.id:
                await asyncio.to_thread(forget_group, chat.id)
            else:
                await asyncio.to_thread(forget_group_member, chat.id, left.id)
        else:
            command = _command(message, context.bot.username)
            if command is not None:
                name, args = command
                if name == "packs":
                    await packs_command(update, context)
                elif name == "add":
                    await add_command(update, context, args)
                elif name == "emoji":
                    await _note_member(context, chat.id, user)
                    await emoji_command(update, context, args, chat.id)
                elif name in ("start", "help"):
                    await hello(update, context)
            else:
                # An emoji alone counts only as a reply to the bot's own message. A group that
                # makes the bot an admin shows it every message, and an emoji under somebody's
                # sticker there is a reaction, not a request (/emoji is how to ask for that).
                target = _target(message)
                text = message.text or ""
                if (target is not None and _sent_by(target, context.bot.id)
                        and looks_like_emoji_message(text)
                        and sticker_ops.sticker_of(target) is not None):
                    await _note_member(context, chat.id, user)
                    await change_emoji(update, context, target, text, chat.id)
    except Exception as exc:
        error = exc

        async def report():
            raise error

        context.application.create_task(report(), update=update)
    raise ApplicationHandlerStop


async def bot_membership_changed(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Added to a group: say hello, with its packs. Removed: forget the group."""
    change = update.my_chat_member
    if change is None or change.chat.type not in GROUP_TYPES:
        return
    before, now = change.old_chat_member.status, change.new_chat_member.status
    if now in ("left", "kicked"):
        await asyncio.to_thread(forget_group, change.chat.id)
    elif before in ("left", "kicked"):
        await hello(update, context)
