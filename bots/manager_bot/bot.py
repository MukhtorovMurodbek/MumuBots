import asyncio
import contextlib
import functools
import hashlib
import html
import json
import logging
import os
import re
import secrets
import time as time_module
import zipfile
from collections import OrderedDict, deque
from itertools import takewhile
from datetime import datetime, time, timedelta, timezone
from io import BytesIO

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from telegram import (
    BotCommand, BotCommandScopeChat, InlineKeyboardButton,
    InlineKeyboardMarkup, LinkPreviewOptions, Update,
)
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder,
    ApplicationHandlerStop,
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
import offline_key
import problems
import redact
import reserve
import reserve_copies
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
BOT_USERNAME = (os.environ.get("MBOT_USERNAME") or "").strip().lstrip("@") or None
BOT_NAME = "managerbot"
DISPLAY_NAME = "ManagerBot"

ADMIN_IDS = {int(x) for x in os.environ.get("MBOT_ADMIN_ID", "").split(",") if x.strip()}

DOWN_AFTER_SECONDS = int(os.environ.get("MBOT_DOWN_AFTER_SECONDS", "120"))

COMMAND_TIMEOUT_SECONDS = int(os.environ.get("MBOT_COMMAND_TIMEOUT_SECONDS", "90"))

RESULT_POLL_SECONDS = int(os.environ.get("MBOT_RESULT_POLL_SECONDS", "20"))
EVENT_POLL_SECONDS = int(os.environ.get("MBOT_EVENT_POLL_SECONDS", "30"))
RESULT_POLL_FAST_SECONDS = int(os.environ.get("MBOT_RESULT_POLL_FAST_SECONDS", "1"))
EVENT_POLL_FAST_SECONDS = int(os.environ.get("MBOT_EVENT_POLL_FAST_SECONDS", "2"))
PUMP_TICK_SECONDS = int(os.environ.get("MBOT_PUMP_TICK_SECONDS", "1"))

REDEPLOY_GRACE_SECONDS = int(os.environ.get("MBOT_REDEPLOY_GRACE_SECONDS", "300"))

STARTUP_ROLLCALL_SECONDS = int(os.environ.get("MBOT_ROLLCALL_SECONDS", "5"))

ROLLCALL_FRESH_SECONDS = int(os.environ.get("MBOT_ROLLCALL_FRESH_SECONDS", "120"))

ALERT_LEVELS = {"warning", "error", "critical"}

DIGEST_AT_UTC = os.environ.get("MBOT_DIGEST_UTC", "").strip()

NIGHTLY_REPORT_AT_UTC = os.environ.get("MBOT_NIGHTLY_UTC", "13:00").strip()
NIGHTLY_REPORT_HOURS = int(os.environ.get("MBOT_NIGHTLY_HOURS", "24"))

TELEGRAM_MAX_CHARS = 3900

POLL_TIMEOUT = int(os.environ.get("POLL_TIMEOUT", "30"))


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


_reported_strangers: set[int] = set()


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


async def guard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user = update.effective_user
    if user and is_admin(user.id):
        return True
    if user and user.id not in _reported_strangers:
        _reported_strangers.add(user.id)
        await notify_owner(context, "👀 A stranger found ManagerBot, and was turned away.")
        await asyncio.to_thread(
            db.log_event, BOT_NAME, "warning", "stranger",
            "An unknown user messaged ManagerBot.",
        )
    if update.message:
        await update.message.reply_text("This is a private bot.")
    return False


async def strangers_stop_here(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is not None and is_admin(user.id):
        return
    if update.message is not None and user is not None:
        await guard(update, context)
    elif update.callback_query is not None:
        with contextlib.suppress(Exception):
            await update.callback_query.answer()
    raise ApplicationHandlerStop


async def notify_owner(context: ContextTypes.DEFAULT_TYPE, text: str) -> bool:
    delivered = False
    text = redact.redact(text)
    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_message(chat_id=admin_id, text=text, parse_mode=ParseMode.HTML)
            delivered = True
        except Exception:
            logger.exception("Couldn't deliver an alert to %s", admin_id)
    return delivered


async def send_long(context: ContextTypes.DEFAULT_TYPE, chat_id: int, text: str, filename: str = "output.txt") -> None:
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


def _build_board(sweep_line: str = "") -> str:
    now = datetime.now(timezone.utc)
    snapshot = db.status_snapshot([b["schema"] for b in ALL_BOTS], now - timedelta(hours=1))
    beats = {row["bot_id"]: row for row in snapshot["beats"]}
    active_by_schema = snapshot["active"]

    up_count = 0
    lines = []
    for bot in ALL_BOTS:
        beat = beats.get(bot["id"])
        me = " (this bot)" if bot["id"] == BOT_NAME else ""
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
            f"· v{html.escape(beat.get('release') or beat['version'])}{errors}{users}"
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


MEMORY_USD_PER_MB_MONTH = float(os.environ.get("MEMORY_USD_PER_MB_MONTH") or 0.01)
USAGE_REPORT_HOURS = int(os.environ.get("USAGE_REPORT_HOURS") or 24)
CPU_USD_PER_VCPU_MONTH = float(os.environ.get("CPU_USD_PER_VCPU_MONTH") or 20)


def _cpu_windows(rows: list[dict]) -> list[tuple[float, float]]:
    ordered = sorted((row for row in rows if row.get("cpu_seconds") is not None and row.get("sampled_at")),
                     key=lambda row: row["sampled_at"])
    found = []
    for before, after in zip(ordered, ordered[1:]):
        used = after["cpu_seconds"] - before["cpu_seconds"]
        seconds = (after["sampled_at"] - before["sampled_at"]).total_seconds()
        if used < 0 or seconds <= 0 or seconds > 2 * (after.get("window_minutes") or 15) * 60:
            continue
        found.append((used, seconds))
    return found


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
        windows = _cpu_windows(rows)
        if windows:
            average_cpu = sum(used for used, _ in windows) / sum(seconds for _, seconds in windows)
            busiest_cpu = max(used / seconds for used, seconds in windows)
            line += (f"\n  CPU: {average_cpu:.2f} vCPU on average, {busiest_cpu:.2f} in the busiest "
                     f"{monitoring.USAGE_SAMPLE_MINUTES} minutes ≈ "
                     f"${average_cpu * CPU_USD_PER_VCPU_MONTH:.2f}/month")
        line += f"\n  {updates} update(s), busiest window {people} person(s)"
        lines.append(line)

    lines.append(f"\n<b>All five \u2248 ${total_cost:.2f}/month in memory</b> "
                 f"at ${MEMORY_USD_PER_MB_MONTH:.3f} per MB-month.")
    lines.append("\nMemory is the bill: a container is paid for every minute it holds "
                 "what it holds, busy or not.")
    return "\n".join(lines)


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


BUTTONS_DEFAULT_DAYS = 7
BUTTONS_MAX_DAYS = 90
BUTTONS_MAX_LINES = 25
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


STATS_DEFAULT_DAYS = 30
STATS_MAX_DAYS = 400
STATS_MAX_LINES = 12
STATS_USAGE = f"Usage: /stats [days] -- from 1 to {STATS_MAX_DAYS} days, {STATS_DEFAULT_DAYS} if not said."
STATS_LABELS = {
    "link_site": "Links delivered, by site",
    "link_media": "Media delivered, site:kind",
    "link_extra": "Extras",
    "conversion": "Conversions, from>to",
    "sticker": "Stickers added",
    "sticker_pack": "Packs",
    "video_fit_offered": "Clips that didn't fit",
    "video_fit": "What was done about them",
    "anon_relay": "Messages relayed",
}


def _stats_report(days: int) -> str:
    rows = family_link.stats_history(days)
    if not rows:
        return "No statistics recorded yet."
    per_bot: dict[str, dict[str, list]] = {}
    for row in rows:
        per_bot.setdefault(row["bot_id"], {}).setdefault(row["metric"], []).append((row["key"], int(row["n"])))
    names = {child["id"]: child["name"] for child in CHILDREN}
    order = [child["id"] for child in CHILDREN if child["id"] in per_bot]
    order += sorted(bot_id for bot_id in per_bot if bot_id not in names)
    lines = [f"Statistics, last {days} days (anonymous daily counts, nothing about who)"]
    for bot_id in order:
        lines += ["", names.get(bot_id, bot_id)]
        for metric, items in per_bot[bot_id].items():
            total = sum(n for _, n in items)
            lines.append(f"  {STATS_LABELS.get(metric, metric)} — {total}")
            for key, n in items[:STATS_MAX_LINES]:
                lines.append(f"    {key}: {n} ({n * 100 // total}%)")
            if len(items) > STATS_MAX_LINES:
                lines.append(f"    +{len(items) - STATS_MAX_LINES} more")
    return "\n".join(lines)


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update, context):
        return
    days = STATS_DEFAULT_DAYS
    if context.args:
        try:
            days = int(context.args[0])
        except ValueError:
            days = 0
        if len(context.args) > 1 or not 1 <= days <= STATS_MAX_DAYS:
            await update.message.reply_text(STATS_USAGE)
            return
    try:
        report = await asyncio.to_thread(_stats_report, days)
    except Exception as exc:
        await update.message.reply_text(f"\u26a0\ufe0f Couldn't read the statistics: {exc}")
        return
    await send_long(context, update.effective_chat.id, report, "stats.txt")


