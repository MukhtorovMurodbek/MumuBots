import asyncio
import logging
import math
import os
import re
import tempfile
import time
import uuid
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from telegram import BotCommand, Update, InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    PreCheckoutQueryHandler,
    ContextTypes,
    TypeHandler,
    filters,
)

import family_link
import i18n
import lifecycle
import shared_features
from live_message import LiveMessage, edit_in_place
from db import (
    init_db,
    record_star_invoice,
    get_star_ledger,
    dump_database_csv_zip,
    count_active_users_since,
    get_user_language,
    set_user_language,
)
import big_files
import convert_utils
import raw_convert
import ocr_convert
import formats
import jobs
from convert_runner import ConvertRunner, download_card
from convert_utils import (
    BOT_API_MAX_MB,
    FREE_UNDER_MB,
    gif_is_animated,
    max_file_mb_in,
    price_for_size,
    price_for_job,
    document_pages,
    price_tiers_text,
    ConversionError as ConvertError,
)
from shared_features import (
    body,
    collapsed,
    heading,
    joined,
    lead_in,
    reply_formatted,
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
    balance_command,
    STARS_SANDBOX,
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
    donation_precheckout,
    donation_payment_received,
    credit_stars_payment,
    payer_language,
    setup_logging,
    error_handler,
    track_activity,
    build_status_text,
    format_ledger_amount,
)

setup_logging(__file__)
logger = logging.getLogger(__name__)

START_TIME = datetime.now(timezone.utc)

BOT_TOKEN = os.environ.get("CBOT_TOKEN")
BOT_USERNAME = (os.environ.get("CBOT_USERNAME") or "").strip().lstrip("@") or None
ADMIN_IDS = {int(x) for x in os.environ.get("CBOT_ADMIN_ID", "").split(",") if x.strip()}

LOCAL_BOT_API_URL = os.environ.get("LOCAL_BOT_API_URL")
LOCAL_BOT_API_MODE = os.environ.get("LOCAL_BOT_API_MODE", "true").lower() == "true"

BOT_NAME = "convertbot"

POLL_TIMEOUT = int(os.environ.get("POLL_TIMEOUT", "30"))


def _is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


async def _deny(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await unknown_command(update, context)


UPLOAD_DIR = Path(os.environ.get("CONVERT_UPLOAD_DIR") or (Path(tempfile.gettempdir()) / "convertbot"))
PENDING_TTL_SECONDS = int(os.environ.get("CONVERT_PENDING_TTL_SECONDS", "900"))


_EXT_RE = re.compile(r"[^a-z0-9]")


def _safe_ext(ext: str | None, limit: int = 8) -> str:
    return _EXT_RE.sub("", (ext or "").lower())[:limit]


def _new_upload_path(user_id: int, ext: str) -> Path:
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    return UPLOAD_DIR / f"{user_id}-{uuid.uuid4().hex}.{_safe_ext(ext) or 'bin'}"


def _unlink(path) -> None:
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:
        logger.debug("Could not remove %s", path, exc_info=True)


def _discard_pending(context: ContextTypes.DEFAULT_TYPE) -> None:
    state = context.user_data.pop("convert", None)
    busy = jobs.active_paths()
    for path in (state or {}).get("paths", []):
        if str(path) not in busy:
            _unlink(path)


def _sweep_uploads(max_age_seconds: float = PENDING_TTL_SECONDS, keep=(), busy_stems=()) -> int:
    if not UPLOAD_DIR.is_dir():
        return 0
    sweep_all = max_age_seconds <= 0
    cutoff = time.time() - max_age_seconds
    job_cutoff = time.time() - max(max_age_seconds, jobs.TIME_LIMIT_S + 300)
    removed = 0
    for path in UPLOAD_DIR.iterdir():
        try:
            if str(path) in keep:
                continue
            if any(path.name == stem or path.name.startswith((stem + "-", stem + "."))
                   for stem in busy_stems):
                continue
            made_by_a_job = path.is_dir() or "-source." in path.name
            if path.is_file() and (sweep_all or path.stat().st_mtime < (job_cutoff if made_by_a_job else cutoff)):
                path.unlink()
                removed += 1
            elif (path.is_dir() and not path.is_symlink() and path.name.endswith("-contents")
                  and (sweep_all or path.stat().st_mtime < job_cutoff)):
                convert_utils.remove_tree(path)
                if not path.exists():
                    removed += 1
        except OSError:
            continue
    return removed


async def _sweep_uploads_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    removed = await asyncio.to_thread(
        _sweep_uploads, max_age_seconds=PENDING_TTL_SECONDS, keep=jobs.active_paths(),
        busy_stems=jobs.active_stems())
    if removed:
        logger.info("Swept %d abandoned upload(s).", removed)


async def _conversions_in_flight_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        refunded = await _runner(context.application).keep_alive_and_recover()
    except Exception:
        logger.debug("Could not check conversions in flight", exc_info=True)
        return
    if refunded:
        logger.warning("Refunded %d conversion(s) lost to a restart.", refunded)


def build_help_text(lang: str, full: bool = False) -> str:
    if not full:
        text = i18n.t(lang, "help_brief").rstrip("\n")
    else:
        text = i18n.t(lang, "help_text", pricing=price_tiers_text(lang)).rstrip("\n")
    blurb = sibling_bots_blurb(BOT_NAME, lang)
    return text + "\n\n" + blurb if blurb else text


def _help_page_convert(lang: str) -> str:
    return i18n.t(lang, "helppage_convert",
                  max_mb=convert_utils.MAX_FILE_MB, pricing=price_tiers_text(lang),
                  free_mb=convert_utils.FREE_UNDER_MB, batch=formats.MAX_BATCH,
                  ocr_pages=ocr_convert.OCR_MAX_PAGES, ocr_free_pages=ocr_convert.OCR_FREE_PAGES,
                  minutes=max(1, PENDING_TTL_SECONDS // 60), least=convert_utils.least_paid_price())


def _help_topup_ladder(lang: str) -> tuple[str, str]:
    per_star = family_link.TOPUP_MULTIPLIER
    rate = f"{per_star:g}"
    if not family_link.TOPUP_LADDER:
        return i18n.t(lang, "helppage_recharge_flat", rate=rate), ""
    lines = []
    for number, (stars, multiplier) in enumerate(family_link.TOPUP_LADDER):
        extra = per_star * multiplier - per_star
        split = i18n.t(lang, "helppage_recharge_bonus_part", rate=rate, bonus=f"{extra:g}") if extra > 0 else ""
        lines.append(i18n.t(lang, "helppage_recharge_step_next" if number else "helppage_recharge_step_first",
                            stars=stars, each=f"{per_star * multiplier:g}", split=split))
    lines.append(i18n.t(lang, "helppage_recharge_step_after", rate=rate))
    return "\n".join(lines), i18n.t(lang, "helppage_recharge_bonus_rule", rate=rate,
                                    days=family_link.BONUS_EXPIRY_DAYS)


def _help_page_recharge(lang: str) -> str:
    ladder, bonus_rule = _help_topup_ladder(lang)
    quote = family_link.quote_credit(0, shared_features.DONATE_STAR_OPTIONS[0])
    if quote["bonus"] > 0:
        example = i18n.t(lang, "helppage_recharge_example", stars=quote["stars"], total=quote["total"],
                         base=quote["base"], bonus=quote["bonus"])
    else:
        example = i18n.t(lang, "helppage_recharge_example_flat", stars=quote["stars"], total=quote["total"])
    text = i18n.t(lang, "helppage_recharge", options=" · ".join(str(n) for n in shared_features.DONATE_STAR_OPTIONS),
                  ladder=ladder, bonus_rule=bonus_rule, example=example)
    return text if bonus_rule else text.replace(ladder + "\n\n", ladder + "\n", 1)


def _help_page_balance(lang: str) -> str:
    quote = family_link.quote_credit(0, shared_features.DONATE_STAR_OPTIONS[0])
    if quote["bonus"] > 0:
        example = i18n.t(lang, "helppage_balance_example", stars=quote["stars"], base=quote["base"],
                         bonus=quote["bonus"])
    else:
        example = i18n.t(lang, "helppage_balance_example_one", stars=quote["stars"], total=quote["total"])
    return i18n.t(lang, "helppage_balance", rows=shared_features.BALANCE_RECENT_ROWS,
                  days=family_link.BONUS_EXPIRY_DAYS, example=example)


HELP_PAGES = {"convert": _help_page_convert, "recharge": _help_page_recharge, "balance": _help_page_balance}
HELP_ALIASES = {"donate": "recharge", "mystars": "balance"}


BOT_COMMANDS = [
    BotCommand("start", "What this bot does, and how to start"),
    BotCommand("language", "Choose the language / Tilni tanlash / Выбрать язык"),
    BotCommand("convert", "Convert a file — or just send one"),
    BotCommand("formats", "Every format read and written"),
    BotCommand("cancel", "Stop the current step"),
    BotCommand("help", "Everything this bot does"),
    BotCommand("feedback", "Suggest something, or report a problem"),
    BotCommand("balance", "⚡ credit, and where it went"),
    BotCommand("recharge", "Top up ⚡ credit"),
    BotCommand("paysupport", "Trouble with a payment"),
    BotCommand("privacy", "What is kept, and for how long"),
    BotCommand("terms", "What this bot may be used for"),
    BotCommand("deletemydata", "Delete all stored data"),
]

ADMIN_COMMANDS = [
    BotCommand("status", "🔒 Uptime, host, errors, active users"),
    BotCommand("stars", "🔒 The Stars ledger: paid, free and refunded"),
    BotCommand("messageas", "🔒 messageas <user_id> <text> — DM as this bot"),
    BotCommand("dbdump", "🔒 This bot's tables as a zip of CSVs"),
]


def help_rows(lang: str) -> list:
    return [sibling_bots_keyboard_row(BOT_NAME)]


async def _reply(update: Update, text: str, **kwargs):
    if update.message:
        return await LiveMessage.reply_to(update.message, text, **kwargs)
    return await edit_in_place(update.callback_query.message, update.get_bot(), text, **kwargs)


async def _continue_start(update: Update, context: ContextTypes.DEFAULT_TYPE, lang: str):
    await _reply(
        update,
        i18n.t(lang, "start_greeting") + build_help_text(lang),
        reply_markup=help_keyboard(lang, False, help_rows(lang), HELP_PAGES),
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
                                        link_preview_options=NO_PREVIEW)
        return
    full = wants_full_help(context)
    await update.message.reply_text(build_help_text(lang, full=full),
                                    reply_markup=help_keyboard(lang, full, help_rows(lang), HELP_PAGES))


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


def _ext_of(filename: str | None, fallback: str) -> str:
    compound = formats.compound_extension(filename)
    if compound:
        return compound
    if filename and "." in filename:
        return formats.normalise(_safe_ext(filename.rsplit(".", 1)[-1])) or fallback
    return fallback


def _ext_of_file(file_obj, fallback: str) -> str:
    ext = _ext_of(getattr(file_obj, "file_name", None), "")
    if ext:
        return ext
    import mimetypes
    guessed = mimetypes.guess_extension(getattr(file_obj, "mime_type", None) or "")
    guessed = formats.normalise(guessed) if guessed else ""
    if guessed and formats.is_supported_source(guessed):
        return guessed
    return fallback


async def convert_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = await i18n.get_lang(update.effective_user.id, context)
    await update.message.reply_text(
        i18n.t(lang, "convert_start_prompt", max_mb=convert_utils.MAX_FILE_MB, pricing=price_tiers_text(lang)),
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton(i18n.t(lang, "btn_what_formats"), callback_data="fmtall"),
        ]]),
    )


