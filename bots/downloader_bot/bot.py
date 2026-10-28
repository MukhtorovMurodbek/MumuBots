"""
Media downloader bot -- 4th bot in the family (see ARCHITECTURE.md).

Commands:
  /start, /help   - greeting + how it works
  /caption on|off - toggle the "via @thisbot" credit caption
  /lossless on|off- send downloads as uncompressed files
  /donate         - support hosting costs (voluntary)
  /en, /uz, /rus  - switch language (English/Uzbek/Russian); also asked
                    once, trilingually, on first /start
  /providers      - owner-only: which download route is currently working

Paste a link at any time (no command needed). Instagram, TikTok, Twitter/X,
Pinterest and Reddit all go through resolvers.py, which tries several
independent ways of getting the media and remembers which ones work -- read
that file's docstring first, because the reason it exists (a datacenter IP
gets a login page where a phone gets the post) is the single most important
thing to know about this bot.

Reddit and Twitter/X keep one thing resolvers.py does not do: a post with no
media in it is rendered as a clean card image (cards.py) instead of a wall of
text, which is the actual point of "downloading" one of those.

YouTube is deliberately not mentioned in /help, /start, or the bot's command
menu (BOT_COMMANDS below) -- it is handled if pasted but never advertised. It
is also the one platform with no route but yt-dlp, so it is the one most
likely to be refusing this server on any given day; advertising it would be
promising something the bot cannot keep. Don't "helpfully" add it to the help
text later without checking with the repo owner first.

Requires: python-telegram-bot[job-queue]>=21.3, yt-dlp>=2024.1, httpx,
          Pillow, python-dotenv>=1.0
Env vars: DBOT_TOKEN, DBOT_USERNAME (no @), DBOT_ADMIN_ID (optional,
          comma-separated, gates /messageas, /dbdump, /status, /providers),
          DATABASE_URL and DB_SCHEMA (the shared family database, and this
          bot's schema in it), SIBLING_BOTS. Download limits, concurrency and
          the per-site cookie/proxy settings are documented in .env.example.
"""
import asyncio
import contextvars
import hashlib
import logging
import os
import shutil
import uuid
import time
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timedelta, timezone
from io import BytesIO

# main.py gives each bot its settings; this only reads a .env if one is present.
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from telegram import (
    BotCommand, InputMediaDocument, InputMediaPhoto, InputMediaVideo,
    LinkPreviewOptions, Update, InlineKeyboardButton, InlineKeyboardMarkup,
)
from telegram.error import BadRequest

# For any message whose text is an error. yt-dlp's failures quote their own
# documentation URLs, and Telegram turns the first link in a message into a
# preview card -- so a one-line "couldn't download that" arrived as a
# full-width GitHub repository with a logo, which looks far more like the bot
# breaking than like the site saying no.
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
)
from shared_features import (
    note_active,
    note_job,
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
# Without the @, which is also taken off if it was written with one -- the
# one-process edition always allowed it, and a link built from "@name" is broken.
BOT_USERNAME = (os.environ.get("DBOT_USERNAME") or "").strip().lstrip("@") or None


BOT_NAME = "downloaderbot"  # this bot's id within SIBLING_BOTS

# The key the stored file ids are filed under comes from the token (sent_files.py).
sent_files.configure(BOT_TOKEN)

# Owner-only admin tools (/messageas, /dbdump, /status) -- comma-separated
# Telegram user ids. Empty/unset means disabled for everyone.
ADMIN_IDS = {int(x) for x in os.environ.get("DBOT_ADMIN_ID", "").split(",") if x.strip()}


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


# Telegram's long-poll window -- see the note in main().
POLL_TIMEOUT = int(os.environ.get("POLL_TIMEOUT", "30"))

# ---------------------------------------------------------------------------
# How many downloads may be in flight at once
# ---------------------------------------------------------------------------
# This is the bot in the family with real resource spikes: yt-dlp holding a
# video, ffmpeg muxing it, and the finished file being uploaded back, all at
# once. There was no limit at all, so ten people pasting links in the same
# minute meant ten simultaneous downloads -- and on a container sized for
# one, that is the OOM kill that ARCHITECTURE.md gives as the reason this bot
# runs in a container of its own.
#
# Three at a time -- two at most for any one person, see MAX_PER_USER below --
# keeps the box responsive and costs a busy user a few seconds of queueing, which the "Downloading..." message already accounts for. The
# semaphore is created lazily because it must belong to the running loop.
#
# Three is for a small container. On a larger one (2.2.0) it follows the machine: about
# 400 MB of the bot's share of the memory for each (yt-dlp holding a video, ffmpeg muxing
# it), and two for every vCPU, from three up to eight. DBOT_MAX_CONCURRENT_DOWNLOADS says
# otherwise; with the memory unknown (a laptop) it is three.


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
    """Said once, when the bot starts."""
    job_memory = resources.job_memory_mb("downloader_bot")
    logger.info("DownloaderBot runs up to %d downloads at once and %d album PDF(s) (%s MB for its work).",
                MAX_CONCURRENT_DOWNLOADS, merge.pdfs_at_once(), "unknown" if job_memory is None else job_memory)


_download_slots: asyncio.Semaphore | None = None


def _slots() -> asyncio.Semaphore:
    global _download_slots
    if _download_slots is None:
        _download_slots = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)
    return _download_slots


# The "is it still public?" question for a stored file id (resolvers.lookup, in
# _resend_by_file_id) downloads nothing and takes no download slot, but it asks
# the same routes the downloads use: a few at a time, so that many old posts sent
# at once cannot run those routes into their rate limits.
MAX_CONCURRENT_LOOKUPS = 2

_lookup_slots: asyncio.Semaphore | None = None


def _lookups() -> asyncio.Semaphore:
    global _lookup_slots
    if _lookup_slots is None:
        _lookup_slots = asyncio.Semaphore(MAX_CONCURRENT_LOOKUPS)
    return _lookup_slots


# ...and no more than MAX_PER_USER of them to any one person.
#
# This was one each, behind a Lock, and it was never the thing that made
# somebody wait. Every bot in the family has python-telegram-bot process
# updates one at a time, and handle_link ran the whole download before it
# returned -- so a second link was not even read until the first had been
# fetched, muxed and uploaded, and nor was anybody else's. The per-person
# lock only ever guarded a queue that could not form.
#
# Each link is now its own task (see _spawn_download), so the handler returns
# at once and the limits below are what actually decide. Three slots for the
# box, two for any one person: somebody pasting a batch gets two moving at
# once, and the third slot is always there for the next person to arrive.
MAX_PER_USER = max(1, int(os.environ.get("DBOT_MAX_PER_USER") or 2))

# Links one person may have waiting or in flight before a new one is refused.
# Past that it is a backlog rather than a queue, and each waiting link is a
# status message sitting in their chat saying nothing is happening.
MAX_PENDING_PER_USER = max(1, int(os.environ.get("DBOT_MAX_PENDING_PER_USER") or 5))

# The temporary storage limit is the product of two numbers already here:
# every download holds at most DBOT_MAX_DOWNLOAD_MB on disk (video.py's
# ceiling, 48 MB by default), and only MAX_CONCURRENT_DOWNLOADS hold one at a
# time -- a link waiting for a slot has written nothing. So the work directory
# never holds more than about 3 x 48 = 144 MB, however many links arrive.
# What that cannot know is whether the disk has that much left, so a
# download also refuses to start with less than MIN_FREE_MB free.
MIN_FREE_MB = int(os.environ.get("DBOT_MIN_FREE_MB") or 300)

LARGE_ROOM_MB = int(os.environ.get("DBOT_LARGE_ROOM_MB") or 1024)

# The last deliveries are kept a while (delivery_cache.py). They live in the
# work directory too, so they are kept only while the disk still has what the
# downloads themselves need. And they give way: a download that needs more than
# that (a large file, a merge) first deletes kept files, least recently used first
# (_make_room), so a kept delivery does not make a download refuse for space.
delivery_cache.configure(resolvers._WORK_DIR)


def _cache_floor_mb() -> int:
    return MIN_FREE_MB + MAX_CONCURRENT_DOWNLOADS * video.MAX_DOWNLOAD_MB


def _make_room(mb: int) -> None:
    """A download is about to take up to `mb` MB: kept files go first, until the
    disk has that and MIN_FREE_MB more, or none are left."""
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

# The same link again while the first is still going (handle_link): the links
# a person has queued or running, as (chat id, person id, platform, canonical
# link) -> when it was marked (time.monotonic). The copy gets a 👌 and no job:
# no allowance check, no record, no status message. A mark is made when the
# link is accepted and cleared when its job ends (_spawn_download's finally) or
# when the link is refused before a job starts, so the dict never holds more
# than the jobs in flight. IN_FLIGHT_MAX_SECONDS is the belt and braces: a mark
# older than that is ignored and overwritten, in case one was somehow never
# cleared, so that a link can never be stuck for good.
_in_flight: "dict[tuple[int, int, str, str], float]" = {}
IN_FLIGHT_MAX_SECONDS = 3600


def _already_in_flight(key) -> bool:
    marked = _in_flight.get(key)
    return marked is not None and time.monotonic() - marked < IN_FLIGHT_MAX_SECONDS


