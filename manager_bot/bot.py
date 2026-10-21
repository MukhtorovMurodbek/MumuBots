"""ManagerBot -- the private one, for the owner only.

The other four bots in the family each serve the public and each answer to
their own owner-only commands. ManagerBot serves exactly one person and
answers for all of them:

  * **Watches.** Every bot stamps a heartbeat into the shared database every
    30 seconds (family_link.py). ManagerBot checks those stamps once a minute
    and messages the owner the moment one goes stale -- and again when it
    comes back. It never repeats itself while a bot stays down.
  * **Reports.** Anything a bot considers worth waking someone for -- an
    unhandled exception, a donation, a restart -- lands in family.events and
    ManagerBot forwards it as a DM within about twenty seconds.
  * **Reaches in.** /run <bot> <command> puts a job on the family command
    queue; the target bot runs it in its own process, with its own code and
    its own Telegram identity, and the answer comes back here. That is how
    /dbdump, /whois, /message and the rest work against a bot deployed on a
    machine ManagerBot cannot otherwise reach.
  * **Reads across.** One shared Postgres database with a schema per bot
    means "how many people used ConvertBot this week" is a single query, no
    matter whether ConvertBot itself is even running.

Nothing here is reachable by anyone but the ids in MBOT_ADMIN_ID. A stranger
who finds this bot gets one flat sentence and nothing else -- and the owner
gets told they turned up.

Env vars: MBOT_TOKEN, MBOT_USERNAME (no @), MBOT_ADMIN_ID (required -- with
it empty the bot refuses to start rather than run wide open), DATABASE_URL
(the shared family database), DB_SCHEMA (default "manager_bot").
"""
import asyncio
import functools
import hashlib
import html
import json
import logging
import os
import re
import secrets
import tempfile
import time as time_module
import zipfile
from collections import OrderedDict, deque
from itertools import takewhile
from datetime import datetime, time, timedelta, timezone
from io import BytesIO
from pathlib import Path

# The modules the bots share live once, in ../shared (since 2.0.0). A module
# of this bot's own with the same name still comes first.
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(1, str(_Path(__file__).resolve().parent.parent / "shared"))

# Settings: the environment, then this bot's .env, then shared/.env -- see
# shared/envfiles.py.
import envfiles  # noqa: E402
envfiles.load(__file__)

from telegram import (
    BotCommand, BotCommandScopeChat, InlineKeyboardButton,
    InlineKeyboardMarkup, LinkPreviewOptions, Update,
)
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    TypeHandler,
    filters,
)

import db
import family_link
import lifecycle
import problems
from live_message import LiveMessage, edit_in_place
from db import init_db
import monitoring
from monitoring import (
    attach_maintenance,
    build_status_text,
    error_handler,
    flush_on_shutdown,
    setup_logging,
    track_activity,
    tune_runtime,
)

setup_logging(__file__)
logger = logging.getLogger(__name__)

START_TIME = datetime.now(timezone.utc)

BOT_TOKEN = os.environ.get("MBOT_TOKEN")
# Without the @, which is also taken off if it was written with one -- the
# one-process edition always allowed it, and a link built from "@name" is broken.
BOT_USERNAME = (os.environ.get("MBOT_USERNAME") or "").strip().lstrip("@") or None
BOT_NAME = "managerbot"
DISPLAY_NAME = "ManagerBot"

ADMIN_IDS = {int(x) for x in os.environ.get("MBOT_ADMIN_ID", "").split(",") if x.strip()}

# A bot is "down" once its heartbeat is this stale. family_link beats every
# 30s by default, so this is four missed beats -- long enough not to page
# anyone over one slow database round-trip, short enough to catch a crash
# loop before it has been down for an hour.
DOWN_AFTER_SECONDS = int(os.environ.get("MBOT_DOWN_AFTER_SECONDS", "120"))

# How long a queued command waits for its target before ManagerBot gives up
# and says so.
COMMAND_TIMEOUT_SECONDS = int(os.environ.get("MBOT_COMMAND_TIMEOUT_SECONDS", "90"))

# How ManagerBot collects command results and child-bot events: the same
# adaptive poll the child bots use for the command queue (family_link._bus_tick).
# One fast tick decides each time whether to sweep -- every FAST interval while
# the bus is active (family_link.bus_is_active(), which ManagerBot sets the
# moment it queues a command), every SLOW interval once it has gone quiet.
RESULT_POLL_SECONDS = int(os.environ.get("MBOT_RESULT_POLL_SECONDS", "20"))
EVENT_POLL_SECONDS = int(os.environ.get("MBOT_EVENT_POLL_SECONDS", "30"))
RESULT_POLL_FAST_SECONDS = int(os.environ.get("MBOT_RESULT_POLL_FAST_SECONDS", "1"))
EVENT_POLL_FAST_SECONDS = int(os.environ.get("MBOT_EVENT_POLL_FAST_SECONDS", "2"))
# How often the pumps tick to make that decision.
PUMP_TICK_SECONDS = int(os.environ.get("MBOT_PUMP_TICK_SECONDS", "1"))

# A redeploy makes a bot's heartbeat stale exactly the way a crash does, and
# for the first minute of it there is no way to tell them apart from the
# outside. So the inside says: a bot that is shut down on purpose leaves a
# note in family.settings on its way out (lifecycle.mark_expected_restart),
# and a stale heartbeat with a fresh note next to it is reported as a
# redeploy rather than as a failure. If it is still missing when the note has
# gone stale, the ordinary "it is down" alert fires after all -- a deploy
# that never came back is exactly the thing worth being told about.
REDEPLOY_GRACE_SECONDS = int(os.environ.get("MBOT_REDEPLOY_GRACE_SECONDS", "300"))

# How long the startup roll-call waits before reporting who is up. The point
# of the delay is to send one message instead of five: the other four write
# their first heartbeat inside family_link.attach(), synchronously, before
# they start polling -- so a few seconds is all it takes for a family started
# together to be fully visible. start_all.ps1 already gives them the same head
# start, for the same reason. Set to 0 to report immediately.
STARTUP_ROLLCALL_SECONDS = int(os.environ.get("MBOT_ROLLCALL_SECONDS", "5"))

# A bot whose heartbeat is younger than this at roll-call time came up with
# this batch rather than having been running already. Affects the wording of
# one line, nothing else.
ROLLCALL_FRESH_SECONDS = int(os.environ.get("MBOT_ROLLCALL_FRESH_SECONDS", "120"))

# Which event levels are worth an unprompted DM. Everything is still
# recorded either way -- /events shows the rest.
ALERT_LEVELS = {"warning", "error", "critical"}

# "HH:MM" in UTC for a once-a-day summary, or empty for none (the default --
# down/up transitions and crashes already arrive on their own, and a daily
# "all fine" message is the kind of thing you stop reading).
DIGEST_AT_UTC = os.environ.get("MBOT_DIGEST_UTC", "").strip()

# The nightly problem report, at 22:00 where the owner is. It was "at around
# midnight by seoul/suwon time" from 1.7.0; on 2026-10-03 the owner asked:
# "Change the nightly report time to 22.00 instead of 24.00." Korea has not
# observed daylight saving since 1988, so KST is UTC+9 all year and 13:00 UTC
# is 22:00 in Suwon on every date -- no timezone database needed, and no
# twice-yearly hour where the report arrives at nine.
#
# Deliberately NOT the release window. That is 04:00 Asia/Tashkent (23:00
# UTC), chosen for when the *users* are asleep; this one is chosen for when
# the owner is awake. The two have no reason to be the same time and it would
# be a bug if changing one moved the other.
NIGHTLY_REPORT_AT_UTC = os.environ.get("MBOT_NIGHTLY_UTC", "13:00").strip()
NIGHTLY_REPORT_HOURS = int(os.environ.get("MBOT_NIGHTLY_HOURS", "24"))

TELEGRAM_MAX_CHARS = 3900  # a little under the real 4096, leaving room for markup

# Telegram's long-poll window -- see the note in main().
POLL_TIMEOUT = int(os.environ.get("POLL_TIMEOUT", "30"))


# ---------------------------------------------------------------------------
# Who is in the family
# ---------------------------------------------------------------------------
# id : display name : Postgres schema. The ids match each bot's own BOT_NAME
# constant (and the SIBLING_BOTS env var they all share), which is what the
# command queue and the heartbeat table key on. Override with FAMILY_REGISTRY
# if you add a sixth bot without redeploying this one.

DEFAULT_REGISTRY = (
    "stickerbot:StickerBot:sticker_bot,"
    "convertbot:ConvertBot:convert_bot,"
    "downloaderbot:DownloaderBot:downloader_bot,"
    "anonbot:AnonBot:anon_bot"
)


def _parse_registry() -> list[dict]:
    raw = os.environ.get("FAMILY_REGISTRY", DEFAULT_REGISTRY)
    bots = []
    for entry in raw.split(","):
        parts = [p.strip() for p in entry.strip().split(":")]
        if len(parts) == 3 and all(parts):
            bots.append({"id": parts[0], "name": parts[1], "schema": parts[2]})
    return bots


CHILDREN = _parse_registry()
ALL_BOTS = CHILDREN + [{"id": BOT_NAME, "name": DISPLAY_NAME, "schema": db.DB_SCHEMA}]


# The one place a name that is not the family name is allowed to exist.
#
# Every layer of this project derives from one canonical id -- see the naming
# table in ARCHITECTURE.md -- with exactly one exception: the Telegram
# @username. One of those was chosen before the family names settled and
# cannot simply be brought into line, because a username is not a label but
# an address:
#
#   AnonBot is @mumu_chat_bot, and every inbox link anybody has ever posted
#   is https://t.me/mumu_chat_bot?start=q_<token>. Changing it does not
#   rename the bot, it breaks every one of those links, permanently, with no
#   redirect and no way to find the people holding them. That username is
#   frozen for as long as the bot has users.
#
# So rather than pretend the mismatch is not there, it is written down here
# and every form resolves. `/logs chat`, `/logs anon`, `/logs anonbot` and
# `/logs @mumu_chat_bot` all reach the same bot.
#
# This bot used to be the second exception. It was ManagerBot's predecessor
# ParentBot at @mumu_manager_bot, and the plan was to move the username to
# @mumu_parent_bot. v1.6.0 did the opposite -- the name followed the address
# rather than the address following the name -- because the username was the
# only layer with anything depending on it and the other six were free. So
# there is one exception left in the family rather than two.
#
# `parent` and `parentbot` stay as aliases. They are what the owner has been
# typing for months, and an alias costs a list entry.
USERNAME_ALIASES = {
    "stickerbot": ["mumu_sticker_bot"],
    "convertbot": ["mumu_convert_bot"],
    "downloaderbot": ["mumu_downloader_bot"],
    "anonbot": ["mumu_chat_bot", "chat", "chatbot"],
    "managerbot": ["mumu_manager_bot", "manager", "parent", "parentbot"],
}


def _names_of(bot: dict) -> list[str]:
    return [bot["id"], bot["name"].lower(), bot["schema"], *USERNAME_ALIASES.get(bot["id"], [])]


def resolve_bot(token: str) -> dict | None:
    """Accepts "sticker", "stickerbot", "StickerBot", "sticker_bot",
    "@mumu_sticker_bot" -- any unambiguous prefix of the id, the display
    name, the schema, or the Telegram username."""
    token = token.strip().lower().lstrip("@")
    if not token:
        return None
    for bot in ALL_BOTS:
        if token in _names_of(bot):
            return bot
    matches = [b for b in ALL_BOTS if any(n.startswith(token) for n in _names_of(b))]
    return matches[0] if len(matches) == 1 else None


def bot_list_hint() -> str:
    return ", ".join(b["id"] for b in ALL_BOTS)


# ---------------------------------------------------------------------------
# Access control
# ---------------------------------------------------------------------------
# Tracked so the owner hears about a stranger once rather than on every
# message they send. In memory only: a restart re-arms it, which is fine --
# the point is "someone found the private bot", not an audit log.
_reported_strangers: set[int] = set()


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


async def guard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """True if this update may proceed. Non-admins get one flat sentence,
    and the owner gets told about them the first time."""
    user = update.effective_user
    if user and is_admin(user.id):
        return True
    if user and user.id not in _reported_strangers:
        _reported_strangers.add(user.id)
        # That somebody found it, not who: no user details go to the owner.
        await notify_owner(context, "👀 A stranger found ManagerBot, and was turned away.")
        await asyncio.to_thread(
            db.log_event, BOT_NAME, "warning", "stranger",
            "An unknown user messaged ManagerBot.",
        )
    if update.message:
        await update.message.reply_text("This is a private bot.")
    return False


# ---------------------------------------------------------------------------
# Sending
# ---------------------------------------------------------------------------

async def notify_owner(context: ContextTypes.DEFAULT_TYPE, text: str) -> bool:
    """Never raises. True if at least one owner got it -- a caller that must
    not lose the message (the 220 gift's one notice) tries again on False."""
    delivered = False
    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_message(chat_id=admin_id, text=text, parse_mode=ParseMode.HTML)
            delivered = True
        except Exception:
            logger.exception("Couldn't deliver an alert to %s", admin_id)
    return delivered


async def send_long(context: ContextTypes.DEFAULT_TYPE, chat_id: int, text: str, filename: str = "output.txt") -> None:
    """Anything past Telegram's message ceiling goes as a file instead of
    being silently cut in half -- a truncated log tail is worse than useless
    when you are trying to work out what broke."""
    if len(text) <= TELEGRAM_MAX_CHARS:
        await context.bot.send_message(chat_id=chat_id, text=text)
        return
    await context.bot.send_message(chat_id=chat_id, text=f"{text[:TELEGRAM_MAX_CHARS]}\n\n[…full text attached]")
    await context.bot.send_document(
        chat_id=chat_id, document=BytesIO(text.encode("utf-8")), filename=filename
    )


def format_delta(seconds: float) -> str:
    seconds = int(seconds)
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m"
    return f"{secs}s"


# ---------------------------------------------------------------------------
# /status -- the board
# ---------------------------------------------------------------------------

def _build_board(sweep_line: str = "") -> str:
    """Runs entirely in a worker thread (it is all blocking SQL), so the
    caller wraps it in asyncio.to_thread. One query for the whole board --
    see db.status_snapshot. The sweep timings live in memory and are mutated
    by the event loop, so the caller reads them there (_sweep_line()) and
    passes the finished line in; this function never touches them."""
    now = datetime.now(timezone.utc)
    snapshot = db.status_snapshot([b["schema"] for b in ALL_BOTS], now - timedelta(hours=1))
    beats = {row["bot_id"]: row for row in snapshot["beats"]}
    active_by_schema = snapshot["active"]

    up_count = 0
    lines = []
    for bot in ALL_BOTS:
        beat = beats.get(bot["id"])
        me = " (me)" if bot["id"] == BOT_NAME else ""
        if beat is None:
            lines.append(f"❔ <b>{bot['name']}</b>{me} — never seen. Has it ever been started?")
            continue

        is_up = beat["seconds_ago"] <= DOWN_AFTER_SECONDS
        up_count += is_up
        if not is_up:
            lines.append(
                f"❌ <b>{bot['name']}</b>{me} — DOWN, last heartbeat "
                f"{format_delta(beat['seconds_ago'])} ago (was up "
                f"{format_delta((beat['last_seen'] - beat['started_at']).total_seconds())})"
            )
            continue

        active = active_by_schema.get(bot["schema"])
        uptime = format_delta((now - beat["started_at"]).total_seconds())
        errors = f" · ⚠️ {beat['error_count']}" if beat["error_count"] else ""
        users = "" if active is None else f" · {active} user(s)/h"
        lines.append(
            f"✅ <b>{bot['name']}</b>{me} — up {uptime} · {html.escape(beat['host'] or '?')} "
            f"· v{beat['version']}{errors}{users}"
        )

    info = snapshot["database"]
    db_line = f"🗄 Database: {info['name']} · Postgres {info['version']} · {info['size']}"
    header = f"👪 <b>Bot family</b> — {up_count}/{len(ALL_BOTS)} up"
    alerts = "on" if snapshot["alerts"] == "on" else "OFF"
    stamp = now.strftime("%H:%M:%S UTC")
    return "\n".join([header, "", *lines, "", db_line,
                      *([sweep_line] if sweep_line else []),
                      f"🔔 Alerts: {alerts} · 🕐 {stamp}"])


def _board_keyboard() -> InlineKeyboardMarkup:
    """A refresh button, because /status is the command anyone watching a
    deploy types over and over -- and a mute toggle right where you notice
    you want it."""
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🔄 Refresh", callback_data="board:refresh"),
        InlineKeyboardButton("🔔 Alerts", callback_data="board:alerts"),
        InlineKeyboardButton("📡 Ping all", callback_data="board:ping"),
    ]])


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update, context):
        return
    try:
        board = await asyncio.to_thread(_build_board, _sweep_line())
    except Exception as exc:
        await update.message.reply_text(
            f"⚠️ Couldn't read the family database: {exc}\n\n"
            "Every bot may well be fine -- this is ManagerBot's own connection failing."
        )
        return
    await update.message.reply_text(board, parse_mode=ParseMode.HTML, reply_markup=_board_keyboard())


# ---------------------------------------------------------------------------
# /usage -- what the containers have been costing, rather than whether they
# are up
# ---------------------------------------------------------------------------
# /status answers "is it alive". This answers "what is alive costing, and is
# that normal", which is a different question and the one the bill asks.
#
# The rate below is Railway's published memory price. It is here, as a
# constant with its own name, so the number in the message is arithmetic
# anybody can check rather than a figure somebody remembered -- and so that
# when Railway changes it, one line changes.
MEMORY_USD_PER_MB_MONTH = float(os.environ.get("MEMORY_USD_PER_MB_MONTH") or 0.01)
USAGE_REPORT_HOURS = int(os.environ.get("USAGE_REPORT_HOURS") or 24)


def _usage_report(hours: int) -> str:
    samples = family_link.usage_history(None, hours)
    if not samples:
        return (f"No usage samples in the last {hours}h.\n\n"
                "Each bot writes one every USAGE_SAMPLE_MINUTES (15 by default), so this "
                "is empty until they have been up that long on a version that has it.")

    per_bot: dict[str, list[dict]] = {}
    for sample in samples:
        per_bot.setdefault(sample["bot_id"], []).append(sample)

    lines = [f"<b>Usage, last {hours}h</b>"]
    total_cost = 0.0
    for bot_id in sorted(per_bot):
        rows = per_bot[bot_id]
        resident = [row["rss_mb"] for row in rows if row["rss_mb"]]
        peaks = [row["peak_rss_mb"] for row in rows if row["peak_rss_mb"]]
        ceilings = [row["ceiling_mb"] for row in rows if row["ceiling_mb"]]
        updates = sum(row["updates"] or 0 for row in rows)
        people = max((row["users"] or 0) for row in rows)
        average = sum(resident) / len(resident) if resident else 0
        cost = average * MEMORY_USD_PER_MB_MONTH
        total_cost += cost
        line = f"\n<b>{html.escape(bot_id)}</b>"
        if resident:
            line += f"\n  {average:.0f} MB average, {max(peaks or resident)} MB peak"
            if ceilings:
                headroom = max(peaks or resident) / max(ceilings)
                line += f" of {max(ceilings)} MB ({headroom:.0%})"
            line += f"\n  \u2248 ${cost:.2f}/month in memory"
        else:
            line += "\n  no memory readings (not a Linux container?)"
        line += f"\n  {updates} update(s), busiest window {people} person(s)"
        lines.append(line)

    lines.append(f"\n<b>All five \u2248 ${total_cost:.2f}/month in memory</b> "
                 f"at ${MEMORY_USD_PER_MB_MONTH:.3f} per MB-month.")
    lines.append("\nMemory is the bill: a container is paid for every minute it holds "
                 "what it holds, busy or not.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# /idle -- would sleeping this bot be worth it, and what would it cost
# ---------------------------------------------------------------------------
# Railway stops charging for a container once it has sent no outbound traffic
# for about five minutes, and starts again when something wakes it. Whether
# that is a good trade for a given bot is two numbers: how much of its life it
# spends quiet, and how many times a day somebody would therefore wait for a
# cold start.
#
# Both are measured rather than guessed. Every bot accumulates, per window,
# the seconds it *would* have been asleep (each gap between updates, minus the
# five minutes before sleeping begins) and the longest gap it saw. This reads
# those back.
#
# Nothing sleeps yet. This is the evidence for deciding what should.
COLD_START_SECONDS = int(os.environ.get("COLD_START_SECONDS") or 6)


def _idle_report(hours: int) -> str:
    samples = family_link.usage_history(None, hours)
    if not samples:
        return (f"No usage samples in the last {hours}h -- nothing to judge yet.\n\n"
                "Each bot writes one every 15 minutes, so a day of running gives about "
                "96 per bot, which is enough to see a daily shape.")

    per_bot: dict[str, list[dict]] = {}
    for sample in samples:
        per_bot.setdefault(sample["bot_id"], []).append(sample)

    lines = [f"<b>Idle time, last {hours}h</b>",
             f"<i>Sleeping starts after {monitoring.SLEEP_AFTER_SECONDS // 60} min of quiet. "
             f"Nothing sleeps yet.</i>"]
    for bot_id in sorted(per_bot):
        rows = per_bot[bot_id]
        covered = sum((row["window_minutes"] or 0) for row in rows) * 60
        if not covered:
            continue
        sleepable = sum((row["sleepable_seconds"] or 0) for row in rows)
        longest = max((row["max_gap_seconds"] or 0) for row in rows)
        updates = sum((row["updates"] or 0) for row in rows)
        resident = [row["rss_mb"] for row in rows if row["rss_mb"]]
        average = sum(resident) / len(resident) if resident else 0
        share = sleepable / covered
        # One wake per gap long enough to have slept through. The count is not
        # stored, so it is bounded from what is: a bot cannot have woken more
        # times than it handled updates.
        wakes = min(updates, int(sleepable / max(monitoring.SLEEP_AFTER_SECONDS, 1)) + 1)
        saving = average * MEMORY_USD_PER_MB_MONTH * share
        verdict = ("worth sleeping" if share >= 0.6 and average
                   else "not worth it yet" if share >= 0.2
                   else "busy enough to leave alone")
        lines.append(
            f"\n<b>{html.escape(bot_id)}</b> — {share:.0%} of the time asleep"
            f"\n  longest quiet stretch {longest // 3600}h {(longest % 3600) // 60}m"
            f"\n  {updates} update(s), about {wakes} wake(s) — "
            f"{wakes * COLD_START_SECONDS}s of cold start in total"
            f"\n  would save \u2248 ${saving:.2f}/month · <i>{verdict}</i>"
        )
    lines.append("\nA wake costs the person who caused it a few seconds before their "
                 "first reply. That is the whole trade.")
    return "\n".join(lines)


async def idle_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update, context):
        return
    hours = USAGE_REPORT_HOURS
    if context.args:
        try:
            hours = max(1, min(24 * 45, int(context.args[0])))
        except ValueError:
            pass
    try:
        report = await asyncio.to_thread(_idle_report, hours)
    except Exception as exc:
        await update.message.reply_text(f"\u26a0\ufe0f Couldn't read the usage table: {exc}")
        return
    await update.message.reply_text(report, parse_mode=ParseMode.HTML)