def _formats_overview(lang: str) -> tuple[str, InlineKeyboardMarkup]:
    sources, targets, pairs = formats.counts()
    text = joined(
        heading(i18n.t(lang, "formats_title")),
        body(i18n.t(lang, "formats_intro", sources=sources, targets=targets, pairs=pairs)),
        collapsed(i18n.t(lang, "formats_highlights")),
    )
    available = [category for category in formats.CATEGORIES if formats.supported_sources(category)]
    buttons = [
        InlineKeyboardButton(i18n.t(lang, f"category_{category}").title(),
                             callback_data=f"fmtcat:{category}")
        for category in available
    ]
    rows = [buttons[index:index + 3] for index in range(0, len(buttons), 3)]
    return text, InlineKeyboardMarkup(rows)


def _formats_category(lang: str, category: str) -> tuple[str, InlineKeyboardMarkup]:
    label = i18n.t(lang, f"category_{category}")
    reads = ", ".join(ext.upper() for ext in formats.supported_sources(category))
    writes = ", ".join(i18n.target_label(lang, ext) for ext in formats.supported_targets(category))
    text = joined(
        heading(i18n.t(lang, "formats_category_title", category=label.title())),
        lead_in(f"{i18n.t(lang, 'formats_reads')}: {reads}"),
        lead_in(f"{i18n.t(lang, 'formats_writes')}: {writes}"),
    )
    back = InlineKeyboardMarkup([[
        InlineKeyboardButton(i18n.t(lang, "btn_formats_back"), callback_data="fmtall"),
    ]])
    return text, back


def _format_lookup(lang: str, word: str) -> str | None:
    ext = formats.find_format(word)
    if not ext:
        return None
    targets = formats.targets_for(ext)
    if not targets:
        return i18n.t(lang, "formats_lookup_none", ext=ext)
    return i18n.t(
        lang, "formats_lookup", ext=ext,
        category=i18n.t(lang, f"category_{formats.category_of(ext)}"),
        targets=", ".join(i18n.target_label(lang, target) for target in targets),
    )


async def formats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = await i18n.get_lang(update.effective_user.id, context)
    if context.args:
        answer = _format_lookup(lang, " ".join(context.args))
        await reply_formatted(
            update.message,
            body(answer or i18n.t(lang, "formats_lookup_unknown",
                                  ext=_safe_ext(context.args[0].lstrip(".")) or "?")),
        )
        return
    text, keyboard = _formats_overview(lang)
    await reply_formatted(update.message, text, reply_markup=keyboard)


async def formats_overview_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    lang = await i18n.get_lang(update.effective_user.id, context)
    await query.answer()
    text, keyboard = _formats_overview(lang)
    await edit_in_place(query.message, context.bot, text,
                        parse_mode=ParseMode.HTML, reply_markup=keyboard)


async def formats_category_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    lang = await i18n.get_lang(update.effective_user.id, context)
    category = query.data.split(":", 1)[1]
    if category not in formats.CATEGORIES:
        await query.answer()
        return
    await query.answer()
    text, keyboard = _formats_category(lang, category)
    await edit_in_place(query.message, context.bot, text,
                        parse_mode=ParseMode.HTML, reply_markup=keyboard)


async def _cancel_items(update: Update, context: ContextTypes.DEFAULT_TYPE, lang: str) -> list[CancelItem]:
    items = cancel_items(context, lang)
    for job in jobs.jobs_for(update.effective_user.id):
        items.append(CancelItem(
            f"job_{job.id}",
            i18n.t(lang, "cancel_item_job_running" if job.state == "running" else "cancel_item_job_queued",
                   target=i18n.target_label(lang, job.target_ext), src=job.src_ext.upper(),
                   size=f"{job.size / 1024 / 1024:.1f}"),
            i18n.t(lang, "cancel_button_job", target=i18n.target_label(lang, job.target_ext)),
        ))
    state = context.user_data.get("convert")
    if state:
        items.append(CancelItem(
            "conversion",
            i18n.t(lang, "cancel_item_conversion", ext=state.get("src_ext", "?")),
            i18n.t(lang, "cancel_button_conversion"),
        ))
    return items


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

    items = {item.key: item for item in await _cancel_items(update, context, lang)}
    keys = list(items) if key == CANCEL_PICK_ALL else [key]
    stopped = []
    for chosen in keys:
        item = items.get(chosen)
        if item is None:
            continue
        if chosen == "conversion":
            _discard_pending(context)
            stopped.append(item.label)
        elif chosen.startswith("job_"):
            if jobs.cancel(chosen[len("job_"):], user_id=update.effective_user.id):
                stopped.append(item.label)
        elif cancel_shared_item(context, lang, chosen):
            stopped.append(item.label)
    await finish_cancel_choice(update, context, lang, stopped)