async def board_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
        await query.message.reply_text(
            f"⚠️ Couldn't read the family database: {exc}\n\n"
            "Every bot may well be fine -- this is ManagerBot's own connection failing."
        )
        return
    await edit_in_place(query.message, context.bot, board,
                        parse_mode=ParseMode.HTML, reply_markup=_board_keyboard())


async def me_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update, context):
        return
    now = datetime.now(timezone.utc)
    hour = await asyncio.to_thread(db.count_active_users_since, now - timedelta(hours=1))
    since_start = await asyncio.to_thread(db.count_active_users_since, START_TIME)
    await update.message.reply_text(build_status_text(START_TIME, hour, since_start))


COMMAND_SINCE = {
    "probe": "1.2.1",
    "routewatch": "2.1.0",
    "versions": "2.1.0",
    "providers": "1.2.3",
    "stars": "1.2.3",
    "crashtest": "1.2.3",
    "gift": "2.1.1",
    "softstop": "2.1.6",
    "softstopbot": "2.1.6",
}


def _version_key(version: str | None):
    if not version:
        return None
    parts = []
    for chunk in str(version).strip().split("."):
        digits = "".join(takewhile(str.isdigit, chunk))
        if not digits:
            return None
        parts.append((int(digits), chunk[len(digits):]))
    return tuple(parts)


FLAG_SINCE = {("broadcast", "--active"): "1.3.0", ("logs", "problems"): "1.6.0"}


def _too_old_for(bot_version: str | None, command: str, args: list[str] | None = None) -> str | None:
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
    listed = (beat or {}).get("commands")
    if not listed:
        return set(family_link.COMMANDS)
    return {name.strip() for name in listed.split(",") if name.strip()}


async def _dispatch(update: Update, context: ContextTypes.DEFAULT_TYPE,
                    bot: dict, command: str, args: list[str]) -> None:
    if bot["id"] == BOT_NAME:
        await update.effective_message.reply_text(
            "That one is ManagerBot itself. Use /status, /me, /events or /backup directly."
        )
        return
    beat = await asyncio.to_thread(db.heartbeat_of, bot["id"])

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

    if command in ("softstop", "softstopbot", "restart"):
        args = [arg for arg in args if not arg.replace(".", "", 1).isdigit()] + [str(int(time_module.time()))]
    command_id = await asyncio.to_thread(
        db.queue_command, bot["id"], command, " ".join(args),
        update.effective_user.id, update.effective_chat.id,
    )
    await update.effective_message.reply_text(f"→ {bot['name']} · {command} — queued as #{command_id}")


SERVICE_ASK_SECONDS = 120
_SERVICE_ASKS: "OrderedDict[str, dict]" = OrderedDict()


async def _service_of(update: Update, bot_word: str):
    bot = resolve_bot(bot_word)
    if bot is None or bot["id"] == BOT_NAME:
        await update.message.reply_text(
            "That one is ManagerBot itself -- it is stopped or restarted from the host." if bot else
            f"No such bot: {bot_word}. Known: {bot_list_hint()}")
        return None
    try:
        beats = {row["bot_id"]: row for row in await asyncio.to_thread(db.all_heartbeats)}
    except Exception as exc:
        await update.message.reply_text(f"Could not read the heartbeats, so nothing was asked: {exc}")
        return None
    beat = beats.get(bot["id"])
    if beat is None or beat["seconds_ago"] > DOWN_AFTER_SECONDS:
        await update.message.reply_text(f"{bot['name']} is not running, so there is nothing to do.")
        return None
    host = beat.get("host")
    together = sorted(row["bot_id"] for row in beats.values()
                      if host and row.get("host") == host and row["seconds_ago"] <= DOWN_AFTER_SECONDS)
    together = together or [bot["id"]]
    mine = beats.get(BOT_NAME) or {}
    if BOT_NAME in together or (host and mine.get("host") == host):
        await update.message.reply_text(
            f"{bot['name']} runs in the same service as ManagerBot, so this would take ManagerBot down too. "
            "Do it from the host.")
        return None
    return bot, together


def _names(bot_ids) -> str:
    return ", ".join((resolve_bot(bot_id) or {}).get("name", bot_id) for bot_id in bot_ids)


def _ask_service(kind: str, bot: dict, together: list, force: bool = False) -> str:
    nonce = secrets.token_hex(4)
    _SERVICE_ASKS[nonce] = {"kind": kind, "bot": bot["id"], "together": together, "force": force,
                            "at": time_module.monotonic()}
    while len(_SERVICE_ASKS) > 32:
        _SERVICE_ASKS.popitem(last=False)
    return nonce


async def shutdown_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update, context):
        return
    if len(context.args) != 1:
        await update.message.reply_text(
            "Usage: /shutdown <bot>\n"
            "Asks first: that bot alone, or the whole service it runs in. Either way it stops only if "
            "nobody is using it; otherwise the reasons are given and nothing stops. A stopped bot stays "
            f"down until its service next starts.\nKnown: {bot_list_hint()}")
        return
    found = await _service_of(update, context.args[0])
    if found is None:
        return
    bot, together = found
    nonce = _ask_service("shutdown", bot, together)
    others = [bot_id for bot_id in together if bot_id != bot["id"]]
    if others:
        text = (f"⏹ <b>{html.escape(bot['name'])}</b> shares one service with "
                f"{html.escape(_names(others))}.\n\n"
                f"<b>Only {html.escape(bot['name'])}</b>: the others carry on; it comes back when the "
                f"service next starts.\n<b>All {len(together)}</b>: the whole service stops and stays down "
                "until it is deployed or redeployed.\n\n"
                "Either way it happens only if nobody is using them.")
        rows = [[InlineKeyboardButton(f"Stop only {bot['name']}", callback_data=f"svc:{nonce}:one")],
                [InlineKeyboardButton(f"Stop all {len(together)}", callback_data=f"svc:{nonce}:all")],
                [InlineKeyboardButton("Cancel", callback_data=f"svc:{nonce}:no")]]
    else:
        text = (f"⏹ Stop <b>{html.escape(bot['name'])}</b>? It is alone in its service, which stops and "
                "stays down until it is deployed or redeployed -- only if nobody is using it.")
        rows = [[InlineKeyboardButton(f"Stop {bot['name']}", callback_data=f"svc:{nonce}:all"),
                 InlineKeyboardButton("Cancel", callback_data=f"svc:{nonce}:no")]]
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(rows))


async def restart_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update, context):
        return
    args = [arg.lower() for arg in context.args]
    force = "force" in args
    words = [arg for arg in context.args if arg.lower() != "force"]
    if len(words) != 1:
        await update.message.reply_text(
            "Usage: /restart <bot> [force]\nRestarts the service that bot runs in -- every bot in it -- "
            "once nobody is using it; force restarts that bot alone, at once, whatever it is doing "
            f"(for a stuck bot).\nKnown: {bot_list_hint()}")
        return
    found = await _service_of(update, words[0])
    if found is None:
        return
    bot, together = found
    nonce = _ask_service("restart", bot, together, force)
    if force:
        text = (f"🔄 Restart <b>{html.escape(bot['name'])}</b> <b>at once</b>, whatever it is doing? Anybody "
                "mid-something with it loses that. It restarts alone; the other bots of its service carry on.")
        label = f"Restart {bot['name']} now"
    else:
        text = (f"🔄 Restart the service of <b>{html.escape(bot['name'])}</b>? Every bot in it restarts: "
                f"{html.escape(_names(together))}.\nIt restarts once nobody is using it; otherwise the "
                "reasons are given and nothing restarts.")
        label = f"Restart all {len(together)}"
    rows = [[InlineKeyboardButton(label, callback_data=f"svc:{nonce}:all"),
             InlineKeyboardButton("Cancel", callback_data=f"svc:{nonce}:no")]]
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(rows))


async def service_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update, context):
        return
    query = update.callback_query
    _, nonce, choice = (query.data.split(":") + ["", ""])[:3]
    ask = _SERVICE_ASKS.pop(nonce, None)
    await query.answer()
    if ask is None or time_module.monotonic() - ask["at"] > SERVICE_ASK_SECONDS:
        await edit_in_place(query.message, context.bot, "That question has expired -- nothing was asked. "
                                                        "Send the command again.")
        return
    if choice not in ("one", "all"):
        await edit_in_place(query.message, context.bot, "Cancelled -- nothing was asked.")
        return
    bot = resolve_bot(ask["bot"])
    if ask["kind"] == "restart":
        command, args = "restart", (["force"] if ask["force"] else [])
        chosen = f"restart {bot['name']} now" if ask["force"] else f"restart {_names(ask['together'])}"
    elif choice == "one":
        command, args, chosen = "softstopbot", [], f"stop only {bot['name']}"
    else:
        command, args, chosen = "softstop", [], f"stop {_names(ask['together'])}"
    await edit_in_place(query.message, context.bot, f"Asked: {chosen}. The answer follows.")
    await _dispatch(update, context, bot, command, args)


async def run_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update, context):
        return
    if len(context.args) < 2:
        lines = ["Usage: /run &lt;bot&gt; &lt;command&gt; [args]", "", "<b>Bots:</b> " + bot_list_hint(), "", "<b>Commands:</b>"]
        lines += [f"  <code>{name}</code> — {desc}" for name, desc in family_link.COMMAND_HELP.items()]
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


_FANOUTS: "OrderedDict[int, dict]" = OrderedDict()
MAX_FANOUTS = 64


def _remember_fanout(command_id: int, group: dict) -> None:
    _FANOUTS[command_id] = group
    while len(_FANOUTS) > MAX_FANOUTS:
        _FANOUTS.popitem(last=False)