async def usage_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update, context):
        return
    hours = USAGE_REPORT_HOURS
    if context.args:
        try:
            hours = max(1, min(24 * 45, int(context.args[0])))
        except ValueError:
            pass
    try:
        report = await asyncio.to_thread(_usage_report, hours)
    except Exception as exc:
        await update.message.reply_text(f"\u26a0\ufe0f Couldn't read the usage table: {exc}")
        return
    await update.message.reply_text(report, parse_mode=ParseMode.HTML)


# ---------------------------------------------------------------------------
# /buttons -- how often each button is tapped (counts, nothing about who)
# ---------------------------------------------------------------------------
# Each public bot counts the taps of its buttons per day for the whole bot and
# writes the counts with its usage sample (family.button_taps; the key is the
# button's name, never an id or a link). DownloaderBot also counts a few events
# that are not callbacks -- how long people take to tap "get it as a file", whether
# the delivery cache answered it -- which are the numbers that decide how long that
# cache should keep a download. ManagerBot's own buttons are not counted.
BUTTONS_DEFAULT_DAYS = 7
BUTTONS_MAX_DAYS = 90
BUTTONS_MAX_LINES = 25            # keys listed per bot, then "+N more"
BUTTONS_USAGE = f"Usage: /buttons [days] -- from 1 to {BUTTONS_MAX_DAYS} days, {BUTTONS_DEFAULT_DAYS} if not said."

_REDO_KEYS = ("redo.to_file", "redo.to_compressed", "redo.expired", "redo.spent")
_REDO_AFTER = (("lt1m", "<1m"), ("1-2m", "1-2m"), ("2-5m", "2-5m"), ("5-15m", "5-15m"),
               ("15-60m", "15-60m"), ("gt60m", ">60m"), ("unknown", "unknown"))
_DOWNLOADER_BLOCK = (set(_REDO_KEYS) | {f"redo.after.{name}" for name, _ in _REDO_AFTER}
                     | {"redo.cache.hit", "redo.cache.miss", "cache.kept", "cache.not_kept",
                        "link.again.cached", "link.again.not_cached"})


def _downloader_block(counts: dict) -> list[str]:
    n = lambda key: counts.get(key, 0)  # noqa: E731
    return [
        f"  Redo taps: {n('redo.to_file') + n('redo.to_compressed')} (to file {n('redo.to_file')}, "
        f"to compressed {n('redo.to_compressed')}); expired {n('redo.expired')}, double {n('redo.spent')}",
        "  Tap after: " + " \u00b7 ".join(f"{label} {n('redo.after.' + name)}" for name, label in _REDO_AFTER),
        f"  Redo from cache: hit {n('redo.cache.hit')} \u00b7 miss {n('redo.cache.miss')}; "
        f"deliveries kept {n('cache.kept')} \u00b7 not kept {n('cache.not_kept')}",
        f"  Same link again: cached {n('link.again.cached')} \u00b7 not cached {n('link.again.not_cached')}",
    ]


def _buttons_report(days: int) -> str:
    rows = family_link.button_taps_history(days)
    if not rows:
        return "No button taps recorded yet."
    per_bot: dict[str, dict[str, int]] = {}
    for row in rows:
        per_bot.setdefault(row["bot_id"], {})[row["button"]] = int(row["taps"])
    names = {child["id"]: child["name"] for child in CHILDREN}
    order = [child["id"] for child in CHILDREN if child["id"] in per_bot]
    order += sorted(bot_id for bot_id in per_bot if bot_id not in names)
    lines = [f"Button taps, last {days} days (counts only, nothing about who)"]
    for bot_id in order:
        counts = per_bot[bot_id]
        lines += ["", names.get(bot_id, bot_id)]
        if bot_id == "downloaderbot":
            lines += _downloader_block(counts)
            counts = {key: taps for key, taps in counts.items() if key not in _DOWNLOADER_BLOCK}
        ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
        lines += [f"  {key}: {taps}" for key, taps in ranked[:BUTTONS_MAX_LINES]]
        if len(ranked) > BUTTONS_MAX_LINES:
            lines.append(f"  +{len(ranked) - BUTTONS_MAX_LINES} more")
    return "\n".join(lines)


async def buttons_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update, context):
        return
    days = BUTTONS_DEFAULT_DAYS
    if context.args:
        try:
            days = int(context.args[0])
        except ValueError:
            days = 0
        if len(context.args) > 1 or not 1 <= days <= BUTTONS_MAX_DAYS:
            await update.message.reply_text(BUTTONS_USAGE)
            return
    try:
        report = await asyncio.to_thread(_buttons_report, days)
    except Exception as exc:
        await update.message.reply_text(f"\u26a0\ufe0f Couldn't read the button taps: {exc}")
        return
    await send_long(context, update.effective_chat.id, report, "buttons.txt")


async def board_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """The three buttons under the /status board."""
    if not await guard(update, context):
        return
    query = update.callback_query
    action = query.data.split(":", 1)[1]

    if action == "alerts":
        current = await asyncio.to_thread(db.get_setting, "alerts", "on")
        new = "off" if current == "on" else "on"
        await asyncio.to_thread(db.set_setting, "alerts", new)
        await query.answer(f"Alerts {new}.")
    elif action == "ping":
        for child in CHILDREN:
            await asyncio.to_thread(
                db.queue_command, child["id"], "ping", "",
                update.effective_user.id, update.effective_chat.id,
            )
        await query.answer(f"Pinged all {len(CHILDREN)}.")
        return
    else:
        await query.answer()

    try:
        board = await asyncio.to_thread(_build_board, _sweep_line())
    except Exception as exc:
        # A second query.answer() on the same callback is rejected by
        # Telegram, and this one has already been answered above -- so say it
        # in the chat instead.
        await query.message.reply_text(
            f"⚠️ Couldn't read the family database: {exc}\n\n"
            "Every bot may well be fine -- this is ManagerBot's own connection failing."
        )
        return
    # live_message rather than edit_text: it swallows "message is not
    # modified" (which the timestamp in the footer makes rare anyway), and it
    # moves the board down to the bottom of the chat if anything has been
    # said since the buttons were tapped.
    await edit_in_place(query.message, context.bot, board,
                        parse_mode=ParseMode.HTML, reply_markup=_board_keyboard())


async def me_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ManagerBot's own /status, in the same shape every other bot uses."""
    if not await guard(update, context):
        return
    now = datetime.now(timezone.utc)
    hour = await asyncio.to_thread(db.count_active_users_since, now - timedelta(hours=1))
    since_start = await asyncio.to_thread(db.count_active_users_since, START_TIME)
    await update.message.reply_text(build_status_text(START_TIME, hour, since_start))


# ---------------------------------------------------------------------------
# /run -- reaching into another bot
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Asking an older bot for something it has never heard of
# ---------------------------------------------------------------------------
# The bots deploy as separate services, so one can be behind the others --
# a deploy that failed, or (until 2.0.0, when publish.ps1 -Skip existed) one
# held back on purpose -- and a bot on an older version answers an unknown
# family command with "Unknown command" rather than failing. That is the
# right behaviour and it stays.
#
# What it is not is a good explanation. ManagerBot is always the first thing
# to ship (it is private, so it costs nobody anything), which means it is
# routinely a version or two ahead of the bots it is asking. Typing
# /providers and being told "Unknown command 'providers'" says the bot is
# broken; the truth is that DownloaderBot has not been published since the
# command was written.
#
# ManagerBot already knows every bot's version -- it is in the heartbeat and
# on the /status board. So it can say which it is, before the round trip.
# Anything not listed here has been in the family since before the versions
# were tracked, and is never blocked.
COMMAND_SINCE = {
    "probe": "1.2.1",
    "routewatch": "2.1.0",
    "versions": "2.1.0",
    "providers": "1.2.3",
    "stars": "1.2.3",
    "crashtest": "1.2.3",
    "gift": "2.2.0",
}


def _version_key(version: str | None):
    """A sortable form of a family version, or None if it cannot be read.

    Tolerant on purpose. It has to cope with `1.2.2R` (a retouch release
    sorts after the plain patch), with `test` (what testbot/run.ps1 sets),
    and with whatever a future release invents -- and the cost of getting it
    wrong is refusing a command that would have worked. So anything it
    cannot parse reads as None, and None never blocks.
    """
    if not version:
        return None
    parts = []
    for chunk in str(version).strip().split("."):
        digits = "".join(takewhile(str.isdigit, chunk))
        if not digits:
            return None
        # The suffix orders after the bare number: 1.2.2 < 1.2.2R.
        parts.append((int(digits), chunk[len(digits):]))
    return tuple(parts)


# A flag can be as new as a command, and is more dangerous: an unknown
# command is refused, while an unknown flag is just part of the message. A
# 1.2.x bot handed `broadcast --active hello` would send every user it has
# ever seen a message beginning "--active".
FLAG_SINCE = {("broadcast", "--active"): "1.3.0", ("logs", "problems"): "1.6.0"}


def _too_old_for(bot_version: str | None, command: str, args: list[str] | None = None) -> str | None:
    """The sentence to show instead of queueing, or None to go ahead."""
    needed = COMMAND_SINCE.get(command)
    for (cmd, flag), since in FLAG_SINCE.items():
        if cmd == command and args and flag in args:
            needed = since
            command = f"{command} {flag}"
            break
    if not needed:
        return None
    have, want = _version_key(bot_version), _version_key(needed)
    if have is None or want is None or have >= want:
        return None
    return (f"That bot is on <b>{html.escape(str(bot_version))}</b> and "
            f"<code>{html.escape(command)}</code> arrived in <b>{needed}</b>, so it has "
            f"never heard of it — nothing is broken, it just has not been deployed "
            f"since. It will have it after the next release.")


def _accepted_commands(beat: "dict | None") -> set:
    """Which bus commands a bot answers, from the list it publishes in its own
    heartbeat.

    Falls back to the shared set for a bot that has never been seen, or one
    still on a version from before it advertised anything -- which is the
    honest answer for both: the shared commands are the ones every bot has
    always had, and refusing on the strength of a missing column would break
    every command against a bot that is merely old."""
    listed = (beat or {}).get("commands")
    if not listed:
        return set(family_link.COMMANDS)
    return {name.strip() for name in listed.split(",") if name.strip()}


async def _dispatch(update: Update, context: ContextTypes.DEFAULT_TYPE,
                    bot: dict, command: str, args: list[str]) -> None:
    if bot["id"] == BOT_NAME:
        await update.effective_message.reply_text(
            "That one is me. Use /status, /me, /events or /backup directly."
        )
        return
    # Asked before the round trip rather than after it, so a version gap
    # costs one lookup instead of ninety seconds and a confusing answer.
    beat = await asyncio.to_thread(db.heartbeat_of, bot["id"])

    # What that bot says it answers, rather than what this process happens to
    # have in COMMANDS. A bot can register commands of its own at startup --
    # ConvertBot's `stars`, DownloaderBot's `probe` and `providers` -- in its
    # own process, where ManagerBot cannot see them. Checking the local dict
    # refused exactly those three, which is how /stars came back as
    # "ConvertBot has no 'stars'" from the only bot that has it.
    accepted = _accepted_commands(beat)
    if command not in accepted:
        known = ", ".join(sorted(accepted))
        await update.effective_message.reply_text(
            f"{bot['name']} has no '{command}'. It answers: {known}")
        return
    stale = _too_old_for((beat or {}).get("version"), command, args)
    if stale:
        await update.effective_message.reply_text(
            f"⏳ <b>{html.escape(bot['name'])}</b> · {html.escape(command)}\n\n"
            + stale,
            parse_mode=ParseMode.HTML,
        )
        return

    command_id = await asyncio.to_thread(
        db.queue_command, bot["id"], command, " ".join(args),
        update.effective_user.id, update.effective_chat.id,
    )
    # "queued as #N", not "(#N)": the bare number read as a *result* -- an
    # acknowledgement of "errors" that says (#5) and is answered seconds later
    # with "No errors since this instance started" looks like the bot
    # contradicting itself. N is the row id of the queued command, which is
    # only ever useful for matching this line to the timeout notice that
    # quotes the same id.
    await update.effective_message.reply_text(f"→ {bot['name']} · {command} — queued as #{command_id}")


async def run_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/run <bot> <command> [args] -- the general form. The shortcuts below
    (/dbdump, /whois, /say, ...) are just this with the command baked in."""
    if not await guard(update, context):
        return
    if len(context.args) < 2:
        lines = ["Usage: /run &lt;bot&gt; &lt;command&gt; [args]", "", "<b>Bots:</b> " + bot_list_hint(), "", "<b>Commands:</b>"]
        lines += [f"  <code>{name}</code> — {desc}" for name, desc in family_link.COMMAND_HELP.items()]
        # Whatever the bots themselves say they answer beyond the shared set.
        # Read rather than listed by hand, so a bot that gains a command shows
        # up here on its next heartbeat.
        try:
            beats = await asyncio.to_thread(db.all_heartbeats)
        except Exception:
            beats = []
        extra: dict = {}
        for beat in beats:
            for name in _accepted_commands(beat) - set(family_link.COMMANDS):
                extra.setdefault(name, []).append(
                    (resolve_bot(beat["bot_id"]) or {}).get("name", beat["bot_id"]))
        for name, owners in sorted(extra.items()):
            lines.append(f"  <code>{html.escape(name)}</code> — only on "
                         f"{html.escape(', '.join(sorted(owners)))}")
        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)
        return

    bot = resolve_bot(context.args[0])
    if not bot:
        await update.message.reply_text(f"No such bot: {context.args[0]}. Known: {bot_list_hint()}")
        return
    await _dispatch(update, context, bot, context.args[1].lower(), context.args[2:])


def _shortcut(command: str, min_args: int, usage: str, default_bot: str | None = None):
    """Builds /whois, /say, /logs and friends -- each is /run with the
    command fixed and the first argument still naming the target bot.

    `default_bot` is for the commands only one bot has. /providers and
    /stars are not ambiguous, so making the owner type which bot owns them
    is asking them to remember something this file already knows. Naming the
    bot still works, and still wins, for when that stops being true.
    """
    async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await guard(update, context):
            return
        args = list(context.args)
        bot = resolve_bot(args[0]) if args else None
        if bot is not None:
            args = args[1:]
        elif default_bot:
            bot = resolve_bot(default_bot)
        if bot is None:
            if len(args) < min_args or not args:
                await update.message.reply_text(usage or f"Which bot? Known: {bot_list_hint()}")
            else:
                await update.message.reply_text(
                    f"No such bot: {args[0]}. Known: {bot_list_hint()}")
            return
        if len(args) < max(0, min_args - 1):
            await update.message.reply_text(usage)
            return
        await _dispatch(update, context, bot, command, args)
    return handler


# ---------------------------------------------------------------------------
# Asking every bot at once
# ---------------------------------------------------------------------------
# /errors <bot> was one bot's answer and /errors on its own was a usage line,
# which is the wrong way round for the question it answers. "Has anything
# broken?" is a question about the family, and making the owner ask it four
# times -- and remember four names to ask with -- is four chances to check
# three bots and forget the fourth.
#
# So a command with no bot named goes to all of them and comes back as one
# message. The machinery is the same as a family /ping: queue the command per
# bot, remember which group each queued id belongs to, and rewrite one live
# message as the answers arrive. Written generally because /errors is not the
# only command shaped this way -- anything read-only and short belongs here.

_FANOUTS: "OrderedDict[int, dict]" = OrderedDict()
MAX_FANOUTS = 64


def _remember_fanout(command_id: int, group: dict) -> None:
    _FANOUTS[command_id] = group
    while len(_FANOUTS) > MAX_FANOUTS:
        _FANOUTS.popitem(last=False)


async def _fanout(update: Update, context: ContextTypes.DEFAULT_TYPE,
                  command: str, args: list, title: str, footer: str = "") -> None:
    """Ask every child bot the same thing and gather the answers into one
    message."""
    targets = list(CHILDREN)
    live = await LiveMessage.reply_to(update.effective_message,
                                      f"{title} — asking all {len(targets)}…",
                                      parse_mode=ParseMode.HTML)
    group = {"live": live, "title": title, "command": command, "footer": footer,
             "answers": {}, "queued": set()}
    # A bot too old for the command is not queued at all: it would sit there
    # until the timeout and then be reported as down, which is the one wrong
    # answer that starts a hunt for a fault that is really a missing deploy.
    for child in targets:
        beat = await asyncio.to_thread(db.heartbeat_of, child["id"])
        stale = _too_old_for((beat or {}).get("version"), command, args)
        if stale:
            group["answers"][child["id"]] = (
                "⏳", f"on {(beat or {}).get('version') or 'an unknown version'} — "
                     f"too old for this command; it will have it after the next release.")
            continue
        group["queued"].add(child["id"])
        command_id = await asyncio.to_thread(
            db.queue_command, child["id"], command, " ".join(args),
            update.effective_user.id, update.effective_chat.id,
        )
        _remember_fanout(command_id, group)
    if not group["queued"]:
        await _render_fanout(context, group)


def _fanout_text(group: dict) -> str:
    """One section per bot, in the order the family is declared in -- which is
    the order every other list in this bot uses, and the order the owner reads
    them in. Not sorted by how bad the news is: a report whose lines move
    about between runs is one nobody can skim."""
    parts = []
    for child in CHILDREN:
        answer = group["answers"].get(child["id"])
        if answer is None:
            continue
        mark, body = answer
        parts.append(f"{mark} <b>{html.escape(child['name'])}</b>\n"
                     f"{html.escape(body.strip() or '(no output)')}")
    waiting = len(group["queued"] - set(group["answers"]))
    head = f"{group['title']}"
    if waiting > 0:
        head += f" — waiting on {waiting} more"
    text = head + "\n\n" + "\n\n".join(parts)
    # Only once everything is in: a footer that appears under a half-finished
    # list reads as though the list is finished.
    if not waiting and group.get("footer"):
        text += "\n\n" + group["footer"]
    return text


async def _render_fanout(context: ContextTypes.DEFAULT_TYPE, group: dict) -> None:
    text = _fanout_text(group)
    if len(text) <= TELEGRAM_MAX_CHARS:
        await group["live"].set(context.bot, text, parse_mode=ParseMode.HTML)
        return
    # Four bots' worth of error tails can outgrow one message. The file is
    # plain text, so the markup comes off rather than being shown as tags.
    await group["live"].set(context.bot, f"{group['title']} — the answers are attached.")
    await context.bot.send_document(
        chat_id=group["live"].chat_id,
        document=BytesIO(re.sub(r"<[^>]+>", "", text).encode("utf-8")),
        filename=f"{group['command']}.txt")


async def _deliver_fanout(context: ContextTypes.DEFAULT_TYPE, group: dict, result: dict) -> None:
    target = resolve_bot(result["target_bot"])
    key = target["id"] if target else result["target_bot"]
    if result["status"] == "timeout":
        group["answers"][key] = ("⏳", f"no answer in {COMMAND_TIMEOUT_SECONDS}s — that bot looks down.")
    else:
        group["answers"][key] = ("✅" if result["ok"] else "⚠️", result["output"] or "(no output)")
    await _render_fanout(context, group)


def _recorded_line(counts: dict, hours: int) -> str:
    """One line summarising what the shared table holds for the same window
    the live counts cover -- which is what makes the difference between the
    two commands visible without explaining it every time."""
    if not counts:
        return (f"Nothing recorded in the last {hours}h either.\n"
                f"<i>Above: since each process started, lost on a redeploy. "
                f"This line: the shared record, which is not.</i>")
    totals: dict = {}
    for per_level in counts.values():
        for level, numbers in per_level.items():
            totals[level] = totals.get(level, 0) + numbers["incidents"]
    parts = [f"{problems.LEVEL_ICONS.get(level, '•')} {count} "
             f"{problems.LEVEL_NAMES.get(level, '?')}"
             for level, count in sorted(totals.items())]
    return (f"<b>Recorded, last {hours}h:</b> " + " · ".join(parts)
            + "\n<i>Above: since each process started, lost on a redeploy. "
              "This line: the shared record, which is not. "
              "<code>/nightly</code> breaks it down, <code>/reports</code> goes back further.</i>")


async def errors_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/errors <bot> is that bot's errors since it started. /errors on its own
    is every bot's, in one message -- which is what the question usually is."""
    if not await guard(update, context):
        return
    args = list(context.args)
    if args:
        bot = resolve_bot(args[0])
        if bot is None:
            await update.message.reply_text(f"No such bot: {args[0]}. Known: {bot_list_hint()}")
            return
        await _dispatch(update, context, bot, "errors", args[1:])
        return
    try:
        counts = await asyncio.to_thread(family_link.problem_counts_since,
                                         NIGHTLY_REPORT_HOURS)
        footer = _recorded_line(counts, NIGHTLY_REPORT_HOURS)
    except Exception:
        # The live half is the half this command is named for; a database
        # that will not answer must not cost it.
        logger.debug("Could not read the recorded problem counts", exc_info=True)
        footer = ""
    await _fanout(update, context, "errors", [],
                  "⚠️ <b>Errors since each bot started</b>", footer=footer)


# ---------------------------------------------------------------------------
# /errorlog, /botlog, /problemlog -- the logs, without remembering how
# ---------------------------------------------------------------------------
# The owner asked for "easier commands to see the logs". /logs <bot> [n]
# [bot|problems] reads all three, but only for somebody who remembers the file
# names and the bot names. These carry the file in the command's name, and with
# no bot named they answer with a button per bot instead of a usage line.
# Typing the bot still works: /problemlog conv 100.

LOG_FILES = {"errorlog": "errors", "botlog": "bot", "problemlog": "problems"}
LOG_TITLES = {"errorlog": "errors.log", "botlog": "bot.log", "problemlog": "problems.log"}


