"""
fstik-style sticker pack manager bot.

Part of a small bot family (see ARCHITECTURE.md): generic file conversion
(/convert, /mystars, /stars) lives in @ConvertBot, and Instagram/TikTok
downloading lives in @DownloaderBot. Each bot is its own process, its own
folder and its own deployment, with no shared files or file handoffs between
them -- so a clip that's too complex to compress into a video sticker just
dead-ends here with a pointer to @ConvertBot; the user re-sends it there.
The five do share one Postgres database, with a schema each; family_link.py
is the only code that touches anything outside this bot's own schema.

Commands:
  /start        - greeting + menu (also handles co-edit share links)
  /newpack      - create a new sticker set
  /addsticker   - add to an existing pack
  /mypacks      - list your packs; tap one for Add/Rename/Co-edit
  /import       - (while editing a pack) bulk-import from another Telegram
                  pack or a WhatsApp sticker pack .zip/.wastickers file
  /done         - optional: close the pack being edited and get its link
                  (every sticker is saved as it is added, so nothing asks for it)
  /cancel       - stop whatever the bot is waiting on you for
  /whomade      - look up who created a pack (packs made through this bot)
  /donate       - support hosting costs (voluntary)
  /en, /uz, /rus - switch language (English/Uzbek/Russian); also asked
                    once, trilingually, on first /start

Once you're "editing" a pack (after /newpack, tapping Add on a pack, or
opening someone else's co-edit link), just send images, GIFs, videos, or
static/video stickers one after another -- each is added immediately with a
default 😭 emoji, and answered with a short reply of its own. Send emoji right
after a sticker to retag it, or reply with emoji to any sticker (or to the
bot's reply about it) to retag that one. Other commands work in the middle of
a session without ending it. In group chats see group_mode.py.
GIFs/videos are auto-converted (via ffmpeg) into Telegram's video-sticker
format; Lottie/.tgs animated stickers still aren't supported anywhere in
this bot family (needs a separate rendering pipeline neither bot has).

Requires: python-telegram-bot[job-queue]>=21.3, Pillow>=10.0, ffmpeg on PATH
Env vars: SBOT_TOKEN, SBOT_USERNAME (no @), DATABASE_URL and DB_SCHEMA
          (the shared family database, and this bot's schema in it),
          SIBLING_BOTS (see shared_features.py). Every performance/cost knob
          has a working default and is documented in .env.example.
"""
import asyncio
import logging
import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from io import BytesIO

# main.py gives each bot its settings; this only reads a .env if one is present.
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
)
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
from video_sticker import ConversionError
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
# Without the @, which is also taken off if it was written with one -- the
# one-process edition always allowed it, and a link built from "@name" is broken.
BOT_USERNAME = (os.environ.get("SBOT_USERNAME") or "").strip().lstrip("@") or None

# Owner-only admin tools (/whois, /messageas, /dbdump, /status) -- comma-separated
# Telegram user ids, e.g. "111,222" for more than one of your own accounts.
# Empty/unset means those commands are disabled for everyone.
ADMIN_IDS = {int(x) for x in os.environ.get("SBOT_ADMIN_ID", "").split(",") if x.strip()}

# Optional: point at a self-hosted Bot API server (https://github.com/tdlib/telegram-bot-api)
# instead of Telegram's cloud one. This is what actually breaks the 20 MB download / 50 MB
# upload ceiling -- a local server raises those to (practically) unlimited download and
# ~1.9 GB upload. See https://github.com/tdlib/telegram-bot-api for setup.
LOCAL_BOT_API_URL = os.environ.get("LOCAL_BOT_API_URL")  # e.g. "http://localhost:8081"
LOCAL_BOT_API_MODE = os.environ.get("LOCAL_BOT_API_MODE", "true").lower() == "true"

# This bot's id within the family (matches a key in SIBLING_BOTS, see shared_features.py)
BOT_NAME = "stickerbot"

# Telegram's long-poll window. Higher means fewer requests for identical
# latency; 30s is comfortably inside every proxy's idle timeout.
POLL_TIMEOUT = int(os.environ.get("POLL_TIMEOUT", "30"))

TITLE, EDITING, RENAME, INVITE = range(4)

# The most characters Telegram takes in a pack's title; a longer one is cut.
PACK_TITLE_MAX = 64


def build_help_text(lang: str, full: bool = False) -> str:
    """The short version unless asked for the long one -- the family's help
    template (shared_features, "/help in two lengths"). Both end with the
    sibling-bots pointer; the per-command pages (HELP_PAGES below) are the
    buttons under the help, not a line in it."""
    if not full:
        text = i18n.t(lang, "help_brief").rstrip("\n")
    else:
        text = i18n.t(lang, "help_text").rstrip("\n")
    blurb = sibling_bots_blurb(BOT_NAME, lang)
    return text + "\n\n" + blurb if blurb else text


# ---------- /help <name> and the buttons under /help: a page for each command ----------
# The mechanism is in shared_features ("A page for each command"). Every
# number on a page is read from the module that owns it when the page is shown
# -- by attribute, never copied -- so a page says what the code does now.

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
# There is no /groups command, so its button says what it is rather than "/groups".
HELP_LABELS = {"groups": "help_btn_groups"}


def build_start_text(lang: str) -> str:
    return i18n.t(lang, "start_intro") + build_help_text(lang)


# Public command menu (the "/" button in Telegram's chat UI) -- set on
# startup via set_my_commands() below instead of pasting into @BotFather by
# hand. Owner-only admin commands (/whois, /messageas, /dbdump) are
# deliberately left off -- no reason to advertise them to every user.
# Language-switch commands are described in the language they switch to
# (self-explanatory by script/language, since Telegram's command menu itself
# isn't per-user).
BOT_COMMANDS = [
    # Order is deliberate: what this is, how to change language, what the bot
    # does, how to stop it, help, money, then the policies. /language sits
    # second on the owner's instruction -- somebody reading a menu in the
    # wrong language needs the way out before anything else.
    #
    # /en, /uz and /rus are NOT here and still work. They are the way back for
    # somebody who cannot read the menu at all, so they must never stop
    # working -- but three lines for what /language already does is three
    # lines of noise for everybody who can read it.
    BotCommand("start", "What I do, and how to start"),
    BotCommand("language", "Choose your language / Tilni tanlash / Выбрать язык"),
    BotCommand("newpack", "Make a new sticker pack"),
    BotCommand("addsticker", "Add stickers to one of your packs"),
    BotCommand("mypacks", "Your packs"),
    BotCommand("import", "Copy stickers in from another pack"),
    BotCommand("done", "Close the pack you are filling (optional)"),
    BotCommand("emoji", "Reply to a sticker: change its emoji"),
    BotCommand("whomade", "Who made a pack — send me one of its stickers"),
    BotCommand("cancel", "Stop whatever I am waiting for"),
    BotCommand("help", "Everything I can do"),
    BotCommand("feedback", "Suggest something, or report a problem"),
    BotCommand("balance", "Your ⚡ credit"),
    BotCommand("donate", "Chip in for hosting costs"),
    BotCommand("paysupport", "Trouble with a payment"),
    BotCommand("privacy", "What I keep about you"),
    BotCommand("terms", "What I may be used for"),
    BotCommand("deletemydata", "Delete everything I hold on you"),
]

# The same menu, in the owner's own chat, with the commands only they can
# run. Kept out of BOT_COMMANDS on purpose -- a stranger should not be offered
# /dbdump -- but hidden from the owner too, which was the accident. See
# publish_commands() in shared_features.py. English, like the rest of the
# admin output: the only person who sees this list wrote the bot.
ADMIN_COMMANDS = [
    BotCommand("status", "🔒 Uptime, host, errors, active users"),
    BotCommand("whois", "🔒 whois <user_id> — a user, and their packs"),
    BotCommand("messageas", "🔒 messageas <user_id> <text> — DM as this bot"),
    BotCommand("dbdump", "🔒 This bot's tables as a zip of CSVs"),
    BotCommand("crashtest", "🔒 Raise on purpose, to check the alert arrives"),
]

# ---------- small helpers ----------


async def reply(update: Update, text: str, **kwargs) -> LiveMessage:
    """Works whether the update came from a command or a button tap. A
    button tap evolves the tapped message in place instead of sending a new
    one -- so menu navigation (start -> my packs -> a pack -> ...) reuses a
    single message, fstik/BotFather-style, rather than piling up a fresh one
    per tap. A command has no prior bot message to reuse, so it always sends
    fresh."""
    if update.message:
        return await LiveMessage.reply_to(update.message, text, **kwargs)
    return await edit_in_place(update.callback_query.message, update.get_bot(), text, **kwargs)


def slugify(text: str) -> str:
    """Telegram sticker-set names must start with a *letter* (not a digit or
    underscore) -- a title like "2007" would otherwise slugify to "2007",
    which Telegram rejects outright with 'Invalid sticker set name is
    specified'. Prefixing with a letter when needed avoids that.

    Stripping stray underscores happens both before *and* after truncating
    to 30 chars -- truncating mid-run of underscores (e.g. a long title
    with a space/symbol landing right at the cutoff) can otherwise leave a
    trailing "_" that collides with the "_" separator appended after this
    in bot.py, producing "__" in the final name, which Telegram also rejects.
    """
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", text).strip("_").lower()
    slug = slug[:30].strip("_") or "pack"
    if not slug[0].isalpha():
        slug = ("p_" + slug)[:30].strip("_")
    return slug


