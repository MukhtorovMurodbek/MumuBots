"""shared/vault.py: pseudonyms and seals for personal data, against made-up keys.

Checked here:
  - a key file is 32 bytes as base64 or hex, and anything else is refused by name;
  - a pseudonym is pinned to its formula -- change it and every stored one is orphaned;
  - every pseudonym is below -2**62, where no Telegram id is, and none repeats;
  - a pseudonym is never taken for an id;
  - a seal opens to what went in, only under its own key and purpose, and not
    after a single byte of it changes;
  - a sealed id is refused on a row whose pseudonym is someone else's;
  - DATA_KEY_INIT writes a missing key file once, never writes over one, and
    is refused when it is not the key the file holds.

Needs the `cryptography` package (it is in every bot's requirements.txt).
"""
import base64
import hashlib
import hmac
import os
import pathlib
import random
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "shared"))
import vault  # noqa: E402

failures = []


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + (f"  -- {detail}" if detail and not ok else ""))
    if not ok:
        failures.append(name)


def raises(fn, kind=vault.VaultError):
    try:
        fn()
    except kind as exc:
        return str(exc) or True
    except Exception as exc:  # the wrong kind of failure is a failure
        return False if kind is not Exception else str(exc)
    return False


TEST_KEY = bytes(range(32))
folder = pathlib.Path(tempfile.mkdtemp())


def use_key(text: str, name: str = "key") -> None:
    path = folder / name
    path.write_text(text, encoding="ascii")
    vault.KEY_FILE = str(path)
    vault._keys = None


# --- the key file ---------------------------------------------------------
vault.KEY_FILE = str(folder / "absent")
vault._keys = None
said = raises(lambda: vault.pseudonym(1))
check("a missing key file is refused, naming DATA_KEY_FILE", bool(said) and "DATA_KEY_FILE" in str(said), said)

use_key("c2hvcnQ=")
check("a key of the wrong length is refused", bool(raises(lambda: vault.pseudonym(1))))
use_key("not a key at all!")
check("a file that is not base64 or hex is refused", bool(raises(lambda: vault.pseudonym(1))))

use_key(TEST_KEY.hex())
from_hex = (vault.fingerprint(), vault.pseudonym(42))
use_key(base64.b64encode(TEST_KEY).decode() + "\n")
from_b64 = (vault.fingerprint(), vault.pseudonym(42))
check("the same key as hex or as base64 is the same key", from_hex == from_b64)
check("the fingerprint is pinned", vault.require() == "fcc37c41b6fbf065", vault.fingerprint())

# --- pseudonyms -------------------------------------------------------------
check("a person's pseudonym is pinned", vault.pseudonym(123456789) == -8691855235571614995,
      vault.pseudonym(123456789))
check("a chat's pseudonym is pinned", vault.pseudonym(-1001234567890) == -6190425679070805001,
      vault.pseudonym(-1001234567890))
prk = hmac.new(b"\0" * 32, TEST_KEY, hashlib.sha256).digest()
kp = hmac.new(prk, b"mumubots pseudonym v1\x01", hashlib.sha256).digest()
mac = hmac.new(kp, b"tg:777", hashlib.sha256).digest()
check("the formula is HKDF, then HMAC-SHA256 folded below -2**62",
      vault.pseudonym(777) == -(2 ** 63) + (int.from_bytes(mac[:8], "big") >> 2))

rng = random.Random(7)
ids = {rng.randrange(1, 2 ** 52) for _ in range(20000)} | {1, 2 ** 52, -1, -1001234567890, -(10 ** 13)}
made = {i: vault.pseudonym(i) for i in ids}
check("every pseudonym is below -2**62", all(p < vault.PSEUDONYM_CEILING for p in made.values()))
check("every pseudonym fits a BIGINT", all(-(2 ** 63) <= p for p in made.values()))
check("no two ids share a pseudonym", len(set(made.values())) == len(made))
check("the same id gives the same pseudonym", all(vault.pseudonym(i) == p for i, p in list(made.items())[:200]))
check("no id is taken for a pseudonym", not any(vault.is_pseudonym(i) for i in ids))
check("every pseudonym is known as one", all(vault.is_pseudonym(p) for p in made.values()))
check("None is not a pseudonym", vault.is_pseudonym(None) is False)
check("a pseudonym of a pseudonym is refused",
      bool(raises(lambda: vault.pseudonym(made[1]), ValueError)))

use_key(base64.b64encode(os.urandom(32)).decode(), "other")
check("another key gives other pseudonyms", vault.pseudonym(123456789) != -8691855235571614995)
check("another key has another fingerprint", vault.fingerprint() != "fcc37c41b6fbf065")
other_seal = vault.seal(b"x", "test.purpose")
use_key(TEST_KEY.hex())

# --- seals ------------------------------------------------------------------
blob = vault.seal(b"hello", "test.purpose")
check("a seal is the version byte, a 12-byte nonce, the text and a 16-byte tag",
      blob[0] == 1 and len(blob) == 1 + 12 + 5 + 16)
check("a seal opens to what went in", vault.unseal(blob, "test.purpose") == b"hello")
check("a seal opens from a memoryview, as psycopg hands bytea back",
      vault.unseal(memoryview(blob), "test.purpose") == b"hello")
