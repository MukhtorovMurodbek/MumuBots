"""The Telegram side of a conversion job.

jobs.py decides what happens -- when credit is taken, when it goes back, who
is told. This file is how it is said: the status message a person watches,
the file they receive, the sentence that explains a failure, and the message
the owner gets when the bot is at fault.

Kept out of bot.py for two reasons. bot.py is already the file every handler
lives in, and this is not a handler: it runs in a background task long after
the button that started it was answered. And it takes its dependencies in
its constructor -- the application, the admin ids, the caption builder --
so nothing here imports bot.py and nothing can go round in a circle.
"""
from __future__ import annotations

import asyncio
import logging
import os
import socket
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

import big_files
import db
import family_link
import i18n
import jobs
import lifecycle
import live_message
import problems
from live_message import LiveMessage
from shared_features import emit_event, note_job, report_markup

logger = logging.getLogger(__name__)

BOT_LABEL = os.environ.get("FAMILY_LABEL") or "ConvertBot"

# Which process a paid conversion in flight belongs to -- see jobs.HOLD_STALE_S.
PROCESS_ID = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:6]}"


def _upper(ext: str) -> str:
    return (ext or "?").upper()


# ---------------------------------------------------------------------------
# The status card: one message, redrawn while the person waits
# ---------------------------------------------------------------------------

BAR_CELLS = 10


def progress_bar(fraction: float) -> str:
    """Ten cells and the percentage. Never 100% before the work is done: a
    full bar with the file still to come reads as a hang."""
    fraction = max(0.0, min(fraction, 0.99))
    filled = int(fraction * BAR_CELLS + 0.5)
    return "🟩" * filled + "⬜" * (BAR_CELLS - filled) + f" {int(fraction * 100)}%"


