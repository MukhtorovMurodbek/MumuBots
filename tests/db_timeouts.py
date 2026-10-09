"""What a pooled connection ends up with, asked of a real Postgres.

    python tests/db_timeouts.py

tests/test_db_timeouts.py reads the source; this asks the server. Each bot's
db.py is imported in its own subprocess (all five are called db.py), pointed at
the scratch database (TEST_DATABASE_URL, default botfamily_test), and:

  * a pooled connection reports statement_timeout = 1min and
    idle_in_transaction_session_timeout = 2min, search_path still has the bot's
    schema first, and libpq was handed connect_timeout 10, tcp_user_timeout
    30000 and the keepalives;
  * a statement that runs past a lowered limit is cancelled, quickly, and the
    connection is usable afterwards, back on the pool's minute;
  * the same goes for a pooled_read() connection;
  * ManagerBot's /sql still runs under its own ten seconds, not the pool's
    minute, and a query past them is cancelled at ten;
  * the poll lease's connection has the keepalives and the unacknowledged-data
    limit and NEITHER server-side timeout (statement_timeout and
    idle_in_transaction_session_timeout both 0): a server that cut an idle lease
    would drop it.

It uses a schema of its own per bot (<schema>_timeouts) and drops it again, so
it can run beside the other suites' data but not at the same moment as them.
"""
import os
import subprocess
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
DSN = os.environ.get("TEST_DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/botfamily_test")

BOTS = ["sticker_bot", "convert_bot", "downloader_bot", "anon_bot", "manager_bot"]

CHILD = r'''
import os, sys, time
sys.path.insert(0, os.getcwd())
sys.path.insert(1, os.path.join(os.path.dirname(os.getcwd()), "shared"))
import psycopg
import db

BOT = os.environ["TIMEOUTS_BOT"]
bad = []

def check(label, ok, detail=""):
    print(("  ok   " if ok else "  FAIL ") + label + ("" if ok else "  [" + str(detail) + "]"))
    if not ok:
        bad.append(label)

db.ensure_schema()

def settings(conn):
    return (conn.execute("SHOW statement_timeout").fetchone()[0],
            conn.execute("SHOW idle_in_transaction_session_timeout").fetchone()[0],
            conn.execute("SHOW search_path").fetchone()[0])

with db.pooled() as conn:
    statement, idle, path = settings(conn)
    check("statement_timeout is a minute", statement == "1min", statement)
    check("idle_in_transaction_session_timeout is two minutes", idle == "2min", idle)
    check("search_path still has the bot's schema first", path.startswith(db.DB_SCHEMA + ","), path)
    given = conn.info.get_parameters()
    check("libpq was given connect_timeout 10", given.get("connect_timeout") == "10", given.get("connect_timeout"))
    check("... tcp_user_timeout 30000", given.get("tcp_user_timeout") == "30000", given.get("tcp_user_timeout"))
    check("... and the keepalives", (given.get("keepalives"), given.get("keepalives_idle"),
                                     given.get("keepalives_interval"), given.get("keepalives_count"))
          == ("1", "30", "10", "5"), given)

    # A statement past a lowered limit is cancelled at it. (Lowered for this
    # transaction only: the production default stays a minute.)
    conn.execute("SET LOCAL statement_timeout = '300ms'")
    started = time.monotonic()
    try:
        conn.execute("SELECT pg_sleep(8)")
        cancelled = False
    except psycopg.errors.QueryCanceled:
        cancelled = True
    took = time.monotonic() - started
    check("a statement past the limit is cancelled, not waited for", cancelled and took < 4, (cancelled, took))
    conn.rollback()

with db.pooled() as conn:
    check("the connection is usable afterwards, back on the pool's minute",
          conn.execute("SELECT 1").fetchone()[0] == 1 and settings(conn)[0] == "1min", settings(conn))

with db.pooled_read() as conn:
    statement, idle, _ = settings(conn)
    check("a pooled_read() connection has the same limits", (statement, idle) == ("1min", "2min"), (statement, idle))

if BOT == "manager_bot":
    cols, rows = db.run_readonly_query("SELECT current_setting('statement_timeout')")
    check("/sql runs under its own ten seconds, not the pool's minute", rows == [("10s",)], rows)
    started = time.monotonic()
    try:
        db.run_readonly_query("SELECT pg_sleep(30)")
        cancelled = False
    except psycopg.errors.QueryCanceled:
        cancelled = True
    took = time.monotonic() - started
    check("... and a query past them is cancelled at ten", cancelled and 8 < took < 20, (cancelled, took))
    with db.pooled() as conn:
        check("... leaving the pool on its minute", settings(conn)[0] == "1min", settings(conn))

if BOT == "sticker_bot":
    import lifecycle
    check("the poll lease can be taken", lifecycle._try_lease("db-timeouts-test") is True)
    lease = lifecycle._lease_conn
    statement, idle, _ = settings(lease)
    check("the lease connection has no statement_timeout", statement == "0", statement)
    check("... and no idle_in_transaction_session_timeout", idle == "0", idle)
    given = lease.info.get_parameters()
    check("... but a connect timeout, the keepalives and the unacknowledged-data limit",
          (given.get("connect_timeout"), given.get("keepalives"), given.get("keepalives_idle"),
           given.get("keepalives_interval"), given.get("keepalives_count"), given.get("tcp_user_timeout"))
          == ("15", "1", "30", "10", "5", "30000"), given)
    check("... and no options of its own", "options" not in given, given.get("options"))
    lifecycle.release_lease()

db.close_pool()
sys.exit(1 if bad else 0)
'''


def main() -> int:
    import psycopg
    failures = 0
    for bot in BOTS:
        schema = f"{bot}_timeouts"
        print(f"== {bot} ({schema}) ==")
        env = dict(os.environ)
        env.update({
            "DATABASE_URL": DSN,
            "DB_SCHEMA": schema,
            "TIMEOUTS_BOT": bot,
            "ENV_FILES": "off",
            "PYTHONIOENCODING": "utf-8",
        })
        proc = subprocess.run(
            [sys.executable, "-c", CHILD], cwd=ROOT / bot, env=env,
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300,
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
