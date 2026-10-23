#!/usr/bin/env python3
"""Reconcile AnonBot's two databases (e.g. your laptop's local DB and its
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
  - anon_conversations is the one table with an internal foreign key
    (anon_follower_state / anon_relay point at anon_conversations.id). Its
    id is always freshly assigned by the target database; this script
    tracks the old-id -> new-id mapping in memory for the duration of the
    run and rewrites the matching anon_follower_state / anon_relay rows to
    point at the right place. If both databases coincidentally used the
    same conv_number for a genuinely different conversation between the
    same two people (recognised by a different started_at), the incoming
    one is renumbered instead of merged into the existing row.
  - Known limitation: if a conversation was still in the middle of a
    reply-chain at the exact moment you switched hosts, replies to messages
    sent before the switch won't resolve on the new side until you merge
    again -- the bot always starts fresh routing state after a switch, same
    as it would if you'd wiped the DB. Everything else merges cleanly.
  - Always run with --dry-run first. It runs the exact same logic inside a
    transaction that gets rolled back instead of committed, and prints what
    it *would* do.
  - Personal data is sealed (2.1.1): every Telegram id in these tables is a
    pseudonym, and the ids the bot must read back sit beside it in sealed
    columns. Both databases must have been sealed with the SAME data key --
    family.settings 'data_key:v1' must exist in both, be equal, and be the key
    this machine's DATA_KEY_FILE holds -- or nothing is merged. A sealed value
    is compared by what it opens to (the same person sealed twice gives two
    different ciphertexts), and a source row whose sealed value does not open,
    or does not belong to its row, is skipped and counted, never copied.
    This script never prints an id, a username or a sealed value.
"""
from __future__ import annotations

import argparse
import os
import sys
from contextlib import closing
from pathlib import Path


import db
import vault

# "ids" are the columns that hold a person or chat as a pseudonym. "sealed" maps
# a sealed column to (purpose, "id" or "text", the pseudonym column it must
# match). Two databases sealed with one key hold the same pseudonym for the
# same person, so the keys compare as stored; the sealed bytes do not (a fresh
# nonce each time), so rows are compared by what the seals open to.
PLAIN_TABLES = [
    {"name": "star_transactions", "key": ("payload",),
     "cols": ["user_id", "username_sealed", "amount_stars", "item", "status", "payload",
              "charge_id", "currency", "created_at", "updated_at"],
     "ids": ["user_id"],
     "sealed": {"username_sealed": ("stars.username", "text", None)}},
    {"name": "donation_prompts", "key": ("user_id",),
     "cols": ["user_id", "action_count", "last_shown_at"],
     "ids": ["user_id"], "sealed": {}},
    {"name": "anon_links", "key": ("owner_user_id",),
     "cols": ["owner_user_id", "owner_sealed", "token", "is_paused", "created_at"],
     "ids": ["owner_user_id"],
     "sealed": {"owner_sealed": ("anon.link.owner", "id", "owner_user_id")}},
    {"name": "anon_blocks", "key": ("owner_user_id", "follower_user_id"),
     "cols": ["owner_user_id", "follower_user_id", "blocked_at"],
     "ids": ["owner_user_id", "follower_user_id"], "sealed": {}},
    {"name": "user_settings", "key": ("user_id",),
     "cols": ["user_id", "user_sealed", "language"],
     "ids": ["user_id"],
     "sealed": {"user_sealed": ("anon.user", "id", "user_id")}},
]
KEY_STAMP = "data_key:v1"


class MergeRefused(RuntimeError):
    """The two databases cannot be merged as they stand. Nothing was written."""
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


def key_stamp(conn) -> "str | None":
    """The fingerprint of the data key this database was sealed with, or None
    when it has never been stamped (or has no family.settings at all)."""
    if conn.execute("SELECT to_regclass('family.settings')").fetchone()[0] is None:
        return None
    row = conn.execute("SELECT value FROM family.settings WHERE key = %s", (KEY_STAMP,)).fetchone()
    return row[0] if row else None


