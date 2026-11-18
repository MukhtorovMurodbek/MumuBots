import asyncio
import html
import logging
import os
from datetime import datetime, timedelta, timezone
from io import BytesIO

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from telegram import (
    BotCommand, InlineKeyboardButton, InlineKeyboardMarkup,
    LinkPreviewOptions, Update,
)

NO_PREVIEW = {"link_preview_options": LinkPreviewOptions(is_disabled=True)}
from telegram.constants import ParseMode
from telegram.error import BadRequest, Forbidden
from telegram.ext import (
    ApplicationBuilder,
    ApplicationHandlerStop,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    MessageReactionHandler,
    PreCheckoutQueryHandler,
    TypeHandler,
    filters,
)

import family_link
import i18n
import lifecycle
import live_message
import shared_features
from live_message import LiveMessage, edit_in_place
from anon_logic import (
    REACTION_NOTE_SECONDS,
    RELAYABLE_REACTIONS,
    message_allowed,
    new_conversation_allowed,
    pick_reaction,
    reaction_note_allowed,
    relay_content,
)
from db import (
    CONVERSATIONS_PER_PAGE,
    RELAY_RETENTION_DAYS,
    claim_promo_seen,
    claim_reactions_note,
    clear_follower_state,
    count_owner_conversation_states,
    first_relay_message,
    get_active_conversation_for_follower,
    get_anon_link_state,
    get_anon_stats,
    get_conversation,
    get_conversation_for_relay,
    get_reaction_route,
    get_relay_row,
    get_routing_context,
    list_owner_conversations,
    set_conversation_archived,
    list_anon_blocks_with_conversations,
    get_delivery_context,
    get_link_view,
    get_or_create_anon_link,
    get_user_language,
    set_user_language,
    init_db,
    record_relay_and_touch,
    record_anon_relays,
    mark_follower_opened,
    cancel_unopened_conversation,
    regenerate_anon_link,
    set_anon_blocked,
    set_anon_link_paused,
    start_new_anon_conversation,
    dump_database_csv_zip,
    count_active_users_since,
)
from shared_features import (
    note_stat,
    publish_profile,
    publish_commands,
    refresh_chat_menu,
    ERASE_PREFIX,
    delete_my_data_chosen,
    delete_my_data_command,
    privacy_command,
    terms_command,
    paysupport_command,
    attach_problem_reports,
    problem_report_callback,
    attach_flood_gate,
    attach_maintenance,
    awaited_text_received,
    feedback_command,
    FORCE_REPLY_KEY,
    HELP_PAGE_PATTERN,
    HELP_TOGGLE_PATTERN,
    full_help_command,
    help_page_button,
    help_page_name,
    help_page_text,
    help_keyboard,
    help_toggle,
    wants_full_help,
    CANCEL_PICK_ALL,
    CANCEL_PICK_NONE,
    CancelItem,
    ask_cancel_choice,
    cancel_choice_key,
    cancel_items,
    cancel_shared_item,
    finish_cancel_choice,
    keep_going,
    finish_cancel,
    flush_on_shutdown,
    language_keyboard,
    tune_runtime,
    donate_amount_chosen,
    donate_fiat_amount_chosen,
    donate_custom_button_chosen,
    balance_command,
    donate_command,
    donation_payment_callback,
    donation_precheckout_callback,
    maybe_donation_nudge,
    refuse_new_work,
    sibling_bots_blurb,
    sibling_bots_keyboard_row,
    setup_logging,
    error_handler,
    track_activity,
    build_status_text,
)

setup_logging(__file__)
logger = logging.getLogger(__name__)

START_TIME = datetime.now(timezone.utc)

BOT_TOKEN = os.environ.get("ABOT_TOKEN")
BOT_USERNAME = (os.environ.get("ABOT_USERNAME") or "").strip().lstrip("@") or None
BOT_NAME = "anonbot"

ADMIN_IDS = {int(x) for x in os.environ.get("ABOT_ADMIN_ID", "").split(",") if x.strip()}

POLL_TIMEOUT = int(os.environ.get("POLL_TIMEOUT", "30"))


def _is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


async def _deny(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await unknown_command(update, context)


async def _reply(update: Update, text: str, **kwargs):
    if update.message:
        return await LiveMessage.reply_to(update.message, text, **kwargs)
    return await edit_in_place(update.callback_query.message, update.get_bot(), text, **kwargs)


def build_help_text(lang: str, full: bool = False) -> str:
    if not full:
        text = i18n.t(lang, "help_brief").rstrip("\n")
    else:
        text = i18n.t(lang, "help_text", reactions=" ".join(RELAYABLE_REACTIONS)).rstrip("\n")
    blurb = sibling_bots_blurb(BOT_NAME, lang)
    return text + "\n\n" + blurb if blurb else text


def _help_page_link(lang: str) -> str:
    return i18n.t(lang, "helppage_link")


def _help_page_which(lang: str) -> str:
    return i18n.t(lang, "helppage_which", example=i18n.t(lang, "which_owner", conv_number=4))


HELP_PAGES = {"link": _help_page_link, "which": _help_page_which}
HELP_ALIASES = {}


BOT_COMMANDS = [
    BotCommand("start", "What this bot does, and how to start"),
    BotCommand("language", "Choose the language / Tilni tanlash / Выбрать язык"),
    BotCommand("link", "The inbox link, and its settings"),
    BotCommand("conversations", "Conversations"),
    BotCommand("which", "Which conversation is this? — reply to a message"),
    BotCommand("blocked", "Blocked people"),
    BotCommand("cancel", "Stop the current step"),
    BotCommand("help", "Everything this bot does"),
    BotCommand("feedback", "Suggest something, or report a problem"),
    BotCommand("balance", "⚡ credit balance"),
    BotCommand("donate", "Chip in for hosting costs"),
    BotCommand("paysupport", "Trouble with a payment"),
    BotCommand("privacy", "What is kept, and for how long"),
    BotCommand("terms", "What this bot may be used for"),
    BotCommand("deletemydata", "Delete all stored data"),
]

ADMIN_COMMANDS = [
    BotCommand("status", "🔒 Uptime, host, errors, active users"),
    BotCommand("messageas", "🔒 messageas <user_id> <text> — DM as this bot"),
    BotCommand("dbdump", "🔒 This bot's tables as a zip of CSVs"),
]


def _link_url(token: str) -> str:
    return f"https://t.me/{BOT_USERNAME}?start=q_{token}"


def _incoming_keyboard(conversation_id: int, lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(i18n.t(lang, "reply_button"), callback_data=f"aq_reply:{conversation_id}"),
                InlineKeyboardButton(i18n.t(lang, "block_button"), callback_data=f"aq_block:{conversation_id}"),
            ]
        ]
    )


def _follower_keyboard(conversation_id: int, lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(i18n.t(lang, "reply_button"), callback_data=f"aq_reply:{conversation_id}")]]
    )


def _follow_cancel_keyboard(conversation_id: int, lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(i18n.t(lang, "follow_cancel_button"), callback_data=f"aq_fcancel:{conversation_id}")]]
    )