def _log_picker(kind: str, lines: str = "") -> InlineKeyboardMarkup:
    buttons = [InlineKeyboardButton(b["name"], callback_data=f"logpick:{kind}:{b['id']}:{lines}")
               for b in ALL_BOTS if b["id"] != BOT_NAME]
    return InlineKeyboardMarkup([buttons[i:i + 2] for i in range(0, len(buttons), 2)])


def _log_shortcut(kind: str):
    async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await guard(update, context):
            return
        args = list(context.args)
        named = [a for a in args if not a.isdigit()]
        lines = next((a for a in args if a.isdigit()), "")
        target = resolve_bot(named[0]) if named else None
        if named and target is None:
            await update.message.reply_text(f"No such bot: {named[0]}. Known: {bot_list_hint()}")
            return
        if target is None:
            await update.message.reply_text(f"Whose {LOG_TITLES[kind]}?",
                                            reply_markup=_log_picker(kind, lines))
            return
        await _dispatch(update, context, target, "logs", ([lines] if lines else []) + [LOG_FILES[kind]])
    return handler


async def log_pick_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """A bot tapped under /errorlog, /botlog or /problemlog. The buttons stay,
    so another bot's log is one more tap."""
    query = update.callback_query
    if not await guard(update, context):
        await query.answer()
        return
    parts = (query.data or "").split(":")
    kind = parts[1] if len(parts) > 1 else ""
    target = resolve_bot(parts[2]) if len(parts) > 2 else None
    lines = parts[3] if len(parts) > 3 and parts[3].isdigit() else ""
    if kind not in LOG_FILES or target is None:
        await query.answer("That button no longer works.", show_alert=True)
        return
    await query.answer()
    await _dispatch(update, context, target, "logs", ([lines] if lines else []) + [LOG_FILES[kind]])


# ---------------------------------------------------------------------------
# /ping -- the whole round trip, leg by leg
# ---------------------------------------------------------------------------
# "Is it up?" was the old question and a word was a fine answer. The useful
# question is "where does the time go?", and answering that means measuring
# five separate hops on two machines whose clocks do not agree:
#
#   the phone -> Telegram -> here     network, plus however long the update
#                                     sat in Telegram's queue
#   here -> Supabase                  the INSERT that queues the command
#   Supabase -> the target bot        how fast it hears about it
#   the target bot                    its own work, including its own hop to
#                                     Supabase
#   Supabase -> here                  the answer coming back
#
# Two machines, one honest clock. Every cross-machine figure below is a
# difference between two Postgres timestamps -- created_at, claimed_at,
# finished_at, clock_timestamp() at pickup -- so none of them contains the
# difference between this host's idea of the time and Railway's. The
# per-machine figures (each end's own round trip to Supabase, and its clock
# skew against it) are measured locally at each end and reported separately,
# which is what makes "the database is slow from there" distinguishable from
# "that bot is slow".
#
# The one figure that is *not* exact is the first: Telegram stamps a message
# with whole seconds, so the phone-to-here leg is only good to about a
# second, and it is labelled that way rather than quietly presented as
# precise.

_PING_TRACES: "OrderedDict[int, dict]" = OrderedDict()
MAX_PING_TRACES = 64


def _remember_trace(command_id: int, trace: dict) -> None:
    _PING_TRACES[command_id] = trace
    while len(_PING_TRACES) > MAX_PING_TRACES:
        _PING_TRACES.popitem(last=False)


def _ms(delta) -> str:
    """A duration, in whichever unit makes it readable. Takes a timedelta
    (the Postgres-clock differences) or a plain number of milliseconds (the
    locally measured legs)."""
    if delta is None:
        return "     ?"
    ms = delta.total_seconds() * 1000 if hasattr(delta, "total_seconds") else float(delta)
    if ms >= 1000:
        return f"{ms / 1000:6.2f} s"
    return f"{ms:6.0f} ms"


def _row(label: str, value: str) -> str:
    return f"{label:<30}{value}"


def _describe_end(name: str, where: str, probe: dict) -> str:
    if not probe or "error" in probe:
        return _row(name, (probe or {}).get("error", "not reported"))
    skew = probe.get("skew_ms", 0)
    bits = [f"Supabase {_ms(probe.get('db_ms', 0)).strip()} per query",
            f"clock {skew:+.0f} ms"]
    return f"{name}\n  {where}\n  " + " · ".join(bits)


def _render_ping(trace: dict, result: dict) -> str:
    """The report. Everything in the first block is one Postgres clock or a
    stopwatch that started and stopped in this process; nothing in it is a
    subtraction across two machines' clocks."""
    name = trace["bot"]["name"]
    created, claimed = result.get("created_at"), result.get("claimed_at")
    finished, taken = result.get("finished_at"), result.get("taken_at")

    if result["status"] == "timeout" or claimed is None:
        return (f"🏓 <b>{html.escape(name)}</b> — no answer in {COMMAND_TIMEOUT_SECONDS}s.\n"
                f"It never picked the ping up, which means its process is not running.")

    there = {}
    try:
        there = json.loads(result.get("output") or "{}")
    except ValueError:
        pass

    lines = [
        _row("you → Telegram → ManagerBot", _ms(trace["to_manager"]) + "   ±1 s"),
        _row("ManagerBot → Telegram (ack)", _ms(trace["ack_ms"])),
        _row("ManagerBot → Supabase (queue)", _ms(trace["queue_ms"])),
        _row(f"queued → {name} claimed it", _ms(claimed - created)),
        _row(f"{name} answering", _ms(finished - claimed)),
        _row("answer → ManagerBot collected it", _ms(taken - finished)),
        "─" * 40,
        _row("bus round trip", _ms(taken - created)),
    ]

    # Named for what it is. This is the admin bus -- ManagerBot handing a
    # command to another bot through a Postgres table and waiting for the
    # answer to come back. It is not what a user waits for: their message goes
    # to the bot they are talking to directly and never touches this path. The
    # two were easy to confuse while this number was tens of seconds.
    head = (f"🏓 <b>{html.escape(name)}</b> — {_ms(taken - created).strip()} "
            f"round trip on the admin bus")
    ends = "\n\n".join([
        _describe_end("ManagerBot", trace["here"].get("where", "?"), trace["here"]),
        _describe_end(name, there.get("where", "?"), there),
    ])
    up = there.get("up")
    tail = f"\nUp {up}." if up else ""
    return (f"{head}\n\n<pre>{html.escape(chr(10).join(lines))}</pre>\n"
            f"{_render_user_wait(name, there.get('user') or {})}"
            f"<b>Each end</b>\n{html.escape(ends)}{html.escape(tail)}")


def _user_wait_ms(result: dict) -> "float | None":
    """The number the summary sorts on and shows first: what a person waits
    for this bot's own work on an info command. None when the bot is too old
    to measure it, or could not."""
    try:
        there = json.loads(result.get("output") or "{}")
    except ValueError:
        return None
    value = (there.get("user") or {}).get("total_ms")
    return float(value) if isinstance(value, (int, float)) else None


def _render_user_wait(name: str, user: dict) -> str:
    """The other half of the report, and the half anybody outside this chat
    would recognise.

    The block above it is the admin bus. This is a /start: one indexed read
    of the row that says which language somebody chose, and one trip to
    Telegram carrying the answer back. Neither is a guess -- the bot ran both
    while it was answering the ping.

    What is deliberately not added in is the person's own trip to Telegram.
    It is the largest term for somebody on mobile data and neither end can
    measure it, so it is named as missing rather than folded in at a number
    nobody chose.
    """
    if not user:
        return ""
    if "total_ms" not in user:
        trouble = user.get("info_error") or user.get("telegram_error")
        if not trouble:
            return ""
        return f"<b>What a user waits for</b>\n{html.escape(trouble)}\n\n"
    lines = [
        _row("reading their row (info)", _ms(user["info_ms"])),
        _row("handing the reply to Telegram", _ms(user["telegram_ms"])),
        "-" * 40,
        _row("an info command, here", _ms(user["total_ms"])),
    ]
    note = (f"A /start in {name}: the database read plus the send. Their own trip "
            f"to Telegram is on top of this, and cannot be measured from either end.")
    return (f"<b>What a user waits for</b>\n"
            f"<pre>{html.escape(chr(10).join(lines))}</pre>{html.escape(note)}\n\n")


async def _ping_one(update: Update, context: ContextTypes.DEFAULT_TYPE, bot: dict) -> None:
    started = time_module.perf_counter()
    now = datetime.now(timezone.utc)
    live = await LiveMessage.reply_to(update.message, f"🏓 {bot['name']} — timing the round trip…")
    ack_ms = (time_module.perf_counter() - started) * 1000

    # This end's own distance from the database, measured the same way the
    # far end measures its own -- the two are only comparable because both
    # are one round trip from the same probe.
    try:
        here = await asyncio.to_thread(family_link.ping_probe)
    except Exception as exc:
        here = {"error": f"{type(exc).__name__}: {exc}"}

    queue_started = time_module.perf_counter()
    command_id = await asyncio.to_thread(
        db.queue_command, bot["id"], "ping", "trace",
        update.effective_user.id, update.effective_chat.id,
    )
    queue_ms = (time_module.perf_counter() - queue_started) * 1000

    _remember_trace(command_id, {
        "bot": bot, "live": live, "here": here,
        "to_manager": now - update.message.date,
        "ack_ms": ack_ms, "queue_ms": queue_ms,
    })


async def broadcast_ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/ping <bot> reports the round trip leg by leg. /ping with nothing
    named asks all of them at once and reports one line each -- still the
    fastest way to tell "the database is down" from "one bot is down"."""
    if not await guard(update, context):
        return
    if context.args:
        bot = resolve_bot(context.args[0])
        if not bot:
            await update.message.reply_text(f"No such bot: {context.args[0]}. Known: {bot_list_hint()}")
            return
        await _ping_one(update, context, bot)
        return

    # Everything _render_ping needs is measured here too, per bot, even
    # though the summary shows one number each. That is what lets the
    # buttons under the result open a full leg-by-leg breakdown instantly,
    # from the ping that was actually run, rather than quietly running a
    # second one and showing different numbers than the row above it.
    started = time_module.perf_counter()
    now = datetime.now(timezone.utc)
    live = await LiveMessage.reply_to(update.message, f"🏓 Pinging all {len(CHILDREN)}…")
    ack_ms = (time_module.perf_counter() - started) * 1000
    try:
        here = await asyncio.to_thread(family_link.ping_probe)
    except Exception as exc:
        here = {"error": f"{type(exc).__name__}: {exc}"}

    group = {"live": live, "rows": {}, "expected": len(CHILDREN), "details": {}}
    for child in CHILDREN:
        queue_started = time_module.perf_counter()
        command_id = await asyncio.to_thread(
            db.queue_command, child["id"], "ping", "trace",
            update.effective_user.id, update.effective_chat.id,
        )
        _remember_trace(command_id, {
            "bot": child, "group": group, "here": here,
            "to_manager": now - update.message.date, "ack_ms": ack_ms,
            "queue_ms": (time_module.perf_counter() - queue_started) * 1000,
        })


# ---------------------------------------------------------------------------
# The buttons under a family ping
# ---------------------------------------------------------------------------
# /ping with no bot named used to end at four numbers, and getting the
# breakdown for the one that looked wrong meant typing /ping <bot> and
# waiting for a second round trip. Four commands to see four bots.
#
# The results of the ping that just ran are already in memory, so the
# breakdown costs nothing to show: one button per bot, plus one that prints
# all four. Kept per live message rather than globally, so two pings in the
# same chat do not overwrite each other's buttons.

_PING_GROUPS: "OrderedDict[tuple[int, int], dict]" = OrderedDict()
MAX_PING_GROUPS = 16


def _remember_group(group: dict) -> None:
    live = group["live"]
    _PING_GROUPS[(live.chat_id, live.message_id)] = group
    while len(_PING_GROUPS) > MAX_PING_GROUPS:
        _PING_GROUPS.popitem(last=False)


def _ping_keyboard(group: dict) -> InlineKeyboardMarkup:
    live = group["live"]
    key = f"{live.chat_id}:{live.message_id}"
    buttons = [
        InlineKeyboardButton(child["name"], callback_data=f"pingdet:{key}:{child['id']}")
        for child in CHILDREN if child["id"] in group["details"]
    ]
    rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    rows.append([InlineKeyboardButton("📋 All details", callback_data=f"pingdet:{key}:*")])
    return InlineKeyboardMarkup(rows)


async def ping_detail_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """A tap on one of those buttons. Renders from what the ping already
    collected, so the detail agrees with the summary above it by
    construction -- a second round trip would give different numbers and
    invite the question of which of the two is real."""
    if not await guard(update, context):
        return
    query = update.callback_query
    _, chat_id, message_id, which = query.data.split(":", 3)
    group = _PING_GROUPS.get((int(chat_id), int(message_id)))
    if group is None:
        # Only after a redeploy, or sixteen pings ago. Say so rather than
        # showing an empty report.
        await query.answer("That ping is no longer in memory — run /ping again.",
                           show_alert=True)
        return

    wanted = [c for c in CHILDREN
              if (which == "*" or which == c["id"]) and c["id"] in group["details"]]
    if not wanted:
        await query.answer("Nothing was recorded for that one.", show_alert=True)
        return
    await query.answer()

    body = "\n\n".join(_render_ping(*group["details"][c["id"]]) for c in wanted)
    if len(body) <= TELEGRAM_MAX_CHARS:
        await context.bot.send_message(chat_id=query.message.chat_id, text=body,
                                       parse_mode=ParseMode.HTML)
        return
    # Four full reports can outgrow one message. The file is plain text, so
    # the markup is stripped rather than shown as tags.
    await send_long(context, query.message.chat_id,
                    re.sub(r"<[^>]+>", "", body), filename="ping.txt")


def _summary_lines(rows: dict) -> list:
    """The family ping's table: one bot a line, fastest first.

    Alphabetical was the order until the owner asked for this, and
    alphabetical is the one order that hides the answer. Four numbers are
    being compared precisely because one of them might be the odd one out,
    and finding it meant reading all four, every time.

    Sorted on what a person would actually wait for, falling back to the bus
    figure for a bot too old to report one. A bot that never answered sorts
    last whatever it is called: it has no time at all, and putting it first
    because its name begins with A is the same mistake in a new order.
    """
    def key(item):
        row = item[1]
        if row.get("user") is not None:
            return (0, row["user"])
        if row.get("bus") is not None:
            return (1, row["bus"])
        return (2, 0.0)

    lines = []
    for name, row in sorted(rows.items(), key=key):
        if row.get("note"):
            lines.append(f"{name:<14}{row['note']}")
            continue
        user = _ms(row["user"]) if row.get("user") is not None else "     ?"
        lines.append(f"{name:<14}{user} user   {_ms(row['bus'])} bus")
    return lines


async def _deliver_ping(context: ContextTypes.DEFAULT_TYPE, trace: dict, result: dict) -> None:
    """A ping's answer replaces the message that announced it, rather than
    arriving underneath -- and moves to the bottom of the chat by itself if
    anything was said in the meantime (see live_message.py)."""
    group = trace.get("group")
    if group is None:
        await trace["live"].set(context.bot, _render_ping(trace, result), parse_mode=ParseMode.HTML)
        return

    name = trace["bot"]["name"]
    if result["status"] == "timeout" or not result.get("claimed_at"):
        group["rows"][name] = {"user": None, "bus": None, "note": "no answer — down"}
    else:
        group["rows"][name] = {
            "user": _user_wait_ms(result),
            "bus": (result["taken_at"] - result["created_at"]).total_seconds() * 1000,
            "note": "",
        }
    # Kept whole, so the buttons below can print the breakdown without
    # asking the bot anything a second time.
    group["details"][trace["bot"]["id"]] = (trace, result)
    lines = _summary_lines(group["rows"])
    missing = group["expected"] - len(group["rows"])
    text = f"🏓 <b>Family ping</b>\n<pre>{html.escape(chr(10).join(lines))}</pre>"
    keyboard = None
    if missing > 0:
        text += f"\nWaiting on {missing} more (down after {COMMAND_TIMEOUT_SECONDS}s)."
    else:
        text += ("\n<b>user</b> is an info command answered on the spot — one database "
                 "read and the send — which is the path everybody except this chat "
                 "takes. <b>bus</b> is the admin round trip through Postgres, which "
                 "nobody using a bot ever takes. Fastest first; tap a bot for its "
                 "breakdown.")
        _remember_group(group)
        keyboard = _ping_keyboard(group)
    await group["live"].set(context.bot, text, parse_mode=ParseMode.HTML,
                            reply_markup=keyboard)


# ---------------------------------------------------------------------------
# Reading the shared database directly
# ---------------------------------------------------------------------------

async def users_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """No command queue involved -- this is read straight out of the shared
    database, so it answers for bots that are currently down too."""
    if not await guard(update, context):
        return
    hours = int(context.args[0]) if context.args and context.args[0].isdigit() else 24
    since = datetime.now(timezone.utc) - timedelta(hours=hours)

    by_schema = await asyncio.to_thread(
        db.active_users_by_schema, [b["schema"] for b in ALL_BOTS], since, True
    )
    lines = [f"👥 Active users, last {hours}h (all-time known in brackets)", ""]
    for bot in ALL_BOTS:
        counts = by_schema.get(bot["schema"])
        if counts is None:
            lines.append(f"  {bot['name']}: — (no tables yet)")
        else:
            lines.append(f"  {bot['name']}: {counts[0]}  [{counts[1]}]")
    # Counted now, not taken from the 15-minute job's last answer: this is the
    # figure to look at when deciding whether the 220 gift can start.
    try:
        people = await asyncio.to_thread(db.unique_people, [b["schema"] for b in CHILDREN])
        lines.append(f"👤 Unique people across all bots: {people} (the gift's mark is {family_link.PROMO_USERS})")
    except Exception as exc:
        lines.append(f"👤 Unique people across all bots: could not count ({type(exc).__name__})")
    await update.message.reply_text("\n".join(lines))


async def donations_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update, context):
        return

    by_schema = await asyncio.to_thread(
        db.donations_by_schema, [b["schema"] for b in ALL_BOTS]
    )
    lines = ["💝 Paid donations, per bot", ""]
    any_paid = False
    for bot in ALL_BOTS:
        per_currency = by_schema.get(bot["schema"])
        if not per_currency:
            continue
        name = bot["name"]
        for currency, count, total in per_currency:
            any_paid = True
            unit = "⭐" if currency == "XTR" else f" {currency} (minor units)"
            lines.append(f"  {name}: {total}{unit} over {count} payment(s)")
    if not any_paid:
        lines.append("  Nothing paid yet, anywhere.")
    await update.message.reply_text("\n".join(lines))


# ---------------------------------------------------------------------------
# Credit balances
# ---------------------------------------------------------------------------
# The family charges in credits and holds a balance per person, in
# family.star_balances (see family_link.py, which owns the arithmetic). Before
# this the only way to look at one was Supabase's table editor and the only
# way to change one was a hand-written UPDATE -- which is the sort of thing
# that goes wrong at 2 a.m. and leaves no record of who did it.
#
# Everything here goes through family_link, so an owner adjusting a balance
# writes the same kind of ledger row a top-up does and the history stays
# readable. There is no "just set the number" path that skips the ledger.

def _parse_balance_change(arg: str):
    """`+100`, `-50` or `=0` -> ("add"/"set", amount). None if it is not one.

    Deliberately three explicit forms rather than a bare number. A bare
    number is ambiguous in the one direction that matters -- `/balance 12345
    500` could plausibly mean "give them 500" or "make it 500" -- and the two
    differ by however much they already had.
    """
    if len(arg) < 2 or arg[0] not in "+-=":
        return None
    try:
        amount = int(arg[1:])
    except ValueError:
        return None
    if arg[0] == "=":
        return ("set", amount)
    return ("add", amount if arg[0] == "+" else -amount)


def _ledger_lines(rows) -> list:
    out = []
    for row in rows:
        when = row["occurred_at"].strftime("%Y-%m-%d %H:%M")
        sign = "+" if row["delta"] > 0 else ""
        paid = f", {row['stars_paid']} ⭐ paid" if row["stars_paid"] else ""
        detail = f" — {html.escape(str(row['detail']))}" if row["detail"] else ""
        out.append(f"  {when}  {sign}{row['delta']} ⚡ → {row['balance_after']} ⚡"
                   f"  [{row['reason']} via {row['bot_id']}{paid}]{detail}")
    return out


# How many rows /balance shows: the largest balances in the family overview, and
# the latest movements of one person. The balance page under /help prints them.
BALANCE_OVERVIEW_ROWS = 20
BALANCE_HISTORY_ROWS = 12


async def balance_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/balance                -- the family-wide picture
       /balance <user_id>      -- one person, with their last movements
       /balance <user_id> +100 -- give them 100 credits
       /balance <user_id> -100 -- take 100 away (may go negative)
       /balance <user_id> =0   -- set it to exactly 0
    """
    if not await guard(update, context):
        return
    args = context.args or []

    if not args:
        totals, rows = await asyncio.to_thread(family_link.star_balance_overview, BALANCE_OVERVIEW_ROWS)
        lines = [
            "⚡ Credit balances, whole family",
            "",
            f"  Outstanding: {totals['outstanding']} ⚡ across {totals['wallets']} wallet(s)",
            f"  Ever credited: {totals['topped_up']} ⚡   ever spent: {totals['spent']} ⚡",
            f"  Real money behind it: {totals['stars_paid']} ⭐",
            f"  Bonus credit still unspent (expires if unused): {totals['bonus']} ⚡",
            f"  Rate: 1 ⭐ = {family_link.credit_for_stars(1)} ⚡ · ladder: "
            + (", ".join(f"next {size} ⭐ at {mult:g}x" for size, mult in family_link.TOPUP_LADDER) or "off")
            + f" · bonus expires after {family_link.BONUS_EXPIRY_DAYS} days",
        ]
        if rows:
            lines += ["", "Largest balances:"]
            for row in rows:
                lines.append(f"  <code>{row['user_id']}</code>  {row['balance']} ⚡"
                             f"  (in {row['topped_up']}, out {row['spent']},"
                             f" paid {row['stars_paid']} ⭐)")
        else:
            lines += ["", "  Nobody holds any credit yet."]
        lines += ["", "One person: /balance &lt;user_id&gt; · "
                      "change it: /balance &lt;user_id&gt; +100"]
        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)
        return

    if not args[0].lstrip("-").isdigit():
        await update.message.reply_text(
            "Usage: /balance [user_id] [+100 | -100 | =0]\n"
            "A numeric Telegram user id — /whois &lt;bot&gt; &lt;id&gt; if you need "
            "to check who it is.",
            parse_mode=ParseMode.HTML)
        return
    user_id = int(args[0])

    if len(args) > 1:
        change = _parse_balance_change(args[1])
        if change is None:
            await update.message.reply_text(
                f"Didn't understand <code>{html.escape(args[1])}</code>. Use +100 to add, "
                "-100 to take away, or =100 to set it exactly.",
                parse_mode=ParseMode.HTML)
            return
        kind, amount = change
        who = update.effective_user
        detail = f"by admin {who.id}" + (f" (@{who.username})" if who.username else "")
        if kind == "set":
            after = await asyncio.to_thread(
                family_link.set_star_balance, user_id, amount, "adjustment", detail)
            what = f"set to {after} ⚡"
        else:
            after = await asyncio.to_thread(
                family_link.move_stars, user_id, amount, "adjustment", detail)
            what = f"{'+' if amount > 0 else ''}{amount} ⚡ → {after} ⚡"
        # Into the family log too, so a change shows up in /events beside
        # everything else that happened tonight.
        await asyncio.to_thread(
            family_link.report_event, "info", "payment",
            f"Balance of {user_id} {what} by admin {who.id}")
        await update.message.reply_text(
            f"⚡ <code>{user_id}</code>: {what}", parse_mode=ParseMode.HTML)

    totals = await asyncio.to_thread(family_link.star_totals, user_id)
    rows = await asyncio.to_thread(family_link.star_ledger_for, user_id, BALANCE_HISTORY_ROWS)
    lines = [
        f"⚡ <code>{user_id}</code>",
        "",
        f"  Balance: {totals['balance']} ⚡",
        f"  Credited over time: {totals['topped_up']} ⚡   spent: {totals['spent']} ⚡",
        f"  Actually paid: {totals['stars_paid']} ⭐",
        f"  Bonus credit: {totals['bonus']} ⚡"
        + (f", next {totals['bonus_next_amount']} ⚡ expires {totals['bonus_next_expiry']:%Y-%m-%d}"
           if totals["bonus"] else ""),
    ]
    if rows:
        lines += ["", "Last movements:"] + _ledger_lines(rows)
    else:
        lines += ["", "  No movements recorded."]
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