check("the same value sealed twice looks different", vault.seal(b"hello", "test.purpose") != blob)
check("a seal does not open for another purpose", bool(raises(lambda: vault.unseal(blob, "test.other"))))
check("a seal does not open under another key", bool(raises(lambda: vault.unseal(other_seal, "test.purpose"))))
altered = [bytes(blob[:i]) + bytes([blob[i] ^ 1]) + bytes(blob[i + 1:]) for i in range(len(blob))]
check("changing any one byte stops it opening", all(raises(lambda b=b: vault.unseal(b, "test.purpose")) for b in altered))
check("a truncated seal is refused", bool(raises(lambda: vault.unseal(blob[:20], "test.purpose"))))
check("an empty value is refused", bool(raises(lambda: vault.unseal(b"", "test.purpose"))))

from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: E402

ks = hmac.new(prk, b"mumubots seal v1\x01", hashlib.sha256).digest()
nonce = bytes(12)
known = b"\x01" + nonce + AESGCM(ks).encrypt(nonce, b"pinned", b"mumubots v1 test.purpose")
check("a seal made elsewhere by the same rules opens", vault.unseal(known, "test.purpose") == b"pinned")

check("text round-trips", vault.open_text(vault.seal_text("Ünïcödé @name", "t"), "t") == "Ünïcödé @name")
check("None text stays None", vault.seal_text(None, "t") is None and vault.open_text(None, "t") is None)
check("json round-trips", vault.open_json(vault.seal_json({"a": [1, "ж"]}, "j"), "j") == {"a": [1, "ж"]})

sealed = vault.seal_id(123456789, "test.owner")
check("an id round-trips", vault.open_id(sealed, "test.owner") == 123456789)
check("an id opens on its own row", vault.open_id(sealed, "test.owner", expected=vault.pseudonym(123456789)) == 123456789)
check("an id is refused on someone else's row",
      bool(raises(lambda: vault.open_id(sealed, "test.owner", expected=vault.pseudonym(987654321)))))
check("a negative chat id round-trips", vault.open_id(vault.seal_id(-1001234567890, "c"), "c") == -1001234567890)

payer = vault.pseudonym_in(123456789, "anon.payer")
check("a domain stand-in is a pseudonym", vault.is_pseudonym(payer))
check("a domain stand-in is stable", payer == vault.pseudonym_in(123456789, "anon.payer"))
check("a domain stand-in differs from pseudonym()", payer != vault.pseudonym(123456789))
check("each domain gets its own stand-in", payer != vault.pseudonym_in(123456789, "anon.payer2"))
check("different people differ in a domain", payer != vault.pseudonym_in(123456780, "anon.payer"))
kp = hmac.new(prk, b"mumubots pseudonym v1\x01", hashlib.sha256).digest()
pinned = -(2 ** 63) + (int.from_bytes(hmac.new(kp, b"tg:anon.payer:123456789", hashlib.sha256).digest()[:8], "big") >> 2)
check("a domain stand-in made elsewhere by the same rules matches", payer == pinned)
check("a domain stand-in refuses a pseudonym", bool(raises(lambda: vault.pseudonym_in(payer, "anon.payer"), ValueError)))
check("a domain stand-in refuses an empty or odd domain",
      all(raises(lambda d=d: vault.pseudonym_in(1, d), ValueError) for d in ("", "a:b", "1x", "a b", "é")))

# --- DATA_KEY_INIT: the key given once, written to the volume -----------------
use_key(base64.b64encode(TEST_KEY).decode())
known_fingerprint = vault.fingerprint()
check("with no DATA_KEY_INIT there is nothing to say at start", vault.init_note is None)
init_text = base64.b64encode(TEST_KEY).decode()
os.environ[vault.INIT_VARIABLE] = init_text


def init_at(path: pathlib.Path) -> None:
    vault.KEY_FILE = str(path)
    vault._keys = None
    vault.init_note = None


try:
    target = folder / "volume" / "keys" / "mumu_data_key"
    init_at(target)
    check("no key file and DATA_KEY_INIT set: the key loads", vault.fingerprint() == known_fingerprint)
    check("...from a key file it wrote, in a folder it made, holding that key",
          target.is_file() and vault._read_key(target) == TEST_KEY)
    check("...and the start-up note says so and asks to delete the variable, without the key",
          bool(vault.init_note) and "written to" in vault.init_note and "Delete DATA_KEY_INIT" in vault.init_note
          and init_text not in vault.init_note and TEST_KEY.hex() not in vault.init_note, vault.init_note or "")
    if os.name != "nt":
        check("...readable by its owner only", (target.stat().st_mode & 0o777) == 0o600, oct(target.stat().st_mode))

    init_at(target)
    check("the key file there and DATA_KEY_INIT the same key: it loads, and the note asks to delete the variable",
          vault.fingerprint() == known_fingerprint and "still set" in (vault.init_note or ""), vault.init_note or "")

    other = base64.b64encode(bytes(range(1, 33))).decode()
    before = target.read_bytes()
    os.environ[vault.INIT_VARIABLE] = other
    init_at(target)
    said = raises(lambda: vault.fingerprint())
    check("DATA_KEY_INIT another key than the file's: refused, naming the variable", bool(said)
          and "DATA_KEY_INIT" in str(said) and other not in str(said), str(said))
    check("...and the key file is not written over", target.read_bytes() == before)

    os.environ[vault.INIT_VARIABLE] = "not a key"
    fresh = folder / "volume2" / "mumu_data_key"
    init_at(fresh)
    said = raises(lambda: vault.fingerprint())
    check("DATA_KEY_INIT that is not a key: refused by the variable's name, and no file is made",
          bool(said) and "DATA_KEY_INIT" in str(said) and not fresh.exists(), str(said))
finally:
    os.environ.pop(vault.INIT_VARIABLE, None)
    use_key(base64.b64encode(TEST_KEY).decode())

if failures:
    print(f"FAILED ({len(failures)}):", *failures, sep="\n  ")
    sys.exit(1)
print("all passed")