def _clear_in_flight(key, stamp) -> None:
    """Take a link's mark down, only if it is still the one this job made: a job
    that ran past IN_FLIGHT_MAX_SECONDS may find its mark replaced by a newer copy
    of the link, and that mark is the newer job's to clear."""
    if _in_flight.get(key) == stamp:
        _in_flight.pop(key, None)


async def _answer_duplicate(update: Update) -> None:
    """The one answer to a link that is already being fetched: a 👌 on the copy.
    A reaction rather than a line of text because it cannot spam back during a
    paste storm and needs no translation (the first link's own status message is
    already on screen). Switching to text later is a change to this function
    only. Never raises: a reaction that cannot be set (an old message, a chat
    that does not allow it) is nothing the person needs to hear about."""
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
        # Dropped as soon as it is idle: one dict entry per person who has
        # ever pasted a link is a slow leak on a process that runs for months.
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
    """Run one link's whole download in a task of its own.

    `work` is a coroutine function. The update travels with the task so that
    anything escaping it reaches the error handler attributed to the person
    it happened to -- which, since the handler stopped filing network errors
    with an update as blips, means it is reported rather than swallowed.

    `in_flight` is the mark handle_link made for this link (see _in_flight),
    cleared in the same finally that lowers the pending count: whatever the
    job did, the next copy of the link is a job of its own. The redo and merge
    buttons pass none."""
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
    """The short version unless asked for the long one -- the family's help
    template (shared_features, "/help in two lengths"). Both end with the
    sibling-bots pointer; the per-command pages (HELP_PAGES below) are the
    buttons under the help, not a line in it."""
    if not full:
        text = i18n.t(lang, "help_brief").rstrip("\n")
    else:
        text = i18n.t(lang, "help_text", username=BOT_USERNAME or "this_bot").rstrip("\n")
    blurb = sibling_bots_blurb(BOT_NAME, lang)
    return text + "\n\n" + blurb if blurb else text


# ---------- /help <name> and the buttons under /help: a page for each part with options ----------
# The mechanism is in shared_features ("A page for each command"). Every
# number on a page is read from the module that owns it when the page is shown
# -- by attribute, never copied -- so a page says what the code does now.
# "download" is not a command: it is the page for sending a link.

def _help_large_prices(lang: str, key: str) -> str:
    return i18n.t(lang, key, free_mb=large_files.FREE_MAX_MB, max=large_files.LARGE_MAX_MB,
                  first=large_files.first_price(), top=large_files.top_price())


def _help_page_download(lang: str) -> str:
    # Looked at when the page is shown: large_files.CONFIGURED can turn off at runtime.
    if db.DOWNLOADS_PER_DAY > 0 and db.DOWNLOADS_PER_HOUR > 0:
        # The same arithmetic as db.download_allowance.
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
# "download" is not a command, so its button says what the page is about
# rather than "/download"; "settings" is one, and keeps its name.
HELP_LABELS = {"download": "help_btn_download"}


# Public command menu (the "/" button in Telegram's chat UI) -- set on
# startup via set_my_commands() below instead of pasting into @BotFather by
# hand. The owner-only /dbdump is deliberately left off. Language-switch
# commands are described in the language they switch to (self-explanatory
# by script/language, since Telegram's command menu itself isn't per-user).
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
    #
    # /caption and /lossless are gone from here and still work. Both are one
    # switch each, and "lossless" is a word for people who already know what
    # it means; /settings says what it is in a word everybody knows and shows
    # both switches with their current state on them.
    BotCommand("start", "What I do, and how to start"),
    BotCommand("language", "Choose your language / Tilni tanlash / Выбрать язык"),
    BotCommand("settings", "Captions and file quality"),
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
    BotCommand("providers", "🔒 Which download route works, which is resting"),
    BotCommand("messageas", "🔒 messageas <user_id> <text> — DM as this bot"),
    BotCommand("dbdump", "🔒 This bot's tables as a zip of CSVs"),
]


async def _reply(update: Update, text: str, **kwargs):
    """Works whether the update came from a command or a button tap. A tap
    evolves the tapped message in place -- so choosing a language turns the
    picker itself into the instructions rather than leaving a stale picker
    sitting above them -- while a command has no earlier bot message to reuse
    and so always sends fresh.

    "In place" only holds while that message is still the last thing in the
    chat; once the user has said anything since, the answer is sent fresh
    instead of being written somewhere they have scrolled past. See
    live_message.py."""
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
    """
    lang = await asyncio.to_thread(get_user_language, update.effective_user.id)
    if lang is None:
        await update.message.reply_text(i18n.LANGUAGE_PROMPT, reply_markup=language_keyboard())
        return
    context.user_data["lang"] = lang
    await _continue_start(update, context, lang)


async def language_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/language -- the picker on demand, which is what /start used to be.

    Same trilingual greeting and same three buttons, with a tick on the
    language in force so a returning user can see which one they are on
    before deciding to change it. The tap that follows runs through
    _apply_language exactly as /en, /uz and /rus do, so it ends where they
    end: at the instructions, in the language just chosen.
    """
    lang = await asyncio.to_thread(get_user_language, update.effective_user.id)
    # Keep the cached language warm even though the picker itself is
    # trilingual -- the next handler this user hits would otherwise pay for
    # a database read that /language had already done.
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
    """Everything this bot could be waiting on -- which is only ever the
    shared donation prompt. Spelled out anyway, and with the same signature
    the other three bots use, so that the day this bot grows a state of its
    own there is somewhere obvious for it to go."""
    return cancel_items(context, lang)


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """This bot asks the fewest questions in the family -- a link needs no
    follow-up, and /caption and /lossless answer themselves with buttons --
    so the only thing /cancel usually has to catch is a donation amount it
    asked for. It exists anyway, because "does /cancel work here?" should not
    depend on which of the five bots you happen to be typing into, and
    because a forced reply left over from /donate is exactly the kind of
    thing you need a way out of. Downloads already in flight are not
    affected: those are the bot working, not the bot waiting on you.

    With one thing pending the question below is a formality here, and it is
    asked anyway: /cancel means the same thing in all four bots, and "it
    confirms except in DownloaderBot, where it just does it" is precisely
    the kind of per-bot exception this family keeps trying to stop growing.
    """
    lang = await i18n.get_lang(update.effective_user.id, context)
    if await ask_cancel_choice(update, context, await _cancel_items(update, context, lang), lang):
        return
    await finish_cancel(update, context, lang, [])


async def cancel_choice_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """A tap on one of /cancel's buttons."""
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
    """The single path every language change goes through -- /en, /uz, /rus
    and a tap on the picker alike -- so all four end the same way: with the
    instructions, printed in the language just chosen."""
    await asyncio.to_thread(set_user_language, update.effective_user.id, lang)
    context.user_data["lang"] = lang
    # The menu follows the choice too -- Telegram otherwise shows it in the
    # language of the phone.
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
    """A tap on the picker -- same effect as /en, /uz or /rus."""
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
    # /caption on|off still works directly for muscle memory, but bare
    # /caption now offers buttons instead of just telling you the syntax.
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
    """/lossless [on|off] -- send downloads as files instead of as media.

    Telegram re-encodes anything a bot sends as a photo or a video: that
    compression is what makes it play or preview inline in the chat, and
    there is no way to ask for one without the other. Sent as a *document*
    the bytes arrive exactly as they left the source -- the original
    resolution, bitrate and container -- at the cost of a tap to open, and
    no preview in the chat list.

    So this is off by default and per-user, the same shape as /caption:
    almost everyone wants the version that plays, and the people who do not
    want it badly enough to say so. The bot's upload ceiling is unchanged
    either way (20 MB, or 50 MB behind a local Bot API server), and a file
    too big is too big in either form.
    """
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
    """The large-files switch, from /settings, or from the button under a
    refusal for a file over the free ceiling (`large:offer`). Off by default:
    these cost credit, and credit is never spent without a yes."""
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
    """Both switches this bot has, on one screen, with their state on them.

    /caption and /lossless still work and still do exactly this one at a
    time. What they were not was findable: a menu entry saying "lossless" is
    a word for somebody who already knows what it means, and the two of them
    were 2 of the 15 lines in a menu whose other 13 are about the bot or the
    law. One entry called settings is what a person looks for when they want
    to change something.
    """
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
    # The third switch only where it can do anything: with the MTProto route
    # set up (large_files.py).
    if large_files.CONFIGURED:
        large_on = await asyncio.to_thread(get_large_files_enabled, update.effective_user.id)
        text += "\n\n" + _large_status(lang, large_on)
        rows += _large_keyboard(lang, large_on).inline_keyboard
    keyboard = InlineKeyboardMarkup(rows)
    await update.message.reply_text(text, reply_markup=keyboard)


async def dbdump_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/dbdump -- exports every table in this bot's own database as one
    zip of CSVs. Owner-only."""
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


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/status -- uptime, hosting environment, any crashes since this
    process started, and active-user counts. Owner-only, same reasoning as
    /dbdump: this is operational info, not something every user should see."""
    if not _is_admin(update.effective_user.id):
        return await _deny(update, context)
    now = datetime.now(timezone.utc)
    users_hour = await asyncio.to_thread(count_active_users_since, now - timedelta(hours=1))
    users_since_start = await asyncio.to_thread(count_active_users_since, START_TIME)
    await update.message.reply_text(
        build_status_text(START_TIME, users_hour, users_since_start) + "\n" + _cache_status_line()
        + "\n" + _sent_files_status_line())