async def addcredit_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/addcredit <user_id> <amount> -- put ⚡ into anybody's balance by id.

    The owner's answer to "give my account 10k": not a seed that runs on its
    own, but a lever to pull for any account, on purpose, with a record. It
    is /balance <id> +N under a name nobody has to remember the syntax of.

    Recorded as a grant rather than a bonus, so it never expires, and in the
    family log so it shows up in /events beside everything else.
    """
    if not await guard(update, context):
        return
    args = context.args or []
    amount_text = args[1].lstrip("+") if len(args) == 2 else ""
    if len(args) != 2 or not args[0].isdigit() or not amount_text.isdigit() or int(amount_text) <= 0:
        await update.message.reply_text(
            "Usage: /addcredit &lt;user_id&gt; &lt;amount&gt;\n"
            "Adds that many ⚡ to the account. It does not expire. "
            "To take credit away or set an exact number, use /balance.",
            parse_mode=ParseMode.HTML)
        return
    user_id, amount = int(args[0]), int(amount_text)
    who = update.effective_user
    detail = f"added by admin {who.id}" + (f" (@{who.username})" if who.username else "")
    after = await asyncio.to_thread(family_link.move_stars, user_id, amount, "grant", detail)
    await asyncio.to_thread(
        family_link.report_event, "info", "payment",
        f"+{amount} credit added to {user_id} by admin {who.id}, balance {after}")
    await update.message.reply_text(
        f"⚡ +{amount} added to <code>{user_id}</code>. Balance now {after} ⚡.",
        parse_mode=ParseMode.HTML)


# /events: how many it shows when no number is given, and the most it will.
EVENTS_DEFAULT = 15
EVENTS_MAX = 60


async def events_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update, context):
        return
    limit = EVENTS_DEFAULT
    bot_id = None
    for arg in context.args:
        if arg.isdigit():
            limit = min(int(arg), EVENTS_MAX)
        else:
            match = resolve_bot(arg)
            if match:
                bot_id = match["id"]
    rows = await asyncio.to_thread(db.recent_events, limit, bot_id)
    if not rows:
        await update.message.reply_text("Nothing recorded yet.")
        return
    icons = {"info": "·", "warning": "⚠️", "error": "🔥", "critical": "🚨"}
    lines = [f"🗒 Last {len(rows)} event(s)" + (f" from {bot_id}" if bot_id else ""), ""]
    for row in rows:
        stamp = row["occurred_at"].strftime("%m-%d %H:%M")
        lines.append(f"{icons.get(row['level'], '·')} {stamp} [{row['bot_id']}] {row['message']}")
    await send_long(context, update.effective_chat.id, "\n".join(lines), "events.txt")


async def sql_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/sql SELECT ... -- read-only, against the whole shared database, so
    joins across two bots' schemas work. The connection itself is set READ
    ONLY, so Postgres rejects a write even if the keyword check below is
    fooled."""
    if not await guard(update, context):
        return
    query = update.message.text.partition(" ")[2].strip()
    if not query:
        await update.message.reply_text(
            'Usage: /sql SELECT ...\n\ne.g. /sql SELECT count(*) FROM sticker_bot.packs'
        )
        return
    if query.lower().split()[0] not in ("select", "with", "table", "explain", "show"):
        await update.message.reply_text("Read-only: start with SELECT, WITH, TABLE, EXPLAIN or SHOW.")
        return
    try:
        cols, rows = await asyncio.to_thread(db.run_readonly_query, query)
    except Exception as exc:
        await update.message.reply_text(f"⚠️ {type(exc).__name__}: {exc}")
        return
    if not rows:
        await update.message.reply_text("No rows.")
        return
    body = [" | ".join(cols), "-" * 40]
    body += [" | ".join("NULL" if v is None else str(v) for v in row) for row in rows]
    body.append(f"\n({len(rows)} row(s), capped at {db.SQL_MAX_ROWS})")
    await send_long(context, update.effective_chat.id, "\n".join(body), "query.txt")


# ---------------------------------------------------------------------------
# Backups
# ---------------------------------------------------------------------------

# What one document may be. The cloud Bot API refuses a bot's upload over
# 50 MB, and the multipart request around the file costs a little of that.
BACKUP_PART_BYTES = int(os.environ.get("MBOT_BACKUP_PART_MB") or 49) * 1024 * 1024


def _backup_parts(size: int, limit: "int | None" = None) -> list:
    """(start, end) byte ranges of at most `limit` bytes, covering `size`."""
    limit = limit or BACKUP_PART_BYTES
    if size <= limit:
        return [(0, size)]
    return [(start, min(start + limit, size)) for start in range(0, size, limit)]


def _build_backup(out_path: Path) -> dict:
    """family_db.backup into `out_path`, and what the caption says about it.

    Imported here rather than at the top: family_db is a command-line tool
    that happens to live in this folder, and nothing else in the bot needs
    it loaded."""
    import family_db
    try:
        family_db.backup(db.DATABASE_URL, out_path, None)
    except SystemExit as exc:
        # Its command-line way of saying "no tables at all".
        raise RuntimeError(str(exc) or "nothing to back up") from None
    with zipfile.ZipFile(out_path) as archive:
        manifest = json.loads(archive.read("MANIFEST.json"))
    digest = hashlib.sha256()
    with open(out_path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    tables = manifest.get("tables") or []
    return {"tables": len(tables), "rows": sum(t.get("approx_rows", 0) for t in tables),
            "size": out_path.stat().st_size, "sha256": digest.hexdigest()}


async def backup_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """The whole shared database, every schema, as the archive family_db.py
    restores from -- sent here, and pinned.

    Lossless: each table is Postgres's own COPY into CSV, so binary columns,
    NULLs, timestamps and JSON come back exactly, compressed with DEFLATE and
    nothing thrown away. MANIFEST.json names every table's columns, which is
    what lets a restore line up against a database that has gained columns
    since. It replaces the old zip, which summarised binary columns and could
    be read but not restored from.

    Redeploying from it: start each bot once against the new database so the
    tables exist, then
        python manager_bot/family_db.py restore --into <url> --file <zip> --mode replace
    DEPLOY.md, "Restoring from a pinned backup", has the whole of it.

    Pinned with no notification, and nothing in this bot ever unpins -- so
    every backup stays one tap away at the top of this chat, the newest and
    all the ones before it. tests/test_backup.py holds that to the source.
    Over BACKUP_PART_BYTES it goes as numbered parts, each pinned, joined
    back with `copy /b` or `cat`.
    """
    if not await guard(update, context):
        return
    chat_id = update.effective_chat.id
    note = await update.message.reply_text("Backing up the whole database…")
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M")
    name = f"botfamily_{stamp}.zip"
    with tempfile.TemporaryDirectory(prefix="mbot-backup-") as folder:
        path = Path(folder) / name
        try:
            info = await asyncio.to_thread(_build_backup, path)
        except Exception as exc:
            logger.exception("Backup failed")
            await edit_in_place(note, context.bot, f"⚠️ Backup failed: {type(exc).__name__}: {exc}")
            return

        parts = _backup_parts(info["size"])
        summary = (f"{info['size'] / (1024 * 1024):.1f} MB · {info['tables']} tables · "
                   f"~{info['rows']} rows\nsha256 {info['sha256']}")
        pinned = 0
        for index, (start, end) in enumerate(parts, 1):
            with open(path, "rb") as handle:
                handle.seek(start)
                chunk = handle.read(end - start)
            if len(parts) == 1:
                filename = name
                caption = "🗄 Database backup — lossless, restorable\n" + summary
            else:
                filename = f"{name}.{index:03d}"
                caption = (f"🗄 Database backup — part {index} of {len(parts)}\n"
                           + summary + "\n(the checksum is of the joined file)")
            try:
                sent = await context.bot.send_document(
                    chat_id=chat_id, document=BytesIO(chunk), filename=filename,
                    caption=caption, read_timeout=300, write_timeout=300)
            except Exception as exc:
                logger.exception("Could not send backup part %s", index)
                await edit_in_place(note, context.bot,
                                    f"⚠️ The backup was made but part {index} of {len(parts)} "
                                    f"could not be sent: {type(exc).__name__}: {exc}")
                return
            try:
                await context.bot.pin_chat_message(
                    chat_id=chat_id, message_id=sent.message_id, disable_notification=True)
                pinned += 1
            except Exception:
                logger.warning("Could not pin backup part %s", index, exc_info=True)

    lines = [f"✅ Backed up and pinned ({pinned} of {len(parts)} pinned)." if pinned < len(parts)
             else ("✅ Backed up and pinned." if len(parts) == 1
                   else f"✅ Backed up in {len(parts)} parts, all pinned.")]
    if len(parts) > 1:
        lines.append(f"Join them first: <code>copy /b {name}.001+{name}.002 {name}</code> "
                     f"on Windows, or <code>cat {name}.0* &gt; {name}</code>.")
    lines.append("To restore: start each bot once against the new database, then "
                 "<code>python manager_bot/family_db.py restore --into &lt;url&gt; "
                 f"--file {name} --mode replace</code>. DEPLOY.md has the details.")
    await edit_in_place(note, context.bot, "\n".join(lines), parse_mode=ParseMode.HTML)


# ---------------------------------------------------------------------------
# Problem reports
# ---------------------------------------------------------------------------

async def reports_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/reports [n] [mine] -- every problem the public bots have shown anybody,
    newest first.

    It used to list only the ones somebody chose to report, which made it a
    list of how many people could be bothered rather than a list of what is
    going wrong. Since 1.7.0 each bot records a problem the moment it shows
    it, so this is the whole picture; `mine` narrows it to the ones somebody
    attached their own details to, which are the ones worth a reply.

    A row here carries nothing about anybody. The details a person volunteered
    are read one at a time with /report <incident>, which is the point: they
    are looked up on purpose, not pushed at the owner in a list.
    """
    if not await guard(update, context):
        return
    args = [a.lower() for a in context.args]
    shared_only = any(a in ("mine", "shared", "sent") for a in args)
    limit = next((int(a) for a in args if a.isdigit()), 15)
    rows = await asyncio.to_thread(family_link.recent_problem_reports,
                                   max(1, min(limit, 50)), shared_only)
    if not rows:
        await update.message.reply_text(
            "Nobody has attached their details to a problem yet." if shared_only
            else "No problems recorded yet.")
        return
    lines = ["🐞 " + ("Problems somebody put their name to" if shared_only
                      else "Latest problems, whether reported or not"), ""]
    for row in rows:
        problem = problems.PROBLEMS.get(row["code"])
        seen = f" ×{row['seen']}" if (row.get("seen") or 1) > 1 else ""
        mark = "🙋" if row.get("shared_at") else "  "
        lines.append(f"{mark}{_row_icon(row)} {row['reported_at']:%m-%d %H:%M} · {row['bot_id']} · "
                     f"{row['code']}{seen} ({row['incident']}) — "
                     f"{problem.title if problem else 'unknown code'}")
    kinds = " · ".join(f"{icon} {heading.lower()}"
                       for icon, heading, _ in (problems.KIND_LABELS[k] for k in problems.KIND_ORDER))
    lines += ["", "🚨 urgent (messaged when it happened) · ⚠️ fault",
              f"and where the bot was right and said why: {kinds}",
              "🙋 somebody attached their own details — /report <incident>",
              "What a code means: /decode <code>",
              f"The last {NIGHTLY_REPORT_HOURS}h, laid out: /nightly"]
    await send_long(context, update.effective_chat.id, "\n".join(lines), "reports.txt")


async def report_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/report <incident> -- one problem in full, including whoever
    volunteered to be named on it.

    This is the only place the details a person sent are shown, and it takes a
    deliberate command to get here. The alert that says a report arrived does
    not carry them: the standing rule is that nothing arrives at the owner's
    Telegram identifying a user, and somebody consenting to be identified buys
    the owner the ability to look, not a notification they did not ask for.
    """
    if not await guard(update, context):
        return
    if not context.args:
        await update.message.reply_text("Usage: /report <incident> — the six-character "
                                        "code beside a problem, e.g. /report JNKS4N")
        return
    incident = context.args[0].strip().upper()
    row = await asyncio.to_thread(family_link.problem_report_by_incident, incident)
    if row is None:
        await update.message.reply_text(
            f"No problem recorded as {incident}. /reports lists the recent ones.")
        return
    lines = [f"🐞 <b>{html.escape(row['incident'])}</b> · {html.escape(row['bot_id'])} · "
             f"{html.escape(row['code'])}", ""]
    if row.get("occurred_at"):
        lines.append(f"Happened: {row['occurred_at']:%Y-%m-%d %H:%M} UTC")
    lines.append(f"Recorded: {row['reported_at']:%Y-%m-%d %H:%M} UTC")
    lines.append(f"Version: {html.escape(str(row.get('version') or '?'))}")
    level = int(row.get("level") or problems.level(row["code"]))
    lines.append(f"Level: {_row_icon(row)} {_row_level_name(row)}"
                 + (" — the owner was messaged when it happened" if level == problems.URGENT
                    else " — in the nightly report"))
    if (row.get("seen") or 1) > 1:
        lines.append(f"Shown {row['seen']} times")
    lines.append("")
    if row.get("shared_at"):
        who = f"id {row['user_id']}"
        if row.get("username"):
            who += f" (@{html.escape(str(row['username']))})"
        lines += [f"🙋 <b>Sent by</b> {who}",
                  f"Language {html.escape(str(row.get('user_lang') or '?'))} · "
                  f"{html.escape(str(row.get('chat_kind') or '?'))} chat · "
                  f"attached {row['shared_at']:%Y-%m-%d %H:%M} UTC", ""]
    else:
        lines += ["Nobody attached their details to this one — it was recorded "
                  "automatically, and holds nothing about who hit it.", ""]
    lines.append(html.escape(problems.decode(row["code"])))
    text = "\n".join(lines)
    if len(text) > TELEGRAM_MAX_CHARS:
        await send_long(context, update.effective_chat.id,
                        re.sub(r"<[^>]+>", "", text), f"{incident}.txt")
        return
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


# ---------------------------------------------------------------------------
# The nightly problem report
# ---------------------------------------------------------------------------
# Everything the four bots showed anybody in the last day, in one message, at
# 22:00 where the owner is (midnight until 2.1.0). The owner: "At the end of
# the day, at around midnight by seoul/suwon time, managerbot should
# automatically send me a full reply to the report command."
#
# It is laid out by how much each line is worth reading, which is what the
# levels are for:
#
#   urgent     listed one by one, with their incidents. These have already
#              been messaged as they happened -- this is the recap, and seeing
#              the same three incidents again is the point, not a duplicate.
#   faults     grouped by bot and code with a count, because "DL-ALL-ROUTES
#              x9" is the useful shape and nine separate lines is not.
#   explained  the bot was right and said why. Until 2.1.0 this was one line,
#              "Refused — 2", which told the owner somebody had been turned
#              down when what had happened was two people forgetting to
#              swipe-reply. Now each KIND (problems.KINDS) is named for what
#              it is, with its codes, their titles and their counts -- never
#              their incidents, which nobody needs for these.
#
# Then the feedback, when the owner has asked for it here rather than as it
# arrives (/feedback).
#
# A day with nothing in it still sends, briefly. "Nothing broke today" is
# information when it arrives every day at the same time; it is only noise
# when it is the only thing that ever arrives, and the two lines above make
# sure it is not.

MAX_INCIDENTS_LISTED = 4
# Codes listed under one kind before the rest are summed into a line.
MAX_CODES_PER_KIND = 6


def _row_icon(row) -> str:
    """🚨 / ⚠️ for the first two levels; for the third, its kind's own."""
    level = int(row.get("level") or problems.level(row["code"]))
    kind = problems.kind(row["code"]) if level == problems.REFUSED else None
    return problems.KIND_LABELS[kind][0] if kind else problems.LEVEL_ICONS.get(level, "•")


def _row_level_name(row) -> str:
    level = int(row.get("level") or problems.level(row["code"]))
    kind = problems.kind(row["code"]) if level == problems.REFUSED else None
    return problems.KIND_LABELS[kind][1].lower() if kind else problems.LEVEL_NAMES.get(level, "?")


def _group_problems(rows: list) -> dict:
    """{level: {(bot, code): [rows]}}, newest first within each group."""
    grouped: dict = {}
    for row in rows:
        level = int(row.get("level") or problems.level(row["code"]))
        grouped.setdefault(level, {}).setdefault((row["bot_id"], row["code"]), []).append(row)
    return grouped


def _bot_name(bot_id: str) -> str:
    found = resolve_bot(bot_id)
    return found["name"] if found else bot_id


# DownloaderBot's daily route check (downloader_bot/routewatch.py) keeps what it
# found in family.settings: "routewatch:last_run" = when, and
# "routewatch:state:<platform>" = "<state>|<since>|<alerted>". It pushes only a
# platform's trouble starting, and never YouTube's; the owner asked for every
# platform's standing, YouTube included, in the 22:00 report instead (2026-10-07).
ROUTES_PREFIX = "routewatch:"
ROUTES_ORDER = ("instagram", "tiktok", "twitter", "pinterest", "youtube")
ROUTES_NAMES = {"instagram": "Instagram", "tiktok": "TikTok", "twitter": "X",
                "pinterest": "Pinterest", "youtube": "YouTube"}
ROUTES_LOOK = {"up": "✅", "last_resort": "⚠️ last route only", "down": "❌ down",
               "sample_gone": "❔ sample post gone"}
# The check runs once a day; past this, the line says the check itself stopped.
ROUTES_STALE_S = 2 * 86400


def _ago(seconds: float) -> str:
    hours = seconds / 3600
    return f"{hours:.0f}h" if hours < 48 else f"{hours / 24:.0f}d"


def routes_section(settings: dict, now: float) -> "list[str]":
    """The report's download-routes lines, from the route check's settings. Pure."""
    try:
        last = float(settings.get(ROUTES_PREFIX + "last_run") or "")
    except ValueError:
        last = None
    if last is None:
        return ["\n🛣 <b>Download routes</b> — not checked yet "
                "(<code>/run downloaderbot routewatch</code>)"]
    states = {}
    for key, value in settings.items():
        if not key.startswith(ROUTES_PREFIX + "state:"):
            continue
        parts = (value or "").split("|")
        try:
            states[key[len(ROUTES_PREFIX + "state:"):]] = (parts[0], float(parts[1]))
        except (IndexError, ValueError):
            continue
    head = f"\n🛣 <b>Download routes</b> · checked {_ago(now - last)} ago"
    if now - last > ROUTES_STALE_S:
        head += " — ⚠️ the daily check has stopped running"
    order = [p for p in ROUTES_ORDER if p in states] + sorted(p for p in states if p not in ROUTES_ORDER)
    shown = []
    for platform in order:
        state, since = states[platform]
        look = ROUTES_LOOK.get(state, html.escape(state))
        if state != "up" and now - since >= 86400:
            look += f" for {_ago(now - since)}"
        shown.append(f"{html.escape(ROUTES_NAMES.get(platform, platform))} {look}")
    return [head, "  " + " · ".join(shown)] if shown else [head]


def build_problem_report(rows: list, hours: int, feedback: "list | None" = None,
                         feedback_note: "str | None" = None, routes: "dict | None" = None,
                         now: "float | None" = None) -> str:
    """The message. Pure, so tests/test_reporting.py can read it without a
    database or a Telegram. `feedback` is what to show in full, and
    `feedback_note` a line to say instead when it has already gone out.
    `routes` is the route check's settings (see routes_section); None leaves
    the section out."""
    grouped = _group_problems(rows)
    signed = [row for row in rows if row.get("shared_at")]
    lines = [f"🌙 <b>Problems, last {hours}h</b>"]

    urgent = grouped.get(problems.URGENT, {})
    if urgent:
        total = sum(len(group) for group in urgent.values())
        lines.append(f"\n🚨 <b>Urgent — {total}</b>")
        for (bot_id, code), group in sorted(urgent.items()):
            problem = problems.PROBLEMS.get(code)
            for row in group:
                mark = " 🙋" if row.get("shared_at") else ""
                lines.append(
                    f"  {html.escape(_bot_name(bot_id))} · <code>{html.escape(code)}</code> "
                    f"({html.escape(row['incident'])}) {row['reported_at']:%H:%M} — "
                    f"{html.escape(problem.title if problem else 'unknown code')}{mark}")

    faults = grouped.get(problems.FAULT, {})
    if faults:
        total = sum(len(group) for group in faults.values())
        bots = len({bot_id for bot_id, _ in faults})
        lines.append(f"\n⚠️ <b>Faults — {total} across {bots} bot(s)</b>")
        for (bot_id, code), group in sorted(faults.items(), key=lambda item: -len(item[1])):
            problem = problems.PROBLEMS.get(code)
            shown = [row["incident"] for row in group[:MAX_INCIDENTS_LISTED]]
            more = len(group) - len(shown)
            incidents = ", ".join(html.escape(incident) for incident in shown)
            if more:
                incidents += f", +{more}"
            mark = " 🙋" if any(row.get("shared_at") for row in group) else ""
            lines.append(
                f"  {html.escape(_bot_name(bot_id))} · <code>{html.escape(code)}</code> "
                f"×{len(group)} — {html.escape(problem.title if problem else 'unknown code')}"
                f"{mark}\n      {incidents}")

    by_kind: dict = {}
    for (bot_id, code), group in grouped.get(problems.REFUSED, {}).items():
        by_kind.setdefault(problems.kind(code) or problems.GUIDED, []).append((bot_id, code, group))
    for kind in problems.KIND_ORDER:
        entries = sorted(by_kind.get(kind, ()), key=lambda e: (-len(e[2]), e[0], e[1]))
        if not entries:
            continue
        icon, heading, meaning = problems.KIND_LABELS[kind]
        total = sum(len(group) for _, _, group in entries)
        lines.append(f"\n{icon} <b>{html.escape(heading)} — {total}</b>")
        for bot_id, code, group in entries[:MAX_CODES_PER_KIND]:
            problem = problems.PROBLEMS.get(code)
            count = f" ×{len(group)}" if len(group) > 1 else ""
            lines.append(
                f"  {html.escape(_bot_name(bot_id))} · <code>{html.escape(code)}</code>{count} — "
                f"{html.escape(problem.title if problem else 'unknown code')}")
        rest = entries[MAX_CODES_PER_KIND:]
        if rest:
            lines.append(f"  +{sum(len(group) for _, _, group in rest)} more, under "
                         f"{len(rest)} other code(s) — <code>/reports</code>")
        lines.append(f"  <i>{html.escape(meaning)}</i>")

    if not rows:
        lines.append("\nNothing at all — no bot showed anybody a problem today.")

    if routes is not None:
        lines += routes_section(routes, time_module.time() if now is None else now)

    if feedback:
        lines.append(f"\n💬 <b>Feedback — {len(feedback)}</b> · read only")
        lines += [_feedback_block(item) for item in feedback]
    elif feedback_note:
        lines.append(f"\n💬 {html.escape(feedback_note)}")

    if signed:
        incidents = ", ".join(f"<code>{html.escape(row['incident'])}</code>" for row in signed[:8])
        lines.append(f"\n🙋 <b>{len(signed)} attached their own details</b>: {incidents}"
                     f"\n  <code>/report &lt;incident&gt;</code> to see what they sent.")

    lines.append("\n<code>/reports</code> for the longer history, "
                 "<code>/errors</code> for what is happening right now.")
    return "\n".join(lines)


async def nightly_problem_report(context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        rows = await asyncio.to_thread(
            family_link.recent_problem_reports_since, NIGHTLY_REPORT_HOURS)
    except Exception as exc:
        await notify_owner(context, f"🌙 Nightly problem report failed: {html.escape(str(exc))}")
        return
    # Whatever feedback nobody has been sent yet goes out here, in either
    # mode: in "nightly" that is all of it, and in "instant" it is only what
    # arrived while alerts were muted, which this is the place to catch.
    feedback, note = [], None
    try:
        feedback = await asyncio.to_thread(family_link.take_undelivered_feedback, 200)
        if not feedback and await _feedback_mode() == family_link.FEEDBACK_INSTANT:
            count = await asyncio.to_thread(family_link.feedback_count_since, NIGHTLY_REPORT_HOURS)
            if count:
                note = (f"{count} feedback message(s) in the last {NIGHTLY_REPORT_HOURS}h, each "
                        f"sent to you as it came — /feedback to read them again.")
    except Exception:
        logger.warning("Couldn't collect feedback for the nightly report", exc_info=True)
    routes = await asyncio.to_thread(family_link.settings_with_prefix, ROUTES_PREFIX)
    text = build_problem_report(rows, NIGHTLY_REPORT_HOURS, feedback, note, routes=routes)
    if feedback and len(text) > TELEGRAM_MAX_CHARS:
        # A long day of feedback goes in messages of its own rather than
        # pushing the problems past one message, where the cut could land
        # inside a tag and lose the whole report.
        text = build_problem_report(rows, NIGHTLY_REPORT_HOURS, routes=routes)
        await _send_feedback(context, f"💬 <b>Feedback — {len(feedback)}</b> · read only, with "
                                      f"tonight's report", feedback)
    if len(text) <= TELEGRAM_MAX_CHARS:
        await notify_owner(context, text)
        return
    # A very bad day can outgrow one message. The head still arrives as a
    # message rather than only as an attachment, because a day that bad is the
    # one where a file nobody opens is the worst possible outcome.
    await notify_owner(context, text[:TELEGRAM_MAX_CHARS])
    plain = re.sub(r"<[^>]+>", "", text)
    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_document(
                chat_id=admin_id, document=BytesIO(plain.encode("utf-8")),
                filename="problems.txt")
        except Exception:
            logger.exception("Couldn't deliver the nightly report file to %s", admin_id)


# ---------------------------------------------------------------------------
# Feedback
# ---------------------------------------------------------------------------
# What people send with /feedback in the four public bots (shared_features),
# delivered here: each one as it arrives, or all of them with the 22:00
# report. The owner, 2026-10-03: "notifications for each feedback won't hurt
# for now. Then add a switch for me to decide if i want each feedback to arrive
# to me or only grouped in a nightly report." Instant is the default.
#
# A piece of feedback carries the words, the bot and the language -- nothing
# about who wrote it, under the standing rule about what reaches the owner
# unasked. Which makes it one-way, on purpose; the owner, offered a way to
# reply: "Make the feedback one sided. I can only see the text copied and sent
# by the bots. Make sure its mentioned anywhere that the feedback is read only
# for me." So every place it shows here says "read only", and every place a
# public bot mentions /feedback says nobody can write back.
FEEDBACK_READ_ONLY = "read only: it names nobody, so there is nobody to answer"

# A message holds 4096 characters; one piece of feedback can be that long by
# itself, so what is shown of one is capped and /feedback <id> shows the rest.
FEEDBACK_SHOWN_CHARS = 3000
# Rows /feedback lists, and how far back /feedback <n> looks for one piece.
FEEDBACK_LIST_ROWS = 10
FEEDBACK_SEARCH_ROWS = 50
_feedback_mode_cache = {"value": None, "read_at": 0.0}


async def _feedback_mode() -> str:
    """"instant" or "nightly", read at most once a minute -- the event pump asks
    on every sweep, and the answer changes when the owner taps a button here,
    which updates this as well."""
    now = time_module.monotonic()
    if _feedback_mode_cache["value"] is None or now - _feedback_mode_cache["read_at"] > 60:
        try:
            value = await asyncio.to_thread(db.get_setting, family_link.FEEDBACK_DELIVERY_KEY,
                                            family_link.FEEDBACK_INSTANT)
        except Exception:
            value = _feedback_mode_cache["value"] or family_link.FEEDBACK_INSTANT
        _feedback_mode_cache.update(value=value, read_at=now)
    return _feedback_mode_cache["value"]


def _feedback_block(item: dict, shown: int = FEEDBACK_SHOWN_CHARS) -> str:
    """One piece of feedback as HTML: where from, when, then the words."""
    body = item["body"]
    cut = len(body) > shown
    text = html.escape(body[:shown]) + (f"… <code>/feedback {item['id']}</code> for the rest"
                                        if cut else "")
    when = item["created_at"].strftime("%m-%d %H:%M") if item.get("created_at") else "?"
    return (f"  {html.escape(_bot_name(item['bot_id']))} · {html.escape(item.get('lang') or '?')} · "
            f"{when} UTC · #{item['id']}\n  <i>{text}</i>")


async def _send_feedback(context, header: str, items: list) -> None:
    """Items packed into as few messages as fit, never split inside one."""
    chunk = [header]
    for block in (_feedback_block(item) for item in items):
        if len("\n\n".join(chunk + [block])) > TELEGRAM_MAX_CHARS:
            await notify_owner(context, "\n\n".join(chunk))
            chunk = []
        chunk.append(block)
    if chunk:
        await notify_owner(context, "\n\n".join(chunk))


async def _deliver_feedback(context) -> None:
    """Each new piece as it arrives, in "instant" mode. Called from the event
    pump after the events, so it is held while alerts are muted (and goes out
    with the nightly report if they still are)."""
    if await _feedback_mode() != family_link.FEEDBACK_INSTANT:
        return
    try:
        items = await asyncio.to_thread(family_link.take_undelivered_feedback, 10)
    except Exception:
        return
    for item in items:
        await notify_owner(context, "💬 <b>Feedback</b> · read only\n" + _feedback_block(item))


def _feedback_keyboard(mode: str) -> InlineKeyboardMarkup:
    tick = {mode: "✓ "}
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(f"{tick.get(family_link.FEEDBACK_INSTANT, '')}🔔 As it arrives",
                             callback_data="fbmode:instant"),
        InlineKeyboardButton(f"{tick.get(family_link.FEEDBACK_NIGHTLY, '')}🌙 With the 22:00 report",
                             callback_data="fbmode:nightly"),
    ]])


