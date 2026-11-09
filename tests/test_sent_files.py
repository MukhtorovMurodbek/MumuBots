"""DownloaderBot's file-id store (sent_files.py) kept small and cheap as it grows (the owner,
2026-11-09): 16-byte keys, the ids as bytes, one row per post however it was shared, no write
when a row is already marked used today, and a short memory of the rows used lately."""
import base64
import os
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest

from conftest import connect, load

DOWNLOADER = "downloader_bot"

VIDEO_ID = "BAACAgIAAxkBAAIEZWZw8C2wE0OSUEsjSf2cZ7z5d9H_AAKbTQACmnmBS9Vhm7c2l5aBNQQ"
PHOTO_ID = "AgACAgIAAxkBAAIEZmZw8EBKq1d0cA6v2s0d8vYbXz1OAAJ-2zEbmnmBSyJ6Wm0P6jcXAQADAgADeQADNgQ"


def _utc_today() -> date:
    """The store's "today": the UTC date, in the bot and in the database alike."""
    return datetime.now(timezone.utc).date()


@pytest.fixture()
def store():
    sent_files = load(DOWNLOADER, "sent_files")
    sent_files.configure("123456:test-token")
    sent_files.reset_stats()
    yield sent_files
    sent_files.forget_all()


@pytest.fixture()
def db(store):
    module = load(DOWNLOADER, "db")
    module.init_db()
    with connect() as conn:
        conn.execute(f"TRUNCATE {module.DB_SCHEMA}.sent_files")
        conn.commit()
    return module


def test_items_round_trip_and_are_smaller_than_the_text(store):
    pairs = [("video", VIDEO_ID)] + [("photo", PHOTO_ID)] * 3
    blob = store.encode_items(pairs)
    assert isinstance(blob, bytes)
    assert store.decode_items(blob) == pairs
    # The ids as the bytes their base64url encodes: a quarter smaller than their text.
    assert len(blob) < sum(len(file_id) for _, file_id in pairs)


def test_an_id_that_does_not_round_trip_is_kept_as_text(store):
    odd = "not/base64+at all=="
    blob = store.encode_items([("photo", odd)])
    assert store.decode_items(blob) == [("photo", odd)]


def test_what_a_row_may_not_hold(store):
    assert store.encode_items([]) is None
    assert store.encode_items([("video", VIDEO_ID)] * 11) is None
    assert store.encode_items([("audio", VIDEO_ID)]) is None
    assert store.encode_items([("video", "")]) is None
    assert store.encode_items([("video", "x" * 400)]) is None


@pytest.mark.parametrize("blob", [b"", b"\x00", b"\x01", b"\x01\x00", b"\x01\x05\x03abc",
                                  b"\x01\x00\x05ab", b"\x01\x80\x02\xff\xfe", None, 42, "text"])
def test_rows_that_do_not_read_count_as_absent(store, blob):
    assert store.decode_items(blob) is None


def test_one_key_per_post_however_it_was_shared(store):
    key = store.post_key
    assert isinstance(key("instagram", "https://www.instagram.com/p/C0de_1/", False), uuid.UUID)
    assert (key("instagram", "https://www.instagram.com/p/C0de_1/", False)
            == key("instagram", "https://www.instagram.com/reel/C0de_1/", False))
    assert (key("twitter", "https://x.com/someone/status/1234567890", False)
            == key("twitter", "https://x.com/i/status/1234567890", False))
    # A different form, a different post, or a quality mark is another row.
    assert (key("instagram", "https://www.instagram.com/p/C0de_1/", False)
            != key("instagram", "https://www.instagram.com/p/C0de_1/", True))
    assert (key("instagram", "https://www.instagram.com/p/C0de_1/", False)
            != key("instagram", "https://www.instagram.com/p/C0de_2/", False))
    watch = "https://www.youtube.com/watch?v=abcdefghijk"
    assert key("youtube", watch, False) != key("youtube", watch + "#q720", False)
    assert store.post_identity("youtube", watch + "#upto480") == "youtube:abcdefghijk#upto480"
    # A link no rule reads is its own identity, as before.
    assert store.post_identity("tiktok", "https://vm.tiktok.com/ZMabc/") == "https://vm.tiktok.com/ZMabc/"


def test_the_key_depends_on_the_token(store):
    first = store.post_key("instagram", "https://www.instagram.com/p/C0de_1/", False)
    store.configure("654321:another-token")
    assert store.post_key("instagram", "https://www.instagram.com/p/C0de_1/", False) != first


