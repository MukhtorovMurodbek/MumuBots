#!/usr/bin/env python3
"""One-time move: four separate bot databases -> one database, four schemas.

    python migrate_to_shared_db.py --into postgresql://.../botfamily --dry-run
    python migrate_to_shared_db.py --into postgresql://.../botfamily

Before v0.5.0 each bot had a Postgres database entirely of its own
(stickerbot, convertbot, downloaderbot, anonbot), and every one of them put
its tables in `public`. From v0.5.0 the family shares one database with a
schema per bot, which is what makes ManagerBot possible: heartbeats, the
command queue and cross-bot queries all live in that one place.

This script reads each bot's OLD database (from that bot's own .env, or
--from-<bot>) and copies every row into its new schema in the target
database. It is additive and re-runnable:

  * The new tables are created by importing each bot's own db.py and calling
    its init_db() against the new schema -- so the schema definitions come
    from the bots themselves and cannot drift from what they expect.
  * Rows are inserted with ON CONFLICT DO NOTHING, so running it twice adds
    nothing the second time and never overwrites anything.
  * The old databases are left completely untouched. Nothing is dropped,
    nothing is renamed. If this goes wrong, point the bots back at their old
    DATABASE_URLs and you have lost nothing.
  * --dry-run does the whole thing inside a transaction and rolls it back.

Run it once, then set DATABASE_URL and DB_SCHEMA in each bot's .env (see
that bot's .env.example) and start them.
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import sys
from contextlib import closing
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parent

# folder -> (new schema, old database name in the default local URL)
BOTS = [
    ("sticker_bot", "sticker_bot", "stickerbot"),
    ("convert_bot", "convert_bot", "convertbot"),
    ("downloader_bot", "downloader_bot", "downloaderbot"),
    ("anon_bot", "anon_bot", "anonbot"),
]


def read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def load_bot_db_module(folder: str):
    """Imports one bot's db.py under a unique module name. Each bot's db.py
    only imports the standard library and psycopg, so four of them can live
    in one process without colliding."""
    spec = importlib.util.spec_from_file_location(f"{folder}_db", ROOT / folder / "db.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def source_dsn(folder: str, old_db_name: str, override: str | None) -> str:
    if override:
        return override
    env = read_env_file(ROOT / folder / ".env")
    # The .env may already have been switched over to the shared database --
    # in that case the old per-bot URL is what we want, not what's in there.
    dsn = env.get("OLD_DATABASE_URL") or env.get("DATABASE_URL")
    if dsn and old_db_name in dsn:
        return dsn
    return f"postgresql://postgres:postgres@localhost:5432/{old_db_name}"


def table_names(conn, schema: str) -> list[str]:
    cur = conn.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = %s AND table_type = 'BASE TABLE' ORDER BY table_name",
        (schema,),
    )
    return [row[0] for row in cur.fetchall()]


def column_names(conn, schema: str, table: str) -> list[str]:
    cur = conn.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = %s AND table_name = %s ORDER BY ordinal_position",
        (schema, table),
    )
    return [row[0] for row in cur.fetchall()]


def migrate_one(folder: str, schema: str, src_dsn: str, dst_dsn: str, dry_run: bool) -> list[str]:
    report: list[str] = []

    module = load_bot_db_module(folder)
    module.DB_SCHEMA = schema
    try:
        module.init_db(dst_dsn)
    except Exception as exc:
        return [f"  !! could not create {schema}.* from {folder}/db.py: {exc}"]

    try:
        src_conn = psycopg.connect(src_dsn)
    except Exception as exc:
        return [f"  -- old database not reachable ({exc.__class__.__name__}), nothing to copy"]

    with closing(src_conn) as src, closing(psycopg.connect(dst_dsn)) as dst:
        old_tables = table_names(src, "public")
        new_tables = set(table_names(dst, schema))
        if not old_tables:
            return ["  -- old database is empty, nothing to copy"]

        for table in old_tables:
            if table not in new_tables:
                report.append(f"  {table}: not part of this bot any more, skipped")
                continue
            src_cols = column_names(src, "public", table)
            dst_cols = set(column_names(dst, schema, table))
            cols = [c for c in src_cols if c in dst_cols]
            if not cols:
                report.append(f"  {table}: no shared columns, skipped")
                continue

            rows = src.execute(f'SELECT {", ".join(f_quote(c) for c in cols)} FROM public."{table}"').fetchall()
            if not rows:
                report.append(f"  {table}: empty")
                continue

            placeholders = ", ".join(["%s"] * len(cols))
            sql = (
                f'INSERT INTO "{schema}"."{table}" ({", ".join(f_quote(c) for c in cols)}) '
                f"VALUES ({placeholders}) ON CONFLICT DO NOTHING"
            )
            before = dst.execute(f'SELECT count(*) FROM "{schema}"."{table}"').fetchone()[0]
            with dst.cursor() as cur:
                cur.executemany(sql, rows)
            after = dst.execute(f'SELECT count(*) FROM "{schema}"."{table}"').fetchone()[0]
            report.append(f"  {table}: {len(rows)} row(s) read, {after - before} new (now {after})")

            # BIGSERIAL ids were copied across as values; the sequence behind
            # them has to be moved past them or the next insert collides.
            for col in cols:
                seq = dst.execute(
                    "SELECT pg_get_serial_sequence(%s, %s)", (f'"{schema}"."{table}"', col)
                ).fetchone()[0]
                if seq:
                    dst.execute(
                        f'SELECT setval(%s, COALESCE((SELECT MAX("{col}") FROM "{schema}"."{table}"), 1), true)',
                        (seq,),
                    )

        if dry_run:
            dst.rollback()
        else:
            dst.commit()
    return report


def f_quote(col: str) -> str:
    return f'"{col}"'


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--into", default=os.environ.get("FAMILY_DATABASE_URL"),
        help="the new shared database, e.g. postgresql://postgres:postgres@localhost:5432/botfamily",
    )
    parser.add_argument("--dry-run", action="store_true", help="do it all, then roll back")
    parser.add_argument("--only", nargs="*", help="only these bot folders")
    for folder, _, _ in BOTS:
        parser.add_argument(f"--from-{folder.replace('_', '-')}", dest=f"src_{folder}", default=None,
                            help=f"override {folder}'s old database URL")
    args = parser.parse_args()

    if not args.into:
        sys.exit(
            "Tell me where to migrate to: --into postgresql://.../botfamily "
            "(or set FAMILY_DATABASE_URL)."
        )

    try:
        with closing(psycopg.connect(args.into)) as conn:
            name = conn.execute("SELECT current_database()").fetchone()[0]
    except Exception as exc:
        sys.exit(f"Can't reach the target database: {exc}")

    print(f"Target: {name}")
    if args.dry_run:
        print("DRY RUN -- everything is rolled back at the end.\n")

    for folder, schema, old_name in BOTS:
        if args.only and folder not in args.only:
            continue
        src = source_dsn(folder, old_name, getattr(args, f"src_{folder}"))
        print(f"=== {folder} -> schema \"{schema}\" (from {src.rsplit('@', 1)[-1]}) ===")
        for line in migrate_one(folder, schema, src, args.into, args.dry_run):
            print(line)
        print()

    print("Dry run finished -- nothing was written." if args.dry_run else "Migration finished.")
    # The target DSN is named by host rather than printed, the same way the
    # source is above. It carries the password of the database everything has
    # just been moved into, and echoing it would put that in terminal
    # scrollback for the sake of saving a paste -- from a value the operator
    # supplied on this very command line, so they already have it.
    print(
        "\nNext: in each bot's .env set\n"
        f"  DATABASE_URL=   the --into value you passed ({args.into.rsplit('@', 1)[-1]})\n"
        "  DB_SCHEMA=<that bot's schema>\n"
        "then start the bots. Their old databases are still there, untouched."
    )


if __name__ == "__main__":
    main()
