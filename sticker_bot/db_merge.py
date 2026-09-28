#!/usr/bin/env python3
"""Reconcile StickerBot's two databases (e.g. your laptop's local DB and its
cloud DB) into one, without ever deleting or overwriting a row that already
exists.

    python db_merge.py --from local --into cloud --dry-run   # preview first
    python db_merge.py --from local --into cloud             # actually do it

"local" and "cloud" are aliases read from LOCAL_DATABASE_URL and
CLOUD_DATABASE_URL (in your environment, or a .env file next to this
script) -- "local" falls back to this bot's own DATABASE_URL from db.py if
LOCAL_DATABASE_URL isn't set. You can also pass a full postgresql:// URL
directly instead of an alias.

POLICY -- read this before you run it for real
  - Additive only. A row that already exists in the target (matched by
    that table's natural key -- NEVER the internal numeric id, which is
    meaningless across two independently-run databases) is left completely
    untouched. Nothing is ever deleted, and no existing row's values are
    ever changed by this script.
  - Because of that, this can never destroy data. The worst case is that a
    handful of rows needed a human judgement call and got skipped and
    printed instead of guessed at automatically -- both values are printed
    so you can decide by hand; the script never guesses.
  - Always run with --dry-run first. It runs the exact same logic inside a
    transaction that gets rolled back instead of committed, and prints what
    it *would* do.
"""
from __future__ import annotations

import argparse
import os
import sys
from contextlib import closing
from pathlib import Path


import db

PLAIN_TABLES = [
    {"name": "star_transactions", "key": ("payload",),
     "cols": ["user_id", "username", "amount_stars", "item", "status", "payload",
              "charge_id", "currency", "created_at", "updated_at"]},
    {"name": "donation_prompts", "key": ("user_id",),
     "cols": ["user_id", "action_count", "last_shown_at"]},
    {"name": "packs", "key": ("name",),
     "cols": ["user_id", "name", "title", "created_at", "owner_username", "owner_name"]},
    {"name": "pack_editors", "key": ("pack_name", "user_id"),
     "cols": ["pack_name", "user_id", "added_at"]},
    {"name": "pack_share_tokens", "key": ("pack_name",),
     "cols": ["pack_name", "token"]},
    {"name": "user_settings", "key": ("user_id",),
     "cols": ["user_id", "language"]},
]
# activity_events (per-update telemetry backing /status's active-user counts)
# is deliberately NOT merged here -- it's high-volume, has no real natural
# key (occurred_at is a timestamp, not an identity), and has zero value
# once it's more than an hour or two old. Losing it on a redeploy is fine.


def load_dotenv_if_present() -> None:
    env_path = Path(__file__).resolve().parent / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def resolve_dsn(alias_or_url: str) -> str:
    if alias_or_url.startswith("postgres://") or alias_or_url.startswith("postgresql://"):
        return alias_or_url
    env_key = f"{alias_or_url.upper()}_DATABASE_URL"
    dsn = os.environ.get(env_key) or (db.DATABASE_URL if alias_or_url == "local" else None)
    if not dsn:
        sys.exit(f"Don't know a database called '{alias_or_url}'. Pass a full postgresql:// URL, "
                  f"or set {env_key} in your environment / .env file.")
    return dsn


def fetch_rows(conn, table: str, cols: list[str]) -> list[tuple]:
    cur = conn.execute(f"SELECT {', '.join(cols)} FROM {table}")
    return cur.fetchall()


def merge_plain_table(src_conn, dst_conn, spec: dict, report: list[str]) -> None:
    name, key_cols, cols = spec["name"], spec["key"], spec["cols"]
    key_idx = [cols.index(k) for k in key_cols]

    src_rows = fetch_rows(src_conn, name, cols)
    dst_rows = fetch_rows(dst_conn, name, cols)
    dst_by_key = {tuple(row[i] for i in key_idx): row for row in dst_rows}

    added, identical, conflicts = 0, 0, []
    placeholders = ", ".join(["%s"] * len(cols))
    for row in src_rows:
        key = tuple(row[i] for i in key_idx)
        existing = dst_by_key.get(key)
        if existing is None:
            dst_conn.execute(f"INSERT INTO {name} ({', '.join(cols)}) VALUES ({placeholders})", row)
            added += 1
        elif existing == row:
            identical += 1
        else:
            conflicts.append((key, existing, row))

    if added or conflicts:
        report.append(f"{name}: +{added} added, {identical} already identical, {len(conflicts)} conflicts (skipped)")
    else:
        report.append(f"{name}: nothing to add ({identical} already identical)")
    for key, existing, row in conflicts:
        report.append(
            f"    conflict on {name} {key_cols}={key}: "
            f"target has {dict(zip(cols, existing))}, source has {dict(zip(cols, row))} -- kept target's, review by hand"
        )


def run_merge(src_dsn: str, dst_dsn: str, dry_run: bool) -> list[str]:
    report: list[str] = []
    if not dry_run:
        # The family shares one database with a schema per bot, so the target
        # side may be a brand-new empty schema (a freshly deployed Railway
        # database, say). Idempotent, and skipped on --dry-run so a preview
        # still cannot write anything at all.
        db.init_db(dst_dsn)
    with closing(db.connect(src_dsn)) as src_conn, closing(db.connect(dst_dsn)) as dst_conn:
        for spec in PLAIN_TABLES:
            merge_plain_table(src_conn, dst_conn, spec, report)
        if dry_run:
            dst_conn.rollback()
        else:
            dst_conn.commit()
    return report


def main() -> None:
    load_dotenv_if_present()
    parser = argparse.ArgumentParser(description="Merge StickerBot's two databases together, additive-only.")
    parser.add_argument("--from", dest="src", required=True, help="Source: 'local', 'cloud', or a postgresql:// URL")
    parser.add_argument("--into", dest="dst", required=True, help="Target: 'local', 'cloud', or a postgresql:// URL")
    parser.add_argument("--dry-run", action="store_true", help="Show what would happen, change nothing")
    parser.add_argument("--yes", action="store_true", help="Skip the confirmation prompt")
    args = parser.parse_args()

    src_dsn = resolve_dsn(args.src)
    dst_dsn = resolve_dsn(args.dst)

    if not args.dry_run and not args.yes:
        answer = input(
            f"This will copy any new rows from '{args.src}' into '{args.dst}'. "
            f"Nothing in '{args.dst}' is ever deleted or changed. Continue? [y/N] "
        )
        if answer.strip().lower() not in ("y", "yes"):
            print("Cancelled -- nothing was touched.")
            return

    report = run_merge(src_dsn, dst_dsn, dry_run=args.dry_run)

    print(f"\n{'DRY RUN -- nothing was saved' if args.dry_run else 'Merge complete'} "
          f"({args.src} -> {args.dst}):\n")
    for line in report:
        print(" ", line)
    if args.dry_run:
        print("\nRe-run without --dry-run to actually write these changes.")


if __name__ == "__main__":
    main()