def _cache_status_line() -> str:
    """One line for the owner: counts only, never whose or what."""
    stats = delivery_cache.stats()
    if not stats["enabled"]:
        return "Delivery cache: off"
    return (f"Delivery cache: {stats['entries']} kept ({stats['bytes'] / (1024 * 1024):.1f} MB), "
            f"{stats['hits']} hits / {stats['misses']} misses since start")


def _sent_files_status_line() -> str:
    """The file-id store (sent_files.py), for the owner: counts since the bot started,
    never whose or what."""
    stats = sent_files.stats()
    if not stats["enabled"]:
        return "Sent-file store: off"
    return (f"Sent-file store: rows read {stats['hit']} fresh / {stats['stale']} stale / {stats['miss']} none; "
            f"lookups {stats['present']} present / {stats['missing']} missing / {stats['unknown']} unknown; "
            f"{stats['invalid']} invalid ids, {stats['stored']} stored since start")


# How often the in-memory provider scores are written to Postgres. A minute
# is short enough that a crash loses nothing that matters and long enough that
# a busy hour is sixty writes rather than a write per download. See
# resolvers.take_dirty().
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
        # Scores are advice, not user data. If the database is unreachable the
        # bot carries on choosing providers from memory rather than failing a
        # download over a bookkeeping write.
        logger.warning("couldn't save provider health", exc_info=True)


# How long one route gets during a bus probe. Shorter than a real download's
# PROVIDER_TIMEOUT, because ManagerBot calls a family command failed after 90
# seconds and a probe nobody can run from their phone is not worth having.
PROBE_TIMEOUT_S = float(os.environ.get("DBOT_PROBE_TIMEOUT", "12"))


async def _bus_probe(context, args):
    """`probe [url|platform ...]` over the family bus.

    THE POINT OF THIS COMMAND. Every route in resolvers.py was verified from
    the developer's laptop, on a residential connection -- and the entire
    reason those routes exist is that a residential address and a datacenter
    address get different answers from the same site. So a laptop test proves
    the code and proves nothing about production. This runs the same checks
    from inside the container that actually serves users, which is the only
    place the answer means anything.

    It asks EVERY route rather than stopping at the first that works, and it
    deliberately leaves the health scores alone: a probe is a question, not
    traffic, and an operator's curiosity should not reorder the chain real
    users get.

    Defaults to the sample links in resolvers.SAMPLES. Name platforms
    ("probe instagram tiktok") to narrow it, or paste real links to probe
    those instead -- one per platform, since the later one wins.
    """
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
    """`routewatch` over the family bus: the daily check, now.

    Where `probe` asks every route of every platform and leaves the scores
    alone, this is the same thing routewatch.py does by itself once a day --
    first working route per platform, the state it puts each platform in, the
    owner told if that changed, and yt-dlp compared with its newest release.
    Asking is not neutral: it updates what the daily check remembers, so a
    problem found now will not be announced again tonight.

    The whole check can outlast the 90 seconds ManagerBot gives a command
    (yt-dlp alone is allowed 90), so past routewatch.REPLY_WITHIN_S this
    answers with the platforms finished so far and goes on in the background."""
    return await routewatch.run(force=True, reply_within=routewatch.REPLY_WITHIN_S), None, None


