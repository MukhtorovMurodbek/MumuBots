import asyncio
import contextlib
import contextvars
import gc
import logging
import os
import re
import socket
import sys
import threading
import time
import traceback
import uuid
import zlib
from collections import OrderedDict, deque, namedtuple
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler

from telegram import (
    BotCommand, BotCommandScopeAllChatAdministrators, BotCommandScopeAllGroupChats,
    BotCommandScopeAllPrivateChats, BotCommandScopeChat, ForceReply, InlineKeyboardButton,
    InlineKeyboardMarkup, LabeledPrice, LinkPreviewOptions, ReplyKeyboardRemove,
    Update,
)
from telegram.constants import ParseMode
from telegram.error import BadRequest, Conflict, NetworkError, RetryAfter
from telegram.ext import (
    ApplicationHandlerStop, BaseUpdateProcessor, CallbackQueryHandler, CommandHandler, ConversationHandler,
    TypeHandler, filters,
)

import psycopg

import db
import family_link
import i18n
import lifecycle
import live_message
import problems
import redact
import reserve
import resources


def _parse_sibling_bots() -> list[dict]:
    raw = os.environ.get("SIBLING_BOTS", "")
    bots = []
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        parts = entry.split(":")
        if len(parts) != 3:
            continue
        bot_id, name, username = parts
        bots.append({"id": bot_id, "name": name, "username": username.strip().lstrip("@")})
    return bots


def _convert_bot_name() -> str:
    for bot in _parse_sibling_bots():
        if bot["id"] == "convertbot":
            return "@" + bot["username"]
    return "ConvertBot"


def sibling_bots_blurb(this_bot_name: str, lang: str) -> str:
    others = [b for b in _parse_sibling_bots() if b["id"] != this_bot_name]
    if not others:
        return ""
    return i18n.t(lang, "sibling_blurb")


def sibling_bots_keyboard_row(this_bot_name: str, only: str | None = None) -> list[InlineKeyboardButton]:
    others = [b for b in _parse_sibling_bots() if b["id"] != this_bot_name]
    if only:
        others = [b for b in others if b["id"] == only]
    return [InlineKeyboardButton(f"↗️ {b['name']}", url=f"https://t.me/{b['username']}") for b in others]


def language_keyboard(current: str | None = None) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(("✅ " if code == current else "") + label,
                             callback_data=f"setlang:{code}")
        for code, label in i18n.LANGUAGE_LABELS.items()
    ]])


HELP_TOGGLE_PATTERN = r"^help:(full|brief)$"


def wants_full_help(context) -> bool:
    args = getattr(context, "args", None) or []
    return bool(args) and str(args[0]).lower() in ("full", "all", "more")


def help_keyboard(lang: str, full: bool, rows=(), pages=None, labels=None) -> InlineKeyboardMarkup:
    toggle = (InlineKeyboardButton(i18n.t(lang, "help_brief_button"), callback_data="help:brief")
              if full else
              InlineKeyboardButton(i18n.t(lang, "help_full_button"), callback_data="help:full"))
    page_rows = help_page_buttons(lang, pages, labels) if pages else []
    return InlineKeyboardMarkup(page_rows + [[toggle]] + [list(row) for row in rows if row])


def help_toggle(build_text, rows=None, pages=None, labels=None):
    async def toggle(update, context) -> None:
        query = update.callback_query
        await query.answer()
        lang = await i18n.get_lang(update.effective_user.id, context)
        full = query.data == "help:full"
        await live_message.edit_in_place(
            query.message, context.bot, build_text(lang, full=full),
            reply_markup=help_keyboard(lang, full, rows(lang) if rows else (), pages, labels))
    return toggle


HELP_PAGE_PREFIX = "helpdet:"
HELP_PAGE_PATTERN = r"^helpdet:[a-z]{1,20}$"


def help_page_name(context, pages, aliases=None) -> str | None:
    args = getattr(context, "args", None) or []
    if not args:
        return None
    word = str(args[0]).lower().lstrip("/").split("@", 1)[0]
    if word.startswith("help_"):
        word = word[len("help_"):]
    word = (aliases or {}).get(word, word)
    return word if word in pages else None


def help_page_text(lang: str, pages, name: str) -> str:
    return pages[name](lang).rstrip() + "\n\n" + i18n.t(lang, "help_page_everything")


def help_page_buttons(lang: str, pages, labels=None) -> list[list[InlineKeyboardButton]]:
    labels = labels or {}
    buttons = [
        InlineKeyboardButton(i18n.t(lang, labels[name]) if name in labels else "/" + name,
                             callback_data=HELP_PAGE_PREFIX + name)
        for name in pages
    ]
    return [buttons[i:i + 2] for i in range(0, len(buttons), 2)]


def help_page_button(pages):
    async def page(update, context) -> None:
        query = update.callback_query
        user = update.effective_user
        lang = await i18n.get_lang(user.id, context)
        name = query.data[len(HELP_PAGE_PREFIX):]
        if name not in pages:
            await query.answer(i18n.t(lang, "help_page_gone"), show_alert=True)
            return
        await query.answer()
        chat_id = query.message.chat_id if query.message is not None else user.id
        await context.bot.send_message(chat_id=chat_id, text=help_page_text(lang, pages, name),
                                       link_preview_options=NO_PREVIEW)
    return page


def full_help_command(build_text, rows=None, pages=None, labels=None):
    async def fullhelp(update, context) -> None:
        lang = await i18n.get_lang(update.effective_user.id, context)
        await update.message.reply_text(build_text(lang, full=True),
                                        reply_markup=help_keyboard(lang, True, rows(lang) if rows else (), pages, labels))
    return fullhelp


WORKER_THREADS = int(os.environ.get("WORKER_THREADS", "4"))

GC_THRESHOLD = int(os.environ.get("GC_GEN0_THRESHOLD", "5000"))


async def tune_runtime(application) -> None:
    asyncio.get_running_loop().set_default_executor(
        ThreadPoolExecutor(max_workers=WORKER_THREADS, thread_name_prefix="worker")
    )
    gc.collect()
    gc.freeze()
    gc.set_threshold(GC_THRESHOLD, 20, 20)


FLOOD_UPDATES_PER_MINUTE = int(os.environ.get("FLOOD_UPDATES_PER_MINUTE", "40"))
FLOOD_REMIND_SECONDS = int(os.environ.get("FLOOD_REMIND_SECONDS", "60"))
_FLOOD_MAX_TRACKED = 4096

_flood_window: "OrderedDict[int, deque]" = OrderedDict()
_flood_told: "OrderedDict[int, float]" = OrderedDict()


def _flood_trim(store) -> None:
    while len(store) > _FLOOD_MAX_TRACKED:
        store.popitem(last=False)


def flood_wait_seconds(user_id: int) -> int:
    if FLOOD_UPDATES_PER_MINUTE <= 0:
        return 0
    now = time.monotonic()
    window = _flood_window.setdefault(user_id, deque())
    _flood_window.move_to_end(user_id)
    _flood_trim(_flood_window)
    while window and now - window[0] > 60:
        window.popleft()
    if len(window) >= FLOOD_UPDATES_PER_MINUTE:
        return max(1, int(60 - (now - window[0])) + 1)
    window.append(now)
    return 0


def _flood_should_tell(user_id: int) -> bool:
    now = time.monotonic()
    last = _flood_told.get(user_id)
    if last is not None and now - last < FLOOD_REMIND_SECONDS:
        return False
    _flood_told[user_id] = now
    _flood_told.move_to_end(user_id)
    _flood_trim(_flood_told)
    return True


def attach_flood_gate(app, admin_ids=(), group: int = -3, exempt=None) -> None:
    admins = set(admin_ids)

    async def _gate(update, context) -> None:
        user = update.effective_user
        if user is None or user.id in admins:
            return
        if in_group_chat(update):
            return
        if exempt is not None:
            try:
                if exempt(update):
                    return
            except Exception:
                logging.getLogger(__name__).debug("Flood exemption check failed", exc_info=True)
        wait = flood_wait_seconds(user.id)
        if not wait:
            return
        if _flood_should_tell(user.id):
            try:
                lang = await i18n.get_lang(user.id, context)
                if update.callback_query is not None:
                    await update.callback_query.answer(
                        i18n.t(lang, "flood_wait", seconds=wait), show_alert=True)
                elif update.effective_message is not None:
                    await update.effective_message.reply_text(
                        i18n.t(lang, "flood_wait", seconds=wait))
            except Exception:
                logging.getLogger(__name__).debug(
                    "Could not tell a flooding user to wait", exc_info=True)
        raise ApplicationHandlerStop

    app.add_handler(TypeHandler(Update, _gate), group=group)


PROMO_DONE_KEY = "_promo_done"
_promo_claiming: set = set()
_promo_state = {"over": False}


def _notice_language(user_id: int, telegram_code: "str | None") -> str:
    chosen = family_link._language_of(user_id)
    if chosen in i18n.SUPPORTED_LANGUAGES:
        return chosen
    code = (telegram_code or "").split("-")[0].lower()
    return code if code in i18n.SUPPORTED_LANGUAGES else "en"


PROMO_CALLBACK = "promogift"


async def _offer_gift(context, user_id: int, chat_id: int, telegram_code: "str | None" = None) -> None:
    log = logging.getLogger(__name__)
    try:
        window = await asyncio.to_thread(family_link.promo_window)
        now = datetime.now(timezone.utc)
        if window is None or not window[0] <= now < window[1]:
            if window is not None and now >= window[1]:
                _promo_state["over"] = True
            return
        if await asyncio.to_thread(family_link.promo_given_to, user_id):
            context.user_data[PROMO_DONE_KEY] = family_link.PROMO_KEY
            return
        lang = await asyncio.to_thread(_notice_language, user_id, telegram_code)
        context.user_data[PROMO_DONE_KEY] = family_link.PROMO_KEY
        button = InlineKeyboardButton(family_link.phrase("gift_claim_button", lang, amount=family_link.PROMO_CREDITS),
                                      callback_data=PROMO_CALLBACK)
        await context.bot.send_message(chat_id=chat_id, text=family_link.promo_offer(lang, window[1]),
                                       reply_markup=InlineKeyboardMarkup([[button]]))
    except Exception as exc:
        log.warning("The gift could not be offered (%s); the next message tries again", type(exc).__name__)
        context.user_data.pop(PROMO_DONE_KEY, None)
    finally:
        _promo_claiming.discard(user_id)


async def _claim_and_tell(context, user_id: int, chat_id: int, telegram_code: "str | None" = None) -> None:
    log = logging.getLogger(__name__)
    try:
        outcome, expires = await asyncio.to_thread(family_link.claim_promo_gift, user_id)
    except Exception as exc:
        log.warning("The gift could not be claimed (%s)", type(exc).__name__)
        return
    lang = await asyncio.to_thread(_notice_language, user_id, telegram_code)
    if outcome == family_link.PROMO_GIVEN:
        text = family_link.promo_notice(lang, expires)
    elif outcome == family_link.PROMO_HAD:
        text = family_link.phrase("gift_already_had", lang)
    else:
        text = family_link.phrase("gift_not_open", lang)
    context.user_data[PROMO_DONE_KEY] = family_link.PROMO_KEY
    try:
        await context.bot.send_message(chat_id=chat_id, text=text)
    except Exception:
        log.debug("The gift's notice could not be sent", exc_info=True)


def attach_promo_claim(app, group: int = -2) -> None:
    async def _claim_now(update, context) -> None:
        user, chat = update.effective_user, update.effective_chat
        if update.callback_query is not None:
            with contextlib.suppress(Exception):
                await update.callback_query.answer()
        if user is not None and chat is not None and not getattr(user, "is_bot", False):
            await _claim_and_tell(context, user.id, chat.id, getattr(user, "language_code", None))
        raise ApplicationHandlerStop

    app.add_handler(CommandHandler("gift", _claim_now, filters=filters.ChatType.PRIVATE), group=group)
    app.add_handler(CallbackQueryHandler(_claim_now, pattern=f"^{PROMO_CALLBACK}$"), group=group)

    async def _claim_on_message(update, context) -> None:
        if _promo_state["over"]:
            return
        user, chat = update.effective_user, update.effective_chat
        if (update.message is None or user is None or chat is None or chat.type != "private"
                or getattr(user, "is_bot", False)):
            return
        if context.user_data.get(PROMO_DONE_KEY) == family_link.PROMO_KEY or user.id in _promo_claiming:
            return
        _promo_claiming.add(user.id)
        context.application.create_task(
            _offer_gift(context, user.id, chat.id, getattr(user, "language_code", None)))

    app.add_handler(TypeHandler(Update, _claim_on_message), group=group)


DONATION_STEPS = [15, 60, 200]
DONATION_COOLDOWN_FLOOR_DAYS = 3


DONATION_PIN = os.environ.get("DONATION_PIN", "off").lower() in ("on", "1", "true", "yes")

DONATE_STAR_OPTIONS = [15, 50, 150, 500]
MAX_DONATION_STARS = 100_000

