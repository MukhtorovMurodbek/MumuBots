#!/usr/bin/env python3
"""Make the operator's offline key: the key pair the backups are encrypted to."""
from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _offline_key():
    spec = importlib.util.spec_from_file_location("offline_key", ROOT / "shared" / "security" / "offline_key.py")
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
    print("A new offline key. Do these two things now:\n")
    print("1. Save this PRIVATE key in a password manager (and only there):\n")
    print(f"   {identity}\n")
    print("2. Set this PUBLIC key as BACKUP_PUBLIC_KEY on every Railway service:\n")
    print(f"   {public}\n")
    print("Then clear this terminal (the private key is on the screen).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