async def cancel_conversion_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    lang = await i18n.get_lang(update.effective_user.id, context)
    _discard_pending(context)
    await query.answer()
    await edit_in_place(query.message, context.bot, i18n.t(lang, "cancelled"))


BATCH_WAIT_SECONDS = float(os.environ.get("CONVERT_BATCH_WAIT_SECONDS") or 2.0)


def _batch_job_name(chat_id: int, group_id: str) -> str:
    return f"batch:{chat_id}:{group_id}"


def _too_large_text(lang: str, size: int, limit_mb: int) -> str:
    if limit_mb < convert_utils.MAX_FILE_MB:
        return i18n.t(lang, "file_too_large_in_group", limit=limit_mb, max_mb=convert_utils.MAX_FILE_MB)
    if limit_mb <= BOT_API_MAX_MB and not LOCAL_BOT_API_URL:
        return i18n.t(lang, "file_too_large_download", max_mb=limit_mb)
    return i18n.t(lang, "file_too_large_convert", size=f"{size / 1024 / 1024:.1f}", max_mb=limit_mb)


_PROGRESS_EVERY_S = 3.0
_CARD_FROM_BYTES = 2 * 1024 * 1024


def _download_progress(context, status, lang: str):
    began = time.monotonic()
    last = {"at": 0.0, "text": ""}

    async def progress(done: int, total: int) -> None:
        if status is None or not total:
            return
        now = time.monotonic()
        if now - last["at"] < _PROGRESS_EVERY_S:
            return
        text = download_card(lang, done, total, now - began)
        if text != last["text"]:
            last.update(at=now, text=text)
            await status.set(context.bot, text)
    return progress


GET_FILE_READ_S = float(os.environ.get("CONVERT_GET_FILE_READ_S") or 90)
DOWNLOAD_TRIES = 3


class _Interrupted(Exception):
    pass


def _worth_another_try(exc: BaseException) -> bool:
    from telegram.error import BadRequest, NetworkError, RetryAfter
    if isinstance(exc, BadRequest):
        return False
    return isinstance(exc, (NetworkError, RetryAfter, _Interrupted, asyncio.TimeoutError))


async def _download_bot_api(context, file_obj, path, declared_size: int, status, lang: str) -> None:
    for attempt in range(1, DOWNLOAD_TRIES + 1):
        try:
            await _download_bot_api_once(context, file_obj, path, declared_size, status, lang)
            return
        except Exception as exc:
            if attempt == DOWNLOAD_TRIES or not _worth_another_try(exc):
                logger.warning("Download gave up after %d try(s) at %s: %s", attempt,
                               getattr(exc, "mumu_step", "getFile"), type(exc).__name__)
                if isinstance(exc, _Interrupted):
                    raise ConvertError(str(exc)) from None
                raise
            wait = 2.0 * attempt
            retry_after = getattr(exc, "retry_after", None)
            if retry_after is not None:
                seconds = (retry_after.total_seconds() if isinstance(retry_after, timedelta)
                           else float(retry_after))
                wait = min(max(wait, seconds + 0.5), 30.0)
            logger.info("Download try %d of %d failed (%s); trying again in %.0f s",
                        attempt, DOWNLOAD_TRIES, type(exc).__name__, wait)
            await asyncio.sleep(wait)


async def _download_bot_api_once(context, file_obj, path, declared_size: int, status, lang: str) -> None:
    tg_file = await context.bot.get_file(file_obj.file_id, read_timeout=GET_FILE_READ_S)
    try:
        await _fetch_file(tg_file, path, declared_size, status, context, lang)
    except Exception as exc:
        try:
            exc.mumu_step = "the download"
        except Exception:
            pass
        raise


async def _fetch_file(tg_file, path, declared_size: int, status, context, lang: str) -> None:
    url = getattr(tg_file, "file_path", None) or ""
    read_s = max(60.0, declared_size / 100_000)
    if (status is None or LOCAL_BOT_API_URL or not url.startswith("https://")
            or declared_size < _CARD_FROM_BYTES):
        await tg_file.download_to_drive(custom_path=path, read_timeout=read_s, write_timeout=60,
                                        connect_timeout=30)
        return
    import httpx
    progress = _download_progress(context, status, lang)
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=60.0)) as client:
            async with client.stream("GET", url) as response:
                response.raise_for_status()
                total = int(response.headers.get("content-length") or declared_size or 0)
                done = 0
                with open(path, "wb") as handle:
                    async for chunk in response.aiter_bytes(256 * 1024):
                        handle.write(chunk)
                        done += len(chunk)
                        await progress(done, total)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code >= 500:
            raise _Interrupted(f"Telegram's file server answered {exc.response.status_code}") from None
        raise ConvertError(f"Telegram's file server answered {exc.response.status_code}") from None
    except httpx.HTTPError as exc:
        raise _Interrupted(f"the download was interrupted ({type(exc).__name__})") from None


