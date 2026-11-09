"""Encryption to a key the bots never hold: the owner's offline key.

The data key (vault.py) has to be on the server, because the bots read what it seals all day.
Some things the bots only ever write and never read back: the reserve copies of the database,
the nightly copy in the owner's Telegram, and the notes /deletemydata leaves for a restore. Those
are encrypted to the owner's *public* key instead (BACKUP_PUBLIC_KEY). The matching private key
stays offline -- in the owner's password manager -- and is needed only on the day of a restore,
on the owner's own computer (scripts/restore_reserve.py). So nothing on the server can open them:
not a leaked Railway volume (which also holds the data key), not leaked variables, not a
forwarded Telegram file.

Why it matters (the owner, 2026-11-09, after Railway's breach of 2026-10-21 decrypted customers'
variables): before this, the volume held both the data key and the `latest` copy sealed with that
same key, so the volume alone gave away the whole database.

The format is age v1 with X25519 recipients (https://age-encryption.org/v1), so a copy can be
opened years from now without this code: `age -d -i key.txt copy.age` or `rage -d -i ...`. Keys
are age's own: the public key is the `age1...` line, the private key the `AGE-SECRET-KEY-1...`
line, as `age-keygen` (or scripts/backup_key.py) prints them. tests/test_offline_key.py checks the
files against rage, an independent implementation, both ways.

Only `cryptography` is used: X25519, HKDF-SHA-256, HMAC-SHA-256, ChaCha20-Poly1305.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os

RECIPIENT_VARIABLE = "BACKUP_PUBLIC_KEY"

MAGIC = b"age-encryption.org/v1\n"
_STANZA_X25519 = b"X25519"
_X25519_INFO = b"age-encryption.org/v1/X25519"
_CHUNK = 64 * 1024
_TAG = 16


class OfflineKeyError(RuntimeError):
    """A key that does not parse, or a file that does not open with it."""


# ---------------------------------------------------------------------------
# Bech32 (BIP-173), which is how age writes its keys
# ---------------------------------------------------------------------------
_CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
_GENERATOR = (0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3)


def _polymod(values) -> int:
    check = 1
    for value in values:
        top = check >> 25
        check = (check & 0x1FFFFFF) << 5 ^ value
        for i, generator in enumerate(_GENERATOR):
            if (top >> i) & 1:
                check ^= generator
    return check


def _hrp_expand(hrp: str) -> list:
    return [ord(c) >> 5 for c in hrp] + [0] + [ord(c) & 31 for c in hrp]


def _convert_bits(data, from_bits: int, to_bits: int, pad: bool) -> list:
    acc = bits = 0
    out = []
    maximum = (1 << to_bits) - 1
    for value in data:
        if value < 0 or value >> from_bits:
            raise OfflineKeyError("not a key")
        acc = (acc << from_bits) | value
        bits += from_bits
        while bits >= to_bits:
            bits -= to_bits
            out.append((acc >> bits) & maximum)
    if pad:
        if bits:
            out.append((acc << (to_bits - bits)) & maximum)
    elif bits >= from_bits or ((acc << (to_bits - bits)) & maximum):
        raise OfflineKeyError("not a key")
    return out


def _bech32_encode(hrp: str, data: bytes) -> str:
    words = _convert_bits(data, 8, 5, True)
    checksum_input = _hrp_expand(hrp) + words + [0] * 6
    polymod = _polymod(checksum_input) ^ 1
    checksum = [(polymod >> 5 * (5 - i)) & 31 for i in range(6)]
    return hrp + "1" + "".join(_CHARSET[w] for w in words + checksum)


def _bech32_decode(text: str, hrp: str) -> bytes:
    if text.lower() != text and text.upper() != text:
        raise OfflineKeyError("not a key: mixed case")
    text = text.lower()
    split = text.rfind("1")
    if split < 1 or text[:split] != hrp or len(text) - split - 1 < 6:
        raise OfflineKeyError("not a key of this kind")
    try:
        words = [_CHARSET.index(c) for c in text[split + 1:]]
    except ValueError:
        raise OfflineKeyError("not a key: bad character") from None
    if _polymod(_hrp_expand(hrp) + words) != 1:
        raise OfflineKeyError("not a key: the checksum does not match (a typo?)")
    return bytes(_convert_bits(words[:-6], 5, 8, False))


# ---------------------------------------------------------------------------
# Keys
# ---------------------------------------------------------------------------
_PUBLIC_HRP = "age"
_SECRET_HRP = "age-secret-key-"


def parse_recipient(text: str) -> bytes:
    """The 32-byte X25519 public key in an `age1...` line."""
    raw = _bech32_decode(text.strip(), _PUBLIC_HRP)
    if len(raw) != 32:
        raise OfflineKeyError("not an age public key")
    return raw


def parse_identity(text: str) -> bytes:
    """The 32-byte X25519 private key in an `AGE-SECRET-KEY-1...` line. A key file from
    age-keygen has comment lines starting with '#'; the key line is found among them."""
    lines = [line.strip() for line in text.splitlines() if line.strip() and not line.strip().startswith("#")]
    if len(lines) != 1:
        raise OfflineKeyError("expected one AGE-SECRET-KEY-1... line")
    raw = _bech32_decode(lines[0], _SECRET_HRP)
    if len(raw) != 32:
        raise OfflineKeyError("not an age private key")
    return raw


def recipient_of(identity: str) -> str:
    """The `age1...` public key that belongs to this private key."""
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    secret = X25519PrivateKey.from_private_bytes(parse_identity(identity))
    return _bech32_encode(_PUBLIC_HRP, secret.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw))


def new_identity() -> "tuple[str, str]":
    """A fresh key pair: (the private key line, its public key line)."""
    identity = _bech32_encode(_SECRET_HRP, os.urandom(32)).upper()
    return identity, recipient_of(identity)


def recipient() -> "str | None":
    """BACKUP_PUBLIC_KEY, checked, or None when it is not set. A value that is set but is not a
    public key raises: it must not quietly fall back to something weaker."""
    value = (os.environ.get(RECIPIENT_VARIABLE) or "").strip()
    if not value:
        return None
    try:
        parse_recipient(value)
    except OfflineKeyError as exc:
        if value.upper().startswith("AGE-SECRET-KEY-"):
            raise OfflineKeyError(f"{RECIPIENT_VARIABLE} holds a PRIVATE key. Put only the age1... public "
                                  f"key there, and replace this key pair: the private one has been seen.") from None
        raise OfflineKeyError(f"{RECIPIENT_VARIABLE} is not an age public key (age1...): {exc}") from None
    return value


def fingerprint(recipient_text: str) -> str:
    """12 hex digits that name a public key, for logs and file headers."""
    return hashlib.sha256(parse_recipient(recipient_text)).hexdigest()[:12]


# ---------------------------------------------------------------------------
# The age v1 format
# ---------------------------------------------------------------------------

def _b64(data: bytes) -> bytes:
    return base64.b64encode(data).rstrip(b"=")


def _unb64(text: bytes) -> bytes:
    if b"=" in text or b"\r" in text:
        raise OfflineKeyError("not an age file: bad base64")
    try:
        data = base64.b64decode(text + b"=" * (-len(text) % 4), validate=True)
    except ValueError:
        raise OfflineKeyError("not an age file: bad base64") from None
    if _b64(data) != text:
        raise OfflineKeyError("not an age file: non-canonical base64")
    return data


def _hkdf(ikm: bytes, salt: bytes, info: bytes) -> bytes:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=salt, info=info).derive(ikm)


def _header_mac(file_key: bytes, header: bytes) -> bytes:
    return hmac.new(_hkdf(file_key, b"", b"header"), header, hashlib.sha256).digest()


def _chunk_nonce(counter: int, last: bool) -> bytes:
    return counter.to_bytes(11, "big") + (b"\x01" if last else b"\x00")


def is_encrypted(blob) -> bool:
    return bytes(blob[:len(MAGIC)]) == MAGIC


def encrypt(data: bytes, recipient_text: "str | None" = None) -> bytes:
    """`data` encrypted to the owner's public key (BACKUP_PUBLIC_KEY unless one is given)."""
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
    from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    recipient_text = recipient_text or recipient()
    if not recipient_text:
        raise OfflineKeyError(f"{RECIPIENT_VARIABLE} is not set")
    their = parse_recipient(recipient_text)
    file_key = os.urandom(16)

    ephemeral = X25519PrivateKey.generate()
    share = ephemeral.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    shared = ephemeral.exchange(X25519PublicKey.from_public_bytes(their))
    if shared == bytes(32):
        raise OfflineKeyError("that public key cannot be used")
    wrap_key = _hkdf(shared, share + their, _X25519_INFO)
    body = ChaCha20Poly1305(wrap_key).encrypt(bytes(12), file_key, None)

    header = MAGIC + b"-> X25519 " + _b64(share) + b"\n" + _b64(body) + b"\n---"
    header += b" " + _b64(_header_mac(file_key, header)) + b"\n"

    nonce = os.urandom(16)
    stream = ChaCha20Poly1305(_hkdf(file_key, nonce, b"payload"))
    data = bytes(data)
    chunks = [data[i:i + _CHUNK] for i in range(0, len(data), _CHUNK)] or [b""]
    out = [header, nonce]
    for counter, chunk in enumerate(chunks):
        out.append(stream.encrypt(_chunk_nonce(counter, counter == len(chunks) - 1), chunk, None))
    return b"".join(out)


