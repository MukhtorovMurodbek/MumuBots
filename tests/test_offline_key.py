"""The owner's offline key (offline_key.py): age v1 files the bots can write and never read.
Checked against rage, an independent implementation of age, when it is installed."""
import os
import shutil
import subprocess

import pytest

from conftest import load

RAGE = shutil.which("rage")
needs_rage = pytest.mark.skipif(RAGE is None, reason="rage (or age) is not installed")


@pytest.fixture()
def ok():
    return load("manager_bot", "offline_key")


@pytest.mark.parametrize("size", [0, 1, 1000, 64 * 1024 - 1, 64 * 1024, 64 * 1024 + 1, 3 * 64 * 1024, 200_000])
def test_round_trip(ok, size):
    identity, public = ok.new_identity()
    data = os.urandom(size)
    blob = ok.encrypt(data, public)
    assert ok.is_encrypted(blob)
    assert ok.decrypt(blob, identity) == data


def test_keys_look_like_ages_and_belong_together(ok):
    identity, public = ok.new_identity()
    assert identity.startswith("AGE-SECRET-KEY-1") and len(identity) == 74
    assert public.startswith("age1") and len(public) == 62
    assert ok.recipient_of(identity) == public
    # A key file from age-keygen has comments around the key line.
    assert ok.recipient_of(f"# created: today\n# public key: {public}\n{identity}\n") == public


def test_wrong_key_altered_file_and_typos_are_refused(ok):
    identity, public = ok.new_identity()
    other, _ = ok.new_identity()
    blob = ok.encrypt(b"hello", public)
    with pytest.raises(ok.OfflineKeyError):
        ok.decrypt(blob, other)
    for position in (len(blob) - 1, len(blob) - 20, blob.index(b"---") + 6):
        broken = bytearray(blob)
        broken[position] ^= 1
        with pytest.raises(ok.OfflineKeyError):
            ok.decrypt(bytes(broken), identity)
    with pytest.raises(ok.OfflineKeyError):
        ok.decrypt(blob[:-1], identity)
    typo = public[:10] + ("q" if public[10] != "q" else "p") + public[11:]
    with pytest.raises(ok.OfflineKeyError):
        ok.parse_recipient(typo)


def test_the_setting_refuses_a_private_key(ok, monkeypatch):
    identity, public = ok.new_identity()
    monkeypatch.setenv(ok.RECIPIENT_VARIABLE, public)
    assert ok.recipient() == public
    monkeypatch.setenv(ok.RECIPIENT_VARIABLE, identity)
    with pytest.raises(ok.OfflineKeyError, match="PRIVATE"):
        ok.recipient()
    monkeypatch.delenv(ok.RECIPIENT_VARIABLE)
    assert ok.recipient() is None
    with pytest.raises(ok.OfflineKeyError):
        ok.encrypt(b"x")


@needs_rage
@pytest.mark.parametrize("size", [0, 5, 64 * 1024, 150_000])
def test_rage_opens_what_we_write(ok, tmp_path, size):
    identity, public = ok.new_identity()
    key_file = tmp_path / "key.txt"
    key_file.write_text(identity + "\n")
    data = os.urandom(size)
    (tmp_path / "copy.age").write_bytes(ok.encrypt(data, public))
    out = subprocess.run([RAGE, "-d", "-i", str(key_file), str(tmp_path / "copy.age")],
                         capture_output=True, check=True).stdout
    assert out == data


@needs_rage
@pytest.mark.parametrize("size", [0, 5, 64 * 1024, 150_000])
def test_we_open_what_rage_writes(ok, tmp_path, size):
    keygen = shutil.which("rage-keygen") or shutil.which("age-keygen")
    key_text = subprocess.run([keygen], capture_output=True, text=True, check=True).stdout
    public = ok.recipient_of(key_text)
    assert public in key_text, "our public key for rage's private key is the one rage printed"
    data = os.urandom(size)
    (tmp_path / "plain").write_bytes(data)
    blob = subprocess.run([RAGE, "-e", "-r", public, str(tmp_path / "plain")],
                          capture_output=True, check=True).stdout
    assert ok.decrypt(blob, key_text) == data
