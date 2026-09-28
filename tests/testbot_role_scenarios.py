"""TestBot's login on the shared database: it reads everything, and writes
only its own testbot_* schemas.

    python tests/testbot_role_scenarios.py

Needs Postgres (TEST_DATABASE_URL, or the local default) as a login that can
create databases and roles -- the local docker-compose one is. It works in a
scratch database of its own, dropped at the end, with the role the script
makes, dropped too.

It runs testbot/supabase_role.sql itself, as written, against schemas shaped
like production's -- so what is tested is the file the owner pastes into
Supabase, not a copy of it -- and then asks the database, as that role:

  it can read every table the real bots keep, including one made after;
  it cannot insert, update, delete, truncate or create anything there, or in
  public;
  what every bot runs as it starts -- CREATE SCHEMA IF NOT EXISTS on its own
  schema, CREATE TABLE IF NOT EXISTS, ALTER TABLE -- works in testbot_*;
  a testbot_* schema made earlier by TestBot running as postgres is handed
  over, with its tables;
  switch.py's check finds nothing it could write outside testbot_*, and does
  find the one table a stray grant opens -- and a superuser, everything;
  running the script twice changes nothing.
"""
import importlib.util
import os
import secrets
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg
from psycopg import errors

ROOT = Path(__file__).resolve().parent.parent
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ADMIN = os.environ.get("TEST_DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/botfamily_test")
SCRATCH = "testbot_role_check"
SCRIPT = (ROOT / "testbot" / "supabase_role.sql").read_text(encoding="utf-8")

spec = importlib.util.spec_from_file_location("testbot_switch", ROOT / "testbot" / "switch.py")
switch = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = switch
spec.loader.exec_module(switch)

failures = []


def check(name, ok, detail=""):
    print(("  ok   " if ok else "  FAIL ") + name + ("" if ok else f"\n       {detail}"))
    if not ok:
        failures.append(name)


def dsn(base: str, user: str | None = None, password: str | None = None, database: str | None = None) -> str:
    parts = urlsplit(base)
    user = user or parts.username
    password = password if password is not None else parts.password
    netloc = f"{user}:{password}@{parts.hostname}:{parts.port or 5432}"
    return urlunsplit((parts.scheme, netloc, "/" + (database or parts.path.lstrip("/")), "", ""))


def read(conn, sql: str):
    """The rows, or the error -- a refused read is a finding, not a crash."""
    try:
        return conn.execute(sql).fetchall()
    except psycopg.Error as exc:
        return exc


def refused(conn, sql: str) -> bool:
    try:
        conn.execute(sql)
    except errors.InsufficientPrivilege:
        return True
    return False