def test_the_table_is_compact_and_a_2_1_table_is_replaced(store):
    db = load(DOWNLOADER, "db")
    db.init_db()
    schema = db.DB_SCHEMA
    with connect() as conn:
        conn.execute(f"DROP TABLE IF EXISTS {schema}.sent_files")
        conn.execute(f"CREATE TABLE {schema}.sent_files (post_key TEXT PRIMARY KEY, items TEXT NOT NULL, "
                     f"confirmed_at TIMESTAMPTZ NOT NULL, last_used DATE NOT NULL DEFAULT CURRENT_DATE)")
        conn.execute(f"INSERT INTO {schema}.sent_files VALUES ('ab' || repeat('0', 62), '[]', now(), CURRENT_DATE)")
        conn.commit()
    db.init_db()
    db.init_db()   # a second start changes nothing
    with connect() as conn:
        types = dict(conn.execute(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_schema = %s AND table_name = 'sent_files'", (schema,)).fetchall())
        options = conn.execute(
            "SELECT reloptions FROM pg_class WHERE oid = %s::regclass", (f"{schema}.sent_files",)).fetchone()[0]
        rows = conn.execute(f"SELECT count(*) FROM {schema}.sent_files").fetchone()[0]
    assert types["post_key"] == "uuid" and types["items"] == "bytea"
    assert "fillfactor=90" in options
    assert rows == 0


def test_store_read_and_marks(db, store):
    key = store.post_key("instagram", "https://www.instagram.com/reel/C0de_1/", False)
    items = store.encode_items([("video", VIDEO_ID)])
    db.sent_files_put(key, items)
    blob, confirmed_at, last_used = db.sent_files_get(key)
    assert store.decode_items(blob) == [("video", VIDEO_ID)]
    assert store.is_fresh(confirmed_at) and last_used == _utc_today()
    assert db.sent_files_get(store.post_key("instagram", "https://www.instagram.com/p/Other/", False)) is None

    table = f"{db.DB_SCHEMA}.sent_files"

    def version():
        with connect() as conn:
            return conn.execute(f"SELECT xmin::text FROM {table} WHERE post_key = %s", (key,)).fetchone()[0]

    # Marked today already: marking again writes no new row version.
    before = version()
    db.sent_files_used(key)
    assert version() == before
    # Marked on an earlier day: it is marked today.
    with connect() as conn:
        conn.execute(f"UPDATE {table} SET last_used = (now() AT TIME ZONE 'UTC')::date - 3 WHERE post_key = %s", (key,))
        conn.commit()
    db.sent_files_used(key)
    assert db.sent_files_get(key)[2] == _utc_today()

    db.sent_files_drop(key)
    assert db.sent_files_get(key) is None


def test_unused_rows_are_swept(db, store):
    old = store.post_key("instagram", "https://www.instagram.com/p/Old/", False)
    new = store.post_key("instagram", "https://www.instagram.com/p/New/", False)
    for key in (old, new):
        db.sent_files_put(key, store.encode_items([("photo", PHOTO_ID)]))
    with connect() as conn:
        conn.execute(f"UPDATE {db.DB_SCHEMA}.sent_files SET last_used = (now() AT TIME ZONE 'UTC')::date - %s WHERE post_key = %s",
                     (db.SENT_FILES_KEEP_DAYS + 1, old))
        conn.commit()
    db.prune_old_data()
    assert db.sent_files_get(old) is None and db.sent_files_get(new) is not None


def test_memory_of_recent_rows(store, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(store, "_monotonic", lambda: now[0])
    key = store.post_key("instagram", "https://www.instagram.com/p/C0de_1/", False)
    stamp = datetime.now(timezone.utc) - timedelta(hours=1)
    store.remember(key, b"\x01", stamp, _utc_today() - timedelta(days=1))
    assert store.recall(key) == (b"\x01", stamp, _utc_today() - timedelta(days=1))
    assert not store.used_today(key)
    store.note_used(key)
    assert store.used_today(key)
    # It does not decide trust: the remembered time is the stored one.
    assert store.recall(key)[1] == stamp
    now[0] += store.RECENT_SECONDS + 1
    assert store.recall(key) is None
    store.remember(key, b"\x01", stamp, None)
    store.forget(key)
    assert store.recall(key) is None
    monkeypatch.setattr(store, "RECENT_MAX", 3)
    keys = [uuid.UUID(bytes=os.urandom(16)) for _ in range(5)]
    for each in keys:
        store.remember(each, b"\x01", stamp, None)
    assert store.recall(keys[0]) is None and store.recall(keys[-1]) is not None


def test_real_telegram_ids_round_trip_byte_for_byte(store):
    # Telegram's ids are unpadded base64url; any length and any trailing bits must come back.
    for size in range(40, 80):
        file_id = base64.urlsafe_b64encode(os.urandom(size)).decode().rstrip("=")
        assert store.decode_items(store.encode_items([("video", file_id)])) == [("video", file_id)]


# ---------------------------------------------------------------------------
# The bot's resend path (bot._resend_by_file_id) with the memory in front of the database
# ---------------------------------------------------------------------------

class _Status:
    def __init__(self):
        self.deleted = 0

    async def delete(self, bot):
        self.deleted += 1


def _update(user_id=4242, chat_id=4242):
    from types import SimpleNamespace
    return SimpleNamespace(effective_user=SimpleNamespace(id=user_id),
                           effective_chat=SimpleNamespace(id=chat_id))


@pytest.fixture()
def resend(db, store, monkeypatch):
    """bot._resend_by_file_id with Telegram, the lossless setting and the lookups faked; the
    database calls are real and counted."""
    import asyncio
    from types import SimpleNamespace
    bot = load(DOWNLOADER, "bot")
    # Loading bot.py configures the store from its (absent) token: give it the test's again.
    store.configure("123456:test-token")
    calls = {"get": 0, "used": 0, "confirmed": 0, "sent": [], "lookup": "present"}

    def counted(name, real):
        def wrapper(*args):
            calls[name] += 1
            return real(*args)
        return wrapper

    monkeypatch.setattr(bot, "sent_files_get", counted("get", db.sent_files_get))
    monkeypatch.setattr(bot, "sent_files_used", counted("used", db.sent_files_used))
    monkeypatch.setattr(bot, "sent_files_confirmed", counted("confirmed", db.sent_files_confirmed))
    monkeypatch.setattr(bot, "get_lossless_enabled", lambda user_id: False)
    monkeypatch.setattr(bot, "note_job", lambda ok: None)

    async def send_media(update, context, items, lang, **kwargs):
        calls["sent"].append([file.file_id for file, _ in items])

    async def lookup(platform, url):
        return calls["lookup"]

    monkeypatch.setattr(bot, "_send_media", send_media)
    monkeypatch.setattr(bot.resolvers, "lookup", lookup)
    context = SimpleNamespace(user_data={}, bot=None)

    def run(url="https://www.instagram.com/reel/C0de_1/"):
        return asyncio.run(bot._resend_by_file_id(_update(), context, "en", _Status(), "instagram", url, None))

    return SimpleNamespace(run=run, calls=calls, bot=bot)


def test_resend_reads_the_database_once_for_a_popular_post(resend, db, store):
    key = store.post_key("instagram", "https://www.instagram.com/p/C0de_1/", False)
    db.sent_files_put(key, store.encode_items([("video", VIDEO_ID)]))
    assert resend.run() is True
    assert resend.run("https://www.instagram.com/p/C0de_1/") is True   # same post, other link
    assert resend.run() is True
    calls = resend.calls
    assert calls["sent"] == [[VIDEO_ID]] * 3
    assert calls["get"] == 1, "later sends of the same post come from memory"
    assert calls["used"] == 0, "a row stored today is already marked used today"
    assert store.stats()["remembered"] == 2


def test_resend_of_a_stale_row_asks_first_and_forgets_a_removed_post(resend, db, store):
    key = store.post_key("instagram", "https://www.instagram.com/reel/C0de_1/", False)
    db.sent_files_put(key, store.encode_items([("video", VIDEO_ID)]))
    with connect() as conn:
        conn.execute(f"UPDATE {db.DB_SCHEMA}.sent_files SET confirmed_at = now() - interval '2 days', "
                     f"last_used = (now() AT TIME ZONE 'UTC')::date - 2 WHERE post_key = %s", (key,))
        conn.commit()
    assert resend.run() is True
    assert resend.calls["confirmed"] == 1 and resend.calls["sent"] == [[VIDEO_ID]]
    row = db.sent_files_get(key)
    assert store.is_fresh(row[1]) and row[2] == _utc_today()

    # Gone from the platform: the row goes, from the database and from memory.
    with connect() as conn:
        conn.execute(f"UPDATE {db.DB_SCHEMA}.sent_files SET confirmed_at = now() - interval '2 days' "
                     f"WHERE post_key = %s", (key,))
        conn.commit()
    store.forget_all()
    resend.calls["lookup"] = "missing"
    assert resend.run() is None
    assert db.sent_files_get(key) is None and store.recall(key) is None


def test_resend_drops_ids_telegram_refuses(resend, db, store, monkeypatch):
    from telegram.error import BadRequest
    key = store.post_key("instagram", "https://www.instagram.com/reel/C0de_1/", False)
    db.sent_files_put(key, store.encode_items([("video", VIDEO_ID)]))

    async def refuse(*args, **kwargs):
        raise BadRequest("Wrong file identifier/http url specified")

    monkeypatch.setattr(resend.bot, "_send_media", refuse)
    assert resend.run() is None
    assert db.sent_files_get(key) is None and store.recall(key) is None