STARS_SANDBOX = os.environ.get("FAMILY_STARS_SANDBOX", "").strip().lower() in ("1", "true", "yes", "on")
if STARS_SANDBOX:
    logging.getLogger(__name__).warning(
        "FAMILY_STARS_SANDBOX is on: Stars top-ups will be credited WITHOUT charging anybody. "
        "This must never be set on a bot real people use.")

FIAT_CURRENCIES = {
    "USD": {
        "symbol": "$", "label": "USD", "exp": 2,
        "options": [1, 3, 5, 10], "min_minor": 100, "max_minor": 1_000_000,
    },
}


FIAT_DONATIONS = os.environ.get("FIAT_DONATIONS", "off").lower() in ("on", "1", "true", "yes")


def _fiat_provider_token(currency: str) -> str | None:
    if not FIAT_DONATIONS:
        return None
    return os.environ.get(f"PAYMENT_PROVIDER_TOKEN_{currency}") or None


def _available_fiat_currencies() -> list[str]:
    return [c for c in FIAT_CURRENCIES if _fiat_provider_token(c)]


def format_ledger_amount(amount: int, currency: str) -> str:
    if currency == "XTR":
        return f"{amount}⭐"
    cfg = FIAT_CURRENCIES.get(currency)
    if not cfg:
        return f"{amount} {currency}"
    return f"{amount / (10 ** cfg['exp']):g} {cfg['symbol']}"


def _donation_nudge_due(user_id: int) -> bool:
    count, last_shown, times_shown, donated = db.bump_donation_action(user_id)

    if donated:
        return False
    if times_shown >= len(DONATION_STEPS):
        return False

    if last_shown:
        days_since = (datetime.now(timezone.utc) - datetime.fromisoformat(last_shown)).days
        if days_since < DONATION_COOLDOWN_FLOOR_DAYS:
            return False

    if count < DONATION_STEPS[times_shown]:
        return False

    db.reset_donation_prompt(user_id)
    return True


async def maybe_donation_nudge(user_id: int, lang: str, context=None, chat_id: int | None = None) -> str | None:
    try:
        due = await asyncio.to_thread(_donation_nudge_due, user_id)
    except Exception:
        logging.getLogger(__name__).debug("Donation nudge check failed", exc_info=True)
        return None
    if not due:
        return None
    text = i18n.t(lang, "donation_nudge")
    if DONATION_PIN and context is not None and chat_id is not None:
        await _pin_donation_nudge(context, user_id, chat_id, text)
        return None
    return text


async def _pin_donation_nudge(context, user_id: int, chat_id: int, text: str) -> None:
    try:
        previous = await asyncio.to_thread(db.get_pinned_donation_message, user_id)
    except Exception:
        previous = None
    if previous:
        try:
            await context.bot.unpin_chat_message(chat_id=chat_id, message_id=previous)
        except Exception:
            logging.getLogger(__name__).debug("Could not unpin the previous nudge", exc_info=True)
    try:
        sent = await context.bot.send_message(chat_id=chat_id, text=text)
        await context.bot.pin_chat_message(
            chat_id=chat_id, message_id=sent.message_id, disable_notification=True)
    except Exception:
        logging.getLogger(__name__).debug("Could not pin the donation nudge", exc_info=True)
        return
    try:
        await asyncio.to_thread(db.set_pinned_donation_message, user_id, sent.message_id)
    except Exception:
        logging.getLogger(__name__).debug("Could not record the pinned nudge", exc_info=True)


async def unpin_donation_nudge(context, user_id: int, chat_id: int) -> None:
    try:
        previous = await asyncio.to_thread(db.get_pinned_donation_message, user_id)
    except Exception:
        return
    if not previous:
        return
    try:
        await context.bot.unpin_chat_message(chat_id=chat_id, message_id=previous)
    except Exception:
        logging.getLogger(__name__).debug("Could not unpin after a donation", exc_info=True)
    try:
        await asyncio.to_thread(db.set_pinned_donation_message, user_id, None)
    except Exception:
        pass


async def payer_language(update, context) -> str:
    user = update.effective_user
    try:
        return await i18n.get_lang(user.id, context)
    except Exception as exc:
        logging.getLogger(__name__).warning(
            "payment: the payer's language could not be read (%s); using their app's", type(exc).__name__)
        code = (getattr(user, "language_code", None) or "").split("-")[0].lower()
        return code if code in i18n.SUPPORTED_LANGUAGES else "en"


_MARK_ATTEMPTS = 3
_MARK_BACKOFF_SECONDS = (1.0, 3.0)


async def mark_star_transaction(payload: str, status: str, charge_id: "str | None" = None) -> bool:
    log = logging.getLogger(__name__)
    for attempt in range(1, _MARK_ATTEMPTS + 1):
        try:
            await asyncio.to_thread(db.update_star_transaction, payload, status, charge_id)
            return True
        except psycopg.OperationalError as exc:
            if attempt == _MARK_ATTEMPTS:
                log.error("payment: the invoice row could not be marked %s after %d tries (%s)",
                          status, attempt, type(exc).__name__)
                return False
            await asyncio.sleep(_MARK_BACKOFF_SECONDS[attempt - 1])
        except Exception:
            log.exception("payment: the invoice row could not be marked %s", status)
            return False
    return False


async def credit_stars_payment(update, context, user, stars: int, lang: str, payload: str,
                               charge_id: "str | None", detail: str) -> "dict | None":
    try:
        result = await asyncio.to_thread(family_link.topup, user.id, stars, detail, charge_id)
    except Exception as exc:
        await _payment_not_credited(update, context, user, stars, lang, payload, charge_id, exc)
        return None
    if result is None and charge_id:
        try:
            held = await asyncio.to_thread(family_link.charge_claim, charge_id)
        except Exception as exc:
            held = None
            logging.getLogger(__name__).warning(
                "payment: charge %s was seen before; its claim could not be read (%s)",
                charge_id, type(exc).__name__)
        if held is not None and held.startswith(family_link.UNCREDITED_CLAIM):
            emit_event("warning", "payment",
                       f"A Stars payment of {stars} XTR that was refunded or put aside earlier was "
                       f"delivered again; its record was left as it was (charge {charge_id}).")
            return None
    await mark_star_transaction(payload, "paid", charge_id)
    if result is None:
        emit_event("warning", "payment", "A Stars payment was delivered twice and credited once.")
    elif result.get("recovered"):
        emit_event("warning", "payment",
                   f"A Stars payment of {stars} XTR was credited, but the database lost its answer; "
                   f"the bot read the credit back and carried on (charge {charge_id}).")
    return result


async def _payment_not_credited(update, context, user, stars: int, lang: str, payload: str,
                                charge_id: "str | None", failure: Exception) -> None:
    log = logging.getLogger(__name__)
    log.error("payment: charge %s could not be credited (%s)", charge_id, type(failure).__name__)

    async def tell(key: str) -> None:
        try:
            await update.message.reply_text(i18n.t(lang, key, stars=stars))
        except Exception:
            log.warning("payment: the payer could not be told (%s)", key, exc_info=True)

    async def by_hand(why: str) -> None:
        text = (f"A Stars payment of {stars} XTR could not be credited ({type(failure).__name__}), and "
                f"{why}: put it right by hand (user {user.id}, charge {charge_id or 'none'}, "
                f"invoice {payload}).")
        log.critical("payment: %s", text)
        emit_event("error", "payment", text, keep=True)
        await tell_owner_directly(context.bot, f"🚨 {family_link._display_name or 'A bot'}: {text}")
        await mark_star_transaction(payload, "paid", charge_id)
        await tell("topup_failed")

    if not charge_id:
        await by_hand("it has no charge id to claim or refund it by")
        return
    try:
        held = await asyncio.to_thread(family_link.claim_uncredited, charge_id)
    except Exception as exc:
        await by_hand(f"whether an earlier try credited it could not be checked ({type(exc).__name__})")
        return
    if held is not None:
        if held.startswith(family_link.UNCREDITED_CLAIM):
            log.info("payment: charge %s is being dealt with by another delivery of it", charge_id)
            return
        await mark_star_transaction(payload, "paid", charge_id)
        emit_event("warning", "payment",
                   f"A Stars payment of {stars} XTR was credited, but the answer was lost, so the payer was "
                   f"not told (charge {charge_id}).")
        return
    try:
        await context.bot.refund_star_payment(user_id=user.id, telegram_payment_charge_id=charge_id)
    except Exception as exc:
        await by_hand(f"refunding it failed too ({type(exc).__name__}); its claim is staked, so no later "
                      f"delivery of it credits it")
        return
    await mark_star_transaction(payload, "refunded", charge_id)
    await tell("topup_refunded")
    emit_event("warning", "payment",
               f"A Stars payment of {stars} XTR could not be credited ({type(failure).__name__}), so it was "
               f"refunded (charge {charge_id}).")


async def _credit_topup(update_or_chat, context, user, amount: int, lang: str,
                        payload: str, charge_id: str | None, sandbox: bool = False) -> str:
    if sandbox:
        result = await asyncio.to_thread(family_link.topup, user.id, amount, "sandbox top-up", charge_id)
        await mark_star_transaction(payload, "paid", charge_id)
    else:
        result = await credit_stars_payment(update_or_chat, context, user, amount, lang, payload, charge_id,
                                            "stars top-up")
    if result is None:
        return None
    text = i18n.t(lang, "topup_thanks", stars=result["stars"],
                  credited=result["credited"], balance=result["balance"],
                  total=result["credited"] + result["bonus"], convert_bot=_convert_bot_name())
    if result["bonus"]:
        text += "\n" + i18n.t(lang, "topup_thanks_bonus", bonus=result["bonus"],
                                  date=result["bonus_expires"].strftime("%d.%m.%Y")
                                  if result.get("bonus_expires") else "?")
    text += "\n\n" + i18n.t(lang, "credit_cannot_be_withdrawn")
    if sandbox:
        text = i18n.t(lang, "sandbox_notice") + "\n\n" + text
    emit_event(
        "info", "payment",
        ("SANDBOX top-up (nothing charged): " if sandbox else "Top-up: ")
        + f"{result['stars']} XTR -> {result['credited']} credit"
        + (f" +{result['bonus']} bonus" if result["bonus"] else ""),
    )
    return text


async def _send_donation_invoice(chat_id: int, user, context, amount: int, lang: str, currency: str = "XTR") -> str | None:
    if currency != "XTR" and not _fiat_provider_token(currency):
        return i18n.t(lang, "donate_currency_not_configured", currency=currency)
    payload = f"donate:{uuid.uuid4().hex}"
    await asyncio.to_thread(
        db.record_star_invoice, user.id, user.username, amount, "donation", payload,
        "invoiced", currency,
    )
    if STARS_SANDBOX and currency == "XTR":
        text = await _credit_topup(chat_id, context, user, amount, lang,
                                   payload, f"sandbox:{payload}", sandbox=True)
        await context.bot.send_message(chat_id=chat_id, text=text)
        return None

    provider_token = "" if currency == "XTR" else (_fiat_provider_token(currency) or "")
    try:
        await context.bot.send_invoice(
            chat_id=chat_id,
            title=i18n.t(lang, "donate_invoice_title"),
            description=(i18n.t(lang, "donate_invoice_description",
                                credited=(await asyncio.to_thread(family_link.quote_topup, user.id, amount))["total"])
                         if currency == "XTR"
                         else i18n.t(lang, "donate_invoice_description_fiat")),
            payload=payload,
            provider_token=provider_token,
            currency=currency,
            prices=[LabeledPrice(i18n.t(lang, "donate_invoice_label"), amount)],
        )
        return None
    except Exception as exc:
        await asyncio.to_thread(db.update_star_transaction, payload, "failed")
        return i18n.t(lang, "donate_invoice_error", error=exc)


def _validate_donation_amount(whole_amount: int, currency: str, lang: str) -> tuple[int | None, str | None]:
    if currency == "XTR":
        if whole_amount > MAX_DONATION_STARS:
            return None, i18n.t(lang, "donate_too_many_stars", max=MAX_DONATION_STARS)
        return whole_amount, None
    cfg = FIAT_CURRENCIES[currency]
    amount = whole_amount * (10 ** cfg["exp"])
    if amount < cfg["min_minor"] or amount > cfg["max_minor"]:
        lo = cfg["min_minor"] / (10 ** cfg["exp"])
        hi = cfg["max_minor"] / (10 ** cfg["exp"])
        return None, i18n.t(lang, "donate_out_of_range", currency=currency, lo=f"{lo:g}", hi=f"{hi:g}", symbol=cfg["symbol"])
    return amount, None