async def _fanout(update: Update, context: ContextTypes.DEFAULT_TYPE,
                  command: str, args: list, title: str, footer: str = "") -> None:
    targets = list(CHILDREN)
    live = await LiveMessage.reply_to(update.effective_message,
                                      f"{title} — asking all {len(targets)}…",
                                      parse_mode=ParseMode.HTML)
    group = {"live": live, "title": title, "command": command, "footer": footer,
             "answers": {}, "queued": set()}
    for child in targets:
        beat = await asyncio.to_thread(db.heartbeat_of, child["id"])
        stale = _too_old_for((beat or {}).get("version"), command, args)
        if stale:
            group["answers"][child["id"]] = (
                "⏳", f"on {(beat or {}).get('release') or (beat or {}).get('version') or 'an unknown version'} — "
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
    if not waiting and group.get("footer"):
        text += "\n\n" + group["footer"]
    return text


async def _render_fanout(context: ContextTypes.DEFAULT_TYPE, group: dict) -> None:
    text = _fanout_text(group)
    if len(text) <= TELEGRAM_MAX_CHARS:
        await group["live"].set(context.bot, text, parse_mode=ParseMode.HTML)
        return
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
        logger.debug("Could not read the recorded problem counts", exc_info=True)
        footer = ""
    await _fanout(update, context, "errors", [],
                  "⚠️ <b>Errors since each bot started</b>", footer=footer)


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


_PING_TRACES: "OrderedDict[int, dict]" = OrderedDict()
MAX_PING_TRACES = 64


def _remember_trace(command_id: int, trace: dict) -> None:
    _PING_TRACES[command_id] = trace
    while len(_PING_TRACES) > MAX_PING_TRACES:
        _PING_TRACES.popitem(last=False)


def _ms(delta) -> str:
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
        _row("phone → Telegram → ManagerBot", _ms(trace["to_manager"]) + "   ±1 s"),
        _row("ManagerBot → Telegram (ack)", _ms(trace["ack_ms"])),
        _row("ManagerBot → Supabase (queue)", _ms(trace["queue_ms"])),
        _row(f"queued → {name} claimed it", _ms(claimed - created)),
        _row(f"{name} answering", _ms(finished - claimed)),
        _row("answer → ManagerBot collected it", _ms(taken - finished)),
        "─" * 40,
        _row("bus round trip", _ms(taken - created)),
    ]

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
    try:
        there = json.loads(result.get("output") or "{}")
    except ValueError:
        return None
    value = (there.get("user") or {}).get("total_ms")
    return float(value) if isinstance(value, (int, float)) else None


def _render_user_wait(name: str, user: dict) -> str:
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
    if not await guard(update, context):
        return
    if context.args:
        bot = resolve_bot(context.args[0])
        if not bot:
            await update.message.reply_text(f"No such bot: {context.args[0]}. Known: {bot_list_hint()}")
            return
        await _ping_one(update, context, bot)
        return

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
    if not await guard(update, context):
        return
    query = update.callback_query
    _, chat_id, message_id, which = query.data.split(":", 3)
    group = _PING_GROUPS.get((int(chat_id), int(message_id)))
    if group is None:
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
    await send_long(context, query.message.chat_id,
                    re.sub(r"<[^>]+>", "", body), filename="ping.txt")


def _summary_lines(rows: dict) -> list:
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


async def users_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    try:
        people = await asyncio.to_thread(db.unique_people, [b["schema"] for b in CHILDREN])
        lines.append(f"👤 Unique people across all bots: {people} (the gift's mark is {family_link.PROMO_USERS})")
    except Exception as exc:
        lines.append(f"👤 Unique people across all bots: could not count ({type(exc).__name__})")
    await update.message.reply_text("\n".join(lines))

PEOPLE_RECORD_SECONDS = 3600
PEOPLE_MONTHS_SHOWN = 6
PEOPLE_USAGE = ("/people — the last 7, 30 and 90 days, and the past months\n"
                "/people 14 — the last 14 days\n"
                "/people 2026-09 — one month\n"
                "/people 2026-09-01 [2026-09-15] — from a day to today, or to another day")


def _people_bots() -> "list[tuple[str, str]]":
    return [(bot["id"], bot["schema"]) for bot in ALL_BOTS]


def _people_range(args: list, today) -> "tuple | None":
    if not args:
        return None
    if len(args) == 1 and args[0].isdigit():
        return today - timedelta(days=max(1, int(args[0])) - 1), today, False
    if len(args) == 1 and re.fullmatch(r"\d{4}-\d{2}", args[0]):
        first = datetime.fromisoformat(args[0] + "-01").date()
        return first, (first.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1), True
    if len(args) in (1, 2):
        first = datetime.fromisoformat(args[0]).date()
        last = datetime.fromisoformat(args[1]).date() if len(args) == 2 else today
        return (first, last, False) if first <= last else (last, first, False)
    raise ValueError("too many arguments")


async def record_people(context: ContextTypes.DEFAULT_TYPE):
    try:
        await asyncio.to_thread(db.record_people_counts, _people_bots(), _promo_schemas())
    except Exception:
        logging.getLogger(__name__).warning("Could not write the people counts", exc_info=True)


def _people_lines(counts: "list[dict]") -> "list[str]":
    def row(bot_id: str) -> str:
        return " · ".join(str(found.get(bot_id, 0)) if found is not None else "?" for found in counts)

    lines = [f"  {bot['name']}: {row(bot['id'])}" for bot in ALL_BOTS]
    if any(found is not None and db.EVERYONE in found for found in counts):
        lines.append(f"👤 Across all bots: {row(db.EVERYONE)}")
    else:
        lines.append("👤 Across all bots: needs the data key, which does not open")
    return lines


async def people_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update, context):
        return
    today = datetime.now(timezone.utc).date()
    try:
        chosen = _people_range(context.args, today)
    except ValueError:
        await update.message.reply_text(PEOPLE_USAGE)
        return
    await record_people(context)
    exact_from = db.first_exact_day()
    window = db.ACTIVITY_RETENTION_DAYS
    bots, everyone = _people_bots(), _promo_schemas()

    async def live(first, last) -> dict:
        return await asyncio.to_thread(db.people_live, bots, everyone, first, min(last, today))

    try:
        if chosen is None:
            stretches = [(today - timedelta(days=days - 1), today) for days in (7, 30, window)]
            counts = [await live(first, last) for first, last in stretches]
            lines = [f"👥 Unique users: last 7 days · last 30 days · last {window} days", ""]
            lines += _people_lines(counts)
            months = await asyncio.to_thread(
                db.people_stored, db.MONTH, today - timedelta(days=31 * PEOPLE_MONTHS_SHOWN), today)
            if months:
                lines += ["", "📅 By month (all bots · " + " · ".join(bot["name"] for bot in ALL_BOTS) + "):"]
                for start in sorted(months, reverse=True)[:PEOPLE_MONTHS_SHOWN]:
                    found = months[start]
                    per_bot = " · ".join(str(found.get(bot["id"], 0)) for bot in ALL_BOTS)
                    lines.append(f"  {start:%Y-%m}: {found.get(db.EVERYONE, '?')} ({per_bot})")
            lines += ["", "Other stretches: /people 14, /people 2026-09, /people 2026-09-01 2026-09-15"]
            await update.message.reply_text("\n".join(lines))
            return

        first, last, is_month = chosen
        if last > today:
            last = today
        title = f"👥 Unique users, {first} – {last} ({(last - first).days + 1} days)"
        if first >= exact_from:
            counts = [await live(first, last)]
        else:
            period = db.MONTH if is_month else db.DAY if first == last else None
            stored = (await asyncio.to_thread(db.people_stored, period, first, first)).get(first) if period else None
            if stored is None:
                await update.message.reply_text(
                    f"Exact counts reach back to {exact_from} (the last {window} days). Before that only "
                    f"each whole day and month is kept, as a number: /people 2026-09 for a month, "
                    f"/people 2026-09-03 2026-09-03 for a day. "
                    + ("That one has no count." if period else ""))
                return
            counts = [stored]
        await update.message.reply_text("\n".join([title, ""] + _people_lines(counts)))
    except Exception as exc:
        await update.message.reply_text(f"Could not count ({type(exc).__name__}).")


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


def _parse_balance_change(arg: str):
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


BALANCE_OVERVIEW_ROWS = 20
BALANCE_HISTORY_ROWS = 12


async def balance_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
            "A numeric Telegram user id — /whois &lt;bot&gt; &lt;id&gt; shows "
            "who it is.",
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


BACKUP_PART_BYTES = int(os.environ.get("MBOT_BACKUP_PART_MB") or 49) * 1024 * 1024


def _backup_parts(size: int, limit: "int | None" = None) -> list:
    limit = limit or BACKUP_PART_BYTES
    if size <= limit:
        return [(0, size)]
    return [(start, min(start + limit, size)) for start in range(0, size, limit)]


def _build_backup() -> "tuple[bytes, dict]":
    archive = reserve_copies.build_archive(db.DATABASE_URL)
    with zipfile.ZipFile(BytesIO(archive)) as opened:
        manifest = json.loads(opened.read("MANIFEST.json"))
    tables = manifest.get("tables") or []
    return archive, {"tables": len(tables), "rows": sum(t.get("approx_rows", 0) for t in tables),
                     "size": len(archive), "sha256": hashlib.sha256(archive).hexdigest()}


RESTORE_HINT = ("To restore: <code>python scripts/security/restore_reserve.py</code> (it also erases again "
                "whoever used /deletemydata since). private/runbooks/reserve-copies.md has the steps.")


