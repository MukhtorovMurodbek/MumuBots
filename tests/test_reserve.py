"""Reserve copies (the owner, 2026-11-03): the nightly copy, the volume's current copy,
and a restore that erases again whoever used /deletemydata after the copy was taken."""
import asyncio
import base64
import importlib
import json
import time
import zipfile
from datetime import datetime, timedelta, timezone
from io import BytesIO

import pytest

from conftest import DATABASE_URL, ROOT, connect, load

STICKER = "sticker_bot"
MANAGER = "manager_bot"


@pytest.fixture(scope="module")
def family():
    load(STICKER, "db").init_db()
    load(MANAGER, "db").init_db()
    link = load(STICKER, "family_link")
    link.init_family_schema()
    assert link.check_data_key(fresh=True)["usable"]
    assert load(MANAGER, "family_link").check_data_key(fresh=True)["usable"]
    return link


@pytest.fixture
def offline(monkeypatch):
    """The owner's offline key: BACKUP_PUBLIC_KEY set to its public half; the private
    half (what a restore is given) is returned."""
    identity, public = load(MANAGER, "offline_key").new_identity()
    monkeypatch.setenv("BACKUP_PUBLIC_KEY", public)
    return identity


@pytest.fixture
def volume(tmp_path, monkeypatch, offline):
    for folder in (STICKER, MANAGER):
        monkeypatch.setattr(load(folder, "reserve"), "RESERVE_DIR", str(tmp_path))
    return tmp_path


def _person(user_id: int, when: datetime) -> None:
    with connect() as conn:
        conn.execute("INSERT INTO sticker_bot.user_settings (user_id) VALUES (%s) ON CONFLICT DO NOTHING", (user_id,))
        conn.execute("INSERT INTO sticker_bot.activity_events (user_id, occurred_at) VALUES (%s, %s)", (user_id, when))


def _rows(table: str, user_id: int) -> int:
    with connect() as conn:
        return conn.execute(f"SELECT count(*) FROM sticker_bot.{table} WHERE user_id = %s", (user_id,)).fetchone()[0]


def _erase(user_id: int) -> None:
    """What /deletemydata does, without Telegram."""
    load(STICKER, "db").erase_user(user_id)
    assert load(STICKER, "reserve").note_erasure(user_id)