def _read_header(blob: bytes) -> "tuple[list, bytes, bytes, bytes]":
    """(stanzas, the header bytes the MAC covers, the MAC, the payload)."""
    if not is_encrypted(blob):
        raise OfflineKeyError("not an age file")
    at = len(MAGIC)
    stanzas = []
    while True:
        end = blob.find(b"\n", at)
        if end < 0:
            raise OfflineKeyError("not an age file: the header does not end")
        line = blob[at:end]
        if line.startswith(b"--- "):
            covered = blob[:at + 3]
            mac = _unb64(line[4:])
            return stanzas, covered, mac, blob[end + 1:]
        if not line.startswith(b"-> "):
            raise OfflineKeyError("not an age file: bad stanza")
        args = line[3:].split(b" ")
        at = end + 1
        body = b""
        while True:
            end = blob.find(b"\n", at)
            if end < 0:
                raise OfflineKeyError("not an age file: a stanza does not end")
            piece = blob[at:end]
            at = end + 1
            body += piece
            if len(piece) < 64:
                break
        stanzas.append((args, _unb64(body)))


def decrypt(blob: bytes, identity: str) -> bytes:
    """The contents of an age file encrypted to this private key."""
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
    from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    blob = bytes(blob)
    secret = X25519PrivateKey.from_private_bytes(parse_identity(identity))
    ours = secret.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    stanzas, covered, mac, payload = _read_header(blob)
    file_key = None
    for args, body in stanzas:
        if len(args) != 2 or args[0] != _STANZA_X25519 or len(body) != 32:
            continue
        share = _unb64(args[1])
        if len(share) != 32:
            continue
        shared = secret.exchange(X25519PublicKey.from_public_bytes(share))
        try:
            file_key = ChaCha20Poly1305(_hkdf(shared, share + ours, _X25519_INFO)).decrypt(bytes(12), body, None)
            break
        except InvalidTag:
            continue
    if file_key is None or len(file_key) != 16:
        raise OfflineKeyError("this file was not encrypted to that key")
    if not hmac.compare_digest(_header_mac(file_key, covered), mac):
        raise OfflineKeyError("the file's header was altered")
    if len(payload) < 16 + _TAG:
        raise OfflineKeyError("the file is cut short")
    nonce, sealed = payload[:16], payload[16:]
    stream = ChaCha20Poly1305(_hkdf(file_key, nonce, b"payload"))
    out, counter, size = [], 0, _CHUNK + _TAG
    for start in range(0, len(sealed), size):
        piece = sealed[start:start + size]
        last = start + size >= len(sealed)
        try:
            chunk = stream.decrypt(_chunk_nonce(counter, last), piece, None)
        except InvalidTag:
            raise OfflineKeyError("the file was altered or cut short") from None
        if last and not chunk and counter > 0:
            raise OfflineKeyError("the file ends with an empty chunk")
        out.append(chunk)
        counter += 1
    return b"".join(out)
