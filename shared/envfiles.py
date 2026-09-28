"""Where a bot's settings come from, first match wins:

    1. the real environment -- Railway's variables, a shell, a test;
    2. the bot's own .env, next to its bot.py ("local");
    3. the family's .env, here in shared/ ("shared").

So a setting every bot needs -- DATABASE_URL, the API id for big files, the
owner's id -- is written once, in shared/.env, and a bot that needs it
different says so in its own .env. The owner, 2026-09-28: "There should be
two types of .env files: first type is local, one per bot, second type is
global, lives in shared folder and holds the variables in a centralized file.
... Locals can override shared."

A few settings are one bot's by nature -- its schema, its name on the family
bus, its policy links, its payment tokens. Put in shared/.env they would
apply to all five at once, so there they are ignored, with a warning that
says where they belong. An empty value is no value: `KEY=` in a local .env
does not hide the shared one.

ADMIN_ID is the owner's id for all five at once -- the name Railway's
variables and the C edition use. It stands in for a bot's own
<PREFIX>_ADMIN_ID (SBOT_ADMIN_ID, ...) when that is not set anywhere; the
bot's own still wins. The owner, 2026-09-29, wrote shared/.env with Railway's
names -- one ADMIN_ID -- which the five had not read.

ENV_FILES=off in the environment skips both files, and ADMIN_ID with them.
TestBot sets it: its settings come from testbot/token.env, and the bots' own
files hold the real tokens.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

SHARED_DIR = Path(__file__).resolve().parent
PER_BOT_ONLY = ("DB_SCHEMA", "FAMILY_BOT_ID", "FAMILY_LABEL", "PRIVACY_URL", "TERMS_URL")
PER_BOT_ONLY_PREFIXES = ("PAYMENT_PROVIDER_TOKEN_",)
FAMILY_ADMIN = "ADMIN_ID"
PREFIXES = {"sticker_bot": "SBOT", "convert_bot": "CBOT", "downloader_bot": "DBOT",
            "anon_bot": "ABOT", "manager_bot": "MBOT"}

logger = logging.getLogger(__name__)


def _read(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        from dotenv import dotenv_values
    except ImportError:
        logger.warning("%s exists but python-dotenv is not installed -- it is not read.", path)
        return {}
    return {key: value for key, value in dotenv_values(path).items() if value}


def per_bot_only(key: str) -> bool:
    return key in PER_BOT_ONLY or key.startswith(PER_BOT_ONLY_PREFIXES)


def load(bot_file: str | os.PathLike) -> dict:
    """Fill the environment from the bot's own .env, then shared/.env, never
    over anything already set. Returns what was set, and from where."""
    if os.environ.get("ENV_FILES", "").strip().lower() in ("0", "off", "no", "false"):
        return {}
    bot_dir = Path(bot_file).resolve().parent
    applied = {}
    for path, shared in ((bot_dir / ".env", False), (SHARED_DIR / ".env", True)):
        for key, value in _read(path).items():
            if shared and per_bot_only(key):
                logger.warning("shared/.env sets %s, which is one bot's alone -- ignored. Put it in %s/.env.",
                               key, bot_dir.name)
                continue
            if not os.environ.get(key):
                os.environ[key] = value
                applied[key] = "shared" if shared else "local"
    own = _own_admin(bot_dir)
    if own and not os.environ.get(own) and os.environ.get(FAMILY_ADMIN):
        os.environ[own] = os.environ[FAMILY_ADMIN]
        applied[own] = FAMILY_ADMIN
    return applied


def _own_admin(bot_dir: Path) -> str | None:
    prefix = PREFIXES.get(bot_dir.name)
    return f"{prefix}_ADMIN_ID" if prefix else None


def merged(bot_dir: str | os.PathLike) -> dict:
    """The two files as one, local over shared, without touching the
    environment -- for scripts that read a bot's settings from outside it."""
    bot_dir = Path(bot_dir)
    values = {key: value for key, value in _read(SHARED_DIR / ".env").items() if not per_bot_only(key)}
    values.update(_read(bot_dir / ".env"))
    own = _own_admin(bot_dir)
    if own and not values.get(own) and values.get(FAMILY_ADMIN):
        values[own] = values[FAMILY_ADMIN]
    return values