def _restore_script():
    spec = importlib.util.spec_from_file_location("restore_reserve", ROOT / "scripts" / "restore_reserve.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _encrypt_script():
    return _restore_script()._encrypt_script()


def _emergency() -> None:
    """Every table the bots made, emptied: the database lost, the bots' tables made again."""
    with connect() as conn:
        tables = conn.execute(
            "SELECT schemaname, tablename FROM pg_tables WHERE schemaname IN ('sticker_bot', 'manager_bot', 'family')"
        ).fetchall()
        conn.execute("TRUNCATE " + ", ".join(f'"{s}"."{t}"' for s, t in tables))


# ── the erasure note ─────────────────────────────────────────────────────────

def test_an_erasure_is_noted_for_the_offline_key_in_the_table_and_the_journal(family, volume, offline):
    reserve = load(STICKER, "reserve")
    offline_key = load(STICKER, "offline_key")
    assert reserve.note_erasure(7001)
    with connect() as conn:
        bot, schema, sealed = conn.execute(
            "SELECT bot, db_schema, sealed_user FROM family.erasures ORDER BY id DESC LIMIT 1").fetchone()
    assert (bot, schema) == (STICKER, STICKER)
    assert b"7001" not in bytes(sealed)
    # Only the owner's private key opens it: nothing on the server (the data key included) does.
    assert offline_key.decrypt(bytes(sealed), offline) == b"7001"
    with pytest.raises(load(STICKER, "vault").VaultError):
        load(STICKER, "vault").open_id(bytes(sealed), reserve.ERASURE_PURPOSE)
    line = json.loads((volume / "erasures.jsonl").read_text().splitlines()[-1])
    assert line["bot"] == STICKER and "7001" not in json.dumps(line)
    assert offline_key.decrypt(base64.b64decode(line["user"]), offline) == b"7001"


def test_without_the_offline_key_a_note_is_sealed_with_the_data_key(family, monkeypatch):
    reserve = load(STICKER, "reserve")
    monkeypatch.delenv("BACKUP_PUBLIC_KEY", raising=False)
    monkeypatch.setattr(reserve, "RESERVE_DIR", "")
    assert reserve.note_erasure(7003)
    with connect() as conn:
        sealed = conn.execute("SELECT sealed_user FROM family.erasures ORDER BY id DESC LIMIT 1").fetchone()[0]
    assert load(STICKER, "vault").open_id(bytes(sealed), reserve.ERASURE_PURPOSE) == 7003


def test_without_a_volume_the_note_still_goes_in_the_table(family, monkeypatch, tmp_path):
    reserve = load(STICKER, "reserve")
    monkeypatch.setattr(reserve, "RESERVE_DIR", "")
    assert reserve.journal_path() is None
    assert reserve.note_erasure(7002)


# ── the copies ───────────────────────────────────────────────────────────────

def test_a_copy_opens_only_with_the_offline_key_and_its_label_cannot_be_changed(family, offline, monkeypatch):
    copies = load(MANAGER, "reserve_copies")
    offline_key = load(MANAGER, "offline_key")
    when = datetime(2026, 11, 3, 13, 0, tzinfo=timezone.utc)
    blob = copies.seal_archive(b"PK archive", "latest", when)
    assert copies.read_header(blob)["kind"] == "latest"
    assert copies.open_archive(blob, offline) == b"PK archive"
    assert b"PK archive" not in blob
    relabelled = blob.replace(b'"latest"', b'"nightly"')
    with pytest.raises(offline_key.OfflineKeyError, match="label"):
        copies.open_archive(relabelled, offline)
    with pytest.raises(offline_key.OfflineKeyError):
        copies.open_archive(blob, offline_key.new_identity()[0])
    assert copies.read_header(b"PK\x03\x04 a plain zip") is None
    monkeypatch.delenv("BACKUP_PUBLIC_KEY")
    with pytest.raises(offline_key.OfflineKeyError):
        copies.seal_archive(b"PK archive", "latest", when)


def test_no_volume_copy_without_the_offline_key(family, tmp_path, monkeypatch):
    """The volume holds the data key too, so a copy goes there only encrypted to a key that is
    not on the server."""
    copies = load(MANAGER, "reserve_copies")
    monkeypatch.setattr(load(MANAGER, "reserve"), "RESERVE_DIR", str(tmp_path))
    monkeypatch.delenv("BACKUP_PUBLIC_KEY", raising=False)
    assert "BACKUP_PUBLIC_KEY" in copies.volume_problem()
    monkeypatch.setenv("BACKUP_PUBLIC_KEY", "AGE-SECRET-KEY-1" + "Q" * 58)
    assert "PRIVATE" in copies.volume_problem() or "not an age" in copies.volume_problem()
    bot = load(MANAGER, "bot")
    monkeypatch.setattr(bot, "_RESERVE", {"mark": None, "written_at": None, "tried_at": None, "problem": None})
    telegram = _Telegram()
    monkeypatch.setattr(bot, "ADMIN_IDS", {42})
    monkeypatch.delenv("BACKUP_PUBLIC_KEY")
    asyncio.run(bot.keep_reserve_current(type("Context", (), {"bot": telegram})()))
    assert not (tmp_path / "latest.mumu-reserve").exists()
    assert "BACKUP_PUBLIC_KEY" in (bot._RESERVE["problem"] or "")


def test_latest_leaves_out_the_logs_and_keeps_what_matters(family, volume, offline):
    copies = load(MANAGER, "reserve_copies")
    _person(7100, datetime.now(timezone.utc))
    copies.write_latest(DATABASE_URL)
    archive = copies.open_archive((volume / "latest.mumu-reserve").read_bytes(), offline)
    with zipfile.ZipFile(BytesIO(archive)) as opened:
        names = {f"{t['schema']}.{t['table']}" for t in json.loads(opened.read("MANIFEST.json"))["tables"]}
    assert "sticker_bot.user_settings" in names and "family.erasures" in names
    assert "sticker_bot.activity_events" not in names and "family.usage_samples" not in names


def test_the_change_mark_moves_when_something_that_matters_changes(family):
    copies = load(MANAGER, "reserve_copies")
    before = copies.change_mark(DATABASE_URL)
    with connect() as conn:
        conn.execute("INSERT INTO sticker_bot.user_settings (user_id) VALUES (7200)")
    deadline = time.monotonic() + 5
    while copies.change_mark(DATABASE_URL) == before:
        assert time.monotonic() < deadline, "the counts never moved"
        time.sleep(0.2)


def test_old_erasure_notes_are_dropped_from_the_table_and_the_journal(family, volume):
    copies = load(MANAGER, "reserve_copies")
    reserve = load(STICKER, "reserve")
    assert reserve.note_erasure(7300)
    later = datetime.now(timezone.utc) + timedelta(days=reserve.KEEP_DAYS + 1)
    assert copies.prune_erasures(DATABASE_URL, later) >= 2
    with connect() as conn:
        assert conn.execute("SELECT count(*) FROM family.erasures").fetchone()[0] == 0
    assert (volume / "erasures.jsonl").read_text() == ""


# ── the restore ──────────────────────────────────────────────────────────────

def test_a_restore_erases_again_whoever_erased_after_the_copy(family, volume, tmp_path, offline):
    copies = load(MANAGER, "reserve_copies")
    restore = _restore_script()
    now = datetime.now(timezone.utc)
    _person(8001, now)     # erases after the nightly copy
    _person(8002, now)     # stays
    nightly = tmp_path / "botfamily.zip"
    nightly.write_bytes(copies.build_archive(DATABASE_URL))

    _erase(8001)
    _person(8003, now)     # arrives after the copy
    copies.write_latest(DATABASE_URL)
    _emergency()

    script = _encrypt_script()
    restore.restore(DATABASE_URL, nightly, volume / "latest.mumu-reserve", volume / "erasures.jsonl",
                    script, say=lambda *_: None, identity=offline)
    assert _rows("user_settings", 8001) == 0
    # Its activity is not in `latest`; it came back with the nightly copy, and went again.
    assert _rows("activity_events", 8001) == 0
    assert _rows("user_settings", 8002) == 1 and _rows("activity_events", 8002) == 1
    assert _rows("user_settings", 8003) == 1


def test_a_note_only_in_the_journal_still_erases(family, volume, tmp_path, offline):
    """Erased after the volume's last copy, and the database lost before the next."""
    copies = load(MANAGER, "reserve_copies")
    restore = _restore_script()
    _person(8101, datetime.now(timezone.utc))
    nightly = tmp_path / "nightly.mumu-reserve"
    copies.write_nightly(copies.build_archive(DATABASE_URL), datetime.now(timezone.utc))
    (volume / "nightly.mumu-reserve").replace(nightly)
    copies.write_latest(DATABASE_URL)
    _erase(8101)
    _emergency()

    with pytest.raises(restore.Refusal, match="backup-key-file"):
        restore.restore(DATABASE_URL, nightly, volume / "latest.mumu-reserve", volume / "erasures.jsonl",
                        _encrypt_script(), say=lambda *_: None)
    restore.restore(DATABASE_URL, nightly, volume / "latest.mumu-reserve", volume / "erasures.jsonl",
                    _encrypt_script(), say=lambda *_: None, identity=offline)
    assert _rows("user_settings", 8101) == 0
    with connect() as conn:  # the journal's note is in the table again, for the next restore
        assert conn.execute("SELECT count(*) FROM family.erasures").fetchone()[0] >= 1


def test_a_copy_older_than_the_promise_is_refused(family, tmp_path, offline):
    copies = load(MANAGER, "reserve_copies")
    restore = _restore_script()
    old = tmp_path / "old.mumu-reserve"
    old.write_bytes(copies.seal_archive(copies.build_archive(DATABASE_URL), "nightly",
                                        datetime.now(timezone.utc) - timedelta(days=31)))
    with pytest.raises(restore.Refusal, match="more than 30 days"):
        restore.restore(DATABASE_URL, old, None, None, _encrypt_script(), say=lambda *_: None, identity=offline)


def test_the_address_is_never_an_argument():
    assert _restore_script().main(["--database-url-env", "postgresql://x", "--key-file", "k", "--copy", "c"]) == 2


# ── ManagerBot's jobs, with a stand-in for Telegram ─────────────────────────

class _Telegram:
    def __init__(self):
        self.sent, self.pinned, self.said = [], [], []

    async def send_document(self, chat_id, document, filename, caption, **_):
        self.sent.append((chat_id, filename, caption, document.getvalue()))
        return type("Sent", (), {"message_id": len(self.sent)})()

    async def pin_chat_message(self, chat_id, message_id, **_):
        self.pinned.append((chat_id, message_id))

    async def send_message(self, chat_id, text, **_):
        self.said.append(text)


def test_the_nightly_copy_is_sent_encrypted_pinned_and_kept_on_the_volume(family, volume, monkeypatch, offline,
                                                                         tmp_path):
    bot = load(MANAGER, "bot")
    monkeypatch.setattr(bot, "ADMIN_IDS", {42})
    telegram = _Telegram()
    context = type("Context", (), {"bot": telegram})()
    asyncio.run(bot._nightly_reserve_copy(context))
    (chat, name, caption, data), = telegram.sent
    assert chat == 42 and name.startswith("botfamily_") and name.endswith(".zip.age")
    assert "Nightly reserve copy" in caption and "Encrypted to your offline key" in caption
    assert "Volume: encrypted copy kept" in caption
    assert telegram.pinned == [(42, 1)]
    with zipfile.ZipFile(BytesIO(load(MANAGER, "offline_key").decrypt(data, offline))) as opened:
        assert "sticker_bot/user_settings.csv" in opened.namelist()
    copies = load(MANAGER, "reserve_copies")
    assert copies.read_header((volume / "nightly.mumu-reserve").read_bytes())["kind"] == "nightly"
    # The restore takes the Telegram file as it came, with the private key.
    copy = tmp_path / name
    copy.write_bytes(data)
    archive, _, kind = _restore_script().read_copy(copy, copies, offline)
    assert kind == "encrypted" and archive.startswith(b"PK")


def test_without_the_offline_key_the_telegram_copy_is_plain_and_says_so(family, monkeypatch):
    bot = load(MANAGER, "bot")
    monkeypatch.delenv("BACKUP_PUBLIC_KEY", raising=False)
    monkeypatch.setattr(load(MANAGER, "reserve"), "RESERVE_DIR", "")
    monkeypatch.setattr(bot, "ADMIN_IDS", {42})
    telegram = _Telegram()
    asyncio.run(bot._nightly_reserve_copy(type("Context", (), {"bot": telegram})()))
    (chat, name, caption, data), = telegram.sent
    assert name.endswith(".zip") and "Not encrypted" in caption and data.startswith(b"PK")


def test_the_volume_copy_is_written_on_change_and_not_otherwise(family, volume, monkeypatch):
    bot = load(MANAGER, "bot")
    monkeypatch.setattr(bot, "_RESERVE", {"mark": None, "written_at": None, "tried_at": None, "problem": None})
    monkeypatch.setattr(load(MANAGER, "reserve_copies"), "MIN_SECONDS", 0)
    context = type("Context", (), {"bot": _Telegram()})()
    latest = volume / "latest.mumu-reserve"
    asyncio.run(bot.keep_reserve_current(context))
    first = latest.read_bytes()
    asyncio.run(bot.keep_reserve_current(context))
    assert latest.read_bytes() == first  # nothing changed, nothing written
    with connect() as conn:
        conn.execute("INSERT INTO sticker_bot.user_settings (user_id) VALUES (9001)")
    deadline = time.monotonic() + 5
    while latest.read_bytes() == first:
        assert time.monotonic() < deadline, "the change was never copied"
        time.sleep(0.3)
        asyncio.run(bot.keep_reserve_current(context))
    assert bot._RESERVE["problem"] is None
