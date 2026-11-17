import asyncio
import hashlib
import logging
import os
import re
import secrets
import time
import types
from datetime import datetime, timedelta, timezone
from io import BytesIO

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from telegram import (
    Update,
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputSticker,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyParameters,
)
from telegram.error import BadRequest, NetworkError, TelegramError
from telegram.ext import (
    ApplicationBuilder,
    ChatMemberHandler,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    PreCheckoutQueryHandler,
    ConversationHandler,
    ContextTypes,
    TypeHandler,
    filters,
)

import emoji_utils
import family_link
import group_mode
import i18n
import import_utils
import problems
import lifecycle
import live_message
import shared_features
import sticker_ops
import video_sticker
from live_message import LiveMessage, edit_in_place
from db import (
    init_db,
    add_pack,
    get_user_packs,
    get_pack_owner,
    get_pack_title,
    set_pack_title,
    set_pack_titles,
    get_pack_creator_info,
    get_coedit_view,
    reset_share_token,
    join_by_token,
    set_link_valid_days,
    set_max_editors,
    set_editors_delete,
    pack_name_by_id,
    pack_editors,
    remove_editor,
    shared_packs,
    add_invites,
    pack_invites,
    remove_invite,
    claim_invites,
    invites_usable,
    pack_access,
    MAX_EDITORS_CHOICES,
    VALID_DAYS_CHOICES,
    delete_pack_records,
    dump_database_csv_zip,
    count_active_users_since,
    get_user_language,
    set_user_language,
    is_pack_public,
    set_pack_public,
)
from video_sticker import ConversionError, TooBig
from emoji_utils import DEFAULT_EMOJI, looks_like_emoji_message, split_emoji
from import_utils import (
    parse_telegram_pack_source,
    fetch_importable_stickers,
    sticker_to_input_sticker,
    parse_whatsapp_zip,
    ImportError_ as PackImportError,
    MAX_IMPORT_PER_RUN,
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
    refuse_new_work,
    CANCEL_PICK_ALL,
    CANCEL_PICK_NONE,
    CancelItem,
    ask_cancel_choice,
    cancel_choice_key,
    cancel_items,
    cancel_shared_item,
    finish_cancel_choice,
    keep_going,
    reset_user_state,
    finish_cancel,
    flush_on_shutdown,
    language_keyboard,
    tune_runtime,
    sibling_bots_blurb,
    sibling_bots_keyboard_row,
    maybe_donation_nudge,
    balance_command,
    donate_command,
    donate_amount_chosen,
    donate_fiat_amount_chosen,
    donate_custom_button_chosen,
    awaited_text_received,
    feedback_command,
    HELP_PAGE_PATTERN,
    HELP_TOGGLE_PATTERN,
    NO_PREVIEW,
    full_help_command,
    help_keyboard,
    help_page_button,
    help_page_name,
    help_page_text,
    help_toggle,
    wants_full_help,
    donation_precheckout_callback,
    donation_payment_callback,
    setup_logging,
    error_handler,
    record_error,
    track_activity,
    build_status_text,
)

setup_logging(__file__)
logger = logging.getLogger(__name__)

START_TIME = datetime.now(timezone.utc)

BOT_TOKEN = os.environ.get("SBOT_TOKEN")
BOT_USERNAME = (os.environ.get("SBOT_USERNAME") or "").strip().lstrip("@") or None

ADMIN_IDS = {int(x) for x in os.environ.get("SBOT_ADMIN_ID", "").split(",") if x.strip()}

LOCAL_BOT_API_URL = os.environ.get("LOCAL_BOT_API_URL")
LOCAL_BOT_API_MODE = os.environ.get("LOCAL_BOT_API_MODE", "true").lower() == "true"

BOT_NAME = "stickerbot"

POLL_TIMEOUT = int(os.environ.get("POLL_TIMEOUT", "30"))

TITLE, EDITING, RENAME, INVITE = range(4)

PACK_TITLE_MAX = 64
TITLE_REFRESH_MAX = 50
TITLE_REFRESH_SECONDS = 4


def build_help_text(lang: str, full: bool = False) -> str:
    if not full:
        text = i18n.t(lang, "help_brief").rstrip("\n")
    else:
        text = i18n.t(lang, "help_text").rstrip("\n")
    blurb = sibling_bots_blurb(BOT_NAME, lang)
    return text + "\n\n" + blurb if blurb else text


def _help_page_newpack(lang: str) -> str:
    return i18n.t(lang, "helppage_newpack",
                  title_max=PACK_TITLE_MAX, emoji=emoji_utils.DEFAULT_EMOJI,
                  emoji_max=emoji_utils.MAX_EMOJI_PER_STICKER, side=video_sticker.STICKER_SIDE,
                  seconds=video_sticker.MAX_DURATION_S, fps=video_sticker.MAX_FPS,
                  kb=video_sticker.MAX_BYTES // 1024)


def _help_page_addsticker(lang: str) -> str:
    return i18n.t(lang, "helppage_addsticker", emoji=emoji_utils.DEFAULT_EMOJI)


def _help_page_import(lang: str) -> str:
    return i18n.t(lang, "helppage_import", max=import_utils.MAX_IMPORT_PER_RUN)


def _help_page_groups(lang: str) -> str:
    return i18n.t(lang, "helppage_groups")


HELP_PAGES = {"newpack": _help_page_newpack, "addsticker": _help_page_addsticker, "import": _help_page_import,
              "groups": _help_page_groups}
HELP_ALIASES = {"group": "groups"}
HELP_LABELS = {"groups": "help_btn_groups"}


def build_start_text(lang: str) -> str:
    return i18n.t(lang, "start_intro") + build_help_text(lang)


BOT_COMMANDS = [
    BotCommand("start", "What this bot does, and how to start"),
    BotCommand("language", "Choose the language / Tilni tanlash / Выбрать язык"),
    BotCommand("newpack", "Make a new sticker pack"),
    BotCommand("addsticker", "Add stickers to a pack"),
    BotCommand("mypacks", "Packs"),
    BotCommand("import", "Copy stickers in from another pack"),
    BotCommand("emoji", "Reply to a sticker: change its emoji"),
    BotCommand("whomade", "Who made a pack — send one of its stickers"),
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
    BotCommand("whois", "🔒 whois <user_id> — a user, and their packs"),
    BotCommand("messageas", "🔒 messageas <user_id> <text> — DM as this bot"),
    BotCommand("dbdump", "🔒 This bot's tables as a zip of CSVs"),
    BotCommand("crashtest", "🔒 Raise on purpose, to check the alert arrives"),
]


async def reply(update: Update, text: str, **kwargs) -> LiveMessage:
    if update.message:
        return await LiveMessage.reply_to(update.message, text, **kwargs)
    return await edit_in_place(update.callback_query.message, update.get_bot(), text, **kwargs)


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", text).strip("_").lower()
    slug = slug[:30].strip("_") or "pack"
    if not slug[0].isalpha():
        slug = ("p_" + slug)[:30].strip("_")
    return slug


def help_rows(lang: str) -> list:
    return [[
        InlineKeyboardButton(i18n.t(lang, "btn_new_pack"), callback_data="menu_newpack"),
        InlineKeyboardButton(i18n.t(lang, "btn_my_packs"), callback_data="menu_mypacks"),
    ]]


def start_menu_keyboard(lang: str) -> InlineKeyboardMarkup:
    return help_keyboard(lang, False, help_rows(lang) + [sibling_bots_keyboard_row(BOT_NAME)], HELP_PAGES,
                         HELP_LABELS)


def packs_keyboard(packs: list[tuple[str, str]], lang: str, shared=()) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(title, callback_data=f"packopen:{pack_ref(name)}")] for name, title in packs]
    rows += [[InlineKeyboardButton(f"👥 {title}", callback_data=f"pack:{pack_ref(name)}")] for name, title in shared]
    rows.append([InlineKeyboardButton(i18n.t(lang, "btn_new_pack"), callback_data="menu_newpack")])
    rows.append([InlineKeyboardButton(i18n.t(lang, "btn_back"), callback_data="menu_start")])
    return InlineKeyboardMarkup(rows)


def pack_detail_keyboard(pack_name: str, lang: str, public: bool = False) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(i18n.t(lang, "btn_open_pack"), url=f"https://t.me/addstickers/{pack_name}")],
            [InlineKeyboardButton(i18n.t(lang, "btn_add_stickers"), callback_data=f"pack:{pack_ref(pack_name)}")],
            [
                InlineKeyboardButton(i18n.t(lang, "btn_rename"), callback_data=f"packrename:{pack_ref(pack_name)}"),
                InlineKeyboardButton(i18n.t(lang, "btn_coedit"), callback_data=f"packcoedit:{pack_ref(pack_name)}"),
            ],
            [InlineKeyboardButton(i18n.t(lang, "btn_pack_public" if public else "btn_pack_private"),
                                  callback_data=f"packpub:{pack_ref(pack_name)}")],
            [InlineKeyboardButton(i18n.t(lang, "btn_delete_pack"), callback_data=f"packdel:{pack_ref(pack_name)}")],
            [InlineKeyboardButton(i18n.t(lang, "btn_back"), callback_data="menu_mypacks")],
        ]
    )


def _status_text(context: ContextTypes.DEFAULT_TYPE, lang: str, note: str = "") -> str:
    title = context.user_data.get("title") or i18n.t(lang, "status_default_title")
    created = context.user_data.get("pack_created")
    verb = i18n.t(lang, "status_verb_creating" if not created else "status_verb_editing")
    intro = context.user_data.get("status_intro", "")
    text = i18n.t(lang, "status_line_open", verb=verb, title=title)
    if intro:
        text = f"{text}\n{intro}"
    if note:
        text += f"\n{note}"
    return text


STATUS_KEY = "status"


def _has_status(context: ContextTypes.DEFAULT_TYPE) -> bool:
    return bool(context.user_data.get(STATUS_KEY))


async def _start_status(
    context: ContextTypes.DEFAULT_TYPE, chat_id: int, title: str, lang: str,
    intro: str = "", adopt: dict | None = None, **send_kwargs,
):
    context.user_data["title"] = title
    context.user_data["sticker_count"] = 0
    context.user_data["status_intro"] = intro
    text = _status_text(context, lang)
    for attempt in (1, 2):
        try:
            live = LiveMessage.restore(adopt)
            if live is None:
                live = await LiveMessage.send(context.bot, chat_id, text, **send_kwargs)
            else:
                await live.set(context.bot, text)
            context.user_data[STATUS_KEY] = live.save()
            return
        except NetworkError as exc:
            if attempt == 1 and "ConnectError" in str(exc):
                logger.info("No connection for the status message; trying once more")
                await asyncio.sleep(1.5)
                continue
            raise


async def _refresh_status(context: ContextTypes.DEFAULT_TYPE, lang: str, note: str = ""):
    live = LiveMessage.restore(context.user_data.get(STATUS_KEY))
    if live is None:
        chat_id = getattr(context, "_chat_id", None)
        if note and chat_id is not None and problems.find_code(note):
            await context.bot.send_message(chat_id, note)
        return
    await live.set(context.bot, _status_text(context, lang, note))
    context.user_data[STATUS_KEY] = live.save()


async def _end_status(context: ContextTypes.DEFAULT_TYPE, final_note: str):
    live = LiveMessage.restore(context.user_data.get(STATUS_KEY))
    if live is not None:
        await live.finish(context.bot, final_note)
    for key in (STATUS_KEY, "sticker_count", "status_intro", *BATCH_KEYS):
        context.user_data.pop(key, None)


async def _owns_pack_or_deny(pack_name: str, user_id: int) -> bool:
    if not pack_name:
        return False
    return await asyncio.to_thread(get_pack_owner, pack_name) == user_id


async def whomade_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = await i18n.get_lang(update.effective_user.id, context)
    if not context.args:
        await update.message.reply_text(i18n.t(lang, "whomade_usage"))
        return

    source = " ".join(context.args)
    pack_name = parse_telegram_pack_source(source) or source
    info = await asyncio.to_thread(get_pack_creator_info, pack_name)
    if not info:
        await update.message.reply_text(i18n.t(lang, "whomade_not_found"))
        return

    if info["owner_username"]:
        creator = f"@{info['owner_username']}"
    elif info["owner_name"]:
        creator = info["owner_name"]
    else:
        creator = i18n.t(lang, "whomade_someone")

    created_date = (info["created_at"] or "")[:10]
    await update.message.reply_text(
        i18n.t(lang, "whomade_result", title=info["title"], creator=creator, date=created_date)
    )


