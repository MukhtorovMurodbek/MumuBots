#!/usr/bin/env python3
"""Make the owner's offline key: the key pair the backups are encrypted to.

    python scripts/backup_key.py                  # a new pair, printed
    python scripts/backup_key.py --public-of FILE # the public key of a private key you have

The PRIVATE key (AGE-SECRET-KEY-1...) goes into your password manager, and nowhere else:
not Railway, not GitHub, not a chat. It opens the reserve copies, the nightly copies in your
Telegram and the erasure notes, on the day of a restore (scripts/restore_reserve.py
--backup-key-file). Lose it and those copies cannot be opened by anybody, you included.

The PUBLIC key (age1...) is not a secret: set it as BACKUP_PUBLIC_KEY on every Railway
service (MumuCombined and MumuManager). docs/data-protection.md has the whole picture.

The keys are age's own (https://age-encryption.org), so `age-keygen` or `rage-keygen` make
the same thing, and `age -d -i key.txt copy.zip.age > copy.zip` opens a copy without this
code.
"""
from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _offline_key():
    # Self-contained (the standard library and `cryptography`), so it loads by path,
    # without the bots' loader.
    spec = importlib.util.spec_from_file_location("offline_key", ROOT / "bots" / "shared" / "offline_key.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    parser.add_argument("--public-of", type=Path, metavar="FILE",
                        help="print the public key of the private key in FILE")
    args = parser.parse_args(argv)
    offline_key = _offline_key()
    if args.public_of:
        try:
            print(offline_key.recipient_of(args.public_of.read_text(encoding="ascii")))
        except (OSError, offline_key.OfflineKeyError) as exc:
            print(f"Could not read a private key from {args.public_of}: {exc}", file=sys.stderr)
            return 1
        return 0
    identity, public = offline_key.new_identity()
    print("Your offline key, new. Do these two things now:\n")
    print("1. Save this PRIVATE key in your password manager (and only there):\n")
    print(f"   {identity}\n")
    print("2. Set this PUBLIC key as BACKUP_PUBLIC_KEY on every Railway service:\n")
    print(f"   {public}\n")
    print("Then clear this terminal (the private key is on the screen).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