async def providers_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/providers -- which download route is working and which is not.

    Owner-only, same reasoning as /dbdump and /status: operational detail, and
    left in English like the rest of the admin output. This is the command
    that answers "Instagram broke again, what happened" without reading logs.
    """
    if not _is_admin(update.effective_user.id):
        return await _deny(update, context)
    await update.message.reply_text(_providers_text(), **NO_PREVIEW)


async def _bus_providers(context, args):
    """The same report, asked for from ManagerBot (`/providers downloader`).

    Every owner-only command in this family should be reachable from
    ManagerBot, and this one was not -- which mattered more than most, because
    the question it answers ("which route is broken right now") is one you
    ask when something is already wrong and you are not sitting in this bot's
    chat.

    It reads the in-process health table, so it answers for the container
    that is actually serving downloads. `probe` beside it is the active
    version of the same question: this says what has been happening, probe
    goes and finds out.
    """
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


# ---------------------------------------------------------------------------
# "the other way round", on a button
# ---------------------------------------------------------------------------
# Getting the same download in the other quality meant three steps: /lossless,
# pick the other setting, then find the link and send it again. All the user
# was ever saying was "that one, but as a file" -- and the /lossless setting
# is a *preference*, so changing it to get one file the other way leaves it
# changed for every file after.
#
# So the button does the one delivery and touches nothing else. Nothing about
# the backend changes: it is the same resolve, the same download, the same
# send, and it counts against the same daily allowance -- because it is a
# second download, exactly as re-pasting the link would have been.
#
# The URL cannot ride in the callback data (64 bytes, and these are share
# links with tracking parameters), so it is held in user_data under a short
# token. Bounded, because a tab left open for a week should not be a leak:
# the last few downloads keep their button and the older ones answer "that
# one has expired, send the link again", which is one step worse than the
# button and no worse than before it existed.
REDELIVER_PREFIX = "redo:"
REDELIVER_MEMORY = 5
# "merge:pdf:<token>" and "merge:zip:<token>": 20 bytes (10 + a 10-character token), against Telegram's 64.
MERGE_PREFIX = "merge:"


# ---------------------------------------------------------------------------
# A chosen quality travels inside the link
# ---------------------------------------------------------------------------
# The quality picker's choice is part of what was asked for: the 720p and the 1080p of one
# video are two deliveries, and the delivery cache, the file-id store and the redo button all
# key on the link. So the height rides on the link -- "#q720" for the one height the person
# picked, "#upto480" for a group's cap -- and comes off only where the link is fetched
# (_split_height). A capped copy is then never sent to somebody who asked for the best, nor
# the other way round.
_EXACT_MARK, _CAP_MARK = "#q", "#upto"


def _with_height(url: str, height: int, exact: bool = True) -> str:
    return f"{url}{_EXACT_MARK if exact else _CAP_MARK}{int(height)}"


def _split_height(url: str) -> "tuple[str, int | None, bool]":
    """("<link>", 720, True) from "<link>#q720", ("<link>", 480, False) from
    "<link>#upto480", and (url, None, False) for a link with neither."""
    for mark, exact in ((_CAP_MARK, False), (_EXACT_MARK, True)):
        base, found, height = url.rpartition(mark)
        if found and height.isdigit():
            return base, int(height), exact
    return url, None, False


# The settings of the group chat a download is for (groups.py), or None in a private chat.
# group_router sets it around handle_link, and the download task handle_link starts copies it
# (asyncio tasks take the context they were created in), so the whole path -- the quality, video
# or file, the credit line, no buttons -- reads it here instead of taking it as one more argument
# through every function.
_GROUP: contextvars.ContextVar = contextvars.ContextVar("group_settings", default=None)


def _remember_delivery(context, platform: str, url: str, lossless: bool) -> "str | None":
    if _GROUP.get() is not None:
        # A group gets no buttons (_send_media), so there is nothing for them to read back,
        # and the link a member posted there is not kept with them for it.
        return None
    memory = context.user_data.setdefault("redeliver", {})
    while len(memory) >= REDELIVER_MEMORY:
        memory.pop(next(iter(memory)))
    token = uuid.uuid4().hex[:10]
    # "at" is when it was delivered, to count how long people take to tap the
    # button (REDO_DELAY_BUCKETS); a number, so it survives being saved as JSON.
    memory[token] = {"platform": platform, "url": url, "lossless": lossless, "at": time.time()}
    return token


# How long after the delivery the redo button was tapped, as the counts name it:
# [from, to) in seconds. A delivery saved by an older version has no "at": unknown.
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
    """True the first time `action` ("redo", "pdf" or "zip") is taken from this
    delivery, False every time after. Per action, not per delivery: the PDF of
    an album and its ZIP are two different things to ask for, and each can be
    asked for once -- what must not happen is the same one twice, which fetches
    the album again against the allowance. The mark is made in the same breath
    as the look, with no await between them, so a double tap (two updates in
    flight at once) cannot get both through. A list, because user_data is saved
    as JSON."""
    spent = remembered.setdefault("spent", [])
    if not isinstance(spent, list):
        # 2.1.0 saved a bare True once its redo button was used; user_data
        # outlives an upgrade, so that delivery's redo is still spent.
        spent = remembered["spent"] = ["redo"]
    if action in spent:
        return False
    spent.append(action)
    return True


def _unspend_action(remembered: dict, action: str) -> None:
    """Give `action` back: the tap that spent it came to nothing (the person
    was out of downloads), so the button is theirs to tap again."""
    spent = remembered.get("spent", [])
    if isinstance(spent, list) and action in spent:
        spent.remove(action)


def _remembered_kb(lang: str, token: str, remembered: dict):
    """The keyboard of a delivered album as it stands now: the same buttons it
    was first sent with (the counts are kept in `remembered` by _send_media),
    less every action already taken. A tap takes away its own button and no
    other."""
    return _nudge_kb(lang, token, remembered.get("lossless", False),
                     photos=remembered.get("photos", 0), items=remembered.get("items", 0),
                     spent=remembered.get("spent") if isinstance(remembered.get("spent"), (list, tuple))
                     else ("redo",) if remembered.get("spent") else ())


def _nudge_kb(lang: str | None = None, token: str | None = None,
              lossless: bool = False, photos: int = 0,
              items: int = 0, spent=()) -> InlineKeyboardMarkup | None:
    # Point at ConvertBot too -- e.g. someone might want just the audio, or
    # a gif, out of what they just downloaded. No file handoff between bots
    # anymore (each bot is fully independent) -- they re-send it there.
    rows = []
    if lang is not None and token is not None:
        # The label says what tapping it *gets you*, not what mode you are in.
        # "Lossless: off" is a setting; "get it as a file" is a thing that
        # happens, and only one of the two reads as a button.
        # `spent`: the actions already taken from this delivery, whose buttons are gone.
        if "redo" not in spent:
            rows.append([InlineKeyboardButton(
                i18n.t(lang, "redeliver_as_compressed" if lossless else "redeliver_as_file"),
                callback_data=f"{REDELIVER_PREFIX}{token}")])
        # An album, in one file. PDF only where there are pages to make (two
        # photos; a post of one photograph and a video has nothing to bind),
        # ZIP wherever there is more than one thing to carry. Same token as the
        # button above -- merge_callback finds the link the same way.
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
    """Put the delivery's keyboard under the message as it now stands (see
    _remembered_kb): the buttons of the actions spent are gone, the rest stay."""
    try:
        await query.edit_message_reply_markup(reply_markup=_remembered_kb(lang, token, remembered))
    except Exception:
        logger.debug("Couldn't change the delivery's buttons", exc_info=True)


async def redeliver_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """The button under a finished download. Runs the ordinary path with the
    quality flipped for this one delivery, and leaves /lossless alone."""
    query = update.callback_query
    lang = await i18n.get_lang(update.effective_user.id, context)
    token = query.data[len(REDELIVER_PREFIX):]
    remembered = context.user_data.get("redeliver", {}).get(token)
    if not remembered or not _spend_action(remembered, "redo"):
        # Counts only (note_tap): an old button, or one already used.
        note_tap("redo.spent" if remembered else "redo.expired")
        await query.answer(i18n.t(lang, "redeliver_expired"), show_alert=True)
        return
    await query.answer()
    # Which way, and how long after the delivery: the numbers that say whether the
    # five-minute delivery cache is the right length (counts only).
    note_tap("redo.to_compressed" if remembered.get("lossless") else "redo.to_file")
    note_tap(_redo_delay_key(remembered))
    # This link is now the person's latest, whatever else they were looking at:
    # a kept delivery of any other link is dropped, one of this link keeps going
    # (delivery_cache.note_link). Before the allowance below, like handle_link.
    delivery_cache.note_link(update.effective_user.id, remembered["platform"], remembered["url"])
    # The button is spent (above, before any await: a double tap is two updates
    # in flight, and the markup below is only edited once Telegram answers):
    # tapping it twice would download the same file twice against the same
    # allowance, and the second tap is always a mis-tap. The markup is edited
    # rather than the text, because the message it is under is a photo or a
    # video and edit_message_text does not work on those. Only this button goes:
    # the album's PDF and ZIP buttons stay, they are other things to ask for.
    await _take_button_off(query, lang, token, remembered)

    allowance = await asyncio.to_thread(download_allowance, update.effective_user.id)
    if not allowance["allowed"]:
        # Nothing was started, so nothing is spent: the button comes back.
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


# ---------------------------------------------------------------------------
# an album as one file
# ---------------------------------------------------------------------------
# 📄 PDF and 🗜 ZIP, on the line that follows a delivered album. They are the
# redo button's sibling and work the same way: the token in the callback data
# finds the link in user_data, the tap counts as one download, and the post is
# resolved and fetched again. The delivery cache (delivery_cache.py) keeps the
# files of the last download for five minutes, but a merge does not use them yet
# (a follow-up): it fetches, builds, sends, and deletes what it touched. What is
# built is sent as one document, and everything it touched is deleted, whatever
# happened.

class _MergeStop(Exception):
    """The merge ends here, with this message (an i18n key) to the person."""

    def __init__(self, key: str):
        super().__init__(key)
        self.key = key


def _merge_text(lang: str, key: str) -> str:
    # Every merge message gets every number any of them might mention.
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
    """The 📄 PDF and 🗜 ZIP buttons under a delivered album. Mirrors
    redeliver_callback: answer, spend the buttons, count one download, and run
    the work in a task of its own."""
    query = update.callback_query
    lang = await i18n.get_lang(update.effective_user.id, context)
    kind, _, token = query.data[len(MERGE_PREFIX):].partition(":")
    # This action is spent here, by the first tap, before any await: its button
    # is only taken off further down, and until Telegram has done that a double
    # tap (or a second device) delivers a second tap of the same button. It
    # would fetch the album again and count a second download. The second tap
    # says what any spent button says. The other action on the same album (the
    # ZIP after the PDF) is a different thing to ask for and still works, and
    # its button stays.
    remembered = context.user_data.get("redeliver", {}).get(token) if kind in ("pdf", "zip") else None
    if not remembered or not _spend_action(remembered, kind):
        if not remembered:
            note_tap("merge.expired")
        await query.answer(i18n.t(lang, "redeliver_expired"), show_alert=True)
        return
    await query.answer()
    # Only the tapped button goes (a second tap of it would fetch the same album
    # a second time against the same allowance); the other merge button, the
    # redo button and the nudge row stay.
    await _take_button_off(query, lang, token, remembered)

    allowance = await asyncio.to_thread(download_allowance, update.effective_user.id)
    if not allowance["allowed"]:
        # Nothing was started, so nothing is spent: the button comes back.
        _unspend_action(remembered, kind)
        await _take_button_off(query, lang, token, remembered)
        await query.message.reply_text(_allowance_message(lang, allowance))
        return
    # Counted here, before anything is fetched, on purpose: it is a rate limit
    # and not money, so a merge that is then refused (merge_no_photos, say)
    # still counts, exactly as a download that fails does.
    await asyncio.to_thread(record_download, update.effective_user.id, remembered["platform"])

    async def work():
        await _merge_and_send(update, context, kind, remembered["platform"], remembered["url"])

    _spawn_download(context, update, work)


async def _merge_and_send(update: Update, context: ContextTypes.DEFAULT_TYPE,
                          kind: str, platform: str, url: str) -> bool:
    """Fetch the post again, build one PDF (its photos) or one ZIP (all of it),
    send it as a document, delete everything. True if it was sent.

    The same functions and the same limits as _resolve_and_send: the route
    chain, resolvers.download() with its per-file ceiling, the free-space check,
    and the same three kinds of slot. On top of those, the two limits of this
    feature -- MERGE_MAX_ITEMS files, MERGE_MAX_MB in all -- and neither is
    checked after the fact: a post with too many files is refused before the
    first is fetched, and the fetching stops at the file that takes the total
    past the MB limit."""
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
    # Under the work directory like every download, with a name nobody can
    # guess or collide with; the person is shown the proper one (merge.output_name).
    out_path = os.path.join(resolvers._WORK_DIR, f"{uuid.uuid4().hex}.{kind}")  # noqa: SLF001
    async with _user_slot(user_id), _slots(), lifecycle.busy(
            chat_id, i18n.t(lang, "restarting_send_again")):
        # The inputs and the output it builds: kept deliveries give way first.
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
                # One at a time across the whole process (merge.pdf_gate): a
                # decode is a few hundred MB. Whoever waits sees the same
                # "working" message as whoever builds.
                try:
                    await merge.build_pdf_queued(sources, out_path)
                except (merge.NothingToMerge, merge.NotAPhoto):
                    # build_pdf leaves out what is not a still photograph, as
                    # this leaves out a video; with fewer than two left it is
                    # the same ending as a post that had fewer to begin with.
                    raise _MergeStop("merge_no_photos")
            else:
                await asyncio.to_thread(merge.build_zip, sources, out_path)
            if os.path.getsize(out_path) > large_files.BOT_API_SEND_BYTES:
                # Possible for a PDF (a PNG that is smaller than the JPEG
                # it becomes); the Bot API would refuse it, so say so first.
                raise _MergeStop("merge_too_big")

            caption = None
            if await asyncio.to_thread(get_caption_enabled, user_id):
                caption = i18n.t(lang, "download_credit_caption", username=BOT_USERNAME)
            with open(out_path, "rb") as handle:
                # As a document, and with the content-type sniffing off, which
                # is what keeps Telegram from deciding a ZIP of videos is a
                # video (see _send_media).
                await message.reply_document(
                    handle, filename=merge.output_name(platform, kind),
                    caption=caption, read_timeout=120, write_timeout=120,
                    disable_content_type_detection=True,
                )
            await status.delete(context.bot)
            note_job(True)
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
        return  # a donation nudge is for the person, never for a whole group
    nudge = await maybe_donation_nudge(
        update.effective_user.id, lang, context, update.effective_chat.id)
    if nudge:
        await update.effective_message.reply_text(nudge)


def _image_extension(data: bytes) -> str:
    """Name a file after what it actually is.

    Telegram shows a document by its filename, and there is nothing in a
    download's bytes to say what the source called it -- so a .jpg that is
    really a .webp is exactly the sort of small lie that has people
    believing the bot mangled their picture. Four magic numbers cover
    everything these platforms serve.
    """
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
    """An image, with the same "the other way round" button the media path
    puts under a video. Reddit and Twitter reach the chat through here rather
    than through _send_media, and a button that appeared under a downloaded
    video but not under a downloaded picture would read as a bug."""
    group = _GROUP.get()
    if lossless is None:
        lossless = (group["as_file"] if group is not None
                    else await asyncio.to_thread(get_lossless_enabled, update.effective_user.id))
    token = _remember_delivery(context, platform, url, lossless)
    sent = await _reply_image(update, data, stem,
                              reply_markup=None if group is not None else _nudge_kb(lang, token, lossless),
                              lossless=lossless)
    filename = f"{stem}.{_image_extension(data)}"
    await asyncio.to_thread(delivery_cache.keep_bytes, update.effective_user.id, platform, url,
                            resolvers.MediaItem("photo", filename=filename), data, filename,
                            _cache_floor_mb())
    return sent


async def _reply_image(update: Update, data: bytes, stem: str, reply_markup=None,
                       lossless: bool | None = None):
    """One image back, compressed as a photo or verbatim as a file -- see
    lossless_toggle for which and why.

    `lossless=None` means "whatever this person's setting says", which is
    every ordinary call. The button passes the opposite of what was just
    delivered, for that one delivery only."""
    if lossless is None:
        lossless = await asyncio.to_thread(get_lossless_enabled, update.effective_user.id)
    # effective_message rather than message: every send path here is also
    # reachable from a button, and on a callback-query update `message` is
    # None while `effective_message` is the message the button is on. Getting
    # this wrong is an AttributeError inside a download, which the user sees
    # as the bot going quiet.
    if lossless:
        return await update.effective_message.reply_document(
            document=BytesIO(data),
            filename=f"{stem}.{_image_extension(data)}",
            reply_markup=reply_markup,
            disable_content_type_detection=True,
        )
    return await update.effective_message.reply_photo(
        BytesIO(data), reply_markup=reply_markup)


@contextmanager
def _media_source(item, path):
    """What goes to Telegram for one file: its stored file id (sent_files.StoredFile),
    or the file on disk, opened to be streamed."""
    stored = getattr(item, "file_id", None)
    if stored:
        yield stored
    else:
        with open(path, "rb") as handle:
            yield handle


def _file_id_of(sent, kind: str, lossless: bool) -> "str | None":
    """The file id Telegram gave the message `sent`, or None when it does not carry
    the kind of file the form expects: a clip with no sound that Telegram turned
    into an animation, say. Then nothing is stored for the delivery."""
    try:
        if lossless:
            attached = sent.document
        elif kind == "video":
            attached = sent.video
        else:
            photos = sent.photo
            attached = photos[-1] if photos else None
        file_id = attached.file_id if attached is not None else None
    except (AttributeError, IndexError, TypeError):
        return None
    return file_id if isinstance(file_id, str) and file_id else None


async def _send_media(update, context, items_with_paths, lang,
                      lossless: bool | None = None, redeliver_token: str | None = None,
                      probing: "video.ProbeRun | None" = None) -> "list[tuple[str, str]] | None":
    """Put what was resolved into the chat.

    One file goes as a photo or a video so it plays inline; several go as an
    album, in Telegram's own maximum of ten per group. Under /lossless every
    one of them goes as a document instead -- see lossless_toggle for the
    whole argument, and note `disable_content_type_detection`, without which
    the Bot API sniffs an uploaded .mp4, decides it is really a video, and
    delivers it re-encoded, which is the entire thing that branch exists to
    avoid.

    A video sent as a video is measured first (video.probe) and goes with its
    real width, height and duration: left out, Telegram draws it as a square.
    Documents and photos are not measured. `probing` is the delivery's probe
    budget (video.ProbeRun); a delivery sent in several calls (_send_large)
    passes the same one to each, so the budget is one per delivery.

    An item may carry a stored `file_id` instead of a file on disk
    (sent_files.StoredFile; its path is None): it is sent by that id, with the
    same captions, keyboard and album chunks, and is not measured -- Telegram
    already has its shape.

    Returns the file id Telegram gave each file, as (kind, file id) in the order
    the items were given, so that the delivery can be stored (sent_files.py); None
    if any of them is missing or is not the kind of file the form expects.
    """
    group = _GROUP.get()
    caption = None
    if await _credit_wanted(update.effective_user.id):
        caption = i18n.t(lang, "download_credit_caption", username=BOT_USERNAME)
    if lossless is None:
        lossless = (group["as_file"] if group is not None
                    else await asyncio.to_thread(get_lossless_enabled, update.effective_user.id))
    if probing is None:
        probing = video.ProbeRun()

    async def shape_of(path: str) -> dict:
        """The keyword arguments that give a video its shape; none when it could
        not be measured. After one ffprobe has been killed for taking too long the
        rest of the delivery is not measured at all, so that an album built to
        stall it costs one wait, not one per file."""
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
    # What the keyboard was built from stays with the delivery, so that a tap can
    # rebuild it without the button it spent (_remembered_kb).
    remembered = context.user_data.get("redeliver", {}).get(redeliver_token)
    if remembered is not None:
        remembered["photos"], remembered["items"] = photos, len(items_with_paths)
    # No buttons in a group chat: they act on the sender's own memory of the delivery, and
    # a group has a dozen people who could tap them.
    keyboard = (None if group is not None
                else _nudge_kb(lang, redeliver_token, lossless, photos=photos, items=len(items_with_paths)))
    # See _reply_image: this is also reached from the redeliver button.
    message = update.effective_message

    if len(items_with_paths) == 1:
        item, path = items_with_paths[0]
        shape = (await shape_of(path) if item.kind == "video" and not lossless
                 and not getattr(item, "file_id", None) else {})
        # Streamed from disk by python-telegram-bot rather than read into
        # memory here first.
        with _media_source(item, path) as f:
            if lossless:
                sent = await message.reply_document(
                    f, filename=os.path.basename(item.filename or path),
                    caption=caption, reply_markup=keyboard,
                    read_timeout=120, write_timeout=120,
                    disable_content_type_detection=True,
                )
            elif item.kind == "video":
                sent = await message.reply_video(
                    f, caption=caption, reply_markup=keyboard,
                    read_timeout=120, write_timeout=120,
                    **shape,
                )
            else:
                sent = await message.reply_photo(
                    f, caption=caption, reply_markup=keyboard,
                    read_timeout=120, write_timeout=120,
                )
        file_id = _file_id_of(sent, item.kind, lossless)
        return [(item.kind, file_id)] if file_id else None

    # An album. The caption rides on the first item of the first group, which
    # is where Telegram shows an album's caption.
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
                        caption=cap, disable_content_type_detection=True))
                elif item.kind == "video":
                    group.append(InputMediaVideo(handle, caption=cap, **shapes[n]))
                else:
                    group.append(InputMediaPhoto(handle, caption=cap))
            sent_group = await message.reply_media_group(
                group, read_timeout=120, write_timeout=120)
        finally:
            for handle in handles:
                if not isinstance(handle, str):         # a stored file id has nothing to close
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
    # Telegram allows no inline keyboard on a media group, so the button that
    # would have been under the album goes on one line after it.
    if keyboard is not None:
        await message.reply_text(
            i18n.t(lang, "album_delivered", count=len(items_with_paths)),
            reply_markup=keyboard)
    return sent_ids


async def _store_sent_files(user_id: int, platform: str, url: str, lossless: bool, sent) -> None:
    """After a delivery from bytes: keep the file ids Telegram gave (sent_files.py),
    so the next person who sends this post gets it without a download. Only when the
    store is on, the sender is one person's, and the delivery is 1 to 10 files with an
    id each (`sent`, from _send_media: None if any is missing). Never raises: a
    failed write is a line in the log and nothing else, and never touches the delivery."""
    if not sent_files.usable(user_id) or not sent:
        return
    try:
        text = sent_files.encode_items(sent)
        if text is None:
            return
        await asyncio.to_thread(sent_files_put, sent_files.post_key(platform, url, lossless), text)
        sent_files.note("stored")
    except Exception as exc:
        logger.warning("The sent-file store could not be written (%s)", type(exc).__name__)


async def _forget_sent_files(key: str) -> None:
    try:
        await asyncio.to_thread(sent_files_drop, key)
    except Exception as exc:
        logger.warning("The sent-file store could not drop a row (%s)", type(exc).__name__)


async def _resend_by_file_id(update, context, lang, status, platform: str, url: str,
                             lossless_override: "bool | None") -> "bool | None":
    """Send this post again from the file ids stored when it was last delivered
    (sent_files.py), if there are any and the post is known to be public still.

    None: nothing was sent, carry on with the normal path (no row, a row that cannot
    be read, a post that is gone or cannot be confirmed, an id Telegram refused).
    True: it was delivered. False: the send failed and the person has been told.

    A row confirmed within the last DBOT_FILEID_CONFIRM_HOURS is sent as it is. An
    older one is first checked with resolvers.lookup: present, and it is sent and the
    confirmation renewed; missing, and the row is deleted; unknown, and the row is
    kept but an unconfirmed post is never sent. The send mirrors the delivery-cache
    hit in _resolve_and_send, step for step."""
    user_id = update.effective_user.id
    if not sent_files.usable(user_id):
        return None
    try:
        if lossless_override is None:
            lossless = await asyncio.to_thread(get_lossless_enabled, user_id)
        else:
            lossless = lossless_override
        key = sent_files.post_key(platform, url, lossless)
        row = await asyncio.to_thread(sent_files_get, key)
    except Exception as exc:
        # A database that hiccups costs a normal fetch, never a failed delivery.
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
        except Exception as exc:
            if isinstance(exc, BadRequest):
                # Any refusal of the request itself drops the row, not only the wordings
                # is_invalid_file_id knows: a row Telegram refuses would otherwise fail
                # every send of this post, for everyone, until the 90-day sweep. A row
                # dropped for a reason that had nothing to do with the ids costs the
                # next person one ordinary download.
                await _forget_sent_files(key)
            if sent_files.is_invalid_file_id(exc):
                # Telegram no longer knows these ids. Nothing was delivered (one file, or
                # one chunk of at most ten), so: fetch the post as usual in this same
                # job -- the status message is still up.
                sent_files.note("invalid")
                logger.info("A stored file id was refused (%s)", type(exc).__name__)
                context.user_data.get("redeliver", {}).pop(token, None)
                return None
            note_job(False)
            # The kind of failure only, in the log and to the person: the text of an
            # error from these ids could carry one. The row stays: a blocked bot or a
            # flood wait says nothing about the ids.
            logger.warning("Delivering %s from the stored file ids failed (%s)", platform,
                           type(exc).__name__)
            await status.set(context.bot,
                             i18n.t(lang, "download_failed", error=type(exc).__name__),
                             **NO_PREVIEW)
            return False
    try:
        # Used today. Seen public just now only if a lookup said so: a fresh row is
        # trusted, not renewed, which is what makes "gone within 12 hours" true.
        await asyncio.to_thread(sent_files_used if fresh else sent_files_confirmed, key)
    except Exception as exc:
        logger.warning("The sent-file store could not be updated (%s)", type(exc).__name__)
    return True


async def _resolve_and_send(update: Update, context: ContextTypes.DEFAULT_TYPE,
                            platform: str, url: str, status=None,
                            quiet_if_missing: bool = False,
                            lossless_override: bool | None = None,
                            from_redo: bool = False) -> bool:
    """The whole download path: pick a route that is working, fetch, send.

    Returns True if something was delivered. On failure the user has already
    been told, with one exception: `quiet_if_missing` says the caller has a
    better answer than an error for a post with no media in it -- a text
    tweet, which becomes a card instead.

    Reuses an existing status message if one is passed in, instead of stacking
    a second "Fetching..." on top of whatever the caller already showed.

    `from_redo` says the caller is the redo button: its look-up in the delivery
    cache is counted as a hit or a miss (counts only), a pasted link's is not.
    """
    lang = await i18n.get_lang(update.effective_user.id, context)
    fetch_url, height, exact = _split_height(url)
    group = _GROUP.get()
    if lossless_override is None and group is not None:
        lossless_override = group["as_file"]
    # A download outlasts the shutdown grace period, so starting one now would
    # end in silence. A few seconds' wait beats a lost file.
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

    # Sent before, to anybody? Then it goes again by the file ids Telegram gave
    # (sent_files.py): no slot, no disk, no route asked for media. Before the slots
    # on purpose -- it uploads nothing, and the flood gate, the waiting limit and
    # the allowance already bound how many can ask. None: carry on as usual.
    resent = await _resend_by_file_id(update, context, lang, status, platform, url, lossless_override)
    if resent is not None:
        if resent:
            await _after_send(update, context, lang)
        return resent

    cached = delivery_cache.take(update.effective_user.id, platform, url)
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
                await _store_sent_files(update.effective_user.id, platform, url, delivered_lossless, sent)
            except Exception as exc:
                note_job(False)
                # The kind of failure only, in the log and to the person: the text
                # of an error from these files would carry their path.
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
            resolved = await resolvers.resolve(platform, fetch_url, height=height, exact=exact)
        except resolvers.NothingWorked as exc:
            logger.info("no route worked for %s: %s", platform, exc)
            if exc.kind == "missing" and quiet_if_missing:
                return False
            if exc.kind == "too_big" and large_files.CONFIGURED:
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
            token = _remember_delivery(context, platform, url, delivered_lossless)
            sent = await _send_media(update, context, list(zip(items, paths)), lang,
                                     lossless=delivered_lossless, redeliver_token=token)
            await status.delete(context.bot)
            note_job(True)
            kept = delivery_cache.keep(update.effective_user.id, platform, url,
                                       list(zip(items, paths)), _cache_floor_mb())
            if delivery_cache.usable(update.effective_user.id):
                note_tap("cache.kept" if kept else "cache.not_kept")
            if kept:
                paths.clear()
            await _store_sent_files(update.effective_user.id, platform, url, delivered_lossless, sent)
        except net.TooLarge as exc:
            if not large_files.CONFIGURED:
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
    """A file over the free ceiling (large_files.py): refused with its price
    and the switch, or -- for somebody who has switched large files on --
    fetched, charged once it is here, and sent. Returns whether it was sent."""
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

    # About to fetch up to LARGE_MAX_MB: kept deliveries give way first. After the
    # refusals above, so that a person who is refused costs nobody their kept files.
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
            await status.set(context.bot, i18n.t(lang, "large_files_too_big", max=large_files.LARGE_MAX_MB)
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
    """The "via @bot" line: the group's setting in a group chat, the person's own elsewhere."""
    group = _GROUP.get()
    if group is not None:
        return group["credit"]
    return await asyncio.to_thread(get_caption_enabled, user_id)


async def _send_large(update, context, items_with_paths, lang, status=None) -> None:
    caption = None
    if await _credit_wanted(update.effective_user.id):
        caption = i18n.t(lang, "download_credit_caption", username=BOT_USERNAME)
    probing = video.ProbeRun()

    async def waiting() -> None:
        if status is not None:
            await status.set(context.bot, i18n.t(lang, "transfer_waiting"))

    for item, path in items_with_paths:
        if os.path.getsize(path) > large_files.BOT_API_SEND_BYTES:
            shape = await video.probe(path, probing) if item.kind == "video" else None
            await big_files.send_document(update.effective_chat.id, path,
                                          os.path.basename(item.filename or path), caption or "",
                                          waiting=waiting,
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

    # Text or link post -- render a card instead of just dumping the title.
    # Card meta label ("upvotes") stays English -- Pillow's built-in bitmap
    # font (cards.py) has no Cyrillic glyphs, so translating it would just
    # draw missing-glyph boxes.
    title = post.get("title", "")
    selftext = post.get("selftext", "")
    body = title if not selftext else title + "\n\n" + selftext
    subreddit = "r/" + post.get("subreddit", "?")
    author = "u/" + post.get("author", "?")
    score = post.get("score")
    meta = f"{score:,} upvotes" if isinstance(score, int) else ""

    png = await asyncio.to_thread(cards.render_card, "reddit", subreddit, author, body, meta)
    # The cards go through the same switch as the downloads. A card is text
    # rendered as an image, which is the content photo compression damages
    # most visibly, so "lossless" meaning "except the ones with words on"
    # would be the wrong kind of surprising.
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
        # Card meta label ("likes") stays English -- see the Reddit card
        # comment above re: Pillow's built-in font having no Cyrillic glyphs.
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

    # X blocked the fetch entirely -- don't error, just hand back the link
    # rather than pretend this failed outright.
    await status.set(context.bot, i18n.t(lang, "twitter_fetch_failed_link", url=url))


def _allowance_message(lang: str, allowance: dict) -> str:
    """What somebody who has run out is told.

    Three things, in this order: that there is a limit and it is not
    personal, when it lifts, and -- only for people who have not already
    given -- that donating moves it. Asking a donor to donate is how a
    thank-you turns into a nag, and the ledger already knows.
    """
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
    # A group's quality cap (groups.py), on the platforms that go straight down the
    # download path; Reddit and X read the post first, from the link as it is.
    group = _GROUP.get()
    if group is not None and group["quality"] and platform not in ("reddit", "twitter"):
        url = _with_height(url, group["quality"], exact=False)
    # Another link: the person's last delivery is no longer wanted, and goes now.
    # The same link instead gives it its time again (delivery_cache.LIFE_SECONDS,
    # 10 minutes by default). Before every check below,
    # so that a link refused for any reason still counts as the latest one.
    found = delivery_cache.note_link(update.effective_user.id, platform, url)
    if found == delivery_cache.NOTED_AGAIN_CACHED:
        note_tap("link.again.cached")             # counts only: how often the same link comes back
    elif found == delivery_cache.NOTED_AGAIN_NOT_CACHED:
        note_tap("link.again.not_cached")

    # The same person, in the same chat, sending the same link while a job they
    # started from a link message for it is still queued or running: no new
    # job, no allowance check, no record, no status message and no "busy" -- one
    # 👌 on the copy (_answer_duplicate). The check and the mark are one
    # synchronous stretch, nothing awaited between them, so that two copies
    # handled at the same moment cannot both get through. Before everything
    # below, which would each answer or charge for it.
    key = (update.effective_chat.id, update.effective_user.id, platform, url)
    if _already_in_flight(key):
        await _answer_duplicate(update)
        return
    stamp = _in_flight[key] = time.monotonic()
    spawned = False
    try:
        # Asked once, here, for every platform. The gate used to live inside the
        # video path only -- which covers Instagram, TikTok and YouTube, and
        # misses the paths that fetch an image and send it without ever reaching
        # that function: a Pinterest pin, a Reddit image post and a Twitter photo
        # set all sailed straight through an announced update. They are quicker
        # than a video download, but "quick" is not the test: a redeploy lands
        # whenever it lands, and a fetch that started ten seconds before it ends
        # in silence exactly the same way.
        lang = await i18n.get_lang(update.effective_user.id, context)
        refusal = await refuse_new_work(lang, update.effective_user.id, update.effective_chat.id)
        if refusal:
            await update.message.reply_text(refusal)
            return

        # A share of the day, per person. MAX_CONCURRENT_DOWNLOADS above stops
        # the box falling over; this is the different problem of one person
        # holding both of its slots all afternoon while everybody else queues
        # behind them. Checked here rather than inside the download so a refused
        # link costs one query instead of a fetch -- and here rather than in each
        # of the three branches below, because the Reddit and Twitter paths cost
        # the same fetches and used to be the way past a limit that only the
        # video path enforced.
        # Checked before the allowance, so a link refused for being one too many
        # does not also count against the day's share.
        if _user_pending.get(update.effective_user.id, 0) >= MAX_PENDING_PER_USER:
            await update.message.reply_text(
                i18n.t(lang, "download_queue_full", count=MAX_PENDING_PER_USER))
            return
        allowance = await asyncio.to_thread(download_allowance, update.effective_user.id)
        if not allowance["allowed"]:
            await update.message.reply_text(_allowance_message(lang, allowance))
            return
        await asyncio.to_thread(record_download, update.effective_user.id, platform)

        async def work():
            if platform == "reddit":
                await _handle_reddit(update, context, url)
            elif platform == "twitter":
                await _handle_twitter(update, context, url)
            elif platform == "youtube" and _GROUP.get() is None:
                await _offer_qualities(update, context, url)
            else:
                await _resolve_and_send(update, context, platform, url)

        # Returns straight away: the next link, and everybody else's, is read now
        # rather than after this one has finished uploading.
        _spawn_download(context, update, work, in_flight=key)
        spawned = True
    finally:
        # Every way out that did not start the job -- the maintenance refusal,
        # the full queue, the allowance, an exception, a create_task that
        # raised -- takes the mark back. A job that started clears its own
        # (_spawn_download's finally).
        if not spawned:
            _clear_in_flight(key, stamp)


# ---------------------------------------------------------------------------
# The quality picker
# ---------------------------------------------------------------------------
# The owner (2026-10-28): "If bot has options for the quality, for example when downloading a
# high quality youtube video, it should ask the user in which size they want it", with a
# screenshot of another bot's list of heights, each with its size, and a button each. For
# MP3, trimming and the rest: "utilizing the converter bot would be good enough" -- a button
# to ConvertBot, where the person sends the video.
#
# Offered for the one platform whose route reads every version a video has (yt-dlp); the
# others send what their route gives. A choice that fits the free limit is ✅; one over it but
# within the large-file limit is ⚡ (paid, and only where large files are set up); anything
# bigger is not offered. With fewer than two choices nothing is asked: the download goes ahead.
QUALITY_PREFIX = "ytq:"
QUALITY_MEMORY = 5
QUALITY_LOOKUP_TIMEOUT_S = float(os.environ.get("DBOT_QUALITY_LOOKUP_TIMEOUT", "25"))


def _size_text(size: int) -> str:
    return f"{max(1, round(size / large_files.MB))} MB"


def quality_choices(options, free_mb: int, paid_mb: int) -> "list[tuple[int, int, bool]]":
    """[(height, bytes, free)] of the options worth a button: free when it fits `free_mb`
    (with the same margin the format picker keeps), paid up to `paid_mb` (0: none). Pure."""
    chosen = []
    for height, size in options:
        if size <= free_mb * large_files.MB * video.SIZE_MARGIN:
            chosen.append((height, size, True))
        elif size <= paid_mb * large_files.MB * video.SIZE_MARGIN:
            chosen.append((height, size, False))
    return chosen


async def _offer_qualities(update: Update, context: ContextTypes.DEFAULT_TYPE, url: str) -> None:
    """Ask which quality, or -- when there is nothing to choose between, or the versions
    could not be read -- download as usual."""
    lang = await i18n.get_lang(update.effective_user.id, context)
    refusal = await refuse_new_work(lang, update.effective_user.id, update.effective_chat.id)
    if refusal:
        await update.effective_message.reply_text(refusal)
        return
    status = await LiveMessage.reply_to(update.effective_message, i18n.t(lang, "fetching"))
    found = None
    try:
        async with _lookups():
            found = await asyncio.wait_for(asyncio.to_thread(video.list_qualities, url),
                                           QUALITY_LOOKUP_TIMEOUT_S)
    except Exception as exc:
        # The download that follows tries every route and says what went wrong.
        logger.info("No qualities to offer for a link (%s)", type(exc).__name__)
    paid_mb = large_files.LARGE_MAX_MB if large_files.CONFIGURED else 0
    choices = quality_choices((found or {}).get("options") or (), video.MAX_DOWNLOAD_MB, paid_mb)
    if len(choices) < 2:
        await _resolve_and_send(update, context, "youtube", url, status=status)
        return

    memory = context.user_data.setdefault("qualities", {})
    while len(memory) >= QUALITY_MEMORY:
        memory.pop(next(iter(memory)))
    token = uuid.uuid4().hex[:10]
    memory[token] = {"url": url, "heights": [height for height, _, _ in choices]}

    lines = [f"{'✅' if free else '⚡'} {height}p: {_size_text(size)}" for height, size, free in choices]
    text = i18n.t(lang, "quality_heading", title=(found.get("title") or "")[:200]) + "\n\n" + "\n".join(lines)
    if not all(free for _, _, free in choices):
        text += "\n\n" + i18n.t(lang, "quality_paid_note", free=video.MAX_DOWNLOAD_MB)
    text += "\n\n" + i18n.t(lang, "quality_choose")
    buttons = [InlineKeyboardButton(f"{'✅' if free else '⚡'} {height}p",
                                    callback_data=f"{QUALITY_PREFIX}{token}:{height}")
               for height, _, free in choices]
    rows = [buttons[i:i + 3] for i in range(0, len(buttons), 3)]
    convert = sibling_bots_keyboard_row(BOT_NAME, only="convertbot")
    if convert:
        rows.append([InlineKeyboardButton(i18n.t(lang, "quality_more_button"), url=convert[0].url)])
    await status.set(context.bot, text, reply_markup=InlineKeyboardMarkup(rows), **NO_PREVIEW)


async def quality_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """A tap on the picker: that height, downloaded in the picker's place. One tap per
    picker -- the link was counted against the allowance once, when it was sent."""
    query = update.callback_query
    lang = await i18n.get_lang(update.effective_user.id, context)
    token, _, height = query.data[len(QUALITY_PREFIX):].partition(":")
    entry = context.user_data.get("qualities", {}).pop(token, None)
    if entry is None or not height.isdigit() or int(height) not in entry["heights"]:
        await query.answer(i18n.t(lang, "quality_expired"), show_alert=True)
        return
    await query.answer()
    url = _with_height(entry["url"], int(height))
    # The person's latest link is this version of it, so the delivery is kept (delivery_cache).
    delivery_cache.note_link(update.effective_user.id, "youtube", url)
    status = LiveMessage.adopt(query.message)
    await status.set(context.bot, i18n.t(lang, "quality_picked", height=height))

    async def work():
        await _resolve_and_send(update, context, "youtube", url, status=status)

    _spawn_download(context, update, work)


# ---------------------------------------------------------------------------
# Group chats
# ---------------------------------------------------------------------------
# Added to a group, the bot downloads the links people post there and sends what it gets to
# the chat, following the group's settings (groups.py). It sees those links only if it may
# read the group's messages: as an admin, or with privacy mode turned off for it in
# @BotFather. When it can do neither, its greeting says so.
#
# Everything from a group chat goes through group_router, in its own handler group before
# the private-chat handlers, and stops there: none of the private chat's answers ("that
# doesn't look like a link", the feedback prompt, ...) is ever said to a whole group.
GROUP_TYPES = ("group", "supergroup")
GROUP_COMMANDS = [
    BotCommand("settings", "What I download here, and how"),
    BotCommand("help", "How I work in a group"),
]


async def _group_settings(chat_id: int) -> dict:
    return groups.normalize(await asyncio.to_thread(get_group_settings, chat_id))


def _group_command(message) -> "str | None":
    """The command a group message is ("settings"), "" for another bot's command
    (/settings@OtherBot), or None for a message that is not a command."""
    text = (message.text or "") if message is not None else ""
    if not text.startswith("/"):
        return None
    name, _, addressee = text.split()[0][1:].partition("@")
    if addressee and (not BOT_USERNAME or addressee.lower() != BOT_USERNAME.lower()):
        return ""
    return name.lower()


async def _sees_group_messages(bot, chat_id: int) -> bool:
    """Whether the links people post here reach the bot. True when it cannot tell."""
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
    """The greeting when the bot is added, and the answer to /start and /help in a group."""
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
             "reddit": "Reddit", "youtube": i18n.t(lang, "group_other_videos")}
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
    """A tap on a group's settings. Checked again here, not only when the screen was drawn:
    whoever taps may not be whoever sent /settings."""
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
    """Every message from a group chat: /settings, /start and /help, links -- and silence for
    everything else. Always ends the update here (ApplicationHandlerStop). An error is handed
    to the error handler as a task of its own, so that it is reported like any other while
    the update still stops here."""
    message, chat, user = update.effective_message, update.effective_chat, update.effective_user
    try:
        command = _group_command(message)
        if message is not None and message.migrate_from_chat_id:
            # The group became a supergroup, which has an id of its own: its settings come along.
            await asyncio.to_thread(move_group_settings, message.migrate_from_chat_id, chat.id)
        elif command == "settings" and user is not None:
            # Counted as having used the bot only for what they send to it: in a group the
            # bot reads everybody's messages (shared_features.track_activity).
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
    """Added to a group: say hello and what it needs. Removed: forget the group's settings."""
    change = update.my_chat_member
    if change is None or change.chat.type not in GROUP_TYPES:
        return
    before, now = change.old_chat_member.status, change.new_chat_member.status
    if now in ("left", "kicked"):
        await asyncio.to_thread(forget_group, change.chat.id)
    elif before in ("left", "kicked"):
        await _group_hello(update, context)