async def _download_big(context, msg, path, declared_size: int, status, lang: str) -> None:
    progress = _download_progress(context, status, lang)

    async def waiting() -> None:
        if status is not None:
            await status.set(context.bot, i18n.t(lang, "transfer_waiting"))

    try:
        await big_files.download(msg.chat_id, msg.message_id, path, declared_size, progress, waiting)
    except big_files.Stalled:
        raise ConvertError(i18n.t(lang, "download_stalled", seconds=round(big_files.STALL_S))) from None
    except asyncio.TimeoutError:
        raise ConvertError(i18n.t(lang, "download_too_slow",
                                  minutes=big_files.transfer_limit_s(declared_size) // 60)) from None


async def convert_receive_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = await i18n.get_lang(update.effective_user.id, context)
    msg = update.message
    if msg.animation:
        file_obj = msg.animation
        ext = _ext_of_file(file_obj, "mp4")
    elif msg.document:
        file_obj = msg.document
        ext = _ext_of_file(file_obj, "")
    elif msg.photo:
        file_obj = msg.photo[-1]
        ext = "jpg"
    elif msg.video:
        file_obj = msg.video
        ext = _ext_of(getattr(file_obj, "file_name", None), "mp4")
    elif msg.audio:
        file_obj = msg.audio
        ext = _ext_of(getattr(file_obj, "file_name", None), "mp3")
    elif msg.voice:
        file_obj = msg.voice
        ext = "ogg"
    else:
        return

    if not ext:
        await msg.reply_text(
            i18n.t(lang, "unknown_extension"), reply_markup=_what_formats_keyboard(lang),
        )
        return
    if not formats.is_supported_source(ext):
        await msg.reply_text(
            i18n.t(lang, "unsupported_format", ext=formats.normalise(ext)),
            reply_markup=_what_formats_keyboard(lang),
        )
        return

    declared_size = file_obj.file_size or 0
    limit_mb = max_file_mb_in(msg.chat_id)
    if declared_size and declared_size > limit_mb * 1024 * 1024:
        await msg.reply_text(_too_large_text(lang, declared_size, limit_mb))
        return

    async with jobs.storage_gate():
        problem = await asyncio.to_thread(
            jobs.storage_problem, update.effective_user.id, declared_size, UPLOAD_DIR)
        reservation = None if problem is not None else jobs.reserve_storage(
            update.effective_user.id, declared_size)
    if problem is not None:
        which, used, limit = problem
        key = "storage_full_user" if which == "user" else "storage_full_global"
        await msg.reply_text(i18n.t(lang, key, used=used, limit=limit))
        return
    try:
        await _receive_with_room(update, context, msg, file_obj, ext, lang, declared_size, reservation)
    finally:
        jobs.release_storage(reservation)


async def _receive_with_room(update: Update, context: ContextTypes.DEFAULT_TYPE, msg, file_obj, ext: str,
                             lang: str, declared_size: int, reservation) -> None:
    group_id = msg.media_group_id
    state = context.user_data.get("convert")
    joining = bool(
        group_id and state and state.get("group_id") == group_id
        and state.get("state") == "awaiting_format"
    )
    if not joining:
        _discard_pending(context)
    elif formats.normalise(state["src_ext"]) != ext:
        await msg.reply_text(i18n.t(lang, "batch_mixed_formats"))
        return
    elif len(state["paths"]) >= formats.MAX_BATCH:
        return
    elif state.get("over"):
        state["declared"] = state.get("declared", state["size"]) + declared_size
        _schedule_batch(context, msg, update.effective_user, group_id)
        return
    elif state.get("declared", state["size"]) + declared_size > convert_utils.MAX_FILE_MB * 1024 * 1024:
        state["declared"] = state.get("declared", state["size"]) + declared_size
        state["over"] = True
        _schedule_batch(context, msg, update.effective_user, group_id)
        return

    status = None
    if not joining:
        status = await LiveMessage.reply_to(msg, download_card(lang, 0, 0, 0))
    path = _new_upload_path(update.effective_user.id, ext)
    try:
        async with lifecycle.busy(msg.chat_id, i18n.t(lang, "restarting_send_again")):
            if declared_size > big_files.BOT_API_DOWNLOAD_BYTES and not LOCAL_BOT_API_URL:
                await _download_big(context, msg, path, declared_size, status, lang)
            else:
                await _download_bot_api(context, file_obj, path, declared_size, status, lang)
    except big_files.Unavailable as exc:
        _unlink(path)
        logger.warning("Big file not fetched, MTProto unavailable: %s", exc)
        text = i18n.t(lang, "big_download_unavailable", limit=BOT_API_MAX_MB)
        if status is not None:
            await status.set(context.bot, text)
        else:
            await msg.reply_text(text)
        return
    except Exception as exc:
        _unlink(path)
        if status is not None:
            await status.set(context.bot, i18n.t(lang, "download_failed", error=exc))
        else:
            await msg.reply_text(i18n.t(lang, "download_failed", error=exc))
        return
    finally:
        jobs.release_storage(reservation)
    if status is not None:
        await status.delete(context.bot)

    if joining:
        _join_album(state, path)
        _schedule_batch(context, msg, update.effective_user, group_id)
        return

    if group_id and context.job_queue is not None:
        context.user_data["convert"] = {
            "state": "awaiting_format",
            "paths": [str(path)],
            "src_ext": ext,
            "size": path.stat().st_size,
            "declared": declared_size or path.stat().st_size,
            "group_id": group_id,
        }
        _schedule_batch(context, msg, update.effective_user, group_id)
        return

    await _present_conversion_options(msg, context, update.effective_user, [path], ext)


def _join_album(state: dict, path: Path) -> None:
    state["paths"].append(str(path))
    state["size"] += path.stat().st_size
    state["ocr_pages"] = 0
    state["pages"] = None


def _schedule_batch(context: ContextTypes.DEFAULT_TYPE, message, user, group_id: str) -> None:
    if context.job_queue is None:
        return
    name = _batch_job_name(message.chat_id, group_id)
    for job in context.job_queue.get_jobs_by_name(name):
        job.schedule_removal()
    context.job_queue.run_once(
        _present_batch_job, BATCH_WAIT_SECONDS, name=name,
        chat_id=message.chat_id, user_id=user.id,
        data={"message": message, "user": user, "group_id": group_id},
    )


async def _present_batch_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    data = context.job.data
    state = context.user_data.get("convert")
    if not state or state.get("group_id") != data["group_id"]:
        return
    paths = [Path(path) for path in state["paths"]]
    context.user_data.pop("convert", None)
    if state.get("over"):
        for path in paths:
            _unlink(path)
        lang = await i18n.get_lang(data["user"].id, context)
        await data["message"].reply_text(i18n.t(
            lang, "file_too_large_convert",
            size=f"{state.get('declared', state['size']) / 1024 / 1024:.1f}", max_mb=convert_utils.MAX_FILE_MB))
        return
    await _present_conversion_options(
        data["message"], context, data["user"], paths, state["src_ext"],
        group_id=data["group_id"],
    )


def _what_formats_keyboard(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(i18n.t(lang, "btn_what_formats"), callback_data="fmtall"),
    ]])


def _format_rows(lang: str, targets: list[str], expanded: bool, total: int,
                 keep: bool | None = None, ocr: str | None = None) -> list[list]:
    buttons = [InlineKeyboardButton(i18n.target_label(lang, target), callback_data=f"convfmt:{target}")
               for target in targets]
    rows = [buttons[index:index + 3] for index in range(0, len(buttons), 3)]
    if total > len(targets):
        rows.append([InlineKeyboardButton(i18n.t(lang, "btn_more_formats"), callback_data="convmore")])
    elif expanded:
        rows.append([InlineKeyboardButton(i18n.t(lang, "btn_fewer_formats"), callback_data="convless")])
    if ocr is not None:
        rows.append([InlineKeyboardButton(
            i18n.t(lang, "ocr_lang_toggle", name=i18n.t(lang, f"ocr_lang_{ocr}")), callback_data="convocr")])
    if keep is not None:
        rows.append([InlineKeyboardButton(
            i18n.t(lang, "keep_toggle_on" if keep else "keep_toggle_off"), callback_data="convkeep")])
    rows.append([InlineKeyboardButton(i18n.t(lang, "btn_cancel_conversion"), callback_data="convcancel")])
    return rows


def human_size(size_bytes: float) -> str:
    if size_bytes >= 1024 * 1024:
        megabytes = size_bytes / 1024 / 1024
        return f"{megabytes:.1f} MB" if megabytes < 10 else f"{megabytes:.0f} MB"
    return f"{max(1, round(size_bytes / 1024))} KB"


def _options_text(lang: str, paths, src_ext: str, size: int,
                  animated: bool | None = None, notes=()) -> str:
    if len(paths) > 1:
        text = i18n.t(lang, "convert_options_prompt_batch", count=len(paths),
                      size=human_size(size), price_line=i18n.t(lang, "price_after_format"))
    else:
        text = i18n.t(lang, "convert_options_prompt", size=human_size(size),
                      category=i18n.t(lang, f"category_{formats.category_of(src_ext, animated)}"),
                      price_line=i18n.t(lang, "price_after_format"))
    for note in notes:
        text += "\n\n" + note
    return text


def _quote_text(lang: str, src_ext: str, target_ext: str, in_bytes: int,
                out_bytes: int, price: int, balance: int | None,
                send_limit: int = jobs.SEND_LIMIT_BYTES) -> str:
    lines = [f"{src_ext.upper()} → {i18n.target_label(lang, target_ext)}",
             i18n.t(lang, "quote_line_sizes", in_size=human_size(in_bytes),
                    out_size=human_size(out_bytes))]
    if price == 0:
        lines.append(i18n.t(lang, "quote_free", mb=FREE_UNDER_MB))
        return "\n".join(lines)
    lines.append(i18n.t(lang, "quote_cost", price=price))
    lines.append("")
    lines.append(i18n.t(lang, "quote_fixed"))
    if balance is not None:
        lines.append(i18n.t(lang, "quote_balance", balance=max(balance, 0)))
    lines.append("")
    lines.append(i18n.t(lang, "credit_policy_note"))
    lines.append("")
    lines.append(i18n.t(lang, "send_limit_note", mb=send_limit // (1024 * 1024)))
    return "\n".join(lines)


def _quote_keyboard(lang: str, price: int) -> InlineKeyboardMarkup:
    label = (i18n.t(lang, "btn_quote_free") if price == 0
             else i18n.t(lang, "btn_quote_go", price=price))
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(label, callback_data="convgo")],
        [InlineKeyboardButton(i18n.t(lang, "btn_quote_back"), callback_data="convback")],
        [InlineKeyboardButton(i18n.t(lang, "btn_cancel_conversion"), callback_data="convcancel")],
    ])