def _in_production() -> bool:
    return any(os.environ.get(name) for name in
               ("RAILWAY_ENVIRONMENT", "RAILWAY_ENVIRONMENT_NAME", "RAILWAY_PROJECT_ID"))


def _for_telegram(archive: bytes, info: dict, name: str) -> "tuple[bytes, dict, str, str]":
    public = offline_key.recipient()
    if not public:
        if _in_production() and (os.environ.get("MBOT_ALLOW_PLAIN_BACKUPS") or "").strip() != "1":
            raise offline_key.OfflineKeyError(
                "a copy of the whole database is not sent to Telegram unencrypted. Set BACKUP_PUBLIC_KEY "
                "(the public half of the offline key), or MBOT_ALLOW_PLAIN_BACKUPS=1 to send it plain anyway.")
        return archive, info, name, ("Not encrypted: set BACKUP_PUBLIC_KEY to have copies that only the "
                                     "offline key opens (private/runbooks/data-protection.md).")
    sealed = offline_key.encrypt(archive, public)
    numbers = dict(info, size=len(sealed), sha256=hashlib.sha256(sealed).hexdigest())
    return sealed, numbers, name + ".age", f"Encrypted to the offline key {offline_key.fingerprint(public)}."


async def _deliver_backup(bot, chat_id: int, name: str, archive: bytes, info: dict,
                          heading: str) -> "tuple[int, int, str | None]":
    parts = _backup_parts(len(archive))
    summary = (f"{info['size'] / (1024 * 1024):.1f} MB · {info['tables']} tables · "
               f"~{info['rows']} rows\nsha256 {info['sha256']}")
    pinned = 0
    for index, (start, end) in enumerate(parts, 1):
        if len(parts) == 1:
            filename, caption = name, f"{heading}\n{summary}"
        else:
            filename = f"{name}.{index:03d}"
            caption = (f"{heading} — part {index} of {len(parts)}\n"
                       + summary + "\n(the checksum is of the joined file)")
        try:
            sent = await bot.send_document(
                chat_id=chat_id, document=BytesIO(archive[start:end]), filename=filename,
                caption=caption, read_timeout=300, write_timeout=300)
        except Exception as exc:
            logger.exception("Could not send backup part %s", index)
            return pinned, len(parts), (f"part {index} of {len(parts)} could not be sent: "
                                        f"{type(exc).__name__}: {exc}")
        try:
            await bot.pin_chat_message(chat_id=chat_id, message_id=sent.message_id, disable_notification=True)
            pinned += 1
        except Exception:
            logger.warning("Could not pin backup part %s", index, exc_info=True)
    return pinned, len(parts), None


async def backup_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update, context):
        return
    chat_id = update.effective_chat.id
    note = await update.message.reply_text("Backing up the whole database…")
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M")
    name = f"botfamily_{stamp}.zip"
    try:
        archive, info = await asyncio.to_thread(_build_backup)
        archive, info, name, sealing = await asyncio.to_thread(_for_telegram, archive, info, name)
    except Exception as exc:
        logger.exception("Backup failed")
        await edit_in_place(note, context.bot, f"⚠️ Backup failed: {type(exc).__name__}: {exc}")
        return
    pinned, parts, problem = await _deliver_backup(
        context.bot, chat_id, name, archive, info, f"🗄 Database backup — lossless, restorable\n{sealing}")
    if problem:
        await edit_in_place(note, context.bot, f"⚠️ The backup was made but {problem}")
        return
    lines = [f"✅ Backed up and pinned ({pinned} of {parts} pinned)." if pinned < parts
             else ("✅ Backed up and pinned." if parts == 1
                   else f"✅ Backed up in {parts} parts, all pinned.")]
    if parts > 1:
        lines.append(f"Join them first: <code>copy /b {name}.001+{name}.002 {name}</code> "
                     f"on Windows, or <code>cat {name}.0* &gt; {name}</code>.")
    lines.append(RESTORE_HINT)
    await edit_in_place(note, context.bot, "\n".join(lines), parse_mode=ParseMode.HTML)


_RESERVE = {"mark": None, "written_at": None, "tried_at": None, "problem": None}


async def _whole_copy_to_operators(context: ContextTypes.DEFAULT_TYPE, what: str, heading: str, keep) -> None:
    taken_at = datetime.now(timezone.utc)
    name = f"botfamily_{taken_at.strftime('%Y-%m-%d_%H%M')}.zip"
    try:
        archive, info = await asyncio.to_thread(_build_backup)
        sent, sent_info, name, sealing = await asyncio.to_thread(_for_telegram, archive, info, name)
    except Exception as exc:
        logger.exception("The %s failed", what)
        await notify_owner(context, f"🗄 The {what} failed: {html.escape(f'{type(exc).__name__}: {exc}')}")
        return
    volume = ""
    if reserve.RESERVE_DIR:
        try:
            problem = await asyncio.to_thread(reserve_copies.volume_problem)
            if problem:
                raise RuntimeError(problem)
            volume = await keep(archive, taken_at)
        except Exception as exc:
            logger.exception("The %s could not be kept on the volume", what)
            volume = f"\n⚠️ Not kept on the volume: {type(exc).__name__}: {exc}"
    for admin_id in ADMIN_IDS:
        _, _, problem = await _deliver_backup(
            context.bot, admin_id, name, sent, sent_info, f"{heading}\n{sealing}" + volume)
        if problem:
            await notify_owner(context, f"🗄 The {what} was made but {html.escape(problem)}")


async def _keep_nightly(archive: bytes, taken_at: datetime) -> str:
    await asyncio.to_thread(reserve_copies.write_nightly, archive, taken_at)
    pruned = await asyncio.to_thread(reserve_copies.prune_erasures, db.DATABASE_URL)
    written = _RESERVE["written_at"]
    age = (f"{max(0, int((taken_at - written).total_seconds() // 60))} min old" if written
           else "not written since the bots started")
    volume = f"\nVolume: encrypted copy kept; the current one is {age}"
    if pruned:
        volume += f"; {pruned} erasure note(s) past {reserve.KEEP_DAYS} days dropped"
    return volume


async def _nightly_reserve_copy(context: ContextTypes.DEFAULT_TYPE) -> None:
    await _whole_copy_to_operators(context, "nightly reserve copy",
                                   "🗄 Nightly reserve copy — for emergencies only", _keep_nightly)


SHUTDOWN_COPY_SECONDS = 30
_SHUTDOWN_COPY: dict = {"bots": set(), "queued": False}


async def _stopped_since(since: datetime) -> list:
    stopped = []
    for bot in ALL_BOTS:
        if bot["id"] == BOT_NAME:
            continue
        try:
            raw = await asyncio.to_thread(db.get_setting, lifecycle.STOPPED_NOTE_PREFIX + bot["id"])
            stamped = datetime.fromisoformat(raw) if raw else None
        except Exception:
            logger.debug("Could not read the stopped note of %s", bot["id"], exc_info=True)
            continue
        if stamped is not None and stamped >= since:
            stopped.append(bot["id"])
    return stopped


async def _after_a_stop(context: ContextTypes.DEFAULT_TYPE, result: dict) -> None:
    if result["command"] not in ("softstop", "softstopbot") or result["status"] != "done":
        return
    since = (result.get("created_at") or datetime.now(timezone.utc)) - timedelta(seconds=60)
    stopped = await _stopped_since(since)
    if result["target_bot"] not in stopped:
        return
    if result["command"] == "softstopbot":
        stopped = [result["target_bot"]]
    _SHUTDOWN_COPY["bots"].update(stopped)
    if not _SHUTDOWN_COPY["queued"]:
        _SHUTDOWN_COPY["queued"] = True
        context.job_queue.run_once(shutdown_copy, when=SHUTDOWN_COPY_SECONDS)


async def _keep_latest_now(archive: bytes, taken_at: datetime) -> str:
    mark = await asyncio.to_thread(reserve_copies.change_mark, db.DATABASE_URL)
    await asyncio.to_thread(reserve_copies.write_latest, db.DATABASE_URL, taken_at)
    _RESERVE["mark"], _RESERVE["written_at"] = mark, datetime.now(timezone.utc)
    return "\nVolume: the current copy written again at the same time"


async def shutdown_copy(context: ContextTypes.DEFAULT_TYPE) -> None:
    stopped = sorted(_SHUTDOWN_COPY["bots"])
    _SHUTDOWN_COPY["bots"].clear()
    _SHUTDOWN_COPY["queued"] = False
    await _whole_copy_to_operators(context, "copy at shutdown",
                                   f"🗄 Copy at shutdown — taken after {_names(stopped)} stopped",
                                   _keep_latest_now)


async def keep_reserve_current(context: ContextTypes.DEFAULT_TYPE) -> None:
    if not reserve.RESERVE_DIR:
        return
    now = time_module.monotonic()
    if _RESERVE["tried_at"] is not None and now - _RESERVE["tried_at"] < reserve_copies.MIN_SECONDS:
        return
    try:
        problem = await asyncio.to_thread(reserve_copies.volume_problem)
        if problem is None:
            mark = await asyncio.to_thread(reserve_copies.change_mark, db.DATABASE_URL)
            if mark == _RESERVE["mark"] and _RESERVE["written_at"] is not None:
                return
            _RESERVE["tried_at"] = now
            await asyncio.to_thread(reserve_copies.write_latest, db.DATABASE_URL)
            _RESERVE["mark"], _RESERVE["written_at"] = mark, datetime.now(timezone.utc)
    except Exception as exc:
        logger.warning("The reserve copy on the volume could not be written", exc_info=True)
        problem = f"{type(exc).__name__}: {exc}"
    _RESERVE["tried_at"] = now
    if problem and not _RESERVE["problem"]:
        await notify_owner(context, f"🗄 The reserve copy on the volume is not being kept: {html.escape(problem)}")
    elif not problem and _RESERVE["problem"]:
        await notify_owner(context, "🗄 The reserve copy on the volume is being kept again.")
    _RESERVE["problem"] = problem


