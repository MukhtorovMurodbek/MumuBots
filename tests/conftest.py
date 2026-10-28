"""Loads bot modules the way main.py does, against a throwaway database.

Needs a Postgres the tests may create and drop a database on:
TEST_ADMIN_URL (default: the docker-compose.yml database,
postgresql://postgres:postgres@localhost:5432/postgres).
"""
import importlib
import os
import sys
import tempfile
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]
ADMIN_URL = os.environ.get("TEST_ADMIN_URL", "postgresql://postgres:postgres@localhost:5432/postgres")
TEST_DB = "mumu_tests"
# A fixed key keeps the people counts the same from run to run.
TEST_KEY = "dGVzdC1rZXktdGVzdC1rZXktdGVzdC1rZXktMDEyMzQ="


def _fresh_database() -> str:
    with psycopg.connect(ADMIN_URL, autocommit=True) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)')
        conn.execute(f'CREATE DATABASE "{TEST_DB}"')
    return ADMIN_URL.rsplit("/", 1)[0] + "/" + TEST_DB


DATABASE_URL = _fresh_database()
os.environ["DATABASE_URL"] = DATABASE_URL
_key_file = Path(tempfile.mkdtemp(prefix="mumu-key-")) / "data_key"
_key_file.write_text(TEST_KEY + "\n", encoding="ascii")
os.environ["DATA_KEY_FILE"] = str(_key_file)
os.environ["LOG_TO_FILES"] = "0"

sys.path.insert(0, str(ROOT))
import main  # noqa: E402

_bots = {bot.folder: bot for bot in main.discover()[0]}
if main._BotFinder not in sys.meta_path:
    sys.meta_path.insert(0, main._BotFinder)


def load(folder: str, name: str):
    if folder not in main._PACKAGES:
        main.register(_bots[folder])
    return importlib.import_module(f"{folder}.{name}")


def connect():
    return psycopg.connect(DATABASE_URL)