def _is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


async def _deny(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await unknown_command(update, context)


async def whois_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update.effective_user.id):
        return await _deny(update, context)

    if not context.args or not context.args[0].lstrip("-").isdigit():
        await update.message.reply_text("Usage: /whois <user_id>")
        return

    user_id = int(context.args[0])
    lines = [f"🆔 {user_id}"]
    try:
        chat = await context.bot.get_chat(user_id)
        name = " ".join(p for p in (chat.first_name, chat.last_name) if p)
        if name:
            lines.append(f"Name: {name}")
        if chat.username:
            lines.append(f"Username: @{chat.username}")
        if chat.bio:
            lines.append(f"Bio: {chat.bio}")
    except Exception as exc:
        lines.append(f"⚠️ Couldn't fetch their Telegram profile: {exc}")
        lines.append("(They may have never messaged this bot, or blocked it.)")

    packs = await asyncio.to_thread(get_user_packs, user_id)
    if packs:
        lines.append(f"\n📦 Packs ({len(packs)}):")
        lines.extend(f"  • {title}" for _, title in packs)
    else:
        lines.append("\n📦 No packs on record for this id.")

    await update.message.reply_text("\n".join(lines))


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


async def dbdump_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update.effective_user.id):
        return await _deny(update, context)
    status = await update.message.reply_text("Exporting the database...")
    try:
        data = await asyncio.to_thread(dump_database_csv_zip)
    except Exception as exc:
        await edit_in_place(status, context.bot, f"⚠️ Export failed: {exc}")
        return
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M")
    await update.message.reply_document(
        document=BytesIO(data), filename=f"stickerbot_db_{stamp}.zip",
    )
    await status.delete()


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update.effective_user.id):
        return await _deny(update, context)
    now = datetime.now(timezone.utc)
    users_hour = await asyncio.to_thread(count_active_users_since, now - timedelta(hours=1))
    users_since_start = await asyncio.to_thread(count_active_users_since, START_TIME)
    await update.message.reply_text(build_status_text(START_TIME, users_hour, users_since_start))


async def crashtest_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update.effective_user.id):
        return await _deny(update, context)
    raise RuntimeError("Manual /crashtest trigger -- error tracking is working as intended.")


async def _claim_invites(update: Update, context: ContextTypes.DEFAULT_TYPE, lang: str) -> None:
    user = update.effective_user
    if user is None or not user.username:
        return
    try:
        joined = await asyncio.to_thread(claim_invites, user.id, user.username)
    except Exception:
        logger.warning("Could not check this person's invitations", exc_info=True)
        return
    if joined:
        titles = ", ".join(f"“{title}”" for _, title in joined)
        await update.effective_message.reply_text(i18n.t(lang, "invites_claimed", titles=titles))


def pack_ref(pack_name: str) -> str:
    return hashlib.sha256(pack_name.encode()).hexdigest()[:10]


async def _pack_of(user_id: int, ref: str) -> "str | None":
    own = await asyncio.to_thread(get_user_packs, user_id)
    shared = await asyncio.to_thread(shared_packs, user_id)
    for name, _ in list(own) + list(shared):
        if ref == name or ref == pack_ref(name):
            return name
    return None


PACK_BUTTON = "📦 "
PACK_BUTTONS_MAX = int(os.environ.get("SBOT_PACK_BUTTONS", "12"))
PACK_LABEL_MAX = 40
KB_TITLES = "kb_titles"
HELD_KEY = "held"
HELD_OFFER_KEY = "held_offer"
KEEP_ACROSS_SESSIONS = ("lang", "_last_seen", HELD_KEY, HELD_OFFER_KEY, KB_TITLES)


def _fresh_session(context, keep: "dict | None" = None) -> None:
    kept = {key: context.user_data[key] for key in KEEP_ACROSS_SESSIONS if key in context.user_data}
    context.user_data.clear()
    context.user_data.update(kept)
    if keep:
        context.user_data.update(keep)


def _pack_labels(packs) -> "list[tuple[str, str]]":
    seen: dict = {}
    numbered = {}
    for name, title in reversed(list(packs)):
        base = (title or name)[:PACK_LABEL_MAX]
        seen[base] = seen.get(base, 0) + 1
        numbered[name] = base if seen[base] == 1 else f"{base} · {seen[base]}"
    return [(numbered[name], name) for name, _ in packs]


def pack_reply_keyboard(packs, lang: str) -> ReplyKeyboardMarkup:
    rows = [[KeyboardButton(i18n.t(lang, "btn_kb_edit", title=label)),
             KeyboardButton(i18n.t(lang, "btn_kb_view", title=label))]
            for label, _ in _pack_labels(packs)[:PACK_BUTTONS_MAX]]
    rows.append([KeyboardButton(i18n.t(lang, "btn_new_pack"))])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True, is_persistent=True)


_VARIATION_SELECTORS = dict.fromkeys(map(ord, "\ufe0f\ufe0e"), None)


def _plain(text: str) -> str:
    return (text or "").translate(_VARIATION_SELECTORS).strip()


def _pack_button_action(text: str, packs, remembered: "dict | None" = None) -> "tuple[str, str] | None":
    text = _plain(text)
    owned = dict(packs)
    labels = list(_pack_labels(packs))
    labels += [(label, name) for name, label in (remembered or {}).items() if name in owned]
    for label, name in labels:
        for lang in i18n.SUPPORTED_LANGUAGES:
            if text == _plain(i18n.t(lang, "btn_kb_edit", title=label)):
                return "edit", name
            if text == _plain(i18n.t(lang, "btn_kb_view", title=label)):
                return "view", name
    return None


_BUTTON_PREFIXES: "list[str]" = []


class _LooksLikePackButton(filters.MessageFilter):
    def filter(self, message) -> bool:
        if not _BUTTON_PREFIXES:
            for lang in i18n.SUPPORTED_LANGUAGES:
                for key in ("btn_kb_edit", "btn_kb_view"):
                    head = i18n.t(lang, key, title="\0").split("\0")[0]
                    _BUTTON_PREFIXES.append(head.translate(_VARIATION_SELECTORS))
        text = (message.text or "").translate(_VARIATION_SELECTORS)
        return any(text.startswith(head) and len(text) > len(head) for head in _BUTTON_PREFIXES)


PACK_BUTTON_TEXT = _LooksLikePackButton()


async def _current_titles(context, packs) -> "list[tuple[str, str]]":
    packs = list(packs)
    try:
        changed = await _refresh_pack_titles(context.bot, packs[:TITLE_REFRESH_MAX])
    except Exception:
        logger.debug("Couldn't read the packs' titles from Telegram", exc_info=True)
        return packs
    if not changed:
        return packs
    target = context.user_data.get("target_pack")
    if target in changed:
        context.user_data["title"] = changed[target]
    return [(name, changed.get(name, title)) for name, title in packs]


async def _pack_keyboard_for(context, user_id: int, lang: str, sync: bool = False) -> ReplyKeyboardMarkup:
    packs = await asyncio.to_thread(get_user_packs, user_id)
    if sync:
        packs = await _current_titles(context, packs[:PACK_BUTTONS_MAX]) + list(packs[PACK_BUTTONS_MAX:])
    context.user_data[KB_TITLES] = {name: label for label, name in _pack_labels(packs)[:PACK_BUTTONS_MAX]}
    return pack_reply_keyboard(packs, lang)


async def _with_buttons(context, chat, user_id, lang: str) -> dict:
    if chat is None or getattr(chat, "type", None) != "private" or not user_id:
        return {}
    try:
        return {"reply_markup": await _pack_keyboard_for(context, user_id, lang)}
    except Exception:
        logger.debug("Pack buttons not attached", exc_info=True)
        return {}


def _new_pack_labels() -> "list[str]":
    return sorted({i18n.t(lang, "btn_new_pack") for lang in i18n.SUPPORTED_LANGUAGES})


async def _send_pack_keyboard(update: Update, context: ContextTypes.DEFAULT_TYPE, lang: str) -> None:
    if update.effective_chat is None or update.effective_chat.type != "private":
        return
    try:
        await context.bot.send_message(update.effective_chat.id, i18n.t(lang, "packs_keyboard_note"),
                                       reply_markup=await _pack_keyboard_for(
                                           context, update.effective_user.id, lang, sync=True))
    except Exception:
        logger.debug("Couldn't send the pack buttons", exc_info=True)


async def _open_pack(update: Update, context: ContextTypes.DEFAULT_TYPE, lang: str, pack_name: str,
                     owner_id: int, title: str, adopt: "dict | None" = None,
                     intro_key: str = "editing_intro_add"):
    same = context.user_data.get("target_pack") == pack_name
    last_sticker = context.user_data.get("last_sticker_id") if same else None
    title = (await _current_titles(context, [(pack_name, title)]))[0][1]
    _fresh_session(context, {"lang": lang, "mode": "add", "pack_created": True, "target_pack": pack_name,
                             "owner_id": owner_id})
    if last_sticker:
        context.user_data["last_sticker_id"] = last_sticker
    send_kwargs = {}
    if adopt is None and update.effective_chat.type == "private":
        send_kwargs["reply_markup"] = await _pack_keyboard_for(context, update.effective_user.id, lang, sync=True)
    await _start_status(context, update.effective_chat.id, title, lang, intro=i18n.t(lang, intro_key),
                        adopt=adopt, **send_kwargs)
    await _add_held(update, context, lang)
    return EDITING


async def pack_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = await i18n.get_lang(update.effective_user.id, context)
    packs = await asyncio.to_thread(get_user_packs, update.effective_user.id)
    found = _pack_button_action(update.message.text.strip(), packs, context.user_data.get(KB_TITLES))
    if found is None:
        await update.message.reply_text(
            i18n.t(lang, "pack_button_gone"),
            reply_markup=await _pack_keyboard_for(context, update.effective_user.id, lang, sync=True))
        return None
    action, pack_name = found
    title = dict(packs).get(pack_name) or pack_name
    if action == "view":
        title = (await _current_titles(context, [(pack_name, title)]))[0][1]
        await update.message.reply_text(
            i18n.t(lang, "pack_view", title=title),
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(
                i18n.t(lang, "btn_open_pack"), url=f"https://t.me/addstickers/{pack_name}")]]))
        return None
    return await _open_pack(update, context, lang, pack_name, update.effective_user.id, title)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = await asyncio.to_thread(get_user_language, update.effective_user.id)
    await _claim_invites(update, context, lang or "en")
    if context.args:
        if lang is None:
            context.user_data["pending_start_args"] = context.args
            await update.message.reply_text(i18n.LANGUAGE_PROMPT, reply_markup=language_keyboard())
            return ConversationHandler.END
        context.user_data["lang"] = lang
        return await _continue_start(update, context, lang, context.args)

    if _has_status(context) and lang is not None:
        context.user_data["lang"] = lang
        await reply(update, build_start_text(lang), reply_markup=start_menu_keyboard(lang))
        await _send_pack_keyboard(update, context, lang)
        return None
    if _has_status(context):
        await _end_status(context, i18n.t("en", "cancelled_status_note"))
    _fresh_session(context)

    if lang is None:
        await update.message.reply_text(i18n.LANGUAGE_PROMPT, reply_markup=language_keyboard())
        return ConversationHandler.END

    context.user_data["lang"] = lang
    result = await _continue_start(update, context, lang, None)
    await _send_pack_keyboard(update, context, lang)
    return result


async def language_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = await asyncio.to_thread(get_user_language, update.effective_user.id)
    if lang:
        context.user_data["lang"] = lang
    await update.message.reply_text(i18n.LANGUAGE_PROMPT, reply_markup=language_keyboard(lang))


async def _continue_start(update: Update, context: ContextTypes.DEFAULT_TYPE, lang: str, args):
    if args and args[0].startswith("s_"):
        return await start_coedit_link(update, context, args[0][2:], lang)

    await reply(update, build_start_text(lang), reply_markup=start_menu_keyboard(lang))
    return ConversationHandler.END


async def _apply_language(update: Update, context: ContextTypes.DEFAULT_TYPE, lang: str):
    await asyncio.to_thread(set_user_language, update.effective_user.id, lang)
    pending = context.user_data.get("pending_start_args") or []
    if _has_status(context):
        await _end_status(context, i18n.t(lang, "cancelled_status_note"))
    _fresh_session(context)
    context.user_data["lang"] = lang
    await refresh_chat_menu(context, update.effective_user.id, lang)
    return await _continue_start(update, context, lang, pending)