def main() -> int:
    password = secrets.token_hex(12)
    admin_scratch = dsn(ADMIN, database=SCRATCH)
    role_scratch = dsn(ADMIN, user="testbot", password=password, database=SCRATCH)

    with psycopg.connect(ADMIN, autocommit=True) as conn:
        conn.execute(f"DROP DATABASE IF EXISTS {SCRATCH} WITH (FORCE)")
        conn.execute(f"CREATE DATABASE {SCRATCH}")
    try:
        with psycopg.connect(admin_scratch, autocommit=True) as conn:
            # Shaped like production: the real bots' schemas, and a testbot_*
            # one left behind by TestBot running as postgres before the role.
            for schema, table in (("family", "star_balances"), ("convert_bot", "jobs"), ("sticker_bot", "packs"),
                                  ("downloader_bot", "downloads"), ("anon_bot", "conversations"),
                                  ("manager_bot", "notes"), ("testbot_convert_bot", "left_behind")):
                conn.execute(f"CREATE SCHEMA {schema}")
                conn.execute(f"CREATE TABLE {schema}.{table} (id serial PRIMARY KEY, value text)")
                conn.execute(f"INSERT INTO {schema}.{table} (value) VALUES ('real')")
            conn.execute("CREATE TABLE public.shared (id int)")

            print("== the script, as written ==")
            conn.execute(SCRIPT.replace("CHANGE-ME", password))
            check("runs", True)
            conn.execute(SCRIPT.replace("CHANGE-ME", password))
            check("and runs again, changing nothing", True)
            settings = conn.execute("SELECT rolconnlimit, rolconfig, rolsuper, rolcreaterole, rolcreatedb "
                                    "FROM pg_roles WHERE rolname = 'testbot'").fetchone()
            check("at most 24 connections, statements cut at a minute, and no power over other logins",
                  settings[0] == 24 and "statement_timeout=60s" in (settings[1] or [])
                  and not any(settings[2:]), settings)

        with psycopg.connect(role_scratch, autocommit=True) as role:
            print("\n== as testbot: the real bots' schemas ==")
            rows = read(role, "SELECT value FROM family.star_balances")
            check("reads the family schema", rows == [("real",)], rows)
            for schema, table in (("convert_bot", "jobs"), ("sticker_bot", "packs"), ("manager_bot", "notes")):
                rows = read(role, f"SELECT count(*) FROM {schema}.{table}")
                check(f"reads {schema}", rows == [(1,)], rows)
            for sql in ("INSERT INTO family.star_balances (value) VALUES ('test')",
                        "UPDATE family.star_balances SET value = 'test'",
                        "DELETE FROM convert_bot.jobs",
                        "TRUNCATE sticker_bot.packs",
                        "CREATE TABLE family.new_table (id int)",
                        "CREATE TABLE public.new_table (id int)",
                        "INSERT INTO public.shared VALUES (1)",
                        "ALTER TABLE convert_bot.jobs ADD COLUMN extra text",
                        "DROP TABLE anon_bot.conversations",
                        "SELECT setval('family.star_balances_id_seq', 99)"):
                check(f"cannot: {sql.split(' (')[0][:60]}", refused(role, sql))

        with psycopg.connect(admin_scratch, autocommit=True) as conn:
            conn.execute("CREATE TABLE convert_bot.made_later (id int)")
            conn.execute("INSERT INTO convert_bot.made_later VALUES (7)")
        with psycopg.connect(role_scratch, autocommit=True) as role:
            rows = read(role, "SELECT id FROM convert_bot.made_later")
            check("reads a table made after the script, too", rows == [(7,)], rows)
            check("and cannot write it", refused(role, "INSERT INTO convert_bot.made_later VALUES (8)"))

            print("\n== as testbot: its own schemas ==")
            for sql in ("CREATE SCHEMA IF NOT EXISTS testbot_family",
                        "CREATE SCHEMA IF NOT EXISTS \"testbot_convert_bot\"",
                        "CREATE TABLE IF NOT EXISTS testbot_family.heartbeats (bot_id text PRIMARY KEY, at timestamptz)",
                        "INSERT INTO testbot_family.heartbeats VALUES ('testbot_convert', now())",
                        "ALTER TABLE testbot_family.heartbeats ADD COLUMN IF NOT EXISTS version text",
                        "UPDATE testbot_family.heartbeats SET version = 'test'",
                        "DELETE FROM testbot_family.heartbeats"):
                try:
                    role.execute(sql)
                    worked = True
                except psycopg.Error as exc:
                    worked = exc
                check(f"can: {sql[:70]}", worked is True, worked)
            try:
                role.execute("INSERT INTO testbot_convert_bot.left_behind (value) VALUES ('test')")
                role.execute("ALTER TABLE testbot_convert_bot.left_behind ADD COLUMN extra text")
                handed = True
            except psycopg.Error as exc:
                handed = exc
            check("a testbot_* schema TestBot made as postgres is its own now, tables and sequences",
                  handed is True, handed)

        print("\n== switch.py's check ==")
        found = switch.writable_outside_testbot(role_scratch)
        check("finds nothing testbot could write outside testbot_*", found == [], found)
        with psycopg.connect(admin_scratch, autocommit=True) as conn:
            conn.execute("GRANT INSERT ON convert_bot.jobs TO testbot")
            found = switch.writable_outside_testbot(role_scratch)
            check("finds the schema one stray grant opens", found == ["convert_bot"], found)
            conn.execute("REVOKE INSERT ON convert_bot.jobs FROM testbot")
            conn.execute("GRANT CREATE ON SCHEMA public TO testbot")
            found = switch.writable_outside_testbot(role_scratch)
            check("and the right to create in a schema", found == ["public"], found)
            conn.execute("REVOKE CREATE ON SCHEMA public FROM testbot")
        found = switch.writable_outside_testbot(admin_scratch)
        check("and, for a superuser, everything", found and "superuser" in found[0], found)
    finally:
        with psycopg.connect(ADMIN, autocommit=True) as conn:
            conn.execute(f"DROP DATABASE IF EXISTS {SCRATCH} WITH (FORCE)")
            conn.execute("DROP ROLE IF EXISTS testbot")

    print(f"\nFAILURES: {len(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
