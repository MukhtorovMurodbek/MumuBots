#!/usr/bin/env python3
"""One-time move: ParentBot -> ManagerBot, in the live database.

    python scripts/rename_to_managerbot.py --dry-run
    python scripts/rename_to_managerbot.py

v1.6.0 renamed every layer of the private bot except its @username, which was
already `@mumu_manager_bot` and was the reason for the flip. Six of the seven
layers are text in this repository and were changed by editing files. The
seventh is not: the bus id `parentbot` and the schema `parent_bot` are rows
and objects in the shared Postgres database, and no amount of editing here
moves them.

WHAT HAPPENS IF THIS IS NOT RUN
    ManagerBot starts, finds no `manager_bot` schema, and creates an empty
    one -- so its own tables (persisted conversation state, the settings it
    keeps) look wiped. Meanwhile `family.heartbeats` gains a second row and
    `/status` reports six bots: five running and a `parentbot` that has been
    silent since the deploy. Nothing is lost, but everything is in two places
    and the fix is this script run late instead of early.

WHAT IT DOES
    1. `ALTER SCHEMA parent_bot RENAME TO manager_bot` -- the tables, their
       rows, their indexes and their sequences all travel with it. Postgres
       does this as a catalogue update; it does not copy data and it is not
       slow on a schema of any size.
    2. Rewrites `parentbot` to `managerbot` in every column of every table in
       the family schema that names a bot. The columns are discovered from
       `information_schema` rather than listed here, so a table added later is
       covered without anybody remembering this file -- the same reasoning as
       the erase tests.
    3. Rewrites the `family.settings` keys that carry a bot id inside them
       (`maintenance:parentbot`, and the restart note).
    4. Leaves message text alone. `family.events` holds sentences somebody
       wrote at the time, and a row that says "ParentBot started" is what
       happened; rewriting it would make the log a worse record, not a
       better one.

SAFE TO RUN TWICE. Every statement is conditional on the old name still being
there, so a second run reports zero of everything and changes nothing.

ORDER, AT DEPLOY TIME
    Run this first, then update the Railway variables (MBOT_* in place of
    PBOT_*, DB_SCHEMA=manager_bot), then redeploy. ManagerBot is private and
    has one user, so the gap between the rename and the new container costs
    nothing -- it is the one bot in the family where that is true. DEPLOY.md
    has the full sequence.
"""
from __future__ import annotations

import argparse
import os
import sys
from contextlib import closing
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parent.parent

# One per line rather than unpacked in pairs: tests/test_sql_safety.py reads
# the module's ALL_CAPS assignments to decide which f-string holes are the
# code's own, and it only sees a plain `NAME = ...`.
OLD_SCHEMA = "parent_bot"
NEW_SCHEMA = "manager_bot"
OLD_ID = "parentbot"
NEW_ID = "managerbot"
OLD_NAME = "ParentBot"
NEW_NAME = "ManagerBot"
FAMILY_SCHEMA = "family"


def read_env_file(path: Path) -> dict:
    """The same three-line parser the other scripts use: KEY=value, # is a
    comment, and nothing is unquoted or expanded."""
    values = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


def resolve_dsn(override: str | None) -> str:
    if override:
        return override
    if os.environ.get("DATABASE_URL"):
        return os.environ["DATABASE_URL"]
    for folder in ("manager_bot", "parent_bot", "sticker_bot"):
        env = read_env_file(ROOT / folder / ".env")
        if env.get("DATABASE_URL"):
            return env["DATABASE_URL"]
    raise SystemExit(
        "No database to talk to. Pass --dsn, set DATABASE_URL, or run this "
        "from a checkout where a bot's .env has one."
    )


def bot_naming_columns(conn) -> list:
    """Every text column in the family schema that holds a bot id.

    Asked of the catalogue rather than listed, so `family.usage_samples` --
    which did not exist when the first version of this problem came up -- is
    covered without an edit. The names are matched, not guessed at: a column
    called `bot_id`, `target_bot` or `display_name` is the whole set today
    and the pattern is what a new one would be called too.
    """
    rows = conn.execute(
        """
        SELECT table_name, column_name
          FROM information_schema.columns
         WHERE table_schema = %s
           AND data_type IN ('text', 'character varying')
           AND (column_name IN ('bot_id', 'target_bot', 'display_name')
                OR column_name LIKE '%%bot_id')
         ORDER BY table_name, column_name
        """,
        (FAMILY_SCHEMA,),
    ).fetchall()
    return [(table, column) for table, column in rows]