def _feedback_mode_line(mode: str) -> str:
    return ("🔔 Each piece reaches you as it arrives." if mode == family_link.FEEDBACK_INSTANT
            else "🌙 It reaches you with the 22:00 report.")


async def _feedback_overview(mode: str) -> str:
    items = await asyncio.to_thread(family_link.recent_feedback, FEEDBACK_LIST_ROWS)
    lines = ["💬 <b>Feedback</b>", f"<i>{FEEDBACK_READ_ONLY}.</i>", _feedback_mode_line(mode), ""]
    if not items:
        lines.append("Nobody has sent any yet.")
    else:
        lines.append(f"The latest {len(items)}, newest first:")
        lines += [_feedback_block(item, shown=280) for item in items]
    lines += ["", f"Kept {family_link.FEEDBACK_RETENTION_DAYS} days, then deleted. "
                  "<code>/feedback &lt;id&gt;</code> shows one in full."]
    return "\n".join(lines)


async def feedback_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/feedback -- the latest, and the switch. /feedback instant|nightly sets
    it; /feedback <id> shows one piece in full."""
    if not await guard(update, context):
        return
    args = [a.lower() for a in context.args]
    if args and args[0] in (family_link.FEEDBACK_INSTANT, family_link.FEEDBACK_NIGHTLY):
        await asyncio.to_thread(db.set_setting, family_link.FEEDBACK_DELIVERY_KEY, args[0])
        _feedback_mode_cache.update(value=args[0], read_at=time_module.monotonic())
        await update.message.reply_text(_feedback_mode_line(args[0]), parse_mode=ParseMode.HTML)
        return
    if args and args[0].lstrip("#").isdigit():
        wanted = int(args[0].lstrip("#"))
        items = await asyncio.to_thread(family_link.recent_feedback, FEEDBACK_SEARCH_ROWS)
        item = next((i for i in items if i["id"] == wanted), None)
        if item is None:
            await update.message.reply_text(
                f"No feedback #{wanted} among the latest {FEEDBACK_SEARCH_ROWS}.")
            return
        await _send_feedback_one(context, update.effective_chat.id, item)
        return
    mode = await _feedback_mode()
    await update.message.reply_text(await _feedback_overview(mode), parse_mode=ParseMode.HTML,
                                    reply_markup=_feedback_keyboard(mode))


async def _send_feedback_one(context, chat_id: int, item: dict) -> None:
    head = (f"💬 #{item['id']} · {_bot_name(item['bot_id'])} · {item.get('lang') or '?'} · "
            f"{item['created_at']:%Y-%m-%d %H:%M} UTC\n\n")
    await send_long(context, chat_id, head + item["body"], f"feedback-{item['id']}.txt")


async def feedback_mode_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """The switch under /feedback."""
    query = update.callback_query
    if not await guard(update, context):
        await query.answer()
        return
    mode = query.data.split(":", 1)[1]
    if mode not in (family_link.FEEDBACK_INSTANT, family_link.FEEDBACK_NIGHTLY):
        await query.answer()
        return
    await asyncio.to_thread(db.set_setting, family_link.FEEDBACK_DELIVERY_KEY, mode)
    _feedback_mode_cache.update(value=mode, read_at=time_module.monotonic())
    await query.answer("As it arrives" if mode == family_link.FEEDBACK_INSTANT
                       else "With the 22:00 report")
    await edit_in_place(query.message, context.bot, await _feedback_overview(mode),
                        parse_mode=ParseMode.HTML, reply_markup=_feedback_keyboard(mode))


# The furthest back /problems will look, in hours (a week).
PROBLEMS_MAX_HOURS = 168


async def nightly_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/nightly [hours] -- the report the owner gets at 22:00, on demand."""
    if not await guard(update, context):
        return
    hours = int(context.args[0]) if context.args and context.args[0].isdigit() \
        else NIGHTLY_REPORT_HOURS
    hours = max(1, min(hours, PROBLEMS_MAX_HOURS))
    rows = await asyncio.to_thread(family_link.recent_problem_reports_since, hours)
    # On demand it only counts the feedback: taking it here would mark it as
    # sent, and the 22:00 report would then leave out what it was asked for.
    note = None
    try:
        count = await asyncio.to_thread(family_link.feedback_count_since, hours)
        if count:
            note = f"{count} feedback message(s) in the last {hours}h — /feedback to read them."
    except Exception:
        logger.debug("Couldn't count feedback for /problems", exc_info=True)
    routes = await asyncio.to_thread(family_link.settings_with_prefix, ROUTES_PREFIX)
    text = build_problem_report(rows, hours, feedback_note=note, routes=routes)
    if len(text) > TELEGRAM_MAX_CHARS:
        await send_long(context, update.effective_chat.id,
                        re.sub(r"<[^>]+>", "", text), "problems.txt")
        return
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


async def decode_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/decode <code> -- what an error code means, from problems.py, the same
    registry the public bots code their messages from. Any unambiguous part of
    a code works: /decode reddit."""
    if not await guard(update, context):
        return
    wanted = " ".join(context.args or []).replace("🆔", "").strip().upper()
    if not wanted:
        await update.message.reply_text("Usage: /decode <code> — for example /decode CV-TIMEOUT")
        return
    matches = [wanted] if wanted in problems.PROBLEMS else [c for c in problems.PROBLEMS if wanted in c]
    if len(matches) == 1:
        await update.message.reply_text(problems.decode(matches[0]))
    elif matches:
        await update.message.reply_text("More than one code matches: " + ", ".join(matches[:40]))
    else:
        await update.message.reply_text(f"No code matches {wanted}.")


# ---------------------------------------------------------------------------
# Alert toggles
# ---------------------------------------------------------------------------

async def alerts_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update, context):
        return
    if context.args and context.args[0].lower() in ("on", "off"):
        value = context.args[0].lower()
        await asyncio.to_thread(db.set_setting, "alerts", value)
        await update.message.reply_text(
            f"🔔 Alerts {value}."
            + ("" if value == "on" else "\nDown/up and crash alerts are muted. /status still works.")
        )
        return
    current = await asyncio.to_thread(db.get_setting, "alerts", "on")
    await update.message.reply_text(f"🔔 Alerts are {current}. Use /alerts on or /alerts off.")


# ---------------------------------------------------------------------------
# The watching itself
# ---------------------------------------------------------------------------

async def _alerts_on() -> bool:
    try:
        return await asyncio.to_thread(db.get_setting, "alerts", "on") == "on"
    except Exception:
        return True  # if the setting can't be read, err towards telling the owner


async def startup_rollcall(context: ContextTypes.DEFAULT_TYPE) -> None:
    """One message, once, a few seconds after ManagerBot starts: who is up.

    This answers "I just started the family, did it work?" -- a question that
    previously had no answer except typing /status yourself. The watchdog only
    speaks on *changes*, and deliberately says nothing on its first pass
    (announcing four bots as up every time ManagerBot restarts would be noise);
    a roll-call is the one moment where the current state, changed or not, is
    exactly what you want to see.

    Deliberately not five messages. Each bot could announce itself -- they
    already write a startup event -- but starting the family would then mean
    five notifications arriving in a random order over ten seconds. Waiting
    once and reporting the whole family together is the same information read
    in one glance.

    Runs on heartbeats, not on process management: ManagerBot never starts,
    stops or supervises the other four, and does not need to in order to say
    what happened. That is what makes this work identically whether the four
    are on this laptop, on Railway, or split across both.
    """
    if not await _alerts_on():
        return
    try:
        beats = {row["bot_id"]: row for row in await asyncio.to_thread(db.all_heartbeats)}
    except Exception as exc:
        logger.warning("Startup roll-call couldn't reach the database: %s", exc)
        await notify_owner(
            context,
            "\U0001f44b <b>ManagerBot is up</b>, but it cannot reach the family "
            "database, so it has no idea who else is.\n"
            f"<code>{html.escape(str(exc))}</code>",
        )
        return

    now = datetime.now(timezone.utc)
    lines, up_count = [], 0
    for bot in CHILDREN:
        beat = beats.get(bot["id"])
        if beat is None:
            lines.append(f"\u2754 <b>{bot['name']}</b> -- never seen. Has it ever been started?")
            continue
        if beat["seconds_ago"] > DOWN_AFTER_SECONDS:
            lines.append(
                f"\u274c <b>{bot['name']}</b> -- not up. Last heartbeat "
                f"{format_delta(beat['seconds_ago'])} ago."
            )
            continue

        up_count += 1
        uptime = (now - beat["started_at"]).total_seconds()
        host = html.escape(beat["host"] or "?")
        if uptime <= ROLLCALL_FRESH_SECONDS:
            lines.append(
                f"\U0001f7e2 <b>{bot['name']}</b> -- just started on {host} "
                f"\u00b7 v{beat['version']}"
            )
        else:
            lines.append(
                f"\u2705 <b>{bot['name']}</b> -- already up ({format_delta(uptime)}) "
                f"on {host} \u00b7 v{beat['version']}"
            )

    header = (
        f"\U0001f44b <b>ManagerBot is up</b> on {html.escape(family_link.HOSTNAME)} -- "
        f"{up_count}/{len(CHILDREN)} of the family with it"
    )
    await notify_owner(context, "\n".join([header, "", *lines]))

    # Seed the up/down table with what was just reported. Without this the
    # watchdog's first pass treats every bot as newly discovered and can
    # repeat the bad news twenty seconds later; from here on it only speaks
    # when something actually changes, which is its job.
    for bot in CHILDREN:
        beat = beats.get(bot["id"])
        is_up = beat is not None and beat["seconds_ago"] <= DOWN_AFTER_SECONDS
        try:
            await asyncio.to_thread(db.set_known_state, bot["id"], is_up)
        except Exception:
            logger.debug("Couldn't seed bot_state for %s", bot["id"], exc_info=True)


# Consecutive failures of ManagerBot's own database connection. The owner is
# told once, not once a minute, and told again when it recovers -- this is
# the one failure that would otherwise be completely silent, since every
# other alert path in this file runs through that same database.
_db_failures = 0
_db_alerted = False


async def _is_redeploying(bot_id: str) -> bool:
    """True while a bot's own goodbye note is still fresh."""
    key = lifecycle.RESTART_NOTE_PREFIX + bot_id
    try:
        raw = await asyncio.to_thread(db.get_setting, key)
        if not raw:
            return False
        stamped = datetime.fromisoformat(raw)
    except Exception:
        return False
    age = (datetime.now(timezone.utc) - stamped).total_seconds()
    return 0 <= age <= REDEPLOY_GRACE_SECONDS


async def _clear_redeploy_note(bot_id: str) -> None:
    try:
        await asyncio.to_thread(db.set_setting, lifecycle.RESTART_NOTE_PREFIX + bot_id, "")
    except Exception:
        logger.debug("Could not clear the redeploy note for %s", bot_id, exc_info=True)


async def watchdog(context: ContextTypes.DEFAULT_TYPE) -> None:
    global _db_failures, _db_alerted
    try:
        await asyncio.to_thread(db.expire_stale_commands, COMMAND_TIMEOUT_SECONDS)
        beats = {row["bot_id"]: row for row in await asyncio.to_thread(db.all_heartbeats)}
        known = await asyncio.to_thread(db.get_known_state)
    except Exception as exc:
        _db_failures += 1
        logger.warning("Watchdog couldn't reach the database (%s in a row): %s", _db_failures, exc)
        if _db_failures >= 3 and not _db_alerted:
            _db_alerted = True
            await notify_owner(
                context,
                "🚨 <b>ManagerBot cannot reach the family database.</b>\n"
                f"<code>{html.escape(str(exc))}</code>\n\n"
                "Until it comes back I cannot see any bot's state, so treat "
                "silence from me as unknown, not as healthy.",
            )
        return

    if _db_alerted:
        _db_alerted = False
        await notify_owner(context, "✅ Family database is reachable again.")
    _db_failures = 0

    if not await _alerts_on():
        return

    for bot in CHILDREN:
        beat = beats.get(bot["id"])
        is_up = beat is not None and beat["seconds_ago"] <= DOWN_AFTER_SECONDS
        was_up = known.get(bot["id"])

        if was_up is None:
            # First sighting -- record it silently. Announcing "StickerBot is
            # up" the first time ManagerBot ever runs is noise, not news.
            await asyncio.to_thread(db.set_known_state, bot["id"], is_up)
            if not is_up and beat is not None:
                await notify_owner(context, f"❌ <b>{bot['name']}</b> is down (first check since I started).")
            continue

        if is_up == was_up:
            continue

        if is_up:
            await asyncio.to_thread(db.set_known_state, bot["id"], True)
            await _clear_redeploy_note(bot["id"])
            uptime = format_delta((datetime.now(timezone.utc) - beat["started_at"]).total_seconds())
            await notify_owner(
                context,
                f"✅ <b>{bot['name']}</b> is back up (started {uptime} ago, "
                f"on {html.escape(beat['host'] or '?')})."
            )
        elif await _is_redeploying(bot["id"]):
            # Deliberate, and recent. Leave known_state alone so that the
            # "it is back" message is not announced either -- a redeploy the
            # owner started should be silent at both ends. If it does not
            # come back, the note ages out and the next pass alerts.
            logger.info("%s is stale but was shut down on purpose -- redeploying.", bot["id"])
            continue
        else:
            await asyncio.to_thread(db.set_known_state, bot["id"], False)
            last = format_delta(beat["seconds_ago"]) if beat else "?"
            await notify_owner(
                context,
                f"❌ <b>{bot['name']}</b> has gone down — no heartbeat for {last}.\n"
                f"Its last host was {html.escape((beat or {}).get('host') or '?')}."
            )