async def donate_command(update, context) -> None:
    lang = await i18n.get_lang(update.effective_user.id, context)
    if context.args:
        raw = context.args[0].replace(",", "")
        currency = "XTR"
        if len(context.args) > 1:
            requested = context.args[1].upper()
            if requested not in FIAT_CURRENCIES:
                await update.message.reply_text(i18n.t(lang, "donate_unknown_currency", currency=context.args[1]))
                return
            if not _fiat_provider_token(requested):
                await update.message.reply_text(i18n.t(lang, "donate_currency_not_configured", currency=requested))
                return
            currency = requested
        if not raw.lstrip("-").isdigit() or int(raw) <= 0:
            await update.message.reply_text(i18n.t(lang, "donate_invalid_amount"))
            return

        amount, validation_error = _validate_donation_amount(int(raw), currency, lang)
        if validation_error:
            await update.message.reply_text(validation_error)
            return

        error = await _send_donation_invoice(update.effective_chat.id, update.effective_user, context, amount, lang, currency)
        if error:
            await update.message.reply_text(error)
        return

    fiat_options = _available_fiat_currencies()
    columns = [("XTR", DONATE_STAR_OPTIONS, "⭐")] + [
        (ccy, FIAT_CURRENCIES[ccy]["options"], FIAT_CURRENCIES[ccy]["symbol"]) for ccy in fiat_options
    ]
    kb_rows = [
        [
            InlineKeyboardButton(
                _donate_button_label(ccy, amount, symbol),
                callback_data=f"donate:{amount}" if ccy == "XTR" else f"donatefiat:{ccy}:{amount}",
            )
            for (ccy, _, symbol), amount in zip(columns, row)
        ]
        for row in zip(*(options for _, options, _ in columns))
    ]
    kb_rows.append([
        InlineKeyboardButton(i18n.t(lang, "donate_custom_button", symbol=symbol), callback_data=f"donatecustom:{ccy}")
        for ccy, _, symbol in columns
    ])
    kb = InlineKeyboardMarkup(kb_rows)

    totals = await asyncio.to_thread(family_link.star_totals, update.effective_user.id)
    multiplier, left = family_link.ladder_position(totals["stars_paid"])
    base_rate = family_link.credit_for_stars(1)
    if left:
        credit_line = i18n.t(lang, "donate_prompt_credit", left=left, mult=f"{multiplier:g}",
                             each=f"{base_rate * multiplier:g}", rate=base_rate,
                             days=family_link.BONUS_EXPIRY_DAYS)
    else:
        credit_line = i18n.t(lang, "donate_prompt_credit_base", rate=base_rate)
    prompt = i18n.t(lang, "donate_prompt") + "\n\n" + credit_line
    await update.message.reply_text(prompt, reply_markup=kb)


def _donate_button_label(ccy: str, amount: int, symbol: str) -> str:
    return f"{amount} {symbol}"


async def donate_amount_chosen(update, context) -> None:
    query = update.callback_query
    lang = await i18n.get_lang(update.effective_user.id, context)
    _, _, tail = (query.data or "").partition(":")
    if not tail.isdigit() or int(tail) not in DONATE_STAR_OPTIONS:
        await query.answer(i18n.t(lang, "donate_invalid_amount"), show_alert=True)
        return
    await query.answer()
    error = await _send_donation_invoice(
        query.message.chat_id, update.effective_user, context, int(tail), lang
    )
    if error:
        await context.bot.send_message(chat_id=query.message.chat_id, text=error)


async def donate_fiat_amount_chosen(update, context) -> None:
    query = update.callback_query
    lang = await i18n.get_lang(update.effective_user.id, context)
    parts = (query.data or "").split(":", 2)
    currency = parts[1] if len(parts) == 3 else ""
    whole_amount = parts[2] if len(parts) == 3 else ""
    cfg = FIAT_CURRENCIES.get(currency)
    if (cfg is None or currency not in _available_fiat_currencies()
            or not whole_amount.isdigit() or int(whole_amount) not in cfg["options"]):
        await query.answer(i18n.t(lang, "donate_invalid_amount"), show_alert=True)
        return
    amount = int(whole_amount) * (10 ** cfg["exp"])
    await query.answer()
    error = await _send_donation_invoice(query.message.chat_id, update.effective_user, context, amount, lang, currency)
    if error:
        await context.bot.send_message(chat_id=query.message.chat_id, text=error)


async def donate_custom_button_chosen(update, context) -> None:
    query = update.callback_query
    lang = await i18n.get_lang(update.effective_user.id, context)
    _, _, currency = (query.data or "").partition(":")
    if currency != "XTR" and currency not in _available_fiat_currencies():
        await query.answer(i18n.t(lang, "donate_invalid_amount"), show_alert=True)
        return
    await query.answer()
    context.user_data["donate_custom_currency"] = currency
    unit = i18n.t(lang, "stars_unit") if currency == "XTR" else FIAT_CURRENCIES[currency]["label"]
    prompt = await context.bot.send_message(
        chat_id=query.message.chat_id,
        text=i18n.t(lang, "donate_custom_ask", unit=unit),
        reply_markup=ForceReply(selective=True, input_field_placeholder="e.g. 500"),
    )
    remember_force_reply(context, prompt)


async def donate_custom_amount_received(update, context) -> None:
    currency = context.user_data.get("donate_custom_currency")
    if not currency:
        return
    context.user_data.pop("donate_custom_currency", None)
    context.user_data.pop(FORCE_REPLY_KEY, None)
    lang = await i18n.get_lang(update.effective_user.id, context)

    raw = (update.message.text or "").strip().replace(",", "")
    if not raw.lstrip("-").isdigit() or int(raw) <= 0:
        await update.message.reply_text(i18n.t(lang, "donate_invalid_amount_retry"))
        raise ApplicationHandlerStop

    amount, validation_error = _validate_donation_amount(int(raw), currency, lang)
    if validation_error:
        await update.message.reply_text(validation_error)
        raise ApplicationHandlerStop

    error = await _send_donation_invoice(update.effective_chat.id, update.effective_user, context, amount, lang, currency)
    if error:
        await update.message.reply_text(error)
    raise ApplicationHandlerStop


async def donation_precheckout(query) -> None:
    await query.answer(ok=True)


async def donation_payment_received(update, context) -> None:
    sp = update.message.successful_payment
    user = update.effective_user
    lang = await payer_language(update, context)
    if sp.currency == "XTR":
        text = await _credit_topup(update, context, user, sp.total_amount, lang,
                                   sp.invoice_payload, sp.telegram_payment_charge_id)
        if text is None:
            return
        await update.message.reply_text(text)
    else:
        await mark_star_transaction(sp.invoice_payload, "paid", sp.telegram_payment_charge_id)
        emit_event(
            "info", "payment",
            f"Donation received: {format_ledger_amount(sp.total_amount, sp.currency)}",
        )
        await update.message.reply_text(i18n.t(lang, "donate_thanks", amount=sp.total_amount))
    await unpin_donation_nudge(context, user.id, update.effective_chat.id)


_LEDGER_LABELS = {"refund": "returned"}

BALANCE_RECENT_ROWS = 8


async def balance_command(update, context) -> None:
    user = update.effective_user
    lang = await i18n.get_lang(user.id, context)
    totals = await asyncio.to_thread(family_link.star_totals, user.id)
    rows = await asyncio.to_thread(family_link.star_ledger_for, user.id, BALANCE_RECENT_ROWS)
    lines = [i18n.t(lang, "balance_header", balance=totals["balance"])]
    if totals["stars_paid"] > 0 or totals["spent"] > 0:
        lines.append(i18n.t(lang, "balance_totals", paid=totals["stars_paid"],
                            credited=totals["topped_up"], spent=totals["spent"]))
    lines.append("")
    if totals.get("bonus"):
        lines.append(i18n.t(lang, "balance_bonus_line", bonus=totals["bonus"],
                            soon=totals["bonus_next_amount"],
                            date=totals["bonus_next_expiry"].strftime("%d.%m.%Y")))
    multiplier, left = family_link.ladder_position(totals["stars_paid"])
    base_rate = family_link.credit_for_stars(1)
    if left:
        lines.append(i18n.t(lang, "balance_rate", left=left, mult=f"{multiplier:g}",
                            each=f"{base_rate * multiplier:g}"))
    else:
        lines.append(i18n.t(lang, "balance_rate_base", rate=base_rate))
    lines.append(i18n.t(lang, "credit_cannot_be_withdrawn"))
    if rows:
        lines.append("")
        lines.append(i18n.t(lang, "balance_recent"))
        for row in rows:
            when = row["occurred_at"].strftime("%d %b")
            sign = "+" if row["delta"] > 0 else ""
            lines.append(f"  {when}  {sign}{row['delta']} ⚡  "
                         f"{_LEDGER_LABELS.get(row['reason'], row['reason'])}")
    if not rows and totals["balance"] == 0:
        lines.append("")
        lines.append(i18n.t(lang, "balance_empty_hint"))
    await update.message.reply_text("\n".join(lines))


async def donation_precheckout_callback(update, context) -> None:
    query = update.pre_checkout_query
    if not query.invoice_payload.startswith("donate:"):
        await query.answer(ok=False, error_message="Unknown order.")
        return
    await donation_precheckout(query)


async def donation_payment_callback(update, context) -> None:
    sp = update.message.successful_payment
    if not sp.invoice_payload.startswith("donate:"):
        return
    await donation_payment_received(update, context)


async def publish_profile(application) -> None:
    logger = logging.getLogger(__name__)
    for language in (None,) + tuple(i18n.SUPPORTED_LANGUAGES):
        lang = language or "en"
        try:
            await application.bot.set_my_short_description(
                short_description=i18n.t(lang, "bot_short_description"),
                language_code=language,
            )
            await application.bot.set_my_description(
                description=i18n.t(lang, "bot_description"),
                language_code=language,
            )
        except Exception:
            logger.warning("Could not publish the %s profile text.",
                           language or "default", exc_info=True)


def _menu_in(commands, language: str, table_name: str = "COMMAND_MENU"):
    table = getattr(i18n, table_name, {}).get(language) or {}
    return [BotCommand(c.command, table.get(c.command) or c.description)
            for c in commands]


MENU_SIGNATURE_KEY = "_menu_sig"
_MENU: dict = {}


def _menu_for(user_id: int, lang: "str | None"):
    public = _MENU.get("public")
    if public is None:
        return None
    commands = list(public) if lang in (None, "en") else _menu_in(public, lang)
    if user_id in _MENU.get("admin_ids", ()):
        commands = list(commands) + list(_MENU.get("admin_only", ()))
    return commands


def _menu_signature(commands) -> str:
    text = "\n".join(f"{c.command}\t{c.description}" for c in commands)
    return format(zlib.crc32(text.encode("utf-8")), "08x")


async def refresh_chat_menu(context, user_id: int, lang: "str | None") -> None:
    commands = _menu_for(user_id, lang)
    if not commands:
        return
    try:
        await context.bot.set_my_commands(commands, scope=BotCommandScopeChat(chat_id=user_id))
    except Exception:
        logging.getLogger(__name__).debug("Could not set the chat menu for %s", user_id, exc_info=True)
        return
    context.user_data[MENU_SIGNATURE_KEY] = _menu_signature(commands)


_UNOWNED_SCOPES = (
    BotCommandScopeAllPrivateChats, BotCommandScopeAllGroupChats, BotCommandScopeAllChatAdministrators,
)


async def publish_commands(application, public, admin_only=(), admin_ids=(), group=()) -> None:
    logger = logging.getLogger(__name__)
    public = list(public)
    _MENU.update(public=public, admin_only=list(admin_only), admin_ids=set(admin_ids))

    for language in (None,) + tuple(i18n.SUPPORTED_LANGUAGES):
        try:
            await application.bot.set_my_commands(
                public if language in (None, "en") else _menu_in(public, language),
                language_code=language,
            )
        except Exception:
            logger.warning("Could not publish the %s command menu.",
                           language or "default", exc_info=True)

    group = list(group)
    for scope in _UNOWNED_SCOPES:
        for language in (None,) + tuple(i18n.SUPPORTED_LANGUAGES):
            try:
                if group and scope is BotCommandScopeAllGroupChats:
                    await application.bot.set_my_commands(
                        group if language in (None, "en") else _menu_in(group, language, "GROUP_COMMAND_MENU"),
                        scope=scope(), language_code=language)
                    continue
                await application.bot.delete_my_commands(scope=scope(), language_code=language)
            except Exception:
                logger.debug("Could not clear the %s menu (%s).", scope.__name__,
                             language or "untagged", exc_info=True)

    if not admin_only:
        return
    for admin_id in sorted(admin_ids):
        lang = None
        try:
            lang = await asyncio.to_thread(db.get_user_language, admin_id)
        except Exception:
            logger.debug("Could not read the language of admin %s", admin_id, exc_info=True)
        try:
            await application.bot.set_my_commands(
                _menu_for(admin_id, lang),
                scope=BotCommandScopeChat(chat_id=admin_id),
            )
        except Exception:
            logger.warning("Could not publish the operator's command menu to %s.",
                           admin_id, exc_info=True)


NO_PREVIEW = LinkPreviewOptions(is_disabled=True)