def require_same_key(src_conn, dst_conn) -> None:
    """Raises MergeRefused unless both databases carry the same data-key stamp
    AND it is the key this machine holds -- a pseudonym or a seal made under
    another key means nothing here, and merging them would plant rows no bot
    could ever read."""
    src, dst = key_stamp(src_conn), key_stamp(dst_conn)
    if src is None or dst is None:
        raise MergeRefused(
            ("The source" if src is None else "The target") + " database has no data-key stamp "
            "(family.settings 'data_key:v1'): its personal data is not sealed yet. Run "
            "scripts/encrypt_personal_data.py on it first. Nothing was merged.")
    if src != dst:
        raise MergeRefused("The two databases were sealed with different data keys, so their "
                           "pseudonyms and sealed values cannot be combined. Nothing was merged.")
    if src != vault.fingerprint():
        raise MergeRefused("DATA_KEY_FILE is not the key these databases were sealed with. "
                           "Nothing was merged.")


def _opened(row: tuple, cols: list[str], sealed: dict) -> "list | None":
    """`row` with every sealed value replaced by what it opens to, or None
    when one does not open, or does not belong to the pseudonym beside it."""
    out = list(row)
    for column, (purpose, kind, ties_to) in sealed.items():
        i = cols.index(column)
        try:
            if kind == "id":
                out[i] = vault.open_id(row[i], purpose, expected=row[cols.index(ties_to)])
            else:
                out[i] = vault.open_text(row[i], purpose)
        except (vault.VaultError, TypeError, ValueError):
            return None
    return out


def _described(row: tuple, cols: list[str], sealed: dict) -> dict:
    """A row for the report: its pseudonyms and plain columns, with each sealed
    column elided -- the report never carries a ciphertext or what it hides."""
    return {c: ("<sealed>" if c in sealed else v) for c, v in zip(cols, row)}


def _all_pseudonyms(row: tuple, cols: list[str], id_cols: list[str]) -> bool:
    return all(row[cols.index(c)] is None or vault.is_pseudonym(row[cols.index(c)]) for c in id_cols)


def merge_plain_table(src_conn, dst_conn, spec: dict, report: list[str]) -> None:
    name, key_cols, cols = spec["name"], spec["key"], spec["cols"]
    sealed, id_cols = spec["sealed"], spec["ids"]
    key_idx = [cols.index(k) for k in key_cols]

    src_rows = fetch_rows(src_conn, name, cols)
    dst_rows = fetch_rows(dst_conn, name, cols)
    dst_by_key = {tuple(row[i] for i in key_idx): row for row in dst_rows}

    added, identical, conflicts, refused = 0, 0, [], 0
    placeholders = ", ".join(["%s"] * len(cols))
    for row in src_rows:
        key = tuple(row[i] for i in key_idx)
        logical = _opened(row, cols, sealed)
        if logical is None or not _all_pseudonyms(row, cols, id_cols):
            refused += 1
            continue
        existing = dst_by_key.get(key)
        if existing is None:
            dst_conn.execute(f"INSERT INTO {name} ({', '.join(cols)}) VALUES ({placeholders})", row)
            added += 1
        elif _opened(existing, cols, sealed) == logical:
            identical += 1
        else:
            conflicts.append((key, existing, row))

    if added or conflicts:
        report.append(f"{name}: +{added} added, {identical} already identical, {len(conflicts)} conflicts (skipped)")
    else:
        report.append(f"{name}: nothing to add ({identical} already identical)")
    if refused:
        report.append(f"    {name}: {refused} source row(s) NOT copied -- a sealed value did not open, "
                      f"did not belong to its row, or an id was not a pseudonym")
    for key, existing, row in conflicts:
        report.append(
            f"    conflict on {name} {key_cols}={key}: "
            f"target has {_described(existing, cols, sealed)}, source has {_described(row, cols, sealed)} "
            f"-- kept target's, review by hand"
        )