async def _continue_start(update: Update, context: ContextTypes.DEFAULT_TYPE, lang: str, args):
    if args and args[0].startswith("q_"):
        return await start_follow_link(update, context, args[0][2:], lang)

    await _reply(
        update,
        i18n.t(lang, "start_greeting") + build_help_text(lang),
        reply_markup=help_keyboard(lang, False, [sibling_bots_keyboard_row(BOT_NAME)], HELP_PAGES),
    )
    await _knows_reactions(context, update.effective_user.id)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = await asyncio.to_thread(get_user_language, update.effective_user.id)
    if lang is None:
        context.user_data["pending_start_args"] = context.args
        await update.message.reply_text(i18n.LANGUAGE_PROMPT, reply_markup=language_keyboard())
        return

    context.user_data["lang"] = lang
    if context.args:
        await _continue_start(update, context, lang, context.args)
        return

    context.user_data.pop("pending_start_args", None)
    await _continue_start(update, context, lang, None)


async def language_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = await asyncio.to_thread(get_user_language, update.effective_user.id)
    if lang:
        context.user_data["lang"] = lang
    await update.message.reply_text(i18n.LANGUAGE_PROMPT, reply_markup=language_keyboard(lang))


async def _apply_language(update: Update, context: ContextTypes.DEFAULT_TYPE, lang: str):
    await asyncio.to_thread(set_user_language, update.effective_user.id, lang)
    context.user_data["lang"] = lang
    await refresh_chat_menu(context, update.effective_user.id, lang)
    pending = context.user_data.pop("pending_start_args", None) or []
    await _continue_start(update, context, lang, pending)


async def _set_language(update: Update, context: ContextTypes.DEFAULT_TYPE, lang: str):
    await update.message.reply_text(i18n.t(lang, "language_set_confirmation"))
    await _apply_language(update, context, lang)


async def set_language_en(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _set_language(update, context, "en")


async def set_language_uz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _set_language(update, context, "uz")


async def set_language_rus(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _set_language(update, context, "ru")


async def language_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    lang = query.data.split(":", 1)[1]
    await query.answer(i18n.t(lang, "language_set_confirmation"))
    await _apply_language(update, context, lang)


async def start_follow_link(update: Update, context: ContextTypes.DEFAULT_TYPE, token: str, lang: str):
    follower = update.effective_user
    owner_id, is_paused, blocked = await asyncio.to_thread(
        get_link_view, token, follower.id
    )
    if owner_id is None:
        await _reply(update, i18n.t(lang, "follow_link_invalid"))
        return

    if owner_id == follower.id:
        await _reply(update, i18n.t(lang, "follow_link_own"))
        return

    if blocked:
        await _reply(update, i18n.t(lang, "follow_link_blocked"))
        return

    if is_paused:
        await _reply(update, i18n.t(lang, "follow_link_paused"))
        return

    wait = new_conversation_allowed(follower.id)
    if wait:
        await _reply(update, i18n.t(lang, "too_fast", seconds=wait))
        return

    refusal = await refuse_new_work(lang, follower.id, update.effective_chat.id)
    if refusal:
        await _reply(update, refusal)
        return

    conv = await asyncio.to_thread(start_new_anon_conversation, owner_id, follower.id)
    opening = await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=i18n.t(lang, "follow_link_started"),
        reply_markup=_follow_cancel_keyboard(conv["id"], lang),
    )
    live_message.bump(opening.chat_id, opening.message_id)
    await asyncio.to_thread(
        record_anon_relays, follower.id, [opening.message_id], conv["id"]
    )


async def follow_cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    conversation_id = int(query.data.split(":", 1)[1])
    lang = await i18n.get_lang(update.effective_user.id, context)
    await query.answer()
    _, cancelled = await asyncio.gather(
        query.edit_message_text(i18n.t(lang, "follow_link_cancelled")),
        asyncio.to_thread(cancel_unopened_conversation, conversation_id, update.effective_user.id),
        return_exceptions=True,
    )
    if cancelled is not True:
        if isinstance(cancelled, Exception):
            logger.error("Cancelling conversation %s failed: %r", conversation_id, cancelled)
        try:
            await query.edit_message_text(i18n.t(lang, "follow_link_started"))
        except BadRequest:
            pass


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = await i18n.get_lang(update.effective_user.id, context)
    page = help_page_name(context, HELP_PAGES, HELP_ALIASES)
    if page:
        await update.message.reply_text(help_page_text(lang, HELP_PAGES, page),
                                        link_preview_options=shared_features.NO_PREVIEW)
        return
    full = wants_full_help(context)
    await update.message.reply_text(build_help_text(lang, full=full),
                                    reply_markup=help_keyboard(lang, full, (), HELP_PAGES))
    await _knows_reactions(context, update.effective_user.id)


_full_help = full_help_command(build_help_text, None, HELP_PAGES)


async def fullhelp_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _full_help(update, context)
    await _knows_reactions(context, update.effective_user.id)


def _link_message(token: str, paused: bool, lang: str, note: str = "",
                  stats: "dict | None" = None) -> tuple[str, InlineKeyboardMarkup]:
    status = i18n.t(lang, "link_status_paused" if paused else "link_status_active")
    text = i18n.t(lang, "link_message", status=status, url=html.escape(_link_url(token)))
    if stats:
        text += "\n\n" + i18n.t(lang, "stats_message",
                                 followers=stats["distinct_followers"],
                                 conversations=stats["conversations"])
    if note:
        text = f"{note}\n\n{text}"
    kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    i18n.t(lang, "link_button_resume" if paused else "link_button_pause"),
                    callback_data="anonlink:toggle",
                ),
                InlineKeyboardButton(i18n.t(lang, "link_button_newlink"), callback_data="anonlink:newlink"),
            ]
        ]
    )
    return text, kb


async def link_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    owner = update.effective_user
    lang = await i18n.get_lang(owner.id, context)
    token, is_paused = await asyncio.to_thread(get_or_create_anon_link, owner.id)
    stats = await asyncio.to_thread(get_anon_stats, owner.id)
    text, kb = _link_message(token, is_paused, lang, stats=stats)
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb, **NO_PREVIEW)


async def anonlink_toggle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    owner_id = update.effective_user.id
    lang = await i18n.get_lang(owner_id, context)
    state = await asyncio.to_thread(get_anon_link_state, owner_id)
    if not state:
        await query.answer(i18n.t(lang, "anonlink_no_link_yet"), show_alert=True)
        return
    new_paused = not state["is_paused"]
    await asyncio.to_thread(set_anon_link_paused, owner_id, new_paused)
    await query.answer(i18n.t(lang, "anonlink_paused_answer" if new_paused else "anonlink_resumed_answer"))
    stats = await asyncio.to_thread(get_anon_stats, owner_id)
    text, kb = _link_message(state["token"], new_paused, lang, stats=stats)
    await edit_in_place(query.message, context.bot, text, parse_mode=ParseMode.HTML,
                        reply_markup=kb, **NO_PREVIEW)


async def anonlink_newlink_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    owner_id = update.effective_user.id
    lang = await i18n.get_lang(owner_id, context)
    token, is_paused = await asyncio.to_thread(regenerate_anon_link, owner_id)
    await query.answer(i18n.t(lang, "anonlink_newlink_answer"))
    stats = await asyncio.to_thread(get_anon_stats, owner_id)
    text, kb = _link_message(token, is_paused, lang, stats=stats)
    await edit_in_place(query.message, context.bot, text, parse_mode=ParseMode.HTML,
                        reply_markup=kb, **NO_PREVIEW)