async def event_pump(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Forwards what the bots themselves flagged. Info-level events are
    recorded but not pushed, except payments -- a donation arriving is the
    one piece of good news worth interrupting someone for."""
    if not await _alerts_on():
        # Deliberately before claiming anything: take_unnotified_events marks
        # rows as sent, so claiming while muted would silently eat them.
        return
    try:
        events = await asyncio.to_thread(db.take_unnotified_events, 20)
    except Exception:
        return

    if events:
        # A crashing bot tends to report several things at once; stay on the
        # fast cadence long enough to catch the rest.
        family_link.mark_bus_active()

    icons = {"warning": "⚠️", "error": "🔥", "critical": "🚨", "info": "ℹ️"}
    for event in events:
        if event["level"] not in ALERT_LEVELS and event["kind"] != "payment":
            continue
        icon = icons.get(event["level"], "•")
        text = (
            f"{icon} <b>{html.escape(event['bot_id'])}</b> — {html.escape(event['kind'])}\n"
            f"{html.escape(event['message'])}"
        )
        if event["details"]:
            tail = event["details"].strip().splitlines()[-6:]
            text += "\n\n<pre>" + html.escape("\n".join(tail)) + "</pre>"
        await notify_owner(context, text[:4000])

    # Feedback rides the same sweep, after the events and under the same mute.
    await _deliver_feedback(context)


# How long ago each pump actually swept. The interval is chosen from whether
# the bus is active (family_link.bus_is_active(), set when ManagerBot queues a
# command): fast while an answer could be coming back, slow once things have
# gone quiet -- the same adaptive poll the child bots run for the command
# queue, and the whole delivery mechanism now that there is no push.
_last_pumped = {"result": 0.0, "event": 0.0}

# The sweep runs as a task of its own, so the 1 s tick that decides *whether* to
# sweep returns at once. Awaited inside the tick, a sweep of two database round
# trips (events, then feedback) that outlasted one second made the scheduler
# skip every following tick with "maximum number of running instances" -- about
# 2,800 warning lines a day in production, and the same line a real stall
# prints. Now a skip can only mean the tick itself is stuck, and a sweep that
# really is stuck is named here instead.
_pump_tasks: dict[str, "asyncio.Task"] = {}
_pump_started: dict[str, float] = {}
_pump_stall_logged: set[str] = set()
PUMP_STALL_WARN_SECONDS = int(os.environ.get("MBOT_PUMP_STALL_WARN_SECONDS", "60"))

# How long each sweep took, for the /status board: the slowest in the last
# 15 minutes and the slowest since start, so a sweep that is slow every time
# (the database round trip from Railway) can be told from a one-off. In memory
# only -- a restart starts it again, nothing is written to the database. Per
# pump key, (finished at, seconds) on the monotonic clock. Only the event loop
# touches these (the done callback and _sweep_line run on it), which is why
# the board gets its line passed in rather than reading these from its thread.
SWEEP_WINDOW_SECONDS = 900
_sweep_recent: dict[str, "deque[tuple[float, float]]"] = {}
_sweep_slowest: dict[str, float] = {}


def _note_sweep(key: str, started: float, task=None) -> None:
    """Done callback of a sweep task: it ended, however it ended (finished,
    raised or was cancelled), so it is counted. Never raises."""
    try:
        now = time_module.monotonic()
        seconds = max(0.0, now - started)
        recent = _sweep_recent.setdefault(key, deque())
        recent.append((now, seconds))
        while recent and now - recent[0][0] > SWEEP_WINDOW_SECONDS:
            recent.popleft()
        _sweep_slowest[key] = max(_sweep_slowest.get(key, 0.0), seconds)
    except Exception as exc:
        logger.debug("Could not note how long the %s sweep took (%s).", key, type(exc).__name__)


def _sweep_line(key: str = "event") -> str:
    """The /status line about how long the sweeps take. Called on the event
    loop, not in a thread: the deque is mutated by the loop."""
    now = time_module.monotonic()
    name = key.capitalize()
    if key not in _sweep_slowest:
        return f"⏱ {name} sweep: none finished yet since start"
    since_start = f"{_sweep_slowest[key]:.2f} s"
    window = f"{SWEEP_WINDOW_SECONDS // 60} min"
    recent = [seconds for finished, seconds in _sweep_recent.get(key, ())
              if now - finished <= SWEEP_WINDOW_SECONDS]
    if not recent:
        return f"⏱ {name} sweep: none in the last {window} · slowest since start {since_start}"
    return (f"⏱ {name} sweep: slowest {max(recent):.2f} s in the last {window} "
            f"({len(recent)} sweep{'' if len(recent) == 1 else 's'}) · {since_start} since start")


async def _pump_if_due(context, key: str, pump, fast: int, idle: int) -> None:
    now = time_module.monotonic()
    task = _pump_tasks.get(key)
    if task is not None and not task.done():
        # Still sweeping: nothing to add, never a second sweep on top of it.
        if (now - _pump_started[key] > PUMP_STALL_WARN_SECONDS
                and key not in _pump_stall_logged):
            _pump_stall_logged.add(key)
            logger.warning("The %s sweep has been running for %d s.", key, now - _pump_started[key])
        return
    _pump_stall_logged.discard(key)
    due = fast if family_link.bus_is_active() else idle
    if now - _last_pumped[key] < due:
        return
    _last_pumped[key] = now
    _pump_started[key] = now
    # The application owns the task: it keeps it alive, waits for it on
    # shutdown and sends an exception from it to the error handler.
    _pump_tasks[key] = context.application.create_task(pump(context), name=f"{key}-sweep")
    _pump_tasks[key].add_done_callback(functools.partial(_note_sweep, key, now))


async def _result_backstop(context: ContextTypes.DEFAULT_TYPE) -> None:
    await _pump_if_due(context, "result", result_pump,
                       RESULT_POLL_FAST_SECONDS, RESULT_POLL_SECONDS)


async def _event_backstop(context: ContextTypes.DEFAULT_TYPE) -> None:
    await _pump_if_due(context, "event", event_pump,
                       EVENT_POLL_FAST_SECONDS, EVENT_POLL_SECONDS)


async def result_pump(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Delivers whatever the child bots wrote back, and gives up on commands
    nobody claimed."""
    try:
        results = await asyncio.to_thread(db.take_finished_commands, 5)
    except Exception:
        return

    if results:
        # More may still be trickling in (a /ping to everything answers over
        # a few seconds); keep sweeping at the fast cadence for a bit.
        family_link.mark_bus_active()

    for result in results:
        # A /ping is answered by rewriting the message that announced it,
        # with the whole round trip broken down -- not by a second message
        # underneath. Checked before reply_chat_id because the trace already
        # holds the message it is going to become.
        trace = _PING_TRACES.pop(result["id"], None)
        if trace is not None:
            await _deliver_ping(context, trace, result)
            continue

        # A command asked of every bot at once rewrites one message rather
        # than sending four, for the same reason a /ping does.
        group = _FANOUTS.pop(result["id"], None)
        if group is not None:
            await _deliver_fanout(context, group, result)
            continue

        chat_id = result["reply_chat_id"]
        if not chat_id:
            continue
        # Display name, not the raw queue key: the acknowledgement this is
        # answering said "StickerBot", and following it with "stickerbot"
        # reads as a different thing having replied.
        target = resolve_bot(result["target_bot"])
        head = f"{target['name'] if target else result['target_bot']} · {result['command']}"
        if result["status"] == "timeout":
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"⏳ {head} (queued as #{result['id']}) — no answer in {COMMAND_TIMEOUT_SECONDS}s. That bot looks down.",
            )
            continue

        mark = "✅" if result["ok"] else "⚠️"
        body = result["output"] or "(no output)"
        # The pre-flight check in _dispatch catches the version gap for every
        # command in COMMAND_SINCE. This catches the rest: a command added to
        # the bus without being listed there, or a bot whose version string
        # could not be read. "Unknown command" on its own reads as a fault;
        # with the two version numbers beside it, it reads as a deploy.
        if not result["ok"] and body.startswith("Unknown command"):
            beat = await asyncio.to_thread(db.heartbeat_of, result["target_bot"])
            theirs = (beat or {}).get("version")
            if theirs and theirs != family_link.VERSION:
                body += (f"\n\nThat bot is on {theirs}; ManagerBot is on "
                         f"{family_link.VERSION}. Most likely it has not been "
                         f"published since the command was written.")
        if result["file_bytes"]:
            await context.bot.send_document(
                chat_id=chat_id,
                document=BytesIO(bytes(result["file_bytes"])),
                filename=result["file_name"] or f"{result['target_bot']}.bin",
                caption=f"{mark} {head} — {body}"[:1000],
            )
        else:
            await send_long(context, chat_id, f"{mark} {head}\n\n{body}", f"{result['target_bot']}.txt")


async def daily_digest(context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        board = await asyncio.to_thread(_build_board, _sweep_line())
    except Exception as exc:
        await notify_owner(context, f"🚨 Daily check failed: {html.escape(str(exc))}")
        return
    await notify_owner(context, "🌅 <b>Daily check</b>\n\n" + board)


# ---------------------------------------------------------------------------
# /start, /help
# ---------------------------------------------------------------------------

# An f-string for the numbers it quotes; {{bots}} stays a placeholder for start_command.
HELP = f"""👪 <b>ManagerBot</b> — the family's manager.

<b>Is everything alright</b>
/status — every bot: up/down, uptime, host, errors, users (with Refresh /
    Alerts / Ping buttons under it)
/problems [hours] — what the bots showed people, laid out: urgent
    listed, faults grouped, the rest by what kind of thing it was. The 22:00
    report, on demand
/reports [n] [mine] — the latest problems, newest first, each with its
    incident code; mine: only those somebody attached their details to
/report &lt;incident&gt; — one problem in full, with whoever volunteered to be named
/errors — what each bot has hit since it started (live; a redeploy clears it)
/events [bot] [n] — recent crashes, startups, payments
/ping [bot] — no bot named pings all of them, fastest first
/logs &lt;bot&gt; [n] [bot|problems] — tail a log: errors by default, "bot" or
    "problems" for the other two
/usage [bot] — memory, peaks, and what they cost
/idle [bot] — how quiet each bot is, and whether sleeping would pay
/buttons [days] — how often each button is tapped, per bot (counts only)
/providers — which of DownloaderBot's download routes work, which rest
/probe — test every download route, from DownloaderBot's container
/alerts on|off — mute or unmute unprompted alerts

<b>Doing something to a bot</b>
/run &lt;bot&gt; &lt;command&gt; [args] — the general form; run it bare for the list,
    which now includes the commands each bot registers for itself
/restart &lt;bot&gt; — restart its process
/pause [bot|all] [minutes] — stop them taking work an update would lose;
    whoever asks is told to come back, and written down
/warn [bot|all] — tell whoever is mid-something that it is about to reset
/resume [bot|all] — reopen, and tell everyone who was turned away
    (yours to say, so one update can be as many deploys as it needs)

<b>Talking to people</b>
/say &lt;bot&gt; &lt;user_id&gt; &lt;text&gt; — DM someone <i>as</i> that bot
/broadcast &lt;bot|all&gt; [--active] &lt;text&gt; — one message to everyone, <i>as</i> that bot
/whois &lt;bot&gt; &lt;user_id&gt; — look someone up through that bot
/users [hours] — active users per bot, default 24h
/feedback [instant|nightly] — what people sent with /feedback (read only: it
    names nobody, so there is nobody to answer), and whether each one reaches
    you as it arrives or with the 22:00 report

<b>Money</b>
/balance [id] [±n|=n] — see or change a credit balance
/addcredit &lt;id&gt; &lt;amount&gt; — put ⚡ into an account
/gift &lt;bot|all&gt; &lt;key&gt; &lt;credits&gt; &lt;last day&gt; — bonus ⚡ for everyone,
    once each: each bot counts first, and nothing is given until you say so
/promo [stop] — the {family_link.PROMO_CREDITS} ⚡ gift: start it (once), see how it
    goes, end it early
/donations — paid donations per bot
/stars — ConvertBot's Stars: paid, refunded, recent

<b>The database</b>
/backup — the whole database, lossless and restorable, sent here and pinned
/dbdump &lt;bot&gt; — that bot's own tables as CSVs
/sql &lt;SELECT …&gt; — read-only query on the shared database
/decode &lt;code&gt; — what an error code means

<b>Still works, not in the menu</b>
Renamed in 1.7.0, with the old names kept so a habit does not break:
/nightly → /problems · /finishupdates → /resume ·
/errorlog, /botlog and /problemlog → /logs · /me → /status
/crashtest &lt;bot&gt; makes a bot raise on purpose, to check the alert arrives
    (no bot named crashes ManagerBot itself)

Bots: <code>{{bots}}</code>
Any unambiguous prefix works — <code>/logs stick</code> is fine."""


# ---------------------------------------------------------------------------
# /help <name> and the buttons under /help: a page per command
# ---------------------------------------------------------------------------
# The owner, 2026-10-17: each command that is complex or has options gets a
# page, "a detailed explanation and one simple example". 2026-10-18: "make it
# work like the ping command works" -- so /help carries one button per page, a
# tap sends the page as a new message, and /help pause is the same page.
# Every number on a page is read when the page is shown, from the code that
# enforces it, so a page cannot drift from the command it describes. Plain
# text, because the pages contain < and >, and with link previews off.

NO_PREVIEW = LinkPreviewOptions(is_disabled=True)


def _help_page_pause() -> str:
    minutes = lifecycle.DEFAULT_MAINTENANCE_MINUTES
    timeout = COMMAND_TIMEOUT_SECONDS
    return f"""⏸ Updating the bots: /pause, /warn, /resume

In this order:
1. /pause — the bots stop starting new long work, the kind an update would cut off. Whoever asks for some is told to come back in about {minutes} minutes, and written down. Work already running carries on.
2. /warn — only the people in the middle of something right now are told it is about to be reset. It changes nothing, and you can send it again.
3. Deploy, as many times as it takes. The pause survives restarts and redeploys, and it never ends by itself: the minutes are only what people are told.
4. /resume — the bots take work again, and everyone who was turned away is told they can try again (each person once).

Forms:
/pause — all four bots, people told {minutes} minutes
/pause 30 — all four, 30 minutes
/pause convert — one bot (any unambiguous start of its name)
/pause convert 30 — one bot, 30 minutes
/warn [bot|all] and /resume [bot|all] — the same choice of bots
/finishupdates is the old name of /resume.

Each bot answers for itself: how many people it warned, or that it is open again and how many it told. A bot that is down gets nothing: after {timeout} seconds without an answer you are told it looks down; send the command again once it is up. ManagerBot itself is never paused.

Example: /pause 20, then /warn, deploy, check /status, then /resume.
"""


def _help_page_gift() -> str:
    key_rule = family_link.GIFT_KEY_RULE
    max_credit = family_link.GIFT_MAX_CREDIT
    days = family_link.GIFT_MAX_DAYS
    return f"""🎁 /gift — bonus ⚡ for everyone a bot knows, once each

/gift <bot|all> <key> <credits> <last day>
• bot|all — one bot (any unambiguous start of its name) or all four. Somebody who uses two bots still gets it once.
• key — names this gift. {key_rule} A key is given at most once per person, across the whole family, for ever: sending the same /gift twice is safe, and a new gift needs a new key.
• credits — a whole number from 1 to {max_credit}.
• last day — YYYY-MM-DD, from tomorrow up to {days} days ahead. The credit is bonus: spent before ordinary credit, and whatever is left disappears at the end of that day, UTC.

What happens:
1. Each bot first only counts, and says so in a message of its own: how many people it knows, how many would get it, how much ⚡ in all, and the notice they will read. Nothing is given.
2. A card names the amount, the last day and the key, with "Give it" and "Cancel". Nothing is given until you press Give it, and that cannot be taken back. Only the newest /gift can be given; an older card's buttons give nothing.
3. Each bot goes through its people and reports how many it gave it to. Each person is told, in their own language. If a bot fails partway, send the same /gift again: those who already have it are skipped.

/run <bot> gift … --go gives at once, with no count and no card.

Example: /gift all welcome-200 200 2027-01-15
"""


def _help_page_promo() -> str:
    amount = family_link.PROMO_CREDITS
    users = family_link.PROMO_USERS
    claim_days = family_link.PROMO_CLAIM_DAYS
    spend_days = family_link.PROMO_SPEND_DAYS
    minutes = PROMO_COUNT_SECONDS // 60
    return f"""🎉 /promo — the {amount} ⚡ gift for everyone who writes to ConvertBot or DownloaderBot

/promo
/promo stop

The count and the notice:
• Every {minutes} minutes ManagerBot counts the unique people across all the bots, each person once however many bots they use. The first time the count reaches {users} it messages you, once. /users shows the count too.

What /promo shows depends on where the gift stands:
• Not started, under {users} people: the count so far. Nothing to do; you will be told when it is reached.
• Not started, {users} reached: a card with the ad people will see and two buttons, Start the gift and Cancel. Starting can be done once, ever, and it cannot be undone. Only the newest card works.
• Started: when it started, when claiming closes, how many people claimed it, and how many people AnonBot showed the ad to.

Once started:
• Claiming stays open for {claim_days} days: until midnight UTC at the end of the {claim_days}th day after the start.
• The first message a person sends to ConvertBot or DownloaderBot in that time gives them {amount} ⚡ of bonus credit, once in all, not once per bot. They are told in their own language.
• Each gift can be spent for {spend_days} days from the day it was claimed. What is left then disappears at the end of that day, UTC.
• AnonBot shows the ad once to each person who writes to it and does not have the gift yet: not on the first /start of an inbox link, not in the middle of a question, and not again after a restart.

/promo stop ends the claiming at once. Credit already given stays and can be spent until it runs out. Before the start, or after the end, there is nothing to stop.

/gift is the separate, general tool for any gift under its own key. /promo is this one gift only.

Example: /promo stop
"""


def _help_page_broadcast() -> str:
    active_days = family_link.BROADCAST_ACTIVE_DAYS
    per_second = f"{family_link.BROADCAST_PER_SECOND:g}"
    return f"""📢 /broadcast — one message to everyone a bot knows, as that bot

/broadcast <bot|all> <text>
/broadcast <bot|all> --active <text>
• The first word must be a bot (any unambiguous start of its name) or all: "/broadcast hello there" is refused, because "hello" is not a bot.
• --active, right after the bot: only people who used that bot in the last {active_days} days. A bot that cannot tell who is active sends nothing, and says so.
• The text goes as plain text, with no formatting. Line breaks and double spaces are not kept: it arrives as one paragraph.
• First you get a card with the text and "Send it" / "Cancel". Nothing is sent before Send it, and nothing can be taken back after. A newer /broadcast replaces the one waiting.
• Each bot sends at most {per_second} messages a second, skips people who blocked it, and reports how many it reached.

/run <bot> broadcast … sends at once, with no card.

Example: /broadcast all --active The bots stop for an update at 22:00 UTC, for about 10 minutes.
"""


def _help_page_balance() -> str:
    top = BALANCE_OVERVIEW_ROWS
    rows = BALANCE_HISTORY_ROWS
    return f"""⚡ /balance — see or change one person's credit

/balance — the family's overview, with the {top} largest balances.
/balance <id> — that person's balance, what they paid and spent, their bonus and when it runs out, and their last {rows} movements.
/balance <id> +100 — add 100 ⚡
/balance <id> -100 — take 100 ⚡ away (the balance may go below zero)
/balance <id> =0 — set it to exactly 0
• id is the person's numeric Telegram id. Not sure who it is? /whois <bot> <id>.
• A bare number ("/balance 123 500") is refused on purpose: say +, - or =.
• A change happens at once, with no confirmation. The person's /balance shows it as "adjustment", and it is in /events.
• It is one wallet for the whole family: the same balance in every bot.
• /addcredit is the short way to add credit that never expires; /gift is for everyone at once.

Example: /balance 123456789 +50
"""


def _help_page_addcredit() -> str:
    return """➕ /addcredit — put ⚡ into one account

/addcredit <id> <amount>
• id: the person's numeric Telegram id. amount: a whole number above 0.
• It is ordinary credit: it never expires. The person's /balance shows it as "grant".
• It happens at once, with no confirmation, and nothing checks that the id is somebody a bot knows: check with /whois <bot> <id> first.
• To take credit away or set an exact amount: /balance <id> -n or =n.

Example: /addcredit 123456789 100
"""


def _help_page_run() -> str:
    timeout = COMMAND_TIMEOUT_SECONDS
    return f"""🛠 /run — any command, inside one bot

/run <bot> <command> [args]
• /run on its own lists the bots and every command, with the ones only one bot has.
• bot: any unambiguous start of its name. ManagerBot itself is refused; use its own commands.
• The bot must know the command: one its version doesn't have is refused before anything is sent.
• You see "queued as #n" at once; the bot's answer comes as a second message. No answer in {timeout} seconds means the bot looks down.
• No confirmation: /run <bot> broadcast … sends at once, and /run <bot> gift … --go gives at once. /broadcast and /gift are the ones that ask first.
• Shortcuts for the usual ones: /logs, /say, /stars, /probe, /providers.

Example: /run convert status
"""


def _help_page_logs() -> str:
    default = family_link.LOG_TAIL_DEFAULT
    most = family_link.LOG_TAIL_MAX
    return f"""📜 /logs — the end of one bot's log

/logs <bot> [lines] [bot|problems]
• bot: any unambiguous start of its name. One bot at a time.
• lines: how many from the end, {default} if not said, at most {most}. It must come straight after the bot's name.
• The last word picks the log: nothing (or anything else) — errors.log, its errors; "bot" or "all" — bot.log, everything it logged; "problems" — problems.log, what it showed people. ("all" means all of bot.log, not all bots.)
• If the bot's host keeps no log files, you get its in-memory copy since it started, and it says so.
• /errorlog, /botlog and /problemlog ask which bot with buttons.

Example: /logs convert 100 problems
"""


# One sentence on /reports, for the problems page of /help: the same facts as its line in HELP.
REPORTS_LINE = ("/reports [n] lists the latest problems, newest first, each with its "
                "incident code; /reports mine only the ones somebody attached their "
                "details to.")


def _help_page_problems() -> str:
    default = NIGHTLY_REPORT_HOURS
    most = PROBLEMS_MAX_HOURS
    # MBOT_NIGHTLY_UTC left blank turns the daily report off.
    daily = (f"The same report arrives by itself every day at {NIGHTLY_REPORT_AT_UTC} UTC."
             if NIGHTLY_REPORT_AT_UTC else "The daily report is off (MBOT_NIGHTLY_UTC is blank).")
    reports_line = REPORTS_LINE
    return f"""🩺 /problems — the 22:00 report, on demand

/problems [hours]
• hours: how far back, {default} if not said, from 1 to {most}.
• What the bots showed people in that time, laid out: urgent ones listed, faults grouped, the rest by what kind of thing it was. A note says how many /feedback messages came in.
• Too long for one message: the start comes here, and the whole of it as problems.txt.
• {daily} /nightly is the same command.
• One problem in full: /report <incident>. {reports_line}

Example: /problems 72
"""


def _help_page_events() -> str:
    default = EVENTS_DEFAULT
    most = EVENTS_MAX
    return f"""🗒 /events — crashes, startups and payments, as they happened

/events [bot] [n]
• n: how many, {default} if not said, at most {most}. bot: only that bot's events (any unambiguous start of its name). Either order.
• A word that is not a bot is ignored, so a typo shows every bot's events: check that the heading says "from" the bot you meant.
• Each line: date and time, the bot, what happened, with an icon for how serious it was.
• Too long for one message: the start comes here, and the whole of it as events.txt.

Example: /events convert 30
"""


def _help_page_feedback() -> str:
    latest = FEEDBACK_LIST_ROWS
    search = FEEDBACK_SEARCH_ROWS
    days = family_link.FEEDBACK_RETENTION_DAYS
    return f"""💬 /feedback — what people sent with /feedback

/feedback — the latest {latest}, newest first, with two buttons: 🔔 As it arrives / 🌙 With the 22:00 report (✓ on the one in force).
/feedback instant — each one reaches you the moment it is sent.
/feedback nightly — they wait for the 22:00 report.
/feedback <n> or #<n> — one in full (among the latest {search}).
• Read only. A message carries the bot it came from and the language, never who sent it, so there is nobody to answer.
• Each is kept {days} days, then deleted.

Example: /feedback nightly
"""


def _help_page_sql() -> str:
    timeout = db.SQL_TIMEOUT_SECONDS
    rows = db.SQL_MAX_ROWS
    return f"""🔎 /sql — a read-only query on the shared database

/sql SELECT …
• It must start with SELECT, WITH, TABLE, EXPLAIN or SHOW, and be one statement.
• The database itself keeps it read-only, and stops it after {timeout} seconds.
• At most {rows} rows come back, as text with | between the columns. Longer than a message: the start comes here, and all of it as query.txt.
• Every bot's tables are there, each bot in its own schema.
• An error comes back as ⚠️ and the database's own message.

Example: /sql SELECT count(*) FROM sticker_bot.packs
"""


def _help_page_say() -> str:
    return """✉️ /say — a message to one person, as one of the bots

/say <bot> <user_id> <text>
• One bot (any unambiguous start of its name), never all, and the person's numeric Telegram id.
• Plain text, no formatting; line breaks and double spaces are not kept.
• It goes at once: no confirmation, and it cannot be taken back. It arrives from that bot, as if the bot wrote it.
• "Sent." means Telegram took it. If the person blocked the bot or never started it, you get Telegram's refusal instead.

Example: /say convert 123456789 The fix is live — try your file again.
"""


# Name -> function() -> text, in the order /help lists them (a dict literal with
# string keys: tests read it with ast). An alias is a name that also answers:
# the old and the paired commands show the page of the one they belong with.
HELP_PAGES = {
    "pause": _help_page_pause,
    "gift": _help_page_gift,
    "promo": _help_page_promo,
    "broadcast": _help_page_broadcast,
    "balance": _help_page_balance,
    "addcredit": _help_page_addcredit,
    "run": _help_page_run,
    "logs": _help_page_logs,
    "problems": _help_page_problems,
    "events": _help_page_events,
    "feedback": _help_page_feedback,
    "sql": _help_page_sql,
    "say": _help_page_say,
}
HELP_ALIASES = {"warn": "pause", "resume": "pause", "finishupdates": "pause", "nightly": "problems"}


def help_page_name(context) -> "str | None":
    """The page `/help <word>` asks for, or None. Only the first word is read:
    lower case, a leading "/" and anything from "@" on dropped, then a leading
    "help_" (so /help help_pause and /help pause@Bot both work), then the aliases."""
    if not context.args:
        return None
    word = context.args[0].lower().lstrip("/").split("@", 1)[0].removeprefix("help_")
    word = HELP_ALIASES.get(word, word)
    return word if word in HELP_PAGES else None


def help_page_text(name: str) -> str:
    """One page, then the way back to /help."""
    return HELP_PAGES[name]().rstrip() + "\n\nEverything: /help"


def _help_keyboard() -> InlineKeyboardMarkup:
    """One button per page, two to a row, under /help and /start -- the way
    /ping has one per bot. A tap sends the page (help_page_button)."""
    buttons = [InlineKeyboardButton("/" + name, callback_data=f"helpdet:{name}") for name in HELP_PAGES]
    return InlineKeyboardMarkup([buttons[i:i + 2] for i in range(0, len(buttons), 2)])


async def help_page_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """A tap on one of those buttons: the page arrives as a new message, so the
    help above it stays to be tapped again."""
    if not await guard(update, context):
        return
    query = update.callback_query
    name = query.data.split(":", 1)[1]
    if name not in HELP_PAGES:
        # A button from before a redeploy that dropped the page.
        await query.answer("That help page no longer exists. Send /help.", show_alert=True)
        return
    await query.answer()
    await context.bot.send_message(chat_id=query.message.chat_id, text=help_page_text(name),
                                   link_preview_options=NO_PREVIEW)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update, context):
        return
    page = help_page_name(context)
    if page:
        await update.message.reply_text(help_page_text(page), link_preview_options=NO_PREVIEW)
        return
    await update.message.reply_text(
        HELP.format(bots=bot_list_hint()), parse_mode=ParseMode.HTML, reply_markup=_help_keyboard())