def clock(seconds: float) -> str:
    """0:07, 1:42, 12:05 -- a waiting time somebody can read at a glance."""
    seconds = max(0, int(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


def time_left(done: float, total: float, elapsed: float) -> float | None:
    """Seconds still to go, from the rate so far -- or None until there is a
    rate worth believing: a guess from the first second is noise, and a
    number that swings wildly is worse than no number."""
    if not total or done <= 0 or elapsed < 3:
        return None
    fraction = done / total
    if fraction < 0.03 or fraction >= 1:
        return None
    return elapsed * (1 - fraction) / fraction


def status_card(lang: str, job: jobs.Job, now: float | None = None) -> str:
    now = time.monotonic() if now is None else now
    stage = job.extra.get("stage") or "converting"
    lines = [f"🔄 {_upper(job.src_ext)} → {_upper(job.target_ext)}",
             i18n.t(lang, "card_sending" if stage == "sending" else "card_converting")]
    progress = job.extra.get("progress")
    stage_started = job.extra.get("stage_started") or job.started_at or now
    timing = i18n.t(lang, "card_elapsed", elapsed=clock(now - (job.started_at or stage_started)))
    if progress and progress[1]:
        done, total = progress
        lines.append(progress_bar(done / total))
        left = time_left(done, total, now - stage_started)
        if left is not None:
            timing += " · " + i18n.t(lang, "card_left", left=clock(left))
    lines.append(timing)
    if job.price:
        lines.append(i18n.t(lang, "card_paid", price=job.price,
                            balance=job.extra.get("balance_after", "?")))
    else:
        lines.append(i18n.t(lang, "card_free"))
    return "\n".join(lines)


def download_card(lang: str, done: int, total: int, elapsed: float) -> str:
    """The same card for a file arriving, before there is a conversion."""
    lines = [i18n.t(lang, "card_downloading")]
    if total:
        lines.append(progress_bar(done / total))
        sizes = i18n.t(lang, "card_download_sizes", done=_megabytes(done), total=_megabytes(total))
        left = time_left(done, total, elapsed)
        if left is not None:
            sizes += " · " + i18n.t(lang, "card_left", left=clock(left))
        lines.append(sizes)
    return "\n".join(lines)


def _megabytes(size: int) -> str:
    return f"{size / 1024 / 1024:.1f} MB"


def result_filename(path) -> str:
    """What the converted file is called when it arrives.

    Every result used to be `converted.pdf`. Convert three documents and a
    phone downloads `converted.pdf`, `converted (1).pdf`, `converted (2).pdf`
    -- or, on a client that overwrites rather than numbering, one file three
    times. The owner: "The converted result file's name should be unique,
    maybe by naming it with date and time of conversion might help."

    So it carries the moment it was made, to the second, in an order that
    sorts: `converted_2026-09-13_235812.pdf`. UTC, because the container has
    no idea what time it is where the person reading the name is, and a name
    that claims a local time it guessed is worse than one that does not
    claim one.
    """
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S")
    return f"converted_{stamp}{Path(path).suffix}"


class ConvertRunner(jobs.Runner):
    def __init__(self, application, admin_ids, upload_dir, caption_for):
        self.application = application
        self.admin_ids = sorted(admin_ids or ())
        self.upload_dir = Path(upload_dir)
        self.caption_for = caption_for

    @property
    def bot(self):
        return self.application.bot

    def work_dir(self):
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        return self.upload_dir

    # ---- the status message --------------------------------------------

    def stop_keyboard(self, job: jobs.Job) -> InlineKeyboardMarkup:
        key = "job_stop_button" if job.state == "running" else "job_stop_button_queued"
        return InlineKeyboardMarkup([[InlineKeyboardButton(
            i18n.t(job.lang, key), callback_data=f"convstop:{job.id}")]])

    async def _status(self, job: jobs.Job, text: str, markup=None) -> None:
        """Rewrite the conversion's one status message. When something newer is
        below it -- a progress note, or the person writing -- it moves to the
        bottom as a new message, and the copy left above is deleted: two
        status messages for one conversion, one of them frozen, is exactly
        what this card exists to avoid."""
        live = job.extra.get("status_live")
        if live is None and job.extra.get("status_message") is not None:
            live = LiveMessage.adopt(job.extra["status_message"])
        try:
            if live is None:
                sent = await self.bot.send_message(chat_id=job.chat_id, text=text, reply_markup=markup)
                job.extra["status_message"] = sent
                job.extra["status_live"] = LiveMessage.adopt(sent)
                return
            before = live.message_id
            await live.set(self.bot, text, reply_markup=markup)
            job.extra["status_live"] = live
            if live.message_id != before:
                try:
                    await self.bot.delete_message(chat_id=live.chat_id, message_id=before)
                except Exception:
                    logger.debug("Could not remove the old status of conversion %s", job.id, exc_info=True)
        except Exception:
            logger.debug("Could not update the status of conversion %s", job.id, exc_info=True)

    # ---- the policy's hooks --------------------------------------------

    async def refuse_start(self, job: jobs.Job) -> bool:
        return lifecycle.is_paused()

    async def charge(self, job: jobs.Job) -> int | None:
        if job.price <= 0:
            return await asyncio.to_thread(family_link.star_balance, job.user_id)
        return await asyncio.to_thread(
            family_link.spend_stars, job.user_id, job.price, "spend",
            f"{job.src_ext}->{job.target_ext} conversion, {job.size / 1024:.0f} KB")

    async def started(self, job: jobs.Job, balance: int) -> None:
        await self.card(job)

    async def queued(self, job: jobs.Job, ahead: int) -> None:
        if ahead:
            text = i18n.t(job.lang, "job_queued", src=_upper(job.src_ext), target=_upper(job.target_ext),
                          ahead=ahead, price=job.price)
        else:
            text = i18n.t(job.lang, "job_starting", src=_upper(job.src_ext), target=_upper(job.target_ext))
        await self._status(job, text, self.stop_keyboard(job))

    async def card(self, job: jobs.Job) -> None:
        """The live status, redrawn only when what it says has changed."""
        text = status_card(job.lang, job)
        if text == job.extra.get("card_text"):
            return
        job.extra["card_text"] = text
        stopping = job.extra.get("stage") != "sending"
        await self._status(job, text, self.stop_keyboard(job) if stopping else None)

    async def progress(self, job: jobs.Job, stage: int, stages: int, elapsed_s: float,
                       next_s, limit_s: int) -> None:
        """A new message, not an edit: an edit arrives silently, and the point
        is that somebody who has waited two minutes hears about it. Sent under
        the status line, which is then moved below it for the ending."""
        def minutes(seconds: float) -> int:
            return max(1, round(seconds / 60))

        if next_s is not None:
            text = i18n.t(job.lang, "job_progress", elapsed=minutes(elapsed_s),
                          next=minutes(next_s - elapsed_s), last=minutes(limit_s - elapsed_s))
        else:
            text = i18n.t(job.lang, "job_progress_last", elapsed=minutes(elapsed_s),
                          last=minutes(limit_s - elapsed_s))
        try:
            # Not a reply to the status card: the card moves below this note
            # on its next redraw, and the copy this would quote is deleted.
            sent = await self.bot.send_message(chat_id=job.chat_id, text=text)
            live_message.bump(sent.chat_id, sent.message_id)
        except Exception:
            logger.debug("Could not send a progress update for conversion %s", job.id, exc_info=True)

    async def deliver(self, job: jobs.Job, outcome: jobs.Outcome) -> None:
        caption = self.caption_for(job.lang, outcome, job.src_ext, job.target_ext, job.price)
        if job.price:
            balance = await asyncio.to_thread(family_link.star_balance, job.user_id)
            caption += "\n\n" + i18n.t(job.lang, "balance_left_line", balance=balance)
        if outcome.bytes > jobs.SEND_LIMIT_BYTES and not jobs.LOCAL_BOT_API:
            # Over what the Bot API may send, so over MTProto instead. jobs.py
            # only lets a result this size through when that route exists
            # for this chat; if it has gone down since, this raises and the
            # job ends as a failed send, refunded.
            def sending(done, total):
                job.extra["progress"] = (done, total)
            await big_files.send_document(job.chat_id, outcome.path,
                                          result_filename(outcome.path), caption, progress=sending)
            return
        # Streamed from disk rather than read into memory: the result of a
        # large conversion should never have to exist in the bot's process.
        with open(outcome.path, "rb") as handle:
            await self.bot.send_document(
                chat_id=job.chat_id, document=handle, filename=result_filename(outcome.path),
                caption=caption, read_timeout=300, write_timeout=300)

    async def succeeded(self, job: jobs.Job, outcome: jobs.Outcome) -> None:
        note_job(True)
        await self._status(job, i18n.t(job.lang, "job_done", src=_upper(job.src_ext),
                                       target=_upper(job.target_ext)))

    async def refund(self, job: jobs.Job, kind: str) -> int:
        return await asyncio.to_thread(
            family_link.move_stars, job.user_id, job.price, "refund",
            f"{job.src_ext}->{job.target_ext} conversion ended: {kind}")

    async def hold(self, job: jobs.Job) -> None:
        await asyncio.to_thread(db.hold_conversion, job.id, job.user_id, job.chat_id, job.price,
                                job.lang, f"{job.src_ext}->{job.target_ext}", PROCESS_ID)

    async def release(self, job: jobs.Job) -> str:
        return await asyncio.to_thread(db.release_conversion, job.id)

    async def keep_alive_and_recover(self) -> int:
        """Stamp this process's paid conversions, and give back the credit of
        any whose process was killed before it could. Returns how many were
        given back. Called every jobs.HOLD_TOUCH_S."""
        await asyncio.to_thread(db.touch_conversions, PROCESS_ID)
        lost = await asyncio.to_thread(db.claim_orphaned_conversions, PROCESS_ID, jobs.HOLD_STALE_S)
        for row in lost:
            try:
                balance = await asyncio.to_thread(
                    family_link.move_stars, row["user_id"], row["price"], "refund",
                    f"{row['pair']} conversion ended: the bot was stopped before it finished")
            except Exception:
                logger.exception("Could not refund conversion %s, lost to a restart", row["job_id"])
                emit_event("error", "conversion",
                           f"⚠️ {BOT_LABEL}: a conversion lost to a restart could NOT be refunded "
                           f"({row['price']} ⚡, job {row['job_id']}). Refund it by hand.")
                continue
            lang = row["lang"] or "en"
            incident = problems.new_incident()
            text = (i18n.t(lang, "job_reason_interrupted") + "\n\n"
                    + i18n.t(lang, "job_refunded_line", price=row["price"], balance=balance)
                    + problems.code_line("CV-RESTARTED"))
            logger.info("Conversion %s was lost to a restart (CV-RESTARTED, incident %s); %s ⚡ refunded",
                        row["job_id"], incident, row["price"])
            try:
                await self.bot.send_message(chat_id=row["chat_id"], text=text,
                                            reply_markup=report_markup(lang, "CV-RESTARTED", incident))
            except Exception:
                logger.debug("Could not tell anyone about lost conversion %s", row["job_id"], exc_info=True)
            emit_event("warning", "conversion",
                       f"⚠️ {BOT_LABEL}: a conversion was lost to a restart and refunded "
                       f"({row['pair']}, {row['price']} ⚡ back). Incident {incident}.")
        return len(lost)

    async def failed(self, job: jobs.Job, kind: str, detail: str, refunded_to) -> None:
        lang = job.lang
        if kind == jobs.USER_CANCELLED:
            if job.started_at is None:
                text = i18n.t(lang, "job_removed_from_queue")
            elif job.price and job.charged:
                text = i18n.t(lang, "job_user_stopped", price=job.price)
            else:
                text = i18n.t(lang, "job_user_stopped_free")
            await self._status(job, text)
            return
        if kind == jobs.PAUSED:
            await self._status(job, i18n.t(lang, "update_soon_try_later_soon"))
            return
        if kind == jobs.NO_CREDIT:
            balance = await asyncio.to_thread(family_link.star_balance, job.user_id)
            await self._status(job, i18n.t(lang, "job_no_credit", price=job.price, balance=balance))
            return

        if kind in (jobs.TIMEOUT, jobs.CRASH, jobs.SEND_FAILED):
            note_job(False)
        limit = jobs.job_limit(job)
        reasons = {
            jobs.REFUSED: ("job_reason_refused", {"detail": detail}),
            jobs.TIMEOUT: ("job_reason_timeout", {"minutes": max(1, round(limit / 60))}),
            jobs.TOO_SLOW: ("job_reason_too_slow", {"minutes": max(2, round(job.extra.get("projected_s", 0) / 60)),
                                                    "limit": max(1, round(limit / 60))}),
            jobs.CRASH: ("job_reason_crash", {}),
            jobs.NO_MEMORY: ("job_reason_no_memory", {}),
            jobs.TOO_LARGE: ("job_reason_too_large", {"target": _upper(job.target_ext),
                                                      "mb": detail.split()[0] if detail else "?",
                                                      "limit": job.send_limit // (1024 * 1024)}),
            jobs.SEND_FAILED: ("job_reason_send_failed", {}),
            jobs.INTERRUPTED: ("job_reason_interrupted", {}),
        }
        key, fields = reasons.get(kind, ("job_reason_crash", {}))
        text = i18n.t(lang, key, **fields)
        if refunded_to is not None:
            text += "\n\n" + i18n.t(lang, "job_refunded_line", price=job.price, balance=refunded_to)
        # The code, and an incident the owner's alert and this log line carry
        # too, so a report from this person meets both.
        code = problems.JOB_ENDINGS.get(kind, "CV-CRASH")
        incident = job.extra.setdefault("incident", problems.new_incident())
        logger.info("Conversion %s ended %s (%s, incident %s): %s", job.id, kind, code, incident, detail)
        await self._status(job, text + problems.code_line(code), report_markup(lang, code, incident))

    async def alert(self, job: jobs.Job, kind: str, detail: str, elapsed: float, refunded_to) -> None:
        """Straight to the owner, from this bot, in English.

        Sent directly rather than only through family.events, because the
        family log is read by ManagerBot from the shared database and a
        TestBot on a laptop writes to a local one -- so an event alone would
        reach nobody from exactly the place the owner tests. It is still
        recorded as an info event, which ManagerBot does not forward, so in
        production the owner gets one message rather than two; if no admin
        can be reached directly it is raised as a warning instead, which
        ManagerBot does forward.
        """
        limit = jobs.job_limit(job)
        # What failed and what it cost to run -- never who it happened to.
        # The owner does not want user details in anything sent to them: no
        # id, no username, and no person's balance.
        credit = (f"{job.price} ⚡ back to the balance" if refunded_to is not None
                  else ("free conversion, nothing to refund" if not job.price else "not refunded"))
        text = (f"⚠️ {BOT_LABEL}: a conversion failed ({kind})\n"
                f"File: {job.size / 1024 / 1024:.1f} MB {_upper(job.src_ext)} → {_upper(job.target_ext)}\n"
                f"Ran: {elapsed:.0f}s of a {limit}s limit\n"
                f"Detail: {detail or '-'}\n"
                f"Code: {problems.JOB_ENDINGS.get(kind, 'CV-CRASH')} · incident {job.extra.get('incident', '-')}\n"
                f"Credit: {credit}")
        delivered = 0
        for admin_id in self.admin_ids:
            try:
                await self.bot.send_message(chat_id=admin_id, text=text)
                delivered += 1
            except Exception:
                logger.debug("Could not alert admin %s", admin_id, exc_info=True)
        emit_event("info" if delivered else "warning", "conversion", text)