def esc(text: str) -> str:
    return (str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def heading(text: str) -> str:
    return f"<b>{esc(text)}</b>"


_COMMAND_RE = re.compile(r"(?<![\w/])(/[a-z][a-z0-9_]{1,30})\b")


def body(text: str) -> str:
    return _COMMAND_RE.sub(r"<code>\1</code>", esc(text))


def lead_in(text: str, limit: int = 28) -> str:
    label, colon, rest = text.partition(":")
    if not colon or "\n" in label or len(label) > limit:
        return body(text)
    return f"<b>{esc(label)}:</b>{body(rest)}"


def collapsed(text: str) -> str:
    return f"<blockquote expandable>{body(text)}</blockquote>"


def quoted(text: str) -> str:
    return f"<blockquote>{body(text)}</blockquote>"


def joined(*blocks) -> str:
    return "\n\n".join(block for block in blocks if block)


_TAG_RE = re.compile(r"<[^>]+>")


def _plain(text: str) -> str:
    return (_TAG_RE.sub("", text).replace("&lt;", "<")
            .replace("&gt;", ">").replace("&amp;", "&"))


async def reply_formatted(message, text: str, **kwargs):
    try:
        return await message.reply_text(
            text, parse_mode=ParseMode.HTML, link_preview_options=NO_PREVIEW, **kwargs
        )
    except BadRequest as exc:
        if "parse" not in str(exc).lower() and "entit" not in str(exc).lower():
            raise
        logging.getLogger(__name__).warning(
            "This Telegram server would not parse the formatting (%s); sending plain.", exc
        )
        return await message.reply_text(
            _plain(text), link_preview_options=NO_PREVIEW, **kwargs
        )


OPERATOR_CONTACT = (os.environ.get("OPERATOR_CONTACT") or "mukhtorovmurodbek@gmail.com").strip()
PRIVACY_URL = os.environ.get("PRIVACY_URL", "").strip()
TERMS_URL = os.environ.get("TERMS_URL", "").strip()


def _policy_footer(lang: str, url: str) -> str:
    lines = []
    if url:
        label = esc(url)
        lines.append(body(i18n.t(lang, "policy_full_text", url="\x00"))
                     .replace("\x00", f'<a href="{label}">{label}</a>'))
    if OPERATOR_CONTACT:
        lines.append(body(i18n.t(lang, "policy_contact", contact=OPERATOR_CONTACT)))
    return "\n".join(lines)


def privacy_text(lang: str) -> str:
    return joined(
        heading(i18n.t(lang, "privacy_heading")),
        heading(i18n.t(lang, "privacy_kept_heading")) + "\n"
        + collapsed(i18n.t(lang, "privacy_stored")),
        heading(i18n.t(lang, "privacy_problems_heading")) + "\n"
        + collapsed(i18n.t(lang, "privacy_problems")),
        heading(i18n.t(lang, "privacy_feedback_heading")) + "\n"
        + body(i18n.t(lang, "privacy_feedback")),
        heading(i18n.t(lang, "privacy_seen_by_heading")) + "\n"
        + body(i18n.t(lang, "privacy_seen_by")) + "\n"
        + body(i18n.t(lang, "privacy_others")),
        heading(i18n.t(lang, "privacy_kept_for_heading")) + "\n"
        + collapsed(i18n.t(lang, "privacy_kept_for")),
        lead_in(i18n.t(lang, "privacy_your_choices")),
        _policy_footer(lang, PRIVACY_URL),
    )


def terms_text(lang: str) -> str:
    return joined(
        heading(i18n.t(lang, "terms_heading")),
        body(i18n.t(lang, "terms_use")),
        lead_in(i18n.t(lang, "terms_specific")),
        lead_in(i18n.t(lang, "terms_money")),
        lead_in(i18n.t(lang, "terms_no_warranty")),
        _policy_footer(lang, TERMS_URL),
    )


async def privacy_command(update, context) -> None:
    lang = await i18n.get_lang(update.effective_user.id, context)
    await reply_formatted(update.message, privacy_text(lang))


async def terms_command(update, context) -> None:
    lang = await i18n.get_lang(update.effective_user.id, context)
    await reply_formatted(update.message, terms_text(lang))


ERASE_PREFIX = "erasedata:"


def erase_keyboard(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(i18n.t(lang, "delete_data_button_yes"), callback_data=ERASE_PREFIX + "yes"),
        InlineKeyboardButton(i18n.t(lang, "delete_data_button_no"), callback_data=ERASE_PREFIX + "no"),
    ]])


async def paysupport_command(update, context) -> None:
    user = update.effective_user
    lang = await i18n.get_lang(user.id, context)
    await update.message.reply_text(i18n.t(
        lang, "paysupport_text", contact=OPERATOR_CONTACT or "@BotFather", user_id=user.id))


async def delete_my_data_command(update, context):
    lang = await i18n.get_lang(update.effective_user.id, context)
    await reply_formatted(
        update.message,
        joined(heading(i18n.t(lang, "delete_data_confirm")),
               body(i18n.t(lang, "delete_data_consequences"))),
        reply_markup=erase_keyboard(lang),
    )


async def delete_my_data_chosen(update, context):
    query = update.callback_query
    await query.answer()
    lang = await i18n.get_lang(update.effective_user.id, context)
    if query.data != ERASE_PREFIX + "yes":
        await live_message.edit_in_place(
            query.message, context.bot, body(i18n.t(lang, "delete_data_kept")), parse_mode=ParseMode.HTML
        )
        return None

    user_id = update.effective_user.id
    try:
        await asyncio.to_thread(note_erased, user_id)
    except Exception:
        logging.getLogger(__name__).exception("Could not keep an erased user out of the activity flush")
    try:
        rows = await asyncio.to_thread(db.erase_user, user_id)
    except Exception:
        logging.getLogger(__name__).exception("Erasing a user's data failed")
        await live_message.edit_in_place(
            query.message, context.bot, body(i18n.t(lang, "delete_data_failed")), parse_mode=ParseMode.HTML
        )
        return None
    await asyncio.to_thread(reserve.note_erasure, user_id)
    try:
        rows += await asyncio.to_thread(lifecycle.forget_user, user_id)
    except Exception:
        logging.getLogger(__name__).exception(
            "Cleared a user's data but could not clear their saved state"
        )
    try:
        rows += await asyncio.to_thread(family_link.forget_problem_reporter, user_id)
    except Exception:
        logging.getLogger(__name__).exception(
            "Cleared a user's data but could not take them off their problem reports"
        )

    done = body(i18n.t(lang, "delete_data_done", rows=rows))
    try:
        context.application.drop_user_data(user_id)
    except Exception:
        logging.getLogger(__name__).debug("Nothing cached to drop for the erased user")
    await live_message.edit_in_place(
        query.message, context.bot, done, parse_mode=ParseMode.HTML
    )
    return ConversationHandler.END


FORCE_REPLY_KEY = "force_reply_msg_id"


def remember_force_reply(context, message) -> None:
    context.user_data[FORCE_REPLY_KEY] = message.message_id


async def release_force_reply(update, context, stored_only: bool = False) -> bool:
    chat = update.effective_chat
    if chat is None:
        return False
    ids = []
    stored = context.user_data.pop(FORCE_REPLY_KEY, None)
    if stored:
        ids.append(stored)
    replied_to = None if stored_only else getattr(update.effective_message, "reply_to_message", None)
    if replied_to is not None and replied_to.message_id not in ids:
        ids.append(replied_to.message_id)
    released = False
    for message_id in ids:
        try:
            await context.bot.delete_message(chat_id=chat.id, message_id=message_id)
            released = True
        except Exception:
            logging.getLogger(__name__).debug(
                "Could not delete force-reply prompt %s", message_id, exc_info=True
            )
    return released


def reset_user_state(context, keep: dict | None = None) -> None:
    preserved = {k: context.user_data[k] for k in (FORCE_REPLY_KEY, "_last_seen") if k in context.user_data}
    context.user_data.clear()
    context.user_data.update(preserved)
    if keep:
        context.user_data.update(keep)


CancelItem = namedtuple("CancelItem", "key label button")

CANCEL_PICK_PREFIX = "cancelpick:"
CANCEL_PICK_ALL = "all"
CANCEL_PICK_NONE = "none"


def cancel_items(context, lang: str) -> list[CancelItem]:
    items = []
    if context.user_data.get("donate_custom_currency"):
        items.append(CancelItem("donation",
                                i18n.t(lang, "cancel_item_donation"),
                                i18n.t(lang, "cancel_button_donation")))
    if context.user_data.get(FEEDBACK_PROMPT_KEY):
        items.append(CancelItem("feedback",
                                i18n.t(lang, "cancel_item_feedback"),
                                i18n.t(lang, "cancel_button_feedback")))
    return items


def cancel_shared_item(context, lang: str, key: str) -> str | None:
    if key == "donation" and context.user_data.pop("donate_custom_currency", None):
        return i18n.t(lang, "cancel_item_donation")
    if key == "feedback" and context.user_data.pop(FEEDBACK_PROMPT_KEY, None):
        return i18n.t(lang, "cancel_item_feedback")
    return None


def cancel_question(lang: str, items: list[CancelItem]):
    text = i18n.t(lang, "cancel_ask") + "\n" + "\n".join(
        f"\u2022 {item.label}" for item in items
    )
    rows = [[InlineKeyboardButton(item.button, callback_data=CANCEL_PICK_PREFIX + item.key)]
            for item in items]
    if len(items) > 1:
        rows.append([InlineKeyboardButton(i18n.t(lang, "cancel_button_all"),
                                          callback_data=CANCEL_PICK_PREFIX + CANCEL_PICK_ALL)])
    rows.append([InlineKeyboardButton(i18n.t(lang, "cancel_button_none"),
                                      callback_data=CANCEL_PICK_PREFIX + CANCEL_PICK_NONE)])
    return text, InlineKeyboardMarkup(rows)


async def ask_cancel_choice(update, context, items: list[CancelItem], lang: str) -> bool:
    if not items:
        return False
    replied_to = getattr(update.effective_message, "reply_to_message", None)
    if replied_to is not None and FORCE_REPLY_KEY not in context.user_data:
        context.user_data[FORCE_REPLY_KEY] = replied_to.message_id
    text, keyboard = cancel_question(lang, items)
    await update.effective_message.reply_text(text, reply_markup=keyboard)
    return True


def cancel_choice_key(update) -> str:
    return update.callback_query.data.split(":", 1)[1]


def build_cancel_text(lang: str, stopped: list[str]) -> str:
    if not stopped:
        return i18n.t(lang, "cancel_nothing")
    return i18n.t(lang, "cancel_header") + "\n" + "\n".join(f"\u2022 {item}" for item in stopped)


async def finish_cancel(update, context, lang: str, stopped: list[str],
                        stored_only: bool = False, keyboard=None) -> None:
    released = await release_force_reply(update, context, stored_only=stored_only)
    if released and not stopped:
        stopped = [i18n.t(lang, "cancel_item_stale_prompt")]
    message = update.effective_message
    if message is None:
        return
    await message.reply_text(
        build_cancel_text(lang, stopped),
        reply_markup=keyboard or ReplyKeyboardRemove(),
    )


async def finish_cancel_choice(update, context, lang: str, stopped: list[str], keyboard=None) -> None:
    query = update.callback_query
    had_prompt = FORCE_REPLY_KEY in context.user_data
    released = await release_force_reply(update, context, stored_only=True)
    if released and not stopped:
        stopped = [i18n.t(lang, "cancel_item_stale_prompt")]
    text = build_cancel_text(lang, stopped)
    await live_message.edit_in_place(query.message, context.bot, text)
    if had_prompt and not released:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=i18n.t(lang, "cancel_reply_box_freed"),
            reply_markup=keyboard or ReplyKeyboardRemove(),
        )


async def keep_going(update, context, lang: str) -> None:
    await live_message.edit_in_place(
        update.callback_query.message, context.bot, i18n.t(lang, "cancel_kept")
    )


FEEDBACK_PROMPT_KEY = "feedback_prompt"
FEEDBACK_PER_DAY = int(os.environ.get("FEEDBACK_PER_DAY", "5"))
FEEDBACK_MAX_CHARS = 4096
_COMMAND_WORD = re.compile(r"^/\S*\s*")

_feedback_sent: "OrderedDict[int, deque]" = OrderedDict()


def _feedback_room(user_id: int) -> bool:
    if FEEDBACK_PER_DAY <= 0:
        return True
    window = _feedback_sent.get(user_id)
    if not window:
        return True
    now = time.monotonic()
    while window and now - window[0] > 86400:
        window.popleft()
    return len(window) < FEEDBACK_PER_DAY


def _feedback_used(user_id: int) -> None:
    window = _feedback_sent.setdefault(user_id, deque())
    _feedback_sent.move_to_end(user_id)
    window.append(time.monotonic())
    _flood_trim(_feedback_sent)


async def _ask_for_feedback(message, context, lang: str, key: str = "feedback_ask") -> None:
    prompt = await message.reply_text(
        i18n.t(lang, key),
        reply_markup=ForceReply(selective=True,
                                input_field_placeholder=i18n.t(lang, "feedback_placeholder")),
    )
    context.user_data[FEEDBACK_PROMPT_KEY] = prompt.message_id
    remember_force_reply(context, prompt)


