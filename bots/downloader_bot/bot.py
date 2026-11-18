import asyncio
import contextlib
import contextvars
import hashlib
import html
import logging
import os
import shutil
import uuid
import time
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timedelta, timezone
from io import BytesIO

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from telegram import (
    BotCommand, InputMediaDocument, InputMediaPhoto, InputMediaVideo,
    LinkPreviewOptions, MessageEntity, Update, InlineKeyboardButton, InlineKeyboardMarkup,
)
from telegram.constants import ParseMode
from telegram.error import BadRequest, Forbidden

NO_PREVIEW = {"link_preview_options": LinkPreviewOptions(is_disabled=True)}
from telegram.ext import (
    ApplicationBuilder,
    ApplicationHandlerStop,
    ChatMemberHandler,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    PreCheckoutQueryHandler,
    ContextTypes,
    TypeHandler,
    filters,
)

import cards
import channels
import db
import delivery_cache
import family_link
import groups
import i18n
import problems
import lifecycle
import net
import platforms
import resolvers
import routewatch
import sent_files
import shared_features
import big_files
import large_files
import merge
import resources
import video
from live_message import LiveMessage, edit_in_place
from db import (
    init_db,
    get_caption_enabled,
    set_caption_enabled,
    get_lossless_enabled,
    set_lossless_enabled,
    get_large_files_enabled,
    set_large_files_enabled,
    get_user_language,
    set_user_language,
    dump_database_csv_zip,
    count_active_users_since,
    load_provider_health,
    save_provider_health,
    download_allowance,
    record_download,
    sent_files_get,
    sent_files_put,
    sent_files_confirmed,
    sent_files_used,
    sent_files_drop,
    get_group_settings,
    set_group_settings,
    forget_group,
    move_group_settings,
    channel_ids,
)
from shared_features import (
    note_active,
    note_job,
    note_stat,
    note_tap,
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
    refuse_new_work,
    attach_flood_gate,
    attach_promo_claim,
    attach_maintenance,
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
    track_activity,
    build_status_text,
)

setup_logging(__file__)
logger = logging.getLogger(__name__)

START_TIME = datetime.now(timezone.utc)

BOT_TOKEN = os.environ.get("DBOT_TOKEN")
BOT_USERNAME = (os.environ.get("DBOT_USERNAME") or "").strip().lstrip("@") or None


BOT_NAME = "downloaderbot"

sent_files.configure(BOT_TOKEN)

ADMIN_IDS = {int(x) for x in os.environ.get("DBOT_ADMIN_ID", "").split(",") if x.strip()}


def _is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


async def _deny(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await unknown_command(update, context)


POLL_TIMEOUT = int(os.environ.get("POLL_TIMEOUT", "30"))


def downloads_at_once(job_memory_mb: "int | None", cpus: float) -> int:
    if job_memory_mb is None:
        return 3
    return max(3, min(8, job_memory_mb // 400, int(cpus * 2)))


def _max_concurrent_downloads() -> int:
    fixed = os.environ.get("DBOT_MAX_CONCURRENT_DOWNLOADS")
    if fixed:
        return max(1, int(fixed))
    return downloads_at_once(resources.job_memory_mb("downloader_bot"), resources.cpu_limit())


MAX_CONCURRENT_DOWNLOADS = _max_concurrent_downloads()


def log_capacity() -> None:
    job_memory = resources.job_memory_mb("downloader_bot")
    logger.info("DownloaderBot runs up to %d downloads at once and %d album PDF(s) (%s MB for its work).",
                MAX_CONCURRENT_DOWNLOADS, merge.pdfs_at_once(), "unknown" if job_memory is None else job_memory)


_download_slots: asyncio.Semaphore | None = None


def _slots() -> asyncio.Semaphore:
    global _download_slots
    if _download_slots is None:
        _download_slots = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)
    return _download_slots


MAX_CONCURRENT_LOOKUPS = 2

_lookup_slots: asyncio.Semaphore | None = None


def _lookups() -> asyncio.Semaphore:
    global _lookup_slots
    if _lookup_slots is None:
        _lookup_slots = asyncio.Semaphore(MAX_CONCURRENT_LOOKUPS)
    return _lookup_slots


MAX_PER_USER = max(1, int(os.environ.get("DBOT_MAX_PER_USER") or 2))

MAX_PENDING_PER_USER = max(1, int(os.environ.get("DBOT_MAX_PENDING_PER_USER") or 5))

MIN_FREE_MB = int(os.environ.get("DBOT_MIN_FREE_MB") or 300)

LARGE_ROOM_MB = int(os.environ.get("DBOT_LARGE_ROOM_MB") or 1024)

delivery_cache.configure(resolvers._WORK_DIR)


def _cache_floor_mb() -> int:
    return MIN_FREE_MB + MAX_CONCURRENT_DOWNLOADS * video.MAX_DOWNLOAD_MB


def _make_room(mb: int) -> None:
    delivery_cache.make_room(mb, MIN_FREE_MB)


def _disk_room(mb: int) -> bool:
    free = _free_mb()
    if free is None:
        return True
    return free - resources.reserved_disk_bytes() // (1024 * 1024) - mb >= MIN_FREE_MB


def _hold_disk(mb: int):
    return resources.reserve_disk("downloader_bot", mb * 1024 * 1024)


MERGE_MAX_ITEMS = int(os.environ.get("DBOT_MERGE_MAX_ITEMS") or 20)
MERGE_MAX_MB = int(os.environ.get("DBOT_MERGE_MAX_MB") or 48)

_user_slots: "dict[int, asyncio.Semaphore]" = {}
_user_pending: "dict[int, int]" = {}

_in_flight: "dict[tuple[int, int, str, str], float]" = {}
IN_FLIGHT_MAX_SECONDS = 3600


def _already_in_flight(key) -> bool:
    marked = _in_flight.get(key)
    return marked is not None and time.monotonic() - marked < IN_FLIGHT_MAX_SECONDS


def _clear_in_flight(key, stamp) -> None:
    if _in_flight.get(key) == stamp:
        _in_flight.pop(key, None)


async def _answer_duplicate(update: Update) -> None:
    try:
        await update.effective_message.set_reaction("👌")
    except Exception as exc:
        logger.debug("Couldn't react to a repeated link (%s)", type(exc).__name__)


@asynccontextmanager
async def _user_slot(user_id: int):
    slot = _user_slots.get(user_id)
    if slot is None:
        slot = _user_slots[user_id] = asyncio.Semaphore(MAX_PER_USER)
    try:
        async with slot:
            yield
    finally:
        if slot._value >= MAX_PER_USER and not slot._waiters:  # noqa: SLF001 - no public API
            _user_slots.pop(user_id, None)


def _user_slots_full(user_id: int) -> bool:
    slot = _user_slots.get(user_id)
    return slot is not None and slot.locked()


def _free_mb() -> "int | None":
    try:
        return shutil.disk_usage(resolvers._WORK_DIR).free // (1024 * 1024)  # noqa: SLF001
    except OSError:
        return None


def _spawn_download(context, update, work, in_flight=None) -> None:
    user_id = update.effective_user.id
    _user_pending[user_id] = _user_pending.get(user_id, 0) + 1
    stamp = _in_flight.get(in_flight) if in_flight is not None else None

    async def run():
        try:
            await work()
        finally:
            left = _user_pending.get(user_id, 1) - 1
            if left > 0:
                _user_pending[user_id] = left
            else:
                _user_pending.pop(user_id, None)
            if in_flight is not None:
                _clear_in_flight(in_flight, stamp)

    context.application.create_task(run(), update=update)


def build_help_text(lang: str, full: bool = False) -> str:
    if not full:
        text = i18n.t(lang, "help_brief").rstrip("\n")
    else:
        text = i18n.t(lang, "help_text", username=BOT_USERNAME or "this_bot").rstrip("\n")
    blurb = sibling_bots_blurb(BOT_NAME, lang)
    return text + "\n\n" + blurb if blurb else text


def _help_large_prices(lang: str, key: str) -> str:
    return i18n.t(lang, key, free_mb=large_files.FREE_MAX_MB, max=large_files.LARGE_MAX_MB,
                  first=large_files.first_price(), top=large_files.top_price())


def _help_page_download(lang: str) -> str:
    if db.DOWNLOADS_PER_DAY > 0 and db.DOWNLOADS_PER_HOUR > 0:
        quota = i18n.t(lang, "helppage_download_quota",
                       per_day=db.DOWNLOADS_PER_DAY, per_hour=db.DOWNLOADS_PER_HOUR,
                       donor_day=int(db.DOWNLOADS_PER_DAY * db.DONOR_MULTIPLIER),
                       donor_hour=int(db.DOWNLOADS_PER_HOUR * db.DONOR_MULTIPLIER))
    else:
        quota = i18n.t(lang, "helppage_download_quota_off")
    if large_files.CONFIGURED:
        large = i18n.t(lang, "helppage_download_large", max=large_files.LARGE_MAX_MB,
                       first=large_files.first_price(), top=large_files.top_price())
    else:
        large = i18n.t(lang, "helppage_download_large_off")
    return i18n.t(lang, "helppage_download", quota=quota, at_once=MAX_PER_USER, waiting=MAX_PENDING_PER_USER,
                  free_mb=large_files.FREE_MAX_MB, large=large,
                  merge_items=MERGE_MAX_ITEMS, merge_mb=MERGE_MAX_MB)


def _help_page_settings(lang: str) -> str:
    large = _help_large_prices(lang, "helppage_settings_large") + "\n" if large_files.CONFIGURED else ""
    return i18n.t(lang, "helppage_settings", username=BOT_USERNAME or "this_bot", large=large)


HELP_PAGES = {"download": _help_page_download, "settings": _help_page_settings}
HELP_ALIASES = {}
HELP_LABELS = {"download": "help_btn_download"}


BOT_COMMANDS = [
    BotCommand("start", "What this bot does, and how to start"),
    BotCommand("language", "Choose the language / Tilni tanlash / Выбрать язык"),
    BotCommand("settings", "Captions and file quality"),
    BotCommand("channels", "Channels: the video in each post with a link"),
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
    BotCommand("providers", "🔒 Which download route works, which is resting"),
    BotCommand("messageas", "🔒 messageas <user_id> <text> — DM as this bot"),
    BotCommand("dbdump", "🔒 This bot's tables as a zip of CSVs"),
]


async def _reply(update: Update, text: str, **kwargs):
    if update.message:
        return await LiveMessage.reply_to(update.message, text, **kwargs)
    return await edit_in_place(update.callback_query.message, update.get_bot(), text, **kwargs)


async def _continue_start(update: Update, context: ContextTypes.DEFAULT_TYPE, lang: str):
    await _reply(
        update,
        i18n.t(lang, "start_greeting") + build_help_text(lang),
        reply_markup=help_keyboard(lang, False, [sibling_bots_keyboard_row(BOT_NAME)], HELP_PAGES, HELP_LABELS),
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = await asyncio.to_thread(get_user_language, update.effective_user.id)
    if lang is None:
        await update.message.reply_text(i18n.LANGUAGE_PROMPT, reply_markup=language_keyboard())
        return
    context.user_data["lang"] = lang
    await _continue_start(update, context, lang)


async def language_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = await asyncio.to_thread(get_user_language, update.effective_user.id)
    if lang:
        context.user_data["lang"] = lang
    await update.message.reply_text(i18n.LANGUAGE_PROMPT, reply_markup=language_keyboard(lang))


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = await i18n.get_lang(update.effective_user.id, context)
    page = help_page_name(context, HELP_PAGES, HELP_ALIASES)
    if page:
        await update.message.reply_text(help_page_text(lang, HELP_PAGES, page),
                                        link_preview_options=shared_features.NO_PREVIEW)
        return
    full = wants_full_help(context)
    await update.message.reply_text(build_help_text(lang, full=full),
                                    reply_markup=help_keyboard(lang, full, (), HELP_PAGES, HELP_LABELS))


async def _cancel_items(update: Update, context: ContextTypes.DEFAULT_TYPE, lang: str) -> list[CancelItem]:
    return cancel_items(context, lang)


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = await i18n.get_lang(update.effective_user.id, context)
    if await ask_cancel_choice(update, context, await _cancel_items(update, context, lang), lang):
        return
    await finish_cancel(update, context, lang, [])


async def cancel_choice_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    lang = await i18n.get_lang(update.effective_user.id, context)
    key = cancel_choice_key(update)
    await query.answer()
    if key == CANCEL_PICK_NONE:
        await keep_going(update, context, lang)
        return
    if key == CANCEL_PICK_ALL:
        keys = [item.key for item in await _cancel_items(update, context, lang)]
    else:
        keys = [key]
    stopped = [label for label in (cancel_shared_item(context, lang, k) for k in keys) if label]
    await finish_cancel_choice(update, context, lang, stopped)


async def _apply_language(update: Update, context: ContextTypes.DEFAULT_TYPE, lang: str):
    await asyncio.to_thread(set_user_language, update.effective_user.id, lang)
    context.user_data["lang"] = lang
    await refresh_chat_menu(context, update.effective_user.id, lang)
    await _continue_start(update, context, lang)


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


def _caption_keyboard(lang: str, enabled: bool) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(("✅ " if enabled else "") + i18n.t(lang, "caption_state_on"), callback_data="caption:on"),
                InlineKeyboardButton(("✅ " if not enabled else "") + i18n.t(lang, "caption_state_off"), callback_data="caption:off"),
            ]
        ]
    )


