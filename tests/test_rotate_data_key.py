"""Replacing the data key (scripts/rotate_data_key.py; the owner, 2026-11-09, after Railway's
breach of variables): everything the bots wrote under one key is read back by the bots' own
code under the next, nothing opens under the old one, and a rotation back restores the test
database for the tests that follow."""
import base64
import importlib.util
import os
from pathlib import Path

import psycopg
import pytest

import conftest
from conftest import DATABASE_URL, ROOT, connect, load

ANON, STICKER, DOWNLOADER, MANAGER = "anon_bot", "sticker_bot", "downloader_bot", "manager_bot"


def _script(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# One copy of each script for the whole file, so that its Refusal is one class.
ROTATE = _script("rotate_data_key")
ENCRYPT = _script("encrypt_personal_data")


@pytest.fixture(scope="module")
def world():
    """What the bots write, through their own code, under the test key."""
    for folder in (STICKER, DOWNLOADER, ANON, MANAGER):
        load(folder, "db").init_db()
    link = load(STICKER, "family_link")
    link.init_family_schema()
    assert link.check_data_key(fresh=True)["usable"]
    for folder in (STICKER, ANON):
        load(folder, "lifecycle").init_state_table()
    load(ANON, "lifecycle").init_waitlist_table()

    anon = load(ANON, "db")
    token, _ = anon.get_or_create_anon_link(1001)
    first = anon.start_new_anon_conversation(1001, 1003)
    anon.set_anon_blocked(1001, 1003, True)
    conversation = anon.start_new_anon_conversation(1001, 1002)
    anon.record_relay_and_touch(conversation["id"], 1001, [501], 1002, [601], 501, 601)
    anon.set_user_language(1002, "uz")
    anon.record_activity_batch([1001, 1002])
    assert anon.claim_promo_seen(1002, "gift220")
    anon.bump_donation_action(1002)
    anon.record_star_invoice(1002, "guest_name", 50, "donation", "rotate-anon-1", status="paid")

    sticker = load(STICKER, "db")
    sticker.add_pack(2001, "rotate_pack_by_bot", "Cats", owner_username="cat_owner", owner_name="Cat Owner")
    assert sticker.add_invites("rotate_pack_by_bot", ["@friend_one"])[0] == ["@friend_one"]
    sticker.record_star_invoice(2001, "cat_owner", 10, "donation", "rotate-sticker-1")
    load(DOWNLOADER, "db").record_star_invoice(3001, "dl_user", 10, "donation", "rotate-dl-1")

    lifecycle = load(ANON, "lifecycle")
    lifecycle._write_state([("user", "1002", {"lang": "uz"}),
                            ("conversation", "anon|1002:1002", {"step": 2})])
    lifecycle.hold_for_update(1002, 1002, bot_id="rotate-anon")
    os.environ.pop("BACKUP_PUBLIC_KEY", None)
    assert load(STICKER, "reserve").note_erasure(2999)
    return {"token": token, "conversation": conversation["id"], "first": first["id"]}


def _quiet():
    with connect() as conn:
        conn.execute("DELETE FROM family.heartbeats")
        conn.commit()


def _rotate(old_file, new_file, dry_run=False):
    rotate = ROTATE
    lines = []
    old = rotate.keyring("rotate_vault_old", old_file)
    new = rotate.keyring("rotate_vault_new", new_file)
    _quiet()
    with psycopg.connect(DATABASE_URL) as conn:
        rotate.run(conn, old, new, ENCRYPT, say=lines.append)
        conn.rollback() if dry_run else conn.commit()
    return old, new, "\n".join(lines)


def _reads(world, vault_file):
    """The bots' own reads, under the key in `vault_file` (already handed to every bot)."""
    anon = load(ANON, "db")
    owner, paused, blocked = anon.get_link_view(world["token"], 1002)
    assert (owner, paused, blocked) == (1001, False, False)
    assert anon.get_link_view(world["token"], 1003)[2] is True, "the block followed both people"
    active = anon.get_active_conversation_for_follower(1002)
    assert active["id"] == world["conversation"] and active["owner_user_id"] == 1001
    relay = anon.get_relay_row(1001, 501)
    assert relay["conversation_id"] == world["conversation"] and relay["peer_message_id"] == 601
    assert {1001, 1002} <= set(anon.list_all_users())
    assert anon.get_user_language(1002) == "uz"
    assert anon.claim_promo_seen(1002, "gift220") is False, "the announcement stays seen"
    assert anon.bump_donation_action(1002)[3] is True, "the payment still names its payer"

    sticker = load(STICKER, "db")
    assert "cat_owner" in repr(sticker.get_pack_creator_info("rotate_pack_by_bot"))
    assert [name for _, name in sticker.pack_invites("rotate_pack_by_bot")] == ["friend_one"]
    sticker_vault = load(STICKER, "vault")
    with connect() as conn:
        sealed = conn.execute("SELECT username_sealed FROM sticker_bot.star_transactions "
                              "WHERE payload = 'rotate-sticker-1'").fetchone()[0]
        note = conn.execute("SELECT sealed_user FROM family.erasures ORDER BY id DESC LIMIT 1").fetchone()[0]
    assert sticker_vault.open_text(bytes(sealed), "stars.username") == "cat_owner"
    assert sticker_vault.open_id(bytes(note), "reserve.erasure") == 2999
    state = dict(load(ANON, "lifecycle")._read_state("user"))
    assert state.get("1002") == {"lang": "uz"}


def test_rotation_moves_everything_and_back(world, tmp_path):
    original = str(conftest._key_file)   # the test key (DATA_KEY_FILE may point elsewhere by now)
    new_file = tmp_path / "new.key"
    rotate, encrypt = ROTATE, ENCRYPT
    assert rotate._new_key_file(new_file, rotate.keyring("rotate_vault_tmp", original))
    assert oct(new_file.stat().st_mode & 0o777) == "0o600"
    try:
        # A dry run changes nothing.
        _, _, said = _rotate(original, new_file, dry_run=True)
        assert "every check passed" in said
        encrypt.hand_over_key(original, {})
        _reads(world, original)

        old, new, said = _rotate(original, new_file)
        assert "1002" not in said and "cat_owner" not in said, "counts only, never a value"
        _reads(world, new_file)
        with connect() as conn:
            assert conn.execute("SELECT value FROM family.settings WHERE key = 'data_key:v1'").fetchone()[0] \
                == new.fingerprint()
            sealed = conn.execute("SELECT user_sealed FROM anon_bot.user_settings LIMIT 1").fetchone()[0]
            invite = conn.execute("SELECT username_mark FROM sticker_bot.pack_invites "
                                  "WHERE pack_name = 'rotate_pack_by_bot'").fetchone()[0]
        with pytest.raises(old.VaultError):
            old.unseal(bytes(sealed), "anon.user")
        assert invite == new.text_mark("friend_one", "sticker.invite")
        # Someone invited under the old key can still claim the invitation.
        assert [pack for pack, _ in load(STICKER, "db").claim_invites(7777, "friend_one")] == ["rotate_pack_by_bot"]

        # A second run with the same keys is refused: the database is not the old key's any more.
        with pytest.raises(rotate.Refusal, match="another key"):
            _rotate(original, new_file)
    finally:
        # Back to the test key, for the tests that follow -- if the rotation got that far.
        try:
            with connect() as conn:
                stamped = conn.execute("SELECT value FROM family.settings WHERE key = 'data_key:v1'").fetchone()[0]
            if stamped == rotate.keyring("rotate_vault_check", new_file).fingerprint():
                _rotate(new_file, original)
        finally:
            encrypt.hand_over_key(original, {})
    with connect() as conn:
        assert conn.execute("SELECT value FROM family.settings WHERE key = 'data_key:v1'").fetchone()[0] \
            == load(STICKER, "vault").fingerprint()


def test_rotation_refuses_while_bots_are_alive_and_the_same_key(world, tmp_path):
    rotate = ROTATE
    original = str(conftest._key_file)   # the test key (DATA_KEY_FILE may point elsewhere by now)
    with pytest.raises(rotate.Refusal, match="old key"):
        _rotate(original, original)
    with connect() as conn:
        conn.execute("INSERT INTO family.heartbeats (bot_id, started_at, last_seen) VALUES ('rotate-test', now(), now()) "
                     "ON CONFLICT (bot_id) DO UPDATE SET last_seen = now()")
        conn.commit()
    other = tmp_path / "other.key"
    other.write_text(base64.b64encode(os.urandom(32)).decode() + "\n")
    old = rotate.keyring("rotate_vault_old", original)
    new = rotate.keyring("rotate_vault_new", other)
    with psycopg.connect(DATABASE_URL) as conn, pytest.raises(rotate.Refusal, match="alive"):
        rotate.run(conn, old, new, ENCRYPT, say=lambda *_: None)
    _quiet()


def test_the_address_is_never_an_argument():
    assert ROTATE.main(["--database-url-env", "postgresql://x", "--key-file", "a",
                                            "--new-key-file", "b"]) == 2


def test_the_script_never_needs_more_than_its_own_folder():
    # Loaded by path from scripts/, like the others: no package of its own to import.
    assert Path(ROTATE.VAULT_PATH).exists()