def rename(conn) -> list:
    report = []

    exists = conn.execute(
        "SELECT 1 FROM information_schema.schemata WHERE schema_name = %s",
        (OLD_SCHEMA,),
    ).fetchone()
    already = conn.execute(
        "SELECT 1 FROM information_schema.schemata WHERE schema_name = %s",
        (NEW_SCHEMA,),
    ).fetchone()

    if exists and already:
        raise SystemExit(
            f"Both {OLD_SCHEMA} and {NEW_SCHEMA} exist. That means a "
            f"{NEW_NAME} started before this ran and made itself an empty "
            f"schema. Drop the empty one by hand (check which is empty "
            f"first), then run this again."
        )
    if exists:
        conn.execute(f'ALTER SCHEMA "{OLD_SCHEMA}" RENAME TO "{NEW_SCHEMA}"')
        report.append(f"schema {OLD_SCHEMA} -> {NEW_SCHEMA}")
    else:
        report.append(f"schema {OLD_SCHEMA} not there (already renamed)")

    # A heartbeat is rewritten every few seconds and its bot_id is the primary
    # key, so a stale row under the new name would collide with the update for
    # no gain. It is the one row that is safe to simply remove.
    removed = conn.execute(
        f'DELETE FROM "{FAMILY_SCHEMA}".heartbeats WHERE bot_id = %s', (NEW_ID,)
    ).rowcount
    if removed:
        report.append(f"family.heartbeats: dropped {removed} stale {NEW_ID} row(s)")

    for table, column in bot_naming_columns(conn):
        old = OLD_NAME if column == "display_name" else OLD_ID
        new = NEW_NAME if column == "display_name" else NEW_ID
        count = conn.execute(
            f'UPDATE "{FAMILY_SCHEMA}"."{table}" SET "{column}" = %s WHERE "{column}" = %s',
            (new, old),
        ).rowcount
        if count:
            report.append(f"{FAMILY_SCHEMA}.{table}.{column}: {count} row(s)")

    # heartbeats also records which schema a bot's own tables are in.
    count = conn.execute(
        f'UPDATE "{FAMILY_SCHEMA}".heartbeats SET db_schema = %s WHERE db_schema = %s',
        (NEW_SCHEMA, OLD_SCHEMA),
    ).rowcount
    if count:
        report.append(f"{FAMILY_SCHEMA}.heartbeats.db_schema: {count} row(s)")

    # settings keys carry the bot id inside them: maintenance:parentbot, and
    # the note lifecycle.py leaves before an expected restart.
    count = conn.execute(
        f"""UPDATE "{FAMILY_SCHEMA}".settings
               SET key = replace(key, %s, %s)
             WHERE key LIKE %s""",
        (OLD_ID, NEW_ID, f"%{OLD_ID}%"),
    ).rowcount
    if count:
        report.append(f"{FAMILY_SCHEMA}.settings.key: {count} row(s)")

    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--dsn", help="the shared database; defaults to DATABASE_URL or a bot's .env")
    parser.add_argument("--dry-run", action="store_true",
                        help="do all of it in a transaction and roll it back")
    args = parser.parse_args()

    dsn = resolve_dsn(args.dsn)
    where = dsn.rsplit("@", 1)[-1]
    print(f"{'Would rename' if args.dry_run else 'Renaming'} {OLD_NAME} -> {NEW_NAME} in {where}\n")

    with closing(psycopg.connect(dsn, autocommit=False)) as conn:
        report = rename(conn)
        for line in report:
            print("  " + line)
        if not report:
            print("  nothing to do")
        if args.dry_run:
            conn.rollback()
            print("\nRolled back -- nothing was changed.")
        else:
            conn.commit()
            print("\nDone. Now set MBOT_* and DB_SCHEMA=manager_bot on Railway "
                  "and redeploy. See DEPLOY.md.")


if __name__ == "__main__":
    sys.exit(main())