async def _set_language(update: Update, context: ContextTypes.DEFAULT_TYPE, lang: str):
    await update.message.reply_text(i18n.t(lang, "language_set_confirmation"))
    return await _apply_language(update, context, lang)


async def set_language_en(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await _set_language(update, context, "en")


async def set_language_uz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await _set_language(update, context, "uz")


async def set_language_rus(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await _set_language(update, context, "ru")


async def language_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    lang = query.data.split(":", 1)[1]
    await query.answer(i18n.t(lang, "language_set_confirmation"))
    return await _apply_language(update, context, lang)


async def menu_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = await i18n.get_lang(update.effective_user.id, context)
    await update.callback_query.answer()
    await edit_in_place(update.callback_query.message, context.bot, build_start_text(lang), reply_markup=start_menu_keyboard(lang))


async def start_coedit_link(update: Update, context: ContextTypes.DEFAULT_TYPE, token: str, lang: str):
    user = update.effective_user
    outcome, pack_name = await asyncio.to_thread(join_by_token, token, user.id)
    refusal = {"invalid": "coedit_link_invalid", "gone": "coedit_pack_gone", "own": "coedit_own_pack",
               "expired": "coedit_link_expired", "full": "coedit_link_full"}.get(outcome)
    if refusal:
        await reply(update, i18n.t(lang, refusal))
        return ConversationHandler.END
    owner_id = await asyncio.to_thread(get_pack_owner, pack_name)
    title = await asyncio.to_thread(get_pack_title, pack_name) or pack_name

    _fresh_session(context)
    context.user_data["lang"] = lang
    context.user_data["mode"] = "add"
    context.user_data["pack_created"] = True
    context.user_data["target_pack"] = pack_name
    context.user_data["owner_id"] = owner_id

    intro = i18n.t(lang, "coedit_joined_intro", title=title)
    await _start_status(context, update.effective_chat.id, title, lang, intro=intro)
    return EDITING


async def menu_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = await i18n.get_lang(update.effective_user.id, context)
    await update.callback_query.answer()
    await edit_in_place(update.callback_query.message, context.bot, build_help_text(lang, full=True),
                        reply_markup=help_keyboard(lang, True, help_rows(lang), HELP_PAGES, HELP_LABELS))


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = await i18n.get_lang(update.effective_user.id, context)
    page = help_page_name(context, HELP_PAGES, HELP_ALIASES)
    if page:
        await update.message.reply_text(help_page_text(lang, HELP_PAGES, page),
                                        link_preview_options=NO_PREVIEW)
        return
    full = wants_full_help(context)
    await update.message.reply_text(build_help_text(lang, full=full),
                                    reply_markup=help_keyboard(lang, full, help_rows(lang), HELP_PAGES, HELP_LABELS))


async def menu_mypacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await show_packs(update, context)


async def mypacks_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_packs(update, context)


async def show_packs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = await i18n.get_lang(update.effective_user.id, context)
    user_id = update.effective_user.id
    if update.message is not None:
        await _claim_invites(update, context, lang)
    packs = await asyncio.to_thread(get_user_packs, user_id)
    shared = await asyncio.to_thread(shared_packs, user_id)
    if not packs and not shared:
        kb = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton(i18n.t(lang, "btn_new_pack"), callback_data="menu_newpack")],
                [InlineKeyboardButton(i18n.t(lang, "btn_back"), callback_data="menu_start")],
            ]
        )
        await reply(update, i18n.t(lang, "no_packs_yet"), reply_markup=kb)
        return
    text = i18n.t(lang, "your_packs") + ("\n" + i18n.t(lang, "your_packs_shared_note") if shared else "")
    shown = await reply(update, text, reply_markup=packs_keyboard(packs, lang, shared))
    changed = await _refresh_pack_titles(context.bot, (list(packs) + list(shared))[:TITLE_REFRESH_MAX])
    if changed:
        def fix(rows):
            return [(name, changed.get(name, title)) for name, title in rows]
        await shown.set(context.bot, text, reply_markup=packs_keyboard(fix(packs), lang, fix(shared)))


async def _refresh_pack_titles(bot, packs) -> "dict[str, str]":
    async def current(name, title):
        try:
            now = (await bot.get_sticker_set(name)).title
        except TelegramError:
            return None
        return (name, now) if now and now != title else None

    try:
        found = await asyncio.wait_for(
            asyncio.gather(*(current(name, title) for name, title in packs)), TITLE_REFRESH_SECONDS)
    except asyncio.TimeoutError:
        return {}
    changed = dict(pair for pair in found if pair)
    if changed:
        await asyncio.to_thread(set_pack_titles, changed)
    return changed


async def pack_detail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    pack_name = await _pack_of(update.effective_user.id, query.data.split(":", 1)[1])
    user = update.effective_user
    lang = await i18n.get_lang(user.id, context)

    if not await _owns_pack_or_deny(pack_name, user.id):
        await query.answer(i18n.t(lang, "not_your_pack"), show_alert=True)
        return
    await query.answer()

    title = await asyncio.to_thread(get_pack_title, pack_name) or pack_name
    public = await asyncio.to_thread(is_pack_public, pack_name)
    await edit_in_place(query.message, context.bot,
        i18n.t(lang, "pack_detail_title", title=title), reply_markup=pack_detail_keyboard(pack_name, lang, public)
    )


async def pack_public_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    pack_name = await _pack_of(update.effective_user.id, query.data.split(":", 1)[1])
    user = update.effective_user
    lang = await i18n.get_lang(user.id, context)
    if not await _owns_pack_or_deny(pack_name, user.id):
        await query.answer(i18n.t(lang, "not_your_pack"), show_alert=True)
        return
    public = not await asyncio.to_thread(is_pack_public, pack_name)
    await asyncio.to_thread(set_pack_public, pack_name, public)
    title = await asyncio.to_thread(get_pack_title, pack_name) or pack_name
    await query.answer(i18n.t(lang, "pack_now_public" if public else "pack_now_private", title=title),
                       show_alert=True)
    await edit_in_place(query.message, context.bot, i18n.t(lang, "pack_detail_title", title=title),
                        reply_markup=pack_detail_keyboard(pack_name, lang, public))


CO_PREFIX = "co:"


def _period_label(lang: str, days: "int | None") -> str:
    return i18n.t(lang, "co_period_none") if days is None else i18n.t(lang, f"co_period_{days}")


def _valid_line(lang: str, view: dict) -> str:
    expires = view["expires_at"]
    if expires is None:
        return i18n.t(lang, "co_valid_forever")
    left = expires - datetime.now(timezone.utc)
    date = expires.strftime("%Y-%m-%d %H:%M UTC")
    if left.total_seconds() <= 0:
        return i18n.t(lang, "co_valid_expired", date=date)
    hours = int(left.total_seconds() // 3600)
    left_text = i18n.t(lang, "co_left_days", n=hours // 24) if hours >= 48 else i18n.t(lang, "co_left_hours", n=max(1, hours))
    return i18n.t(lang, "co_valid_until", date=date, left=left_text)


def _next_choice(choices, current):
    try:
        return choices[(choices.index(current) + 1) % len(choices)]
    except ValueError:
        return choices[0]


async def _send_coedit_message(message, bot, pack_name: str, lang: str):
    view = await asyncio.to_thread(get_coedit_view, pack_name)
    title = view["title"] or pack_name
    link = f"https://t.me/{BOT_USERNAME}?start=s_{view['token']}"
    invited = i18n.t(lang, "co_invited_count", n=view["invited"]) if view["invited"] else ""
    text = i18n.t(lang, "coedit_message", title=title, link=link, valid_line=_valid_line(lang, view),
                  editors=view["editors"], max=view["max_editors"], invited=invited)
    pid = view["pack_id"]
    kb = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(i18n.t(lang, "btn_co_valid", period=_period_label(lang, view["valid_days"])),
                                  callback_data=f"{CO_PREFIX}exp:{pid}")],
            [InlineKeyboardButton(i18n.t(lang, "btn_co_max", n=view["max_editors"]),
                                  callback_data=f"{CO_PREFIX}max:{pid}")],
            [InlineKeyboardButton(i18n.t(lang, "btn_co_delete_on" if view["editors_delete"] else "btn_co_delete_off"),
                                  callback_data=f"{CO_PREFIX}del:{pid}")],
            [InlineKeyboardButton(i18n.t(lang, "btn_co_invite"), callback_data=f"{CO_PREFIX}inv:{pid}")],
            [InlineKeyboardButton(i18n.t(lang, "btn_co_list", n=view["editors"] + view["invited"]),
                                  callback_data=f"{CO_PREFIX}list:{pid}")],
            [InlineKeyboardButton(i18n.t(lang, "btn_reset_link"), callback_data=f"{CO_PREFIX}new:{pid}")],
            [InlineKeyboardButton(i18n.t(lang, "btn_back"), callback_data=f"packopen:{pack_ref(pack_name)}")],
        ]
    )
    await edit_in_place(message, bot, text, reply_markup=kb, link_preview_options=NO_PREVIEW)


async def _editor_name(bot, user_id: int) -> str:
    try:
        chat = await bot.get_chat(user_id)
    except Exception:
        return f"#{user_id}"
    if chat.username:
        return f"@{chat.username}"
    return chat.full_name or f"#{user_id}"


CO_LIST_MAX = 30
INVITE_MARK_CHARS = 16


async def _send_coedit_list(message, bot, pack_name: str, pid: int, lang: str):
    editors = await asyncio.to_thread(pack_editors, pack_name)
    invites = await asyncio.to_thread(pack_invites, pack_name)
    title = await asyncio.to_thread(get_pack_title, pack_name) or pack_name
    lines = [i18n.t(lang, "co_list_heading", title=title)]
    rows = []
    names = await asyncio.gather(*(_editor_name(bot, user_id) for user_id in editors[:CO_LIST_MAX]))
    for user_id, name in zip(editors, names):
        lines.append(f"• {name}")
        rows.append([InlineKeyboardButton(f"✖ {name}", callback_data=f"{CO_PREFIX}rme:{pid}:{user_id}")])
    for mark, username in invites[:CO_LIST_MAX]:
        lines.append(i18n.t(lang, "co_list_invited", username=username))
        rows.append([InlineKeyboardButton(f"✖ @{username}",
                                          callback_data=f"{CO_PREFIX}rmi:{pid}:{mark[:INVITE_MARK_CHARS]}")])
    hidden = max(0, len(editors) - CO_LIST_MAX) + max(0, len(invites) - CO_LIST_MAX)
    if hidden:
        lines.append(i18n.t(lang, "co_list_more", n=hidden))
    if not editors and not invites:
        lines.append(i18n.t(lang, "co_list_empty"))
    else:
        lines.append("\n" + i18n.t(lang, "co_list_remove_hint"))
    rows.append([InlineKeyboardButton(i18n.t(lang, "btn_back"), callback_data=f"{CO_PREFIX}back:{pid}")])
    await edit_in_place(message, bot, "\n".join(lines), reply_markup=InlineKeyboardMarkup(rows))


async def _co_pack(update: Update, context: ContextTypes.DEFAULT_TYPE, lang: str):
    query = update.callback_query
    parts = query.data[len(CO_PREFIX):].split(":")
    pack_name = None
    if len(parts) >= 2 and parts[1].isdigit():
        pack_name = await asyncio.to_thread(pack_name_by_id, int(parts[1]))
    if pack_name is None or not await _owns_pack_or_deny(pack_name, update.effective_user.id):
        await query.answer(i18n.t(lang, "only_owner_coedit"), show_alert=True)
        return None
    return pack_name, int(parts[1]), parts[0], parts[2:]


