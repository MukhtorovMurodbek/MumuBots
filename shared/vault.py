"""Personal data the database cannot give away on its own.

The owner, 2026-10-22, the day Railway leaked every customer's environment
variables: "encrypt those infos in database so even in case of any leakage no
one should be able to retrieve those data ... Lets take it seriously."

Two tools, both keyed from one 32-byte secret that lives in a file of its own
(DATA_KEY_FILE) -- never in the database, and never in the environment, since
what leaked that day was the environment.

PSEUDONYMS. `pseudonym(telegram_id)` is a keyed hash (HMAC-SHA256) folded into
a BIGINT below -2**62. A table that has to *find* a person -- by equality, in a
join, a unique key, COUNT(DISTINCT) -- stores this instead of the id, so every
query keeps its shape and its index. Without the key nobody can compute one
from an id, or an id from one. A private chat's id is its person's id, so the
two get the same pseudonym and still match. No Telegram id falls in that range
(people are positive, chats no lower than about -10**13), so a stored value
says by itself whether it is one: converting a table twice changes nothing,
and `pseudonym()` refuses one that already is.

SEALS. `seal(data, purpose)` is AES-256-GCM with a fresh 96-bit nonce, for the
few values a bot must read back -- the chat an anonymous message goes to, a
name the owner is shown. The purpose is bound in as associated data, so a
sealed owner cannot be opened as a sealed follower. Where the row also keeps
the pseudonym, `open_id(..., expected=)` checks that the two agree, so a
ciphertext moved onto another row is refused rather than obeyed.

What it does not protect against: whoever holds both the database and the key.
The bots need the key to run, so this covers a leaked DATABASE_URL, a stolen
backup, a breach of the database host -- not the machine the bots run on.
Losing the key loses every sealed value and every way to find a person by id.
Keep a copy offline.

A HOST WITHOUT SECRET FILES (Railway) gets the key once, through the variable
DATA_KEY_INIT: the first start writes it to DATA_KEY_FILE, which points at the
host's volume, and from then on the variable is deleted, so the host's
variables hold the key for one deploy and no longer. A key file that is there
already is never written over: DATA_KEY_INIT must then be the same key, or the
bot does not start.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
from pathlib import Path

KEY_FILE = os.environ.get("DATA_KEY_FILE", "/run/secrets/mumu_data_key")
INIT_VARIABLE = "DATA_KEY_INIT"

# What the last load did with DATA_KEY_INIT, for the start-up log; None when it
# is not set. It names the variable and the file, never the key.
init_note: "str | None" = None

# The first byte of every seal. A new key or a new scheme gets a new number,
# so values sealed under the old one can still be told apart.
SEAL_VERSION = 1

PSEUDONYM_CEILING = -(2 ** 62)

_NONCE = 12
_TAG = 16

_keys: "tuple[bytes, bytes, str] | None" = None


class VaultError(RuntimeError):
    """The key is missing or unusable, or a sealed value does not open."""


def _hkdf(master: bytes, label: bytes) -> bytes:
    """RFC 5869 with a zero salt and one output block: two unrelated keys out of one."""
    prk = hmac.new(b"\0" * 32, master, hashlib.sha256).digest()
    return hmac.new(prk, label + b"\x01", hashlib.sha256).digest()


def _parse_key(text: str, where: str) -> bytes:
    text = text.strip()
    try:
        raw = bytes.fromhex(text) if len(text) == 64 else base64.b64decode(text, validate=True)
    except (ValueError, binascii.Error):
        raise VaultError(f"{where} does not hold a key: 32 bytes, as base64 or 64 hex digits") from None
    if len(raw) != 32:
        raise VaultError(f"{where} holds {len(raw)} bytes; a data key is 32")
    return raw


def _read_key(path: Path) -> bytes:
    try:
        text = path.read_text(encoding="ascii")
    except (OSError, UnicodeDecodeError) as exc:
        raise VaultError(
            f"No data key at {path} ({exc}). Point DATA_KEY_FILE at the key file -- see vault.py."
        ) from None
    return _parse_key(text, str(path))


def _write_new_key_file(path: Path, raw: bytes) -> bool:
    """The key as a base64 line, readable by its owner only. Creates the file and
    never opens one that exists (O_EXCL): False when another process got there first."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w", encoding="ascii") as handle:
        handle.write(base64.b64encode(raw).decode("ascii") + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    return True


def _load() -> "tuple[bytes, bytes, str]":
    global _keys, init_note
    if _keys is None:
        path = Path(KEY_FILE)
        given = os.environ.get(INIT_VARIABLE, "").strip()
        note = None
        if given:
            init_key = _parse_key(given, INIT_VARIABLE)
            if not path.exists():
                try:
                    if _write_new_key_file(path, init_key):
                        note = (f"The data key was written to {path} from {INIT_VARIABLE}. "
                                f"Delete {INIT_VARIABLE} from the host's variables now.")
                except OSError as exc:
                    raise VaultError(f"{INIT_VARIABLE} is set, but the key could not be written to {path} "
                                     f"({type(exc).__name__}). Point DATA_KEY_FILE at the host's volume.") from None
        master = _read_key(path)
        if given:
            if not hmac.compare_digest(init_key, master):
                raise VaultError(f"{INIT_VARIABLE} is not the key in {path}, and a key file is never written "
                                 f"over. Delete {INIT_VARIABLE}, or the file if it holds the wrong key.")
            note = note or (f"{INIT_VARIABLE} is still set, and the key is already in {path}. "
                            f"Delete {INIT_VARIABLE} from the host's variables.")
        init_note = note
        _keys = (
            _hkdf(master, b"mumubots pseudonym v1"),
            _hkdf(master, b"mumubots seal v1"),
            _hkdf(master, b"mumubots fingerprint v1")[:8].hex(),
        )
    return _keys


def require() -> str:
    """Load the key and the cipher now, so a bot fails at start rather than at its first message; returns the key's fingerprint."""
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: F401
    except ImportError:
        raise VaultError("The `cryptography` package is not installed; personal data cannot be read") from None
    return _load()[2]


def fingerprint() -> str:
    """16 hex digits that name the key without revealing it: stored beside the data it sealed, so a wrong key file is caught at start."""
    return _load()[2]


def new_key() -> str:
    """A fresh key, as the base64 line a key file holds."""
    return base64.b64encode(os.urandom(32)).decode("ascii")


def is_pseudonym(value) -> bool:
    return value is not None and int(value) < PSEUDONYM_CEILING


def pseudonym(telegram_id: int) -> int:
    """The stored stand-in for a person's or a chat's id."""
    value = int(telegram_id)
    if value < PSEUDONYM_CEILING:
        raise ValueError("pseudonym() was given a pseudonym")
    mac = hmac.new(_load()[0], b"tg:%d" % value, hashlib.sha256).digest()
    return -(2 ** 63) + (int.from_bytes(mac[:8], "big") >> 2)


def pseudonym_in(telegram_id: int, domain: str) -> int:
    """A stand-in that can't be matched to pseudonym() of the same id, nor to another domain's: for a table
    whose rows can be lined up with a record that holds the real id (a payment and the family ledger), so that
    the match reveals only that domain's stand-in. Same range as pseudonym(), so is_pseudonym() holds."""
    value = int(telegram_id)
    if value < PSEUDONYM_CEILING:
        raise ValueError("pseudonym_in() was given a pseudonym")
    if not domain or not domain.isascii() or not all(c.isalnum() or c in "._-" for c in domain) or not domain[0].isalpha():
        raise ValueError("pseudonym_in() needs a domain of letters, digits, '.', '_' or '-', starting with a letter")
    mac = hmac.new(_load()[0], b"tg:%s:%d" % (domain.encode("ascii"), value), hashlib.sha256).digest()
    return -(2 ** 63) + (int.from_bytes(mac[:8], "big") >> 2)


def _aad(purpose: str) -> bytes:
    return b"mumubots v1 " + purpose.encode("utf-8")


def seal(data: bytes, purpose: str) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    nonce = os.urandom(_NONCE)
    return bytes([SEAL_VERSION]) + nonce + AESGCM(_load()[1]).encrypt(nonce, bytes(data), _aad(purpose))


def unseal(blob, purpose: str) -> bytes:
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    blob = bytes(blob)
    if len(blob) < 1 + _NONCE + _TAG or blob[0] != SEAL_VERSION:
        raise VaultError(f"Not a sealed value ({purpose})")
    try:
        return AESGCM(_load()[1]).decrypt(blob[1:1 + _NONCE], blob[1 + _NONCE:], _aad(purpose))
    except InvalidTag:
        raise VaultError(f"A sealed value did not open ({purpose}): another key, or altered") from None


def seal_id(telegram_id: int, purpose: str) -> bytes:
    return seal(b"%d" % int(telegram_id), purpose)


def open_id(blob, purpose: str, *, expected: "int | None" = None) -> int:
    """A sealed id back. With `expected` (the row's pseudonym), refuse one that belongs to another row."""
    value = int(unseal(blob, purpose).decode("ascii"))
    if expected is not None and pseudonym(value) != int(expected):
        raise VaultError(f"A sealed id does not belong to its row ({purpose})")
    return value


def seal_text(text: "str | None", purpose: str) -> "bytes | None":
    return None if text is None else seal(text.encode("utf-8"), purpose)


def open_text(blob, purpose: str) -> "str | None":
    return None if blob is None else unseal(blob, purpose).decode("utf-8")


def seal_json(value, purpose: str) -> bytes:
    return seal(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"), purpose)


def open_json(blob, purpose: str):
    return json.loads(unseal(blob, purpose).decode("utf-8"))