async def caption_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = await i18n.get_lang(update.effective_user.id, context)
    if not context.args or context.args[0].lower() not in ("on", "off"):
        current = await asyncio.to_thread(get_caption_enabled, update.effective_user.id)
        state = i18n.t(lang, "caption_state_on" if current else "caption_state_off")
        await update.message.reply_text(
            i18n.t(lang, "caption_status", state=state),
            reply_markup=_caption_keyboard(lang, current),
        )
        return
    enabled = context.args[0].lower() == "on"
    await asyncio.to_thread(set_caption_enabled, update.effective_user.id, enabled)
    state = i18n.t(lang, "caption_state_on" if enabled else "caption_state_off")
    await update.message.reply_text(i18n.t(lang, "caption_turned", state=state))


async def caption_toggle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    lang = await i18n.get_lang(update.effective_user.id, context)
    enabled = query.data.split(":", 1)[1] == "on"
    await asyncio.to_thread(set_caption_enabled, update.effective_user.id, enabled)
    state = i18n.t(lang, "caption_state_on" if enabled else "caption_state_off")
    await query.answer(i18n.t(lang, "caption_toggle_answer", state=state))
    await edit_in_place(query.message, context.bot, 
        i18n.t(lang, "caption_status", state=state),
        reply_markup=_caption_keyboard(lang, enabled),
    )


def _lossless_keyboard(lang: str, enabled: bool) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(("✅ " if enabled else "") + i18n.t(lang, "lossless_state_on"), callback_data="lossless:on"),
                InlineKeyboardButton(("✅ " if not enabled else "") + i18n.t(lang, "lossless_state_off"), callback_data="lossless:off"),
            ]
        ]
    )


async def lossless_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = await i18n.get_lang(update.effective_user.id, context)
    if not context.args or context.args[0].lower() not in ("on", "off"):
        current = await asyncio.to_thread(get_lossless_enabled, update.effective_user.id)
        state = i18n.t(lang, "lossless_state_on" if current else "lossless_state_off")
        await update.message.reply_text(
            i18n.t(lang, "lossless_status", state=state),
            reply_markup=_lossless_keyboard(lang, current),
        )
        return
    enabled = context.args[0].lower() == "on"
    await asyncio.to_thread(set_lossless_enabled, update.effective_user.id, enabled)
    state = i18n.t(lang, "lossless_state_on" if enabled else "lossless_state_off")
    await update.message.reply_text(i18n.t(lang, "lossless_turned", state=state))


async def lossless_toggle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    lang = await i18n.get_lang(update.effective_user.id, context)
    enabled = query.data.split(":", 1)[1] == "on"
    await asyncio.to_thread(set_lossless_enabled, update.effective_user.id, enabled)
    state = i18n.t(lang, "lossless_state_on" if enabled else "lossless_state_off")
    await query.answer(i18n.t(lang, "lossless_toggle_answer", state=state))
    await edit_in_place(query.message, context.bot, 
        i18n.t(lang, "lossless_status", state=state),
        reply_markup=_lossless_keyboard(lang, enabled),
    )


def _large_keyboard(lang: str, enabled: bool) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(("✅ " if enabled else "") + i18n.t(lang, "large_state_on"), callback_data="large:on"),
                InlineKeyboardButton(("✅ " if not enabled else "") + i18n.t(lang, "large_state_off"), callback_data="large:off"),
            ]
        ]
    )


def _large_status(lang: str, enabled: bool) -> str:
    state = i18n.t(lang, "large_state_on" if enabled else "large_state_off")
    return i18n.t(lang, "large_status", state=state, free=large_files.FREE_MAX_MB, per=large_files.first_price(),
                  top=large_files.top_price(),
                  max=large_files.LARGE_MAX_MB)


async def large_toggle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    lang = await i18n.get_lang(update.effective_user.id, context)
    choice = query.data.split(":", 1)[1]
    enabled = choice in ("on", "offer")
    await asyncio.to_thread(set_large_files_enabled, update.effective_user.id, enabled)
    state = i18n.t(lang, "large_state_on" if enabled else "large_state_off")
    await query.answer(i18n.t(lang, "large_toggle_answer", state=state))
    if choice == "offer":
        await edit_in_place(query.message, context.bot,
                            i18n.t(lang, "large_files_turned_on", per=large_files.first_price()))
        return
    await edit_in_place(query.message, context.bot, _large_status(lang, enabled),
                        reply_markup=_large_keyboard(lang, enabled))


async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = await i18n.get_lang(update.effective_user.id, context)
    caption_on = await asyncio.to_thread(get_caption_enabled, update.effective_user.id)
    lossless_on = await asyncio.to_thread(get_lossless_enabled, update.effective_user.id)
    caption_state = i18n.t(lang, "caption_state_on" if caption_on else "caption_state_off")
    lossless_state = i18n.t(lang, "lossless_state_on" if lossless_on else "lossless_state_off")
    text = (i18n.t(lang, "settings_heading") + "\n\n"
            + i18n.t(lang, "caption_status", state=caption_state) + "\n\n"
            + i18n.t(lang, "lossless_status", state=lossless_state))
    rows = (_caption_keyboard(lang, caption_on).inline_keyboard
            + _lossless_keyboard(lang, lossless_on).inline_keyboard)
    if large_files.CONFIGURED:
        large_on = await asyncio.to_thread(get_large_files_enabled, update.effective_user.id)
        text += "\n\n" + _large_status(lang, large_on)
        rows += _large_keyboard(lang, large_on).inline_keyboard
    keyboard = InlineKeyboardMarkup(rows)
    await update.message.reply_text(text, reply_markup=keyboard)


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
    await update.message.reply_document(document=BytesIO(data), filename=f"downloaderbot_db_{stamp}.zip")
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
    await update.message.reply_text(
        build_status_text(START_TIME, users_hour, users_since_start) + "\n" + _cache_status_line()
        + "\n" + _sent_files_status_line())


def _cache_status_line() -> str:
    stats = delivery_cache.stats()
    if not stats["enabled"]:
        return "Delivery cache: off"
    return (f"Delivery cache: {stats['entries']} kept ({stats['bytes'] / (1024 * 1024):.1f} MB), "
            f"{stats['hits']} hits / {stats['misses']} misses since start")


def _sent_files_status_line() -> str:
    stats = sent_files.stats()
    if not stats["enabled"]:
        return "Sent-file store: off"
    return (f"Sent-file store: rows read {stats['hit']} fresh / {stats['stale']} stale / {stats['miss']} none "
            f"({stats['remembered']} from memory); "
            f"lookups {stats['present']} present / {stats['missing']} missing / {stats['unknown']} unknown; "
            f"{stats['invalid']} invalid ids, {stats['stored']} stored since start")


PROVIDER_HEALTH_FLUSH_SECONDS = int(
    os.environ.get("DBOT_PROVIDER_FLUSH_SECONDS", "60"))


async def _flush_provider_health(_context=None) -> None:
    dirty = resolvers.take_dirty()
    if not dirty:
        return
    rows = [(name, h.ok, h.failed, h.streak, h.last_ok, h.last_fail, h.last_error)
            for name, h in dirty.items()]
    try:
        await asyncio.to_thread(save_provider_health, rows)
    except Exception:
        logger.warning("couldn't save provider health", exc_info=True)


PROBE_TIMEOUT_S = float(os.environ.get("DBOT_PROBE_TIMEOUT", "12"))


async def _bus_probe(context, args):
    fetch = True
    urls: dict[str, str] = {}
    for arg in args:
        if arg in ("--nofetch", "nofetch"):
            fetch = False
            continue
        if arg in resolvers.SAMPLES:
            urls[arg] = resolvers.SAMPLES[arg]
            continue
        detected = platforms.detect_platform(arg)
        if detected:
            urls[detected[0]] = detected[1]
    results = await resolvers.probe_all(urls or None, fetch=fetch,
                                        timeout=PROBE_TIMEOUT_S)
    header = ("Download routes, asked from inside this container "
              f"({os.environ.get('RAILWAY_ENVIRONMENT') or 'local'}). "
              "Scores untouched.\n")
    return header + resolvers.format_probe(results), None, None


async def _bus_routewatch(context, args):
    return await routewatch.run(force=True, reply_within=routewatch.REPLY_WITHIN_S), None, None


async def providers_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update.effective_user.id):
        return await _deny(update, context)
    await update.message.reply_text(_providers_text(), **NO_PREVIEW)


async def _bus_providers(context, args):
    return _providers_text(), None, None


def _providers_text() -> str:
    now = time.time()
    lines = []
    for platform, chain in resolvers.PROVIDERS.items():
        lines.append(f"\n{platform}:")
        for name, _fn in chain:
            h = resolvers.health(name)
            if h.ok == 0 and h.failed == 0:
                lines.append(f"  \u2022 {name} -- not tried yet")
                continue
            if h.streak == 0 and h.ok:
                mark, note = "\u2705", f"last ok {_ago(now - (h.last_ok or now))} ago"
            elif h.cooling_until > now:
                mark = "\u274c"
                note = (f"{h.streak} in a row, resting "
                        f"{_ago(h.cooling_until - now)}: "
                        f"{resolvers.tidy_error(h.last_error)}")
            else:
                mark = "\u26a0\ufe0f"
                note = f"{h.streak} in a row: {resolvers.tidy_error(h.last_error)}"
            lines.append(f"  {mark} {name} -- {h.ok} ok / {h.failed} failed, {note}")
    return "Download routes, best first per platform:" + "\n".join(lines)