def help_rows(lang: str) -> list:
    """New pack and My packs: under /help, and under /start with the
    sibling bots after them. The Help button that used to be here is the
    family's full-guide button now (shared_features.help_keyboard)."""
    return [[
        InlineKeyboardButton(i18n.t(lang, "btn_new_pack"), callback_data="menu_newpack"),
        InlineKeyboardButton(i18n.t(lang, "btn_my_packs"), callback_data="menu_mypacks"),
    ]]


def start_menu_keyboard(lang: str) -> InlineKeyboardMarkup:
    return help_keyboard(lang, False, help_rows(lang) + [sibling_bots_keyboard_row(BOT_NAME)], HELP_PAGES,
                         HELP_LABELS)


def packs_keyboard(packs: list[tuple[str, str]], lang: str, shared=()) -> InlineKeyboardMarkup:
    """One button per pack (tap opens its menu), fStik-style. Packs somebody shared with
    you come after yours, marked 👥, and a tap opens them for adding straight away: their
    menu (rename, sharing, deleting) is their owner's."""
    rows = [[InlineKeyboardButton(title, callback_data=f"packopen:{name}")] for name, title in packs]
    rows += [[InlineKeyboardButton(f"👥 {title}", callback_data=f"pack:{name}")] for name, title in shared]
    rows.append([InlineKeyboardButton(i18n.t(lang, "btn_new_pack"), callback_data="menu_newpack")])
    rows.append([InlineKeyboardButton(i18n.t(lang, "btn_back"), callback_data="menu_start")])
    return InlineKeyboardMarkup(rows)


def pack_detail_keyboard(pack_name: str, lang: str, public: bool = False) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(i18n.t(lang, "btn_open_pack"), url=f"https://t.me/addstickers/{pack_name}")],
            [InlineKeyboardButton(i18n.t(lang, "btn_add_stickers"), callback_data=f"pack:{pack_name}")],
            [
                InlineKeyboardButton(i18n.t(lang, "btn_rename"), callback_data=f"packrename:{pack_name}"),
                InlineKeyboardButton(i18n.t(lang, "btn_coedit"), callback_data=f"packcoedit:{pack_name}"),
            ],
            # Private (the default) or public in groups -- see group_mode.py.
            [InlineKeyboardButton(i18n.t(lang, "btn_pack_public" if public else "btn_pack_private"),
                                  callback_data=f"packpub:{pack_name}")],
            [InlineKeyboardButton(i18n.t(lang, "btn_delete_pack"), callback_data=f"packdel:{pack_name}")],
            [InlineKeyboardButton(i18n.t(lang, "btn_back"), callback_data="menu_mypacks")],
        ]
    )


def _status_text(context: ContextTypes.DEFAULT_TYPE, lang: str, note: str = "") -> str:
    title = context.user_data.get("title") or i18n.t(lang, "status_default_title")
    created = context.user_data.get("pack_created")
    verb = i18n.t(lang, "status_verb_creating" if not created else "status_verb_editing")
    intro = context.user_data.get("status_intro", "")
    # No running count since 2.2.0: each sticker is answered on its own (sticker_ops), so
    # this message is only refreshed for imports and stays where it was otherwise.
    text = i18n.t(lang, "status_line_open", verb=verb, title=title)
    if intro:
        text = f"{intro}\n\n{text}"
    if note:
        text += f"\n{note}"
    return text


# The one message that opens an editing session and carries the long notes
# (an import's progress and summary). Until 2.2.0 it was the only thing the
# bot said while a pack was open, every "added ✅" folded into it; the owner
# found that several pictures sent quickly read as if only one had been added,
# so each sticker now gets a short reply of its own (_add_and_confirm), which
# is also what a later emoji reply points at.
#
# When the user does speak over it -- another photo, a stray "ok" -- the
# handle notices and moves the whole status down to the bottom as a new
# message instead of rewriting one they have scrolled past. See
# live_message.py.
STATUS_KEY = "status"


def _has_status(context: ContextTypes.DEFAULT_TYPE) -> bool:
    return bool(context.user_data.get(STATUS_KEY))


async def _start_status(
    context: ContextTypes.DEFAULT_TYPE, chat_id: int, title: str, lang: str,
    intro: str = "", adopt: dict | None = None,
):
    """Sets up the session's status message.

    When `adopt` is a saved handle (typically the prompt that led here), that
    message becomes the status display instead of a new one being sent -- so
    "What should the pack title be?" turns into "📝 Creating "X" -- 0
    sticker(s)..." in place.
    """
    context.user_data["title"] = title
    context.user_data["sticker_count"] = 0
    context.user_data["status_intro"] = intro
    text = _status_text(context, lang)
    try:
        live = LiveMessage.restore(adopt)
        if live is None:
            live = await LiveMessage.send(context.bot, chat_id, text)
        else:
            await live.set(context.bot, text)
        context.user_data[STATUS_KEY] = live.save()
    except Exception:
        logger.exception("Couldn't set up the status message")


async def _refresh_status(context: ContextTypes.DEFAULT_TYPE, lang: str, note: str = ""):
    live = LiveMessage.restore(context.user_data.get(STATUS_KEY))
    if live is None:
        return
    await live.set(context.bot, _status_text(context, lang, note))
    # Saved back because set() may have moved it: a status the user talked
    # over is re-sent at the bottom, under a new message id.
    context.user_data[STATUS_KEY] = live.save()


async def _end_status(context: ContextTypes.DEFAULT_TYPE, final_note: str):
    live = LiveMessage.restore(context.user_data.get(STATUS_KEY))
    if live is not None:
        await live.finish(context.bot, final_note)
    for key in (STATUS_KEY, "sticker_count", "status_intro"):
        context.user_data.pop(key, None)


async def _owns_pack_or_deny(pack_name: str, user_id: int) -> bool:
    """True if user_id owns pack_name. Packs can only be managed (renamed,
    shared, or opened via the /mypacks menu) by their owner -- co-editors
    get the editing session only (adding, and taking a sticker out by
    sending it again), through a valid share link or an invitation of their
    @username.

    Async because it is a database round trip, and this runs on the first
    line of six different button handlers: doing it inline would stall every
    other user's update for the duration."""
    return await asyncio.to_thread(get_pack_owner, pack_name) == user_id


# ---------- /whomade ----------

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


# ---------- owner-only admin tools ----------

def _is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