async def _take_feedback(update, context, lang: str, text: str) -> None:
    message = update.effective_message
    user_id = update.effective_user.id
    if not _feedback_room(user_id):
        await message.reply_text(i18n.t(lang, "feedback_limit", per_day=FEEDBACK_PER_DAY))
        return
    try:
        await asyncio.to_thread(family_link.save_feedback, lang, text[:FEEDBACK_MAX_CHARS])
    except Exception:
        logging.getLogger(__name__).warning("Could not keep a piece of feedback", exc_info=True)
        await message.reply_text(i18n.t(lang, "feedback_failed"))
        return
    _feedback_used(user_id)
    await message.reply_text(i18n.t(lang, "feedback_thanks"))


async def feedback_command(update, context) -> None:
    message = update.effective_message
    lang = await i18n.get_lang(update.effective_user.id, context)
    text = _COMMAND_WORD.sub("", message.text or "", count=1).strip()
    if text:
        await _take_feedback(update, context, lang, text)
        return
    await _ask_for_feedback(message, context, lang)


def _answers_feedback_prompt(update, context) -> bool:
    prompt = context.user_data.get(FEEDBACK_PROMPT_KEY)
    replied = getattr(update.effective_message, "reply_to_message", None)
    return bool(prompt) and replied is not None and replied.message_id == prompt


async def awaited_text_received(update, context) -> None:
    message = update.effective_message
    if message is None:
        return
    if _answers_feedback_prompt(update, context):
        context.user_data.pop(FEEDBACK_PROMPT_KEY, None)
        context.user_data.pop(FORCE_REPLY_KEY, None)
        lang = await i18n.get_lang(update.effective_user.id, context)
        text = (message.text or "").strip()
        if text:
            await _take_feedback(update, context, lang, text)
        else:
            await _ask_for_feedback(message, context, lang, "feedback_text_only")
        raise ApplicationHandlerStop
    if message.text is not None:
        await donate_custom_amount_received(update, context)


_LOG_FORMAT = "%(asctime)s %(name)s %(levelname)s %(message)s"

_recent_errors: deque[tuple[str, str]] = deque(maxlen=10)
_error_count = 0

_event_hook = None


def set_event_hook(fn) -> None:
    global _event_hook
    _event_hook = fn


def emit_event(level: str, kind: str, message: str, details: str | None = None,
               keep: bool = False) -> None:
    if _event_hook is None:
        if keep:
            logging.getLogger(__name__).error("Not on the family bus, so this did not reach ManagerBot: %s",
                                              message)
        return
    try:
        _event_hook(level, kind, message, details, keep=keep)
    except Exception:
        logging.getLogger(__name__).warning("Family event hook failed", exc_info=True)


async def tell_owner_directly(bot, text: str) -> bool:
    if not REPORTS_TO or bot is None:
        return False
    try:
        await bot.send_message(chat_id=REPORTS_TO, text=redact.redact(text)[:4000])
        return True
    except Exception as exc:
        logging.getLogger(__name__).warning("Could not message the operator directly (%s)", type(exc).__name__)
        return False


def _log_to_files() -> bool:
    override = os.environ.get("LOG_TO_FILES")
    if override is not None:
        return override.strip().lower() in ("1", "true", "yes", "on")
    return not (os.environ.get("RAILWAY_ENVIRONMENT_NAME") or os.environ.get("RAILWAY_ENVIRONMENT"))