def _ago(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 90:
        return f"{seconds}s"
    if seconds < 5400:
        return f"{seconds // 60}m"
    if seconds < 172800:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


REDELIVER_PREFIX = "redo:"
REDELIVER_MEMORY = 5
MERGE_PREFIX = "merge:"


_EXACT_MARK, _CAP_MARK, _SOUND_MARK = "#q", "#upto", "#sound"


def _with_height(url: str, height: int, exact: bool = True) -> str:
    return f"{url}{_EXACT_MARK if exact else _CAP_MARK}{int(height)}"


def _with_sound(url: str) -> str:
    return url + _SOUND_MARK


def _split_choice(url: str) -> "tuple[str, int | None, bool, bool]":
    sound = url.endswith(_SOUND_MARK)
    if sound:
        url = url[:-len(_SOUND_MARK)]
    for mark, exact in ((_CAP_MARK, False), (_EXACT_MARK, True)):
        base, found, height = url.rpartition(mark)
        if found and height.isdigit():
            return base, int(height), exact, sound
    return url, None, False, sound


def _split_height(url: str) -> "tuple[str, int | None, bool]":
    base, height, exact, _ = _split_choice(url)
    return base, height, exact


_GROUP: contextvars.ContextVar = contextvars.ContextVar("group_settings", default=None)


def _remember_delivery(context, platform: str, url: str, lossless: bool) -> "str | None":
    if _GROUP.get() is not None:
        return None
    memory = context.user_data.setdefault("redeliver", {})
    while len(memory) >= REDELIVER_MEMORY:
        memory.pop(next(iter(memory)))
    token = uuid.uuid4().hex[:10]
    memory[token] = {"platform": platform, "url": url, "lossless": lossless, "at": time.time()}
    return token


REDO_DELAY_BUCKETS = (
    (60, "lt1m"), (120, "1-2m"), (300, "2-5m"), (900, "5-15m"), (3600, "15-60m"),
)


def _redo_delay_key(remembered: dict) -> str:
    at = remembered.get("at")
    if isinstance(at, bool) or not isinstance(at, (int, float)):
        return "redo.after.unknown"
    seconds = time.time() - at
    for limit, name in REDO_DELAY_BUCKETS:
        if seconds < limit:
            return f"redo.after.{name}"
    return "redo.after.gt60m"


def _spend_action(remembered: dict, action: str) -> bool:
    spent = remembered.setdefault("spent", [])
    if not isinstance(spent, list):
        spent = remembered["spent"] = ["redo"]
    if action in spent:
        return False
    spent.append(action)
    return True


def _unspend_action(remembered: dict, action: str) -> None:
    spent = remembered.get("spent", [])
    if isinstance(spent, list) and action in spent:
        spent.remove(action)


def _remembered_kb(lang: str, token: str, remembered: dict):
    return _nudge_kb(lang, token, remembered.get("lossless", False),
                     photos=remembered.get("photos", 0), items=remembered.get("items", 0),
                     spent=remembered.get("spent") if isinstance(remembered.get("spent"), (list, tuple))
                     else ("redo",) if remembered.get("spent") else ())


def _nudge_kb(lang: str | None = None, token: str | None = None,
              lossless: bool = False, photos: int = 0,
              items: int = 0, spent=()) -> InlineKeyboardMarkup | None:
    rows = []
    if lang is not None and token is not None:
        if "redo" not in spent:
            rows.append([InlineKeyboardButton(
                i18n.t(lang, "redeliver_as_compressed" if lossless else "redeliver_as_file"),
                callback_data=f"{REDELIVER_PREFIX}{token}")])
        merge_row = []
        if photos >= merge.MIN_PDF_PHOTOS and "pdf" not in spent:
            merge_row.append(InlineKeyboardButton("📄 PDF", callback_data=f"{MERGE_PREFIX}pdf:{token}"))
        if items >= merge.MIN_ZIP_ITEMS and "zip" not in spent:
            merge_row.append(InlineKeyboardButton("🗜 ZIP", callback_data=f"{MERGE_PREFIX}zip:{token}"))
        if merge_row:
            rows.append(merge_row)
    kb_row = sibling_bots_keyboard_row(BOT_NAME, only="convertbot")
    if kb_row:
        rows.append(kb_row)
    return InlineKeyboardMarkup(rows) if rows else None


async def _take_button_off(query, lang: str, token: str, remembered: dict) -> None:
    try:
        await query.edit_message_reply_markup(reply_markup=_remembered_kb(lang, token, remembered))
    except Exception:
        logger.debug("Couldn't change the delivery's buttons", exc_info=True)


async def redeliver_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    lang = await i18n.get_lang(update.effective_user.id, context)
    token = query.data[len(REDELIVER_PREFIX):]
    remembered = context.user_data.get("redeliver", {}).get(token)
    if not remembered or not _spend_action(remembered, "redo"):
        note_tap("redo.spent" if remembered else "redo.expired")
        await query.answer(i18n.t(lang, "redeliver_expired"), show_alert=True)
        return
    await query.answer()
    note_tap("redo.to_compressed" if remembered.get("lossless") else "redo.to_file")
    note_tap(_redo_delay_key(remembered))
    delivery_cache.note_link(update.effective_user.id, remembered["platform"], remembered["url"])
    await _take_button_off(query, lang, token, remembered)

    allowance = await asyncio.to_thread(download_allowance, update.effective_user.id)
    if not allowance["allowed"]:
        _unspend_action(remembered, "redo")
        await _take_button_off(query, lang, token, remembered)
        await query.message.reply_text(_allowance_message(lang, allowance))
        return
    await asyncio.to_thread(record_download, update.effective_user.id, remembered["platform"])

    async def work():
        platform, url = remembered["platform"], remembered["url"]
        if platform in ("reddit", "twitter") and not delivery_cache.has(update.effective_user.id, platform, url):
            handler = _handle_reddit if platform == "reddit" else _handle_twitter
            await handler(update, context, url, lossless=not remembered["lossless"])
            return
        await _resolve_and_send(
            update, context, platform, url,
            lossless_override=not remembered["lossless"], from_redo=True,
        )

    _spawn_download(context, update, work)


class _MergeStop(Exception):
    def __init__(self, key: str):
        super().__init__(key)
        self.key = key


def _merge_text(lang: str, key: str) -> str:
    return i18n.t(lang, key, items=MERGE_MAX_ITEMS, mb=MERGE_MAX_MB, mp=merge.MAX_MEGAPIXELS)


def _file_digest(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _without_duplicates(items: list, paths: list) -> "tuple[list, list]":
    seen: set = set()
    kept_items, kept_paths = [], []
    for item, path in zip(items, paths):
        digest = _file_digest(path)
        if digest in seen:
            _remove_files([path])
            continue
        seen.add(digest)
        kept_items.append(item)
        kept_paths.append(path)
    if len(kept_paths) < len(paths):
        logger.info("dropped %d file(s) that came twice", len(paths) - len(kept_paths))
    return kept_items, kept_paths


def _remove_files(paths) -> None:
    for path in paths:
        try:
            if path and os.path.exists(path):
                os.remove(path)
        except OSError:
            logger.warning("couldn't remove %s", path)


async def merge_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    lang = await i18n.get_lang(update.effective_user.id, context)
    kind, _, token = query.data[len(MERGE_PREFIX):].partition(":")
    remembered = context.user_data.get("redeliver", {}).get(token) if kind in ("pdf", "zip") else None
    if not remembered or not _spend_action(remembered, kind):
        if not remembered:
            note_tap("merge.expired")
        await query.answer(i18n.t(lang, "redeliver_expired"), show_alert=True)
        return
    await query.answer()
    await _take_button_off(query, lang, token, remembered)

    allowance = await asyncio.to_thread(download_allowance, update.effective_user.id)
    if not allowance["allowed"]:
        _unspend_action(remembered, kind)
        await _take_button_off(query, lang, token, remembered)
        await query.message.reply_text(_allowance_message(lang, allowance))
        return
    await asyncio.to_thread(record_download, update.effective_user.id, remembered["platform"])

    async def work():
        await _merge_and_send(update, context, kind, remembered["platform"], remembered["url"])

    _spawn_download(context, update, work)


async def _merge_and_send(update: Update, context: ContextTypes.DEFAULT_TYPE,
                          kind: str, platform: str, url: str) -> bool:
    lang = await i18n.get_lang(update.effective_user.id, context)
    user_id, chat_id = update.effective_user.id, update.effective_chat.id
    message = update.effective_message
    refusal = await refuse_new_work(lang, user_id, chat_id)
    if refusal:
        await message.reply_text(refusal)
        return False

    status = await LiveMessage.reply_to(message, i18n.t(lang, "fetching"))
    if _slots().locked() or _user_slots_full(user_id):
        await status.set(context.bot, i18n.t(lang, "queued"))

    paths: list[str] = []
    resolved = None
    out_path = os.path.join(resolvers._WORK_DIR, f"{uuid.uuid4().hex}.{kind}")  # noqa: SLF001
    async with _user_slot(user_id), _slots(), lifecycle.busy(
            chat_id, i18n.t(lang, "restarting_send_again")):
        _make_room(MERGE_MAX_MB * 2)
        if not _disk_room(MERGE_MAX_MB * 2):
            await status.set(context.bot, i18n.t(lang, "download_short_on_space"))
            return False
        held = _hold_disk(MERGE_MAX_MB * 2)
        cached = delivery_cache.take(user_id, platform, url)
        try:
            if cached is not None:
                kept = [(item, path) for item, path in cached.items if kind != "pdf" or item.kind == "photo"]
                wanted, sources = [item for item, _ in kept], [path for _, path in kept]
                if sum(os.path.getsize(path) for path in sources) > MERGE_MAX_MB * 1024 * 1024:
                    raise _MergeStop("merge_too_big")
            else:
                try:
                    resolved = await resolvers.resolve(platform, _split_height(url)[0])
                except resolvers.NothingWorked as exc:
                    logger.info("no route worked for %s (merge): %s", platform, exc)
                    if exc.kind not in ("missing", "too_big"):
                        note_job(False)
                    raise _MergeStop({
                        "missing": "download_missing",
                        "blocked": "download_blocked",
                        "too_big": "merge_too_big",
                    }.get(exc.kind, "download_all_routes_failed"))
                wanted = [item for item in resolved.items if item.kind == "photo"] \
                    if kind == "pdf" else list(resolved.items)
            if kind == "pdf" and len(wanted) < merge.MIN_PDF_PHOTOS:
                raise _MergeStop("merge_no_photos")
            if len(wanted) > MERGE_MAX_ITEMS:
                raise _MergeStop("merge_too_big")

            if cached is None:
                await status.set(context.bot, i18n.t(lang, "downloading"))
                total = 0
                for item in wanted:
                    paths.append(await resolvers.download(item))
                    total += os.path.getsize(paths[-1])
                    if total > MERGE_MAX_MB * 1024 * 1024:
                        raise _MergeStop("merge_too_big")
                wanted, paths[:] = await asyncio.to_thread(_without_duplicates, wanted, list(paths))
                sources = paths

            if kind == "pdf":
                try:
                    await merge.build_pdf_queued(sources, out_path)
                except (merge.NothingToMerge, merge.NotAPhoto):
                    raise _MergeStop("merge_no_photos")
            else:
                await asyncio.to_thread(merge.build_zip, sources, out_path)
            if os.path.getsize(out_path) > large_files.BOT_API_SEND_BYTES:
                raise _MergeStop("merge_too_big")

            caption = _media_caption(lang, await asyncio.to_thread(get_caption_enabled, user_id))
            with open(out_path, "rb") as handle:
                await message.reply_document(
                    handle, filename=merge.output_name(platform, kind),
                    caption=caption, parse_mode=ParseMode.HTML, read_timeout=120, write_timeout=120,
                    disable_content_type_detection=True,
                )
            await status.delete(context.bot)
            note_job(True)
            note_stat("link_extra", f"album_{kind}")
        except _MergeStop as stop:
            await status.set(context.bot, _merge_text(lang, stop.key), **NO_PREVIEW)
            return False
        except net.TooLarge:
            await status.set(context.bot, _merge_text(lang, "merge_too_big"), **NO_PREVIEW)
            return False
        except merge.PictureTooLarge as exc:
            logger.info("merge refused a picture: %s", exc)
            await status.set(context.bot, _merge_text(lang, "merge_picture_too_big"), **NO_PREVIEW)
            return False
        except net.FetchError as exc:
            note_job(False)
            await status.set(context.bot, str(exc), **NO_PREVIEW)
            return False
        except Exception:
            note_job(False)
            logger.exception("Merging %s (%s) failed", platform, kind)
            await status.set(context.bot, _merge_text(lang, "merge_failed"), **NO_PREVIEW)
            return False
        finally:
            if cached is not None:
                delivery_cache.release(cached)
            resources.release_disk(held)
            _remove_files(paths + [item.path for item in (resolved.items if resolved else [])]
                          + [out_path])

    await _after_send(update, context, lang)
    return True


async def _after_send(update: Update, context: ContextTypes.DEFAULT_TYPE, lang: str):
    if _GROUP.get() is not None:
        return
    nudge = await maybe_donation_nudge(
        update.effective_user.id, lang, context, update.effective_chat.id)
    if nudge:
        await update.effective_message.reply_text(nudge)


def _image_extension(data: bytes) -> str:
    if data.startswith(b"\x89PNG"):
        return "png"
    if data.startswith(b"GIF8"):
        return "gif"
    if data[:2] == b"\xff\xd8":
        return "jpg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return "bin"


async def _reply_image_with_button(update, context, data: bytes, stem: str, lang: str,
                                   platform: str, url: str,
                                   lossless: bool | None = None):
    group = _GROUP.get()
    if lossless is None:
        lossless = (group["as_file"] if group is not None
                    else await asyncio.to_thread(get_lossless_enabled, update.effective_user.id))
    token = _remember_delivery(context, platform, url, lossless)
    sent = await _reply_image(update, data, stem,
                              reply_markup=None if group is not None else _nudge_kb(lang, token, lossless),
                              lossless=lossless,
                              caption=_media_caption(lang, await _credit_wanted(update.effective_user.id)))
    filename = f"{stem}.{_image_extension(data)}"
    await asyncio.to_thread(delivery_cache.keep_bytes, update.effective_user.id, platform, url,
                            resolvers.MediaItem("photo", filename=filename), data, filename,
                            _cache_floor_mb())
    return sent


COPYRIGHT_URL = (os.environ.get("TERMS_URL") or "").strip()


def _media_caption(lang: str, credit: bool) -> str:
    caption = (i18n.t(lang, "copyright_note", url=html.escape(COPYRIGHT_URL, quote=True)) if COPYRIGHT_URL
               else i18n.t(lang, "copyright_note_plain"))
    if credit:
        caption += "\n\n" + html.escape(i18n.t(lang, "download_credit_caption", username=BOT_USERNAME))
    return caption


async def _reply_image(update: Update, data: bytes, stem: str, reply_markup=None,
                       lossless: bool | None = None, caption: "str | None" = None):
    if lossless is None:
        lossless = await asyncio.to_thread(get_lossless_enabled, update.effective_user.id)
    if lossless:
        return await update.effective_message.reply_document(
            document=BytesIO(data),
            filename=f"{stem}.{_image_extension(data)}",
            reply_markup=reply_markup, caption=caption, parse_mode=ParseMode.HTML,
            disable_content_type_detection=True,
        )
    return await update.effective_message.reply_photo(
        BytesIO(data), reply_markup=reply_markup, caption=caption, parse_mode=ParseMode.HTML)


@contextmanager
def _media_source(item, path):
    stored = getattr(item, "file_id", None)
    if stored:
        yield stored
    else:
        with open(path, "rb") as handle:
            yield handle


def _file_id_of(sent, kind: str, lossless: bool) -> "str | None":
    try:
        if lossless:
            attached = sent.document
        elif kind == "video":
            attached = sent.video
        elif kind == "audio":
            attached = sent.audio
        else:
            photos = sent.photo
            attached = photos[-1] if photos else None
        file_id = attached.file_id if attached is not None else None
    except (AttributeError, IndexError, TypeError):
        return None
    return file_id if isinstance(file_id, str) and file_id else None


def _audio_filename(title: "str | None", ext: str) -> str:
    stem = "".join(ch for ch in (title or "") if ch.isalnum() or ch in " -_.,()'&").strip(" .")[:60]
    return (stem or "audio") + ext


async def _send_media(update, context, items_with_paths, lang,
                      lossless: bool | None = None, redeliver_token: str | None = None,
                      probing: "video.ProbeRun | None" = None,
                      title: "str | None" = None, duration: "int | None" = None) -> "list[tuple[str, str]] | None":
    group = _GROUP.get()
    caption = _media_caption(lang, await _credit_wanted(update.effective_user.id))
    if lossless is None:
        lossless = (group["as_file"] if group is not None
                    else await asyncio.to_thread(get_lossless_enabled, update.effective_user.id))
    if probing is None:
        probing = video.ProbeRun()

    async def shape_of(path: str) -> dict:
        if probing.exhausted:
            return {}
        shape = await video.probe(path, probing)
        if shape is None:
            return {}
        fields = {"width": shape.width, "height": shape.height}
        if shape.duration is not None:
            fields["duration"] = shape.duration
        return fields

    photos = sum(1 for item, _ in items_with_paths if item.kind == "photo")
    remembered = context.user_data.get("redeliver", {}).get(redeliver_token)
    if remembered is not None:
        remembered["photos"], remembered["items"] = photos, len(items_with_paths)
    keyboard = (None if group is not None
                else _nudge_kb(lang, redeliver_token, lossless, photos=photos, items=len(items_with_paths)))
    message = update.effective_message

    if len(items_with_paths) == 1:
        item, path = items_with_paths[0]
        shape = (await shape_of(path) if item.kind == "video" and not lossless
                 and not getattr(item, "file_id", None) else {})
        with _media_source(item, path) as f:
            if lossless:
                sent = await message.reply_document(
                    f, filename=os.path.basename(item.filename or path),
                    caption=caption, parse_mode=ParseMode.HTML, reply_markup=keyboard,
                    read_timeout=120, write_timeout=120,
                    disable_content_type_detection=True,
                )
            elif item.kind == "video":
                sent = await message.reply_video(
                    f, caption=caption, parse_mode=ParseMode.HTML, reply_markup=keyboard,
                    read_timeout=120, write_timeout=120,
                    **shape,
                )
            elif item.kind == "audio":
                ext = os.path.splitext(item.filename or path or "")[1] or ".m4a"
                sent = await message.reply_audio(
                    f, caption=caption, parse_mode=ParseMode.HTML, reply_markup=keyboard, title=(title or None) and title[:64],
                    filename=_audio_filename(title, ext), duration=duration or None,
                    read_timeout=120, write_timeout=120,
                )
            else:
                sent = await message.reply_photo(
                    f, caption=caption, parse_mode=ParseMode.HTML, reply_markup=keyboard,
                    read_timeout=120, write_timeout=120,
                )
        file_id = _file_id_of(sent, item.kind, lossless)
        return [(item.kind, file_id)] if file_id else None

    sent_ids: "list[tuple[str, str]] | None" = []
    for chunk_start in range(0, len(items_with_paths), 10):
        chunk = items_with_paths[chunk_start:chunk_start + 10]
        shapes = [await shape_of(path) if item.kind == "video" and not lossless
                  and not getattr(item, "file_id", None) else {}
                  for item, path in chunk]
        handles = [getattr(item, "file_id", None) or open(path, "rb") for item, path in chunk]
        try:
            group = []
            for n, ((item, path), handle) in enumerate(zip(chunk, handles)):
                cap = caption if (chunk_start == 0 and n == 0) else None
                if lossless:
                    group.append(InputMediaDocument(
                        handle, filename=os.path.basename(item.filename or path),
                        caption=cap, parse_mode=ParseMode.HTML, disable_content_type_detection=True))
                elif item.kind == "video":
                    group.append(InputMediaVideo(handle, caption=cap, parse_mode=ParseMode.HTML, **shapes[n]))
                else:
                    group.append(InputMediaPhoto(handle, caption=cap, parse_mode=ParseMode.HTML))
            sent_group = await message.reply_media_group(
                group, read_timeout=120, write_timeout=120)
        finally:
            for handle in handles:
                if not isinstance(handle, str):
                    handle.close()
        try:
            sent_group = list(sent_group)
        except TypeError:
            sent_group = []
        file_ids = [_file_id_of(sent, item.kind, lossless) for (item, _), sent in zip(chunk, sent_group)]
        if sent_ids is not None and len(sent_group) == len(chunk) and all(file_ids):
            sent_ids += [(item.kind, file_id) for (item, _), file_id in zip(chunk, file_ids)]
        else:
            sent_ids = None
    if keyboard is not None:
        await message.reply_text(
            i18n.t(lang, "album_delivered", count=len(items_with_paths)),
            reply_markup=keyboard)
    return sent_ids


async def _store_sent_files(user_id: int, platform: str, url: str, lossless: bool, sent) -> None:
    if not sent_files.usable(user_id) or not sent:
        return
    try:
        items = sent_files.encode_items(sent)
        if items is None:
            return
        key = sent_files.post_key(platform, url, lossless)
        await asyncio.to_thread(sent_files_put, key, items)
        sent_files.remember(key, items, sent_files._clock(), sent_files._clock().date())
        sent_files.note("stored")
    except Exception as exc:
        logger.warning("The sent-file store could not be written (%s)", type(exc).__name__)


async def _forget_sent_files(key) -> None:
    sent_files.forget(key)
    try:
        await asyncio.to_thread(sent_files_drop, key)
    except Exception as exc:
        logger.warning("The sent-file store could not drop a row (%s)", type(exc).__name__)


async def _resend_by_file_id(update, context, lang, status, platform: str, url: str,
                             lossless_override: "bool | None") -> "bool | None":
    user_id = update.effective_user.id
    if not sent_files.usable(user_id):
        return None
    try:
        if lossless_override is None:
            lossless = await asyncio.to_thread(get_lossless_enabled, user_id)
        else:
            lossless = lossless_override
        key = sent_files.post_key(platform, url, lossless)
        row = sent_files.recall(key)
        if row is not None:
            sent_files.note("remembered")
        else:
            row = await asyncio.to_thread(sent_files_get, key)
            if row is not None:
                sent_files.remember(key, *row)
    except Exception as exc:
        logger.warning("The sent-file store could not be read (%s)", type(exc).__name__)
        sent_files.note("miss")
        return None
    pairs = sent_files.decode_items(row[0]) if row else None
    if pairs is None:
        sent_files.note("miss")
        return None
    fresh = sent_files.is_fresh(row[1])
    sent_files.note("hit" if fresh else "stale")

    async with lifecycle.busy(update.effective_chat.id, i18n.t(lang, "restarting_send_again")):
        if not fresh:
            try:
                async with _lookups():
                    verdict = await resolvers.lookup(platform, _split_height(url)[0])
            except Exception as exc:
                logger.warning("The post could not be looked up (%s)", type(exc).__name__)
                verdict = "unknown"
            sent_files.note(verdict if verdict in ("present", "missing") else "unknown")
            if verdict == "missing":
                await _forget_sent_files(key)
                return None
            if verdict != "present":
                return None
        token = _remember_delivery(context, platform, url, lossless)
        try:
            await _send_media(update, context, [(file, None) for file in sent_files.stored_files(pairs)], lang,
                              lossless=lossless, redeliver_token=token)
            await status.delete(context.bot)
            note_job(True)
            note_stat("link_site", platform)
        except Exception as exc:
            if isinstance(exc, BadRequest):
                await _forget_sent_files(key)
            if sent_files.is_invalid_file_id(exc):
                sent_files.note("invalid")
                logger.info("A stored file id was refused (%s)", type(exc).__name__)
                context.user_data.get("redeliver", {}).pop(token, None)
                return None
            note_job(False)
            logger.warning("Delivering %s from the stored file ids failed (%s)", platform,
                           type(exc).__name__)
            await status.set(context.bot,
                             i18n.t(lang, "download_failed", error=type(exc).__name__),
                             **NO_PREVIEW)
            return False
    try:
        if not fresh:
            await asyncio.to_thread(sent_files_confirmed, key)
            sent_files.note_used(key, confirmed_at=sent_files._clock())
        elif not sent_files.used_today(key):
            await asyncio.to_thread(sent_files_used, key)
            sent_files.note_used(key)
    except Exception as exc:
        logger.warning("The sent-file store could not be updated (%s)", type(exc).__name__)
    return True


async def _resolve_and_send(update: Update, context: ContextTypes.DEFAULT_TYPE,
                            platform: str, url: str, status=None,
                            quiet_if_missing: bool = False,
                            lossless_override: bool | None = None,
                            from_redo: bool = False,
                            title: "str | None" = None, duration: "int | None" = None) -> bool:
    lang = await i18n.get_lang(update.effective_user.id, context)
    fetch_url, height, exact, sound = _split_choice(url)
    if sound:
        lossless_override = False
    group = _GROUP.get()
    if lossless_override is None and group is not None:
        lossless_override = group["as_file"]
    refusal = await refuse_new_work(
        lang, update.effective_user.id, update.effective_chat.id
    )
    if refusal:
        if status is None:
            await update.effective_message.reply_text(refusal)
        else:
            await status.set(context.bot, refusal)
        return False

    if status is None:
        status = await LiveMessage.reply_to(
            update.effective_message, i18n.t(lang, "fetching"))
    else:
        await status.set(context.bot, i18n.t(lang, "fetching"))

    resent = None if sound else await _resend_by_file_id(
        update, context, lang, status, platform, url, lossless_override)
    if resent is not None:
        if resent:
            await _after_send(update, context, lang)
        return resent

    cached = None if sound else delivery_cache.take(update.effective_user.id, platform, url)
    if from_redo and delivery_cache.usable(update.effective_user.id):
        note_tap("redo.cache.hit" if cached is not None else "redo.cache.miss")
    if cached is not None:
        async with lifecycle.busy(update.effective_chat.id, i18n.t(lang, "restarting_send_again")):
            try:
                if lossless_override is None:
                    delivered_lossless = await asyncio.to_thread(
                        get_lossless_enabled, update.effective_user.id)
                else:
                    delivered_lossless = lossless_override
                token = _remember_delivery(context, platform, url, delivered_lossless)
                sent = await _send_media(update, context, list(cached.items), lang,
                                         lossless=delivered_lossless, redeliver_token=token)
                await status.delete(context.bot)
                note_job(True)
                note_stat("link_site", platform)
                await _store_sent_files(update.effective_user.id, platform, url, delivered_lossless, sent)
            except Exception as exc:
                note_job(False)
                logger.warning("Delivering %s from the kept files failed (%s)", platform,
                               type(exc).__name__)
                await status.set(context.bot,
                                 i18n.t(lang, "download_failed", error=type(exc).__name__),
                                 **NO_PREVIEW)
                return False
            finally:
                delivery_cache.release(cached)
        await _after_send(update, context, lang)
        return True

    if _slots().locked() or _user_slots_full(update.effective_user.id):
        await status.set(context.bot, i18n.t(lang, "queued"))

    paths: list[str] = []
    async with _user_slot(update.effective_user.id), _slots(), lifecycle.busy(
            update.effective_chat.id, i18n.t(lang, "restarting_send_again")):
        if not _disk_room(video.MAX_DOWNLOAD_MB):
            await status.set(context.bot, i18n.t(lang, "download_short_on_space"))
            return False
        try:
            resolved = await resolvers.resolve(platform, fetch_url, height=height, exact=exact, sound=sound)
        except resolvers.NothingWorked as exc:
            logger.info("no route worked for %s: %s", platform, exc)
            if exc.kind == "missing" and quiet_if_missing:
                return False
            if exc.kind == "too_big" and large_files.CONFIGURED and not sound:
                return await _large_download(update, context, status, lang, platform, url)
            if exc.kind not in ("missing", "too_big"):
                note_job(False)
            key = {
                "missing": "download_missing",
                "blocked": "download_blocked",
                "too_big": "download_too_big",
            }.get(exc.kind, "download_all_routes_failed")
            await status.set(context.bot, i18n.t(lang, key), **NO_PREVIEW)
            return False

        held = _hold_disk(video.MAX_DOWNLOAD_MB)
        try:
            await status.set(context.bot, i18n.t(lang, "downloading"))
            items = resolved.items
            for item in items:
                paths.append(await resolvers.download(item))
            if len(paths) > 1:
                items, paths[:] = await asyncio.to_thread(_without_duplicates, items, list(paths))
            if lossless_override is None:
                delivered_lossless = await asyncio.to_thread(
                    get_lossless_enabled, update.effective_user.id)
            else:
                delivered_lossless = lossless_override
            token = None if sound else _remember_delivery(context, platform, url, delivered_lossless)
            sent = await _send_media(update, context, list(zip(items, paths)), lang,
                                     lossless=delivered_lossless, redeliver_token=token,
                                     title=title or getattr(resolved, "title", None), duration=duration)
            await status.delete(context.bot)
            note_job(True)
            note_stat("link_site", platform)
            for item in items:
                note_stat("link_media", f"{platform}:{getattr(item, 'kind', None) or 'other'}")
            if not sound:
                kept = delivery_cache.keep(update.effective_user.id, platform, url,
                                           list(zip(items, paths)), _cache_floor_mb())
                if delivery_cache.usable(update.effective_user.id):
                    note_tap("cache.kept" if kept else "cache.not_kept")
                if kept:
                    paths.clear()
                await _store_sent_files(update.effective_user.id, platform, url, delivered_lossless, sent)
        except net.TooLarge as exc:
            if not large_files.CONFIGURED or sound:
                await status.set(context.bot, str(exc), **NO_PREVIEW)
                return False
            for path in paths:
                if path and os.path.exists(path):
                    os.remove(path)
            paths.clear()
            return await _large_download(update, context, status, lang, platform, url)
        except net.FetchError as exc:
            note_job(False)
            await status.set(context.bot, str(exc), **NO_PREVIEW)
            return False
        except Exception as exc:
            note_job(False)
            logger.exception("Delivering %s via %s failed", platform, resolved.provider)
            await status.set(context.bot, i18n.t(lang, "download_failed", error=exc),
                             **NO_PREVIEW)
            return False
        finally:
            resources.release_disk(held)
            for path in paths:
                try:
                    if path and os.path.exists(path):
                        os.remove(path)
                except OSError:
                    logger.warning("couldn't remove %s", path)

    await _after_send(update, context, lang)
    return True


async def _large_download(update, context, status, lang, platform: str, url: str) -> bool:
    chat_id, user_id = update.effective_chat.id, update.effective_user.id
    free, per = large_files.FREE_MAX_MB, large_files.first_price()
    if not large_files.available(chat_id):
        await status.set(context.bot, i18n.t(lang, "large_files_private_only", free=free))
        return False
    if not await asyncio.to_thread(get_large_files_enabled, user_id):
        offer = InlineKeyboardMarkup([[InlineKeyboardButton(
            i18n.t(lang, "large_files_turn_on_button"), callback_data="large:offer")]])
        await status.set(context.bot, i18n.t(lang, "large_files_off", free=free, per=per), reply_markup=offer)
        return False
    balance = await asyncio.to_thread(family_link.star_balance, user_id)
    if balance < per:
        await status.set(context.bot, i18n.t(lang, "large_files_no_credit", free=free, per=per, balance=balance))
        return False

    _make_room(large_files.LARGE_MAX_MB)
    room = min(large_files.LARGE_MAX_MB, LARGE_ROOM_MB)
    if not _disk_room(room):
        await status.set(context.bot, i18n.t(lang, "download_short_on_space"))
        return False
    await status.set(context.bot, i18n.t(lang, "large_files_fetching", free=free, per=per))
    paths: list[str] = []
    held = _hold_disk(room)
    fetch_url, height, exact = _split_height(url)
    try:
        try:
            resolved = await resolvers.resolve(platform, fetch_url, max_mb=large_files.LARGE_MAX_MB,
                                               height=height, exact=exact)
            for item in resolved.items:
                paths.append(await resolvers.download(item, max_mb=large_files.LARGE_MAX_MB))
        except (resolvers.NothingWorked, net.TooLarge) as exc:
            too_big = isinstance(exc, net.TooLarge) or exc.kind == "too_big"
            if not too_big:
                note_job(False)
            await status.set(context.bot, i18n.t(lang, "large_files_too_big", max=large_files.LARGE_MAX_MB,
                                                 minutes=video.MAX_DURATION_S // 60)
                             if too_big else i18n.t(lang, "download_all_routes_failed"), **NO_PREVIEW)
            return False
        except net.FetchError as exc:
            note_job(False)
            await status.set(context.bot, str(exc), **NO_PREVIEW)
            return False

        size = sum(os.path.getsize(path) for path in paths)
        mb = max(1, round(size / large_files.MB))
        price = large_files.price_for(size)
        after = await asyncio.to_thread(family_link.spend_stars, user_id, price, "spend",
                                        f"large download, {mb} MB")
        if after is None:
            balance = await asyncio.to_thread(family_link.star_balance, user_id)
            await status.set(context.bot, i18n.t(lang, "large_files_short", mb=mb, price=price, balance=balance))
            return False
        try:
            await status.set(context.bot, i18n.t(lang, "large_files_sending", mb=mb))
            await _send_large(update, context, list(zip(resolved.items, paths)), lang, status)
        except Exception:
            logger.exception("A large download of %s MB could not be sent", mb)
            note_job(False)
            back = await asyncio.to_thread(family_link.move_stars, user_id, price, "refund",
                                           "large download not delivered")
            await status.set(context.bot, i18n.t(lang, "large_files_send_failed", price=price, balance=back))
            return False
        note_job(True)
        note_stat("link_site", platform)
        note_stat("link_extra", "large_file")
        await status.set(context.bot, i18n.t(lang, "large_files_charged", mb=mb, price=price, balance=after))
        return True
    finally:
        resources.release_disk(held)
        for path in paths:
            try:
                if path and os.path.exists(path):
                    os.remove(path)
            except OSError:
                logger.warning("couldn't remove %s", path)


async def _credit_wanted(user_id: int) -> bool:
    group = _GROUP.get()
    if group is not None:
        return group["credit"]
    return await asyncio.to_thread(get_caption_enabled, user_id)


async def _send_large(update, context, items_with_paths, lang, status=None) -> None:
    caption = _media_caption(lang, await _credit_wanted(update.effective_user.id))
    probing = video.ProbeRun()

    async def waiting() -> None:
        if status is not None:
            await status.set(context.bot, i18n.t(lang, "transfer_waiting"))

    for item, path in items_with_paths:
        if os.path.getsize(path) > large_files.BOT_API_SEND_BYTES:
            shape = await video.probe(path, probing) if item.kind == "video" else None
            await big_files.send_document(update.effective_chat.id, path,
                                          os.path.basename(item.filename or path), caption,
                                          waiting=waiting, html=True,
                                          video=None if shape is None else {
                                              "duration": shape.duration or 0, "width": shape.width,
                                              "height": shape.height})
        else:
            await _send_media(update, context, [(item, path)], lang, probing=probing)


async def _handle_reddit(update: Update, context: ContextTypes.DEFAULT_TYPE, url: str,
                         lossless: bool | None = None):
    lang = await i18n.get_lang(update.effective_user.id, context)
    status = await LiveMessage.reply_to(update.effective_message, i18n.t(lang, "fetching"))
    try:
        post = await platforms.fetch_reddit_post(url)
    except Exception as exc:
        await status.set(context.bot, i18n.t(lang, "reddit_fetch_failed", error=exc))
        return

    if platforms.reddit_is_media(post):
        direct_img = platforms.reddit_direct_image_url(post)
        if direct_img:
            try:
                image = await net.fetch_bytes(direct_img)
                await _reply_image_with_button(
                    update, context, image, "reddit", lang, "reddit", url, lossless)
                await status.delete(context.bot)
                await _after_send(update, context, lang)
                return
            except Exception:
                logger.exception("Reddit direct image fetch failed -- falling back")
        await _resolve_and_send(update, context, "reddit", url, status=status, lossless_override=lossless)
        return

    title = post.get("title", "")
    selftext = post.get("selftext", "")
    body = title if not selftext else title + "\n\n" + selftext
    subreddit = "r/" + post.get("subreddit", "?")
    author = "u/" + post.get("author", "?")
    score = post.get("score")
    meta = f"{score:,} upvotes" if isinstance(score, int) else ""

    png = await asyncio.to_thread(cards.render_card, "reddit", subreddit, author, body, meta)
    await _reply_image_with_button(
        update, context, png, "reddit_post", lang, "reddit", url, lossless)
    await status.delete(context.bot)
    await _after_send(update, context, lang)


async def _handle_twitter(update: Update, context: ContextTypes.DEFAULT_TYPE, url: str,
                          lossless: bool | None = None):
    lang = await i18n.get_lang(update.effective_user.id, context)
    status = await LiveMessage.reply_to(update.effective_message, i18n.t(lang, "fetching"))
    if await _resolve_and_send(update, context, "twitter", url, status=status,
                               quiet_if_missing=True, lossless_override=lossless):
        return

    tweet = await platforms.fetch_tweet_syndication(url)
    if tweet:
        user = tweet.get("user") or {}
        handle = user.get("screen_name")
        source = f"@{handle}" if handle else "Twitter/X"
        author = user.get("name", "")
        likes = tweet.get("favorite_count")
        meta = f"{likes:,} likes" if isinstance(likes, int) else ""
        png = await asyncio.to_thread(
            cards.render_card, "twitter", source, author, tweet.get("text", ""), meta)
        await _reply_image_with_button(
            update, context, png, "tweet", lang, "twitter", url, lossless)
        await status.delete(context.bot)
        await _after_send(update, context, lang)
        return

    await status.set(context.bot, i18n.t(lang, "twitter_fetch_failed_link", url=url))


def _allowance_message(lang: str, allowance: dict) -> str:
    key = ("quota_hour" if allowance["scope"] == "hour" else "quota_day")
    text = i18n.t(lang, key, used=allowance[allowance["scope"]],
                  limit=allowance[f"{allowance['scope']}_max"],
                  minutes=allowance["wait_min"])
    if not allowance["donor"]:
        text += "\n\n" + i18n.t(lang, "quota_donor_hint")
    return text + problems.code_line("DL-QUOTA-HOUR" if allowance["scope"] == "hour" else "DL-QUOTA-DAY")


async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    detected = platforms.detect_platform(update.message.text or update.message.caption or "")
    if not detected:
        return
    platform, url = detected
    group = _GROUP.get()
    if group is not None and group["quality"] and platform not in ("reddit", "twitter"):
        url = _with_height(url, group["quality"], exact=False)
    found = delivery_cache.note_link(update.effective_user.id, platform, url)
    if found == delivery_cache.NOTED_AGAIN_CACHED:
        note_tap("link.again.cached")
    elif found == delivery_cache.NOTED_AGAIN_NOT_CACHED:
        note_tap("link.again.not_cached")

    key = (update.effective_chat.id, update.effective_user.id, platform, url)
    if _already_in_flight(key):
        await _answer_duplicate(update)
        return
    stamp = _in_flight[key] = time.monotonic()
    spawned = False
    try:
        lang = await i18n.get_lang(update.effective_user.id, context)
        refusal = await refuse_new_work(lang, update.effective_user.id, update.effective_chat.id)
        if refusal:
            await update.message.reply_text(refusal)
            return

        if _user_pending.get(update.effective_user.id, 0) >= MAX_PENDING_PER_USER:
            await update.message.reply_text(
                i18n.t(lang, "download_queue_full", count=MAX_PENDING_PER_USER))
            return
        allowance = await asyncio.to_thread(download_allowance, update.effective_user.id)
        if not allowance["allowed"]:
            await update.message.reply_text(_allowance_message(lang, allowance))
            return
        picking = platform in resolvers.PICK_PLATFORMS and _GROUP.get() is None
        if not picking:
            await asyncio.to_thread(record_download, update.effective_user.id, platform)

        async def work():
            if platform == "reddit":
                await _handle_reddit(update, context, url)
            elif platform == "twitter":
                await _handle_twitter(update, context, url)
            elif picking:
                await _offer_choices(update, context, platform, url)
            else:
                await _resolve_and_send(update, context, platform, url)

        _spawn_download(context, update, work, in_flight=key)
        spawned = True
    finally:
        if not spawned:
            _clear_in_flight(key, stamp)


PICK_PREFIX = "pick:"
PICK_MEMORY = 5
PICK_AUDIO = "a"


def _remember_pick(context, platform: str, url: str, found: "resolvers.Choices") -> str:
    memory = context.user_data.setdefault("picks", {})
    while len(memory) >= PICK_MEMORY:
        memory.pop(next(iter(memory)))
    token = uuid.uuid4().hex[:10]
    memory[token] = {"platform": platform, "url": url, "title": (found.title or "")[:200],
                     "duration": found.duration,
                     "qualities": [quality for quality, _, _ in found.qualities],
                     "audio": bool(found.audio), "at": time.time()}
    return token


def _size_label(lang: str, size: int) -> str:
    mb = size / large_files.MB
    return i18n.t(lang, "quality_size", mb=f"{mb:.1f}" if mb < 10 else f"{mb:.0f}")


def _pick_kb(lang: str, token: str, found: "resolvers.Choices") -> InlineKeyboardMarkup:
    buttons = [InlineKeyboardButton(f"{'🎬' if free else '⚡'} {quality}p · {_size_label(lang, size)}",
                                    callback_data=f"{PICK_PREFIX}{token}:{quality}")
               for quality, size, free in found.qualities]
    rows = [buttons[n:n + 2] for n in range(0, len(buttons), 2)]
    if found.audio:
        rows.append([InlineKeyboardButton(f"{i18n.t(lang, 'quality_audio')} · {_size_label(lang, found.audio)}",
                                          callback_data=f"{PICK_PREFIX}{token}:{PICK_AUDIO}")])
    convert = sibling_bots_keyboard_row(BOT_NAME, only="convertbot")
    if convert:
        rows.append([InlineKeyboardButton(i18n.t(lang, "quality_more_button"), url=convert[0].url)])
    return InlineKeyboardMarkup(rows)


def _pick_caption(lang: str, found: "resolvers.Choices") -> str:
    lines = []
    if found.title:
        lines.append("🎞 " + found.title[:300])
    if found.duration:
        minutes, seconds = divmod(int(found.duration), 60)
        hours, minutes = divmod(minutes, 60)
        lines.append("⏱ " + (f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes}:{seconds:02d}"))
    tail = []
    if any(not free for _, _, free in found.qualities):
        tail.append(i18n.t(lang, "quality_paid_note", free=video.MAX_DOWNLOAD_MB))
    tail.append(i18n.t(lang, "quality_choose_or_sound" if found.audio else "quality_choose"))
    return "\n".join(lines) + ("\n\n" if lines else "") + "\n\n".join(tail)


async def _offer_choices(update: Update, context: ContextTypes.DEFAULT_TYPE, platform: str, url: str) -> None:
    lang = await i18n.get_lang(update.effective_user.id, context)
    status = await LiveMessage.reply_to(update.effective_message, i18n.t(lang, "fetching"))
    paid_bytes = large_files.LARGE_MAX_MB * large_files.MB if large_files.CONFIGURED else 0
    async with _lookups():
        found = await resolvers.choices(platform, url, paid_bytes)
    if found is None:
        note_tap("pick.not_listed")
        await asyncio.to_thread(record_download, update.effective_user.id, platform)
        await _resolve_and_send(update, context, platform, url, status=status)
        return
    token = _remember_pick(context, platform, url, found)
    caption, keyboard = _pick_caption(lang, found), _pick_kb(lang, token, found)
    message = update.effective_message
    card = None
    if found.thumbnail:
        try:
            card = await message.reply_photo(found.thumbnail, caption=caption, reply_markup=keyboard)
        except Exception as exc:
            logger.info("The picker's picture was not sent (%s)", type(exc).__name__)
    if card is None:
        await message.reply_text(caption, reply_markup=keyboard, **NO_PREVIEW)
    await status.delete(context.bot)
    note_tap("pick.offered")


def _parse_pick(data: str) -> "tuple[str, object] | None":
    token, _, wanted = data[len(PICK_PREFIX):].partition(":")
    if wanted == PICK_AUDIO:
        return token, resolvers.AUDIO
    return (token, int(wanted)) if wanted.isdigit() else None


async def pick_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id, chat_id = update.effective_user.id, update.effective_chat.id
    lang = await i18n.get_lang(user_id, context)
    parsed = _parse_pick(query.data or "")
    remembered = context.user_data.get("picks", {}).get(parsed[0]) if parsed else None
    choice = parsed[1] if parsed else None
    sound = choice == resolvers.AUDIO
    if not remembered or (sound and not remembered.get("audio")) or (
            not sound and choice not in remembered.get("qualities", ())):
        note_tap("pick.expired")
        await query.answer(i18n.t(lang, "quality_expired"), show_alert=True)
        return
    platform = remembered["platform"]
    url = _with_sound(remembered["url"]) if sound else _with_height(remembered["url"], choice)
    key = (chat_id, user_id, platform, url)
    if _already_in_flight(key):
        await query.answer("👌")
        return
    await query.answer()
    refusal = await refuse_new_work(lang, user_id, chat_id)
    if refusal:
        await query.message.reply_text(refusal)
        return
    if _user_pending.get(user_id, 0) >= MAX_PENDING_PER_USER:
        await query.message.reply_text(i18n.t(lang, "download_queue_full", count=MAX_PENDING_PER_USER))
        return
    allowance = await asyncio.to_thread(download_allowance, user_id)
    if not allowance["allowed"]:
        await query.message.reply_text(_allowance_message(lang, allowance))
        return
    await asyncio.to_thread(record_download, user_id, platform)
    note_tap("pick.audio" if sound else f"pick.{choice}p")
    if not sound:
        delivery_cache.note_link(user_id, platform, url)
    stamp = _in_flight[key] = time.monotonic()
    spawned = False
    try:

        async def work():
            await _resolve_and_send(update, context, platform, url, title=remembered.get("title"),
                                    duration=remembered.get("duration"))

        _spawn_download(context, update, work, in_flight=key)
        spawned = True
    finally:
        if not spawned:
            _clear_in_flight(key, stamp)


GROUP_TYPES = ("group", "supergroup")
GROUP_COMMANDS = [
    BotCommand("settings", "What gets downloaded here, and how"),
    BotCommand("help", "How the bot works in a group"),
]


async def _group_settings(chat_id: int) -> dict:
    return groups.normalize(await asyncio.to_thread(get_group_settings, chat_id))


def _group_command(message) -> "str | None":
    text = (message.text or "") if message is not None else ""
    if not text.startswith("/"):
        return None
    name, _, addressee = text.split()[0][1:].partition("@")
    if addressee and (not BOT_USERNAME or addressee.lower() != BOT_USERNAME.lower()):
        return ""
    return name.lower()


async def _sees_group_messages(bot, chat_id: int) -> bool:
    if getattr(bot, "can_read_all_group_messages", False):
        return True
    try:
        member = await bot.get_chat_member(chat_id, bot.id)
    except Exception:
        return True
    return member.status in ("administrator", "creator")


async def _is_group_admin(bot, chat_id: int, user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(chat_id, user_id)
    except Exception:
        return False
    return member.status in ("administrator", "creator")


async def _group_hello(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    lang = await i18n.get_lang(user.id, context) if user is not None else "en"
    chat_id = update.effective_chat.id
    text = i18n.t(lang, "group_hello")
    if not await _sees_group_messages(context.bot, chat_id):
        text += "\n\n" + i18n.t(lang, "group_cant_see")
    await context.bot.send_message(chat_id, text, **NO_PREVIEW)


def _group_settings_view(lang: str, settings: dict) -> "tuple[str, InlineKeyboardMarkup]":
    def button(label: str, action: str) -> InlineKeyboardButton:
        return InlineKeyboardButton(label, callback_data=groups.PREFIX + action)

    names = {"instagram": "Instagram", "tiktok": "TikTok", "twitter": "X", "pinterest": "Pinterest",
             "reddit": "Reddit", platforms.UNADVERTISED: i18n.t(lang, "group_other_videos")}
    rows = [[button(i18n.t(lang, "group_on" if settings["on"] else "group_paused"), "on")]]
    sites = [button(("✅ " if site in settings["platforms"] else "▫️ ") + names[site], f"p:{site}")
             for site in groups.PLATFORMS]
    rows += [sites[i:i + 2] for i in range(0, len(sites), 2)]
    rows.append([button(("• " if settings["quality"] == q else "")
                        + (i18n.t(lang, "group_quality_best") if q == 0 else f"{q}p"), f"q:{q}")
                 for q in groups.QUALITIES])
    rows.append([button(i18n.t(lang, "group_as_file" if settings["as_file"] else "group_as_video"), "as_file")])
    rows.append([button(i18n.t(lang, "group_credit_on" if settings["credit"] else "group_credit_off"), "credit")])
    rows.append([button(i18n.t(lang, "group_admins_only" if settings["admins_only"] else "group_anyone"),
                        "admins")])
    return i18n.t(lang, "group_settings_text", free=video.MAX_DOWNLOAD_MB), InlineKeyboardMarkup(rows)


async def group_settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    lang = await i18n.get_lang(update.effective_user.id, context)
    text, markup = _group_settings_view(lang, await _group_settings(update.effective_chat.id))
    await update.effective_message.reply_text(text, reply_markup=markup)


async def group_settings_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    chat = update.effective_chat
    lang = await i18n.get_lang(update.effective_user.id, context)
    parsed = groups.parse(query.data or "")
    if parsed is None or chat is None or chat.type not in GROUP_TYPES:
        await query.answer()
        return
    action, value = parsed
    settings = await _group_settings(chat.id)
    if groups.needs_admin(action, settings) and not await _is_group_admin(
            context.bot, chat.id, update.effective_user.id):
        await query.answer(i18n.t(lang, "group_admins_alert"), show_alert=True)
        return
    changed = groups.apply(settings, action, value)
    if changed is None:
        await query.answer()
        return
    note_active(context, update.effective_user)
    await asyncio.to_thread(set_group_settings, chat.id, changed)
    await query.answer(i18n.t(lang, "group_saved"))
    text, markup = _group_settings_view(lang, changed)
    try:
        await query.edit_message_text(text, reply_markup=markup)
    except BadRequest as exc:
        if "not modified" not in str(exc).lower():
            raise


async def group_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message, chat, user = update.effective_message, update.effective_chat, update.effective_user
    try:
        command = _group_command(message)
        if message is not None and message.migrate_from_chat_id:
            await asyncio.to_thread(move_group_settings, message.migrate_from_chat_id, chat.id)
        elif command == "settings" and user is not None:
            note_active(context, user)
            await group_settings_command(update, context)
        elif command in ("start", "help"):
            if user is not None:
                note_active(context, user)
            await _group_hello(update, context)
        elif command is None and user is not None:
            text = (message.text or message.caption or "") if message is not None else ""
            detected = platforms.detect_platform(text) if text else None
            if detected is not None:
                settings = await _group_settings(chat.id)
                if settings["on"] and detected[0] in settings["platforms"]:
                    note_active(context, user)
                    token = _GROUP.set(settings)
                    try:
                        await handle_link(update, context)
                    finally:
                        _GROUP.reset(token)
    except Exception as exc:
        error = exc

        async def report():
            raise error

        context.application.create_task(report(), update=update)
    raise ApplicationHandlerStop


async def bot_membership_changed(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    change = update.my_chat_member
    if change is not None and change.chat.type == "channel":
        await _channel_membership_changed(update, context)
        return
    if change is None or change.chat.type not in GROUP_TYPES:
        return
    before, now = change.old_chat_member.status, change.new_chat_member.status
    if now in ("left", "kicked"):
        await asyncio.to_thread(forget_group, change.chat.id)
    elif before in ("left", "kicked"):
        await _group_hello(update, context)


CHANNEL_RIGHT_TEXTS = {"edit": "channel_right_edit", "post": "channel_right_post",
                       "delete": "channel_right_delete"}
CHANNELS_LISTED = 50


async def _channel_settings(chat_id: int) -> "tuple[dict, bool]":
    raw = await asyncio.to_thread(get_group_settings, chat_id)
    return channels.normalize(raw), channels.is_channel(raw)


async def _bot_rights(bot, chat_id: int) -> "tuple[bool, bool, bool]":
    try:
        member = await bot.get_chat_member(chat_id, bot.id)
    except Exception:
        return False, False, False
    if member.status != "administrator":
        return False, False, False
    return (bool(getattr(member, "can_edit_messages", False)),
            bool(getattr(member, "can_post_messages", False)),
            bool(getattr(member, "can_delete_messages", False)))


async def _is_channel_admin(bot, chat_id: int, user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(chat_id, user_id)
    except Exception:
        return False
    return member.status in ("administrator", "creator")


async def _channel_title(bot, chat_id: int) -> str:
    try:
        return (await bot.get_chat(chat_id)).title or str(chat_id)
    except Exception:
        return str(chat_id)


def _link_post(post) -> "tuple[str, str] | None":
    if post is None or not post.text or getattr(post, "forward_origin", None) is not None:
        return None
    return platforms.detect_platform(post.text)


async def channel_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    post = update.channel_post
    try:
        detected = _link_post(post)
        if detected is not None:
            settings, stored = await _channel_settings(post.chat_id)
            if not stored:
                await asyncio.to_thread(set_group_settings, post.chat_id, settings)
            if settings["on"] and detected[0] in settings["platforms"]:
                context.application.create_task(
                    _counted_channel_job(_channel_job(context.bot, post, detected[0], detected[1], settings)),
                    update=update)
    except Exception as exc:
        error = exc

        async def report():
            raise error

        context.application.create_task(report(), update=update)
    raise ApplicationHandlerStop


_channel_jobs = 0


async def _counted_channel_job(job) -> None:
    global _channel_jobs
    _channel_jobs += 1
    try:
        await job
    finally:
        _channel_jobs -= 1


async def _channel_job(bot, post, platform: str, url: str, settings: dict) -> None:
    if lifecycle.is_draining():
        return
    if settings["quality"] and platform not in ("reddit", "twitter"):
        url = _with_height(url, settings["quality"], exact=False)
    fetch_url, height, exact = _split_height(url)
    paths: list = []
    async with _slots():
        if not _disk_room(video.MAX_DOWNLOAD_MB):
            logger.warning("A channel post was left as it is: the disk is short of space")
            return
        try:
            resolved = await resolvers.resolve(platform, fetch_url, height=height, exact=exact)
        except resolvers.NothingWorked as exc:
            logger.info("A channel post was left as it is: no route worked for %s (%s)", platform, exc.kind)
            if exc.kind not in ("missing", "too_big"):
                note_job(False)
            return
        held = _hold_disk(video.MAX_DOWNLOAD_MB)
        try:
            items = list(resolved.items)
            for item in items:
                paths.append(await resolvers.download(item))
            if len(paths) > 1:
                items, paths[:] = await asyncio.to_thread(_without_duplicates, items, list(paths))
            if await _put_into_channel(bot, post, list(zip(items, paths)), settings):
                note_job(True)
                note_stat("link_site", platform)
                note_stat("link_extra", "channel_post")
        except (net.TooLarge, net.FetchError) as exc:
            logger.info("A channel post was left as it is: %s", type(exc).__name__)
        except Exception:
            note_job(False)
            raise
        finally:
            resources.release_disk(held)
            _remove_files(paths)


async def _video_shape(path: str, probing) -> dict:
    if probing.exhausted:
        return {}
    shape = await video.probe(path, probing)
    if shape is None:
        return {}
    fields = {"width": shape.width, "height": shape.height}
    if shape.duration is not None:
        fields["duration"] = shape.duration
    return fields


def _channel_media(item, handle, path: str, as_file: bool, caption=None, entities=None, shape=None):
    if as_file:
        return InputMediaDocument(handle, caption=caption, caption_entities=entities,
                                  filename=os.path.basename(item.filename or path),
                                  disable_content_type_detection=True)
    if item.kind == "video":
        return InputMediaVideo(handle, caption=caption, caption_entities=entities,
                               supports_streaming=True, **(shape or {}))
    return InputMediaPhoto(handle, caption=caption, caption_entities=entities)


async def _post_one(bot, chat_id: int, item, handle, path: str, as_file: bool, caption, entities,
                    shape: dict) -> None:
    common = {"caption": caption, "caption_entities": entities, "disable_notification": True,
              "read_timeout": 120, "write_timeout": 120}
    if as_file:
        await bot.send_document(chat_id, handle, filename=os.path.basename(item.filename or path),
                                disable_content_type_detection=True, **common)
    elif item.kind == "video":
        await bot.send_video(chat_id, handle, supports_streaming=True, **shape, **common)
    else:
        await bot.send_photo(chat_id, handle, **common)


async def _put_into_channel(bot, post, items_with_paths, settings: dict) -> "str | None":
    credit = None
    if settings["credit"] and BOT_USERNAME:
        credit = i18n.t(settings["lang"], "download_credit_caption", username=BOT_USERNAME)
    caption = channels.caption_for(post.text, credit)
    entities = list(post.entities or ())
    if caption != post.text:
        mention = "@" + BOT_USERNAME
        at = caption.rindex(mention)
        entities.append(MessageEntity(MessageEntity.MENTION, channels.utf16_length(caption[:at]),
                                      channels.utf16_length(mention)))
    can_edit, can_post, can_delete = await _bot_rights(bot, post.chat_id)
    choice = channels.plan(len(items_with_paths), caption, can_edit, can_post, can_delete,
                           settings["repost"])
    if choice is None:
        logger.info("A channel post was left as it is: %d file(s), a %d-character caption, rights "
                    "edit=%s post=%s delete=%s, repost %s", len(items_with_paths),
                    channels.utf16_length(caption), can_edit, can_post, can_delete,
                    "on" if settings["repost"] else "off")
        return None
    as_file, probing = settings["as_file"], video.ProbeRun()
    with contextlib.ExitStack() as files:
        parts = []
        for n, (item, path) in enumerate(items_with_paths):
            shape = await _video_shape(path, probing) if item.kind == "video" and not as_file else {}
            parts.append((item, path, files.enter_context(open(path, "rb")), shape,
                          caption if n == 0 else None, (entities or None) if n == 0 else None))
        if choice == "edit":
            item, path, handle, shape, text, marks = parts[0]
            await bot.edit_message_media(chat_id=post.chat_id, message_id=post.message_id,
                                         media=_channel_media(item, handle, path, as_file, text, marks, shape),
                                         read_timeout=120, write_timeout=120)
            return "edit"
        for start in range(0, len(parts), channels.ALBUM_MAX):
            chunk = parts[start:start + channels.ALBUM_MAX]
            if len(chunk) == 1:
                item, path, handle, shape, text, marks = chunk[0]
                await _post_one(bot, post.chat_id, item, handle, path, as_file, text, marks, shape)
            else:
                album = [_channel_media(item, handle, path, as_file, text, marks, shape)
                         for item, path, handle, shape, text, marks in chunk]
                await bot.send_media_group(post.chat_id, album, disable_notification=True,
                                           read_timeout=120, write_timeout=120)
    await bot.delete_message(post.chat_id, post.message_id)
    return "repost"


def _channel_view(lang: str, chat_id: int, title: str, settings: dict,
                  rights: "tuple[bool, bool, bool]") -> "tuple[str, InlineKeyboardMarkup]":
    def button(label: str, action: str, value: str = "") -> InlineKeyboardButton:
        return InlineKeyboardButton(label, callback_data=channels.data(chat_id, action, value))

    names = {"instagram": "Instagram", "tiktok": "TikTok", "twitter": "X", "pinterest": "Pinterest",
             "reddit": "Reddit", platforms.UNADVERTISED: i18n.t(lang, "group_other_videos")}
    rows = [[button(i18n.t(lang, "group_on" if settings["on"] else "group_paused"), "on")]]
    sites = [button(("✅ " if site in settings["platforms"] else "▫️ ") + names[site], "p", site)
             for site in channels.PLATFORMS]
    rows += [sites[i:i + 2] for i in range(0, len(sites), 2)]
    rows.append([button(("• " if settings["quality"] == q else "")
                        + (i18n.t(lang, "group_quality_best") if q == 0 else f"{q}p"), "q", str(q))
                 for q in channels.QUALITIES])
    rows.append([button(i18n.t(lang, "group_as_file" if settings["as_file"] else "group_as_video"), "as_file")])
    rows.append([button(i18n.t(lang, "group_credit_on" if settings["credit"] else "group_credit_off"), "credit")])
    rows.append([button(i18n.t(lang, "channel_repost_on" if settings["repost"] else "channel_repost_off"),
                        "repost")])
    text = i18n.t(lang, "channel_settings_text", title=title, free=video.MAX_DOWNLOAD_MB)
    missing = channels.missing_rights(*rights, settings["repost"])
    if missing:
        text += "\n\n" + i18n.t(lang, "channel_needs_rights",
                                  rights=", ".join(i18n.t(lang, CHANNEL_RIGHT_TEXTS[m]) for m in missing))
    return text, InlineKeyboardMarkup(rows)


async def _channel_screen(bot, lang: str, chat_id: int, settings: dict,
                          title: "str | None" = None) -> "tuple[str, InlineKeyboardMarkup]":
    title = title or await _channel_title(bot, chat_id)
    return _channel_view(lang, chat_id, title, settings, await _bot_rights(bot, chat_id))


async def _channel_membership_changed(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    change = update.my_chat_member
    chat_id, now = change.chat.id, change.new_chat_member.status
    if now in ("left", "kicked"):
        await asyncio.to_thread(forget_group, chat_id)
        return
    if now != "administrator":
        return
    who = change.from_user
    lang = await i18n.get_lang(who.id, context) if who is not None else "en"
    settings, stored = await _channel_settings(chat_id)
    if not stored:
        settings["lang"] = lang if lang in channels.LANGS else "en"
        await asyncio.to_thread(set_group_settings, chat_id, settings)
    if who is None or who.is_bot:
        return
    text, markup = await _channel_screen(context.bot, lang, chat_id, settings, change.chat.title)
    if change.old_chat_member.status in ("left", "kicked"):
        text = i18n.t(lang, "channel_hello", title=change.chat.title or chat_id) + "\n\n" + text
    try:
        await context.bot.send_message(who.id, text, reply_markup=markup)
    except Forbidden:
        logger.info("Added to a channel by somebody who never started the bot: /channels is there for them")


async def channels_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat, user = update.effective_chat, update.effective_user
    if chat is None or chat.type != "private" or user is None:
        return
    lang = await i18n.get_lang(user.id, context)
    ids = (await asyncio.to_thread(channel_ids))[:CHANNELS_LISTED]
    runs = await asyncio.gather(*(_is_channel_admin(context.bot, cid, user.id) for cid in ids))
    theirs = [cid for cid, yes in zip(ids, runs) if yes]
    if not theirs:
        await update.effective_message.reply_text(i18n.t(lang, "channels_none"))
        return
    if len(theirs) == 1:
        settings, _ = await _channel_settings(theirs[0])
        text, markup = await _channel_screen(context.bot, lang, theirs[0], settings)
        await update.effective_message.reply_text(text, reply_markup=markup)
        return
    titles = await asyncio.gather(*(_channel_title(context.bot, cid) for cid in theirs))
    rows = [[InlineKeyboardButton(title, callback_data=channels.data(cid, "open"))]
            for cid, title in zip(theirs, titles)]
    await update.effective_message.reply_text(i18n.t(lang, "channels_pick"),
                                              reply_markup=InlineKeyboardMarkup(rows))


async def channel_settings_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query, user, chat = update.callback_query, update.effective_user, update.effective_chat
    parsed = channels.parse(query.data or "")
    if parsed is None or user is None or chat is None or chat.type != "private":
        await query.answer()
        return
    lang = await i18n.get_lang(user.id, context)
    chat_id, action, value = parsed
    settings, stored = await _channel_settings(chat_id)
    if not stored:
        await query.answer(i18n.t(lang, "channel_gone"), show_alert=True)
        return
    if not await _is_channel_admin(context.bot, chat_id, user.id):
        await query.answer(i18n.t(lang, "channel_admins_alert"), show_alert=True)
        return
    if action == "open":
        await query.answer()
    else:
        changed = channels.apply(settings, action, value)
        if changed is None:
            await query.answer()
            return
        note_active(context, user)
        await asyncio.to_thread(set_group_settings, chat_id, changed)
        settings = changed
        await query.answer(i18n.t(lang, "group_saved"))
    text, markup = await _channel_screen(context.bot, lang, chat_id, settings)
    try:
        await query.edit_message_text(text, reply_markup=markup)
    except BadRequest as exc:
        if "not modified" not in str(exc).lower():
            raise


async def unrecognized_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = await i18n.get_lang(update.effective_user.id, context)
    await update.message.reply_text(i18n.t(lang, "unrecognized_message"))


async def unknown_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = await i18n.get_lang(update.effective_user.id, context)
    await update.message.reply_text(i18n.t(lang, "unknown_command"))


async def _delete_my_data_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query is not None and query.data == ERASE_PREFIX + "yes":
        delivery_cache.forget_user(update.effective_user.id)
        context.user_data.pop("redeliver", None)
        context.user_data.pop("picks", None)
    return await delete_my_data_chosen(update, context)


async def _post_init(application):
    await tune_runtime(application)
    try:
        resolvers.load(await asyncio.to_thread(load_provider_health))
    except Exception:
        logger.warning("couldn't load provider health -- starting from scratch",
                       exc_info=True)
    if application.job_queue is not None:
        application.job_queue.run_repeating(
            _flush_provider_health,
            interval=PROVIDER_HEALTH_FLUSH_SECONDS,
            first=PROVIDER_HEALTH_FLUSH_SECONDS,
        )
    lifecycle.optional_now("daily route check", routewatch.schedule, application)
    await lifecycle.on_start(BOT_NAME)
    lifecycle.optional_now("clearing the delivery cache", delivery_cache.wipe)
    if application.job_queue is not None:
        application.job_queue.run_repeating(
            _sweep_delivery_cache, interval=DELIVERY_CACHE_SWEEP_SECONDS,
            first=DELIVERY_CACHE_SWEEP_SECONDS)
    await lifecycle.optional("command menu", publish_commands(application, BOT_COMMANDS, ADMIN_COMMANDS, ADMIN_IDS,
                                                               group=GROUP_COMMANDS))
    await lifecycle.optional("profile text", publish_profile(application))
    if big_files.CONFIGURED:
        task = asyncio.get_running_loop().create_task(_check_big_files())
        _background.add(task)
        task.add_done_callback(_background.discard)


DELIVERY_CACHE_SWEEP_SECONDS = 15


async def _sweep_delivery_cache(_context=None) -> None:
    try:
        delivery_cache.sweep()
    except Exception as exc:
        logger.warning("The delivery cache sweep failed (%s)", type(exc).__name__)


async def _check_big_files() -> None:
    if await big_files.verify() or big_files.VERIFIED is not False:
        return
    logger.warning("Large files are off: %s.", big_files.describe())
    family_link.report_event_soon("warning", "big_files", f"DownloaderBot: {big_files.describe()}.")


_background: set = set()


async def _post_stop(application):
    await _flush_provider_health()
    await net.close_client()
    await big_files.close()
    await lifecycle.on_stop(application)
    await flush_on_shutdown(application)


def main():
    if not BOT_TOKEN or not BOT_USERNAME:
        raise SystemExit("Set DBOT_TOKEN and DBOT_USERNAME environment variables first.")

    init_db()
    log_capacity()
    builder = (
        ApplicationBuilder().token(BOT_TOKEN)
        .post_init(_post_init).post_stop(_post_stop)
    )
    builder = shared_features.per_user_updates(builder, "DBOT")
    state = lifecycle.persistence()
    if state is not None:
        builder = builder.persistence(state)
    app = builder.build()
    lifecycle.install(app, BOT_NAME, ADMIN_IDS)
    lifecycle.add_work_source("channel post(s)", lambda: _channel_jobs)
    app.add_error_handler(error_handler)
    attach_problem_reports(app)
    app.add_handler(TypeHandler(Update, track_activity), group=-4)
    attach_flood_gate(app, ADMIN_IDS)
    attach_promo_claim(app)
    app.add_handler(MessageHandler(filters.ChatType.CHANNEL, channel_router), group=-1)
    app.add_handler(MessageHandler(filters.ChatType.GROUPS, group_router), group=-1)
    app.add_handler(MessageHandler((filters.TEXT | filters.REPLY) & ~filters.COMMAND,
                                   awaited_text_received), group=-1)
    app.add_handler(ChatMemberHandler(bot_membership_changed, ChatMemberHandler.MY_CHAT_MEMBER))
    app.add_handler(CallbackQueryHandler(group_settings_callback, pattern="^" + groups.PREFIX))
    app.add_handler(CallbackQueryHandler(channel_settings_callback, pattern="^" + channels.PREFIX))
    app.add_handler(CallbackQueryHandler(pick_callback, pattern="^" + PICK_PREFIX))

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("fullhelp", full_help_command(build_help_text, None, HELP_PAGES, HELP_LABELS)))
    app.add_handler(CallbackQueryHandler(help_toggle(build_help_text, None, HELP_PAGES, HELP_LABELS),
                                         pattern=HELP_TOGGLE_PATTERN))
    app.add_handler(CallbackQueryHandler(help_page_button(HELP_PAGES), pattern=HELP_PAGE_PATTERN))
    app.add_handler(CommandHandler("feedback", feedback_command))
    app.add_handler(CommandHandler("cancel", cancel_command))
    app.add_handler(CommandHandler("settings", settings_command))
    app.add_handler(CommandHandler("channels", channels_command))
    app.add_handler(CommandHandler("caption", caption_toggle))
    app.add_handler(CommandHandler("lossless", lossless_toggle))
    app.add_handler(CommandHandler("dbdump", dbdump_command))
    app.add_handler(CommandHandler("messageas", messageas_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("providers", providers_command))
    app.add_handler(CallbackQueryHandler(caption_toggle_callback, pattern="^caption:"))
    app.add_handler(CallbackQueryHandler(lossless_toggle_callback, pattern="^lossless:"))
    app.add_handler(CallbackQueryHandler(large_toggle_callback, pattern="^large:"))
    app.add_handler(CallbackQueryHandler(redeliver_callback, pattern="^redo:"))
    app.add_handler(CallbackQueryHandler(merge_callback, pattern="^merge:"))
    app.add_handler(CallbackQueryHandler(cancel_choice_callback, pattern="^cancelpick:"))
    app.add_handler(CallbackQueryHandler(language_chosen, pattern="^setlang:"))
    app.add_handler(CommandHandler("language", language_command))

    app.add_handler(CommandHandler("en", set_language_en))
    app.add_handler(CommandHandler("uz", set_language_uz))
    app.add_handler(CommandHandler("rus", set_language_rus))
    app.add_handler(CommandHandler("privacy", privacy_command))
    app.add_handler(CommandHandler("terms", terms_command))
    app.add_handler(CommandHandler("paysupport", paysupport_command))
    app.add_handler(CallbackQueryHandler(problem_report_callback, pattern=r"^rpt"))
    app.add_handler(CommandHandler("deletemydata", delete_my_data_command))
    app.add_handler(CallbackQueryHandler(_delete_my_data_chosen, pattern="^" + ERASE_PREFIX))
    app.add_handler(MessageHandler(filters.TEXT & filters.Regex(platforms.ANY_LINK_RE), handle_link))

    app.add_handler(CommandHandler("balance", balance_command))
    app.add_handler(CommandHandler("donate", donate_command))
    app.add_handler(CallbackQueryHandler(donate_amount_chosen, pattern="^donate:"))
    app.add_handler(CallbackQueryHandler(donate_fiat_amount_chosen, pattern="^donatefiat:"))
    app.add_handler(CallbackQueryHandler(donate_custom_button_chosen, pattern="^donatecustom:"))
    app.add_handler(PreCheckoutQueryHandler(donation_precheckout_callback))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, donation_payment_callback))

    app.add_handler(MessageHandler(filters.COMMAND, unknown_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, unrecognized_message))

    family_link.attach(app, BOT_NAME, "DownloaderBot", START_TIME)
    family_link.COMMANDS["probe"] = _bus_probe
    family_link.COMMAND_HELP["probe"] = (
        "probe [platform|url ...] -- try every download route from in here")
    family_link.COMMANDS["routewatch"] = _bus_routewatch
    family_link.COMMAND_HELP["routewatch"] = (
        "the daily route check, now: which platforms have a way in, and is yt-dlp current")
    family_link.COMMANDS["providers"] = _bus_providers
    family_link.COMMAND_HELP["providers"] = (
        "which download route is working and which is resting")
    attach_maintenance(app)

    logger.info("Bot starting (polling)...")
    app.run_polling(**lifecycle.polling_kwargs(
        timeout=POLL_TIMEOUT,
        allowed_updates=[Update.MESSAGE, Update.CALLBACK_QUERY, Update.PRE_CHECKOUT_QUERY,
                         Update.MY_CHAT_MEMBER],
    ))


if __name__ == "__main__":
    main()