async def _deny(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """How an owner-only command answers everybody else: exactly the way a
    misspelling does.

    The intent was always to avoid confirming the command exists, and a bare
    `return` looked like the way to do that. It is not: a bot that answers
    every command it has and ignores precisely one has just pointed at the
    interesting one, and to the person who typed it the bot simply looks
    broken. Giving back the same sentence a typo gets makes the owner-only
    commands indistinguishable from commands that were never there.
    """
    await unknown_command(update, context)


async def whois_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/whois <user_id> -- looks up a Telegram user's profile (name,
    username, bio) plus any packs of theirs on record, so a numeric id
    (e.g. from /whomade or the packs table) can be turned back into "which
    of my friends is this". Owner-only: this surfaces info about other
    people, not something every user should be able to query."""
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
    """/messageas <user_id> <text> -- sends a message to that user as this
    bot. Only works if the user has messaged the bot before (Telegram
    doesn't let bots cold-message anyone). Owner-only for obvious reasons."""
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
    """/dbdump -- exports every table in this bot's own database as one
    zip of CSVs. Owner-only: this is the whole user table, not something to
    hand out on request."""
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
    """/status -- uptime, hosting environment, any crashes since this
    process started, and active-user counts. Owner-only, same reasoning as
    /dbdump: this is operational info, not something every user should see."""
    if not _is_admin(update.effective_user.id):
        return await _deny(update, context)
    now = datetime.now(timezone.utc)
    users_hour = await asyncio.to_thread(count_active_users_since, now - timedelta(hours=1))
    users_since_start = await asyncio.to_thread(count_active_users_since, START_TIME)
    await update.message.reply_text(build_status_text(START_TIME, users_hour, users_since_start))


async def crashtest_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/crashtest -- owner-only, deliberately raises so you can confirm
    /status's error tracking + logs/errors.log actually catch something
    without needing a real bug. PTB's own dispatcher catches whatever this
    raises and routes it to error_handler (shared_features.py) -- polling
    keeps running either way. Safe to leave in permanently (gated same as
    every other admin command), or ask to have it removed once you're done
    testing."""
    if not _is_admin(update.effective_user.id):
        return await _deny(update, context)
    raise RuntimeError("Manual /crashtest trigger -- error tracking is working as intended.")


# ---------- /start and menu buttons ----------

async def _claim_invites(update: Update, context: ContextTypes.DEFAULT_TYPE, lang: str) -> None:
    """Packs whose owner invited this person's @username: they co-edit them from now on,
    and are told so once. Never in the way: a failure is a line in the log."""
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


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/start prints the instructions -- except the very first one, which
    asks for a language first.

    v0.7.0 made every /start the language picker, which put a three-button
    detour in front of the one command every Telegram user types by reflex,
    for the sake of a choice that is made once. The 0.6.0 shape is back, and
    the picker has moved to a command of its own:

      * No language on record -- a brand-new user -- and /start does exactly
        what /language does: greet in all three languages and ask. Picking
        one prints the instructions (see _apply_language), so the first
        /start still ends where every later one begins. This is the only
        time /start asks.
      * A language on record, and /start prints the instructions in it.
        Anyone who wants the picker back asks for it by name: /language.

    A deep link (/start s_<token>, a co-edit invite) still goes straight
    where it points, and a brand-new user who arrives on one picks a
    language first and is carried there afterwards by pending_start_args.
    """
    lang = await asyncio.to_thread(get_user_language, update.effective_user.id)
    await _claim_invites(update, context, lang or "en")
    if context.args:
        if lang is None:
            context.user_data["pending_start_args"] = context.args
            await update.message.reply_text(i18n.LANGUAGE_PROMPT, reply_markup=language_keyboard())
            return ConversationHandler.END
        context.user_data["lang"] = lang
        return await _continue_start(update, context, lang, context.args)

    # Since 2.2.0 a bare /start in the middle of a pack shows the menu and
    # leaves the pack open (the owner: "users should be able to do anything
    # else while they are adding new stickers"). Returning None keeps the
    # conversation in the state it was in; the next picture still goes in.
    if _has_status(context) and lang is not None:
        context.user_data["lang"] = lang
        await reply(update, build_start_text(lang), reply_markup=start_menu_keyboard(lang))
        return None
    if _has_status(context):
        await _end_status(context, i18n.t("en", "cancelled_status_note"))
    context.user_data.clear()

    if lang is None:
        await update.message.reply_text(i18n.LANGUAGE_PROMPT, reply_markup=language_keyboard())
        return ConversationHandler.END

    context.user_data["lang"] = lang
    return await _continue_start(update, context, lang, None)


async def language_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/language -- the picker on demand, which is what /start used to be.

    Same trilingual greeting and same three buttons, with a tick on the
    language in force so a returning user can see which one they are on
    before deciding to change it. The tap that follows runs through
    _apply_language like /en, /uz and /rus do, and so ends where they end:
    at the instructions, in the language just chosen.

    Deliberately *not* a conversation entry point. Asking to see the picker
    is not a reason to tear down a pack someone is halfway through editing;
    the tap that actually changes language is an entry point, because the
    instructions it prints have to leave the state machine at the top level.
    """
    lang = await asyncio.to_thread(get_user_language, update.effective_user.id)
    # Keep the cached language warm even though the picker itself is
    # trilingual -- the next handler this user hits would otherwise pay for
    # a database read that /language had already done.
    if lang:
        context.user_data["lang"] = lang
    await update.message.reply_text(i18n.LANGUAGE_PROMPT, reply_markup=language_keyboard(lang))


async def _continue_start(update: Update, context: ContextTypes.DEFAULT_TYPE, lang: str, args):
    if args and args[0].startswith("s_"):
        return await start_coedit_link(update, context, args[0][2:], lang)

    await reply(update, build_start_text(lang), reply_markup=start_menu_keyboard(lang))
    return ConversationHandler.END


async def _apply_language(update: Update, context: ContextTypes.DEFAULT_TYPE, lang: str):
    """The single path every language change goes through -- /en, /uz, /rus
    and a tap on the picker alike. Two things all of them have to do, which
    is why they share this:

    1. Show the instructions. Answering a language change with nothing but
       "Language set" leaves someone looking at a bot whose manual they have
       just made themselves unable to reach; printing the full help in the
       language they picked is the point of having picked it.
    2. Leave the user at the top level. This used to strand people: change
       language halfway through /newpack and the pack-editing conversation
       stayed open behind the start menu that appeared, so /newpack got
       "I don't recognize that command" and the New pack button did nothing
       at all. Clearing user_data and returning END puts the state machine
       where the screen says it is.

    pending_start_args is read back before the clear so a brand-new user who
    arrived on a co-edit link still lands in that pack once they have picked
    a language.
    """
    await asyncio.to_thread(set_user_language, update.effective_user.id, lang)
    pending = context.user_data.get("pending_start_args") or []
    if _has_status(context):
        await _end_status(context, i18n.t(lang, "cancelled_status_note"))
    context.user_data.clear()
    context.user_data["lang"] = lang
    # The menu follows the choice too -- Telegram otherwise shows it in the
    # language of the phone. After the clear, so the signature survives it.
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
    """A tap on the picker. Registered as a conversation entry point as well
    as a plain handler, because the very first thing a brand-new user does
    can be opening someone's co-edit link -- and that has to be able to put
    them straight into an editing session. Being an entry point is also what
    lets the END _apply_language returns actually close a conversation the
    tap interrupted: a plain handler's return value is thrown away."""
    query = update.callback_query
    lang = query.data.split(":", 1)[1]
    await query.answer(i18n.t(lang, "language_set_confirmation"))
    return await _apply_language(update, context, lang)


async def menu_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """'⬅️ Back' from deeper in the menu -- edits back to the start view
    instead of leaving a trail of old menu messages behind."""
    lang = await i18n.get_lang(update.effective_user.id, context)
    await update.callback_query.answer()
    await edit_in_place(update.callback_query.message, context.bot, build_start_text(lang), reply_markup=start_menu_keyboard(lang))


async def start_coedit_link(update: Update, context: ContextTypes.DEFAULT_TYPE, token: str, lang: str):
    """Handles /start s_<token> -- someone opening a co-edit share link.

    Replies through reply() rather than update.message, because a first-time
    user reaches this by tapping a language button, and a button tap has no
    message of its own to reply to."""
    user = update.effective_user
    # The link's own limits (owner, 2026-10-28): how many editors it lets in, and until
    # when. Somebody already in comes back in whatever the link's state.
    outcome, pack_name = await asyncio.to_thread(join_by_token, token, user.id)
    refusal = {"invalid": "coedit_link_invalid", "gone": "coedit_pack_gone", "own": "coedit_own_pack",
               "expired": "coedit_link_expired", "full": "coedit_link_full"}.get(outcome)
    if refusal:
        await reply(update, i18n.t(lang, refusal))
        return ConversationHandler.END
    owner_id = await asyncio.to_thread(get_pack_owner, pack_name)
    title = await asyncio.to_thread(get_pack_title, pack_name) or pack_name

    context.user_data.clear()
    context.user_data["lang"] = lang
    context.user_data["mode"] = "add"
    context.user_data["pack_created"] = True
    context.user_data["target_pack"] = pack_name
    context.user_data["owner_id"] = owner_id

    intro = i18n.t(lang, "coedit_joined_intro", title=title)
    await _start_status(context, update.effective_chat.id, title, lang, intro=intro)
    return EDITING


async def menu_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """The Help button on a start menu sent before 2.1.0: the full guide."""
    lang = await i18n.get_lang(update.effective_user.id, context)
    await update.callback_query.answer()
    await edit_in_place(update.callback_query.message, context.bot, build_help_text(lang, full=True),
                        reply_markup=help_keyboard(lang, True, help_rows(lang), HELP_PAGES, HELP_LABELS))


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/help is the short version; /help full is the long one."""
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
    await reply(update, text, reply_markup=packs_keyboard(packs, lang, shared))


# ---------- pack detail menu (Add / Rename / Co-edit) ----------

async def pack_detail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    pack_name = query.data.split(":", 1)[1]
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
    """The 🔒/🌐 button: a pack private (only its owner and co-editors add to it) or
    public (also anybody in a group its owner or a co-editor uses the bot in). Owner only."""
    query = update.callback_query
    pack_name = query.data.split(":", 1)[1]
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


# ---------- co-editing menu ----------

# The owner (2026-10-28): "The link might be shared/leaked to a group too large. Add options
# when creating a link, like max editors (10 by default excluding owner), adding editor list
# by telegram username (manually) or an activity period (1 week by default)." So the screen
# shows the link with its limits, and buttons that cycle each (db.MAX_EDITORS_CHOICES,
# db.VALID_DAYS_CHOICES), invite people by @username, and list everybody with a ✖ each. And
# (owner, same day): "let the owner pick if they want to grant delete rights to the link" --
# the 🗑 switch, off by default, lets every co-editor of the pack delete stickers.
#
# Its buttons carry the pack's number ("co:<action>:<id>"): a pack's name can be 48
# characters, and a button's data 64 bytes. The cotoken_reset:<name> buttons of messages sent
# before this still work.
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
    # One trip to the database for everything the screen shows (get_coedit_view).
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
            [InlineKeyboardButton(i18n.t(lang, "btn_back"), callback_data=f"packopen:{pack_name}")],
        ]
    )
    await edit_in_place(message, bot, text, reply_markup=kb, link_preview_options=NO_PREVIEW)


async def _editor_name(bot, user_id: int) -> str:
    """How the owner's list names a co-editor: their name as Telegram gives it now, or
    their number when Telegram will not say."""
    try:
        chat = await bot.get_chat(user_id)
    except Exception:
        return f"#{user_id}"
    if chat.username:
        return f"@{chat.username}"
    return chat.full_name or f"#{user_id}"


# The list shows this many people at most; the rest are summed up in a line.
CO_LIST_MAX = 30
# How much of an invitation's mark (64 hex characters) its ✖ button carries.
INVITE_MARK_CHARS = 16


async def _send_coedit_list(message, bot, pack_name: str, pid: int, lang: str):
    editors = await asyncio.to_thread(pack_editors, pack_name)
    invites = await asyncio.to_thread(pack_invites, pack_name)
    title = await asyncio.to_thread(get_pack_title, pack_name) or pack_name
    lines = [i18n.t(lang, "co_list_heading", title=title)]
    rows = []
    # Asked of Telegram all at once: one at a time, thirty names took seconds to draw.
    names = await asyncio.gather(*(_editor_name(bot, user_id) for user_id in editors[:CO_LIST_MAX]))
    for user_id, name in zip(editors, names):
        lines.append(f"• {name}")
        rows.append([InlineKeyboardButton(f"✖ {name}", callback_data=f"{CO_PREFIX}rme:{pid}:{user_id}")])
    for mark, username in invites[:CO_LIST_MAX]:
        lines.append(i18n.t(lang, "co_list_invited", username=username))
        # The start of the invitation's mark, not its place in the list: the list can change
        # (somebody takes up an invitation) between drawing it and the tap.
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
    """(pack name, pack number, action, the rest of the button's data) for a co-edit button
    whose pack the tapper owns, or None once they have been told why not."""
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
    """Every button of the co-edit screen and its list, except "➕ Add by @username"
    (invite_start), which waits for typing and so belongs to the conversation."""
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


# A Telegram username: 5 to 32 letters, digits and underscores, starting with a letter.
USERNAME_RE = re.compile(r"^@?([A-Za-z][A-Za-z0-9_]{4,31})$")
INVITES_PER_MESSAGE = 20


async def invite_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """"➕ Add by @username": asks for the names, as a message. An open pack stays open
    behind the question, as behind a rename."""
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
    context.user_data.clear()
    context.user_data.update(session)
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
    pack_name = query.data.split(":", 1)[1]
    user = update.effective_user
    lang = await i18n.get_lang(user.id, context)

    if not await _owns_pack_or_deny(pack_name, user.id):
        await query.answer(i18n.t(lang, "only_owner_coedit"), show_alert=True)
        return
    await query.answer()
    await _send_coedit_message(query.message, context.bot, pack_name, lang)


async def coedit_reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    pack_name = query.data.split(":", 1)[1]
    user = update.effective_user
    lang = await i18n.get_lang(user.id, context)

    if not await _owns_pack_or_deny(pack_name, user.id):
        await query.answer(i18n.t(lang, "only_owner_coedit"), show_alert=True)
        return

    await asyncio.to_thread(reset_share_token, pack_name)
    await query.answer(i18n.t(lang, "link_reset_confirm"))
    await _send_coedit_message(query.message, context.bot, pack_name, lang)


# ---------- rename ----------

async def rename_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    pack_name = query.data.split(":", 1)[1]
    user = update.effective_user
    lang = await i18n.get_lang(user.id, context)

    if not await _owns_pack_or_deny(pack_name, user.id):
        await query.answer(i18n.t(lang, "only_owner_rename"), show_alert=True)
        return None  # a pack being filled stays open, as behind a rename that goes ahead

    await query.answer()
    # A pack being filled stays open behind the rename (receive_new_title goes
    # back to it); anything else that was waiting is dropped, as before.
    session = _session_of(context)
    context.user_data.clear()
    context.user_data.update(session)
    context.user_data["lang"] = lang
    context.user_data["rename_pack"] = pack_name
    # The new title arrives as a plain text message, not a button tap --
    # stashing this prompt's handle lets receive_new_title turn it into the
    # result instead of sending a separate reply. By then the user's own
    # title message is underneath it, so the handle will (correctly) send a
    # new message rather than rewrite one they have already scrolled past.
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
    new_title = update.message.text.strip()[:PACK_TITLE_MAX]  # Telegram title limit
    if not pack_name or prompt is None:
        await update.message.reply_text(i18n.t(lang, "rename_broken_state"))
        return EDITING if _session_of(context) else ConversationHandler.END

    kb = InlineKeyboardMarkup([[InlineKeyboardButton(i18n.t(lang, "btn_back_to_pack"), callback_data=f"packopen:{pack_name}")]])
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
    context.user_data.clear()
    return ConversationHandler.END


# ---------- delete pack (owner-only, two taps to confirm) ----------

async def delete_pack_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """First tap of 'Delete pack' -- owner-only (co-editors never even see
    this button, since it only appears in the /mypacks detail menu, but the
    check is repeated here too since callback_data can be replayed)."""
    query = update.callback_query
    pack_name = query.data.split(":", 1)[1]
    user = update.effective_user
    lang = await i18n.get_lang(user.id, context)

    if not await _owns_pack_or_deny(pack_name, user.id):
        await query.answer(i18n.t(lang, "only_owner_delete"), show_alert=True)
        return
    await query.answer()

    title = await asyncio.to_thread(get_pack_title, pack_name) or pack_name
    kb = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(i18n.t(lang, "btn_delete"), callback_data=f"packdelconfirm1:{pack_name}")],
            [InlineKeyboardButton(i18n.t(lang, "btn_cancel_inline"), callback_data=f"packopen:{pack_name}")],
        ]
    )
    await edit_in_place(query.message, context.bot, 
        i18n.t(lang, "delete_confirm1", title=title),
        reply_markup=kb,
    )


async def delete_pack_confirm1(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Second tap -- one more explicit confirmation before anything actually happens."""
    query = update.callback_query
    pack_name = query.data.split(":", 1)[1]
    user = update.effective_user
    lang = await i18n.get_lang(user.id, context)

    if not await _owns_pack_or_deny(pack_name, user.id):
        await query.answer(i18n.t(lang, "only_owner_delete"), show_alert=True)
        return
    await query.answer()

    title = await asyncio.to_thread(get_pack_title, pack_name) or pack_name
    kb = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(i18n.t(lang, "btn_delete_confirm"), callback_data=f"packdelconfirm2:{pack_name}")],
            [InlineKeyboardButton(i18n.t(lang, "btn_cancel_inline"), callback_data=f"packopen:{pack_name}")],
        ]
    )
    await edit_in_place(query.message, context.bot, 
        i18n.t(lang, "delete_confirm2", title=title),
        reply_markup=kb,
    )