async def unknown_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update, context):
        return
    await update.message.reply_text("Not a command I have. /help lists them.")


async def plain_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update, context):
        return
    await update.message.reply_text("I only take commands here. /help lists them.")


async def crashtest_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Proves the crash path, the errors.log and the family alert all really
    fire -- for ManagerBot itself, or for any child bot by name.

    Naming a bot is the more useful direction and the one that was missing.
    ManagerBot crashing tells you ManagerBot's own reporting works, which you
    can see happening in front of you. What you actually want to know, when
    a bot has gone quiet, is whether *that* bot would have told you -- and
    that is the one test you could not run from here.
    """
    if not await guard(update, context):
        return
    if context.args:
        bot = resolve_bot(context.args[0])
        if not bot:
            await update.message.reply_text(
                f"No such bot: {context.args[0]}. Known: {bot_list_hint()}")
            return
        await _dispatch(update, context, bot, "crashtest", [])
        return
    raise RuntimeError("Manual /crashtest trigger -- ManagerBot's error tracking works.")


# ---------------------------------------------------------------------------
# Running an update without ambushing anybody
# ---------------------------------------------------------------------------
# Four commands, in the order they are meant to be used:
#
#   /pause           the bots stop starting work a restart would throw away,
#                    and tell whoever asks how long they expect to be. Anyone
#                    turned away is written down.
#   /warn            everyone already mid-something hears that it is about to
#                    be reset -- before the deploy, not as the door closes.
#   ...deploy, as many times as it takes...
#   /finishupdates   the bots reopen, and everyone who was turned away is
#                    told they can try again.
#
# /broadcast is the odd one out and belongs here anyway: it is the same
# "speak as the bot they actually talk to" mechanism, for anything the owner
# wants to say that these four sentences do not cover.
#
# Nothing here is on a timer. An update is usually several deploys, and a
# pause that expired on its own would let the bots reopen between two of
# them -- which is the exact window this is meant to close.

def _targets(token: str | None) -> list[dict] | None:
    """The bots a command applies to. No name, or "all", means every child --
    ManagerBot is never a target: it is the one doing the asking, and it has
    no users to announce anything to."""
    if not token or token.lower() == "all":
        return list(CHILDREN)
    bot = resolve_bot(token)
    if not bot or bot["id"] == BOT_NAME:
        return None
    return [bot]


async def _fan_out(update: Update, context: ContextTypes.DEFAULT_TYPE,
                   targets: list[dict], command: str, args: str, headline: str) -> None:
    """Queue one family command to several bots and say so once.

    Deliberately not a progress report: each bot answers in its own time and
    its answer arrives on its own, the way every other /run result does.
    """
    for bot in targets:
        await asyncio.to_thread(
            db.queue_command, bot["id"], command, args,
            update.effective_user.id, update.effective_chat.id,
        )
    names = ", ".join(b["name"] for b in targets)
    await update.message.reply_text(f"{headline}\n→ {names}")


async def pause_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/pause [bot|all] [minutes] — stop taking new long work, and say why."""
    if not await guard(update, context):
        return
    args = list(context.args)
    minutes = None
    if args and args[-1].isdigit():
        minutes = args.pop()
    targets = _targets(args[0] if args else None)
    if targets is None:
        await update.message.reply_text(
            f"Usage: /pause [bot|all] [minutes]. Known: {bot_list_hint()}"
        )
        return
    promised = minutes or str(lifecycle.DEFAULT_MAINTENANCE_MINUTES)
    await _fan_out(
        update, context, targets, "pause", minutes or "",
        f"⏸ Paused — telling users to come back in about {promised} minute(s).\n"
        f"They stay paused until /resume, however many deploys that takes.",
    )


async def warn_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/warn [bot|all] — tell whoever is mid-something that it will be reset."""
    if not await guard(update, context):
        return
    targets = _targets(context.args[0] if context.args else None)
    if targets is None:
        await update.message.reply_text(f"Usage: /warn [bot|all]. Known: {bot_list_hint()}")
        return
    await _fan_out(
        update, context, targets, "warnbusy", "",
        "📣 Warning everyone with work in flight that it is about to be reset.",
    )


async def finish_updates_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/finishupdates [bot|all] — reopen, and go back to everyone turned away.

    Explicitly the owner's call and never automatic: one update is usually
    several deploys, and only the person doing them knows which one was the
    last.
    """
    if not await guard(update, context):
        return
    targets = _targets(context.args[0] if context.args else None)
    if targets is None:
        await update.message.reply_text(
            f"Usage: /resume [bot|all]. Known: {bot_list_hint()}"
        )
        return
    await _fan_out(
        update, context, targets, "resume", "",
        "✅ Reopening — everyone who was turned away is being told they can try again.",
    )


# ---- /broadcast, which asks before it speaks ------------------------------
# Every other command here affects people who are already mid-something with
# a bot. This one reaches everyone the bot has ever met, cannot be recalled,
# and is one fat-fingered bot name away from going to the wrong audience. So
# it is the one command in ManagerBot that asks twice.

BROADCAST_PENDING = "broadcast_pending"
BROADCAST_PREVIEW = 3000


async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/broadcast <bot|all> <text> — one message to everyone, as that bot."""
    if not await guard(update, context):
        return
    if len(context.args) < 2:
        await update.message.reply_text(
            f"Usage: /broadcast &lt;bot|all&gt; [--active] &lt;text&gt;\nKnown: {bot_list_hint()}",
            parse_mode=ParseMode.HTML,
        )
        return
    targets = _targets(context.args[0])
    if targets is None:
        await update.message.reply_text(
            f"No such bot: {context.args[0]}. Known: {bot_list_hint()}"
        )
        return
    text = " ".join(context.args[1:]).strip()
    # "--active" first in the text aims it at recent users. The child bot reads
    # the flag, so it stays in what is queued; the card shows the words without it.
    aimed = text.split(" ", 1)[0] == "--active"
    shown = text.partition(" ")[2].strip() if aimed else text
    if not shown:
        await update.message.reply_text("Nothing to say — give it some text.")
        return

    context.user_data[BROADCAST_PENDING] = {
        "bots": [b["id"] for b in targets],
        "text": text,
    }
    names = ", ".join(b["name"] for b in targets)
    audience = (f"active in the last {family_link.BROADCAST_ACTIVE_DAYS} day(s)"
                if aimed else "they know")
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("Send it", callback_data="upd:send"),
        InlineKeyboardButton("Cancel", callback_data="upd:cancel"),
    ]])
    await update.message.reply_text(
        f"📢 <b>Send as {html.escape(names)}, to everyone {audience}?</b>\n\n"
        f"<pre>{html.escape(shown[:BROADCAST_PREVIEW])}</pre>\n"
        f"This cannot be taken back.",
        parse_mode=ParseMode.HTML, reply_markup=kb,
    )


async def broadcast_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not await guard(update, context):
        return
    pending = context.user_data.pop(BROADCAST_PENDING, None)
    if query.data == "upd:cancel" or not pending:
        await query.answer()
        await edit_in_place(
            query.message, context.bot,
            "Cancelled — nothing was sent." if pending else
            "That broadcast is no longer waiting. Send /broadcast again.",
        )
        return

    await query.answer()
    sent_to = []
    for bot_id in pending["bots"]:
        await asyncio.to_thread(
            db.queue_command, bot_id, "broadcast", pending["text"],
            update.effective_user.id, update.effective_chat.id,
        )
        sent_to.append(bot_id)
    await edit_in_place(
        query.message, context.bot,
        f"📢 Queued to {', '.join(sent_to)}. Each one reports back when it has "
        f"finished going through its list.",
    )


# ---- /gift, which counts before it gives ----------------------------------
# Bonus credit for everyone a bot knows, once per person across the family
# (family_link.grant_bonus_once). Like /broadcast it reaches everyone and cannot
# be taken back, and it is money: so the first step only asks each bot to count,
# and the giving waits for a button.
#
# Only the latest /gift waits, and its buttons carry its own nonce
# (gift:go:<nonce>): a tap on an older card -- another key, another amount --
# must never give the newer gift, nor cancel it.

GIFT_PENDING = "gift_pending"
GIFT_DONE = "gift_done"          # the nonce of the card last given or cancelled


async def gift_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/gift <bot|all> <key> <credits> <last day YYYY-MM-DD>"""
    if not await guard(update, context):
        return
    if len(context.args) != 4:
        await update.message.reply_text(
            "Usage: /gift &lt;bot|all&gt; &lt;key&gt; &lt;credits&gt; &lt;last day YYYY-MM-DD&gt;\n"
            "e.g. <code>/gift all gift-100-users 100 2027-01-15</code>\n"
            f"Known: {bot_list_hint()}",
            parse_mode=ParseMode.HTML,
        )
        return
    targets = _targets(context.args[0])
    if targets is None:
        await update.message.reply_text(
            f"No such bot: {context.args[0]}. Known: {bot_list_hint()}"
        )
        return
    parsed = family_link._gift_args(context.args[1:])
    if isinstance(parsed, str):
        await update.message.reply_text(parsed)
        return
    key, amount, _expires, raw_day = parsed
    args = f"{key} {amount} {raw_day}"
    for bot in targets:
        await asyncio.to_thread(
            db.queue_command, bot["id"], "gift", args,
            update.effective_user.id, update.effective_chat.id,
        )
    nonce = secrets.token_hex(4)
    context.user_data[GIFT_PENDING] = {"bots": [b["id"] for b in targets], "args": args,
                                       "nonce": nonce}
    names = ", ".join(b["name"] for b in targets)
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("Give it", callback_data=f"gift:go:{nonce}"),
        InlineKeyboardButton("Cancel", callback_data=f"gift:cancel:{nonce}"),
    ]])
    await update.message.reply_text(
        f"🎁 <b>{amount} ⚡ of bonus credit, good until {html.escape(raw_day)}</b>, "
        f"to everyone {html.escape(names)} know(s), once per person "
        f"(key <code>{html.escape(key)}</code>).\n\n"
        f"Each bot is counting now and will report how many people it would give "
        f"it to, with the notice they would read. Nothing is given until you press "
        f"<b>Give it</b>, and that cannot be taken back.",
        parse_mode=ParseMode.HTML, reply_markup=kb,
    )


async def gift_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not await guard(update, context):
        return
    await query.answer()
    # The check and the pop are one synchronous stretch, so two taps cannot both
    # give. A card that is not the waiting one's (an older /gift, or a card from
    # before the nonce) leaves the waiting gift alone.
    action, _, nonce = query.data.removeprefix("gift:").partition(":")
    if nonce and nonce == context.user_data.get(GIFT_DONE):
        # A second tap on the card just given or cancelled: its text already
        # says what happened, and "no longer waiting" over "Giving" would read
        # as if nothing had been given.
        return
    pending = context.user_data.get(GIFT_PENDING)
    if not pending:
        await edit_in_place(query.message, context.bot,
                            "That gift is no longer waiting. Send /gift again.")
        return
    if not nonce or nonce != pending.get("nonce"):
        await edit_in_place(
            query.message, context.bot,
            "That card is not the one waiting (a newer /gift replaced it, or the "
            "card is from before an update). Nothing was given. Use the newest "
            "card, or send /gift again.",
        )
        return
    context.user_data.pop(GIFT_PENDING, None)
    context.user_data[GIFT_DONE] = nonce
    key, amount = pending["args"].split()[:2]
    if action != "go":
        await edit_in_place(query.message, context.bot,
                            f"Cancelled — nothing was given ({amount} ⚡, key {key}).")
        return
    queued, failed = [], []
    for bot_id in pending["bots"]:
        try:
            await asyncio.to_thread(
                db.queue_command, bot_id, "gift", pending["args"] + " --go",
                update.effective_user.id, update.effective_chat.id,
            )
            queued.append(bot_id)
        except Exception as exc:
            failed.append(bot_id)
            logger.warning("Gift %s: could not be queued for %s (%s)", key, bot_id, type(exc).__name__)
    if queued:
        text = (f"🎁 Giving {amount} ⚡ (key {key}), through {', '.join(queued)}. "
                f"Each bot reports back how many people it gave it to when it has been "
                f"through its list.")
    else:
        text = f"🎁 Nothing was given ({amount} ⚡, key {key}): no bot could be asked."
    if failed:
        # Safe to repeat: a bot gives a key only to people who have not had it.
        text += (f"\n\n⚠️ Could not ask {', '.join(failed)} (a database error, see "
                 f"the log). Send the same /gift again and press Give it: nobody "
                 f"gets it twice.")
    await edit_in_place(query.message, context.bot, text)


# ---- the 220 ⚡ gift: the count, the owner's notice and /promo -------------
# The owner, 2026-10-18: a unique-user counter across all the bots, a message to
# him when it reaches 220, and one short command that starts the gift. This one
# gift is not general ("it dies after first usage"): family_link keeps the start
# as a row written once, ever, and everything below only asks it. A job counts
# the people every 15 minutes and tells the owner the first time the mark is
# reached; /promo shows where the gift stands and, once, starts it; /promo stop
# ends the claiming early. Every state /promo shows is read fresh from the
# database, never from a cache, because the owner acts on it.

PROMO_COUNT_SECONDS = 15 * 60
PROMO_COUNT_FIRST_SECONDS = 60
PROMO_PENDING = "promo_pending"      # the nonce of the card waiting for a tap
PROMO_DONE = "promo_done"            # the nonce of the card last started or cancelled
PROMO_BUTTON_PATTERN = r"^promo:(go|cancel):[0-9a-f]{8}$"
# What the 15-minute job last counted, {"count", "at"}, for anything that wants
# the figure without a database trip. /users and /promo count afresh instead.
_promo_count: dict = {"count": None, "at": None}


def _promo_schemas() -> list[str]:
    return [child["schema"] for child in CHILDREN]


def _promo_moment(when: datetime) -> str:
    return when.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _promo_last_day(closes: datetime) -> str:
    """The last day somebody can claim, from the midnight claiming closes at."""
    return (closes - timedelta(days=1)).strftime("%d.%m.%Y")


def _promo_db_error(exc: Exception) -> str:
    return f"Could not read the gift's state ({type(exc).__name__}). Nothing was changed."


async def count_unique_people(context: ContextTypes.DEFAULT_TYPE):
    """The 15-minute job: count the people across all the bots, and the first
    time the count reaches the mark, tell the owner once.

    The notice is sent first and the milestone written only after it went, so a
    failed send is tried again at the next count. (Two ManagerBots overlapping
    during a deploy could both send it; that is harmless, and the milestone is
    still written once.) Any error is a warning naming the kind and nothing
    more: the next count tries again."""
    try:
        count = await asyncio.to_thread(db.unique_people, _promo_schemas())
        _promo_count.update(count=count, at=datetime.now(timezone.utc))
        if count < family_link.PROMO_USERS:
            return
        if await asyncio.to_thread(family_link.promo_milestone_at) is not None:
            return
        # Already started (the owner saw the count in /users and did not wait
        # for this): "start it with /promo" would be stale, so only note it.
        started = await asyncio.to_thread(family_link.promo_window, True)
        if started is not None:
            await asyncio.to_thread(family_link.note_promo_milestone)
            return
        delivered = await notify_owner(
            context,
            f"🎉 Mumu bots have reached {count} unique users (everyone who has used any of the bots, "
            f"counted once). That's the mark for the {family_link.PROMO_CREDITS} ⚡ gift: start it with /promo.",
        )
        if delivered is False:
            logger.warning("The gift's milestone notice could not be delivered; trying again at the next count")
            return
        await asyncio.to_thread(family_link.note_promo_milestone)
    except Exception as exc:
        logger.warning("The unique-people count did not finish (%s); trying again at the next one",
                       type(exc).__name__)


def _promo_status_text(started: datetime, closes: datetime, claims: int, seen: "int | None") -> str:
    now = datetime.now(timezone.utc)
    is_open = now < closes
    stopped_early = closes < family_link.promo_closes_for(started)
    text = (f"The {family_link.PROMO_CREDITS} ⚡ gift started {_promo_moment(started)}.\n"
            f"Claims {'are open until' if is_open else 'closed at'} {_promo_moment(closes)}"
            f"{' -- stopped early' if stopped_early else ''}.\n"
            f"Claimed by {claims} people. AnonBot showed the ad to {'?' if seen is None else seen} people.")
    if is_open:
        text += "\nTo end the claiming now: /promo stop"
    return text