async def newlink_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    owner = update.effective_user
    lang = await i18n.get_lang(owner.id, context)
    token, _ = await asyncio.to_thread(regenerate_anon_link, owner.id)
    await update.message.reply_text(
        i18n.t(lang, "newlink_message", url=html.escape(_link_url(token))),
        parse_mode=ParseMode.HTML,
        **NO_PREVIEW,
    )


async def pause_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    owner = update.effective_user
    lang = await i18n.get_lang(owner.id, context)
    await asyncio.to_thread(get_or_create_anon_link, owner.id)
    await asyncio.to_thread(set_anon_link_paused, owner.id, True)
    await update.message.reply_text(i18n.t(lang, "pause_message"))


async def resume_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = await i18n.get_lang(update.effective_user.id, context)
    await asyncio.to_thread(set_anon_link_paused, update.effective_user.id, False)
    await update.message.reply_text(i18n.t(lang, "resume_message"))


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = await i18n.get_lang(update.effective_user.id, context)
    stats = await asyncio.to_thread(get_anon_stats, update.effective_user.id)
    await update.message.reply_text(
        i18n.t(lang, "stats_message", followers=stats["distinct_followers"], conversations=stats["conversations"])
    )


async def dbdump_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update.effective_user.id):
        return await _deny(update, context)
    status = await LiveMessage.reply_to(update.message, "Exporting the database...")
    try:
        data = await asyncio.to_thread(dump_database_csv_zip)
    except Exception as exc:
        await status.set(context.bot, f"⚠️ Export failed: {exc}")
        return
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M")
    await update.message.reply_document(document=BytesIO(data), filename=f"anonbot_db_{stamp}.zip")
    await status.delete(context.bot)


async def messageas_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update.effective_user.id):
        return await _deny(update, context)

    if len(context.args) < 2 or not context.args[0].lstrip("-").isdigit():
        await update.message.reply_text("Usage: /messageas <user_id> <message text>")
        return

    user_id = int(context.args[0])
    text = " ".join(context.args[1:])
    try:
        await context.bot.send_message(chat_id=user_id, text=text)
        await update.message.reply_text("✅ Sent.")
    except Exception as exc:
        await update.message.reply_text(f"⚠️ Couldn't send it: {exc}")


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update.effective_user.id):
        return await _deny(update, context)
    now = datetime.now(timezone.utc)
    users_hour = await asyncio.to_thread(count_active_users_since, now - timedelta(hours=1))
    users_since_start = await asyncio.to_thread(count_active_users_since, START_TIME)
    await update.message.reply_text(build_status_text(START_TIME, users_hour, users_since_start))


async def _blocked_list_text_and_kb(owner_id: int, lang: str):
    rows = await asyncio.to_thread(list_anon_blocks_with_conversations, owner_id)
    if not rows:
        return i18n.t(lang, "blocked_none"), None
    lines = [i18n.t(lang, "blocked_header")]
    kb = []
    for conversation_id, conv_numbers in rows:
        if conv_numbers:
            joined = ", ".join(f"#{n}" for n in conv_numbers)
            line = i18n.t(lang, "blocked_guest_line", conversations=joined)
            button = i18n.t(lang, "blocked_unblock_button", conversations=joined)
        else:
            line = i18n.t(lang, "blocked_guest_line_unknown")
            button = i18n.t(lang, "blocked_unblock_button_unknown")
        lines.append(line)
        if conversation_id is not None:
            kb.append([InlineKeyboardButton(button, callback_data=f"aq_unblockg:{conversation_id}")])
    return "\n".join(lines), InlineKeyboardMarkup(kb) if kb else None


async def blocked_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = await i18n.get_lang(update.effective_user.id, context)
    text, kb = await _blocked_list_text_and_kb(update.effective_user.id, lang)
    await update.message.reply_text(text, reply_markup=kb)


CONVS_PREFIX = "aq_convs:"
CONV_CARD_PREFIX = "aq_conv:"
CONV_ARCH_PREFIX = "aq_arch:"
CONV_JUMP_PREFIX = "aq_jump:"