async def unrecognized_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Catches anything that isn't a recognized link and isn't a known
    command -- registered last, so it only fires when nothing else already
    handled the update. Silence here would just look like the bot ignored
    them."""
    lang = await i18n.get_lang(update.effective_user.id, context)
    await update.message.reply_text(i18n.t(lang, "unrecognized_message"))


async def unknown_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = await i18n.get_lang(update.effective_user.id, context)
    await update.message.reply_text(i18n.t(lang, "unknown_command"))


async def _delete_my_data_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """The shared erase button, and what only this bot holds on top of what it
    erases: the person's kept delivery (delivery_cache.py), and the memory the
    redo buttons read their links from. Their old buttons then answer "expired",
    as they do after any restart. Done on the tap of "yes", before the erase
    itself, so that even an erase that fails halfway leaves no file of theirs
    here. forget_user also stops a download still in flight from being kept
    when it lands."""
    query = update.callback_query
    if query is not None and query.data == ERASE_PREFIX + "yes":
        delivery_cache.forget_user(update.effective_user.id)
        context.user_data.pop("redeliver", None)
    return await delete_my_data_chosen(update, context)


async def _post_init(application):
    await tune_runtime(application)
    # Which download routes were working when this container's predecessor
    # stopped. Without it a bot that redeploys several times a day re-learns
    # that a dead service is dead every single time, at a user's expense.
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


# How often a kept delivery that has run out of time is found and deleted. A
# delivery lives DBOT_CACHE_SECONDS from its last use; this is only how late
# after that its files may still be on the disk. take() looks at the time too, so
# a late sweep never serves an expired one.
DELIVERY_CACHE_SWEEP_SECONDS = 15


async def _sweep_delivery_cache(_context=None) -> None:
    try:
        delivery_cache.sweep()
    except Exception as exc:
        logger.warning("The delivery cache sweep failed (%s)", type(exc).__name__)


async def _check_big_files() -> None:
    """In the background, as the bot starts: an API id and hash that are set
    but wrong turn large files off here -- no switch, 48 MB, as if they were
    not set -- instead of failing the first person who asks for one."""
    if await big_files.verify() or big_files.VERIFIED is not False:
        return
    logger.warning("Large files are off: %s.", big_files.describe())
    family_link.report_event_soon("warning", "big_files", f"DownloaderBot: {big_files.describe()}.")


_background: set = set()


async def _post_stop(application):
    # Before the pool closes: this is the one write that would otherwise lose
    # a whole flush interval of "this route just started failing".
    await _flush_provider_health()
    await net.close_client()
    await big_files.close()
    # Before flush_on_shutdown, which closes the pool lifecycle writes through.
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
    # The 220 ⚡ gift: the first message while it is open claims it (see
    # attach_promo_claim). Its own group, after the flood gate's.
    attach_promo_claim(app)
    # Runs after track_activity but before every other handler -- a no-op
    # unless /feedback or a "Custom" donate button has just asked for
    # something to be typed, in which case it takes the answer and stops it
    # from also being treated as a link (see awaited_text_received's
    # docstring).
    # Group chats first in the same handler group: python-telegram-bot runs the first
    # handler that matches, so a group message reaches group_router and never the
    # private-chat prompt-taker below it (see group_router).
    app.add_handler(MessageHandler(filters.ChatType.GROUPS, group_router), group=-1)
    app.add_handler(MessageHandler((filters.TEXT | filters.REPLY) & ~filters.COMMAND,
                                   awaited_text_received), group=-1)
    app.add_handler(ChatMemberHandler(bot_membership_changed, ChatMemberHandler.MY_CHAT_MEMBER))
    app.add_handler(CallbackQueryHandler(group_settings_callback, pattern="^" + groups.PREFIX))
    app.add_handler(CallbackQueryHandler(quality_chosen, pattern="^" + QUALITY_PREFIX))

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("fullhelp", full_help_command(build_help_text, None, HELP_PAGES, HELP_LABELS)))
    app.add_handler(CallbackQueryHandler(help_toggle(build_help_text, None, HELP_PAGES, HELP_LABELS),
                                         pattern=HELP_TOGGLE_PATTERN))
    app.add_handler(CallbackQueryHandler(help_page_button(HELP_PAGES), pattern=HELP_PAGE_PATTERN))
    app.add_handler(CommandHandler("feedback", feedback_command))
    app.add_handler(CommandHandler("cancel", cancel_command))
    app.add_handler(CommandHandler("settings", settings_command))
    app.add_handler(CommandHandler("caption", caption_toggle))
    app.add_handler(CommandHandler("lossless", lossless_toggle))
    app.add_handler(CommandHandler("dbdump", dbdump_command))  # owner-only
    app.add_handler(CommandHandler("messageas", messageas_command))  # owner-only
    app.add_handler(CommandHandler("status", status_command))  # owner-only
    app.add_handler(CommandHandler("providers", providers_command))  # owner-only
    app.add_handler(CallbackQueryHandler(caption_toggle_callback, pattern="^caption:"))
    app.add_handler(CallbackQueryHandler(lossless_toggle_callback, pattern="^lossless:"))
    app.add_handler(CallbackQueryHandler(large_toggle_callback, pattern="^large:"))
    app.add_handler(CallbackQueryHandler(redeliver_callback, pattern="^redo:"))
    app.add_handler(CallbackQueryHandler(merge_callback, pattern="^merge:"))
    app.add_handler(CallbackQueryHandler(cancel_choice_callback, pattern="^cancelpick:"))
    app.add_handler(CallbackQueryHandler(language_chosen, pattern="^setlang:"))
    app.add_handler(CommandHandler("language", language_command))

    # ---- language: /en, /uz, /rus -- pick at first /start, change anytime ----
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
    app.add_handler(CallbackQueryHandler(_delete_my_data_chosen, pattern="^" + ERASE_PREFIX))
    app.add_handler(MessageHandler(filters.TEXT & filters.Regex(platforms.ANY_LINK_RE), handle_link))

    # ---- donations (Telegram Stars) -- this bot's only Stars usage ----
    app.add_handler(CommandHandler("balance", balance_command))
    app.add_handler(CommandHandler("donate", donate_command))
    app.add_handler(CallbackQueryHandler(donate_amount_chosen, pattern="^donate:"))
    app.add_handler(CallbackQueryHandler(donate_fiat_amount_chosen, pattern="^donatefiat:"))
    app.add_handler(CallbackQueryHandler(donate_custom_button_chosen, pattern="^donatecustom:"))
    app.add_handler(PreCheckoutQueryHandler(donation_precheckout_callback))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, donation_payment_callback))

    # Both registered last, so every real handler above gets first shot.
    app.add_handler(MessageHandler(filters.COMMAND, unknown_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, unrecognized_message))

    # ManagerBot's link: heartbeats, crash/donation events, and the queue it
    # uses to run this bot's owner-only commands remotely. Never raises --
    # with no shared database reachable the bot just runs on its own.
    family_link.attach(app, BOT_NAME, "DownloaderBot", START_TIME)
    # One extra bus command, registered here rather than in family_link.py:
    # only this bot has download routes to probe, and family_link.py has to
    # stay byte-identical across all five. The dispatcher looks COMMANDS up
    # per command, so adding to it after import is enough.
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
    # A 30-second long poll is the same latency as the default 10 -- Telegram
    # answers the moment an update exists -- for a third of the HTTP requests.
    # allowed_updates lists every kind this bot has a handler for, so Telegram
    # stops sending the rest rather than this process parsing and dropping it.
    app.run_polling(**lifecycle.polling_kwargs(
        timeout=POLL_TIMEOUT,
        allowed_updates=[Update.MESSAGE, Update.CALLBACK_QUERY, Update.PRE_CHECKOUT_QUERY,
                         Update.MY_CHAT_MEMBER],
    ))


if __name__ == "__main__":
    main()