async def reports_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
        problem = problems.lookup(row["code"])
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
                 + (" — sent to the operator when it happened" if level == problems.URGENT
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


MAX_INCIDENTS_LISTED = 4
MAX_CODES_PER_KIND = 6


def _row_icon(row) -> str:
    level = int(row.get("level") or problems.level(row["code"]))
    kind = problems.kind(row["code"]) if level == problems.REFUSED else None
    return problems.KIND_LABELS[kind][0] if kind else problems.LEVEL_ICONS.get(level, "•")


def _row_level_name(row) -> str:
    level = int(row.get("level") or problems.level(row["code"]))
    kind = problems.kind(row["code"]) if level == problems.REFUSED else None
    return problems.KIND_LABELS[kind][1].lower() if kind else problems.LEVEL_NAMES.get(level, "?")


def _group_problems(rows: list) -> dict:
    grouped: dict = {}
    for row in rows:
        level = int(row.get("level") or problems.level(row["code"]))
        grouped.setdefault(level, {}).setdefault((row["bot_id"], row["code"]), []).append(row)
    return grouped


def _bot_name(bot_id: str) -> str:
    found = resolve_bot(bot_id)
    return found["name"] if found else bot_id


ROUTES_PREFIX = "routewatch:"
ROUTES_ORDER = ("instagram", "tiktok", "twitter", "pinterest")
ROUTES_NAMES = {"instagram": "Instagram", "tiktok": "TikTok", "twitter": "X", "pinterest": "Pinterest"}
ROUTES_LOOK = {"up": "✅", "last_resort": "⚠️ last route only", "down": "❌ down",
               "sample_gone": "❔ sample post gone"}
ROUTES_STALE_S = 2 * 86400


def _ago(seconds: float) -> str:
    hours = seconds / 3600
    return f"{hours:.0f}h" if hours < 48 else f"{hours / 24:.0f}d"


def routes_section(settings: dict, now: float) -> "list[str]":
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
        shown.append(f"{html.escape(ROUTES_NAMES.get(platform, platform.capitalize()))} {look}")
    return [head, "  " + " · ".join(shown)] if shown else [head]


def _brief_line(code: str) -> str:
    text = problems.brief(code)
    return f"\n      <i>{html.escape(text)}</i>" if text else ""


def build_problem_report(rows: list, hours: int, feedback: "list | None" = None,
                         feedback_note: "str | None" = None, routes: "dict | None" = None,
                         now: "float | None" = None, people: "list | None" = None) -> str:
    grouped = _group_problems(rows)
    signed = [row for row in rows if row.get("shared_at")]
    lines = [f"🌙 <b>Nightly report, last {hours}h</b>"]
    if people:
        lines += people

    urgent = grouped.get(problems.URGENT, {})
    if urgent:
        total = sum(len(group) for group in urgent.values())
        lines.append(f"\n🚨 <b>Urgent — {total}</b>")
        for (bot_id, code), group in sorted(urgent.items()):
            problem = problems.lookup(code)
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
            problem = problems.lookup(code)
            shown = [row["incident"] for row in group[:MAX_INCIDENTS_LISTED]]
            more = len(group) - len(shown)
            incidents = ", ".join(html.escape(incident) for incident in shown)
            if more:
                incidents += f", +{more}"
            mark = " 🙋" if any(row.get("shared_at") for row in group) else ""
            lines.append(
                f"  {html.escape(_bot_name(bot_id))} · <code>{html.escape(code)}</code> "
                f"×{len(group)} — {html.escape(problem.title if problem else 'unknown code')}"
                f"{mark}{_brief_line(code)}\n      {incidents}")

    by_kind: dict = {}
    for (bot_id, code), group in grouped.get(problems.REFUSED, {}).items():
        by_kind.setdefault(problems.kind(code) or problems.GUIDED, []).append((bot_id, code, group))
    for kind in problems.KIND_ORDER:
        entries = sorted(by_kind.get(kind, ()), key=lambda e: (-len(e[2]), e[0], e[1]))
        if not entries:
            continue
        icon, heading, _ = problems.KIND_LABELS[kind]
        total = sum(len(group) for _, _, group in entries)
        lines.append(f"\n{icon} <b>{html.escape(heading)} — {total}</b>")
        for bot_id, code, group in entries[:MAX_CODES_PER_KIND]:
            problem = problems.lookup(code)
            count = f" ×{len(group)}" if len(group) > 1 else ""
            lines.append(
                f"  {html.escape(_bot_name(bot_id))} · <code>{html.escape(code)}</code>{count} — "
                f"{html.escape(problem.title if problem else 'unknown code')}{_brief_line(code)}")
        rest = entries[MAX_CODES_PER_KIND:]
        if rest:
            lines.append(f"  +{sum(len(group) for _, _, group in rest)} more, under "
                         f"{len(rest)} other code(s) — <code>/reports</code>")

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


PEOPLE_AT_LAST_REPORT = "nightly:people"


def people_section(total: "int | None", last_day: "int | None", complete: bool,
                   before: "tuple | None") -> "list[str]":
    def change(now, then):
        if now is None or then is None:
            return ""
        return f" ({now - then:+d} since the last report)"
    total_text = "could not count" if total is None else str(total)
    day_text = "could not count" if last_day is None else str(last_day)
    if last_day is not None and not complete:
        day_text += " (AnonBot left out: the data key does not open)"
    return ["\n👥 <b>Unique users</b>",
            f"  All bots, all time: {total_text}{change(total, before[0] if before else None)}",
            f"  Last 24h: {day_text}{change(last_day, before[1] if before else None)}"]


async def _people_report(remember: bool) -> "list[str]":
    schemas = [b["schema"] for b in CHILDREN]
    total = last_day = None
    complete = True
    try:
        total = await asyncio.to_thread(db.unique_people, schemas)
    except Exception:
        logger.warning("Couldn't count the people for the nightly report", exc_info=True)
    try:
        last_day, complete = await asyncio.to_thread(
            db.people_since, schemas, datetime.now(timezone.utc) - timedelta(hours=24))
    except Exception:
        logger.warning("Couldn't count the last day's people for the nightly report", exc_info=True)
    before = None
    try:
        saved = await asyncio.to_thread(db.get_setting, PEOPLE_AT_LAST_REPORT)
        if saved:
            first, second = saved.split("|")[:2]
            before = (int(first) if first else None, int(second) if second else None)
    except Exception:
        logger.debug("Couldn't read the last report's people counts", exc_info=True)
    if remember and total is not None and last_day is not None and complete:
        try:
            await asyncio.to_thread(db.set_setting, PEOPLE_AT_LAST_REPORT, f"{total}|{last_day}")
        except Exception:
            logger.warning("Couldn't keep tonight's people counts", exc_info=True)
    return people_section(total, last_day, complete, before)


async def nightly_problem_report(context: ContextTypes.DEFAULT_TYPE) -> None:
    await _nightly_report(context)
    await _nightly_reserve_copy(context)


async def _nightly_report(context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        rows = await asyncio.to_thread(
            family_link.recent_problem_reports_since, NIGHTLY_REPORT_HOURS)
    except Exception as exc:
        await notify_owner(context, f"🌙 Nightly report failed: {html.escape(str(exc))}")
        return
    feedback, note = [], None
    try:
        feedback = await asyncio.to_thread(family_link.take_undelivered_feedback, 200)
        if not feedback and await _feedback_mode() == family_link.FEEDBACK_INSTANT:
            count = await asyncio.to_thread(family_link.feedback_count_since, NIGHTLY_REPORT_HOURS)
            if count:
                note = (f"{count} feedback message(s) in the last {NIGHTLY_REPORT_HOURS}h, each "
                        f"sent here as it came — /feedback to read them again.")
    except Exception:
        logger.warning("Couldn't collect feedback for the nightly report", exc_info=True)
    routes = await asyncio.to_thread(family_link.settings_with_prefix, ROUTES_PREFIX)
    people = await _people_report(remember=True)
    text = build_problem_report(rows, NIGHTLY_REPORT_HOURS, feedback, note, routes=routes, people=people)
    if feedback and len(text) > TELEGRAM_MAX_CHARS:
        text = build_problem_report(rows, NIGHTLY_REPORT_HOURS, routes=routes, people=people)
        await _send_feedback(context, f"💬 <b>Feedback — {len(feedback)}</b> · read only, with "
                                      f"tonight's report", feedback)
    if len(text) <= TELEGRAM_MAX_CHARS:
        await notify_owner(context, text)
        return
    await notify_owner(context, text[:TELEGRAM_MAX_CHARS])
    plain = re.sub(r"<[^>]+>", "", text)
    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_document(
                chat_id=admin_id, document=BytesIO(plain.encode("utf-8")),
                filename="problems.txt")
        except Exception:
            logger.exception("Couldn't deliver the nightly report file to %s", admin_id)


FEEDBACK_READ_ONLY = "read only: it names nobody, so there is nobody to answer"

FEEDBACK_SHOWN_CHARS = 3000
FEEDBACK_LIST_ROWS = 10
FEEDBACK_SEARCH_ROWS = 50
_feedback_mode_cache = {"value": None, "read_at": 0.0}


async def _feedback_mode() -> str:
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
    body = item["body"]
    cut = len(body) > shown
    text = html.escape(body[:shown]) + (f"… <code>/feedback {item['id']}</code> for the rest"
                                        if cut else "")
    when = item["created_at"].strftime("%m-%d %H:%M") if item.get("created_at") else "?"
    return (f"  {html.escape(_bot_name(item['bot_id']))} · {html.escape(item.get('lang') or '?')} · "
            f"{when} UTC · #{item['id']}\n  <i>{text}</i>")


async def _send_feedback(context, header: str, items: list) -> None:
    chunk = [header]
    for block in (_feedback_block(item) for item in items):
        if len("\n\n".join(chunk + [block])) > TELEGRAM_MAX_CHARS:
            await notify_owner(context, "\n\n".join(chunk))
            chunk = []
        chunk.append(block)
    if chunk:
        await notify_owner(context, "\n\n".join(chunk))


async def _deliver_feedback(context) -> None:
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
    return ("🔔 Each piece arrives as it is sent." if mode == family_link.FEEDBACK_INSTANT
            else "🌙 It arrives with the 22:00 report.")


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


PROBLEMS_MAX_HOURS = 168


async def nightly_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update, context):
        return
    hours = int(context.args[0]) if context.args and context.args[0].isdigit() \
        else NIGHTLY_REPORT_HOURS
    hours = max(1, min(hours, PROBLEMS_MAX_HOURS))
    rows = await asyncio.to_thread(family_link.recent_problem_reports_since, hours)
    note = None
    try:
        count = await asyncio.to_thread(family_link.feedback_count_since, hours)
        if count:
            note = f"{count} feedback message(s) in the last {hours}h — /feedback to read them."
    except Exception:
        logger.debug("Couldn't count feedback for /nightly", exc_info=True)
    routes = await asyncio.to_thread(family_link.settings_with_prefix, ROUTES_PREFIX)
    people = await _people_report(remember=False)
    text = build_problem_report(rows, hours, feedback_note=note, routes=routes, people=people)
    if len(text) > TELEGRAM_MAX_CHARS:
        await send_long(context, update.effective_chat.id,
                        re.sub(r"<[^>]+>", "", text), "problems.txt")
        return
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


async def decode_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update, context):
        return
    wanted = " ".join(context.args or []).replace("🆔", "").strip().upper()
    if not wanted:
        await update.message.reply_text("Usage: /decode <code> — for example /decode CV-TIMEOUT")
        return
    wanted = problems.current(wanted)
    matches = [wanted] if wanted in problems.PROBLEMS else [c for c in problems.PROBLEMS if wanted in c]
    if len(matches) == 1:
        await update.message.reply_text(problems.decode(matches[0]))
    elif matches:
        await update.message.reply_text("More than one code matches: " + ", ".join(matches[:40]))
    else:
        await update.message.reply_text(f"No code matches {wanted}.")


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