def _ago(lang: str, when: str | None) -> str:
    if not when:
        return i18n.t(lang, "rel_now")
    try:
        seen = datetime.fromisoformat(when)
    except (TypeError, ValueError):
        return i18n.t(lang, "rel_now")
    if seen.tzinfo is None:
        seen = seen.replace(tzinfo=timezone.utc)
    seconds = max(0, int((datetime.now(timezone.utc) - seen).total_seconds()))
    if seconds < 90:
        return i18n.t(lang, "rel_now")
    if seconds < 3600:
        return i18n.t(lang, "rel_minutes", n=seconds // 60)
    if seconds < 86400:
        return i18n.t(lang, "rel_hours", n=seconds // 3600)
    return i18n.t(lang, "rel_days", n=seconds // 86400)


async def _conversations_view(owner_id: int, lang: str, archived: bool, page: int):
    counts = await asyncio.to_thread(count_owner_conversation_states, owner_id)
    total = counts["archived"] if archived else counts["open"]
    pages = max(1, -(-total // CONVERSATIONS_PER_PAGE))
    page = max(0, min(page, pages - 1))
    rows = await asyncio.to_thread(
        list_owner_conversations, owner_id, archived,
        page * CONVERSATIONS_PER_PAGE, CONVERSATIONS_PER_PAGE,
    )

    text = (i18n.t(lang, "convs_title_archived" if archived else "convs_title") + "\n"
            + i18n.t(lang, "convs_summary_blocked" if counts["blocked"] else "convs_summary",
                     **counts))
    if not rows:
        text += "\n\n" + i18n.t(lang, "convs_empty_archived" if archived else "convs_empty_open")

    here = f"{1 if archived else 0}:{page}"
    kb = [
        [InlineKeyboardButton(
            i18n.t(lang, "convs_row_button", conv_number=row["conv_number"],
                   when=_ago(lang, row["last_activity_at"])),
            callback_data=f"{CONV_CARD_PREFIX}{row['id']}:{here}")]
        for row in rows
    ]
    if pages > 1:
        flag = 1 if archived else 0
        kb.append([
            InlineKeyboardButton(i18n.t(lang, "convs_prev"),
                                 callback_data=f"{CONVS_PREFIX}{flag}:{max(0, page - 1)}"),
            InlineKeyboardButton(i18n.t(lang, "convs_page", page=page + 1, pages=pages),
                                 callback_data=f"{CONVS_PREFIX}{flag}:{page}"),
            InlineKeyboardButton(i18n.t(lang, "convs_next"),
                                 callback_data=f"{CONVS_PREFIX}{flag}:{min(pages - 1, page + 1)}"),
        ])
    kb.append([InlineKeyboardButton(
        i18n.t(lang, "convs_show_open" if archived else "convs_show_archived"),
        callback_data=f"{CONVS_PREFIX}{0 if archived else 1}:0")])
    return text, InlineKeyboardMarkup(kb)


def _conversation_card(conv: dict, lang: str, archived: bool, page: int):
    is_archived = conv.get("archived_at") is not None
    text = i18n.t(lang, "conv_card", conv_number=conv["conv_number"],
                  started=_ago(lang, conv.get("started_at")),
                  when=_ago(lang, conv.get("last_activity_at")))
    if is_archived:
        text += "\n" + i18n.t(lang, "conv_card_archived",
                              archived=_ago(lang, conv.get("archived_at")))
    back = f"{1 if archived else 0}:{page}"
    kb = [
        [InlineKeyboardButton(i18n.t(lang, "conv_jump_button"),
                              callback_data=f"{CONV_JUMP_PREFIX}{conv['id']}")],
        [
            InlineKeyboardButton(
                i18n.t(lang, "conv_unarchive_button" if is_archived else "conv_archive_button"),
                callback_data=f"{CONV_ARCH_PREFIX}{conv['id']}:{0 if is_archived else 1}:{back}"),
            InlineKeyboardButton(i18n.t(lang, "conv_back_button"),
                                 callback_data=f"{CONVS_PREFIX}{back}"),
        ],
    ]
    return text, InlineKeyboardMarkup(kb)


async def _owned_conversation(update, context, args, lang: str):
    conv = (await asyncio.to_thread(get_conversation, args[0])) if args else None
    if not conv or conv["owner_user_id"] != update.effective_user.id:
        await update.callback_query.answer(i18n.t(lang, "conv_gone"), show_alert=True)
        return None
    return conv


async def conversations_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = await i18n.get_lang(update.effective_user.id, context)
    text, kb = await _conversations_view(update.effective_user.id, lang, False, 0)
    await update.message.reply_text(text, reply_markup=kb)


async def conversations_page_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    lang = await i18n.get_lang(update.effective_user.id, context)
    args = _callback_ints(query, 2)
    await query.answer()
    if args is None:
        return
    text, kb = await _conversations_view(
        update.effective_user.id, lang, bool(args[0]), args[1])
    await edit_in_place(query.message, context.bot, text, reply_markup=kb)


async def conversation_card_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    lang = await i18n.get_lang(update.effective_user.id, context)
    args = _callback_ints(query, 3)
    conv = await _owned_conversation(update, context, args, lang)
    if conv is None:
        return
    await query.answer()
    text, kb = _conversation_card(conv, lang, bool(args[1]), args[2])
    await edit_in_place(query.message, context.bot, text, reply_markup=kb)


async def conversation_archive_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    lang = await i18n.get_lang(update.effective_user.id, context)
    args = _callback_ints(query, 4)
    conv = await _owned_conversation(update, context, args, lang)
    if conv is None:
        return
    archive = bool(args[1])
    await asyncio.to_thread(set_conversation_archived, conv["id"],
                            update.effective_user.id, archive)
    await query.answer(i18n.t(lang, "conv_archived_answer" if archive
                              else "conv_unarchived_answer"))
    conv["archived_at"] = datetime.now(timezone.utc).isoformat() if archive else None
    text, kb = _conversation_card(conv, lang, bool(args[2]), args[3])
    await edit_in_place(query.message, context.bot, text, reply_markup=kb)


ARCH_CMD_PREFIX = "aq_archcmd:"


async def archive_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    user_id = update.effective_user.id
    lang = await i18n.get_lang(user_id, context)
    target = message.reply_to_message
    if target is None:
        await message.reply_text(i18n.t(lang, "archive_needs_reply"))
        return

    conversation_id = await asyncio.to_thread(
        get_conversation_for_relay, message.chat_id, target.message_id)
    conv = (await asyncio.to_thread(get_conversation, conversation_id)
            if conversation_id else None)
    if not conv or user_id not in (conv["owner_user_id"], conv["follower_user_id"]):
        await message.reply_text(i18n.t(lang, "which_unknown"))
        return
    if conv["owner_user_id"] != user_id:
        await message.reply_text(i18n.t(lang, "archive_not_owner"))
        return
    if conv.get("archived_at"):
        await message.reply_text(
            i18n.t(lang, "archive_already", conv_number=conv["conv_number"]))
        return

    await message.reply_text(
        i18n.t(lang, "archive_confirm", conv_number=conv["conv_number"]),
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton(
                i18n.t(lang, "archive_button_yes", conv_number=conv["conv_number"]),
                callback_data=f"{ARCH_CMD_PREFIX}{conv['id']}:1"),
            InlineKeyboardButton(i18n.t(lang, "archive_button_no"),
                                 callback_data=f"{ARCH_CMD_PREFIX}{conv['id']}:0"),
        ]]),
    )


async def archive_command_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = update.effective_user.id
    lang = await i18n.get_lang(user_id, context)
    try:
        conv_id, archive = (int(part) for part in query.data.split(":")[1:3])
    except (ValueError, IndexError):
        await query.answer()
        return
    await query.answer()
    if not archive:
        await edit_in_place(query.message, context.bot, i18n.t(lang, "archive_kept"))
        return

    conv = await asyncio.to_thread(get_conversation, conv_id)
    if not conv or conv["owner_user_id"] != user_id:
        await edit_in_place(query.message, context.bot, i18n.t(lang, "conv_gone"))
        return
    if conv.get("archived_at"):
        await edit_in_place(query.message, context.bot,
                            i18n.t(lang, "archive_already", conv_number=conv["conv_number"]))
        return
    await asyncio.to_thread(set_conversation_archived, conv["id"], user_id, True)
    await edit_in_place(query.message, context.bot,
                        i18n.t(lang, "archive_done", conv_number=conv["conv_number"]))


async def _send_bookmark(bot, chat_id: int, conversation_id: int, text: str, anchor: int):
    try:
        sent = await bot.send_message(chat_id=chat_id, text=text, reply_to_message_id=anchor)
    except BadRequest:
        sent = await bot.send_message(chat_id=chat_id, text=text)
    live_message.bump(sent.chat_id, sent.message_id)
    await asyncio.to_thread(record_anon_relays, chat_id, [sent.message_id], conversation_id)
    return sent


async def conversation_jump_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    lang = await i18n.get_lang(update.effective_user.id, context)
    args = _callback_ints(query, 1)
    conv = await _owned_conversation(update, context, args, lang)
    if conv is None:
        return
    await query.answer()
    chat_id = query.message.chat_id
    anchor = await asyncio.to_thread(first_relay_message, chat_id, conv["id"])
    if anchor is None:
        await context.bot.send_message(
            chat_id=chat_id,
            text=i18n.t(lang, "jump_no_anchor", conv_number=conv["conv_number"],
                        days=RELAY_RETENTION_DAYS),
        )
        return
    await _send_bookmark(context.bot, chat_id, conv["id"],
                         i18n.t(lang, "jump_bookmark", conv_number=conv["conv_number"]),
                         anchor)


async def which_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    user_id = update.effective_user.id
    lang = await i18n.get_lang(user_id, context)
    target = message.reply_to_message
    if target is None:
        await message.reply_text(i18n.t(lang, "which_needs_reply"))
        return

    conversation_id = await asyncio.to_thread(
        get_conversation_for_relay, message.chat_id, target.message_id)
    conv = (await asyncio.to_thread(get_conversation, conversation_id)
            if conversation_id else None)
    if not conv or user_id not in (conv["owner_user_id"], conv["follower_user_id"]):
        await message.reply_text(i18n.t(lang, "which_unknown"))
        return

    if conv["owner_user_id"] == user_id:
        await message.reply_text(
            i18n.t(lang, "which_owner", conv_number=conv["conv_number"]), do_quote=True)
        return

    anchor = await asyncio.to_thread(first_relay_message, message.chat_id, conv["id"])
    if anchor is None:
        await message.reply_text(
            i18n.t(lang, "which_follower_no_anchor",
                   started=_ago(lang, conv.get("started_at"))), do_quote=True)
        return
    await _send_bookmark(context.bot, message.chat_id, conv["id"],
                         i18n.t(lang, "which_follower"), anchor)


def _callback_ints(query, count: int) -> "list[int] | None":
    _, _, tail = (query.data or "").partition(":")
    parts = tail.split(":")
    if len(parts) != count:
        return None
    try:
        return [int(part) for part in parts]
    except ValueError:
        return None


def _conversation_arg(query) -> int | None:
    args = _callback_ints(query, 1)
    return args[0] if args else None


async def reply_button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = update.effective_user.id
    lang = await i18n.get_lang(user_id, context)
    conversation_id = _conversation_arg(query)
    conv = (await asyncio.to_thread(get_conversation, conversation_id)
            if conversation_id is not None else None)
    if not conv or user_id not in (conv["owner_user_id"], conv["follower_user_id"]):
        await query.answer(i18n.t(lang, "not_your_conversation"), show_alert=True)
        return

    await query.answer(i18n.t(lang, "reply_button_hint"), show_alert=True)


async def block_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    lang = await i18n.get_lang(update.effective_user.id, context)
    conversation_id = _conversation_arg(query)
    conv = (await asyncio.to_thread(get_conversation, conversation_id)
            if conversation_id is not None else None)
    if not conv or conv["owner_user_id"] != update.effective_user.id:
        await query.answer(i18n.t(lang, "not_your_conversation"), show_alert=True)
        return

    await asyncio.to_thread(set_anon_blocked, conv["owner_user_id"], conv["follower_user_id"], True)
    await query.answer(i18n.t(lang, "blocked_answer"), show_alert=False)
    kb = InlineKeyboardMarkup(
        [[InlineKeyboardButton(
            i18n.t(lang, "unblock_button_labelled", conv_number=conv["conv_number"]),
            callback_data=f"aq_unblockc:{conversation_id}",
        )]]
    )
    try:
        await query.edit_message_reply_markup(reply_markup=kb)
    except BadRequest:
        pass


async def unblock_from_conversation_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    lang = await i18n.get_lang(update.effective_user.id, context)
    conversation_id = _conversation_arg(query)
    conv = (await asyncio.to_thread(get_conversation, conversation_id)
            if conversation_id is not None else None)
    if not conv or conv["owner_user_id"] != update.effective_user.id:
        await query.answer(i18n.t(lang, "not_your_conversation"), show_alert=True)
        return

    await asyncio.to_thread(set_anon_blocked, conv["owner_user_id"], conv["follower_user_id"], False)
    await query.answer(i18n.t(lang, "unblocked_answer"))
    try:
        await query.edit_message_reply_markup(reply_markup=_incoming_keyboard(conversation_id, lang))
    except BadRequest:
        pass


async def unblock_from_list_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    lang = await i18n.get_lang(update.effective_user.id, context)
    conversation_id = _conversation_arg(query)
    owner_id = update.effective_user.id
    conv = (await asyncio.to_thread(get_conversation, conversation_id)
            if conversation_id is not None else None)
    if not conv or conv["owner_user_id"] != owner_id:
        await query.answer(i18n.t(lang, "not_your_conversation"), show_alert=True)
        return
    await asyncio.to_thread(set_anon_blocked, owner_id, conv["follower_user_id"], False)
    await query.answer(i18n.t(lang, "unblocked_answer"))
    text, kb = await _blocked_list_text_and_kb(owner_id, lang)
    await edit_in_place(query.message, context.bot, text, reply_markup=kb)


async def _refuse_unaddressed(update, context, lang: str, routing: dict) -> None:
    active, owned = routing["active"], routing["owned"]
    anchor = routing.get("active_anchor")

    if active and not owned:
        opening = active.get("follower_first_msg_at") is None
        text = i18n.t(lang, "must_reply_opening" if opening else "must_reply")
    else:
        text = i18n.t(lang, "must_reply_ambiguous")
        anchor = None

    if anchor is not None:
        try:
            await context.bot.send_message(
                chat_id=update.effective_chat.id, text=text,
                reply_to_message_id=anchor,
            )
            return
        except BadRequest:
            pass
    await update.message.reply_text(text, do_quote=True)


_ALBUM_WINDOW = 20
_albums: "dict[str, tuple[int, float]]" = {}


def _album_group(message) -> str | None:
    return getattr(message, "media_group_id", None) or None


def _album_prune(now: float) -> None:
    for key, (_, seen) in list(_albums.items()):
        if now - seen > _ALBUM_WINDOW:
            del _albums[key]


def _album_route(message) -> int | None:
    group = _album_group(message)
    if not group:
        return None
    _album_prune(datetime.now(timezone.utc).timestamp())
    known = _albums.get(group)
    return known[0] if known else None


def _album_remember(message, conversation_id: int) -> bool:
    group = _album_group(message)
    if not group:
        return False
    now = datetime.now(timezone.utc).timestamp()
    _album_prune(now)
    seen = group in _albums
    _albums[group] = (conversation_id, _albums[group][1] if seen else now)
    return seen


async def _refuse_if_updating(message, lang: str, user_id: int, chat_id: int) -> bool:
    refusal = await refuse_new_work(lang, user_id, chat_id)
    if refusal:
        await message.reply_text(refusal)
        return True
    return False


def _anchor_for(peer_anchor: "int | None", conv: dict, newest_key: str) -> "int | None":
    if peer_anchor is not None:
        return peer_anchor
    return conv.get(newest_key)


async def _deliver_follower_message(update: Update, context: ContextTypes.DEFAULT_TYPE, active: dict,
                                    message=None, peer_anchor: int | None = None):
    message = message or update.message
    follower = update.effective_user
    owner_id = active["owner_user_id"]
    follower_lang = await i18n.get_lang(follower.id, context)
    quiet = _album_remember(message, active["id"])

    if not quiet:
        wait = message_allowed(follower.id)
        if wait:
            await message.reply_text(i18n.t(follower_lang, "too_fast", seconds=wait))
            return
        if await _refuse_if_updating(message, follower_lang, follower.id, message.chat_id):
            return

    blocked, inbox_exists, owner_lang = await asyncio.to_thread(
        get_delivery_context, owner_id, follower.id
    )
    if blocked:
        await message.reply_text(i18n.t(follower_lang, "delivery_blocked"))
        return
    if not inbox_exists:
        await message.reply_text(i18n.t(follower_lang, "inbox_gone"))
        return

    owner_lang = owner_lang or "en"
    header = None if quiet else i18n.t(owner_lang, "incoming_header", conv_number=active["conv_number"])
    kb = None if quiet else _incoming_keyboard(active["id"], owner_lang)

    try:
        sent = await relay_content(
            context.bot, message, owner_id,
            reply_to_message_id=_anchor_for(peer_anchor, active, "last_owner_msg_id"),
            reply_markup=kb, header=header,
        )
    except Forbidden:
        await message.reply_text(i18n.t(follower_lang, "delivery_forbidden"))
        return
    except BadRequest as exc:
        logger.exception("Failed relaying follower message")
        await message.reply_text(i18n.t(follower_lang, "delivery_failed", error=exc))
        return

    note_stat("anon_relay", "to_inbox")
    await asyncio.to_thread(
        record_relay_and_touch, active["id"],
        owner_id, [m.message_id for m in sent],
        message.chat_id, [message.message_id],
        sent[-1].message_id, message.message_id,
    )

    opening_line = None
    if active.get("follower_first_msg_at") is None:
        opening_line = await asyncio.to_thread(mark_follower_opened, active["id"], message.chat_id)

    if not quiet:
        await message.reply_text(i18n.t(follower_lang, "sent_confirmation"))
    if opening_line:
        try:
            await context.bot.edit_message_reply_markup(message.chat_id, opening_line, reply_markup=None)
        except BadRequest:
            pass


async def _deliver_owner_reply(update: Update, context: ContextTypes.DEFAULT_TYPE, conversation_id: int,
                               peer_anchor: int | None = None):
    message = update.message
    owner = update.effective_user
    owner_lang = await i18n.get_lang(owner.id, context)
    conv = await asyncio.to_thread(get_conversation, conversation_id)
    if not conv or conv["owner_user_id"] != owner.id:
        await message.reply_text(i18n.t(owner_lang, "reply_no_match"))
        return

    quiet = _album_remember(message, conversation_id)
    if not quiet:
        wait = message_allowed(owner.id)
        if wait:
            await message.reply_text(i18n.t(owner_lang, "too_fast", seconds=wait))
            return
        if await _refuse_if_updating(message, owner_lang, owner.id, message.chat_id):
            return

    follower_id = conv["follower_user_id"]
    follower_lang = await asyncio.to_thread(get_user_language, follower_id) or "en"
    try:
        sent = await relay_content(
            context.bot, message, follower_id,
            reply_to_message_id=_anchor_for(peer_anchor, conv, "last_follower_msg_id"),
            header=None if quiet else i18n.t(follower_lang, "incoming_header_follower"),
            reply_markup=None if quiet else _follower_keyboard(conversation_id, follower_lang),
        )
    except Forbidden:
        await message.reply_text(i18n.t(owner_lang, "reply_forbidden"))
        return
    except BadRequest as exc:
        logger.exception("Failed relaying the inbox holder's reply")
        await message.reply_text(i18n.t(owner_lang, "reply_failed", error=exc))
        return

    note_stat("anon_relay", "reply")
    await asyncio.to_thread(
        record_relay_and_touch, conversation_id,
        owner.id, [message.message_id],
        follower_id, [m.message_id for m in sent],
        message.message_id, sent[-1].message_id,
    )

    if not quiet:
        await message.reply_text(i18n.t(owner_lang, "delivered_confirmation"))
    if conv.get("last_owner_msg_id") is None:
        nudge = await maybe_donation_nudge(
            owner.id, owner_lang, context, message.chat_id)
        if nudge:
            await message.reply_text(nudge)


async def export_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = await i18n.get_lang(update.effective_user.id, context)
    await update.message.reply_text(i18n.t(lang, "export_removed"))


async def _cancel_items(update: Update, context: ContextTypes.DEFAULT_TYPE, lang: str) -> list[CancelItem]:
    items = cancel_items(context, lang)
    if await asyncio.to_thread(get_active_conversation_for_follower, update.effective_user.id):
        items.append(CancelItem("anon_session",
                                i18n.t(lang, "cancel_item_anon_session"),
                                i18n.t(lang, "cancel_button_anon_session")))
    return items


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = await i18n.get_lang(update.effective_user.id, context)
    items = await _cancel_items(update, context, lang)
    if await ask_cancel_choice(update, context, items, lang):
        return
    await finish_cancel(update, context, lang, [], stored_only=True)


async def cancel_choice_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    lang = await i18n.get_lang(update.effective_user.id, context)
    key = cancel_choice_key(update)
    await query.answer()
    if key == CANCEL_PICK_NONE:
        await keep_going(update, context, lang)
        return

    items = {item.key: item for item in await _cancel_items(update, context, lang)}
    keys = list(items) if key == CANCEL_PICK_ALL else [key]
    stopped = []
    for chosen in keys:
        item = items.get(chosen)
        if item is None:
            continue
        if chosen == "anon_session":
            await asyncio.to_thread(clear_follower_state, update.effective_user.id)
            stopped.append(item.label)
        elif cancel_shared_item(context, lang, chosen):
            stopped.append(item.label)
    await finish_cancel_choice(update, context, lang, stopped)


async def edited_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    edited = update.edited_message
    try:
        conversation_id = await asyncio.to_thread(
            get_conversation_for_relay, update.effective_chat.id, edited.message_id
        )
        if conversation_id is not None:
            lang = await i18n.get_lang(update.effective_user.id, context)
            await edited.reply_text(i18n.t(lang, "edit_not_relayed"))
    except Exception:
        logger.exception("Could not answer an edited message")
    finally:
        raise ApplicationHandlerStop


async def relay_reaction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    change = update.message_reaction
    if change is None or change.user is None or change.chat.type != "private":
        return
    reactor = change.user.id
    route = await asyncio.to_thread(get_reaction_route, change.chat.id, change.message_id)
    if route is None or reactor not in (route["owner_user_id"], route["follower_user_id"]):
        return
    await _knows_reactions(context, reactor)
    if route["blocked"]:
        return
    other = (route["follower_user_id"] if reactor == route["owner_user_id"]
             else route["owner_user_id"])
    chosen, note = pick_reaction(change.old_reaction, change.new_reaction)
    try:
        await context.bot.set_message_reaction(
            chat_id=other, message_id=route["peer_message_id"], reaction=chosen)
    except Forbidden:
        return
    except BadRequest as exc:
        if "REACTION_INVALID" in str(exc).upper():
            note = "reaction_not_passed"
        else:
            logger.debug("Could not pass a reaction on: %s", exc)
            return
    if note and reaction_note_allowed(reactor):
        lang = await i18n.get_lang(reactor, context)
        await _show_briefly(context, change.chat.id, i18n.t(lang, note))


_taking_down: set = set()


async def _show_briefly(context, chat_id: int, text: str) -> None:
    try:
        sent = await context.bot.send_message(chat_id=chat_id, text=text,
                                              disable_notification=True)
    except Exception:
        logger.debug("Could not show a reaction note", exc_info=True)
        return

    async def take_down():
        await asyncio.sleep(REACTION_NOTE_SECONDS)
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=sent.message_id)
        except Exception:
            logger.debug("Could not take a reaction note down", exc_info=True)

    task = asyncio.get_running_loop().create_task(take_down())
    _taking_down.add(task)
    task.add_done_callback(_taking_down.discard)


KNOWS_REACTIONS_KEY = "knows_reactions"


async def _knows_reactions(context, user_id: int) -> bool:
    if context.user_data.get(KNOWS_REACTIONS_KEY):
        return False
    try:
        owed = await asyncio.to_thread(claim_reactions_note, user_id)
    except Exception:
        logger.debug("Could not read whether a user knows about reactions", exc_info=True)
        return False
    context.user_data[KNOWS_REACTIONS_KEY] = True
    return owed


async def reactions_update_note(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user, chat = update.effective_user, update.effective_chat
    if user is None or chat is None or chat.type != "private":
        return
    if update.message is None and update.callback_query is None:
        return
    if context.user_data.get(KNOWS_REACTIONS_KEY) or FORCE_REPLY_KEY in context.user_data:
        return
    if not await _knows_reactions(context, user.id):
        return
    lang = await i18n.get_lang(user.id, context)
    try:
        await context.bot.send_message(chat_id=chat.id, text=i18n.t(lang, "reactions_update_note"))
    except Exception:
        logger.debug("Could not send the reactions note", exc_info=True)
        return
    context.user_data[NOTE_UPDATE_KEY] = update.update_id


PROMO_AD_KEY = "_promo_ad"
PROMO_TEASER_FROM = datetime(2026, 11, 18, 2, tzinfo=timezone.utc)
PROMO_TEASER_KEY = "gift-220-users:teaser"
NOTE_UPDATE_KEY = "_note_update"


_NO_AD_COMMANDS = {"/deletemydata", "/privacy", "/terms"}


def _no_ad_after(message) -> bool:
    words = (message.text or "").split(None, 1)
    return bool(words) and words[0].split("@", 1)[0].lower() in _NO_AD_COMMANDS


async def promo_ad_note(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message, user, chat = update.message, update.effective_user, update.effective_chat
    if message is None or user is None or chat is None or chat.type != "private":
        return
    if getattr(user, "is_bot", False):
        return
    words = (message.text or "").split(None, 1)
    if len(words) == 2 and words[0].split("@", 1)[0].lower() == "/start":
        return
    if _no_ad_after(message):
        return
    if FORCE_REPLY_KEY in context.user_data:
        return
    if context.user_data.get(NOTE_UPDATE_KEY) == update.update_id:
        return
    if context.user_data.get(PROMO_AD_KEY) == family_link.PROMO_KEY:
        return
    try:
        window = await asyncio.to_thread(family_link.promo_window)
        now = datetime.now(timezone.utc)
        if window is None:
            if now < PROMO_TEASER_FROM or context.user_data.get(PROMO_AD_KEY) == PROMO_TEASER_KEY:
                return
            lang = await asyncio.to_thread(get_user_language, user.id)
            if not lang:
                return
            shown_now = await asyncio.to_thread(claim_promo_seen, user.id, PROMO_TEASER_KEY)
            context.user_data[PROMO_AD_KEY] = PROMO_TEASER_KEY
            if shown_now:
                await context.bot.send_message(chat_id=chat.id, text=i18n.t(lang, "gift_teaser"))
            return
        if not window[0] <= now < window[1]:
            return
        row = (sibling_bots_keyboard_row(BOT_NAME, only="downloaderbot")
               + sibling_bots_keyboard_row(BOT_NAME, only="convertbot"))
        if not row:
            return
        lang = await asyncio.to_thread(get_user_language, user.id)
        if not lang:
            return
        if await asyncio.to_thread(family_link.promo_given_to, user.id):
            context.user_data[PROMO_AD_KEY] = family_link.PROMO_KEY
            return
        shown_now = await asyncio.to_thread(claim_promo_seen, user.id, family_link.PROMO_KEY)
        context.user_data[PROMO_AD_KEY] = family_link.PROMO_KEY
        if not shown_now:
            return
        await context.bot.send_message(
            chat_id=chat.id, text=family_link.promo_ad(lang, window[1]),
            parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup([row]), **NO_PREVIEW,
        )
    except Exception as exc:
        logger.debug("The gift ad was not shown (%s)", type(exc).__name__)


async def _route_into(update: Update, context: ContextTypes.DEFAULT_TYPE,
                      conversation_id: int, conv: dict | None = None,
                      peer_anchor: int | None = None) -> None:
    user = update.effective_user
    if conv is None:
        conv = await asyncio.to_thread(get_conversation, conversation_id)
    if not conv or user.id not in (conv["owner_user_id"], conv["follower_user_id"]):
        lang = await i18n.get_lang(user.id, context)
        await update.message.reply_text(i18n.t(lang, "reply_stale"))
        return
    if conv["owner_user_id"] == user.id:
        return await _deliver_owner_reply(update, context, conversation_id,
                                          peer_anchor=peer_anchor)
    return await _deliver_follower_message(update, context, conv,
                                           peer_anchor=peer_anchor)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    user = update.effective_user
    chat_id = message.chat_id

    album = _album_route(message)
    if album is not None:
        return await _route_into(update, context, album)

    if message.reply_to_message:
        anchor_id = message.reply_to_message.message_id
        relay = await asyncio.to_thread(get_relay_row, chat_id, anchor_id)
        conversation_id = relay["conversation_id"] if relay else None
        if conversation_id:
            conv = await asyncio.to_thread(get_conversation, conversation_id)
            if conv and user.id in (conv["owner_user_id"], conv["follower_user_id"]):
                return await _route_into(update, context, conversation_id, conv,
                                         peer_anchor=relay["peer_message_id"])
        lang = await i18n.get_lang(user.id, context)
        await message.reply_text(i18n.t(lang, "reply_stale"))
        return

    routing = await asyncio.to_thread(get_routing_context, user.id, chat_id)
    active, owned = routing["active"], routing["owned"]

    if (active and active.get("follower_first_msg_at") is None
            and routing["newest_is_active"]):
        return await _deliver_follower_message(update, context, active)

    if active or owned:
        lang = await i18n.get_lang(user.id, context)
        return await _refuse_unaddressed(update, context, lang, routing)

    lang = await i18n.get_lang(user.id, context)
    await message.reply_text(i18n.t(lang, "generic_nudge"))


async def unknown_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = await i18n.get_lang(update.effective_user.id, context)
    await update.message.reply_text(i18n.t(lang, "unknown_command"))


async def _post_init(application):
    await tune_runtime(application)
    await lifecycle.on_start(BOT_NAME)
    await lifecycle.optional("command menu", publish_commands(application, BOT_COMMANDS, ADMIN_COMMANDS, ADMIN_IDS))
    await lifecycle.optional("profile text", publish_profile(application))


async def _post_stop(application):
    await lifecycle.on_stop(application)
    await flush_on_shutdown(application)

NEEDED_STAGES = ("anon",)


async def _maintenance_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user, chat = update.effective_user, update.effective_chat
    code = (getattr(user, "language_code", None) or "").split("-")[0].lower()
    text = i18n.t(code if code in i18n.SUPPORTED_LANGUAGES else "en", "maintenance_note")
    try:
        if update.callback_query is not None:
            await update.callback_query.answer(text[:200], show_alert=True)
        elif update.message is not None and chat is not None and chat.type == "private":
            await update.message.reply_text(text)
    except Exception:
        logger.debug("Could not show the maintenance note", exc_info=True)


async def _maintenance_post_init(application):
    await lifecycle.on_start(BOT_NAME)


def _maintenance_main(problem: str) -> None:
    logger.critical("AnonBot opens in maintenance: %s. It answers with a note and touches no "
                    "personal data until the data key checks out.", problem)
    app = (ApplicationBuilder().token(BOT_TOKEN)
           .post_init(_maintenance_post_init).post_stop(_post_stop).build())
    lifecycle.install(app, BOT_NAME, ADMIN_IDS)
    app.add_error_handler(error_handler)
    attach_flood_gate(app, ADMIN_IDS)
    app.add_handler(TypeHandler(Update, _maintenance_reply))
    family_link.attach(app, BOT_NAME, "AnonBot", START_TIME, needs=NEEDED_STAGES)
    app.run_polling(**lifecycle.polling_kwargs(
        timeout=POLL_TIMEOUT, allowed_updates=[Update.MESSAGE, Update.CALLBACK_QUERY]))


def main():
    if not BOT_TOKEN or not BOT_USERNAME:
        raise SystemExit("Set ABOT_TOKEN and ABOT_USERNAME environment variables first.")

    init_db()
    problem = family_link.key_problem(family_link.check_data_key(), NEEDED_STAGES)
    if problem:
        return _maintenance_main(problem)
    builder = (
        ApplicationBuilder().token(BOT_TOKEN)
        .post_init(_post_init).post_stop(_post_stop)
    )
    builder = shared_features.per_user_updates(builder, "ABOT")
    state = lifecycle.persistence()
    if state is not None:
        builder = builder.persistence(state)
    app = builder.build()
    lifecycle.install(app, BOT_NAME, ADMIN_IDS)
    app.add_error_handler(error_handler)
    attach_problem_reports(app)
    app.add_handler(TypeHandler(Update, track_activity), group=-4)
    attach_flood_gate(app, ADMIN_IDS)
    app.add_handler(MessageHandler(filters.UpdateType.EDITED_MESSAGE, edited_message), group=-2)
    app.add_handler(MessageHandler((filters.TEXT | filters.REPLY) & ~filters.COMMAND,
                                   awaited_text_received), group=-1)

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("fullhelp", fullhelp_command))
    app.add_handler(CommandHandler("feedback", feedback_command))
    app.add_handler(CommandHandler("cancel", cancel_command))
    app.add_handler(CommandHandler("link", link_command))
    app.add_handler(CommandHandler("newlink", newlink_command))
    app.add_handler(CommandHandler("pause", pause_command))
    app.add_handler(CommandHandler("resume", resume_command))
    app.add_handler(CommandHandler("conversations", conversations_command))
    app.add_handler(CommandHandler("which", which_command))
    app.add_handler(CommandHandler("archive", archive_command))
    app.add_handler(CommandHandler("blocked", blocked_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("export", export_command))
    app.add_handler(CommandHandler("dbdump", dbdump_command))
    app.add_handler(CommandHandler("messageas", messageas_command))
    app.add_handler(CommandHandler("status", status_command))

    app.add_handler(CommandHandler("language", language_command))
    app.add_handler(CommandHandler("en", set_language_en))
    app.add_handler(CommandHandler("uz", set_language_uz))
    app.add_handler(CommandHandler("rus", set_language_rus))
    app.add_handler(CommandHandler("privacy", privacy_command))
    app.add_handler(CommandHandler("terms", terms_command))
    app.add_handler(CommandHandler("paysupport", paysupport_command))
    app.add_handler(CallbackQueryHandler(problem_report_callback, pattern=r"^rpt"))
    app.add_handler(CommandHandler("deletemydata", delete_my_data_command))
    app.add_handler(CallbackQueryHandler(delete_my_data_chosen, pattern="^" + ERASE_PREFIX))

    app.add_handler(CallbackQueryHandler(language_chosen, pattern=r"^setlang:"))
    app.add_handler(CallbackQueryHandler(help_toggle(build_help_text, None, HELP_PAGES), pattern=HELP_TOGGLE_PATTERN))
    app.add_handler(CallbackQueryHandler(help_page_button(HELP_PAGES), pattern=HELP_PAGE_PATTERN))
    app.add_handler(CallbackQueryHandler(cancel_choice_callback, pattern=r"^cancelpick:"))
    app.add_handler(CallbackQueryHandler(anonlink_toggle_callback, pattern=r"^anonlink:toggle$"))
    app.add_handler(CallbackQueryHandler(anonlink_newlink_callback, pattern=r"^anonlink:newlink$"))
    app.add_handler(CallbackQueryHandler(conversations_page_callback, pattern=r"^aq_convs:"))
    app.add_handler(CallbackQueryHandler(conversation_card_callback, pattern=r"^aq_conv:"))
    app.add_handler(CallbackQueryHandler(conversation_archive_callback, pattern=r"^aq_arch:"))
    app.add_handler(CallbackQueryHandler(archive_command_callback, pattern=r"^aq_archcmd:"))
    app.add_handler(CallbackQueryHandler(conversation_jump_callback, pattern=r"^aq_jump:"))
    app.add_handler(CallbackQueryHandler(reply_button_callback, pattern=r"^aq_reply:"))
    app.add_handler(CallbackQueryHandler(follow_cancel_callback, pattern=r"^aq_fcancel:"))
    app.add_handler(CallbackQueryHandler(block_callback, pattern=r"^aq_block:"))
    app.add_handler(CallbackQueryHandler(unblock_from_conversation_callback, pattern=r"^aq_unblockc:"))
    app.add_handler(CallbackQueryHandler(unblock_from_list_callback, pattern=r"^aq_unblockg:"))

    app.add_handler(CommandHandler("balance", balance_command))
    app.add_handler(CommandHandler("donate", donate_command))
    app.add_handler(CallbackQueryHandler(donate_amount_chosen, pattern="^donate:"))
    app.add_handler(CallbackQueryHandler(donate_fiat_amount_chosen, pattern="^donatefiat:"))
    app.add_handler(CallbackQueryHandler(donate_custom_button_chosen, pattern="^donatecustom:"))
    app.add_handler(PreCheckoutQueryHandler(donation_precheckout_callback))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, donation_payment_callback))

    app.add_handler(MessageReactionHandler(
        relay_reaction, message_reaction_types=MessageReactionHandler.MESSAGE_REACTION_UPDATED))

    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.COMMAND, unknown_command))

    app.add_handler(TypeHandler(Update, reactions_update_note), group=1)
    app.add_handler(TypeHandler(Update, promo_ad_note), group=2)

    family_link.attach(app, BOT_NAME, "AnonBot", START_TIME)
    attach_maintenance(app)

    logger.info("Bot starting (polling)...")
    app.run_polling(**lifecycle.polling_kwargs(
        timeout=POLL_TIMEOUT,
        allowed_updates=[
            Update.MESSAGE, Update.EDITED_MESSAGE,
            Update.CALLBACK_QUERY, Update.PRE_CHECKOUT_QUERY,
            Update.MESSAGE_REACTION,
        ],
    ))


if __name__ == "__main__":
    main()