def setup_logging(bot_file: str) -> None:
    fmt = logging.Formatter(_LOG_FORMAT)

    root = logging.getLogger()
    root.setLevel(logging.INFO)

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    root.addHandler(console)

    for noisy in ("httpx", "httpcore", "telegram.ext.Updater", "apscheduler"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    if not _log_to_files():
        return

    log_dir = os.path.join(os.path.dirname(os.path.abspath(bot_file)), "logs")
    os.makedirs(log_dir, exist_ok=True)

    info_file = RotatingFileHandler(
        os.path.join(log_dir, "bot.log"), maxBytes=2_000_000, backupCount=3, encoding="utf-8"
    )
    info_file.setFormatter(fmt)
    root.addHandler(info_file)

    error_file = RotatingFileHandler(
        os.path.join(log_dir, "errors.log"), maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    error_file.setLevel(logging.WARNING)
    error_file.setFormatter(fmt)
    root.addHandler(error_file)


TRANSIENT_NETWORK_ERRORS = (NetworkError, RetryAfter)

NETWORK_ALERT_AFTER = int(os.environ.get("NETWORK_ALERT_AFTER", "20"))

_network_blips = 0
_network_blips_total = 0
_network_alerted = False


PERMANENT_NETWORK_MESSAGES = ("entity too large", "file is too big", "too big", "too large")


def is_transient_network_error(exc: BaseException) -> bool:
    if not isinstance(exc, TRANSIENT_NETWORK_ERRORS) or isinstance(exc, BadRequest):
        return False
    text = str(exc).lower()
    return not any(marker in text for marker in PERMANENT_NETWORK_MESSAGES)


def note_network_blip(exc: BaseException) -> None:
    global _network_blips, _network_blips_total, _network_alerted
    _network_blips += 1
    _network_blips_total += 1
    logging.getLogger(__name__).warning(
        "Transient network error (%s): %s -- retried by PTB, %s in a row",
        type(exc).__name__, exc, _network_blips,
    )
    if _network_blips >= NETWORK_ALERT_AFTER and not _network_alerted:
        _network_alerted = True
        emit_event(
            "warning", "network",
            f"{_network_blips} network errors in a row -- this bot may not be "
            f"reaching Telegram. Latest: {type(exc).__name__}: {exc}",
        )


def note_network_ok() -> None:
    global _network_blips, _network_alerted
    if _network_blips and _network_alerted:
        emit_event("info", "network", "Telegram is reachable again.")
    _network_blips = 0
    _network_alerted = False


CONFLICT_ALERT_COUNT = 3
CONFLICT_ALERT_WINDOW_S = 120
CONFLICT_QUIET_RESET_S = 600

_conflict_times: "deque[float]" = deque(maxlen=CONFLICT_ALERT_COUNT)
_conflict_last: "float | None" = None
_conflict_alerted = False


def note_conflict(exc: BaseException) -> None:
    global _conflict_last, _conflict_alerted
    now = time.monotonic()
    if _conflict_last is not None and now - _conflict_last >= CONFLICT_QUIET_RESET_S:
        _conflict_times.clear()
        _conflict_alerted = False
    _conflict_last = now
    _conflict_times.append(now)
    in_window = sum(1 for seen in _conflict_times if now - seen <= CONFLICT_ALERT_WINDOW_S)
    logging.getLogger(__name__).warning(
        "Conflict on getUpdates: %s within the last %s s (a report needs %s)",
        in_window, CONFLICT_ALERT_WINDOW_S, CONFLICT_ALERT_COUNT,
    )
    if in_window >= CONFLICT_ALERT_COUNT and not _conflict_alerted:
        _conflict_alerted = True
        message = (
            "Another copy of this bot seems to be polling with the same token: "
            f"{in_window} conflicts within {CONFLICT_ALERT_WINDOW_S} seconds"
        )
        logging.getLogger(__name__).error(message)
        report = Conflict(message)
        report.__cause__ = exc
        record_error(report)


def record_error(exc: BaseException) -> None:
    global _error_count
    _error_count += 1
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    _recent_errors.append((stamp, repr(exc)))
    emit_event(
        "error", "crash", f"Unhandled {type(exc).__name__}: {exc}",
        "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
    )


def error_summary() -> str:
    blips = (
        f"\n\U0001f310 {_network_blips_total} transient network error(s) -- retried, not crashes."
        if _network_blips_total else ""
    )
    if _error_count == 0:
        return "✅ No errors since this instance started." + blips
    lines = [f"⚠️ {_error_count} error(s) since start:"]
    lines.extend(f"  {stamp} — {msg}" for stamp, msg in _recent_errors)
    if _error_count > len(_recent_errors):
        lines.append(f"  (+{_error_count - len(_recent_errors)} earlier, see logs/errors.log)")
    return "\n".join(lines) + blips


REPORTS_TO = int(os.environ.get("FAMILY_REPORTS_TO") or 8796896653)
REPORT_BUTTONS = (os.environ.get("FAMILY_REPORT_BUTTONS") or "on").strip().lower() not in ("0", "off", "no", "false")

PROBLEM_FLUSH_SECONDS = int(os.environ.get("PROBLEM_FLUSH_SECONDS", "20"))
MAX_BUFFERED_PROBLEMS = 500

ALERT_COOLDOWN_SECONDS = int(os.environ.get("PROBLEM_ALERT_COOLDOWN_SECONDS", "900"))
BURST_COUNT = int(os.environ.get("PROBLEM_BURST_COUNT", "15"))
BURST_WINDOW_SECONDS = int(os.environ.get("PROBLEM_BURST_WINDOW_SECONDS", "3600"))

_problem_buffer: "OrderedDict[tuple, object]" = OrderedDict()
_alerted: "dict[str, list]" = {}
_recent_faults: "dict[str, deque]" = {}


def _should_alert(code: str, now: float) -> "tuple[bool, int]":
    last, suppressed = _alerted.get(code, (None, 0))
    if last is not None and now - last < ALERT_COOLDOWN_SECONDS:
        _alerted[code] = [last, suppressed + 1]
        return False, 0
    _alerted[code] = [now, 0]
    return True, suppressed


def _is_burst(code: str, now: float) -> bool:
    seen = _recent_faults.setdefault(code, deque())
    seen.append(now)
    while seen and now - seen[0] > BURST_WINDOW_SECONDS:
        seen.popleft()
    return len(seen) >= BURST_COUNT


def _alert_owner(code: str, incident: str, level: int, now: float) -> None:
    send, suppressed = _should_alert(code, now)
    if not send:
        return
    problem = problems.lookup(code)
    title = problem.title if problem else code
    burst = level != problems.level(code)
    message = f"{code} — {title}"
    if burst:
        message += (f" — {BURST_COUNT}+ of these in the last "
                    f"{BURST_WINDOW_SECONDS // 60} minutes, so this looks like it is down "
                    f"rather than unlucky")
    if suppressed:
        message += f" (+{suppressed} more since the last alert)"
    emit_event("critical" if not burst else "error", "problem",
               message, f"Incident {incident} · /report {incident} for anything a user added")


def note_occurrence(code: str, incident: str, occurred_at) -> None:
    key = (code, incident)
    if key in _problem_buffer:
        return
    _problem_buffer[key] = occurred_at
    level = problems.level(code)
    try:
        now = time.monotonic()
        if level == problems.URGENT:
            _alert_owner(code, incident, problems.URGENT, now)
        elif level == problems.FAULT and _is_burst(code, now):
            _alert_owner(code, incident, problems.URGENT, now)
    except Exception:
        logging.getLogger(__name__).debug("Could not alert on %s", code, exc_info=True)
    while len(_problem_buffer) > MAX_BUFFERED_PROBLEMS:
        _problem_buffer.popitem(last=False)


def _flush_problems_now() -> int:
    global _problem_buffer
    if not _problem_buffer:
        return 0
    batch, _problem_buffer = _problem_buffer, OrderedDict()
    written = 0
    for (code, incident), occurred_at in batch.items():
        try:
            family_link.record_problem_occurrence(code, incident, occurred_at,
                                                  problems.level(code))
            written += 1
        except Exception:
            leftover = OrderedDict(list(batch.items())[written:])
            leftover.update(_problem_buffer)
            _problem_buffer = leftover
            raise
    return written


async def _flush_problems_job(context) -> None:
    try:
        await asyncio.to_thread(_flush_problems_now)
    except Exception:
        logging.getLogger(__name__).debug("Problem flush failed; will retry", exc_info=True)

_chat_langs: "OrderedDict[int, str]" = OrderedDict()


def remember_chat_lang(chat_id, lang) -> None:
    if not chat_id or not lang:
        return
    _chat_langs[chat_id] = lang
    _chat_langs.move_to_end(chat_id)
    while len(_chat_langs) > 4096:
        _chat_langs.popitem(last=False)


DETAILS_AFTER = int(os.environ.get("PROBLEM_DETAILS_AFTER", "3"))
DETAILS_WINDOW_SECONDS = int(os.environ.get("PROBLEM_DETAILS_WINDOW_SECONDS", "3600"))
_repeats: "OrderedDict[tuple, deque]" = OrderedDict()


def offers_details(chat_id, code, count: bool = False, ahead: bool = False) -> bool:
    if not problems.reportable(code):
        return False
    if problems.wants_details(code):
        return True
    if chat_id is None:
        return False
    now = time.monotonic()
    key = (chat_id, code)
    seen = _repeats.get(key) or deque()
    if count:
        seen.append(now)
    while seen and now - seen[0] > DETAILS_WINDOW_SECONDS:
        seen.popleft()
    if seen:
        _repeats[key] = seen
        _repeats.move_to_end(key)
        while len(_repeats) > 4096:
            _repeats.popitem(last=False)
    else:
        _repeats.pop(key, None)
    return len(seen) + (1 if ahead else 0) >= DETAILS_AFTER


def report_markup(lang: str, code: str, incident: str, markup=None, chat_id=None):
    if not REPORT_BUTTONS or not offers_details(chat_id, code, ahead=True):
        return markup
    return _with_details_row(lang, code, incident, markup)


def _with_details_row(lang: str, code: str, incident: str, markup=None):
    if _has_report(markup) or (markup is not None and not isinstance(markup, InlineKeyboardMarkup)):
        return markup
    rows = [list(row) for row in markup.inline_keyboard] if markup is not None else []
    rows.append([InlineKeyboardButton(i18n.t(lang, "report_button"),
                                      callback_data=f"rpt:{code}:{incident}")])
    return InlineKeyboardMarkup(rows)


def _with_logged_note(text: str, code: str, lang: str, offered: bool = False) -> str:
    note = i18n.t(lang, "problem_logged_note_optin" if offered else "problem_logged_note")
    body = problems.strip_code(text).rstrip()
    for known in (i18n.t(lang, "problem_logged_note_optin"), i18n.t(lang, "problem_logged_note")):
        if body.endswith(known):
            body = body[: -len(known)].rstrip()
            break
    return f"{body}\n{note}\n{problems.MARK}{code}"


def _has_report(markup) -> bool:
    return any((getattr(button, "callback_data", None) or "").startswith("rpt")
               for row in getattr(markup, "inline_keyboard", None) or () for button in row)


def _button_incident(markup) -> "str | None":
    for row in getattr(markup, "inline_keyboard", None) or ():
        for button in row:
            data = getattr(button, "callback_data", None) or ""
            if data.startswith("rpt:"):
                return data.rsplit(":", 1)[-1]
    return None


problem_log = logging.getLogger("problems")

_incidents: "OrderedDict[tuple, str]" = OrderedDict()


def _remember_incident(chat_id, message_id, code: str, incident: str) -> None:
    if chat_id is None or message_id is None:
        return
    key = (chat_id, message_id, code)
    _incidents[key] = incident
    _incidents.move_to_end(key)
    while len(_incidents) > 4096:
        _incidents.popitem(last=False)


async def note_problem(chat_id, message_id, text, kwargs: dict):
    code = problems.find_code(text) if isinstance(text, str) else None
    if not code:
        return None, None, text
    markup = kwargs.get("reply_markup")
    made = _button_incident(markup)
    known = _incidents.get((chat_id, message_id, code)) if message_id is not None else None
    incident = made or known or problems.new_incident()
    offered = offers_details(chat_id, code, count=incident != known)
    wants_button = (made is None and REPORT_BUTTONS and offered
                    and (markup is None or isinstance(markup, InlineKeyboardMarkup)))
    if wants_button or problems.reportable(code):
        lang = _chat_langs.get(chat_id)
        if lang is None and isinstance(chat_id, int):
            try:
                lang = await asyncio.to_thread(db.get_user_language, chat_id)
            except Exception:
                lang = None
            remember_chat_lang(chat_id, lang)
        lang = lang or "en"
        if wants_button:
            try:
                kwargs["reply_markup"] = _with_details_row(lang, code, incident, markup)
            except Exception:
                logging.getLogger(__name__).debug("Could not add a report button", exc_info=True)
        try:
            text = _with_logged_note(text, code, lang,
                                     offered=_has_report(kwargs.get("reply_markup")))
        except Exception:
            logging.getLogger(__name__).debug("Could not add the logged note", exc_info=True)
    if incident != known:
        problem_log.info("%s incident %s, %s", code, incident,
                         "with a Report button" if _has_report(kwargs.get("reply_markup"))
                         else "no Report button")
        note_occurrence(code, incident, datetime.now(timezone.utc))
    _remember_incident(chat_id, message_id, code, incident)
    return code, incident, text


def _log_problems_to_file() -> None:
    if any(isinstance(handler, RotatingFileHandler) for handler in problem_log.handlers):
        return
    for handler in logging.getLogger().handlers:
        if isinstance(handler, RotatingFileHandler):
            problem_file = RotatingFileHandler(
                os.path.join(os.path.dirname(handler.baseFilename), "problems.log"),
                maxBytes=1_000_000, backupCount=3, encoding="utf-8")
            problem_file.setFormatter(handler.formatter)
            problem_log.addHandler(problem_file)
            return


def attach_problem_reports(application) -> None:
    _log_problems_to_file()
    bot = getattr(application, "bot", None)
    base = type(bot)
    if bot is None or getattr(base, "_reports_problems", False) or not hasattr(base, "send_message"):
        return

    async def send_message(self, chat_id, text, *args, **kwargs):
        code, incident, text = await note_problem(chat_id, None, text, kwargs)
        sent = await base.send_message(self, chat_id, text, *args, **kwargs)
        if code:
            _remember_incident(chat_id, getattr(sent, "message_id", None), code, incident)
        return sent

    async def edit_message_text(self, text, *args, **kwargs):
        chat_id = kwargs.get("chat_id", args[0] if args else None)
        message_id = kwargs.get("message_id", args[1] if len(args) > 1 else None)
        _, _, text = await note_problem(chat_id, message_id, text, kwargs)
        return await base.edit_message_text(self, text, *args, **kwargs)

    async def answer_callback_query(self, callback_query_id, *args, **kwargs):
        text = kwargs.get("text", args[0] if args else None)
        code = problems.find_code(text) if isinstance(text, str) else None
        if code:
            incident = problems.new_incident()
            problem_log.info("%s incident %s, a pop-up", code, incident)
            note_occurrence(code, incident, datetime.now(timezone.utc))
        return await base.answer_callback_query(self, callback_query_id, *args, **kwargs)

    reporting = type(base.__name__, (base,), {
        "__slots__": (), "__module__": base.__module__, "_reports_problems": True,
        "send_message": send_message, "edit_message_text": edit_message_text,
        "answer_callback_query": answer_callback_query,
    })
    try:
        object.__setattr__(bot, "__class__", reporting)
    except Exception:
        logging.getLogger(__name__).warning(
            "Problem logging and Report buttons are off: this bot's send methods could not be wrapped",
            exc_info=True)


async def _send_report_to_owner(bot, code: str, incident: str, occurred_at) -> None:
    if not problems.urgent(code):
        return
    label = os.environ.get("FAMILY_LABEL") or getattr(family_link, "_display_name", None) \
        or family_link._bot_id or "a bot"
    text = (f"🙋 Details provided for {incident} — {label}\n"
            f"Happened {occurred_at:%Y-%m-%d %H:%M} UTC · version {family_link.LABEL}\n\n"
            + problems.decode(code)
            + f"\n\nSomebody attached their own details to this one. "
              f"/report {incident} in ManagerBot to see them.")
    try:
        await bot.send_message(chat_id=REPORTS_TO, text=text)
        return
    except Exception:
        logging.getLogger(__name__).info("Could not message problem report %s directly", incident, exc_info=True)
    emit_event("warning", "report", text)


_open_dialogs: "OrderedDict[tuple, int]" = OrderedDict()


def _remember_dialog(chat_id, incident: str, message_id: int) -> None:
    _open_dialogs[(chat_id, incident)] = message_id
    _open_dialogs.move_to_end((chat_id, incident))
    while len(_open_dialogs) > 512:
        _open_dialogs.popitem(last=False)


async def _close_open_dialog(bot, chat_id, incident: str) -> None:
    message_id = _open_dialogs.pop((chat_id, incident), None)
    if message_id is None:
        return
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception:
        logging.getLogger(__name__).debug("Could not take down an old report question", exc_info=True)


async def problem_report_callback(update, context) -> None:
    query = update.callback_query
    user = update.effective_user
    lang = await i18n.get_lang(user.id, context)
    parts = (query.data or "").split(":")
    action = parts[0]
    if action == "rptc":
        await query.answer()
        chat = getattr(query.message, "chat", None)
        for key, message_id in list(_open_dialogs.items()):
            if key[0] == (getattr(chat, "id", None) or user.id) \
                    and message_id == getattr(query.message, "message_id", None):
                _open_dialogs.pop(key, None)
        await live_message.edit_in_place(query.message, context.bot, i18n.t(lang, "report_cancelled"))
        return
    code = parts[1] if len(parts) > 1 else ""
    incident = parts[2] if len(parts) > 2 else ""
    if action not in ("rpt", "rpts") or not problems.is_code(code) or not problems.is_incident(incident):
        await query.answer(i18n.t(lang, "report_invalid"), show_alert=True)
        return
    await query.answer()
    chat = getattr(query.message, "chat", None)
    chat_id = getattr(chat, "id", None) or user.id
    if action == "rpt":
        when = getattr(query.message, "date", None)
        if when is None or when.timestamp() <= 0:
            when = datetime.now(timezone.utc)
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton(i18n.t(lang, "report_send"),
                                 callback_data=f"rpts:{code}:{incident}:{int(when.timestamp())}"),
            InlineKeyboardButton(i18n.t(lang, "report_cancel"), callback_data="rptc"),
        ]])
        await _close_open_dialog(context.bot, chat_id, incident)
        sent = await context.bot.send_message(
            chat_id=chat_id, reply_markup=keyboard,
            text=i18n.t(lang, "report_disclaimer", code=code, incident=incident))
        _remember_dialog(chat_id, incident, getattr(sent, "message_id", None))
        return
    stamp = parts[3] if len(parts) > 3 else ""
    occurred_at = (datetime.fromtimestamp(int(stamp), tz=timezone.utc) if stamp.isdigit()
                   else datetime.now(timezone.utc))
    _open_dialogs.pop((chat_id, incident), None)
    try:
        new = await asyncio.to_thread(
            family_link.attach_problem_reporter, code, incident, occurred_at,
            user.id, getattr(user, "username", None), lang,
            getattr(chat, "type", None), problems.level(code))
    except Exception:
        logging.getLogger(__name__).exception("Could not store problem report %s (%s)", incident, code)
        await live_message.edit_in_place(query.message, context.bot, i18n.t(lang, "report_failed"))
        return
    if new:
        await _send_report_to_owner(context.bot, code, incident, occurred_at)
    await live_message.edit_in_place(
        query.message, context.bot,
        i18n.t(lang, "report_sent" if new else "report_already", incident=incident))


async def _tell_about_crash(update, context, incident: str) -> None:
    chat = getattr(update, "effective_chat", None)
    user = getattr(update, "effective_user", None)
    if chat is None or user is None or getattr(chat, "type", "") != "private":
        return
    try:
        lang = await i18n.get_lang(user.id, context)
    except Exception:
        lang = "en"
    try:
        await context.bot.send_message(chat_id=chat.id, text=i18n.t(lang, "crash_notice"),
                                       reply_markup=report_markup(lang, "FM-CRASH", incident))
    except Exception:
        logging.getLogger(__name__).debug("Could not tell anyone about crash %s", incident, exc_info=True)


async def error_handler(update, context) -> None:
    if update is None and is_transient_network_error(context.error):
        note_network_blip(context.error)
        return
    if update is None and isinstance(context.error, Conflict):
        note_conflict(context.error)
        return
    incident = problems.new_incident()
    logging.getLogger(__name__).error(
        "Unhandled exception while processing an update (incident %s)", incident, exc_info=context.error)
    record_error(context.error)
    await _tell_about_crash(update, context, incident)


ACTIVITY_FLUSH_SECONDS = int(os.environ.get("ACTIVITY_FLUSH_SECONDS", "60"))

_activity_buffer: set[int] = set()

ERASED_SKIP_SECONDS = 600
_recently_erased: "OrderedDict[int, float]" = OrderedDict()
_activity_lock = threading.Lock()


def note_erased(user_id: int) -> None:
    with _activity_lock:
        _activity_buffer.discard(user_id)
        _recently_erased[user_id] = time.monotonic()
        _recently_erased.move_to_end(user_id)
        _flood_trim(_recently_erased)


def _erased_recently(user_id: int, now: float) -> bool:
    when = _recently_erased.get(user_id)
    if when is None:
        return False
    if now - when > ERASED_SKIP_SECONDS:
        _recently_erased.pop(user_id, None)
        return False
    return True


_activity_marked: "OrderedDict[int, int]" = OrderedDict()