def _promo_card_text(count: int) -> str:
    """The card, in HTML. Everything on it is a number or a date from the code
    except the ad itself, which is HTML on purpose (its last paragraph is in
    italics), so the rest is escaped and the ad is not."""
    closes = family_link.promo_closes_for(datetime.now(timezone.utc))
    claim_by = html.escape(_promo_last_day(closes))
    amount, spend_days = family_link.PROMO_CREDITS, family_link.PROMO_SPEND_DAYS
    return (
        f"Start the {amount} ⚡ gift? This can be done once.\n"
        f"• Anyone who writes to ConvertBot or DownloaderBot until {claim_by} (the end of that day, UTC) "
        f"gets {amount} ⚡ of bonus credit, once, automatically, and a notice.\n"
        f"• Each gift can be spent for {spend_days} days from the day it is claimed.\n"
        f"• AnonBot shows this ad once to each person who writes to it, until then:\n\n"
        f"{family_link.promo_ad('en', closes)}\n\n"
        f"Unique users now: {count}."
    )


async def promo_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/promo -- where the 220 ⚡ gift stands, and once the mark is reached a
    card to start it; /promo stop -- end the claiming early."""
    if not await guard(update, context):
        return
    args = [arg.lower() for arg in context.args]
    if args == ["stop"]:
        await _promo_stop(update)
        return
    if args:
        await update.message.reply_text("Usage: /promo, or /promo stop.")
        return
    try:
        window = await asyncio.to_thread(family_link.promo_window, True)
        if window is not None:
            claims = await asyncio.to_thread(family_link.promo_claim_count)
            anon = next((child for child in CHILDREN if child["id"] == "anonbot"), None)
            seen = (await asyncio.to_thread(db.promo_seen_count, anon["schema"], family_link.PROMO_KEY)
                    if anon else None)
            await update.message.reply_text(_promo_status_text(window[0], window[1], claims, seen))
            return
        count = await asyncio.to_thread(db.unique_people, _promo_schemas())
        milestone = await asyncio.to_thread(family_link.promo_milestone_at)
    except Exception as exc:
        await update.message.reply_text(_promo_db_error(exc))
        return
    if count < family_link.PROMO_USERS and milestone is None:
        await update.message.reply_text(
            f"The {family_link.PROMO_CREDITS} ⚡ gift can start once the bots have {family_link.PROMO_USERS} "
            f"unique users. Now: {count}. I'll message you when it happens.")
        return
    nonce = secrets.token_hex(4)
    context.user_data[PROMO_PENDING] = nonce
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("▶️ Start the gift", callback_data=f"promo:go:{nonce}"),
        InlineKeyboardButton("Cancel", callback_data=f"promo:cancel:{nonce}"),
    ]])
    await update.message.reply_text(_promo_card_text(count), parse_mode=ParseMode.HTML, reply_markup=kb)


async def _promo_stop(update: Update):
    try:
        stopped = await asyncio.to_thread(family_link.stop_promo)
    except Exception as exc:
        await update.message.reply_text(_promo_db_error(exc))
        return
    if not stopped:
        await update.message.reply_text("The gift is not open (not started yet, or already over). Nothing to stop.")
        return
    try:
        claims = await asyncio.to_thread(family_link.promo_claim_count)
    except Exception:
        claims = "?"
    await update.message.reply_text(
        f"Stopped. Nobody can claim the gift any more; the {claims} people who have it keep it until it runs out.")


async def promo_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """A tap on the card. Like gift_button, only the waiting card's nonce counts,
    and the check and the pop are one synchronous stretch, so two taps cannot
    both start it; family_link.start_promo is once-only in the database besides."""
    query = update.callback_query
    if not await guard(update, context):
        return
    await query.answer()
    action, _, nonce = query.data.removeprefix("promo:").partition(":")
    if nonce and nonce == context.user_data.get(PROMO_DONE):
        # A second tap on the card just started or cancelled: its text already
        # says what happened.
        return
    if not nonce or nonce != context.user_data.get(PROMO_PENDING):
        await edit_in_place(
            query.message, context.bot,
            "That card is not the one waiting (a newer /promo replaced it, or it is from before an "
            "update). Nothing was started. Send /promo again.",
        )
        return
    context.user_data.pop(PROMO_PENDING, None)
    context.user_data[PROMO_DONE] = nonce
    if action != "go":
        await edit_in_place(query.message, context.bot, "Cancelled. Nothing was started.")
        return
    # Look again now: the card may be minutes old, and the gift may have been
    # started from another card, or the count may be what the card said.
    try:
        window = await asyncio.to_thread(family_link.promo_window, True)
        if window is not None:
            text = f"It had already started {_promo_moment(window[0])}. Nothing changed."
        else:
            count = await asyncio.to_thread(db.unique_people, _promo_schemas())
            reached = count >= family_link.PROMO_USERS or (
                await asyncio.to_thread(family_link.promo_milestone_at)) is not None
            if not reached:
                text = (f"Not yet: {count} unique users of {family_link.PROMO_USERS}. "
                        f"Nothing was started.")
            else:
                started, won = await asyncio.to_thread(family_link.start_promo)
                if won:
                    text = (f"Started {_promo_moment(started)}. Claims close "
                            f"{_promo_last_day(family_link.promo_closes_for(started))} "
                            f"(the end of that day, UTC). /promo shows how it goes.")
                else:
                    text = f"It had already started {_promo_moment(started)}. Nothing changed."
    except Exception as exc:
        text = f"Could not start it ({type(exc).__name__}). Nothing was started; send /promo again."
    await edit_in_place(query.message, context.bot, text)


# The menu the owner sees, in the order HELP lists them: watching, reaching
# into a bot, shipping an update, then the family-wide reads. It is a private
# bot with one user, so a command left out of the menu is a command that has to
# be remembered instead -- which is how /probe and /stars went a version
# without being typed once, and how /providers went missing again in 2.1.0
# (2026-09-29, the owner typing "/pro" and being offered /problems alone).
#
# What is left out on purpose is only what is a second name for something in
# the list, or would do harm when tapped by accident: see UNLISTED in
# tests/test_menu.py, which holds this list to that.
#
# A description is kept under 50 characters. Telegram's limit is 256, but the
# menu is a popup, and a long line is cut where the window ends -- the owner's
# screenshot of /problems read "...the midnight report" with the rest gone
# (the report has been at 22:00 since 2.1.0).
BOT_COMMANDS = [
    # Grouped by what the owner is in the middle of, because that is how it is
    # read at four in the morning: is everything alright, do something to a
    # bot, talk to somebody, money, the database.
    #
    # Gone from here, still working, because they are second names: /me (its
    # own status, which /status already carries), /errorlog, /botlog and
    # /problemlog (/logs asks which), /finishupdates (/resume). Not /nightly or
    # /reports, which the owner looked for in the menu and did not find
    # (2026-10-10): /nightly is the same report as /problems and is listed under
    # the name he knows it by; and /reports: it is its own command, the raw list of recent
    # problems with their incident codes, which /report <code> then opens. It
    # was written off here as "/problems now" and so left out of the menu for
    # three versions, while /report beside it was offered (found by the owner,
    # 2026-10-10). And /crashtest, which makes a
    # bot raise on purpose and page the owner -- a command a stray tap on the
    # menu should not be able to send. /help names every one of them.
    #
    # /providers, /probe and /stars are here: each reaches the one bot that
    # has it (/run reaches them too, but only by naming the bot and the
    # command), and they were dropped in 1.7.0 as "reachable through /run",
    # which is how a command becomes one nobody is offered.
    BotCommand("status", "Every bot: up, version, errors, users"),
    BotCommand("problems", "The 22:00 problems report, on demand"),
    BotCommand("nightly", "The same report, under its old name"),
    BotCommand("feedback", "What people sent with /feedback"),
    BotCommand("reports", "Latest problems, each with its incident code"),
    BotCommand("report", "One problem in full, by its incident code"),
    BotCommand("errors", "What each bot has hit since it started"),
    BotCommand("events", "Crashes, restarts and payments as they happened"),
    BotCommand("ping", "How fast each bot is, for a user and on the bus"),
    BotCommand("providers", "Which download routes work, which rest"),
    BotCommand("probe", "Test every download route, from its container"),
    BotCommand("logs", "Tail a bot's log"),
    BotCommand("usage", "Memory, peaks, and what they cost"),
    BotCommand("idle", "How quiet each bot is; would sleeping pay?"),
    BotCommand("buttons", "Button taps per bot, counts only"),
    BotCommand("run", "Run a command inside another bot"),
    BotCommand("restart", "Restart a bot's process"),
    BotCommand("pause", "Stop the bots taking work an update would lose"),
    BotCommand("warn", "Tell whoever is mid-something that it will reset"),
    BotCommand("resume", "Reopen, and tell everyone who was waiting"),
    BotCommand("say", "Message one person, as one of the bots"),
    BotCommand("broadcast", "Message everyone, as one of the bots"),
    BotCommand("whois", "Look a user up through a bot"),
    BotCommand("users", "Active users per bot"),
    BotCommand("balance", "See or change a credit balance"),
    BotCommand("addcredit", "Add ⚡ credit to an account by Telegram id"),
    BotCommand("gift", "Bonus ⚡ for everyone; counts, then asks"),
    BotCommand("promo", "The 220 ⚡ gift: start, status, stop"),
    BotCommand("donations", "Paid donations per bot"),
    BotCommand("stars", "ConvertBot's Stars: paid, refunded, recent"),
    BotCommand("backup", "Whole database, restorable, pinned here"),
    BotCommand("dbdump", "One bot's tables as CSVs"),
    BotCommand("sql", "Read-only query on the shared database"),
    BotCommand("decode", "What an error code means"),
    BotCommand("alerts", "Mute or unmute alerts"),
    BotCommand("help", "What all of this does"),
]

# And what anybody else gets, which is the truth: one command, and it says no.
# The list above was going up at the default scope, so a stranger who found
# the private bot was handed a menu reading /sql, /backup, /restart. Every one
# of them refuses them -- guard() is the thing that decides, not the menu --
# but a list of the levers is an invitation to lean on them, and there is no
# reason to publish it.
STRANGER_COMMANDS = [
    BotCommand("start", "what this bot is"),
]


async def _publish_commands(application):
    """The full menu in each owner's chat, and one line for everybody else.

    Never raises. set_my_commands for a chat Telegram has never seen fails,
    and an owner who has not opened their own bot yet is exactly that chat --
    a cosmetic call is not a reason to fail a deploy. Same shape as
    shared_features.publish_commands() in the four public bots; separate
    because ManagerBot's default scope is a refusal rather than a shorter menu.
    """
    try:
        await application.bot.set_my_commands(STRANGER_COMMANDS)
    except Exception:
        logger.warning("Couldn't publish the public command menu.", exc_info=True)
    for admin_id in sorted(ADMIN_IDS):
        try:
            await application.bot.set_my_commands(
                BOT_COMMANDS, scope=BotCommandScopeChat(chat_id=admin_id))
        except Exception:
            logger.warning("Couldn't publish the owner's command menu to %s.",
                           admin_id, exc_info=True)
    # Scopes nothing here writes, cleared so they cannot outrank the ones it
    # does -- see _UNOWNED_SCOPES in the public bots' shared_features.py.
    from telegram import (BotCommandScopeAllChatAdministrators, BotCommandScopeAllGroupChats,
                          BotCommandScopeAllPrivateChats)
    for scope in (BotCommandScopeAllPrivateChats, BotCommandScopeAllGroupChats,
                  BotCommandScopeAllChatAdministrators):
        try:
            await application.bot.delete_my_commands(scope=scope())
        except Exception:
            logger.debug("Couldn't clear the %s menu.", scope.__name__, exc_info=True)


# The two lines a stranger meets before they press anything: the blurb beside
# the bot in search results, and what an empty chat shows above the Start
# button. The four public bots have had these since v1.4.0 and ManagerBot was
# skipped, on the reasoning that a private bot with one user has no audience
# for them. That is the wrong way round -- the owner is the one person who
# never reads them, and the stranger who has just found a bot called
# "mumu manager" is exactly who they are for. Untranslated, like everything
# else here: whoever is reading them is not a user of this bot.
SHORT_DESCRIPTION = "A private bot. It looks after one person's other bots."


def _description() -> str:
    """Read off USERNAME_ALIASES rather than typed out again, so a bot that
    gets renamed is renamed here too. ManagerBot itself is left out: pointing
    a stranger at the bot they are already looking at helps nobody."""
    siblings = ", ".join(
        "@" + USERNAME_ALIASES[bot["id"]][0]
        for bot in CHILDREN if USERNAME_ALIASES.get(bot["id"])
    )
    return (
        "This bot is not for general use and it will not answer you. It "
        "watches the public bots in this family — whether they are up, what "
        "they are doing, and when one of them breaks — and takes commands "
        "from their owner only.\n\n"
        f"The bots it looks after: {siblings}."
    )


async def _publish_profile(application):
    """Never raises, same reasoning as the menu above."""
    try:
        await application.bot.set_my_short_description(SHORT_DESCRIPTION)
        await application.bot.set_my_description(_description())
    except Exception:
        logger.warning("Couldn't publish ManagerBot's profile text.", exc_info=True)


async def _post_init(application):
    await tune_runtime(application)
    await lifecycle.on_start(BOT_NAME)
    await _publish_commands(application)
    await _publish_profile(application)


async def _post_stop(application):
    await lifecycle.on_stop(application)
    await flush_on_shutdown(application)


def main():
    if not BOT_TOKEN or not BOT_USERNAME:
        raise SystemExit("Set MBOT_TOKEN and MBOT_USERNAME environment variables first.")
    if not ADMIN_IDS:
        # Every other bot treats an empty admin list as "disable the admin
        # commands". Here that would leave the entire bot open, so it is a
        # hard stop instead.
        raise SystemExit(
            "MBOT_ADMIN_ID is empty. ManagerBot is owner-only by definition and "
            "refuses to start without knowing who the owner is -- put your numeric "
            "Telegram id there (get it from @userinfobot)."
        )

    init_db()

    builder = (
        ApplicationBuilder().token(BOT_TOKEN)
        .post_init(_post_init).post_stop(_post_stop)
    )
    state = lifecycle.persistence()
    if state is not None:
        builder = builder.persistence(state)
    app = builder.build()
    lifecycle.install(app, BOT_NAME)
    app.add_error_handler(error_handler)
    # Its own group -- see the note in the child bots' main(): a TypeHandler
    # on Update matches everything, so anything sharing a group with it never
    # runs. ManagerBot has nothing else up here today; the numbering is what
    # keeps that true when it does.
    app.add_handler(TypeHandler(Update, track_activity), group=-3)

    app.add_handler(CommandHandler(["start", "help"], start_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("usage", usage_command))
    app.add_handler(CommandHandler("idle", idle_command))
    app.add_handler(CommandHandler("buttons", buttons_command))
    app.add_handler(CommandHandler("me", me_command))
    app.add_handler(CommandHandler("run", run_command))
    app.add_handler(CommandHandler("ping", broadcast_ping))
    app.add_handler(CommandHandler("users", users_command))
    app.add_handler(CommandHandler("donations", donations_command))
    app.add_handler(CommandHandler("balance", balance_command))
    app.add_handler(CommandHandler("addcredit", addcredit_command))
    app.add_handler(CommandHandler("events", events_command))
    app.add_handler(CommandHandler("sql", sql_command))
    app.add_handler(CommandHandler("backup", backup_command))
    app.add_handler(CommandHandler("reports", reports_command))
    app.add_handler(CommandHandler("report", report_command))
    # /problems is the name; /nightly is what it was called when it was only
    # the thing that ran at midnight, and still works. /reports is not a name
    # for it: it is the raw list of recent problems with their incident codes.
    app.add_handler(CommandHandler(["problems", "nightly"], nightly_command))
    app.add_handler(CommandHandler("feedback", feedback_command))
    app.add_handler(CommandHandler("decode", decode_command))
    app.add_handler(CommandHandler("alerts", alerts_command))
    app.add_handler(CommandHandler("crashtest", crashtest_command))

    app.add_handler(CommandHandler("errors", errors_command))
    app.add_handler(CommandHandler("errorlog", _log_shortcut("errorlog")))
    app.add_handler(CommandHandler("botlog", _log_shortcut("botlog")))
    app.add_handler(CommandHandler("problemlog", _log_shortcut("problemlog")))
    app.add_handler(CommandHandler("logs", _shortcut("logs", 1, "Usage: /logs <bot> [lines] [bot|problems]")))
    app.add_handler(CommandHandler("dbdump", _shortcut("dbdump", 1, "Usage: /dbdump <bot> — or /backup for everything")))
    app.add_handler(CommandHandler("restart", _shortcut("restart", 1, "Usage: /restart <bot>")))
    app.add_handler(CommandHandler("whois", _shortcut("whois", 2, "Usage: /whois <bot> <user_id>")))
    app.add_handler(CommandHandler("say", _shortcut("message", 3, "Usage: /say <bot> <user_id> <text>")))
    # The bot-specific ones. Each defaults to the only bot that has it, so
    # the command centre does not make you remember which bot owns what --
    # /providers and /stars are unambiguous, and typing the bot name is
    # still allowed for when that stops being true.
    app.add_handler(CommandHandler("providers", _shortcut("providers", 0, "", default_bot="downloaderbot")))
    app.add_handler(CommandHandler("probe", _shortcut("probe", 0, "", default_bot="downloaderbot")))
    app.add_handler(CommandHandler("stars", _shortcut("stars", 0, "", default_bot="convertbot")))

    app.add_handler(CommandHandler("broadcast", broadcast_command))
    app.add_handler(CommandHandler("gift", gift_command))
    app.add_handler(CommandHandler("promo", promo_command))
    app.add_handler(CommandHandler("pause", pause_command))
    app.add_handler(CommandHandler("warn", warn_command))
    # /pause stops the bots for an update, so its opposite should be called
    # /resume rather than /finishupdates -- which is also what every child bot
    # calls it on the bus. The old name keeps working.
    app.add_handler(CommandHandler(["resume", "finishupdates"], finish_updates_command))

    app.add_handler(CallbackQueryHandler(board_button, pattern=r"^board:"))
    app.add_handler(CallbackQueryHandler(ping_detail_button, pattern=r"^pingdet:"))
    app.add_handler(CallbackQueryHandler(help_page_button, pattern=r"^helpdet:[a-z]{1,20}$"))
    app.add_handler(CallbackQueryHandler(broadcast_button, pattern=r"^upd:"))
    app.add_handler(CallbackQueryHandler(gift_button, pattern=r"^gift:"))
    app.add_handler(CallbackQueryHandler(promo_button, pattern=PROMO_BUTTON_PATTERN))
    app.add_handler(CallbackQueryHandler(log_pick_button, pattern=r"^logpick:"))
    app.add_handler(CallbackQueryHandler(feedback_mode_button, pattern=r"^fbmode:"))
    app.add_handler(MessageHandler(filters.COMMAND, unknown_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, plain_text))

    if app.job_queue is None:
        # Every other bot degrades quietly without one (family_link just stays
        # off). ManagerBot cannot: the watchdog, the alert pump and the command
        # results are all jobs, and without them it would sit there looking
        # healthy while watching nothing at all.
        raise SystemExit(
            "No JobQueue available, which means no watchdog, no alerts and no /run "
            'results -- ManagerBot would be a shell. Install it with:\n'
            '    pip install "python-telegram-bot[job-queue]"'
        )

    # Before the watchdog's first pass, so the roll-call is what seeds
    # family.bot_state and the watchdog has nothing left to announce.
    app.job_queue.run_once(startup_rollcall, when=STARTUP_ROLLCALL_SECONDS)
    app.job_queue.run_repeating(watchdog, interval=60, first=20)
    # The 220 gift's counter: the owner is told once when the bots reach the mark.
    app.job_queue.run_repeating(count_unique_people, interval=PROMO_COUNT_SECONDS,
                                first=PROMO_COUNT_FIRST_SECONDS)
    # ManagerBot collects command results and child-bot events on the same
    # adaptive poll the child bots run for the command queue: a fast tick that
    # decides each time whether to sweep -- every *_POLL_FAST_SECONDS while the
    # bus is active (set the moment ManagerBot queues a command), every
    # *_POLL_SECONDS once it has gone quiet. There is no push behind it, so
    # there is nothing to be "down": a pump that is late is late by one idle
    # interval, not by the life of the process.
    app.job_queue.run_repeating(_event_backstop, interval=PUMP_TICK_SECONDS, first=10)
    app.job_queue.run_repeating(_result_backstop, interval=PUMP_TICK_SECONDS, first=5)

    # One sweep of each at startup -- however late the job queue actually
    # starts (see family_link.RUN_LATE) -- to clear anything a previous run
    # left queued before the first tick falls due.
    app.job_queue.run_once(result_pump, when=0, job_kwargs=family_link.RUN_LATE)
    app.job_queue.run_once(event_pump, when=0, job_kwargs=family_link.RUN_LATE)
    if DIGEST_AT_UTC:
        hour, _, minute = DIGEST_AT_UTC.partition(":")
        app.job_queue.run_daily(
            daily_digest, time(int(hour), int(minute or 0), tzinfo=timezone.utc)
        )
        logger.info("Daily digest scheduled for %s UTC.", DIGEST_AT_UTC)
    if NIGHTLY_REPORT_AT_UTC:
        hour, _, minute = NIGHTLY_REPORT_AT_UTC.partition(":")
        app.job_queue.run_daily(
            nightly_problem_report, time(int(hour), int(minute or 0), tzinfo=timezone.utc)
        )
        logger.info("Nightly problem report scheduled for %s UTC (22:00 in Seoul at the default).",
                    NIGHTLY_REPORT_AT_UTC)

    # ManagerBot rides the same bus it runs: it heartbeats like everyone else,
    # so a second ManagerBot (or a plain SQL query) can see whether it is alive.
    family_link.attach(app, BOT_NAME, DISPLAY_NAME, START_TIME)
    attach_maintenance(app)

    logger.info("ManagerBot starting (polling). Watching: %s", bot_list_hint())
    # A 30-second long poll is the same latency as the default 10 -- Telegram
    # answers the moment an update exists -- for a third of the HTTP requests.
    # ManagerBot has one user, so nearly every request it makes all day is an
    # empty poll.
    app.run_polling(**lifecycle.polling_kwargs(
        timeout=POLL_TIMEOUT,
        allowed_updates=[Update.MESSAGE, Update.CALLBACK_QUERY],
    ))


if __name__ == "__main__":
    main()