async def _alerts_on() -> bool:
    try:
        return await asyncio.to_thread(db.get_setting, "alerts", "on") == "on"
    except Exception:
        return True


async def startup_rollcall(context: ContextTypes.DEFAULT_TYPE) -> None:
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
                f"\u00b7 v{html.escape(beat.get('release') or beat['version'])}"
            )
        else:
            lines.append(
                f"\u2705 <b>{bot['name']}</b> -- already up ({format_delta(uptime)}) "
                f"on {host} \u00b7 v{html.escape(beat.get('release') or beat['version'])}"
            )

    header = (
        f"\U0001f44b <b>ManagerBot is up</b> on {html.escape(family_link.HOSTNAME)} -- "
        f"{up_count}/{len(CHILDREN)} of the family with it"
    )
    await notify_owner(context, "\n".join([header, "", *lines]))

    for bot in CHILDREN:
        beat = beats.get(bot["id"])
        is_up = beat is not None and beat["seconds_ago"] <= DOWN_AFTER_SECONDS
        try:
            await asyncio.to_thread(db.set_known_state, bot["id"], is_up)
        except Exception:
            logger.debug("Couldn't seed bot_state for %s", bot["id"], exc_info=True)


_db_failures = 0
_db_alerted = False


async def _is_redeploying(bot_id: str) -> bool:
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


async def _stopped_on_purpose(bot_id: str) -> bool:
    try:
        return bool(await asyncio.to_thread(db.get_setting, lifecycle.STOPPED_NOTE_PREFIX + bot_id))
    except Exception:
        return False


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
                "Until it comes back no bot's state can be seen, so silence "
                "from ManagerBot means unknown, not healthy.",
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
            await asyncio.to_thread(db.set_known_state, bot["id"], is_up)
            if not is_up and beat is not None:
                await notify_owner(context, f"❌ <b>{bot['name']}</b> is down (first check since ManagerBot started).")
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
        elif await _stopped_on_purpose(bot["id"]):
            await asyncio.to_thread(db.set_known_state, bot["id"], False)
            await notify_owner(context, f"⏹ <b>{bot['name']}</b> stopped as asked (soft shutdown). "
                                        "It stays down until it is deployed again.")
        elif await _is_redeploying(bot["id"]):
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
    if not await _alerts_on():
        return
    try:
        events = await asyncio.to_thread(db.take_unnotified_events, 20)
    except Exception:
        return

    if events:
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

    await _deliver_feedback(context)


_last_pumped = {"result": 0.0, "event": 0.0}

_pump_tasks: dict[str, "asyncio.Task"] = {}
_pump_started: dict[str, float] = {}
_pump_stall_logged: set[str] = set()
PUMP_STALL_WARN_SECONDS = int(os.environ.get("MBOT_PUMP_STALL_WARN_SECONDS", "60"))

SWEEP_WINDOW_SECONDS = 900
_sweep_recent: dict[str, "deque[tuple[float, float]]"] = {}
_sweep_slowest: dict[str, float] = {}


def _note_sweep(key: str, started: float, task=None) -> None:
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
    _pump_tasks[key] = context.application.create_task(pump(context), name=f"{key}-sweep")
    _pump_tasks[key].add_done_callback(functools.partial(_note_sweep, key, now))


async def _result_backstop(context: ContextTypes.DEFAULT_TYPE) -> None:
    await _pump_if_due(context, "result", result_pump,
                       RESULT_POLL_FAST_SECONDS, RESULT_POLL_SECONDS)


async def _event_backstop(context: ContextTypes.DEFAULT_TYPE) -> None:
    await _pump_if_due(context, "event", event_pump,
                       EVENT_POLL_FAST_SECONDS, EVENT_POLL_SECONDS)