def merge_anon_conversations(src_conn, dst_conn, report: list[str]) -> dict[int, int]:
    """Returns {source anon_conversations.id -> target id} for every source
    conversation. A conversation's *identity* is (owner, follower,
    started_at) -- conv_number is only a display counter and can coincide
    between two independently-run databases, so it's not part of the
    identity match."""
    cols = ["id", "owner_user_id", "follower_user_id", "conv_number",
            "started_at", "last_activity_at", "last_owner_msg_id", "last_follower_msg_id",
            "follower_first_msg_at", "archived_at", "owner_sealed", "follower_sealed"]
    src_rows = fetch_rows(src_conn, "anon_conversations", cols)
    dst_rows = fetch_rows(dst_conn, "anon_conversations", cols)

    dst_by_identity = {(r[1], r[2], r[4]): r for r in dst_rows}
    used_conv_numbers: dict[tuple[int, int], set[int]] = {}
    for r in dst_rows:
        used_conv_numbers.setdefault((r[1], r[2]), set()).add(r[3])

    id_map: dict[int, int] = {}
    added, matched, renumbered, refused = 0, 0, 0, 0

    for row in src_rows:
        (src_id, owner, follower, conv_number, started_at,
         last_activity_at, last_owner_msg_id, last_follower_msg_id,
         follower_first_msg_at, archived_at, owner_sealed, follower_sealed) = row
        # Both people are pseudonyms, and each sealed id must open to the person
        # its pseudonym stands for. A row that fails is left behind, and what
        # pointed at it is reported as orphaned below.
        try:
            vault.open_id(owner_sealed, "anon.conversation.owner", expected=owner)
            vault.open_id(follower_sealed, "anon.conversation.follower", expected=follower)
        except (vault.VaultError, TypeError, ValueError):
            refused += 1
            continue
        pair = (owner, follower)
        identity_key = (owner, follower, started_at)
        existing = dst_by_identity.get(identity_key)

        if existing is not None:
            id_map[src_id] = existing[0]
            matched += 1
            continue

        taken = used_conv_numbers.setdefault(pair, set())
        target_conv_number = conv_number
        if target_conv_number in taken:
            target_conv_number = max(taken) + 1
            renumbered += 1

        cur = dst_conn.execute(
            """
            INSERT INTO anon_conversations
                (owner_user_id, follower_user_id, conv_number, started_at, last_activity_at,
                 last_owner_msg_id, last_follower_msg_id, follower_first_msg_at, archived_at,
                 owner_sealed, follower_sealed)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (owner, follower, target_conv_number, started_at, last_activity_at,
             last_owner_msg_id, last_follower_msg_id, follower_first_msg_at, archived_at,
             owner_sealed, follower_sealed),
        )
        new_id = cur.fetchone()[0]
        id_map[src_id] = new_id
        taken.add(target_conv_number)
        dst_by_identity[identity_key] = (
            new_id, owner, follower, target_conv_number, started_at,
            last_activity_at, last_owner_msg_id, last_follower_msg_id,
            follower_first_msg_at, archived_at, owner_sealed, follower_sealed,
        )
        added += 1

    report.append(
        f"anon_conversations: +{added} added"
        + (f" ({renumbered} renumbered to avoid a coincidental conv_number collision)" if renumbered else "")
        + f", {matched} already identical"
        + (f", {refused} NOT copied (a sealed id did not open or did not belong to its row)" if refused else "")
    )
    return id_map


def merge_anon_follower_state(src_conn, dst_conn, id_map: dict[int, int], report: list[str]) -> None:
    cols = ["follower_user_id", "owner_user_id", "conversation_id"]
    src_rows = fetch_rows(src_conn, "anon_follower_state", cols)
    dst_rows = fetch_rows(dst_conn, "anon_follower_state", cols)
    dst_by_key = {row[0]: row for row in dst_rows}

    added, identical, conflicts, orphaned, unsealed = 0, 0, [], 0, 0
    for follower_user_id, owner_user_id, conversation_id in src_rows:
        if not (vault.is_pseudonym(follower_user_id) and vault.is_pseudonym(owner_user_id)):
            unsealed += 1
            continue
        new_conv_id = id_map.get(conversation_id)
        if new_conv_id is None:
            orphaned += 1
            continue
        remapped = (follower_user_id, owner_user_id, new_conv_id)
        existing = dst_by_key.get(follower_user_id)
        if existing is None:
            dst_conn.execute(
                "INSERT INTO anon_follower_state (follower_user_id, owner_user_id, conversation_id) "
                "VALUES (%s, %s, %s)",
                remapped,
            )
            added += 1
        elif existing == remapped:
            identical += 1
        else:
            conflicts.append((follower_user_id, existing, remapped))

    msg = f"anon_follower_state: +{added} added, {identical} already identical, {len(conflicts)} conflicts (skipped)"
    if orphaned:
        msg += f", {orphaned} skipped (pointed at a conversation that wasn't found)"
    if unsealed:
        msg += f", {unsealed} NOT copied (an id was not a pseudonym)"
    report.append(msg)
    for follower_user_id, existing, remapped in conflicts:
        report.append(
            f"    conflict on anon_follower_state follower_user_id={follower_user_id}: "
            f"target routes to conversation {existing[2]}, source would route to {remapped[2]} -- kept target's"
        )


def merge_anon_relay(src_conn, dst_conn, id_map: dict[int, int], report: list[str]) -> None:
    cols = ["chat_id", "message_id", "conversation_id"]
    src_rows = fetch_rows(src_conn, "anon_relay", cols)
    dst_rows = fetch_rows(dst_conn, "anon_relay", cols)
    dst_by_key = {(row[0], row[1]): row for row in dst_rows}

    added, identical, conflicts, orphaned, unsealed = 0, 0, [], 0, 0
    for chat_id, message_id, conversation_id in src_rows:
        if not vault.is_pseudonym(chat_id):
            unsealed += 1
            continue
        new_conv_id = id_map.get(conversation_id)
        if new_conv_id is None:
            orphaned += 1
            continue
        remapped = (chat_id, message_id, new_conv_id)
        key = (chat_id, message_id)
        existing = dst_by_key.get(key)
        if existing is None:
            dst_conn.execute(
                "INSERT INTO anon_relay (chat_id, message_id, conversation_id) VALUES (%s, %s, %s)",
                remapped,
            )
            added += 1
        elif existing == remapped:
            identical += 1
        else:
            conflicts.append((key, existing, remapped))

    msg = f"anon_relay: +{added} added, {identical} already identical, {len(conflicts)} conflicts (skipped)"
    if orphaned:
        msg += f", {orphaned} skipped (pointed at a conversation that wasn't found)"
    if unsealed:
        msg += f", {unsealed} NOT copied (a chat id was not a pseudonym)"
    report.append(msg)


def run_merge(src_dsn: str, dst_dsn: str, dry_run: bool) -> list[str]:
    report: list[str] = []
    # Before anything is written, even by init_db: two databases that were not
    # sealed with one key have nothing in common to merge on.
    with closing(db.connect(src_dsn)) as src_conn, closing(db.connect(dst_dsn)) as dst_conn:
        require_same_key(src_conn, dst_conn)
    if not dry_run:
        # The family shares one database with a schema per bot, so the target
        # side may be a brand-new empty schema (a freshly deployed Railway
        # database, say). Idempotent, and skipped on --dry-run so a preview
        # still cannot write anything at all.
        db.init_db(dst_dsn)
    with closing(db.connect(src_dsn)) as src_conn, closing(db.connect(dst_dsn)) as dst_conn:
        for spec in PLAIN_TABLES:
            merge_plain_table(src_conn, dst_conn, spec, report)
        id_map = merge_anon_conversations(src_conn, dst_conn, report)
        merge_anon_follower_state(src_conn, dst_conn, id_map, report)
        merge_anon_relay(src_conn, dst_conn, id_map, report)

        if dry_run:
            dst_conn.rollback()
        else:
            dst_conn.commit()
    return report


def main() -> None:
    load_dotenv_if_present()
    parser = argparse.ArgumentParser(description="Merge AnonBot's two databases together, additive-only.")
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

    try:
        report = run_merge(src_dsn, dst_dsn, dry_run=args.dry_run)
    except MergeRefused as exc:
        sys.exit(str(exc))

    print(f"\n{'DRY RUN -- nothing was saved' if args.dry_run else 'Merge complete'} "
          f"({args.src} -> {args.dst}):\n")
    for line in report:
        print(" ", line)
    if args.dry_run:
        print("\nRe-run without --dry-run to actually write these changes.")


if __name__ == "__main__":
    main()