async def coedit_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    lang = await i18n.get_lang(update.effective_user.id, context)
    found = await _co_pack(update, context, lang)
    if found is None:
        return
    pack_name, pid, action, rest = found
    if action == "exp":
        view = await asyncio.to_thread(get_coedit_view, pack_name)
        days = _next_choice(VALID_DAYS_CHOICES, view["valid_days"])
        await asyncio.to_thread(set_link_valid_days, pack_name, days)
        await query.answer(i18n.t(lang, "co_valid_set", period=_period_label(lang, days)))
    elif action == "max":
        view = await asyncio.to_thread(get_coedit_view, pack_name)
        most = _next_choice(MAX_EDITORS_CHOICES, view["max_editors"])
        await asyncio.to_thread(set_max_editors, pack_name, most)
        await query.answer(i18n.t(lang, "co_max_set", n=most))
    elif action == "del":
        view = await asyncio.to_thread(get_coedit_view, pack_name)
        allowed = not view["editors_delete"]
        await asyncio.to_thread(set_editors_delete, pack_name, allowed)
        await query.answer(i18n.t(lang, "co_delete_set_on" if allowed else "co_delete_set_off"), show_alert=allowed)
    elif action == "new":
        await asyncio.to_thread(reset_share_token, pack_name)
        await query.answer(i18n.t(lang, "link_reset_confirm"))
    elif action == "list":
        await query.answer()
        await _send_coedit_list(query.message, context.bot, pack_name, pid, lang)
        return
    elif action == "rme" and rest and rest[0].isdigit():
        await asyncio.to_thread(remove_editor, pack_name, int(rest[0]))
        await query.answer(i18n.t(lang, "co_removed"))
        await _send_coedit_list(query.message, context.bot, pack_name, pid, lang)
        return
    elif action == "rmi" and rest and len(rest[0]) == INVITE_MARK_CHARS:
        invites = await asyncio.to_thread(pack_invites, pack_name)
        for mark, _ in invites:
            if mark.startswith(rest[0]):
                await asyncio.to_thread(remove_invite, pack_name, mark)
                break
        await query.answer(i18n.t(lang, "co_removed"))
        await _send_coedit_list(query.message, context.bot, pack_name, pid, lang)
        return
    else:
        await query.answer()
    await _send_coedit_message(query.message, context.bot, pack_name, lang)


USERNAME_RE = re.compile(r"^@?([A-Za-z][A-Za-z0-9_]{4,31})$")
INVITES_PER_MESSAGE = 20


async def invite_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    lang = await i18n.get_lang(update.effective_user.id, context)
    found = await _co_pack(update, context, lang)
    if found is None:
        return None
    pack_name, pid = found[0], found[1]
    if not invites_usable():
        await query.answer(i18n.t(lang, "invite_unavailable"), show_alert=True)
        return None
    await query.answer()
    session = _session_of(context)
    _fresh_session(context, session)
    context.user_data["lang"] = lang
    context.user_data["invite_pack"] = pack_name
    context.user_data["invite_pack_id"] = pid
    title = await asyncio.to_thread(get_pack_title, pack_name) or pack_name
    await edit_in_place(query.message, context.bot, i18n.t(lang, "invite_prompt", title=title))
    return INVITE


async def receive_invites(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = await i18n.get_lang(update.effective_user.id, context)
    pack_name = context.user_data.pop("invite_pack", None)
    pid = context.user_data.pop("invite_pack_id", None)
    back = EDITING if _session_of(context) else ConversationHandler.END
    if not pack_name or not await _owns_pack_or_deny(pack_name, update.effective_user.id):
        await update.message.reply_text(i18n.t(lang, "only_owner_coedit"))
        return back
    names, bad = [], []
    for word in update.message.text.replace(",", " ").split()[:INVITES_PER_MESSAGE]:
        match = USERNAME_RE.match(word)
        if match and match.group(1).lower() not in (n.lower() for n in names):
            names.append(match.group(1))
        elif not match:
            bad.append(word)
    invited, full = await asyncio.to_thread(add_invites, pack_name, names) if names else ([], [])
    view = await asyncio.to_thread(get_coedit_view, pack_name)
    lines = []
    if invited:
        lines.append(i18n.t(lang, "invite_done", names=", ".join("@" + n for n in invited)))
    if full:
        lines.append(i18n.t(lang, "invite_full", max=view["max_editors"], names=", ".join("@" + n for n in full)))
    if bad:
        lines.append(i18n.t(lang, "invite_bad", names=", ".join(bad[:5])))
    kb = InlineKeyboardMarkup([[InlineKeyboardButton(i18n.t(lang, "btn_back"),
                                                     callback_data=f"{CO_PREFIX}back:{pid}")]])
    await update.message.reply_text("\n".join(lines) or i18n.t(lang, "invite_bad", names="—"), reply_markup=kb)
    return back


async def coedit_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    pack_name = await _pack_of(update.effective_user.id, query.data.split(":", 1)[1])
    user = update.effective_user
    lang = await i18n.get_lang(user.id, context)

    if not await _owns_pack_or_deny(pack_name, user.id):
        await query.answer(i18n.t(lang, "only_owner_coedit"), show_alert=True)
        return
    await query.answer()
    await _send_coedit_message(query.message, context.bot, pack_name, lang)


async def coedit_reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    pack_name = await _pack_of(update.effective_user.id, query.data.split(":", 1)[1])
    user = update.effective_user
    lang = await i18n.get_lang(user.id, context)

    if not await _owns_pack_or_deny(pack_name, user.id):
        await query.answer(i18n.t(lang, "only_owner_coedit"), show_alert=True)
        return

    await asyncio.to_thread(reset_share_token, pack_name)
    await query.answer(i18n.t(lang, "link_reset_confirm"))
    await _send_coedit_message(query.message, context.bot, pack_name, lang)


async def rename_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    pack_name = await _pack_of(update.effective_user.id, query.data.split(":", 1)[1])
    user = update.effective_user
    lang = await i18n.get_lang(user.id, context)

    if not await _owns_pack_or_deny(pack_name, user.id):
        await query.answer(i18n.t(lang, "only_owner_rename"), show_alert=True)
        return None

    await query.answer()
    session = _session_of(context)
    _fresh_session(context, session)
    context.user_data["lang"] = lang
    context.user_data["rename_pack"] = pack_name
    prompt = await edit_in_place(
        query.message, context.bot,
        i18n.t(lang, "rename_prompt",
               title=await asyncio.to_thread(get_pack_title, pack_name) or pack_name),
    )
    context.user_data["rename_prompt"] = prompt.save()
    return RENAME


async def receive_new_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = await i18n.get_lang(update.effective_user.id, context)
    pack_name = context.user_data.get("rename_pack")
    prompt = LiveMessage.restore(context.user_data.get("rename_prompt"))
    new_title = update.message.text.strip()[:PACK_TITLE_MAX]
    if not pack_name or prompt is None:
        await update.message.reply_text(i18n.t(lang, "rename_broken_state"))
        return EDITING if _session_of(context) else ConversationHandler.END

    kb = InlineKeyboardMarkup([[InlineKeyboardButton(i18n.t(lang, "btn_back_to_pack"), callback_data=f"packopen:{pack_ref(pack_name)}")]])
    try:
        await context.bot.set_sticker_set_title(name=pack_name, title=new_title)
        await asyncio.to_thread(set_pack_title, pack_name, new_title)
        await prompt.finish(context.bot, i18n.t(lang, "renamed_success", title=new_title), reply_markup=kb)
    except Exception as exc:
        logger.exception("Rename failed")
        await prompt.finish(context.bot, i18n.t(lang, "renamed_failed", error=exc), reply_markup=kb)

    context.user_data.pop("rename_pack", None)
    context.user_data.pop("rename_prompt", None)
    if _session_of(context):
        if context.user_data.get("target_pack") == pack_name:
            context.user_data["title"] = new_title
        return EDITING
    _fresh_session(context)
    return ConversationHandler.END


async def delete_pack_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    pack_name = await _pack_of(update.effective_user.id, query.data.split(":", 1)[1])
    user = update.effective_user
    lang = await i18n.get_lang(user.id, context)

    if not await _owns_pack_or_deny(pack_name, user.id):
        await query.answer(i18n.t(lang, "only_owner_delete"), show_alert=True)
        return
    await query.answer()

    title = await asyncio.to_thread(get_pack_title, pack_name) or pack_name
    kb = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(i18n.t(lang, "btn_delete"), callback_data=f"packdelconfirm1:{pack_ref(pack_name)}")],
            [InlineKeyboardButton(i18n.t(lang, "btn_cancel_inline"), callback_data=f"packopen:{pack_ref(pack_name)}")],
        ]
    )
    await edit_in_place(query.message, context.bot, 
        i18n.t(lang, "delete_confirm1", title=title),
        reply_markup=kb,
    )


async def delete_pack_confirm1(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    pack_name = await _pack_of(update.effective_user.id, query.data.split(":", 1)[1])
    user = update.effective_user
    lang = await i18n.get_lang(user.id, context)

    if not await _owns_pack_or_deny(pack_name, user.id):
        await query.answer(i18n.t(lang, "only_owner_delete"), show_alert=True)
        return
    await query.answer()

    title = await asyncio.to_thread(get_pack_title, pack_name) or pack_name
    kb = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(i18n.t(lang, "btn_delete_confirm"), callback_data=f"packdelconfirm2:{pack_ref(pack_name)}")],
            [InlineKeyboardButton(i18n.t(lang, "btn_cancel_inline"), callback_data=f"packopen:{pack_ref(pack_name)}")],
        ]
    )
    await edit_in_place(query.message, context.bot, 
        i18n.t(lang, "delete_confirm2", title=title),
        reply_markup=kb,
    )


