"""Reserve copies of the database, and what keeps them honest about erasures.

The owner, 2026-11-03: a copy of the database every night, sent to the owner's
Telegram (ManagerBot's nightly report), and a copy of what matters on a Railway
volume, kept current as it changes -- "reserve copies are not used unless it's
an emergency". ManagerBot makes the copies (manager_bot/reserve_copies.py);
this part is every bot's, because every bot has a /deletemydata.

THE PROBLEM IT SOLVES. Someone erases their data at noon; the database is lost
at three; it comes back from last night's copy, which still holds them. So each
erasure leaves a note -- which bot, the person's id sealed with the data key,
and when -- in two places: the family's `erasures` table (which the volume copy
carries within minutes) and, when the volume is mounted here, a line appended
to RESERVE_DIR/erasures.jsonl at once. The restore (scripts/restore_reserve.py)
erases again everyone whose note is newer than the copy it restored.

KEEP_DAYS is the promise in LEGAL.md: no copy older than this is ever restored,
so no note needs to outlive it either. The nightly report deletes older notes.
"""
from __future__ import annotations

import base64
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import db
import family_link
import vault

logger = logging.getLogger(__name__)

# The Railway volume's folder for the reserve copies, or empty for none.
RESERVE_DIR = os.environ.get("RESERVE_DIR", "").strip()

KEEP_DAYS = 30

ERASURE_PURPOSE = "reserve.erasure"
JOURNAL_NAME = "erasures.jsonl"

# This module's package is the bot it was loaded for ("sticker_bot.reserve"),
# which is how the restore finds that bot's own erase.
BOT = __name__.partition(".")[0]


def journal_path() -> "Path | None":
    return Path(RESERVE_DIR) / JOURNAL_NAME if RESERVE_DIR else None


def note_erasure(user_id: int) -> bool:
    """Called by /deletemydata once the erase has happened. False when no note
    could be kept anywhere: then a restore would bring this person back, which
    is worth an error in the log, not a different answer to them."""
    try:
        if not family_link.vault_usable():
            raise vault.VaultError("the data key does not open")
        sealed = vault.seal_id(user_id, ERASURE_PURPOSE)
    except Exception:
        logger.exception("An erasure could not be noted")
        return False
    now = datetime.now(timezone.utc)
    kept = False
    try:
        with db.pooled() as conn:
            conn.execute(
                f"INSERT INTO {family_link.FAMILY_SCHEMA}.erasures (bot, db_schema, sealed_user, erased_at) "
                f"VALUES (%s, %s, %s, %s)",
                (BOT, db.DB_SCHEMA, sealed, now))
            conn.commit()
        kept = True
    except Exception:
        logger.exception("An erasure could not be noted in the database")
    path = journal_path()
    if path is not None:
        line = json.dumps({"bot": BOT, "db_schema": db.DB_SCHEMA, "at": now.isoformat(),
                           "user": base64.b64encode(sealed).decode("ascii")})
        try:
            # One write of one line, flushed to the disk before the person is
            # told their data is gone: the volume is what survives the database.
            with open(path, "a", encoding="ascii") as handle:
                handle.write(line + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            kept = True
        except OSError:
            logger.exception("An erasure could not be noted on the reserve volume")
    return kept