async def result_pump(context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        results = await asyncio.to_thread(db.take_finished_commands, 5)
    except Exception:
        return

    if results:
        family_link.mark_bus_active()

    for result in results:
        try:
            await _after_a_stop(context, result)
        except Exception:
            logger.warning("Could not check whether command #%s stopped a bot", result["id"], exc_info=True)
        trace = _PING_TRACES.pop(result["id"], None)
        if trace is not None:
            await _deliver_ping(context, trace, result)
            continue

        group = _FANOUTS.pop(result["id"], None)
        if group is not None:
            await _deliver_fanout(context, group, result)
            continue

        chat_id = result["reply_chat_id"]
        if not chat_id:
            continue
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


HELP = f"""👪 <b>ManagerBot</b> — the family's manager.

<b>Is everything alright</b>
/status — every bot: up/down, uptime, host, errors, users (with Refresh /
    Alerts / Ping buttons under it)
/nightly [hours] — the 22:00 nightly report, on demand: unique users,
    then what the bots showed people, laid out: urgent listed, faults
    grouped, the rest by what kind of thing it was
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
/stats [days] — what the bots did, per day: sites, conversions, stickers
    (anonymous counts, nothing about who)
/providers — which of DownloaderBot's download routes work, which rest
/probe — test every download route, from DownloaderBot's container
/alerts on|off — mute or unmute unprompted alerts

<b>Doing something to a bot</b>
/run &lt;bot&gt; &lt;command&gt; [args] — the general form; run it bare for the list,
    which now includes the commands each bot registers for itself
/shutdown &lt;bot&gt; — asks: that bot alone, or the whole service it runs in;
    stops only when nobody is using it (refused, with the reasons, while
    anybody is). A stopped service stays down until a deploy or a Redeploy.
    Once it is down, a copy of the whole database follows, as /backup sends
/restart &lt;bot&gt; [force] — asks, then restarts its whole service once nobody is
    using it; force restarts that bot alone, at once (for a stuck bot)
/pause [bot|all] [minutes] — stop them taking work an update would lose;
    whoever asks is told to come back, and written down
/warn [bot|all] — tell whoever is mid-something that it is about to reset
/resume [bot|all] — reopen, and tell everyone who was turned away
    (only when asked, so one update can span as many deploys as it needs)

<b>Talking to people</b>
/say &lt;bot&gt; &lt;user_id&gt; &lt;text&gt; — DM someone <i>as</i> that bot
/broadcast &lt;bot|all&gt; [--active] &lt;text&gt; — one message to everyone, <i>as</i> that bot
/whois &lt;bot&gt; &lt;user_id&gt; — look someone up through that bot
/users [hours] — active users per bot, default 24h
/people [days | month | from [to]] — unique users per bot: exact for the last
    90 days; before that, each whole day's and month's number (no ids kept)
/feedback [instant|nightly] — what people sent with /feedback (read only: it
    names nobody, so there is nobody to answer), and whether each one arrives
    here at once or with the 22:00 report

<b>Money</b>
/balance [id] [±n|=n] — see or change a credit balance
/addcredit &lt;id&gt; &lt;amount&gt; — put ⚡ into an account
/gift &lt;bot|all&gt; &lt;key&gt; &lt;credits&gt; &lt;last day&gt; — bonus ⚡ for everyone,
    once each: each bot counts first, and nothing is given until it is confirmed
/promo [stop] — the {family_link.PROMO_CREDITS} ⚡ gift: start it (once), see how it
    goes, end it early
/donations — paid donations per bot
/stars — ConvertBot's Stars: paid, refunded, recent

<b>The database</b>
/backup — the whole database, lossless and restorable, sent here and pinned (it also comes by itself after the nightly report, and after a /shutdown)
/dbdump &lt;bot&gt; — that bot's own tables as CSVs
/sql &lt;SELECT …&gt; — read-only query on the shared database
/decode &lt;code&gt; — what an error code means

<b>Still works, not in the menu</b>
Renamed in 1.7.0, with the old names kept so a habit does not break:
/problems → /nightly (its name from 1.7.0 to 2.1.2) · /finishupdates → /resume ·
/errorlog, /botlog and /problemlog → /logs · /me → /status
/crashtest &lt;bot&gt; makes a bot raise on purpose, to check the alert arrives
    (no bot named crashes ManagerBot itself)

Bots: <code>{{bots}}</code>
Any unambiguous prefix works — <code>/logs stick</code> is fine."""


NO_PREVIEW = LinkPreviewOptions(is_disabled=True)


def _help_page_pause() -> str:
    minutes = lifecycle.DEFAULT_MAINTENANCE_MINUTES
    timeout = COMMAND_TIMEOUT_SECONDS
    return f"""⏸ Updating the bots: /pause, /warn, /resume

In this order:
1. /pause — the bots stop starting new long work, the kind an update would cut off. Whoever asks for some is told to come back in about {minutes} minutes, and written down. Work already running carries on.
2. /warn — only the people in the middle of something right now are told it is about to be reset. It changes nothing, and can be sent again.
3. Deploy, as many times as it takes. The pause survives restarts and redeploys, and it never ends by itself: the minutes are only what people are told.
4. /resume — the bots take work again, and everyone who was turned away is told they can try again (each person once).

Forms:
/pause — all four bots, people told {minutes} minutes
/pause 30 — all four, 30 minutes
/pause convert — one bot (any unambiguous start of its name)
/pause convert 30 — one bot, 30 minutes
/warn [bot|all] and /resume [bot|all] — the same choice of bots
/finishupdates is the old name of /resume.

Each bot answers for itself: how many people it warned, or that it is open again and how many it told. A bot that is down gets nothing: after {timeout} seconds without an answer it is reported as looking down; send the command again once it is up. ManagerBot itself is never paused.

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
2. A card names the amount, the last day and the key, with "Give it" and "Cancel". Nothing is given until Give it is pressed, and that cannot be taken back. Only the newest /gift can be given; an older card's buttons give nothing.
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
• Every {minutes} minutes ManagerBot counts the unique people across all the bots, each person once however many bots they use. The first time the count reaches {users}, a message here says so, once. /users shows the count too.

What /promo shows depends on where the gift stands:
• Not started, under {users} people: the count so far. Nothing to do; a message follows when it is reached.
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
• First a card shows the text, with "Send it" / "Cancel". Nothing is sent before Send it, and nothing can be taken back after. A newer /broadcast replaces the one waiting.
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
• "queued as #n" shows at once; the bot's answer comes as a second message. No answer in {timeout} seconds means the bot looks down.
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
• If the bot's host keeps no log files, its in-memory copy since it started is sent instead, and it says so.
• /errorlog, /botlog and /problemlog ask which bot with buttons.

Example: /logs convert 100 problems
"""


REPORTS_LINE = ("/reports [n] lists the latest problems, newest first, each with its "
                "incident code; /reports mine only the ones somebody attached their "
                "details to.")


def _help_page_nightly() -> str:
    default = NIGHTLY_REPORT_HOURS
    most = PROBLEMS_MAX_HOURS
    daily = (f"The same report arrives by itself every day at {NIGHTLY_REPORT_AT_UTC} UTC."
             if NIGHTLY_REPORT_AT_UTC else "The daily report is off (MBOT_NIGHTLY_UTC is blank).")
    reports_line = REPORTS_LINE
    return f"""🌙 /nightly — the 22:00 nightly report, on demand

/nightly [hours]
• hours: how far back, {default} if not said, from 1 to {most}.
• First the unique users: all bots, all time, and the last 24 hours, each with its change since the last scheduled report.
• Then what the bots showed people in that time, laid out: urgent ones listed, faults grouped, the rest by what kind of thing it was. A note says how many /feedback messages came in.
• Too long for one message: the start comes here, and the whole of it as problems.txt.
• {daily} /problems is the same command.
• One problem in full: /report <incident>. {reports_line}

Example: /nightly 72
"""


def _help_page_events() -> str:
    default = EVENTS_DEFAULT
    most = EVENTS_MAX
    return f"""🗒 /events — crashes, startups and payments, as they happened

/events [bot] [n]
• n: how many, {default} if not said, at most {most}. bot: only that bot's events (any unambiguous start of its name). Either order.
• A word that is not a bot is ignored, so a typo shows every bot's events: check the bot named after "from" in the heading.
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
/feedback instant — each one arrives here the moment it is sent.
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
• "Sent." means Telegram took it. If the person blocked the bot or never started it, Telegram's refusal is shown instead.

Example: /say convert 123456789 The fix is live — the file can be sent again.
"""


HELP_PAGES = {
    "pause": _help_page_pause,
    "gift": _help_page_gift,
    "promo": _help_page_promo,
    "broadcast": _help_page_broadcast,
    "balance": _help_page_balance,
    "addcredit": _help_page_addcredit,
    "run": _help_page_run,
    "logs": _help_page_logs,
    "nightly": _help_page_nightly,
    "events": _help_page_events,
    "feedback": _help_page_feedback,
    "sql": _help_page_sql,
    "say": _help_page_say,
}
HELP_ALIASES = {"warn": "pause", "resume": "pause", "finishupdates": "pause", "problems": "nightly"}


def help_page_name(context) -> "str | None":
    if not context.args:
        return None
    word = context.args[0].lower().lstrip("/").split("@", 1)[0].removeprefix("help_")
    word = HELP_ALIASES.get(word, word)
    return word if word in HELP_PAGES else None


def help_page_text(name: str) -> str:
    return HELP_PAGES[name]().rstrip() + "\n\nEverything: /help"


def _help_keyboard() -> InlineKeyboardMarkup:
    buttons = [InlineKeyboardButton("/" + name, callback_data=f"helpdet:{name}") for name in HELP_PAGES]
    return InlineKeyboardMarkup([buttons[i:i + 2] for i in range(0, len(buttons), 2)])


async def help_page_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update, context):
        return
    query = update.callback_query
    name = query.data.split(":", 1)[1]
    if name not in HELP_PAGES:
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
    await update.message.reply_text("Not a ManagerBot command. /help lists them.")


async def plain_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update, context):
        return
    await update.message.reply_text("Only commands are taken here. /help lists them.")


async def crashtest_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
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


def _targets(token: str | None) -> list[dict] | None:
    if not token or token.lower() == "all":
        return list(CHILDREN)
    bot = resolve_bot(token)
    if not bot or bot["id"] == BOT_NAME:
        return None
    return [bot]


async def _fan_out(update: Update, context: ContextTypes.DEFAULT_TYPE,
                   targets: list[dict], command: str, args: str, headline: str) -> None:
    for bot in targets:
        await asyncio.to_thread(
            db.queue_command, bot["id"], command, args,
            update.effective_user.id, update.effective_chat.id,
        )
    names = ", ".join(b["name"] for b in targets)
    await update.message.reply_text(f"{headline}\n→ {names}")


async def pause_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
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


BROADCAST_PENDING = "broadcast_pending"
BROADCAST_PREVIEW = 3000


async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
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


GIFT_PENDING = "gift_pending"
GIFT_DONE = "gift_done"


async def gift_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
        f"it to, with the notice they would read. Nothing is given until "
        f"<b>Give it</b> is pressed, and that cannot be taken back.",
        parse_mode=ParseMode.HTML, reply_markup=kb,
    )


async def gift_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not await guard(update, context):
        return
    await query.answer()
    action, _, nonce = query.data.removeprefix("gift:").partition(":")
    if nonce and nonce == context.user_data.get(GIFT_DONE):
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
        text += (f"\n\n⚠️ Could not ask {', '.join(failed)} (a database error, see "
                 f"the log). Send the same /gift again and press Give it: nobody "
                 f"gets it twice.")
    await edit_in_place(query.message, context.bot, text)


PROMO_COUNT_SECONDS = 15 * 60
PROMO_COUNT_FIRST_SECONDS = 60
PROMO_PENDING = "promo_pending"
PROMO_DONE = "promo_done"
PROMO_BUTTON_PATTERN = r"^promo:(go|cancel):[0-9a-f]{8}$"
_promo_count: dict = {"count": None, "at": None}


def _promo_schemas() -> list[str]:
    return [child["schema"] for child in CHILDREN]