def _ocr_withheld(state: dict) -> set[str]:
    if len(state["paths"]) > 1:
        if formats.category_of(state["src_ext"], state.get("animated")) == formats.IMAGE:
            return {"txt", "ocrpdf"}
        return {"ocrpdf"}
    if state["src_ext"] != "pdf":
        return set()
    pages = state.get("ocr_pages") or 0
    return set() if 0 < pages <= ocr_convert.OCR_MAX_PAGES else {"ocrpdf"}


def _offered_targets(state: dict) -> list[str]:
    targets = formats.targets_for(state["src_ext"], state.get("animated"))
    if len(state["paths"]) > 1:
        targets = [target for target in targets if target != state["src_ext"]]
    blocked = set(state.get("blocked") or ()) | set(state.get("too_long") or ()) | _ocr_withheld(state)
    return [target for target in targets if target not in blocked]


def _ocr_choice(context: ContextTypes.DEFAULT_TYPE, lang: str) -> str:
    choice = context.user_data.get("ocr_lang")
    return choice if choice in ocr_convert.CHOICES else ocr_convert.default_choice(lang)


def _ocr_row(context: ContextTypes.DEFAULT_TYPE, lang: str, state: dict, shown) -> str | None:
    pages = state.get("ocr_pages") or 0
    if any(convert_utils.ocr_job_pages(state["src_ext"], target, pages, len(state["paths"])) for target in shown):
        return _ocr_choice(context, lang)
    return None


async def _present_conversion_options(
    message, context: ContextTypes.DEFAULT_TYPE, user, paths, src_ext: str,
    group_id: str | None = None,
):
    lang = await i18n.get_lang(user.id, context)
    src_ext = formats.normalise(src_ext)
    animated = gif_is_animated(paths[0]) if src_ext in formats.ANIMATED_CAPABLE else None
    if not formats.is_supported_source(src_ext):
        for path in paths:
            _unlink(path)
        await message.reply_text(
            i18n.t(lang, "unsupported_format", ext=src_ext),
            reply_markup=_what_formats_keyboard(lang),
        )
        return

    size = sum(Path(path).stat().st_size for path in paths)
    price = price_for_size(size)
    if price is None:
        for path in paths:
            _unlink(path)
        await message.reply_text(
            i18n.t(lang, "file_too_large_convert", size=f"{size / 1024 / 1024:.1f}",
                   max_mb=convert_utils.MAX_FILE_MB)
        )
        return

    megapixels = None
    raw_info = None
    if len(paths) == 1 and src_ext in formats.RAW_FORMATS:
        raw_info = await jobs.describe_raw(paths[0], src_ext)
        if raw_info is not None:
            megapixels = raw_info.out_megapixels
            if raw_info.megapixels > raw_convert.MAX_MP:
                for path in paths:
                    _unlink(path)
                await message.reply_text(i18n.t(lang, "raw_too_big", mp=f"{raw_info.megapixels:.0f}",
                                                max=f"{raw_convert.MAX_MP:.0f}"))
                return
    elif len(paths) == 1 and formats.category_of(src_ext, animated) == formats.IMAGE:
        megapixels = await jobs.describe_image(paths[0])
        if megapixels is not None and megapixels > jobs.MAX_MEGAPIXELS:
            for path in paths:
                _unlink(path)
            await message.reply_text(i18n.t(lang, "too_many_megapixels",
                                            mp=f"{megapixels:.0f}", max=jobs.MAX_MEGAPIXELS))
            return

    pages = None
    ocr_pages = 0
    if len(paths) == 1 and src_ext == "pdf" and formats.category_of(src_ext, animated) == formats.DOCUMENT:
        counted = await jobs.describe_pdf(paths[0])
        if counted is not None:
            pages = counted["pages"]
            if formats.met("ocr") and counted["scanned"] is not None:
                ocr_pages = counted["scanned"]
    elif len(paths) == 1 and formats.category_of(src_ext, animated) == formats.DOCUMENT:
        pages = await asyncio.to_thread(document_pages, paths[0], src_ext)
    elif (len(paths) == 1 and formats.category_of(src_ext, animated) == formats.IMAGE
          and src_ext not in formats.RAW_FORMATS and src_ext != "svg" and formats.met("ocr")):
        ocr_pages = 1

    if animated and src_ext in formats.ANIMATED_CAPABLE:
        frames = max([await asyncio.to_thread(convert_utils.animation_frames, path) for path in paths])
        if frames > convert_utils.MAX_FRAMES:
            for path in paths:
                _unlink(path)
            await message.reply_text(i18n.t(lang, "animation_too_many_frames", frames=frames,
                                            max=convert_utils.MAX_FRAMES))
            return

    too_long = []
    if (formats.category_of(src_ext, animated) == formats.VIDEO
            and src_ext not in formats.ANIMATED_CAPABLE):
        seconds = await asyncio.to_thread(convert_utils.animation_seconds, paths)
        if seconds is None or seconds > convert_utils.ANIMATION_MAX_SECONDS:
            too_long = [target for target in convert_utils.ANIMATION_TARGETS
                        if target in formats.targets_for(src_ext, animated)]

    state = {
        "state": "awaiting_format",
        "paths": [str(path) for path in paths],
        "src_ext": src_ext,
        "size": size,
        "price": 0,
        "group_id": group_id,
        "animated": animated,
        "megapixels": megapixels,
        "pages": pages,
        "ocr_pages": ocr_pages,
        "too_long": too_long,
    }
    every_target = _offered_targets(state)
    blocked = jobs.unsendable_targets(megapixels, every_target, size, message.chat_id,
                                      raw=raw_info is not None)
    state["blocked"] = blocked
    notes = []
    if blocked:
        notes.append(i18n.t(lang, "too_big_to_send_note",
                            formats=", ".join(target.upper() for target in blocked),
                            mp=f"{megapixels:.0f}", limit=jobs.send_ceiling_mb(message.chat_id)))
    if too_long:
        notes.append(i18n.t(lang, "animation_too_long_note",
                            formats=", ".join(target.upper() for target in too_long),
                            seconds=round(convert_utils.ANIMATION_MAX_SECONDS)))
    if raw_info is not None and raw_info.half:
        notes.append(i18n.t(lang, "raw_half_note", mp=f"{raw_info.megapixels:.0f}",
                            out=f"{raw_info.out_megapixels:.0f}"))
    if len(paths) == 1 and formats.category_of(src_ext, animated) == formats.VIDEO:
        fit = await asyncio.to_thread(convert_utils.video_fit_note, paths[0])
        if fit is not None:
            which, width, height, fit_w, fit_h = fit
            notes.append(i18n.t(lang, f"video_fit_{which}_note", w=width, h=height,
                                fw=fit_w, fh=fit_h))
    if ocr_pages > ocr_convert.OCR_MAX_PAGES:
        notes.append(i18n.t(lang, "ocr_too_many_pages_note", max=ocr_convert.OCR_MAX_PAGES))
    state["notes"] = notes

    targets = _offered_targets(state)
    if not targets:
        for path in paths:
            _unlink(path)
        key = "nothing_sendable" if blocked else "no_target_formats"
        await message.reply_text(i18n.t(lang, key, limit=jobs.send_ceiling_mb(message.chat_id)))
        return

    context.user_data["convert"] = state
    keep = bool(context.user_data.get("keep_files"))
    shown = formats.popular_targets(src_ext, animated)
    shown = [target for target in shown if target in targets] or targets
    await message.reply_text(
        _options_text(lang, paths, src_ext, size, animated, notes),
        reply_markup=InlineKeyboardMarkup(_format_rows(lang, shown, False, len(targets), keep=keep,
                                                       ocr=_ocr_row(context, lang, state, shown))),
    )