def _flush_activity_now() -> int:
    global _activity_buffer
    with _activity_lock:
        if not _activity_buffer:
            return 0
        batch, _activity_buffer = _activity_buffer, set()
        now = time.monotonic()
        hour = int(time.time() // 3600)
        batch = {user_id for user_id in batch
                 if not _erased_recently(user_id, now) and _activity_marked.get(user_id) != hour}
        if not batch:
            return 0
        try:
            db.record_activity_batch(batch)
        except Exception:
            _activity_buffer |= batch
            raise
        for user_id in batch:
            _activity_marked[user_id] = hour
            _activity_marked.move_to_end(user_id)
        _flood_trim(_activity_marked)
        return len(batch)


async def _flush_activity_job(context) -> None:
    try:
        await asyncio.to_thread(_flush_activity_now)
    except Exception:
        logging.getLogger(__name__).debug("Activity flush failed; will retry", exc_info=True)


GROUP_CHAT_TYPES = ("group", "supergroup")


def in_group_chat(update) -> bool:
    chat = getattr(update, "effective_chat", None)
    return chat is not None and chat.type in GROUP_CHAT_TYPES


def note_active(context, user) -> None:
    _activity_buffer.add(user.id)
    if len(_usage_users) < 10000 and not is_not_counted(user.id):
        _usage_users.add(user.id)
    context.user_data["_last_seen"] = time.time()
    remember_chat_lang(user.id, context.user_data.get("lang"))


async def track_activity(update, context) -> None:
    note_network_ok()
    _not_counted.set(is_not_counted(getattr(update.effective_user, "id", None)))
    _count_tap(update)
    user = update.effective_user
    if in_group_chat(update):
        if user:
            note_usage_update(None)
        return
    live_message.note_update(update)
    if not user:
        return
    note_usage_update(user.id)
    note_active(context, user)

    have = context.user_data.get(MENU_SIGNATURE_KEY)
    if have:
        try:
            lang = context.user_data.get("lang")
            commands = _menu_for(user.id, lang)
            if commands:
                expected = _menu_signature(commands)
                if have != expected:
                    context.user_data[MENU_SIGNATURE_KEY] = expected
                    context.application.create_task(refresh_chat_menu(context, user.id, lang))
        except Exception:
            logging.getLogger(__name__).debug("Could not check the chat menu", exc_info=True)


async def refuse_new_work(lang: str, user_id: int, chat_id: int) -> str | None:
    if not lifecycle.is_paused():
        return None
    if not lifecycle.in_maintenance():
        return i18n.t(lang, "restarting_send_again")

    await asyncio.to_thread(lifecycle.hold_for_update, user_id, chat_id)
    minutes = lifecycle.maintenance_minutes_left()
    if minutes is None:
        return i18n.t(lang, "update_soon_try_later_soon")
    return i18n.t(lang, "update_soon_try_later", minutes=minutes)


USER_DATA_TTL_HOURS = int(os.environ.get("USER_DATA_TTL_HOURS", "12"))


def _prune_user_data(application) -> int:
    now = time.time()
    cutoff = now - USER_DATA_TTL_HOURS * 3600
    stale = []
    for user_id, data in application.user_data.items():
        seen = data.get("_last_seen")
        if seen is None:
            data["_last_seen"] = now
        elif seen < cutoff:
            stale.append(user_id)
    for user_id in stale:
        application.drop_user_data(user_id)
    return len(stale)


async def _maintenance_job(context) -> None:
    dropped = _prune_user_data(context.application)
    if dropped:
        logging.getLogger(__name__).info("Dropped cached state for %d idle user(s).", dropped)


def attach_maintenance(app) -> None:
    if app.job_queue is None:
        logging.getLogger(__name__).warning(
            "No job queue -- activity counts and memory pruning are off. "
            'Install it with: pip install "python-telegram-bot[job-queue]"'
        )
        return
    app.job_queue.run_repeating(
        _flush_activity_job, interval=ACTIVITY_FLUSH_SECONDS, first=ACTIVITY_FLUSH_SECONDS
    )
    app.job_queue.run_repeating(
        _flush_problems_job, interval=PROBLEM_FLUSH_SECONDS, first=PROBLEM_FLUSH_SECONDS
    )
    app.job_queue.run_repeating(_maintenance_job, interval=3600, first=3600)
    app.job_queue.run_repeating(
        _usage_sample_job, interval=USAGE_SAMPLE_MINUTES * 60,
        first=USAGE_SAMPLE_MINUTES * 60 + 60,
    )


async def flush_on_shutdown(application) -> None:
    try:
        await asyncio.to_thread(_flush_activity_now)
    except Exception:
        logging.getLogger(__name__).debug("Final activity flush failed", exc_info=True)
    try:
        await asyncio.to_thread(_flush_problems_now)
    except Exception:
        logging.getLogger(__name__).debug("Final problem flush failed", exc_info=True)
    await asyncio.to_thread(_flush_stats_now)
    close = getattr(db, "close_pool", None)
    if close is not None:
        try:
            await asyncio.to_thread(close)
        except Exception:
            logging.getLogger(__name__).debug("Closing the connection pool failed", exc_info=True)


def detect_host_environment() -> str:
    railway_env = os.environ.get("RAILWAY_ENVIRONMENT_NAME") or os.environ.get("RAILWAY_ENVIRONMENT")
    if railway_env:
        project = os.environ.get("RAILWAY_PROJECT_NAME", "?")
        service = os.environ.get("RAILWAY_SERVICE_NAME", "?")
        return f"☁️ Cloud (Railway -- project \"{project}\", service \"{service}\", env \"{railway_env}\")"
    return f"💻 Local ({socket.gethostname()})"


def _read_first_int(path: str, key: str | None = None) -> int | None:
    try:
        with open(path) as handle:
            if key is None:
                text = handle.read().strip()
                return int(text) if text.isdigit() else None
            for line in handle:
                if line.startswith(key):
                    return int(line.split()[1])
    except (OSError, ValueError, IndexError):
        return None
    return None


_service_rss_seen = 0


def footprint_numbers() -> dict:
    global _service_rss_seen
    resident = _read_first_int("/proc/self/status", "VmRSS:")
    peak = _read_first_int("/proc/self/status", "VmHWM:")
    ceiling = resources.memory_limit_mb()
    cpu = None
    try:
        import resource

        usage = resource.getrusage(resource.RUSAGE_SELF)
        cpu = usage.ru_utime + usage.ru_stime
        if resident is None:
            scale = 1 if sys.platform == "darwin" else 1024
            peak = usage.ru_maxrss * scale // 1024
    except Exception:
        pass
    numbers = {
        "rss_mb": None if resident is None else resident // 1024,
        "peak_rss_mb": None if peak is None else peak // 1024,
        "ceiling_mb": ceiling,
        "cpu_seconds": None if cpu is None else int(cpu),
        "scope": "process",
    }
    used = resources.memory_used_mb()
    if used is not None:
        _service_rss_seen = max(_service_rss_seen, used)
        service_peak = resources.memory_peak_mb()
        numbers["rss_mb"] = used
        numbers["peak_rss_mb"] = _service_rss_seen if service_peak is None else service_peak
        numbers["scope"] = "service"
        service_cpu = resources.cpu_used_seconds()
        if service_cpu is not None:
            numbers["cpu_seconds"] = int(service_cpu)
    return numbers


def process_footprint() -> str:
    numbers = footprint_numbers()
    parts = []
    if numbers["rss_mb"] is not None:
        if numbers.get("scope") == "service":
            line = f"Memory (this service, its workers included): {numbers['rss_mb']} MB in use"
        else:
            line = f"Memory: {numbers['rss_mb']} MB resident"
        if numbers["peak_rss_mb"]:
            line += f" (peak {numbers['peak_rss_mb']} MB)"
        if numbers["ceiling_mb"]:
            line += f" of {numbers['ceiling_mb']} MB allowed"
        cache = resources.memory_cache_mb() if numbers.get("scope") == "service" else None
        if cache:
            line += f"; disk cache {cache} MB more, which the kernel takes back when it needs to"
        parts.append(line)
    elif numbers["peak_rss_mb"] is not None:
        parts.append(f"Memory: peak {numbers['peak_rss_mb']} MB")
    if numbers["cpu_seconds"] is not None:
        parts.append(f"CPU: {numbers['cpu_seconds']}s used since start")
    trend = usage_trend_line()
    if trend:
        parts.append(trend)
    return (" · ".join(parts) or "Footprint: not readable on this host") + "\n" + resources.describe(
        resources.own_folder(family_link._bot_id))


USAGE_SAMPLE_MINUTES = int(os.environ.get("USAGE_SAMPLE_MINUTES") or 15)
USAGE_MEMORY_WARN_RATIO = float(os.environ.get("USAGE_MEMORY_WARN_RATIO") or 0.85)
USAGE_CPU_WARN_RATIO = float(os.environ.get("USAGE_CPU_WARN_RATIO") or 0.85)
USAGE_SPIKE_FACTOR = float(os.environ.get("USAGE_SPIKE_FACTOR") or 6.0)
USAGE_SPIKE_FLOOR = int(os.environ.get("USAGE_SPIKE_FLOOR") or 60)
USAGE_MONTHLY_USERS_WARN = int(os.environ.get("USAGE_MONTHLY_USERS_WARN") or 400)
SLEEP_AFTER_SECONDS = int(os.environ.get("SLEEP_AFTER_SECONDS") or 300)
USAGE_FAILURE_WARN_RATIO = float(os.environ.get("USAGE_FAILURE_WARN_RATIO") or 0.5)
USAGE_FAILURE_FLOOR = int(os.environ.get("USAGE_FAILURE_FLOOR") or 4)
_USAGE_WINDOW_MEMORY = 24
_ALARM_QUIET_SECONDS = {"memory": 3600, "spike": 3600, "monthly_users": 86400,
                        "failures": 1800, "cpu": 3600}

_usage_updates = 0
_usage_users: set[int] = set()
_usage_recent: "deque[int]" = deque(maxlen=_USAGE_WINDOW_MEMORY)
_usage_alarmed: dict[str, float] = {}
_usage_last_update = time.monotonic()
_usage_window_start = _usage_last_update
_usage_sleepable = 0.0
_usage_max_gap = 0.0
_usage_jobs_ok = 0
_usage_jobs_failed = 0


def _asleep_in_window(now: float) -> float:
    return max(0.0, now - max(_usage_last_update + SLEEP_AFTER_SECONDS, _usage_window_start))


def note_usage_update(user_id: int | None) -> None:
    global _usage_updates, _usage_last_update, _usage_sleepable, _usage_max_gap
    counted = not is_not_counted(user_id)
    if counted:
        _usage_updates += 1
    now = time.monotonic()
    gap = now - _usage_last_update
    _usage_sleepable += _asleep_in_window(now)
    _usage_last_update = now
    _usage_max_gap = max(_usage_max_gap, gap)
    if counted and user_id is not None and len(_usage_users) < 10000:
        _usage_users.add(user_id)


def note_job(ok: bool) -> None:
    global _usage_jobs_ok, _usage_jobs_failed
    if ok:
        _usage_jobs_ok += 1
    else:
        _usage_jobs_failed += 1


TAP_MAX_KEYS = 150
_TAP_FIRST = re.compile(r"[a-z][a-z0-9_]{0,23}")
_TAP_SECOND = {
    "help": re.compile(r"full|brief"),
    "helpdet": re.compile(r"convert|recharge|balance|download|settings|newpack|addsticker|import|link|export|which"),
    "setlang": re.compile(r"[a-z]{2,3}"),
    "erasedata": re.compile(r"yes|no"),
    "donatefiat": re.compile(r"[A-Za-z]{3}"),
    "donatecustom": re.compile(r"[A-Za-z]{3}"),
    "caption": re.compile(r"on|off"),
    "lossless": re.compile(r"on|off"),
    "large": re.compile(r"[a-z]{1,10}"),
    "merge": re.compile(r"pdf|zip"),
    "convfmt": re.compile(r"[a-z0-9]{1,10}"),
    "fmtcat": re.compile(r"[a-z_]{1,16}"),
    "aq_export": re.compile(r"pick|copy|doc|cancel"),
    "anonlink": re.compile(r"toggle|newlink"),
    "confirmremove": re.compile(r"yes|no"),
}
_MANAGER_BOT_ID = "managerbot"
_taps: "dict[str, int]" = {}


def tap_key(data) -> str:
    if not isinstance(data, str):
        return "other"
    parts = data.split(":", 2)
    first = parts[0]
    if _TAP_FIRST.fullmatch(first) is None:
        return "other"
    if len(parts) < 2:
        return first
    second = parts[1]
    if first == "donate":
        return f"donate:{second}" if second in {str(amount) for amount in DONATE_STAR_OPTIONS} else first
    pattern = _TAP_SECOND.get(first)
    if pattern is not None and pattern.fullmatch(second) is not None:
        return f"{first}:{second}"
    return first


_not_counted: "contextvars.ContextVar[bool]" = contextvars.ContextVar("mumu_not_counted", default=False)
_not_counted_ids: "frozenset[int] | None" = None


def not_counted_ids() -> "frozenset[int]":
    global _not_counted_ids
    if _not_counted_ids is None:
        ids = set()
        for name, value in os.environ.items():
            if name in ("ADMIN_ID", "STATS_IGNORE_IDS") or name.endswith("_ADMIN_ID"):
                for word in re.split(r"[\s,]+", value or ""):
                    if word.lstrip("-").isdigit():
                        ids.add(int(word))
        _not_counted_ids = frozenset(ids)
    return _not_counted_ids


def is_not_counted(user_id) -> bool:
    try:
        return user_id is not None and int(user_id) in not_counted_ids()
    except (TypeError, ValueError):
        return False


def note_tap(key: str) -> None:
    if _not_counted.get():
        return
    try:
        if key in _taps:
            _taps[key] += 1
        elif key != "other" and len(_taps) < TAP_MAX_KEYS - (0 if "other" in _taps else 1):
            _taps[key] = 1
        else:
            _taps["other"] = _taps.get("other", 0) + 1
    except Exception:
        pass


STAT_MAX_KEYS = 300
_stats: "dict[tuple, int]" = {}


def note_stat(metric: str, key: str, n: int = 1) -> None:
    if _not_counted.get():
        return
    try:
        item = (str(metric), str(key or "other"))
        if item in _stats:
            _stats[item] += n
        elif len(_stats) < STAT_MAX_KEYS:
            _stats[item] = n
        else:
            _stats[(item[0], "other")] = _stats.get((item[0], "other"), 0) + n
    except Exception:
        pass


def take_stats() -> "dict[tuple, int]":
    global _stats
    stats, _stats = _stats, {}
    return stats


def _flush_stats_now(stats: "dict | None" = None) -> None:
    stats = take_stats() if stats is None else stats
    try:
        family_link.record_stats(stats)
    except Exception as exc:
        logging.getLogger(__name__).debug("Stats not written (%s)", type(exc).__name__)


def take_taps() -> "dict[str, int]":
    global _taps
    taps, _taps = _taps, {}
    return taps


def _count_tap(update) -> None:
    try:
        query = update.callback_query
        data = query.data if query is not None else None
        if isinstance(data, str) and family_link._bot_id != _MANAGER_BOT_ID:  # noqa: SLF001
            note_tap(tap_key(data))
    except Exception:
        pass


def _alarm_due(kind: str) -> bool:
    now = time.time()
    if now - _usage_alarmed.get(kind, 0) < _ALARM_QUIET_SECONDS.get(kind, 3600):
        return False
    _usage_alarmed[kind] = now
    return True


_usage_cpu_last: "tuple[float, float] | None" = None


def _cpu_window(numbers: dict) -> "tuple[float, float] | None":
    global _usage_cpu_last
    cpu = numbers.get("cpu_seconds")
    now = time.monotonic()
    last, _usage_cpu_last = _usage_cpu_last, (None if cpu is None else (cpu, now))
    if cpu is None or last is None or cpu < last[0] or now <= last[1]:
        return None
    return cpu - last[0], now - last[1]


def _median(values) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[middle])
    return (ordered[middle - 1] + ordered[middle]) / 2


