from __future__ import annotations

import base64
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import db
import family_link
import offline_key
import vault

logger = logging.getLogger(__name__)

RESERVE_DIR = os.environ.get("RESERVE_DIR", "").strip()

KEEP_DAYS = 30

ERASURE_PURPOSE = "reserve.erasure"
JOURNAL_NAME = "erasures.jsonl"

BOT = __name__.partition(".")[0]


def journal_path() -> "Path | None":
    return Path(RESERVE_DIR) / JOURNAL_NAME if RESERVE_DIR else None


def _sealed_id(user_id: int) -> bytes:
    try:
        public = offline_key.recipient()
    except offline_key.OfflineKeyError:
        logger.exception("Erasure notes fall back to the data key")
        public = None
    if public:
        return offline_key.encrypt(b"%d" % int(user_id), public)
    if not family_link.vault_usable():
        raise vault.VaultError("the data key does not open")
    return vault.seal_id(user_id, ERASURE_PURPOSE)


def note_erasure(user_id: int) -> bool:
    try:
        sealed = _sealed_id(user_id)
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
            with open(path, "a", encoding="ascii") as handle:
                handle.write(line + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            kept = True
        except OSError:
            logger.exception("An erasure could not be noted on the reserve volume")
    return kept