async def delete_pack_confirm2(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Third tap -- actually deletes the set from Telegram and wipes local records."""
    query = update.callback_query
    pack_name = query.data.split(":", 1)[1]
    user = update.effective_user
    lang = await i18n.get_lang(user.id, context)

    # Awaited since 2.2.0: without it the coroutine itself was the answer, always truthy, so
    # this check never refused anybody -- a forged button could reach the delete below.
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
        # Telegram already has no record of it (e.g. auto-deleted earlier
        # after its last sticker was removed) -- just clean up our side.

    await asyncio.to_thread(delete_pack_records, pack_name)
    kb = InlineKeyboardMarkup([[InlineKeyboardButton(i18n.t(lang, "btn_my_packs_back"), callback_data="menu_mypacks")]])
    await edit_in_place(query.message, context.bot, i18n.t(lang, "delete_success", title=title), reply_markup=kb)


# ---------- /newpack (and "New pack" button) ----------

async def newpack_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = await i18n.get_lang(update.effective_user.id, context)
    context.user_data.clear()
    context.user_data["lang"] = lang
    context.user_data["mode"] = "new"
    context.user_data["pack_created"] = False
    context.user_data["owner_id"] = update.effective_user.id
    if update.callback_query:
        await update.callback_query.answer()
    msg = await reply(update, i18n.t(lang, "newpack_title_prompt"))
    # So receive_title can turn THIS message into the status display below,
    # instead of sending a separate one once the title arrives -- assuming
    # it is still the last thing in the chat by then, which is exactly what
    # the handle is for.
    context.user_data["title_prompt"] = msg.save()
    return TITLE


async def receive_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = await i18n.get_lang(update.effective_user.id, context)
    title = update.message.text.strip()
    if not title:
        await update.message.reply_text(i18n.t(lang, "title_empty"))
        return TITLE
    if len(title) > PACK_TITLE_MAX:  # Telegram's hard limit on set titles
        title = title[:PACK_TITLE_MAX]
        await update.message.reply_text(i18n.t(lang, "title_truncated", title=title))

    intro = i18n.t(lang, "editing_intro_new")
    prompt = context.user_data.pop("title_prompt", None)
    await _start_status(
        context, update.effective_chat.id, title, lang, intro=intro, adopt=prompt,
    )
    return EDITING


# ---------- /addsticker (and per-pack "Add" button) ----------

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
    await update.message.reply_text(
        i18n.t(lang, "pick_pack_prompt"), reply_markup=packs_keyboard(packs, lang)
    )


async def pack_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    pack_name = query.data.split(":", 1)[1]
    user = update.effective_user
    lang = await i18n.get_lang(user.id, context)

    access = await asyncio.to_thread(pack_access, pack_name, user.id)
    if access is None or not access["allowed"]:
        # None, not END: a refused tap (a 👥 pack whose owner has since removed you) leaves a
        # pack you have open as it was.
        await query.answer(i18n.t(lang, "not_your_pack"), show_alert=True)
        return None
    owner_id = access["owner_id"]
    await query.answer()

    context.user_data.clear()
    context.user_data["lang"] = lang
    context.user_data["mode"] = "add"
    context.user_data["pack_created"] = True
    context.user_data["target_pack"] = pack_name
    context.user_data["owner_id"] = owner_id

    # The tip says what sending a sticker that is already in it does for this person:
    # take it out (the owner, or a co-editor allowed to delete), or ask about adding it again.
    deletes = owner_id == user.id or access["can_delete"]
    intro = i18n.t(lang, "editing_intro_add" if deletes else "editing_intro_shared")
    title = await asyncio.to_thread(get_pack_title, pack_name) or pack_name
    await _start_status(
        context, query.message.chat_id, title, lang,
        intro=intro, adopt=LiveMessage.adopt(query.message).save(),
    )
    return EDITING


# ---------- the editing loop ----------

async def _add_input_sticker(
    context: ContextTypes.DEFAULT_TYPE, user, input_sticker: InputSticker,
    want_file_id: bool = True,
) -> "tuple[str | None, int | None]":
    """Creates the pack (first sticker of a /newpack session) or adds to the
    existing target pack. Returns the newly-added sticker's file_id and its
    place in the pack, or (None, None) with want_file_id=False.

    Always acts on Telegram's behalf of the pack's *owner* (owner_id), even
    if a co-editor is the one physically sending the sticker through the
    bot -- Telegram's API ties set ownership to that id, not the caller.

    That file_id costs an extra getStickerSet call -- the API has no way to
    return it from the add itself -- and it exists only so the next message
    can retag the sticker with different emoji. During a bulk import nobody
    is going to retag sticker #37 of 100, so want_file_id=False skips a
    hundred round trips and the whole set is read once at the end instead.
    """
    owner_id = context.user_data["owner_id"]
    if owner_id != user.id and context.user_data.get("pack_created"):
        await _still_coediting(context, user.id)

    if not context.user_data.get("pack_created"):
        title = context.user_data["title"]
        # A random suffix (not the owner's Telegram user id) keeps the pack
        # name -- and therefore the public https://t.me/addstickers/... link
        # everyone sees -- from doubling as a permanent, unrevokable pointer
        # to the creator's numeric user id. Ownership is still tracked
        # server-side via add_pack()/get_pack_owner(), so nothing else here
        # depends on what's actually in the name.
        pack_name = f"{slugify(title)}_{secrets.token_hex(5)}_by_{BOT_USERNAME}"
        # Uploading a sticker (especially a converted video one) can take
        # longer than PTB's default 5s read/write timeout, which was
        # showing up as a spurious "Timed out" failure even when the
        # upload was going through fine -- just slowly.
        await context.bot.create_new_sticker_set(
            user_id=owner_id,
            name=pack_name,
            title=title,
            stickers=[input_sticker],
            read_timeout=60,
            write_timeout=60,
        )
        await asyncio.to_thread(
            add_pack, owner_id, pack_name, title, user.username, user.full_name
        )
        context.user_data["target_pack"] = pack_name
        context.user_data["pack_created"] = True
        if not want_file_id:
            return None, None
        return await sticker_ops.last_sticker(context.bot, pack_name)
    return await sticker_ops.add_to_pack(context.bot, owner_id, context.user_data["target_pack"],
                                         input_sticker, want_file_id)


class NoLongerEditor(Exception):
    """The pack's owner removed this co-editor while their session was open."""


# How long a co-editor's access, once checked, is trusted within a session: a bulk import
# adds up to a hundred stickers, and one question a minute is enough to stop a removed one.
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
    """The keys of an open editing session, to carry it past something else
    (a rename) that starts from a clean user_data. Empty when none is open."""
    if not _has_status(context):
        return {}
    keys = ("mode", "pack_created", "target_pack", "owner_id", "title", "sticker_count",
            "status_intro", "last_sticker_id", "nudge_counted", STATUS_KEY)
    return {key: context.user_data[key] for key in keys if key in context.user_data}


def _behind_question(handler):
    """`handler` for a picture sent while a rename or an invitation waits for typing. With a
    pack open behind the question (since 2.2.0 it stays open there), the picture goes into the
    pack and the question keeps waiting; with none open, nothing happens, as before."""
    async def run(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if _has_status(context):
            await handler(update, context)
        return None
    return run


async def _say(message, working, text: str, **kwargs):
    """`working` (the "⏳ Converting..." reply) turned into `text`, or a new reply to
    `message` when there is none. Returns the message that now says it."""
    if working is not None:
        await working.edit_text(text, **kwargs)
        return working
    return await message.reply_text(text, **kwargs)


async def _add_and_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE, lang: str,
                           input_sticker: InputSticker, working=None) -> None:
    """Add one sticker to the session's pack and answer that one message: added as #N, or
    why not. Both the person's message and the answer are remembered as pointing at the
    new sticker, so a reply to either with emoji retags it (sticker_ops)."""
    msg = update.message
    try:
        file_id, number = await _add_input_sticker(context, update.effective_user, input_sticker)
    except NoLongerEditor as exc:
        await _say(msg, working, i18n.t(lang, "coedit_removed_access", title=str(exc)))
        return
    except Exception as exc:
        logger.exception("Sticker set operation failed")
        record_error(exc)
        await _say(msg, working, sticker_ops.error_note(exc, lang))
        return
    context.user_data["last_sticker_id"] = file_id
    context.user_data["sticker_count"] = context.user_data.get("sticker_count", 0) + 1
    pack_name = context.user_data["target_pack"]
    title = context.user_data.get("title") or pack_name
    # Named every time: with nothing asking for /done any more, a pack can stay open for
    # days, and the reply is where the person sees which one a picture went into. The
    # first sticker of a new pack also carries its link -- what /done used to be for.
    text = i18n.t(lang, "sticker_added_to", title=title, n=number, emoji=DEFAULT_EMOJI)
    if number == 1:
        text = i18n.t(lang, "pack_created_link", pack_name=pack_name) + "\n" + text
    sent = await _say(msg, working, text)
    sticker_ops.remember(msg.chat_id, msg.message_id, pack_name, file_id)
    sticker_ops.remember(sent.chat_id, sent.message_id, pack_name, file_id)
    if not context.user_data.get("nudge_counted"):
        # One "finished something" per session for the donation cadence, as /done was
        # until 2.2.0 (shared_features: not shown every time).
        context.user_data["nudge_counted"] = True
        nudge = await maybe_donation_nudge(update.effective_user.id, lang, context, msg.chat_id)
        if nudge:
            await msg.reply_text(nudge)


async def _remember_last_sticker(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Run once after a bulk import so "send emoji to retag" still points at
    the last sticker added -- one getStickerSet instead of one per sticker."""
    pack_name = context.user_data.get("target_pack")
    if not pack_name:
        return
    try:
        sticker_set = await context.bot.get_sticker_set(pack_name)
        context.user_data["last_sticker_id"] = sticker_set.stickers[-1].file_id
    except Exception:
        logger.debug("Couldn't re-read the pack after a bulk import", exc_info=True)


async def _is_own_pack_sticker(context: ContextTypes.DEFAULT_TYPE, sticker) -> bool:
    """True if an incoming sticker message is a sticker that's still
    actually in the pack currently being edited (as opposed to a fresh
    image, or a sticker from some *other* pack, which still gets added
    normally).

    `sticker.set_name` alone isn't enough to decide this: it reflects
    whatever set the message's *sender* last saw the sticker belong to, and
    a sender's sticker panel can lag behind a removal the bot itself just
    made in this same session (e.g. remove it, then immediately re-send the
    same sticker from their panel before Telegram's client has refreshed
    it). Trusting set_name alone in that case would try to delete an
    already-deleted sticker and fail with STICKERSET_NOT_MODIFIED instead of
    doing what the user clearly wants: re-add it. So once set_name points at
    our pack, confirm against the pack's actual current contents (sticker_ops.in_pack, which
    group chats use too)."""
    return await sticker_ops.in_pack(context.bot, context.user_data.get("target_pack"), sticker)


async def _sticker_already_in_pack(update: Update, context: ContextTypes.DEFAULT_TYPE, sticker) -> int:
    """A sticker sent that is already in the pack being filled: asked about, never acted on at
    once (group_mode.ask_add_again, where the owner's words on it are). Whoever may delete --
    the owner, or a co-editor the owner allows (the co-edit screen's 🗑) -- chooses between
    adding it again and removing it; anybody else between adding it again or not, and is told
    that only the owner deletes. Until 2.2.0 it was removed at once, whoever sent it."""
    user_id = update.effective_user.id
    pack_name = context.user_data["target_pack"]
    lang = await i18n.get_lang(user_id, context)
    access = await asyncio.to_thread(pack_access, pack_name, user_id)
    title = context.user_data.get("title") or (access or {}).get("title") or pack_name
    if access is None or not access["allowed"]:
        await update.message.reply_text(i18n.t(lang, "coedit_removed_access", title=title))
        return EDITING
    kind, file_id = sticker_ops.media_of(update.message)
    await group_mode.ask_add_again(update, context, lang, pack_name, title, kind, file_id, in_group=False,
                                   can_remove=access["can_delete"])
    return EDITING


async def already_in_pack_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """A tap on that question: add it again (y), don't (n), remove it (r) -- and, for the
    pack's last sticker, the owner's "delete the whole pack?" (R) or "keep it" (c). Only by
    whoever was asked, once. A conversation entry point as well as a plain handler, like
    cancel_choice_callback: deleting the pack this way has to be able to close the session
    that was filling it. Returns None (the conversation stays as it was) otherwise."""
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
    """ "Remove it": the right to delete is checked again (the owner may have switched it off
    since). The pack's last sticker takes the whole pack with it -- Telegram deletes an empty
    set -- so the owner is asked once more, and a co-editor is never let do it."""
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
        await context.bot.delete_sticker_from_set(sticker=asked["file_id"])
    except Exception as exc:
        logger.exception("Failed to delete a sticker from its set")
        record_error(exc)
        await query.message.edit_text(i18n.t(lang, "remove_failed", error=exc))
        return None
    filling = context.user_data.get("target_pack") == pack_name
    if last:
        await asyncio.to_thread(delete_pack_records, pack_name)  # Telegram auto-deleted the empty set
        await query.message.edit_text(i18n.t(lang, "pack_deleted_empty"))
        if filling:
            await _end_status(context, i18n.t(lang, "pack_deleted_note"))
            context.user_data.clear()
            return ConversationHandler.END
        return None
    if filling:
        context.user_data["sticker_count"] = max(0, context.user_data.get("sticker_count", 0) - 1)
    await query.message.edit_text(i18n.t(lang, "remove_success"))
    return None


async def confirm_remove_last_sticker(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """The "delete the whole pack?" buttons sent before 2.2.0, still answered for messages already
    in people's chats; since then the question is already_in_pack_chosen's."""
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
        await context.bot.delete_sticker_from_set(sticker=file_id)
    except Exception as exc:
        logger.exception("Failed to delete the last sticker / pack")
        record_error(exc)
        await edit_in_place(query.message, context.bot, i18n.t(lang, "remove_failed", error=exc))
        return EDITING

    if target_pack:
        await asyncio.to_thread(delete_pack_records, target_pack)  # Telegram auto-deleted the empty set
    await edit_in_place(query.message, context.bot, i18n.t(lang, "pack_deleted_empty"))
    await _end_status(context, i18n.t(lang, "pack_deleted_note"))
    context.user_data.clear()
    return ConversationHandler.END


async def receive_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Static images / static stickers -> PNG sticker."""
    msg = update.message
    lang = await i18n.get_lang(update.effective_user.id, context)
    if msg.sticker and await _is_own_pack_sticker(context, msg.sticker):
        return await _sticker_already_in_pack(update, context, msg.sticker)

    if msg.photo:
        file_id = msg.photo[-1].file_id
    elif msg.document:
        file_id = msg.document.file_id
    elif msg.sticker:
        file_id = msg.sticker.file_id
    else:
        return EDITING

    try:
        input_sticker = await sticker_ops.static_sticker(await sticker_ops.download(context.bot, file_id))
    except Exception as exc:
        record_error(exc)
        await msg.reply_text(i18n.t(lang, "image_process_failed", error=exc))
        return EDITING

    await _add_and_confirm(update, context, lang, input_sticker)
    return EDITING


async def receive_video_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """GIFs / videos / video stickers -> WEBM video sticker (via ffmpeg)."""
    msg = update.message
    lang = await i18n.get_lang(update.effective_user.id, context)
    if msg.sticker and await _is_own_pack_sticker(context, msg.sticker):
        return await _sticker_already_in_pack(update, context, msg.sticker)

    if msg.animation:
        file_id = msg.animation.file_id
    elif msg.video:
        file_id = msg.video.file_id
    elif msg.document:
        file_id = msg.document.file_id
    elif msg.sticker:
        file_id = msg.sticker.file_id
    else:
        return EDITING

    # An encode takes longer than the platform's shutdown grace period, so
    # starting one into a landing update would only end in silence. Saying so
    # costs the user a few seconds; not saying so costs them the file.
    refusal = await refuse_new_work(lang, update.effective_user.id, msg.chat_id)
    if refusal:
        await msg.reply_text(refusal)
        return EDITING

    raw = await sticker_ops.download(context.bot, file_id)

    # The reply this clip gets is its progress message too: "converting", then
    # "added as #N", or why not.
    working = await msg.reply_text(i18n.t(lang, "converting_video"))
    try:
        async with lifecycle.busy(msg.chat_id, i18n.t(lang, "restarting_send_again")):
            input_sticker = await sticker_ops.video_sticker(raw, lang)
    except ConversionError as exc:
        # Couldn't hit the 256 KB video-sticker target -- the raw clip itself is
        # still fine, so point at @ConvertBot for a plain (non-sticker)
        # conversion instead of just dead-ending here. No file handoff between
        # bots anymore (each bot is fully independent) -- the user re-sends
        # the file over there themselves.
        record_error(exc)
        kb = InlineKeyboardMarkup([sibling_bots_keyboard_row(BOT_NAME, only="convertbot")])
        await working.edit_text(i18n.t(lang, "video_convert_failed_redirect", error=exc), reply_markup=kb)
        return EDITING
    except Exception as exc:
        logger.exception("Video conversion failed")
        record_error(exc)
        await working.edit_text(i18n.t(lang, "video_convert_generic_failed", error=exc))
        return EDITING

    await _add_and_confirm(update, context, lang, input_sticker, working)
    return EDITING


async def reject_unsupported_sticker(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = await i18n.get_lang(update.effective_user.id, context)
    await update.message.reply_text(i18n.t(lang, "animated_not_supported"))
    return EDITING


# ---------- bulk import (Telegram pack or WhatsApp zip) ----------

async def import_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/import <link or name> -- copies stickers from another public
    Telegram pack into the pack currently being edited. Re-uses each source
    sticker's file_id directly, so quality and emoji tags carry over as-is."""
    lang = await i18n.get_lang(update.effective_user.id, context)
    if not context.args:
        await update.message.reply_text(i18n.t(lang, "import_usage"))
        return EDITING

    source = " ".join(context.args)
    pack_source = parse_telegram_pack_source(source)
    if not pack_source:
        await update.message.reply_text(i18n.t(lang, "import_invalid_source"))
        return EDITING

    # A bulk import is the longest thing this bot does -- dozens of stickers
    # added one at a time, paced to stay under Telegram's rate limit -- and it
    # was the one long path with no check that an update was coming. Starting
    # one into a landing deploy leaves a half-filled pack and no explanation.
    refusal = await refuse_new_work(lang, update.effective_user.id, update.effective_chat.id)
    if refusal:
        await update.message.reply_text(refusal)
        return EDITING

    # Progress and result both land in the session's status message -- see
    # _start_status. A bulk import is the longest thing this bot does, so it
    # is also the one most likely to have the user typing over it; the
    # handle moves the whole report down to the bottom when they do.
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
                await asyncio.sleep(0.3)  # be gentle with Telegram's API during bulk adds
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
    """Top-level /import fallback for when the user isn't currently editing a pack."""
    lang = await i18n.get_lang(update.effective_user.id, context)
    await update.message.reply_text(i18n.t(lang, "import_standalone_hint"))


async def done_standalone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Same idea for /done. Both commands sit in Telegram's command menu, so
    both are one tap away at any moment, including when there is no editing
    session for them to act on -- answering that with "I don't recognize that
    command" is the bot disowning something it is itself advertising."""
    lang = await i18n.get_lang(update.effective_user.id, context)
    await update.message.reply_text(i18n.t(lang, "done_standalone_hint"))


async def receive_whatsapp_import(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """A .zip/.wastickers file sent while editing -- treated as a WhatsApp
    sticker pack export and bulk-imported."""
    msg = update.message
    lang = await i18n.get_lang(update.effective_user.id, context)

    # Same reasoning as /import, which this is the other half of: a zip of
    # stickers added one by one outlasts a shutdown grace period easily.
    refusal = await refuse_new_work(lang, update.effective_user.id, msg.chat_id)
    if refusal:
        await msg.reply_text(refusal)
        return EDITING

    tg_file = await context.bot.get_file(msg.document.file_id)
    raw = await tg_file.download_as_bytearray()

    await _refresh_status(context, lang, i18n.t(lang, "whatsapp_reading"))
    try:
        items = await asyncio.to_thread(parse_whatsapp_zip, bytes(raw), lang)
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
        # Nothing is asked for here since 2.2.0. It used to answer "Send an
        # image/GIF/video/sticker to add, emoji to retag the last one, or
        # /done", and the owner had it removed: "users should be able to do
        # anything else while they are adding new stickers to a pack".
        return EDITING
    if update.message.reply_to_message is not None:
        # A reply with emoji whose sticker emoji_reply could not find (the bot
        # restarted since, or it is not a sticker of a pack made here):
        # retagging the *last* sticker instead would be the wrong one.
        await update.message.reply_text(i18n.t(lang, "emoji_target_unknown"))
        return EDITING

    last_sticker_id = context.user_data.get("last_sticker_id")
    if not last_sticker_id:
        await update.message.reply_text(i18n.t(lang, "no_sticker_to_tag"))
        return EDITING

    emojis = split_emoji(text)
    try:
        await context.bot.set_sticker_emoji_list(sticker=last_sticker_id, emoji_list=emojis)
        await update.message.reply_text(i18n.t(lang, "retagged_success", emojis=" ".join(emojis)))
    except Exception as exc:
        await update.message.reply_text(i18n.t(lang, "retag_failed", error=exc))

    return EDITING


class _EmojiReply(filters.MessageFilter):
    """A message of only emoji that replies to a sticker, or to a message remembered as
    standing for one (sticker_ops.sticker_of)."""

    def filter(self, message) -> bool:
        text = message.text or ""
        return (message.reply_to_message is not None and not text.startswith("/")
                and looks_like_emoji_message(text)
                and sticker_ops.sticker_of(message.reply_to_message) is not None)


async def emoji_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Emoji, as a reply to a sticker or to the bot's "Added" reply: retag that sticker. In
    the middle of a session or not -- it is registered ahead of the conversation, and leaves
    its state where it was."""
    await group_mode.change_emoji(update, context, update.message.reply_to_message, update.message.text)


async def emoji_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/emoji 😎 -- as a reply, that sticker; otherwise, in a session, the last one added.
    /emoji alone as a reply asks for the emoji (group_mode.ask_for_emoji)."""
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

    # Nothing asks for /done since 2.2.0 -- every sticker is saved as it is added, and the
    # first one's reply carries the link -- so the donation checkpoint moved to the first
    # sticker of a session (_add_and_confirm). This only closes the pack and sums it up.
    context.user_data.clear()
    return ConversationHandler.END


async def convert_redirect_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/convert lives in @ConvertBot now -- keep the command here as a
    friendly redirect for anyone still typing it out of habit."""
    lang = await i18n.get_lang(update.effective_user.id, context)
    kb = InlineKeyboardMarkup([sibling_bots_keyboard_row(BOT_NAME, only="convertbot")])
    await update.message.reply_text(
        i18n.t(lang, "convert_redirect"),
        reply_markup=kb,
    )


# The three pack states, as opposed to the donation prompt: cancelling one
# of these is what closes the ConversationHandler, and cancelling anything
# else must not.
PACK_CANCEL_KEYS = ("rename", "editing", "newpack", "invite")
# Questions asked on top of an open pack (since 2.2.0 a rename or an invitation keeps it):
# cancelling one of them goes back to the pack instead of closing it.
OVER_PACK_KEYS = {"rename": ("rename_pack", "rename_prompt"), "invite": ("invite_pack", "invite_pack_id")}


async def _cancel_items(update: Update, context: ContextTypes.DEFAULT_TYPE, lang: str) -> list[CancelItem]:
    """Everything this bot could be waiting on, named well enough to choose
    between. The three pack states are mutually exclusive by construction --
    newpack_start and rename_start both clear user_data before setting their
    own keys -- so at most one of them is ever on offer, and the real choice
    this menu exists for is "the pack, or the donation prompt, or both".
    """
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
    """Stop the chosen things; report what they were and whether a pack went
    with them.

    That second answer is what decides whether the conversation ends, and it
    is the whole reason this is not one indiscriminate reset any more:
    cancelling a donation prompt has no business closing an editing session
    the user never mentioned. The reset that a pack cancellation does still
    need is given back anything not chosen -- in practice the donation
    prompt, the only other thing that can be live at the same time.
    """
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
            # The editing session owns the status message -- close that out
            # before the reset clears the keys _end_status finds it by.
            await _end_status(context, i18n.t(lang, "cancelled_status_note"))
        if chosen in PACK_CANCEL_KEYS:
            pack_stopped = True
            stopped.append(item.label)
        elif cancel_shared_item(context, lang, chosen):
            stopped.append(item.label)

    if pack_stopped:
        keep = {"lang": lang}
        if "donate_custom_currency" in context.user_data:
            keep["donate_custom_currency"] = context.user_data["donate_custom_currency"]
        reset_user_state(context, keep)
    return stopped, pack_stopped


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Asks which of the things it is waiting on to stop, and stops nothing
    until the answer comes back.

    "Cancelled." on its own used to leave you guessing which of several
    half-finished things it had caught; v0.7.0 fixed that by naming them
    afterwards. This is the other half: being asked first, so that a pack
    you are three stickers into is not collateral damage of wanting out of a
    donation prompt.

    Returns None while the question is on screen -- the conversation is still
    open at that point, and it is the button tap that ends it, if the answer
    turns out to be a pack. With nothing pending there is nothing to ask, so
    this answers immediately, exactly as it always has.
    """
    lang = await i18n.get_lang(update.effective_user.id, context)
    items = await _cancel_items(update, context, lang)
    if await ask_cancel_choice(update, context, items, lang):
        return None
    await finish_cancel(update, context, lang, [])
    return ConversationHandler.END


async def cancel_choice_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """A tap on one of /cancel's buttons.

    Registered as a conversation entry point as well as a plain handler, for
    the same reason language_chosen is: a plain handler's return value is
    thrown away, and this one has to be able to close the conversation the
    /cancel it answers was sent from.
    """
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
    await finish_cancel_choice(update, context, lang, stopped)
    if pack_stopped:
        return ConversationHandler.END
    # A rename or an invitation cancelled on top of an open pack: back to the pack. Cancelling
    # only something else (the donation prompt) leaves the conversation where it was.
    if stopped and _has_status(context) and any(key in OVER_PACK_KEYS for key in keys):
        return EDITING
    return None


async def unrecognized_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Only ever reached with no active /newpack or pack-editing session --
    while one's active, the conversation's own state handlers (e.g.
    maybe_emoji_override) claim the update first. Registered last, so
    every real handler above gets first shot."""
    lang = await i18n.get_lang(update.effective_user.id, context)
    await update.message.reply_text(i18n.t(lang, "unrecognized"))


async def unknown_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = await i18n.get_lang(update.effective_user.id, context)
    await update.message.reply_text(i18n.t(lang, "unknown_command"))


async def _post_init(application):
    await tune_runtime(application)
    # Before a single getUpdates goes out: if the container this one is
    # replacing is still polling, two processes would be splitting this
    # bot's updates between them and getting 409 Conflict for their trouble.
    await lifecycle.on_start(BOT_NAME)
    await lifecycle.optional("command menu", publish_commands(application, BOT_COMMANDS, ADMIN_COMMANDS, ADMIN_IDS,
                                                               group=group_mode.GROUP_COMMANDS))
    await lifecycle.optional("profile text", publish_profile(application))


async def _post_stop(application):
    # Order matters: lifecycle writes the in-progress state out, and
    # flush_on_shutdown closes the connection pool it writes through.
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
    # Before the handlers, because ConversationHandler(persistent=...) below
    # asks lifecycle whether persistence actually came up.
    lifecycle.install(app, BOT_NAME)
    app.add_error_handler(error_handler)
    attach_problem_reports(app)
    # ---- the handlers that run before everything else ----
    # ONE GROUP EACH, and that is the whole point. python-telegram-bot runs
    # at most ONE handler per group: the first whose filter matches wins and
    # the rest of that group is skipped. track_activity is a TypeHandler on
    # Update, so it matches every update there is -- which meant that for as
    # long as these shared a group, it won every time and nothing else here
    # ever ran. Separate groups, in the order they have to happen in.
    app.add_handler(TypeHandler(Update, track_activity), group=-4)
    # Counted first, then throttled: somebody who floods is still somebody
    # who was here, and /status is supposed to say so.
    attach_flood_gate(app, ADMIN_IDS)
    # Runs after track_activity but before every other handler (ConversationHandler
    # included) -- a no-op unless /feedback or a "Custom" donate button has
    # just asked for something to be typed, in which case it takes the answer
    # and stops it from also being treated as, e.g., a pack title (see
    # awaited_text_received's docstring).
    # Group chats first in the same handler group: python-telegram-bot runs the first
    # handler that matches, so a group message reaches group_mode's router and never the
    # private-chat prompt-taker below it (see group_mode.group_router).
    app.add_handler(MessageHandler(filters.ChatType.GROUPS, group_mode.group_router), group=-1)
    app.add_handler(MessageHandler((filters.TEXT | filters.REPLY) & ~filters.COMMAND,
                                   awaited_text_received), group=-1)
    app.add_handler(ChatMemberHandler(group_mode.bot_membership_changed, ChatMemberHandler.MY_CHAT_MEMBER))
    # Ahead of the conversation, so a reply with emoji retags the sticker it replies to in
    # any state, and the conversation never takes it for "retag the last one".
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & _EmojiReply(), emoji_reply))

    static_media = filters.PHOTO | filters.Document.IMAGE | filters.Sticker.STATIC
    video_media = filters.ANIMATION | filters.VIDEO | filters.Document.VIDEO | filters.Sticker.VIDEO
    # While a rename or an invitation waits for typing, a picture still goes into the pack
    # open behind it (_behind_question).
    behind_question = [MessageHandler(static_media, _behind_question(receive_media)),
                       MessageHandler(video_media, _behind_question(receive_video_media))]
    conv = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            CommandHandler("newpack", newpack_start),
            CallbackQueryHandler(newpack_start, pattern="^menu_newpack$"),
            CallbackQueryHandler(pack_chosen, pattern="^pack:"),
            CallbackQueryHandler(rename_start, pattern="^packrename:"),
            CallbackQueryHandler(invite_start, pattern="^co:inv:"),
            # The three language switches are entry points purely so that the
            # END they return is honoured. A handler registered only outside
            # the conversation has its return value discarded, which is how a
            # language change used to leave the state machine sitting in
            # TITLE/EDITING behind a start menu.
            CallbackQueryHandler(language_chosen, pattern="^setlang:"),
            # Same reasoning: /cancel's buttons have to be able to close the
            # session the /cancel that raised them was sent from.
            CallbackQueryHandler(cancel_choice_callback, pattern="^cancelpick:"),
            # And the question a sticker already in the pack gets: deleting the pack's last
            # sticker there closes the session (already_in_pack_chosen).
            CallbackQueryHandler(already_in_pack_chosen, pattern="^" + group_mode.DUP_PREFIX),
            CommandHandler("en", set_language_en),
            CommandHandler("uz", set_language_uz),
            CommandHandler("rus", set_language_rus),
            # And /deletemydata, for the same reason and one more. Its END has
            # to be honoured, or an erase leaves the state machine in EDITING
            # holding a pack this bot has just been told to forget -- the next
            # sticker then fails on state that is gone. The command itself
            # returns None and moves nothing; only the confirmation ends it.
            CommandHandler("deletemydata", delete_my_data_command),
            CallbackQueryHandler(delete_my_data_chosen, pattern="^" + ERASE_PREFIX),
        ],
        states={
            TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_title)],
            RENAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_new_title), *behind_question],
            INVITE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_invites), *behind_question],
            EDITING: [
                CallbackQueryHandler(confirm_remove_last_sticker, pattern="^confirmremove:"),
                MessageHandler(
                    filters.Document.FileExtension("zip") | filters.Document.FileExtension("wastickers"),
                    receive_whatsapp_import,
                ),
                MessageHandler(static_media, receive_media),
                MessageHandler(video_media, receive_video_media),
                MessageHandler(
                    filters.Sticker.ANIMATED,
                    reject_unsupported_sticker,
                ),
                CommandHandler("import", import_command),
                CommandHandler("done", done),
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    maybe_emoji_override,
                ),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel_command), CommandHandler("start", start)],
        # Named and persisted, so someone who was three stickers into a pack
        # when the bot was redeployed sends the fourth and it just works.
        name="stickerbot_pack_editing",
        persistent=lifecycle.persistent(),
        # Without this, entry points are ignored for as long as a conversation
        # is open, and every one of them is a dead end mid-session: /newpack
        # fell through to unknown_command ("I don't recognize that command")
        # and the New pack / Add stickers / Rename buttons did nothing at all
        # when tapped, with no feedback to say why. Re-entry restarts the flow
        # the user actually asked for, which is what tapping any of them means.
        allow_reentry=True,
    )

    app.add_handler(conv)
    # ---- language: /language for the picker, /en, /uz, /rus to switch
    # directly -- offered at the first /start, available anytime after.
    # Standalone (not part of the ConversationHandler above) so they still
    # work mid pack-editing session -- conv's per-state handlers don't match
    # a bare "/en" etc, so the update falls through to these. ----
    app.add_handler(CallbackQueryHandler(language_chosen, pattern="^setlang:"))
    app.add_handler(CallbackQueryHandler(cancel_choice_callback, pattern="^cancelpick:"))
    app.add_handler(CommandHandler("language", language_command))
    app.add_handler(CommandHandler("en", set_language_en))
    app.add_handler(CommandHandler("uz", set_language_uz))
    app.add_handler(CommandHandler("rus", set_language_rus))
    # ---- what the bot keeps, what it may be used for, and the erase button.
    # Standalone for the same reason the language switches are: somebody who
    # wants to know what is held on them, or wants it gone, should not have to
    # finish whatever they were doing first. ----
    app.add_handler(CommandHandler("privacy", privacy_command))
    app.add_handler(CommandHandler("terms", terms_command))
    app.add_handler(CommandHandler("paysupport", paysupport_command))
    app.add_handler(CallbackQueryHandler(problem_report_callback, pattern=r"^rpt"))
    app.add_handler(CommandHandler("deletemydata", delete_my_data_command))
    app.add_handler(CallbackQueryHandler(delete_my_data_chosen, pattern="^" + ERASE_PREFIX))
    app.add_handler(CommandHandler("mypacks", mypacks_command))
    app.add_handler(CommandHandler("addsticker", addsticker_command))
    app.add_handler(CommandHandler("help", help_command))
    # Outside the conversation, like /help, so they work in the middle of a pack.
    app.add_handler(CommandHandler("fullhelp", full_help_command(build_help_text, help_rows, HELP_PAGES, HELP_LABELS)))
    app.add_handler(CallbackQueryHandler(help_toggle(build_help_text, help_rows, HELP_PAGES, HELP_LABELS),
                                         pattern=HELP_TOGGLE_PATTERN))
    # A page tap sends a new message and edits nothing, so it is fine here, outside
    # the conversation: it answers in the middle of a pack and does not end it.
    app.add_handler(CallbackQueryHandler(help_page_button(HELP_PAGES), pattern=HELP_PAGE_PATTERN))
    app.add_handler(CommandHandler("feedback", feedback_command))
    app.add_handler(CommandHandler("whomade", whomade_command))
    app.add_handler(CommandHandler("emoji", emoji_command))
    app.add_handler(CommandHandler("whois", whois_command))  # owner-only
    app.add_handler(CommandHandler("messageas", messageas_command))  # owner-only
    app.add_handler(CommandHandler("dbdump", dbdump_command))  # owner-only
    app.add_handler(CommandHandler("status", status_command))  # owner-only
    app.add_handler(CommandHandler("crashtest", crashtest_command))  # owner-only, for testing /status
    # Both only fire outside an active editing session -- inside one, the
    # conversation's own EDITING handlers claim them first.
    app.add_handler(CommandHandler("import", import_standalone))
    app.add_handler(CommandHandler("done", done_standalone))
    app.add_handler(CommandHandler("cancel", cancel_command))  # standalone fallback
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
    app.add_handler(CommandHandler("convert", convert_redirect_command))  # points to @ConvertBot now

    # ---- donations (Telegram Stars) -- this bot's only Stars usage, so these
    # register directly with no payload-prefix branching needed ----
    app.add_handler(CommandHandler("balance", balance_command))
    app.add_handler(CommandHandler("donate", donate_command))
    app.add_handler(CallbackQueryHandler(donate_amount_chosen, pattern="^donate:"))
    app.add_handler(CallbackQueryHandler(donate_fiat_amount_chosen, pattern="^donatefiat:"))
    app.add_handler(CallbackQueryHandler(donate_custom_button_chosen, pattern="^donatecustom:"))
    app.add_handler(PreCheckoutQueryHandler(donation_precheckout_callback))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, donation_payment_callback))

    # Both registered last, so every real handler (and the conversation's
    # own in-state handlers) above gets first shot.
    app.add_handler(MessageHandler(filters.COMMAND, unknown_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, unrecognized_message))

    # ManagerBot's link: heartbeats, crash/donation events, and the queue it
    # uses to run this bot's owner-only commands remotely. Never raises --
    # with no shared database reachable the bot just runs on its own.
    family_link.attach(app, BOT_NAME, "StickerBot", START_TIME)
    attach_maintenance(app)

    logger.info("Bot starting (polling)...")
    # A 30-second long poll instead of the default 10 is the same latency --
    # Telegram answers the moment an update exists -- for a third of the HTTP
    # requests, which on an idle bot is most of what it does all day. The
    # allowed_updates list is every kind this bot has a handler for; anything
    # else (edited messages, channel posts, reactions, other people's
    # membership changes) Telegram now stops sending at all rather than this
    # process downloading and parsing it only to drop it. Its own membership
    # (MY_CHAT_MEMBER) is in since 2.2.0: being added to a group or removed.
    # polling_kwargs() is what lets this process hear SIGTERM before
    # python-telegram-bot shuts it down, so a redeploy can tell anyone
    # mid-conversion what happened instead of leaving them waiting.
    # drop_pending_updates stays off (its default): updates sent while the
    # container was being replaced are still on Telegram's side, and this is
    # the poll that collects them.
    app.run_polling(**lifecycle.polling_kwargs(
        timeout=POLL_TIMEOUT,
        allowed_updates=[Update.MESSAGE, Update.CALLBACK_QUERY, Update.PRE_CHECKOUT_QUERY,
                         Update.MY_CHAT_MEMBER],
    ))


if __name__ == "__main__":
    main()