def _promo_moment(when: datetime) -> str:
    return when.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _promo_last_day(closes: datetime) -> str:
    return (closes - timedelta(days=1)).strftime("%d.%m.%Y")


def _promo_db_error(exc: Exception) -> str:
    return f"Could not read the gift's state ({type(exc).__name__}). Nothing was changed."


async def count_unique_people(context: ContextTypes.DEFAULT_TYPE):
    try:
        count = await asyncio.to_thread(db.unique_people, _promo_schemas())
        _promo_count.update(count=count, at=datetime.now(timezone.utc))
        if count < family_link.PROMO_USERS:
            return
        if await asyncio.to_thread(family_link.promo_milestone_at) is not None:
            return
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
            f"unique users. Now: {count}. A message follows here when it happens.")
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
    query = update.callback_query
    if not await guard(update, context):
        return
    await query.answer()
    action, _, nonce = query.data.removeprefix("promo:").partition(":")
    if nonce and nonce == context.user_data.get(PROMO_DONE):
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


BOT_COMMANDS = [
    BotCommand("status", "Every bot: up, version, errors, users"),
    BotCommand("nightly", "The 22:00 nightly report, on demand"),
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
    BotCommand("stats", "Anonymous daily statistics, for infographics"),
    BotCommand("run", "Run a command inside another bot"),
    BotCommand("restart", "Restart a bot's service once nobody is using it"),
    BotCommand("shutdown", "Stop a bot's service once nobody is using it"),
    BotCommand("pause", "Stop the bots taking work an update would lose"),
    BotCommand("warn", "Tell whoever is mid-something that it will reset"),
    BotCommand("resume", "Reopen, and tell everyone who was waiting"),
    BotCommand("say", "Message one person, as one of the bots"),
    BotCommand("broadcast", "Message everyone, as one of the bots"),
    BotCommand("whois", "Look a user up through a bot"),
    BotCommand("users", "Active users per bot"),
    BotCommand("people", "Unique users over time"),
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

STRANGER_COMMANDS = [
    BotCommand("start", "what this bot is"),
]


async def _publish_commands(application):
    try:
        await application.bot.set_my_commands(STRANGER_COMMANDS)
    except Exception:
        logger.warning("Couldn't publish the public command menu.", exc_info=True)
    for admin_id in sorted(ADMIN_IDS):
        try:
            await application.bot.set_my_commands(
                BOT_COMMANDS, scope=BotCommandScopeChat(chat_id=admin_id))
        except Exception:
            logger.warning("Couldn't publish the operator's command menu to %s.",
                           admin_id, exc_info=True)
    from telegram import (BotCommandScopeAllChatAdministrators, BotCommandScopeAllGroupChats,
                          BotCommandScopeAllPrivateChats)
    for scope in (BotCommandScopeAllPrivateChats, BotCommandScopeAllGroupChats,
                  BotCommandScopeAllChatAdministrators):
        try:
            await application.bot.delete_my_commands(scope=scope())
        except Exception:
            logger.debug("Couldn't clear the %s menu.", scope.__name__, exc_info=True)


SHORT_DESCRIPTION = "A private bot. It looks after one person's other bots."


def _description() -> str:
    siblings = ", ".join(
        "@" + USERNAME_ALIASES[bot["id"]][0]
        for bot in CHILDREN if USERNAME_ALIASES.get(bot["id"])
    )
    return (
        "This bot is not for general use and does not answer strangers. It "
        "watches the public bots in this family — whether they are up, what "
        "they are doing, and when one of them breaks — and takes commands "
        "from their operator only.\n\n"
        f"The bots it looks after: {siblings}."
    )


async def _publish_profile(application):
    try:
        await application.bot.set_my_short_description(SHORT_DESCRIPTION)
        await application.bot.set_my_description(_description())
    except Exception:
        logger.warning("Couldn't publish ManagerBot's profile text.", exc_info=True)


async def _post_init(application):
    await tune_runtime(application)
    await lifecycle.on_start(BOT_NAME)
    await lifecycle.optional("command menu", _publish_commands(application))
    await lifecycle.optional("profile text", _publish_profile(application))


async def _post_stop(application):
    await lifecycle.on_stop(application)
    await flush_on_shutdown(application)


def main():
    if not BOT_TOKEN or not BOT_USERNAME:
        raise SystemExit("Set MBOT_TOKEN and MBOT_USERNAME environment variables first.")
    if not ADMIN_IDS:
        raise SystemExit(
            "MBOT_ADMIN_ID is empty. ManagerBot is operator-only by definition and "
            "refuses to start without knowing who the operator is -- put the operator's "
            "numeric Telegram id there (@userinfobot shows it)."
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
    lifecycle.install(app, BOT_NAME, ADMIN_IDS)
    app.add_error_handler(error_handler)
    app.add_handler(TypeHandler(Update, strangers_stop_here), group=-100)
    app.add_handler(TypeHandler(Update, track_activity), group=-3)

    app.add_handler(CommandHandler(["start", "help"], start_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("usage", usage_command))
    app.add_handler(CommandHandler("idle", idle_command))
    app.add_handler(CommandHandler("buttons", buttons_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("me", me_command))
    app.add_handler(CommandHandler("run", run_command))
    app.add_handler(CommandHandler("ping", broadcast_ping))
    app.add_handler(CommandHandler("users", users_command))
    app.add_handler(CommandHandler("people", people_command))
    app.add_handler(CommandHandler("donations", donations_command))
    app.add_handler(CommandHandler("balance", balance_command))
    app.add_handler(CommandHandler("addcredit", addcredit_command))
    app.add_handler(CommandHandler("events", events_command))
    app.add_handler(CommandHandler("sql", sql_command))
    app.add_handler(CommandHandler("backup", backup_command))
    app.add_handler(CommandHandler("reports", reports_command))
    app.add_handler(CommandHandler("report", report_command))
    app.add_handler(CommandHandler(["nightly", "problems"], nightly_command))
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
    app.add_handler(CommandHandler("restart", restart_command))
    app.add_handler(CommandHandler("shutdown", shutdown_command))
    app.add_handler(CommandHandler("whois", _shortcut("whois", 2, "Usage: /whois <bot> <user_id>")))
    app.add_handler(CommandHandler("say", _shortcut("message", 3, "Usage: /say <bot> <user_id> <text>")))
    app.add_handler(CommandHandler("providers", _shortcut("providers", 0, "", default_bot="downloaderbot")))
    app.add_handler(CommandHandler("probe", _shortcut("probe", 0, "", default_bot="downloaderbot")))
    app.add_handler(CommandHandler("stars", _shortcut("stars", 0, "", default_bot="convertbot")))

    app.add_handler(CommandHandler("broadcast", broadcast_command))
    app.add_handler(CommandHandler("gift", gift_command))
    app.add_handler(CommandHandler("promo", promo_command))
    app.add_handler(CommandHandler("pause", pause_command))
    app.add_handler(CommandHandler("warn", warn_command))
    app.add_handler(CommandHandler(["resume", "finishupdates"], finish_updates_command))

    app.add_handler(CallbackQueryHandler(board_button, pattern=r"^board:"))
    app.add_handler(CallbackQueryHandler(ping_detail_button, pattern=r"^pingdet:"))
    app.add_handler(CallbackQueryHandler(help_page_button, pattern=r"^helpdet:[a-z]{1,20}$"))
    app.add_handler(CallbackQueryHandler(broadcast_button, pattern=r"^upd:"))
    app.add_handler(CallbackQueryHandler(service_button, pattern=r"^svc:"))
    app.add_handler(CallbackQueryHandler(gift_button, pattern=r"^gift:"))
    app.add_handler(CallbackQueryHandler(promo_button, pattern=PROMO_BUTTON_PATTERN))
    app.add_handler(CallbackQueryHandler(log_pick_button, pattern=r"^logpick:"))
    app.add_handler(CallbackQueryHandler(feedback_mode_button, pattern=r"^fbmode:"))
    app.add_handler(MessageHandler(filters.COMMAND, unknown_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, plain_text))

    if app.job_queue is None:
        raise SystemExit(
            "No JobQueue available, which means no watchdog, no alerts and no /run "
            'results -- ManagerBot would be a shell. Install it with:\n'
            '    pip install "python-telegram-bot[job-queue]"'
        )

    app.job_queue.run_once(startup_rollcall, when=STARTUP_ROLLCALL_SECONDS)
    app.job_queue.run_repeating(watchdog, interval=60, first=20)
    app.job_queue.run_repeating(count_unique_people, interval=PROMO_COUNT_SECONDS,
                                first=PROMO_COUNT_FIRST_SECONDS)
    app.job_queue.run_repeating(record_people, interval=PEOPLE_RECORD_SECONDS, first=300)
    if reserve.RESERVE_DIR:
        app.job_queue.run_repeating(keep_reserve_current, interval=60, first=30)
    app.job_queue.run_repeating(_event_backstop, interval=PUMP_TICK_SECONDS, first=10)
    app.job_queue.run_repeating(_result_backstop, interval=PUMP_TICK_SECONDS, first=5)

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

    family_link.attach(app, BOT_NAME, DISPLAY_NAME, START_TIME)
    attach_maintenance(app)

    logger.info("ManagerBot starting (polling). Watching: %s", bot_list_hint())
    app.run_polling(**lifecycle.polling_kwargs(
        timeout=POLL_TIMEOUT,
        allowed_updates=[Update.MESSAGE, Update.CALLBACK_QUERY],
    ))


if __name__ == "__main__":
    main()