def usage_trend_line() -> str:
    if not _usage_recent:
        return ""
    quiet = int(time.monotonic() - _usage_last_update)
    return (f"Recent: {sum(_usage_recent)} update(s) over the last "
            f"{len(_usage_recent)} window(s) of {USAGE_SAMPLE_MINUTES} min · "
            f"quiet for {quiet // 60}m {quiet % 60}s")


def _monthly_users() -> int | None:
    counter = getattr(db, "count_active_users_since", None)
    if counter is None:
        return None
    try:
        return counter(datetime.now(timezone.utc) - timedelta(days=30))
    except Exception:
        return None


def _check_usage_alarms(numbers: dict, updates: int, users: int,
                        jobs_ok: int = 0, jobs_failed: int = 0) -> None:
    jobs = jobs_ok + jobs_failed
    if (jobs >= USAGE_FAILURE_FLOOR
            and jobs_failed / jobs >= USAGE_FAILURE_WARN_RATIO
            and _alarm_due("failures")):
        family_link.report_event(
            "warning" if jobs_ok else "error", "job_failures",
            f"{jobs_failed} of {jobs} job(s) failed in the last "
            f"{USAGE_SAMPLE_MINUTES} min"
            + ("." if jobs_ok else " -- none succeeded."),
            "A job is whatever this bot is for: a conversion, a download, a pack "
            "edit. All of them failing usually means something outside the bot "
            "stopped answering rather than something inside it breaking.",
        )
    ceiling = numbers.get("ceiling_mb")
    peak = numbers.get("peak_rss_mb")
    if ceiling and peak and peak >= ceiling * USAGE_MEMORY_WARN_RATIO and _alarm_due("memory"):
        family_link.report_event(
            "warning", "memory_headroom",
            f"Memory peaked at {peak} MB of {ceiling} MB allowed "
            f"({peak / ceiling:.0%} of the limit).",
            "Crossing the limit is an out-of-memory kill rather than a slow reply. "
            "Either something is holding more than it should, or this service has "
            "outgrown its plan.",
        )
    window = numbers.get("cpu_window")
    if (window and numbers.get("rss_mb") is not None
            and (numbers.get("scope") == "service" or resources.holds_whole_service())):
        used, seconds = window
        limit = resources.cpu_limit()
        if seconds > 0 and used / seconds >= USAGE_CPU_WARN_RATIO * limit and _alarm_due("cpu"):
            family_link.report_event(
                "warning", "cpu_busy",
                f"CPU: this service used {used / seconds:.1f} of its {limit:g} vCPU over the "
                f"last {round(seconds / 60)} minutes.",
                "Busy for this long means conversions or downloads are queueing for the "
                "processor, and everybody's work slows down together. Either something is "
                "spinning, or this service has outgrown its plan.",
            )

    baseline = _median(_usage_recent)
    if (updates >= USAGE_SPIKE_FLOOR and baseline > 0
            and updates >= baseline * USAGE_SPIKE_FACTOR and _alarm_due("spike")):
        family_link.report_event(
            "warning", "activity_spike",
            f"{updates} updates from {users} person(s) in {USAGE_SAMPLE_MINUTES} min, "
            f"against a recent median of {baseline:.0f}.",
            "Could be a launch and could be one script. /status and the usage table "
            "have the shape of it.",
        )

    monthly = _monthly_users()
    if monthly is not None and monthly >= USAGE_MONTHLY_USERS_WARN and _alarm_due("monthly_users"):
        family_link.report_event(
            "warning", "monthly_users",
            f"{monthly} distinct people used this bot in the last 30 days, "
            f"past the {USAGE_MONTHLY_USERS_WARN} mark.",
            "Nothing is refused and nothing is broken. It is the number that decides "
            "whether the plan this runs on is still the right one.",
        )


def _sample_usage_now(taps: "dict[str, int] | None" = None) -> None:
    global _usage_updates, _usage_users
    updates, users = _usage_updates, len(_usage_users)
    _usage_updates, _usage_users = 0, set()
    numbers = footprint_numbers()
    global _usage_sleepable, _usage_max_gap, _usage_jobs_ok, _usage_jobs_failed
    global _usage_window_start
    now = time.monotonic()
    trailing = now - _usage_last_update
    sleepable = int(_usage_sleepable + _asleep_in_window(now))
    max_gap = int(max(_usage_max_gap, trailing))
    jobs_ok, jobs_failed = _usage_jobs_ok, _usage_jobs_failed
    _usage_sleepable, _usage_max_gap = 0.0, 0.0
    _usage_jobs_ok, _usage_jobs_failed = 0, 0
    _usage_window_start = now
    numbers["cpu_window"] = _cpu_window(numbers)
    try:
        family_link.record_usage(
            USAGE_SAMPLE_MINUTES, numbers["rss_mb"], numbers["peak_rss_mb"],
            numbers["ceiling_mb"], numbers["cpu_seconds"], updates, users,
            sleepable, max_gap, jobs_ok, jobs_failed,
        )
    except Exception:
        logging.getLogger(__name__).debug("Usage sample not written", exc_info=True)
    if taps is None:
        taps = take_taps()
    try:
        family_link.record_taps(taps)
    except Exception as exc:
        logging.getLogger(__name__).debug("Button taps not written (%s)", type(exc).__name__)
    _flush_stats_now()
    try:
        _check_usage_alarms(numbers, updates, users, jobs_ok, jobs_failed)
    except Exception:
        logging.getLogger(__name__).debug("Usage alarm check failed", exc_info=True)
    _usage_recent.append(updates)
    resources.reset_memory_peak()


async def _usage_sample_job(context) -> None:
    taps = take_taps()
    await asyncio.to_thread(_sample_usage_now, taps)


def build_status_text(start_time: datetime, users_last_hour: int, users_since_start: int) -> str:
    now = datetime.now(timezone.utc)
    uptime = now - start_time
    days, rem = divmod(int(uptime.total_seconds()), 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)
    uptime_str = f"{days}d {hours}h {minutes}m" if days else f"{hours}h {minutes}m"
    return "\n".join([
        "📊 Status",
        f"Version: {family_link.LABEL} (commands as of {family_link.VERSION})",
        f"Started: {start_time.strftime('%Y-%m-%d %H:%M:%S UTC')} ({uptime_str} ago)",
        f"Hosted: {detect_host_environment()}",
        f"Active users (last hour): {users_last_hour}",
        f"Active users (since this start): {users_since_start}",
        process_footprint(),
        "",
        error_summary(),
    ])


PER_USER_WAITING_MAX = int(os.environ.get("PER_USER_WAITING_MAX") or 24)

UPDATES_IN_MEMORY_MAX = int(os.environ.get("UPDATES_IN_MEMORY_MAX") or 256)


def waiting_updates(application, update) -> list:
    processor = getattr(application, "update_processor", None)
    if not isinstance(processor, PerUserUpdateProcessor):
        return []
    key = PerUserUpdateProcessor.key_of(update)
    return processor.waiting(key) if key is not None else []


class UpdateQueue(asyncio.Queue):
    def __init__(self, processor: "PerUserUpdateProcessor", limit: int):
        super().__init__()
        self.processor, self.limit = processor, limit
        self.held_back = 0

    def full_now(self) -> bool:
        return self.qsize() + self.processor.in_flight >= self.limit

    async def put(self, item) -> None:
        if self.limit > 0 and isinstance(item, Update) and self.full_now():
            self.held_back += 1
            while self.full_now():
                self.processor.done.clear()
                if not self.full_now():
                    break
                try:
                    await asyncio.wait_for(self.processor.done.wait(), timeout=1.0)
                except asyncio.TimeoutError:
                    pass
        await super().put(item)


def per_user_updates(builder, prefix: str):
    if (os.environ.get(f"{prefix}_CONCURRENT_UPDATES") or "").strip().lower() in ("0", "off", "false", "no"):
        return builder
    processor = PerUserUpdateProcessor()
    builder = builder.concurrent_updates(processor)
    if UPDATES_IN_MEMORY_MAX > 0:
        builder = builder.update_queue(UpdateQueue(processor, UPDATES_IN_MEMORY_MAX))
    return builder


class PerUserUpdateProcessor(BaseUpdateProcessor):
    def __init__(self, max_concurrent_updates: int = 4096, waiting_max: int = PER_USER_WAITING_MAX):
        super().__init__(max_concurrent_updates)
        self._locks: "dict[tuple, list]" = {}
        self._waiting: "dict[tuple, list]" = {}
        self.waiting_max = waiting_max
        self.dropped = 0
        self.in_flight = 0
        self.done = asyncio.Event()

    @staticmethod
    def key_of(update) -> "tuple | None":
        try:
            if getattr(update, "pre_checkout_query", None) is not None:
                return None
            user = getattr(update, "effective_user", None)
            if user is not None and getattr(user, "id", None) is not None:
                return ("user", user.id)
            chat = getattr(update, "effective_chat", None)
            if chat is not None and getattr(chat, "id", None) is not None:
                return ("chat", chat.id)
        except Exception:
            pass
        return None

    async def do_process_update(self, update, coroutine) -> None:
        self.in_flight += 1
        try:
            await self._process_in_turn(update, coroutine)
        finally:
            self.in_flight -= 1
            self.done.set()

    async def _process_in_turn(self, update, coroutine) -> None:
        key = self.key_of(update)
        if key is None:
            await coroutine
            return
        entry = self._locks.get(key)
        if entry is None:
            entry = self._locks[key] = [asyncio.Lock(), 0]
        if self.waiting_max and entry[1] > self.waiting_max and not self.must_keep(update):
            self.dropped += 1
            if self.dropped == 1 or self.dropped % 100 == 0:
                logging.getLogger(__name__).warning(
                    "One person has more than %d updates waiting; extras are dropped (%d so far).",
                    self.waiting_max, self.dropped)
            close = getattr(coroutine, "close", None)
            if close is not None:
                close()
            return
        entry[1] += 1
        self._waiting.setdefault(key, []).append(update)
        started = False
        try:
            async with entry[0]:
                started = True
                self._unqueue(key, update)
                await coroutine
        finally:
            if not started:
                self._unqueue(key, update)
            entry[1] -= 1
            if entry[1] == 0 and self._locks.get(key) is entry:
                del self._locks[key]
            if not started:
                close = getattr(coroutine, "close", None)
                if close is not None:
                    close()

    def waiting(self, key) -> list:
        return list(self._waiting.get(key, ()))

    def _unqueue(self, key, update) -> None:
        queued = self._waiting.get(key)
        if queued is None:
            return
        for n, item in enumerate(queued):
            if item is update:
                del queued[n]
                break
        if not queued:
            del self._waiting[key]

    @staticmethod
    def must_keep(update) -> bool:
        message = getattr(update, "message", None)
        return getattr(message, "successful_payment", None) is not None

    async def initialize(self) -> None:
        pass

    async def shutdown(self) -> None:
        pass