async def convert_keep_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    lang = await i18n.get_lang(update.effective_user.id, context)
    keep = not bool(context.user_data.get("keep_files"))
    context.user_data["keep_files"] = keep
    await query.answer(i18n.t(lang, "keep_explained_on" if keep else "keep_explained_off",
                              minutes=max(1, PENDING_TTL_SECONDS // 60)), show_alert=keep)
    state = context.user_data.get("convert")
    if not state or state.get("state") != "awaiting_format":
        return
    if keep:
        _touch(state["paths"])
    targets = _offered_targets(state)
    shown = [target for target in formats.popular_targets(state["src_ext"], state.get("animated"))
             if target in targets] or targets
    state["expanded"] = False
    await edit_in_place(
        query.message, context.bot,
        _options_text(lang, state["paths"], state["src_ext"], state["size"],
                      state.get("animated"), state.get("notes") or ()),
        reply_markup=InlineKeyboardMarkup(_format_rows(lang, shown, False, len(targets), keep=keep,
                                                       ocr=_ocr_row(context, lang, state, shown))),
    )


async def convert_ocr_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    lang = await i18n.get_lang(update.effective_user.id, context)
    state = context.user_data.get("convert")
    if not state or state.get("state") != "awaiting_format":
        await query.answer(i18n.t(lang, "conversion_expired"), show_alert=True)
        return
    choices = ocr_convert.CHOICES
    chosen = choices[(choices.index(_ocr_choice(context, lang)) + 1) % len(choices)]
    context.user_data["ocr_lang"] = chosen
    await query.answer(i18n.t(lang, "ocr_lang_set", name=i18n.t(lang, f"ocr_lang_{chosen}")))
    targets = _offered_targets(state)
    expanded = bool(state.get("expanded"))
    shown = targets if expanded else [
        target for target in formats.popular_targets(state["src_ext"], state.get("animated"))
        if target in targets] or targets
    await edit_in_place(
        query.message, context.bot,
        _options_text(lang, state["paths"], state["src_ext"], state["size"],
                      state.get("animated"), state.get("notes") or ()),
        reply_markup=InlineKeyboardMarkup(_format_rows(
            lang, shown, expanded, len(targets), keep=bool(context.user_data.get("keep_files")),
            ocr=_ocr_row(context, lang, state, shown))),
    )


async def convert_show_more(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    lang = await i18n.get_lang(update.effective_user.id, context)
    state = context.user_data.get("convert")
    if not state or state.get("state") != "awaiting_format":
        await query.answer(i18n.t(lang, "conversion_expired"), show_alert=True)
        return
    await query.answer()
    src_ext = state["src_ext"]
    animated = state.get("animated")
    targets = _offered_targets(state)
    expanded = query.data == "convmore"
    state["expanded"] = expanded
    shown = targets if expanded else [
        target for target in formats.popular_targets(src_ext, animated) if target in targets
    ] or targets
    await edit_in_place(
        query.message, context.bot,
        _options_text(lang, state["paths"], src_ext, state["size"], animated,
                      state.get("notes") or ()),
        reply_markup=InlineKeyboardMarkup(_format_rows(
            lang, shown, expanded, len(targets), keep=bool(context.user_data.get("keep_files")),
            ocr=_ocr_row(context, lang, state, shown))),
    )


def _result_caption(lang: str, result, src_ext: str, target_ext: str, stars_paid: int) -> str:
    if result.zipped and target_ext != "zip":
        key = "converted_file_caption_pages" \
            if formats.category_of(src_ext) in (formats.DOCUMENT, formats.PRESENTATION) \
            else "converted_file_caption_zip"
        caption = i18n.t(lang, key, items=result.items)
    elif result.items > 1 and target_ext == "pdf":
        caption = i18n.t(lang, "converted_file_caption_pdf", items=result.items)
    else:
        caption = i18n.t(lang, "converted_file_caption" if stars_paid else "converted_file_caption_free")
    relaid = ("md", "html", "txt") if formats.met("office") else ("docx", "md", "html", "txt")
    if src_ext in relaid and target_ext == "pdf":
        caption += "\n\n" + i18n.t(lang, "converted_layout_note")
    if target_ext == "docx" and src_ext not in ("doc", "odt", "rtf"):
        caption += "\n\n" + i18n.t(lang, "converted_docx_note")
    return caption


_RUNNER = None


def _runner(application) -> ConvertRunner:
    global _RUNNER
    if _RUNNER is None or _RUNNER.application is not application:
        _RUNNER = ConvertRunner(application, ADMIN_IDS, UPLOAD_DIR, _result_caption)
    return _RUNNER


def _touch(paths) -> None:
    now = time.time()
    for path in paths:
        try:
            os.utime(path, (now, now))
        except OSError:
            pass


async def _enqueue(context: ContextTypes.DEFAULT_TYPE, message, user, lang: str,
                   conv_state: dict, target_ext: str, edit_menu: bool = True) -> None:
    keep = bool(context.user_data.get("keep_files"))
    paths = list(conv_state["paths"])
    reading = convert_utils.ocr_job_pages(conv_state["src_ext"], target_ext, conv_state.get("ocr_pages") or 0,
                                          len(paths))
    if reading:
        time_limit = convert_utils.ocr_limit(conv_state["src_ext"], target_ext, conv_state["size"],
                                             conv_state.get("est_out") or 0, reading,
                                             paid=conv_state["price"] > 0, pages=conv_state.get("pages"))
    else:
        time_limit = jobs.time_limit_for(
            convert_utils.billable_bytes(conv_state["size"], conv_state.get("est_out") or 0),
            convert_utils.work_factor(conv_state["src_ext"], target_ext),
            paid=conv_state["price"] > 0)
    job = jobs.Job(
        user_id=user.id, chat_id=message.chat_id, paths=paths,
        src_ext=conv_state["src_ext"], target_ext=target_ext,
        size=conv_state["size"], price=conv_state["price"], lang=lang,
        username=user.username, keep_file=keep,
        send_limit=jobs.send_limit_for(conv_state["size"], conv_state.get("est_out"),
                                       message.chat_id),
        time_limit=time_limit,
        ocr_lang=_ocr_choice(context, lang) if reading else "", ocr_pages=reading,
    )
    ahead = jobs.ahead_of(job)
    if ahead:
        text = i18n.t(lang, "job_queued", src=job.src_ext.upper(), target=i18n.target_label(lang, target_ext),
                      ahead=ahead, price=job.price)
    else:
        text = i18n.t(lang, "job_starting", src=job.src_ext.upper(), target=i18n.target_label(lang, target_ext))
    runner = _runner(context.application)

    if keep:
        conv_state["state"] = "awaiting_format"
        conv_state.pop("payload", None)
        conv_state.pop("target_ext", None)
        _touch(paths)
        status = await message.reply_text(text, reply_markup=runner.stop_keyboard(job))
    else:
        context.user_data.pop("convert", None)
        if edit_menu:
            await edit_in_place(message, context.bot, text, reply_markup=runner.stop_keyboard(job))
            status = message
        else:
            status = await message.reply_text(text, reply_markup=runner.stop_keyboard(job))
    job.extra["status_message"] = status
    jobs.submit(context.application, job, runner)


async def convert_stop_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    lang = await i18n.get_lang(update.effective_user.id, context)
    job_id = query.data.split(":", 1)[1] if ":" in (query.data or "") else ""
    job = jobs.cancel(job_id, user_id=update.effective_user.id)
    if job is None:
        await query.answer(i18n.t(lang, "job_not_running"))
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            logger.debug("Could not take a stale Stop button down", exc_info=True)
        return
    await query.answer()


async def convert_format_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    target_ext = _safe_ext(query.data.split(":", 1)[1])
    lang = await i18n.get_lang(update.effective_user.id, context)
    conv_state = context.user_data.get("convert")
    if not conv_state or conv_state.get("state") != "awaiting_format":
        await query.answer(i18n.t(lang, "conversion_expired"), show_alert=True)
        return

    if target_ext not in formats.targets_for(conv_state["src_ext"], conv_state.get("animated")):
        await query.answer(i18n.t(lang, "unsupported_format", ext=target_ext), show_alert=True)
        return
    if target_ext in (conv_state.get("too_long") or ()):
        await query.answer(i18n.t(lang, "animation_too_long_alert",
                                  seconds=round(convert_utils.ANIMATION_MAX_SECONDS)), show_alert=True)
        return
    if target_ext in (conv_state.get("blocked") or ()):
        await query.answer(i18n.t(lang, "too_big_to_send_alert",
                                  limit=jobs.send_ceiling_mb(query.message.chat_id)),
                           show_alert=True)
        return
    if target_ext not in _offered_targets(conv_state):
        await query.answer(i18n.t(lang, "not_for_this_file"), show_alert=True)
        return

    in_paths = conv_state["paths"]
    src_ext = conv_state["src_ext"]
    user = update.effective_user

    if not all(os.path.exists(path) for path in in_paths):
        _discard_pending(context)
        await query.answer(i18n.t(lang, "conversion_expired"), show_alert=True)
        return

    price, est_out = price_for_job(src_ext, target_ext, conv_state["size"],
                                   conv_state.get("pages"), conv_state.get("ocr_pages") or 0,
                                   len(in_paths))
    conv_state["price"] = price
    conv_state["est_out"] = est_out
    conv_state["target_ext"] = target_ext

    if price == 0:
        await query.answer()
        await _start_chosen(update, context, conv_state, target_ext, lang)
        return

    balance = await asyncio.to_thread(family_link.star_balance, user.id)
    await query.answer()
    await edit_in_place(
        query.message, context.bot,
        _quote_text(lang, src_ext, target_ext, conv_state["size"], est_out, price, balance,
                    jobs.send_limit_for(conv_state["size"], est_out, query.message.chat_id)),
        reply_markup=_quote_keyboard(lang, price))


async def convert_quote_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    lang = await i18n.get_lang(update.effective_user.id, context)
    state = context.user_data.get("convert")
    if not state or state.get("state") != "awaiting_format":
        await query.answer(i18n.t(lang, "conversion_expired"), show_alert=True)
        return
    await query.answer()
    state.pop("target_ext", None)
    targets = _offered_targets(state)
    shown = [target for target in formats.popular_targets(state["src_ext"], state.get("animated"))
             if target in targets] or targets
    state["expanded"] = False
    await edit_in_place(
        query.message, context.bot,
        _options_text(lang, state["paths"], state["src_ext"], state["size"],
                      state.get("animated"), state.get("notes") or ()),
        reply_markup=InlineKeyboardMarkup(_format_rows(
            lang, shown, False, len(targets), keep=bool(context.user_data.get("keep_files")),
            ocr=_ocr_row(context, lang, state, shown))),
    )


async def convert_quote_go(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    lang = await i18n.get_lang(update.effective_user.id, context)
    state = context.user_data.get("convert")
    target_ext = (state or {}).get("target_ext")
    if not state or state.get("state") != "awaiting_format" or not target_ext:
        await query.answer(i18n.t(lang, "conversion_expired"), show_alert=True)
        return
    if not all(os.path.exists(path) for path in state["paths"]):
        _discard_pending(context)
        await query.answer(i18n.t(lang, "conversion_expired"), show_alert=True)
        return
    await query.answer()
    await _start_chosen(update, context, state, target_ext, lang)


async def _start_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE,
                        conv_state: dict, target_ext: str, lang: str) -> None:
    query = update.callback_query
    price = conv_state["price"]
    src_ext = conv_state["src_ext"]
    user = update.effective_user

    refusal = await refuse_new_work(lang, user.id, query.message.chat_id)
    if refusal:
        await query.answer(refusal, show_alert=True)
        return

    if len(jobs.jobs_for(user.id)) >= jobs.MAX_QUEUED_PER_USER:
        await query.answer(i18n.t(lang, "queue_full", count=jobs.MAX_QUEUED_PER_USER),
                           show_alert=True)
        return

    needed = price + jobs.uncharged_total(user.id) if price > 0 else 0
    balance = await asyncio.to_thread(family_link.star_balance, user.id) if needed else 0
    if needed and balance < needed:
        shortfall = needed - max(balance, 0)
        stars = math.ceil(shortfall / max(family_link.TOPUP_MULTIPLIER, 0.01))
        credited = (await asyncio.to_thread(family_link.quote_topup, user.id, stars))["total"]
        if STARS_SANDBOX:
            await asyncio.to_thread(family_link.topup, user.id, stars,
                                    "sandbox top-up for a conversion")
        else:
            payload = f"convert:{uuid.uuid4().hex}"
            conv_state["target_ext"] = target_ext
            conv_state["payload"] = payload
            conv_state["state"] = "awaiting_payment"
            await asyncio.to_thread(
                record_star_invoice, user.id, user.username, stars,
                f"top-up for a {src_ext}->{'searchable-pdf' if target_ext == 'ocrpdf' else target_ext} conversion",
                payload)
            await context.bot.send_invoice(
                chat_id=query.message.chat_id,
                title=i18n.t(lang, "convert_invoice_title", src=src_ext.upper(),
                             target=i18n.target_label(lang, target_ext)),
                description=i18n.t(lang, "convert_invoice_description", size_kb=f"{conv_state['size'] / 1024:.0f}"),
                payload=payload,
                provider_token="",
                currency="XTR",
                prices=[LabeledPrice(i18n.t(lang, "convert_invoice_label", src=src_ext,
                                           target=target_ext if target_ext != "ocrpdf" else i18n.target_label(lang, target_ext)),
                                     stars)],
            )
            await edit_in_place(query.message, context.bot,
                                i18n.t(lang, "topup_needed", price=needed, balance=max(balance, 0),
                                       stars=stars, credited=credited))
            return

    await _enqueue(context, query.message, user, lang, conv_state, target_ext)


async def precheckout_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.pre_checkout_query
    if query.invoice_payload.startswith("convert:"):
        await query.answer(ok=True)
        return
    if query.invoice_payload.startswith("donate:"):
        await donation_precheckout(query)
        return
    lang = await i18n.get_lang(update.effective_user.id, context)
    await query.answer(ok=False, error_message=i18n.t(lang, "unknown_order"))


async def successful_payment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sp = update.message.successful_payment
    payload = sp.invoice_payload

    if payload.startswith("donate:"):
        await donation_payment_received(update, context)
        return

    lang = await payer_language(update, context)
    charge_id = sp.telegram_payment_charge_id
    user = update.effective_user
    conv_state = context.user_data.get("convert") or {}

    result = await credit_stars_payment(update, context, user, sp.total_amount, lang, payload, charge_id,
                                        "top-up for a conversion")
    if result is None:
        return
    paths = conv_state.get("paths") or []
    if (conv_state.get("payload") != payload or not paths
            or not all(os.path.exists(path) for path in paths)):
        await update.message.reply_text(i18n.t(
            lang, "payment_kept_as_credit",
            credited=result["credited"] + result["bonus"], balance=result["balance"]))
        return
    await _enqueue(context, update.message, user, lang, conv_state,
                   conv_state.get("target_ext"), edit_menu=False)


async def mystars_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await balance_command(update, context)


async def stars_admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update.effective_user.id):
        return await _deny(update, context)
    await update.message.reply_text(await _stars_text())


async def _bus_stars(context, args):
    return await _stars_text(), None, None


async def _stars_text() -> str:
    summary, recent = await asyncio.to_thread(get_star_ledger, 15)
    lines = [
        f"Paid: {summary['paid_total']} ⭐ across {summary['paid_count']} order(s) from {summary['paying_users']} user(s) (Stars only)",
        f"Refunded: {summary['refunded_total']} ⭐ across {summary['refunded_count']} order(s) (Stars only)",
        f"Free conversions: {summary['free_count']}",
        "",
        "Recent transactions (any currency):",
    ]
    for t in recent:
        who = f"@{t['username']}" if t["username"] else str(t["user_id"])
        amount = format_ledger_amount(t["amount_stars"], t["currency"])
        lines.append(f"  {t['created_at'][:16]} {who} {amount} {t['item']} [{t['status']}]")
    return "\n".join(lines)


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
    await update.message.reply_document(document=BytesIO(data), filename=f"convertbot_db_{stamp}.zip")
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


async def unrecognized_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = await i18n.get_lang(update.effective_user.id, context)
    answer = _format_lookup(lang, update.message.text or "")
    if answer:
        await update.message.reply_text(answer)
        return
    await update.message.reply_text(
        i18n.t(lang, "unrecognized_message"), reply_markup=_what_formats_keyboard(lang),
    )


async def unknown_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = await i18n.get_lang(update.effective_user.id, context)
    await update.message.reply_text(i18n.t(lang, "unknown_command"))


async def _check_big_files() -> None:
    if await big_files.verify() or big_files.VERIFIED is not False:
        return
    logger.warning("Largest upload now %d MB: %s.", convert_utils.MAX_FILE_MB, big_files.describe())
    family_link.report_event_soon("warning", "big_files", f"ConvertBot: {big_files.describe()}.")


_background: set = set()


async def _post_init(application):
    await tune_runtime(application)
    await lifecycle.on_start(BOT_NAME)
    await lifecycle.optional("command menu", publish_commands(application, BOT_COMMANDS, ADMIN_COMMANDS, ADMIN_IDS))
    await lifecycle.optional("profile text", publish_profile(application))
    if big_files.CONFIGURED:
        task = asyncio.get_running_loop().create_task(_check_big_files())
        _background.add(task)
        task.add_done_callback(_background.discard)


async def _post_stop(application):
    await lifecycle.on_stop(application)
    await big_files.close()
    await flush_on_shutdown(application)


def main():
    if not BOT_TOKEN or not BOT_USERNAME:
        raise SystemExit("Set CBOT_TOKEN and CBOT_USERNAME environment variables first.")

    init_db()
    formats.probe()

    lifecycle.optional_now("sweep of old uploads", _sweep_uploads, 0)

    builder = (
        ApplicationBuilder().token(BOT_TOKEN)
        .post_init(_post_init).post_stop(_post_stop)
    )
    builder = shared_features.per_user_updates(builder, "CBOT")
    if LOCAL_BOT_API_URL:
        base = LOCAL_BOT_API_URL.rstrip("/")
        builder = (
            builder
            .base_url(f"{base}/bot")
            .base_file_url(f"{base}/file/bot")
            .local_mode(LOCAL_BOT_API_MODE)
        )
        logger.info("Using local Bot API server at %s (local_mode=%s)", base, LOCAL_BOT_API_MODE)
    else:
        logger.info("Largest upload %d MB: %s.", convert_utils.MAX_FILE_MB, big_files.describe())
    jobs.log_capacity()
    state = lifecycle.persistence()
    if state is not None:
        builder = builder.persistence(state)
    app = builder.build()
    lifecycle.install(app, BOT_NAME, ADMIN_IDS)
    lifecycle.add_work_source("conversion(s)", lambda: len(jobs._jobs))
    app.add_error_handler(error_handler)
    attach_problem_reports(app)
    app.add_handler(TypeHandler(Update, track_activity), group=-4)
    attach_flood_gate(app, ADMIN_IDS)
    attach_promo_claim(app)
    app.add_handler(MessageHandler((filters.TEXT | filters.REPLY) & ~filters.COMMAND,
                                   awaited_text_received), group=-1)

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("fullhelp", full_help_command(build_help_text, help_rows, HELP_PAGES)))
    app.add_handler(CallbackQueryHandler(help_toggle(build_help_text, help_rows, HELP_PAGES),
                                         pattern=HELP_TOGGLE_PATTERN))
    app.add_handler(CallbackQueryHandler(help_page_button(HELP_PAGES), pattern=HELP_PAGE_PATTERN))
    app.add_handler(CommandHandler("feedback", feedback_command))
    app.add_handler(CommandHandler("convert", convert_start))
    app.add_handler(CommandHandler("formats", formats_command))
    app.add_handler(CommandHandler("cancel", cancel_command))
    app.add_handler(CommandHandler("mystars", mystars_command))
    app.add_handler(CommandHandler("stars", stars_admin_command))
    app.add_handler(CommandHandler("dbdump", dbdump_command))
    app.add_handler(CommandHandler("messageas", messageas_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CallbackQueryHandler(convert_format_chosen, pattern="^convfmt:"))
    app.add_handler(CallbackQueryHandler(convert_show_more, pattern="^conv(more|less)$"))
    app.add_handler(CallbackQueryHandler(convert_quote_go, pattern="^convgo$"))
    app.add_handler(CallbackQueryHandler(convert_quote_back, pattern="^convback$"))
    app.add_handler(CallbackQueryHandler(formats_overview_callback, pattern="^fmtall$"))
    app.add_handler(CallbackQueryHandler(formats_category_callback, pattern="^fmtcat:"))
    app.add_handler(CallbackQueryHandler(cancel_conversion_callback, pattern="^convcancel$"))
    app.add_handler(CallbackQueryHandler(convert_stop_callback, pattern="^convstop:"))
    app.add_handler(CallbackQueryHandler(convert_keep_callback, pattern="^convkeep$"))
    app.add_handler(CallbackQueryHandler(convert_ocr_callback, pattern="^convocr$"))
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
    app.add_handler(CallbackQueryHandler(delete_my_data_chosen, pattern="^" + ERASE_PREFIX))

    app.add_handler(CommandHandler("balance", balance_command))
    app.add_handler(CommandHandler("recharge", donate_command))
    app.add_handler(CommandHandler("donate", donate_command))
    app.add_handler(CallbackQueryHandler(donate_amount_chosen, pattern="^donate:"))
    app.add_handler(CallbackQueryHandler(donate_fiat_amount_chosen, pattern="^donatefiat:"))
    app.add_handler(CallbackQueryHandler(donate_custom_button_chosen, pattern="^donatecustom:"))

    app.add_handler(PreCheckoutQueryHandler(precheckout_callback))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment_callback))
    app.add_handler(
        MessageHandler(
            filters.Document.ALL | filters.PHOTO | filters.VIDEO | filters.AUDIO
            | filters.VOICE | filters.ANIMATION,
            convert_receive_file,
        )
    )

    app.add_handler(MessageHandler(filters.COMMAND, unknown_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, unrecognized_message))

    family_link.attach(app, BOT_NAME, "ConvertBot", START_TIME)
    family_link.COMMANDS["stars"] = _bus_stars
    family_link.COMMAND_HELP["stars"] = "the Stars ledger: paid, free and refunded"
    attach_maintenance(app)
    if app.job_queue is not None:
        app.job_queue.run_repeating(_sweep_uploads_job, interval=300, first=300)
        app.job_queue.run_repeating(_conversions_in_flight_job, interval=jobs.HOLD_TOUCH_S, first=10)

    logger.info("Bot starting (polling)...")
    app.run_polling(**lifecycle.polling_kwargs(
        timeout=POLL_TIMEOUT,
        allowed_updates=[Update.MESSAGE, Update.CALLBACK_QUERY, Update.PRE_CHECKOUT_QUERY],
    ))


if __name__ == "__main__":
    main()