async def delete_pack_confirm2(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    pack_name = await _pack_of(update.effective_user.id, query.data.split(":", 1)[1])
    user = update.effective_user
    lang = await i18n.get_lang(user.id, context)

    if not await _owns_pack_or_deny(pack_name, user.id):
        await query.answer(i18n.t(lang, "only_owner_delete"), show_alert=True)
        return
    await query.answer()

    title = await asyncio.to_thread(get_pack_title, pack_name) or pack_name
    try:
        await context.bot.delete_sticker_set(name=pack_name)
    except Exception as exc:
        lower = str(exc).lower()
        if "invalid" not in lower and "not found" not in lower:
            logger.exception("Failed to delete sticker set")
            await edit_in_place(query.message, context.bot, i18n.t(lang, "delete_failed", error=exc))
            return

    await asyncio.to_thread(delete_pack_records, pack_name)
    kb = InlineKeyboardMarkup([[InlineKeyboardButton(i18n.t(lang, "btn_my_packs_back"), callback_data="menu_mypacks")]])
    await edit_in_place(query.message, context.bot, i18n.t(lang, "delete_success", title=title), reply_markup=kb)


async def newpack_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = await i18n.get_lang(update.effective_user.id, context)
    _fresh_session(context)
    context.user_data["lang"] = lang
    context.user_data["mode"] = "new"
    context.user_data["pack_created"] = False
    context.user_data["owner_id"] = update.effective_user.id
    if update.callback_query:
        await update.callback_query.answer()
    msg = await reply(update, i18n.t(lang, "newpack_title_prompt"))
    context.user_data["title_prompt"] = msg.save()
    return TITLE


async def receive_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = await i18n.get_lang(update.effective_user.id, context)
    title = update.message.text.strip()
    if not title:
        await update.message.reply_text(i18n.t(lang, "title_empty"))
        return TITLE
    if len(title) > PACK_TITLE_MAX:
        title = title[:PACK_TITLE_MAX]
        await update.message.reply_text(i18n.t(lang, "title_truncated", title=title))

    intro = i18n.t(lang, "editing_intro_new")
    prompt = context.user_data.pop("title_prompt", None)
    await _start_status(
        context, update.effective_chat.id, title, lang, intro=intro, adopt=prompt,
    )
    await _add_held(update, context, lang)
    return EDITING


async def addsticker_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = await i18n.get_lang(update.effective_user.id, context)
    user_id = update.effective_user.id
    await _claim_invites(update, context, lang)
    packs = await asyncio.to_thread(get_user_packs, user_id)
    if not packs and await asyncio.to_thread(shared_packs, user_id):
        return await show_packs(update, context)
    if not packs:
        await update.message.reply_text(i18n.t(lang, "no_packs_for_add"))
        return
    packs = await _current_titles(context, packs)
    await update.message.reply_text(
        i18n.t(lang, "pick_pack_prompt"), reply_markup=packs_keyboard(packs, lang)
    )


async def pack_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    pack_name = await _pack_of(update.effective_user.id, query.data.split(":", 1)[1])
    user = update.effective_user
    lang = await i18n.get_lang(user.id, context)

    access = await asyncio.to_thread(pack_access, pack_name, user.id) if pack_name else None
    if access is None or not access["allowed"]:
        await query.answer(i18n.t(lang, "not_your_pack"), show_alert=True)
        return None
    owner_id = access["owner_id"]
    await query.answer()

    deletes = owner_id == user.id or access["can_delete"]
    title = await asyncio.to_thread(get_pack_title, pack_name) or pack_name
    return await _open_pack(update, context, lang, pack_name, owner_id, title,
                            adopt=LiveMessage.adopt(query.message).save(),
                            intro_key="editing_intro_add" if deletes else "editing_intro_shared")


async def _add_input_sticker(
    context: ContextTypes.DEFAULT_TYPE, user, input_sticker: InputSticker,
    want_file_id: bool = True,
) -> "tuple[str | None, int | None]":
    owner_id = context.user_data["owner_id"]
    if owner_id != user.id and context.user_data.get("pack_created"):
        await _still_coediting(context, user.id)

    if not context.user_data.get("pack_created"):
        title = context.user_data["title"]
        for attempt in range(1, sticker_ops.NAME_TRIES + 1):
            pack_name = f"{slugify(title)}_{secrets.token_hex(5)}_by_{BOT_USERNAME}"
            try:
                await sticker_ops.telegram_call(
                    context.bot.create_new_sticker_set,
                    user_id=owner_id,
                    name=pack_name,
                    title=title,
                    stickers=[input_sticker],
                    read_timeout=60,
                    write_timeout=60,
                )
                break
            except BadRequest as exc:
                if attempt == sticker_ops.NAME_TRIES or not sticker_ops.name_refused(exc):
                    raise
                logger.info("Telegram did not take a new pack's name (%s); trying another", exc)
        await asyncio.to_thread(
            add_pack, owner_id, pack_name, title, user.username, user.full_name
        )
        context.user_data["target_pack"] = pack_name
        context.user_data["pack_created"] = True
        note_stat("sticker_pack", "created")
        if not want_file_id:
            return None, None
        file_id, number = await sticker_ops.last_sticker(context.bot, pack_name)
        return file_id, number or 1
    return await sticker_ops.add_to_pack(context.bot, owner_id, context.user_data["target_pack"],
                                         input_sticker, want_file_id)


class NoLongerEditor(Exception):
    pass


ACCESS_RECHECK_SECONDS = 60


async def _still_coediting(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> None:
    now = datetime.now(timezone.utc).timestamp()
    if now - context.user_data.get("access_checked_at", 0) < ACCESS_RECHECK_SECONDS:
        return
    access = await asyncio.to_thread(pack_access, context.user_data["target_pack"], user_id)
    if access is None or not access["allowed"]:
        raise NoLongerEditor(context.user_data.get("title") or context.user_data["target_pack"])
    context.user_data["access_checked_at"] = now


def _session_of(context: ContextTypes.DEFAULT_TYPE) -> dict:
    if not _has_status(context):
        return {}
    keys = ("mode", "pack_created", "target_pack", "owner_id", "title", "sticker_count",
            "status_intro", "last_sticker_id", "nudge_counted", STATUS_KEY)
    return {key: context.user_data[key] for key in keys if key in context.user_data}


def _behind_question(handler):
    async def run(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if _has_status(context):
            await handler(update, context)
        elif update.effective_chat.type == "private" and (item := _item_of(update.message)) is not None:
            await _hold_item(context, await i18n.get_lang(update.effective_user.id, context), item)
        return None
    return run


BATCH_MEDIA = (filters.PHOTO | filters.Document.IMAGE | filters.Sticker.STATIC | filters.ANIMATION
               | filters.VIDEO | filters.Document.VIDEO | filters.Sticker.VIDEO)
BATCH_KEYS = ("batch_open", "batch_seen", "batch_failed", "batch_chat", "batch_handled")


def _queued_items(context: ContextTypes.DEFAULT_TYPE, update: Update) -> int:
    chat_id = update.effective_chat.id if update.effective_chat is not None else None
    application = getattr(context, "application", None)
    if application is None:
        return 0
    if isinstance(getattr(application, "update_processor", None), shared_features.PerUserUpdateProcessor):
        waiting = shared_features.waiting_updates(application, update)
    else:
        try:
            waiting = list(application.update_queue._queue)
        except Exception:
            waiting = []
    count = 0
    for item in waiting:
        try:
            if (isinstance(item, Update) and item.effective_chat is not None
                    and item.effective_chat.id == chat_id and BATCH_MEDIA.check_update(item)):
                count += 1
        except Exception:
            continue
    return count


def _run_line(context: ContextTypes.DEFAULT_TYPE, update: Update, lang: str, failed_now: bool = False) -> str:
    data = context.user_data
    if not data.get("batch_open"):
        return ""
    seen = data.get("batch_seen", 0)
    queued = _queued_items(context, update)
    if queued:
        return f"⏳ {seen}/{seen + queued}"
    if seen < 2:
        return ""
    failed = data.get("batch_failed", 0) + (1 if failed_now else 0)
    if not failed:
        return f"✅ {seen}/{seen}"
    return i18n.t(lang, "batch_not_added", done=seen, total=seen, failed=failed)


def _with_run(text: str, line: str, failed: bool) -> str:
    if not line:
        return text
    return f"{line}\n{text}" if failed else f"{text}\n{line}"


async def _batch_item(update: Update, context: ContextTypes.DEFAULT_TYPE, handler):
    data = context.user_data
    chat_id = update.effective_chat.id
    if not data.get("batch_open") or data.get("batch_chat") != chat_id:
        data.update(batch_open=True, batch_seen=0, batch_failed=0, batch_chat=chat_id)
    data["batch_seen"] = data.get("batch_seen", 0) + 1
    data["batch_handled"] = False
    before = data.get("sticker_count", 0)
    handled = False
    try:
        result = await handler(update, context)
        handled = data.get("sticker_count", 0) > before or bool(data.get("batch_handled"))
        return result
    finally:
        if not handled:
            data["batch_failed"] = data.get("batch_failed", 0) + 1
        if data.get("batch_open") and not _queued_items(context, update):
            data["batch_open"] = False


async def _add_and_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE, lang: str,
                           input_sticker: InputSticker, item: dict, working=None,
                           fit: "dict | None" = None) -> None:
    try:
        file_id, number = await _add_input_sticker(context, update.effective_user, input_sticker)
    except NoLongerEditor as exc:
        await _say(context, item, working,
                   _with_run(i18n.t(lang, "coedit_removed_access", title=str(exc)),
                             _run_line(context, update, lang, failed_now=True), failed=True))
        return
    except Exception as exc:
        logger.exception("Sticker set operation failed")
        record_error(exc)
        await _say(context, item, working,
                   _with_run(sticker_ops.error_note(exc, lang),
                             _run_line(context, update, lang, failed_now=True), failed=True))
        return
    context.user_data["last_sticker_id"] = file_id
    context.user_data["sticker_count"] = context.user_data.get("sticker_count", 0) + 1
    note_stat("sticker", "video" if getattr(input_sticker, "format", None) == "video" else "picture")
    for choice in ("speed", "res", "fps"):
        if (fit or {}).get(choice):
            note_stat("video_fit", choice)
    pack_name = context.user_data["target_pack"]
    seen = sticker_ops.seen_title(pack_name)
    if seen and seen != context.user_data.get("title"):
        context.user_data["title"] = seen
        try:
            await asyncio.to_thread(set_pack_title, pack_name, seen)
        except Exception:
            logger.debug("Couldn't save a pack's new title", exc_info=True)
    title = context.user_data.get("title") or pack_name
    text = sticker_ops.added_text(lang, title, number, DEFAULT_EMOJI, how=_fit_label(fit, lang))
    if number == 1:
        text = i18n.t(lang, "pack_created_link", pack_name=pack_name) + "\n" + text
    text = _with_run(text, _run_line(context, update, lang), failed=False)
    sent = await _say(context, item, working, text)
    sticker_ops.remember(item["chat"], item["msg"], pack_name, file_id)
    sticker_ops.remember(sent.chat_id, sent.message_id, pack_name, file_id)
    if not context.user_data.get("nudge_counted"):
        context.user_data["nudge_counted"] = True
        nudge = await maybe_donation_nudge(update.effective_user.id, lang, context, item["chat"])
        if nudge:
            await _reply_to_item(context, item, nudge)


async def _remember_last_sticker(context: ContextTypes.DEFAULT_TYPE) -> None:
    pack_name = context.user_data.get("target_pack")
    if not pack_name:
        return
    try:
        sticker_set = await context.bot.get_sticker_set(pack_name)
        context.user_data["last_sticker_id"] = sticker_set.stickers[-1].file_id
    except Exception:
        logger.debug("Couldn't re-read the pack after a bulk import", exc_info=True)


async def _is_own_pack_sticker(context: ContextTypes.DEFAULT_TYPE, item: dict) -> bool:
    sticker = types.SimpleNamespace(set_name=item.get("set"), file_unique_id=item.get("uid"))
    return await sticker_ops.in_pack(context.bot, context.user_data.get("target_pack"), sticker)


async def _sticker_already_in_pack(update: Update, context: ContextTypes.DEFAULT_TYPE, item: dict) -> int:
    context.user_data["batch_handled"] = True
    user_id = update.effective_user.id
    pack_name = context.user_data["target_pack"]
    lang = await i18n.get_lang(user_id, context)
    access = await asyncio.to_thread(pack_access, pack_name, user_id)
    title = context.user_data.get("title") or (access or {}).get("title") or pack_name
    if access is None or not access["allowed"]:
        await update.message.reply_text(i18n.t(lang, "coedit_removed_access", title=title))
        return EDITING
    kind, file_id = item["kind"], item["file_id"]
    await group_mode.ask_add_again(update, context, lang, pack_name, title, kind, file_id, in_group=False,
                                   can_remove=access["can_delete"])
    return EDITING


async def already_in_pack_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query, user = update.callback_query, update.effective_user
    lang = await i18n.get_lang(user.id, context)
    token, _, choice = query.data[len(group_mode.DUP_PREFIX):].partition(":")
    asked = sticker_ops.held(token)
    if asked is None:
        await query.answer(i18n.t(lang, "dup_expired"), show_alert=True)
        return None
    if asked["user"] != user.id:
        await query.answer(i18n.t(lang, "dup_not_yours"), show_alert=True)
        return None
    if choice in ("r", "R") and not asked["group"]:
        return await _remove_asked(update, context, lang, token, asked, confirmed=choice == "R")
    sticker_ops.release(token)
    await query.answer()
    if choice == "y":
        await group_mode.add_again(update, context, lang, asked)
    else:
        await query.message.edit_text(i18n.t(lang, "keep_pack" if choice == "c" else "dup_not_added"))
    return None


async def _remove_asked(update: Update, context: ContextTypes.DEFAULT_TYPE, lang: str, token: str,
                        asked: dict, confirmed: bool):
    query, user = update.callback_query, update.effective_user
    pack_name = asked["pack"]
    access = await asyncio.to_thread(pack_access, pack_name, user.id)
    if access is None or not access["can_delete"]:
        sticker_ops.release(token)
        await query.answer()
        await query.message.edit_text(i18n.t(lang, "remove_not_allowed"))
        return None
    try:
        sticker_set = await context.bot.get_sticker_set(pack_name)
    except Exception:
        sticker_set = None
    last = sticker_set is not None and len(sticker_set.stickers) <= 1
    if last and access["owner_id"] != user.id:
        sticker_ops.release(token)
        await query.answer()
        await query.message.edit_text(i18n.t(lang, "remove_last_owner_only"))
        return None
    if last and not confirmed:
        await query.answer()
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton(i18n.t(lang, "btn_delete_pack_yes"), callback_data=f"{group_mode.DUP_PREFIX}{token}:R"),
            InlineKeyboardButton(i18n.t(lang, "btn_cancel"), callback_data=f"{group_mode.DUP_PREFIX}{token}:c"),
        ]])
        await query.message.edit_text(i18n.t(lang, "remove_last_confirm"), reply_markup=keyboard)
        return None
    sticker_ops.release(token)
    await query.answer()
    try:
        await sticker_ops.telegram_call(context.bot.delete_sticker_from_set, sticker=asked["file_id"])
    except Exception as exc:
        logger.exception("Failed to delete a sticker from its set")
        record_error(exc)
        await query.message.edit_text(i18n.t(lang, "remove_failed", error=exc))
        return None
    filling = context.user_data.get("target_pack") == pack_name
    if last:
        await asyncio.to_thread(delete_pack_records, pack_name)
        await query.message.edit_text(i18n.t(lang, "pack_deleted_empty"))
        if filling:
            await _end_status(context, i18n.t(lang, "pack_deleted_note"))
            _fresh_session(context)
            return ConversationHandler.END
        return None
    if filling:
        context.user_data["sticker_count"] = max(0, context.user_data.get("sticker_count", 0) - 1)
    await query.message.edit_text(i18n.t(lang, "remove_success"))
    return None


