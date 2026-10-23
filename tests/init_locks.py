"""init_db() on a start that has nothing to migrate takes no table lock. Needs Postgres.

    python tests/init_locks.py

Every ALTER TABLE takes an ACCESS EXCLUSIVE lock on its table until the
transaction ends -- even one that changes nothing, `IF NOT EXISTS` or not -- and
init_db() is one transaction that commits at the very end. So a start that only
wanted to check used to queue every other reader and writer of user_settings,
star_transactions and the rest behind it, which during a deploy overlap means
the old instance, still serving. Each bot's db.py (and shared/family_link.py
for the family tables) now looks in information_schema first and issues the
ALTER only when something is missing or different.

For each of the four bots that migrate their own tables, in its own subprocess
and its own schema (<bot>_locks, dropped afterwards), and for the family schema
through family_link.init_family_schema():

  * a fresh database ends with every column the migrations add, with the right
    type and default (and AnonBot without its retired owner_replied_at);
  * a second init_db() issues no ALTER at all -- the SQL it sends is recorded --
    and does look in the catalog (so the first check cannot pass by sending
    nothing);
  * a second init_db() does not wait behind a reader of every table, which is
    the behaviour the ALTER used to break: a transaction holds an ACCESS SHARE
    lock on each table while init_db() runs in a thread, and it must finish;
  * a database from before those migrations (amount_stars an INTEGER, every
    added column gone, AnonBot's default missing, owner_replied_at back) is
    brought forward by exactly the ALTERs it needs, and a third run is quiet.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
DSN = os.environ.get("TEST_DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/botfamily_test")

STARS = {
    ("star_transactions", "amount_stars"): "bigint",
    ("star_transactions", "currency"): "text",
    ("donation_prompts", "times_shown"): "integer",
    ("donation_prompts", "pinned_message_id"): "bigint",
}
# bot -> (columns its migrations add, as (table, column) -> data_type; columns that must be gone)
EXPECT = {
    "sticker_bot": (STARS, []),
    "convert_bot": (STARS, []),
    "downloader_bot": ({**STARS,
                        ("settings", "language"): "text",
                        ("settings", "lossless_enabled"): "integer",
                        ("settings", "large_files_enabled"): "integer"}, []),
    "anon_bot": ({**STARS,
                  ("user_settings", "knows_reactions"): "boolean",
                  ("anon_conversations", "follower_first_msg_at"): "text",
                  ("anon_conversations", "archived_at"): "text",
                  ("anon_relay", "is_prompt"): "boolean",
                  ("anon_relay", "peer_message_id"): "bigint"},
                 [("anon_conversations", "owner_replied_at")]),
}

CHILD = r'''
import json, os, sys, threading, time
sys.path.insert(0, os.getcwd())
sys.path.insert(1, os.path.join(os.path.dirname(os.getcwd()), "shared"))
import psycopg
import db

BOT = os.environ["LOCKS_BOT"]
expected = {tuple(k.split(".")): v for k, v in json.loads(os.environ["LOCKS_EXPECT"]).items()}
gone = [tuple(k.split(".")) for k in json.loads(os.environ["LOCKS_GONE"])]
bad = []

def check(label, ok, detail=""):
    print(("  ok   " if ok else "  FAIL ") + label + ("" if ok else "  [" + str(detail) + "]"))
    if not ok:
        bad.append(label)

statements = []

class Recording(psycopg.Cursor):
    def execute(self, query, params=None, **kwargs):
        statements.append(query if isinstance(query, str) else bytes(query).decode() if isinstance(query, bytes) else str(query))
        return super().execute(query, params, **kwargs)

def recording_connect(dsn=db.DATABASE_URL):
    return psycopg.connect(dsn, options=f"-c search_path={db.DB_SCHEMA},public", cursor_factory=Recording)

real_connect = db.connect

def init_recorded():
    """init_db() with the SQL it sends written down; returns (ALTERs, catalog lookups)."""
    statements.clear()
    db.connect = recording_connect
    try:
        db.init_db()
    finally:
        db.connect = real_connect
    alters = [s for s in statements if s.lstrip().upper().startswith("ALTER")]
    lookups = [s for s in statements if "information_schema.columns" in s]
    return alters, lookups

def shape(conn):
    rows = conn.execute("SELECT table_name, column_name, data_type, column_default FROM information_schema.columns "
                        "WHERE table_schema = %s", (db.DB_SCHEMA,)).fetchall()
    return {(t, c): (d, default) for t, c, d, default in rows}

def check_shape(label):
    with psycopg.connect(db.DATABASE_URL, autocommit=True) as conn:
        found = shape(conn)
    wrong = {k: (found.get(k, (None,))[0], v) for k, v in expected.items() if found.get(k, (None,))[0] != v}
    check(f"{label}: every column the migrations add is there, with its type", not wrong, wrong)
    check(f"{label}: and the retired columns are gone", not [k for k in gone if k in found], [k for k in gone if k in found])
    if BOT == "anon_bot":
        default = found.get(("user_settings", "knows_reactions"), (None, None))[1]
        check(f"{label}: knows_reactions defaults to TRUE", default is not None and default.lower() == "true", default)

db.ensure_schema()
with psycopg.connect(db.DATABASE_URL, autocommit=True) as conn:
    conn.execute(f'DROP SCHEMA IF EXISTS "{db.DB_SCHEMA}" CASCADE')

# -- a fresh database -------------------------------------------------------------
alters, lookups = init_recorded()
check("a fresh database is built", True)
check_shape("fresh")

# -- a second start ---------------------------------------------------------------
alters, lookups = init_recorded()
check("a second init_db() issues no ALTER", not alters, alters)
check("... and did look in the catalog first", len(lookups) >= len(expected), len(lookups))
check_shape("second run")

# -- a second start, while every table is being read -------------------------------
with psycopg.connect(db.DATABASE_URL, autocommit=True) as lister:
    tables = [r[0] for r in lister.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = %s AND table_type = 'BASE TABLE'",
        (db.DB_SCHEMA,)).fetchall()]
reader = psycopg.connect(db.DATABASE_URL, options=f"-c search_path={db.DB_SCHEMA},public")
for table in tables:
    reader.execute(f'SELECT count(*) FROM "{db.DB_SCHEMA}"."{table}"').fetchone()   # ACCESS SHARE, held to the end of the transaction
done = threading.Event()
errors = []
def run():
    try:
        db.init_db()
    except Exception as exc:
        errors.append(exc)
    finally:
        done.set()
thread = threading.Thread(target=run, daemon=True)
started = time.monotonic()
thread.start()
finished = done.wait(25)
took = time.monotonic() - started
reader.rollback()
reader.close()
thread.join(60)
check("a second init_db() does not wait behind readers of every table", finished and not errors,
      f"{'finished' if finished else 'BLOCKED'} after {took:.1f} s; {errors}")

# -- a database from before the migrations ------------------------------------------
with psycopg.connect(db.DATABASE_URL, autocommit=True, options=f"-c search_path={db.DB_SCHEMA},public") as conn:
    conn.execute("ALTER TABLE star_transactions ALTER COLUMN amount_stars TYPE INTEGER")
    for (table, column) in expected:
        if (table, column) != ("star_transactions", "amount_stars"):
            conn.execute(f'ALTER TABLE "{table}" DROP COLUMN "{column}"')
    for (table, column) in gone:
        conn.execute(f'ALTER TABLE "{table}" ADD COLUMN "{column}" TEXT')
before = len(expected) + len(gone)
alters, lookups = init_recorded()
check("an old database is brought forward", True)
check_shape("migrated")
check("... by ALTERs, because there was something to do", len(alters) >= before, (len(alters), before))
alters, lookups = init_recorded()
check("and the run after that is quiet again", not alters, alters)

if BOT == "anon_bot":
    # The column is there but the default is not (the state between the two
    # statements of the original migration): only the default is put back.
    with psycopg.connect(db.DATABASE_URL, autocommit=True, options=f"-c search_path={db.DB_SCHEMA},public") as conn:
        conn.execute("ALTER TABLE user_settings ALTER COLUMN knows_reactions DROP DEFAULT")
    alters, lookups = init_recorded()
    check("a column without its default gets the default, and only that",
          len(alters) == 1 and "SET DEFAULT TRUE" in alters[0], alters)
    check_shape("default restored")

db.close_pool()
with psycopg.connect(db.DATABASE_URL, autocommit=True) as conn:
    conn.execute(f'DROP SCHEMA IF EXISTS "{db.DB_SCHEMA}" CASCADE')
sys.exit(1 if bad else 0)
'''

FAMILY_CHILD = r'''
import os, sys, threading, time
sys.path.insert(0, os.getcwd())
sys.path.insert(1, os.path.join(os.path.dirname(os.getcwd()), "shared"))
import psycopg
import db
import family_link

bad = []

def check(label, ok, detail=""):
    print(("  ok   " if ok else "  FAIL ") + label + ("" if ok else "  [" + str(detail) + "]"))
    if not ok:
        bad.append(label)

ADDED = {("heartbeats", "commands"), ("usage_samples", "sleepable_seconds"), ("usage_samples", "max_gap_seconds"),
         ("usage_samples", "jobs_ok"), ("usage_samples", "jobs_failed"), ("problem_reports", "shared_at"),
         ("problem_reports", "user_id"), ("problem_reports", "username"), ("problem_reports", "user_lang"),
         ("problem_reports", "chat_kind"), ("problem_reports", "seen"), ("problem_reports", "level")}

def columns():
    with psycopg.connect(db.DATABASE_URL, autocommit=True) as conn:
        return {(t, c) for t, c in conn.execute(
            "SELECT table_name, column_name FROM information_schema.columns WHERE table_schema = 'family'").fetchall()}

def run_recorded():
    seen = []
    real = psycopg.Cursor.execute
    def recording(self, query, params=None, **kwargs):
        seen.append(query if isinstance(query, str) else str(query))
        return real(self, query, params, **kwargs)
    psycopg.Cursor.execute = recording
    try:
        family_link.init_family_schema()
    finally:
        psycopg.Cursor.execute = real
    return [s for s in seen if s.lstrip().upper().startswith("ALTER")], [s for s in seen if "information_schema.columns" in s]

with psycopg.connect(db.DATABASE_URL, autocommit=True) as conn:
    conn.execute('DROP SCHEMA IF EXISTS family CASCADE')
db.ensure_schema()
family_link.init_family_schema()
check("a fresh family schema has every column the migrations add", ADDED <= columns(), ADDED - columns())
alters, lookups = run_recorded()
check("a second init_family_schema() issues no ALTER", not alters, alters)
check("... and did look in the catalog first", len(lookups) >= len(ADDED), len(lookups))

with psycopg.connect(db.DATABASE_URL, autocommit=True) as lister:
    tables = [r[0] for r in lister.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'family' AND table_type = 'BASE TABLE'").fetchall()]
reader = psycopg.connect(db.DATABASE_URL)
for table in tables:
    reader.execute(f'SELECT count(*) FROM family."{table}"').fetchone()
done = threading.Event()
errors = []
def run():
    try:
        family_link.init_family_schema()
    except Exception as exc:
        errors.append(exc)
    finally:
        done.set()
threading.Thread(target=run, daemon=True).start()
started = time.monotonic()
finished = done.wait(25)
took = time.monotonic() - started
reader.rollback()
reader.close()
check("... and does not wait behind readers of every family table", finished and not errors,
      f"{'finished' if finished else 'BLOCKED'} after {took:.1f} s; {errors}")

with psycopg.connect(db.DATABASE_URL, autocommit=True) as conn:
    for table, column in ADDED:
        conn.execute(f'ALTER TABLE family."{table}" DROP COLUMN "{column}"')
alters, lookups = run_recorded()
check("a family schema from before them is brought forward", ADDED <= columns() and len(alters) >= len(ADDED),
      (ADDED - columns(), len(alters)))
alters, lookups = run_recorded()
check("... and is then quiet", not alters, alters)

db.close_pool()
with psycopg.connect(db.DATABASE_URL, autocommit=True) as conn:
    conn.execute('DROP SCHEMA IF EXISTS family CASCADE')
sys.exit(1 if bad else 0)
'''


def main() -> int:
    import psycopg
    failures = 0
    runs = [(bot, CHILD, bot, EXPECT[bot]) for bot in EXPECT]
    runs.append(("family schema (shared/family_link.py, via manager_bot)", FAMILY_CHILD, "manager_bot", None))
    for title, child, bot, expect in runs:
        schema = f"{bot}_locks"
        print(f"== {title} ({schema if expect else 'family'}) ==")
        env = dict(os.environ)
        env.update({
            "DATABASE_URL": DSN,
            "DB_SCHEMA": schema,
            "LOCKS_BOT": bot,
            "ENV_FILES": "off",
            "PYTHONIOENCODING": "utf-8",
        })
        if expect:
            env["LOCKS_EXPECT"] = json.dumps({f"{t}.{c}": v for (t, c), v in expect[0].items()})
            env["LOCKS_GONE"] = json.dumps([f"{t}.{c}" for t, c in expect[1]])
        proc = subprocess.run(
            [sys.executable, "-c", child], cwd=ROOT / bot, env=env,
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600,
        )
        print(proc.stdout.rstrip())
        if proc.returncode != 0:
            failures += 1
            print(proc.stderr.rstrip())
        with psycopg.connect(DSN, autocommit=True) as conn:
            conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
    print("\nFAILURES:", failures)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