async def confirm_remove_last_sticker(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    lang = await i18n.get_lang(update.effective_user.id, context)
    await query.answer()
    choice = query.data.split(":", 1)[1]
    file_id = context.user_data.pop("pending_removal", None)

    if choice == "no" or not file_id:
        await edit_in_place(query.message, context.bot, i18n.t(lang, "keep_pack"))
        return EDITING

    target_pack = context.user_data.get("target_pack")
    try:
        await sticker_ops.telegram_call(context.bot.delete_sticker_from_set, sticker=file_id)
    except Exception as exc:
        logger.exception("Failed to delete the last sticker / pack")
        record_error(exc)
        await edit_in_place(query.message, context.bot, i18n.t(lang, "remove_failed", error=exc))
        return EDITING

    if target_pack:
        await asyncio.to_thread(delete_pack_records, target_pack)
    await edit_in_place(query.message, context.bot, i18n.t(lang, "pack_deleted_empty"))
    await _end_status(context, i18n.t(lang, "pack_deleted_note"))
    _fresh_session(context)
    return ConversationHandler.END


async def receive_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await _batch_item(update, context, _receive_media)


async def _receive_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = await i18n.get_lang(update.effective_user.id, context)
    item = _item_of(update.message)
    if item is None:
        return EDITING
    item["kind"] = "static"
    return await _add_item(update, context, lang, item)


async def receive_video_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await _batch_item(update, context, _receive_video_media)


async def _receive_video_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = await i18n.get_lang(update.effective_user.id, context)
    item = _item_of(update.message)
    if item is None:
        return EDITING
    item["kind"] = "video"
    return await _add_item(update, context, lang, item)


def _item_of(msg) -> "dict | None":
    found = sticker_ops.media_of(msg)
    if found is None:
        return None
    sticker = msg.sticker
    return {"chat": msg.chat_id, "msg": msg.message_id, "kind": found[0], "file_id": found[1],
            "set": getattr(sticker, "set_name", None), "uid": getattr(sticker, "file_unique_id", None),
            "at": time.time()}


async def _reply_to_item(context, item: dict, text: str, reply_markup=None, buttons: bool = True):
    if reply_markup is None and buttons and item["chat"] > 0 and not problems.find_code(text):
        try:
            reply_markup = await _pack_keyboard_for(context, item["chat"], context.user_data.get("lang") or "en")
        except Exception:
            logger.debug("Pack buttons not attached; the answer goes without them", exc_info=True)
    sent = await context.bot.send_message(
        item["chat"], text, reply_markup=reply_markup,
        reply_parameters=ReplyParameters(message_id=item["msg"], allow_sending_without_reply=True))
    live_message.bump(sent.chat_id, sent.message_id)
    return sent


async def _say(context, item: dict, working, text: str, **kwargs):
    if working is not None:
        try:
            await context.bot.edit_message_text(chat_id=working.chat_id, message_id=working.message_id,
                                                text=text, **kwargs)
            return working
        except BadRequest as exc:
            lowered = str(exc).lower()
            if "not modified" in lowered:
                return working
            if "not found" not in lowered and "can't be edited" not in lowered:
                raise
        return await _reply_to_item(context, item, text, buttons=False, **kwargs)
    return await _reply_to_item(context, item, text, **kwargs)


async def _add_item(update: Update, context: ContextTypes.DEFAULT_TYPE, lang: str, item: dict,
                    held: bool = False) -> int:
    if item.get("set") and await _is_own_pack_sticker(context, item):
        if held:
            context.user_data["batch_handled"] = True
            title = context.user_data.get("title") or context.user_data.get("target_pack")
            await _reply_to_item(context, item, i18n.t(lang, "held_already_in_pack", title=title))
            return EDITING
        return await _sticker_already_in_pack(update, context, item)
    if item["kind"] == "video":
        await _add_video_item(update, context, lang, item)
    else:
        await _add_static_item(update, context, lang, item)
    return EDITING


async def _add_static_item(update: Update, context: ContextTypes.DEFAULT_TYPE, lang: str, item: dict) -> None:
    try:
        input_sticker = await sticker_ops.static_sticker(await sticker_ops.download(context.bot, item["file_id"]))
    except Exception as exc:
        record_error(exc)
        await _reply_to_item(context, item, _with_run(i18n.t(lang, "image_process_failed", error=exc),
                                                      _run_line(context, update, lang, failed_now=True),
                                                      failed=True))
        return
    await _add_and_confirm(update, context, lang, input_sticker, item)


async def _add_video_item(update: Update, context: ContextTypes.DEFAULT_TYPE, lang: str, item: dict) -> None:
    refusal = await refuse_new_work(lang, update.effective_user.id, item["chat"])
    if refusal:
        await _reply_to_item(context, item, _with_run(refusal, _run_line(context, update, lang, failed_now=True),
                                                      failed=True))
        return
    try:
        raw = await sticker_ops.download(context.bot, item["file_id"])
    except Exception as exc:
        logger.warning("Couldn't fetch a clip to make a sticker of", exc_info=True)
        record_error(exc)
        await _reply_to_item(context, item, _with_run(i18n.t(lang, "video_convert_generic_failed", error=exc),
                                                      _run_line(context, update, lang, failed_now=True),
                                                      failed=True))
        return
    duration = await asyncio.to_thread(video_sticker.clip_duration, bytes(raw))
    if video_sticker.is_too_long(duration):
        if await _offer_video_fit(context, lang, item, None, {}, "long", duration):
            context.user_data["batch_handled"] = True
        return
    await _make_video_sticker(update, context, lang, raw, item, {}, None)


async def _make_video_sticker(update: Update, context: ContextTypes.DEFAULT_TYPE, lang: str, raw, item: dict,
                              fit: dict, answer) -> None:
    if answer is None:
        answer = await _reply_to_item(context, item, i18n.t(lang, "converting_video"), buttons=False)
    try:
        async with lifecycle.busy(item["chat"], i18n.t(lang, "restarting_send_again")):
            input_sticker = await sticker_ops.video_sticker(raw, lang, fit.get("speed") or 1.0,
                                                            bool(fit.get("res")), bool(fit.get("fps")))
    except TooBig:
        if await _offer_video_fit(context, lang, item, answer, fit, "heavy", None):
            context.user_data["batch_handled"] = True
        return
    except ConversionError as exc:
        record_error(exc)
        kb = InlineKeyboardMarkup([sibling_bots_keyboard_row(BOT_NAME, only="convertbot")])
        await _say(context, item, answer, _with_run(i18n.t(lang, "video_convert_failed_redirect", error=exc),
                                                    _run_line(context, update, lang, failed_now=True),
                                                    failed=True), reply_markup=kb)
        return
    except Exception as exc:
        logger.exception("Video conversion failed")
        record_error(exc)
        await _say(context, item, answer, _with_run(i18n.t(lang, "video_convert_generic_failed", error=exc),
                                                    _run_line(context, update, lang, failed_now=True),
                                                    failed=True))
        return
    await _add_and_confirm(update, context, lang, input_sticker, item, working=answer, fit=fit)


def _needs_session(handler):
    async def guarded(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if "owner_id" in context.user_data:
            return await handler(update, context)
        lang = await i18n.get_lang(update.effective_user.id, context)
        if update.callback_query:
            await update.callback_query.answer()
        item = _item_of(update.message) if update.message else None
        if item is not None and update.effective_chat.type == "private":
            await _hold_item(context, lang, item)
            return ConversationHandler.END
        await reply(update, i18n.t(lang, "session_lost"))
        return ConversationHandler.END
    guarded.__name__ = handler.__name__
    guarded.__doc__ = handler.__doc__
    return guarded


HOLD_SECONDS = int(os.environ.get("SBOT_HOLD_SECONDS", "600"))
HOLD_MAX = 30


def _held_now(context) -> list:
    now = time.time()
    return [item for item in context.user_data.get(HELD_KEY) or () if now - item.get("at", 0) < HOLD_SECONDS]


async def _hold_item(context, lang: str, item: dict) -> None:
    waiting = _held_now(context)
    if len(waiting) >= HOLD_MAX:
        await _reply_to_item(context, item, i18n.t(lang, "held_full", n=HOLD_MAX))
        return
    held = waiting + [item]
    context.user_data[HELD_KEY] = held
    packs = await asyncio.to_thread(get_user_packs, item["chat"])
    if not waiting:
        packs = await _current_titles(context, packs[:PACK_BUTTONS_MAX])
    rows = [[InlineKeyboardButton(PACK_BUTTON + label, callback_data=f"hp:{pack_ref(name)}")]
            for label, name in _pack_labels(packs)[:PACK_BUTTONS_MAX]]
    rows.append([InlineKeyboardButton(i18n.t(lang, "btn_new_pack"), callback_data="hp+"),
                 InlineKeyboardButton(i18n.t(lang, "btn_held_discard"), callback_data="hp-")])
    text = i18n.t(lang, "held_offer", n=len(held), minutes=HOLD_SECONDS // 60)
    offer = LiveMessage.restore(context.user_data.get(HELD_OFFER_KEY)) if waiting else None
    if offer is not None:
        try:
            await context.bot.edit_message_text(chat_id=offer.chat_id, message_id=offer.message_id, text=text,
                                                reply_markup=InlineKeyboardMarkup(rows))
            return
        except BadRequest as exc:
            if "not modified" in str(exc).lower():
                return
            logger.debug("Couldn't update the held-items question; asking again", exc_info=True)
        except Exception:
            logger.debug("Couldn't update the held-items question; asking again", exc_info=True)
    sent = await _reply_to_item(context, item, text, reply_markup=InlineKeyboardMarkup(rows))
    context.user_data[HELD_OFFER_KEY] = LiveMessage.adopt(sent).save()


async def media_without_pack(update: Update, context: ContextTypes.DEFAULT_TYPE):
    item = _item_of(update.message)
    if item is None:
        return
    lang = await i18n.get_lang(update.effective_user.id, context)
    await _hold_item(context, lang, item)


async def held_pack_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = update.effective_user
    lang = await i18n.get_lang(user.id, context)
    pack_name = await _pack_of(user.id, query.data.split(":", 1)[1])
    access = await asyncio.to_thread(pack_access, pack_name, user.id) if pack_name else None
    if access is None or not access["allowed"]:
        await query.answer(i18n.t(lang, "not_your_pack"), show_alert=True)
        return None
    await query.answer()
    title = await asyncio.to_thread(get_pack_title, pack_name) or pack_name
    return await _open_pack(update, context, lang, pack_name, access["owner_id"], title,
                            adopt=LiveMessage.adopt(query.message).save())


async def held_new_pack(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await newpack_start(update, context)


async def held_discard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    lang = await i18n.get_lang(update.effective_user.id, context)
    await query.answer()
    context.user_data.pop(HELD_KEY, None)
    context.user_data.pop(HELD_OFFER_KEY, None)
    await edit_in_place(query.message, context.bot, i18n.t(lang, "held_discarded"))


async def _add_held(update: Update, context: ContextTypes.DEFAULT_TYPE, lang: str) -> None:
    held = context.user_data.pop(HELD_KEY, None) or []
    context.user_data.pop(HELD_OFFER_KEY, None)
    now = time.time()
    fresh = [i for i in held if now - i.get("at", 0) < HOLD_SECONDS]
    for item in held:
        if item not in fresh:
            await _reply_to_item(context, item, i18n.t(lang, "held_expired"))
    for item in fresh:
        try:
            await _add_item(update, context, lang, item, held=True)
        except Exception as exc:
            logger.exception("A held item could not be added")
            record_error(exc)
            try:
                await _reply_to_item(context, item, sticker_ops.error_note(exc, lang))
            except Exception:
                logger.debug("Couldn't tell about a held item that failed", exc_info=True)


VIDEO_FIT_KEY = "video_fit"
VIDEO_FIT_KEEP = 10


async def _offer_video_fit(context, lang: str, item: dict, edit, fit: dict,
                           why: str, duration: "float | None") -> bool:
    token = secrets.token_hex(4)
    rows = []
    note_stat("video_fit_offered", why)
    if why == "long":
        speeds = video_sticker.speed_choices(duration)
        seconds = f"{duration:.1f}"
        if speeds:
            text = i18n.t(lang, "video_too_long", seconds=seconds, limit=video_sticker.MAX_DURATION_S)
            for i, speed in enumerate(speeds):
                key = "btn_speed_fit" if i == 0 else "btn_speed"
                rows.append([InlineKeyboardButton(i18n.t(lang, key, speed=f"{speed:g}"),
                                                  callback_data=f"vfit:{token}:s{speed:g}")])
        else:
            text = i18n.t(lang, "video_too_long_no_speed", seconds=seconds,
                          limit=video_sticker.MAX_DURATION_S, speed=f"{video_sticker.MAX_SPEED:g}",
                          longest=f"{video_sticker.MAX_DURATION_S * video_sticker.MAX_SPEED:g}")
    elif fit.get("res") and fit.get("fps"):
        text = i18n.t(lang, "video_too_heavy_last", kb=video_sticker.MAX_BYTES // 1024)
    else:
        text = i18n.t(lang, "video_too_heavy", kb=video_sticker.MAX_BYTES // 1024)
        if not fit.get("res"):
            rows.append([InlineKeyboardButton(i18n.t(lang, "btn_lower_res"), callback_data=f"vfit:{token}:res")])
        if not fit.get("fps"):
            rows.append([InlineKeyboardButton(i18n.t(lang, "btn_lower_fps", fps=video_sticker.LOW_FPS),
                                              callback_data=f"vfit:{token}:fps")])
    if rows:
        rows.append([InlineKeyboardButton(i18n.t(lang, "btn_skip_clip"), callback_data=f"vfit:{token}:x")])
        waiting = context.user_data.setdefault(VIDEO_FIT_KEY, {})
        waiting[token] = {"item": dict(item), "fit": dict(fit), "duration": duration}
        for old in list(waiting)[:-VIDEO_FIT_KEEP]:
            waiting.pop(old, None)
    markup = InlineKeyboardMarkup(rows) if rows else None
    if edit is not None:
        await _say(context, item, edit, text, reply_markup=markup)
    else:
        await _reply_to_item(context, item, text, reply_markup=markup)
    return bool(rows)


async def video_fit_stale(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = await i18n.get_lang(update.effective_user.id, context)
    await update.callback_query.answer(i18n.t(lang, "video_fit_expired"), show_alert=True)


async def video_fit_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    lang = await i18n.get_lang(update.effective_user.id, context)
    _, token, choice = query.data.split(":", 2)
    entry = context.user_data.get(VIDEO_FIT_KEY, {}).pop(token, None)
    if entry is None or not isinstance(entry.get("item"), dict):
        await query.answer(i18n.t(lang, "video_fit_expired"), show_alert=True)
        return EDITING
    await query.answer()
    item = entry["item"]
    if choice == "x":
        note_stat("video_fit", "skipped")
        await _say(context, item, query.message, i18n.t(lang, "video_fit_skipped"))
        return EDITING
    fit = dict(entry.get("fit") or {})
    if choice.startswith("s"):
        try:
            fit["speed"] = float(choice[1:])
        except ValueError:
            return EDITING
    elif choice == "res":
        fit["res"] = True
    elif choice == "fps":
        fit["fps"] = True
    refusal = await refuse_new_work(lang, update.effective_user.id, item["chat"])
    if refusal:
        await _say(context, item, query.message, refusal)
        return EDITING
    answer = await _say(context, item, query.message, i18n.t(lang, "converting_video"))
    try:
        raw = await sticker_ops.download(context.bot, item["file_id"])
    except Exception as exc:
        logger.warning("Couldn't fetch a clip again to make a sticker of", exc_info=True)
        record_error(exc)
        await _say(context, item, answer, i18n.t(lang, "video_convert_generic_failed", error=exc))
        return EDITING
    await _make_video_sticker(update, context, lang, raw, item, fit, answer)
    return EDITING


def _fit_label(fit: "dict | None", lang: str) -> str:
    fit = fit or {}
    parts = []
    if fit.get("speed"):
        parts.append(f"{fit['speed']:g}×")
    if fit.get("res"):
        parts.append(i18n.t(lang, "fit_lower_res"))
    if fit.get("fps"):
        parts.append(i18n.t(lang, "fit_lower_fps", fps=video_sticker.LOW_FPS))
    return ", ".join(parts)


async def reject_unsupported_sticker(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = await i18n.get_lang(update.effective_user.id, context)
    await update.message.reply_text(i18n.t(lang, "animated_not_supported"))
    return EDITING


async def import_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = await i18n.get_lang(update.effective_user.id, context)
    if not context.args:
        await update.message.reply_text(i18n.t(lang, "import_usage"))
        return EDITING

    source = " ".join(context.args)
    pack_source = parse_telegram_pack_source(source)
    if not pack_source:
        await update.message.reply_text(i18n.t(lang, "import_invalid_source"))
        return EDITING

    refusal = await refuse_new_work(lang, update.effective_user.id, update.effective_chat.id)
    if refusal:
        await update.message.reply_text(refusal)
        return EDITING

    await _refresh_status(context, lang, i18n.t(lang, "import_fetching", source=pack_source))
    try:
        stickers = await fetch_importable_stickers(context.bot, pack_source, lang)
    except PackImportError as exc:
        await _refresh_status(context, lang, str(exc) + problems.code_line("ST-IMPORT"))
        return EDITING

    added, skipped, failed = 0, 0, 0
    async with lifecycle.busy(update.effective_chat.id, i18n.t(lang, "restarting_send_again")):
        for sticker in stickers[:MAX_IMPORT_PER_RUN]:
            input_sticker = sticker_to_input_sticker(sticker)
            if input_sticker is None:
                skipped += 1
                continue
            try:
                await _add_input_sticker(context, update.effective_user, input_sticker, want_file_id=False)
                context.user_data["sticker_count"] = context.user_data.get("sticker_count", 0) + 1
                added += 1
                await asyncio.sleep(0.3)
            except NoLongerEditor:
                break
            except Exception:
                logger.exception("Import: failed to add one sticker")
                failed += 1

    await _remember_last_sticker(context)
    summary = i18n.t(lang, "import_summary_head", added=added, source=pack_source)
    if skipped:
        summary += i18n.t(lang, "import_summary_skipped", skipped=skipped)
    if failed:
        summary += i18n.t(lang, "import_summary_failed", failed=failed)
    summary += i18n.t(lang, "import_summary_tail")
    await _refresh_status(context, lang, summary)
    return EDITING


async def import_standalone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = await i18n.get_lang(update.effective_user.id, context)
    await update.message.reply_text(i18n.t(lang, "import_standalone_hint"))


async def done_standalone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = await i18n.get_lang(update.effective_user.id, context)
    await update.message.reply_text(i18n.t(lang, "done_standalone_hint"),
                                    **await _with_buttons(context, update.effective_chat, update.effective_user.id, lang))


async def receive_whatsapp_import(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    lang = await i18n.get_lang(update.effective_user.id, context)

    refusal = await refuse_new_work(lang, update.effective_user.id, msg.chat_id)
    if refusal:
        await msg.reply_text(refusal)
        return EDITING

    try:
        raw = await sticker_ops.download(context.bot, msg.document.file_id)
    except Exception as exc:
        logger.warning("Couldn't fetch a WhatsApp sticker pack", exc_info=True)
        record_error(exc)
        await _refresh_status(context, lang, i18n.t(lang, "video_convert_generic_failed", error=exc))
        return EDITING

    await _refresh_status(context, lang, i18n.t(lang, "whatsapp_reading"))
    try:
        items = await asyncio.to_thread(parse_whatsapp_zip, raw, lang)
    except PackImportError as exc:
        await _refresh_status(context, lang, str(exc) + problems.code_line("ST-IMPORT"))
        return EDITING

    added, failed = 0, 0
    async with lifecycle.busy(msg.chat_id, i18n.t(lang, "restarting_send_again")):
        for png_bytes, emojis in items:
            input_sticker = InputSticker(sticker=png_bytes, emoji_list=emojis[:20], format="static")
            try:
                await _add_input_sticker(context, update.effective_user, input_sticker, want_file_id=False)
                context.user_data["sticker_count"] = context.user_data.get("sticker_count", 0) + 1
                added += 1
                await asyncio.sleep(0.3)
            except NoLongerEditor:
                break
            except Exception:
                logger.exception("WhatsApp import: failed to add one sticker")
                failed += 1

    await _remember_last_sticker(context)
    summary = i18n.t(lang, "whatsapp_summary_head", added=added)
    if failed:
        summary += i18n.t(lang, "import_summary_failed", failed=failed)
    summary += i18n.t(lang, "import_summary_tail")
    await _refresh_status(context, lang, summary)
    return EDITING


async def maybe_emoji_override(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    lang = await i18n.get_lang(update.effective_user.id, context)

    if not looks_like_emoji_message(text):
        return EDITING
    buttons = await _with_buttons(context, update.effective_chat, update.effective_user.id, lang)
    if update.message.reply_to_message is not None:
        await update.message.reply_text(i18n.t(lang, "emoji_target_unknown"), **buttons)
        return EDITING

    last_sticker_id = context.user_data.get("last_sticker_id")
    if not last_sticker_id:
        await update.message.reply_text(i18n.t(lang, "no_sticker_to_tag"), **buttons)
        return EDITING

    emojis = split_emoji(text)
    try:
        await context.bot.set_sticker_emoji_list(sticker=last_sticker_id, emoji_list=emojis)
        await update.message.reply_text(i18n.t(lang, "retagged_success", emojis=" ".join(emojis)), **buttons)
    except Exception as exc:
        await update.message.reply_text(i18n.t(lang, "retag_failed", error=exc))

    return EDITING


class _EmojiReply(filters.MessageFilter):
    def filter(self, message) -> bool:
        text = message.text or ""
        return (message.reply_to_message is not None and not text.startswith("/")
                and looks_like_emoji_message(text)
                and sticker_ops.sticker_of(message.reply_to_message) is not None)


async def emoji_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await group_mode.change_emoji(update, context, update.message.reply_to_message, update.message.text)


async def emoji_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    lang = await i18n.get_lang(update.effective_user.id, context)
    text = " ".join(context.args or [])
    if (not text and message.reply_to_message is not None
            and await group_mode.ask_for_emoji(update, context, message.reply_to_message)):
        return
    if not looks_like_emoji_message(text):
        await message.reply_text(i18n.t(lang, "emoji_usage"))
        return
    if message.reply_to_message is not None:
        await group_mode.change_emoji(update, context, message.reply_to_message, text)
        return
    last_sticker_id = context.user_data.get("last_sticker_id")
    if not last_sticker_id:
        await message.reply_text(i18n.t(lang, "emoji_usage"))
        return
    emojis = split_emoji(text)
    try:
        await context.bot.set_sticker_emoji_list(sticker=last_sticker_id, emoji_list=emojis)
        await message.reply_text(i18n.t(lang, "retagged_success", emojis=" ".join(emojis)))
    except Exception as exc:
        await message.reply_text(i18n.t(lang, "retag_failed", error=exc))


async def done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = await i18n.get_lang(update.effective_user.id, context)
    pack_name = context.user_data.get("target_pack")
    if not pack_name:
        await update.message.reply_text(i18n.t(lang, "nothing_added_yet"))
        return EDITING
    count = context.user_data.get("sticker_count", 0)
    title = context.user_data.get("title") or pack_name
    await _end_status(
        context,
        i18n.t(lang, "done_success", title=title, count=count, pack_name=pack_name),
    )

    _fresh_session(context)
    return ConversationHandler.END


async def convert_redirect_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = await i18n.get_lang(update.effective_user.id, context)
    kb = InlineKeyboardMarkup([sibling_bots_keyboard_row(BOT_NAME, only="convertbot")])
    await update.message.reply_text(
        i18n.t(lang, "convert_redirect"),
        reply_markup=kb,
    )


PACK_CANCEL_KEYS = ("rename", "editing", "newpack", "invite")
OVER_PACK_KEYS = {"rename": ("rename_pack", "rename_prompt"), "invite": ("invite_pack", "invite_pack_id")}


async def _cancel_items(update: Update, context: ContextTypes.DEFAULT_TYPE, lang: str) -> list[CancelItem]:
    items = cancel_items(context, lang)
    if context.user_data.get("invite_pack"):
        pack = context.user_data["invite_pack"]
        title = await asyncio.to_thread(get_pack_title, pack) or pack
        items.append(CancelItem("invite",
                                i18n.t(lang, "cancel_item_invite", title=title),
                                i18n.t(lang, "cancel_button_invite")))
    elif context.user_data.get("rename_pack"):
        pack = context.user_data["rename_pack"]
        title = await asyncio.to_thread(get_pack_title, pack) or pack
        items.append(CancelItem("rename",
                                i18n.t(lang, "cancel_item_rename", title=title),
                                i18n.t(lang, "cancel_button_rename")))
    elif _has_status(context):
        title = context.user_data.get("title") or i18n.t(lang, "status_default_title")
        items.append(CancelItem("editing",
                                i18n.t(lang, "cancel_item_editing", title=title),
                                i18n.t(lang, "cancel_button_editing")))
    elif context.user_data.get("mode"):
        items.append(CancelItem("newpack",
                                i18n.t(lang, "cancel_item_new_pack"),
                                i18n.t(lang, "cancel_button_new_pack")))
    return items


async def _apply_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE, lang: str, keys):
    items = {item.key: item for item in await _cancel_items(update, context, lang)}
    stopped, pack_stopped = [], False
    for chosen in keys:
        item = items.get(chosen)
        if item is None:
            continue
        if chosen in OVER_PACK_KEYS and _has_status(context):
            for key in OVER_PACK_KEYS[chosen]:
                context.user_data.pop(key, None)
            stopped.append(item.label)
            continue
        if chosen == "editing":
            for key in (STATUS_KEY, "sticker_count", "status_intro", *BATCH_KEYS):
                context.user_data.pop(key, None)
        if chosen in PACK_CANCEL_KEYS:
            pack_stopped = True
            stopped.append(item.label)
        elif cancel_shared_item(context, lang, chosen):
            stopped.append(item.label)

    if pack_stopped:
        keep = {key: context.user_data[key] for key in KEEP_ACROSS_SESSIONS if key in context.user_data}
        keep["lang"] = lang
        if "donate_custom_currency" in context.user_data:
            keep["donate_custom_currency"] = context.user_data["donate_custom_currency"]
        reset_user_state(context, keep)
    return stopped, pack_stopped


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = await i18n.get_lang(update.effective_user.id, context)
    items = await _cancel_items(update, context, lang)
    if await ask_cancel_choice(update, context, items, lang):
        return None
    buttons = await _with_buttons(context, update.effective_chat, update.effective_user.id, lang)
    await finish_cancel(update, context, lang, [], keyboard=buttons.get("reply_markup"))
    return ConversationHandler.END


async def cancel_choice_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    lang = await i18n.get_lang(update.effective_user.id, context)
    key = cancel_choice_key(update)
    await query.answer()
    if key == CANCEL_PICK_NONE:
        await keep_going(update, context, lang)
        return None

    if key == CANCEL_PICK_ALL:
        keys = [item.key for item in await _cancel_items(update, context, lang)]
    else:
        keys = [key]
    stopped, pack_stopped = await _apply_cancel(update, context, lang, keys)
    buttons = await _with_buttons(context, update.effective_chat, update.effective_user.id, lang)
    await finish_cancel_choice(update, context, lang, stopped, keyboard=buttons.get("reply_markup"))
    if pack_stopped:
        return ConversationHandler.END
    if stopped and _has_status(context) and any(key in OVER_PACK_KEYS for key in keys):
        return EDITING
    return None


async def unrecognized_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = await i18n.get_lang(update.effective_user.id, context)
    buttons = await _with_buttons(context, update.effective_chat, update.effective_user.id, lang)
    if looks_like_emoji_message(update.message.text or ""):
        key = "emoji_target_unknown" if update.message.reply_to_message is not None else "no_sticker_to_tag"
        await update.message.reply_text(i18n.t(lang, key), **buttons)
        return
    await update.message.reply_text(i18n.t(lang, "unrecognized"), **buttons)


async def unknown_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = await i18n.get_lang(update.effective_user.id, context)
    await update.message.reply_text(i18n.t(lang, "unknown_command"))


async def _post_init(application):
    await tune_runtime(application)
    await lifecycle.on_start(BOT_NAME)
    await lifecycle.optional("command menu", publish_commands(application, BOT_COMMANDS, ADMIN_COMMANDS, ADMIN_IDS,
                                                               group=group_mode.GROUP_COMMANDS))
    await lifecycle.optional("profile text", publish_profile(application))


async def _post_stop(application):
    await lifecycle.on_stop(application)
    await flush_on_shutdown(application)


def main():
    if not BOT_TOKEN or not BOT_USERNAME:
        raise SystemExit("Set SBOT_TOKEN and SBOT_USERNAME environment variables first.")

    init_db()

    builder = (
        ApplicationBuilder().token(BOT_TOKEN)
        .post_init(_post_init).post_stop(_post_stop)
    )
    builder = shared_features.per_user_updates(builder, "SBOT")
    state = lifecycle.persistence()
    if state is not None:
        builder = builder.persistence(state)
    if LOCAL_BOT_API_URL:
        base = LOCAL_BOT_API_URL.rstrip("/")
        builder = (
            builder
            .base_url(f"{base}/bot")
            .base_file_url(f"{base}/file/bot")
            .local_mode(LOCAL_BOT_API_MODE)
        )
        logger.info(
            "Using local Bot API server at %s (local_mode=%s) -- 20 MB download / 50 MB "
            "upload ceilings are lifted.", base, LOCAL_BOT_API_MODE,
        )
    app = builder.build()
    lifecycle.install(app, BOT_NAME, ADMIN_IDS)
    app.add_error_handler(error_handler)
    attach_problem_reports(app)
    app.add_handler(TypeHandler(Update, track_activity), group=-4)
    attach_flood_gate(app, ADMIN_IDS)
    app.add_handler(MessageHandler(filters.ChatType.GROUPS, group_mode.group_router), group=-1)
    app.add_handler(MessageHandler((filters.TEXT | filters.REPLY) & ~filters.COMMAND,
                                   awaited_text_received), group=-1)
    app.add_handler(ChatMemberHandler(group_mode.bot_membership_changed, ChatMemberHandler.MY_CHAT_MEMBER))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & _EmojiReply(), emoji_reply))

    static_media = filters.PHOTO | filters.Document.IMAGE | filters.Sticker.STATIC
    video_media = filters.ANIMATION | filters.VIDEO | filters.Document.VIDEO | filters.Sticker.VIDEO
    behind_question = [MessageHandler(static_media, _behind_question(receive_media)),
                       MessageHandler(video_media, _behind_question(receive_video_media))]
    conv = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            CommandHandler("newpack", newpack_start),
            MessageHandler(filters.ChatType.PRIVATE & PACK_BUTTON_TEXT, pack_button),
            MessageHandler(filters.ChatType.PRIVATE & filters.Text(_new_pack_labels()), newpack_start),
            CallbackQueryHandler(held_pack_chosen, pattern="^hp:"),
            CallbackQueryHandler(held_new_pack, pattern=r"^hp\+$"),
            CallbackQueryHandler(newpack_start, pattern="^menu_newpack$"),
            CallbackQueryHandler(pack_chosen, pattern="^pack:"),
            CallbackQueryHandler(rename_start, pattern="^packrename:"),
            CallbackQueryHandler(invite_start, pattern="^co:inv:"),
            CallbackQueryHandler(language_chosen, pattern="^setlang:"),
            CallbackQueryHandler(cancel_choice_callback, pattern="^cancelpick:"),
            CallbackQueryHandler(already_in_pack_chosen, pattern="^" + group_mode.DUP_PREFIX),
            CommandHandler("en", set_language_en),
            CommandHandler("uz", set_language_uz),
            CommandHandler("rus", set_language_rus),
            CommandHandler("deletemydata", delete_my_data_command),
            CallbackQueryHandler(delete_my_data_chosen, pattern="^" + ERASE_PREFIX),
        ],
        states={
            TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, _needs_session(receive_title))],
            RENAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_new_title), *behind_question],
            INVITE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_invites), *behind_question],
            EDITING: [
                CallbackQueryHandler(_needs_session(confirm_remove_last_sticker), pattern="^confirmremove:"),
                CallbackQueryHandler(_needs_session(video_fit_chosen), pattern="^vfit:"),
                MessageHandler(
                    filters.Document.FileExtension("zip") | filters.Document.FileExtension("wastickers"),
                    _needs_session(receive_whatsapp_import),
                ),
                MessageHandler(static_media, _needs_session(receive_media)),
                MessageHandler(video_media, _needs_session(receive_video_media)),
                MessageHandler(
                    filters.Sticker.ANIMATED,
                    _needs_session(reject_unsupported_sticker),
                ),
                CommandHandler("import", _needs_session(import_command)),
                CommandHandler("done", _needs_session(done)),
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    _needs_session(maybe_emoji_override),
                ),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel_command), CommandHandler("start", start)],
        name="stickerbot_pack_editing",
        persistent=lifecycle.persistent(),
        allow_reentry=True,
    )

    app.add_handler(conv)
    app.add_handler(CallbackQueryHandler(video_fit_stale, pattern="^vfit:"))
    app.add_handler(CallbackQueryHandler(held_discard, pattern="^hp-$"))
    app.add_handler(CallbackQueryHandler(language_chosen, pattern="^setlang:"))
    app.add_handler(CallbackQueryHandler(cancel_choice_callback, pattern="^cancelpick:"))
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
    app.add_handler(CommandHandler("mypacks", mypacks_command))
    app.add_handler(CommandHandler("addsticker", addsticker_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("fullhelp", full_help_command(build_help_text, help_rows, HELP_PAGES, HELP_LABELS)))
    app.add_handler(CallbackQueryHandler(help_toggle(build_help_text, help_rows, HELP_PAGES, HELP_LABELS),
                                         pattern=HELP_TOGGLE_PATTERN))
    app.add_handler(CallbackQueryHandler(help_page_button(HELP_PAGES), pattern=HELP_PAGE_PATTERN))
    app.add_handler(CommandHandler("feedback", feedback_command))
    app.add_handler(CommandHandler("whomade", whomade_command))
    app.add_handler(CommandHandler("emoji", emoji_command))
    app.add_handler(CommandHandler("whois", whois_command))
    app.add_handler(CommandHandler("messageas", messageas_command))
    app.add_handler(CommandHandler("dbdump", dbdump_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("crashtest", crashtest_command))
    app.add_handler(CommandHandler("import", import_standalone))
    app.add_handler(CommandHandler("done", done_standalone))
    app.add_handler(CommandHandler("cancel", cancel_command))
    app.add_handler(CallbackQueryHandler(menu_mypacks, pattern="^menu_mypacks$"))
    app.add_handler(CallbackQueryHandler(menu_help, pattern="^menu_help$"))
    app.add_handler(CallbackQueryHandler(menu_start, pattern="^menu_start$"))
    app.add_handler(CallbackQueryHandler(pack_detail, pattern="^packopen:"))
    app.add_handler(CallbackQueryHandler(pack_public_toggle, pattern="^packpub:"))
    app.add_handler(CallbackQueryHandler(coedit_menu, pattern="^packcoedit:"))
    app.add_handler(CallbackQueryHandler(coedit_reset, pattern="^cotoken_reset:"))
    app.add_handler(CallbackQueryHandler(coedit_button, pattern="^co:(?!inv:)"))
    app.add_handler(CallbackQueryHandler(already_in_pack_chosen, pattern="^" + group_mode.DUP_PREFIX))
    app.add_handler(CallbackQueryHandler(delete_pack_start, pattern="^packdel:"))
    app.add_handler(CallbackQueryHandler(delete_pack_confirm1, pattern="^packdelconfirm1:"))
    app.add_handler(CallbackQueryHandler(delete_pack_confirm2, pattern="^packdelconfirm2:"))
    app.add_handler(CommandHandler("convert", convert_redirect_command))

    app.add_handler(CommandHandler("balance", balance_command))
    app.add_handler(CommandHandler("donate", donate_command))
    app.add_handler(CallbackQueryHandler(donate_amount_chosen, pattern="^donate:"))
    app.add_handler(CallbackQueryHandler(donate_fiat_amount_chosen, pattern="^donatefiat:"))
    app.add_handler(CallbackQueryHandler(donate_custom_button_chosen, pattern="^donatecustom:"))
    app.add_handler(PreCheckoutQueryHandler(donation_precheckout_callback))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, donation_payment_callback))

    app.add_handler(MessageHandler(filters.COMMAND, unknown_command))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & BATCH_MEDIA, media_without_pack))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, unrecognized_message))

    family_link.attach(app, BOT_NAME, "StickerBot", START_TIME)
    attach_maintenance(app)

    logger.info("Bot starting (polling)...")
    app.run_polling(**lifecycle.polling_kwargs(
        timeout=POLL_TIMEOUT,
        allowed_updates=[Update.MESSAGE, Update.CALLBACK_QUERY, Update.PRE_CHECKOUT_QUERY,
                         Update.MY_CHAT_MEMBER],
    ))


if __name__ == "__main__":
    main()
