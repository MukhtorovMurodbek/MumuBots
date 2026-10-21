"""DownloaderBot's sent_files table against a real Postgres: the rows the file-id store keeps. Needs Postgres.

    python tests/sent_files_db_scenarios.py

downloader_bot/sent_files.py and bot._resend_by_file_id keep the file ids Telegram gave what the bot sent, so a post
is sent again without being downloaded again (tests/test_sent_files.py checks the flow with the five database
functions faked in memory). This is the SQL, which a fake cannot check:

  * init_db() creates the table with exactly post_key, items, confirmed_at and last_used -- nothing about a
    person -- with the key as primary key; running it twice changes nothing and keeps the rows;
  * sent_files_put stores a row (confirmed now, used today) and sent_files_get reads it back; the upsert
    replaces the items and renews both; one row per key;
  * sent_files_used touches the day and not the confirmation; sent_files_confirmed touches both; neither makes
    a row that is not there; sent_files_drop deletes it, and a missing one is no error;
  * confirmed_at is a time with a zone, last_used is a day; the stored time makes a fresh row fresh and a
    13-hour-old one stale for sent_files.is_fresh;
  * housekeeping (db.prune_old_data, the job family_link._prune runs) deletes rows unused for more than
    DBOT_FILEID_KEEP_DAYS days, keeps the rest, and touches nothing else; with 30 days it is 30;
  * /deletemydata (db.erase_user) leaves the table alone: there is nothing of a person in it.

Run in two session time zones either side of UTC (the day is the session's day, and the count of days is taken in
the same session).

It creates and drops a schema of its own, so it refuses to run against anything but a database on this machine --
the scratch one in TempTools/postgres (`source TempTools/postgres/pg.sh; pg_start`).
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
SCHEMA = "downloader_bot_sentfiles"

CHILD = r'''
import os, sys
from datetime import datetime, timedelta, timezone
sys.path.insert(0, os.getcwd())
sys.path.insert(1, os.path.join(os.path.dirname(os.getcwd()), "shared"))
import psycopg
import db
import sent_files

bad = []


def check(label, ok, detail=""):
    print(("  ok   " if ok else "  FAIL ") + label + ("" if ok else "  [" + str(detail) + "]"))
    if not ok:
        bad.append(label)


def q(sql, params=()):
    with psycopg.connect(db.DATABASE_URL, autocommit=True, options="-c search_path=" + db.DB_SCHEMA + ",public") as conn:
        cursor = conn.execute(sql, params)
        return cursor.fetchall() if cursor.description else []


def row(key):
    found = q("SELECT items, confirmed_at, last_used FROM sent_files WHERE post_key = %s", (key,))
    return found[0] if found else None


def count():
    return q("SELECT count(*) FROM sent_files")[0][0]


def today():
    return q("SELECT CURRENT_DATE")[0][0]


def db_now():
    return q("SELECT now()")[0][0]


q('DROP SCHEMA IF EXISTS "' + db.DB_SCHEMA + '" CASCADE')
db.init_db()
zone = q("SHOW timezone")[0][0]
print("== sent_files (session time zone " + zone + ") ==")

columns = {c: (t, n) for c, t, n in q(
    "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
    "WHERE table_schema = %s AND table_name = 'sent_files'", (db.DB_SCHEMA,))}
check("exactly post_key, items, confirmed_at and last_used: nothing about a person",
      columns == {"post_key": ("text", "NO"), "items": ("text", "NO"),
                  "confirmed_at": ("timestamp with time zone", "NO"), "last_used": ("date", "NO")}, columns)
key = [r[0] for r in q(
    "SELECT a.attname FROM pg_index i JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey) "
    "WHERE i.indrelid = (%s || '.sent_files')::regclass AND i.indisprimary", ('"' + db.DB_SCHEMA + '"',))]
check("the post key is the primary key", key == ["post_key"], key)
default = q("SELECT column_default FROM information_schema.columns WHERE table_schema = %s AND table_name = 'sent_files' "
            "AND column_name = 'last_used'", (db.DB_SCHEMA,))[0][0]
check("last_used defaults to today", default is not None and "CURRENT_DATE" in default.upper(), default)

check("nothing there yet: get is None", db.sent_files_get("k1") is None)
db.sent_files_put("k1", '[["video","AAA"]]')
found = db.sent_files_get("k1")
check("put, then get: the items text and a time", found is not None and found[0] == '[["video","AAA"]]' and isinstance(found[1], datetime), found)
check("...a time with a zone, within seconds of now", found is not None and found[1].tzinfo is not None
      and abs((db_now() - found[1]).total_seconds()) < 30, found)
check("...used today", row("k1")[2] == today(), row("k1"))
check("a row confirmed just now is fresh for sent_files.is_fresh, one confirmed 13 hours ago is not",
      sent_files.is_fresh(found[1], datetime.now(timezone.utc)) and not sent_files.is_fresh(found[1] - timedelta(hours=13), datetime.now(timezone.utc)))
db.init_db()
check("a second init_db() keeps the rows (and does not fail)", count() == 1 and row("k1") is not None)

q("UPDATE sent_files SET confirmed_at = now() - interval '2 days', last_used = CURRENT_DATE - 5 WHERE post_key = 'k1'")
db.sent_files_put("k1", '[["photo","BBB"],["photo","CCC"]]')
after = row("k1")
check("the upsert replaces the items", after[0] == '[["photo","BBB"],["photo","CCC"]]', after)
check("...renews the confirmation and the day", abs((db_now() - after[1]).total_seconds()) < 30 and after[2] == today(), after)
check("...and is still one row", count() == 1)

q("UPDATE sent_files SET confirmed_at = now() - interval '2 days', last_used = CURRENT_DATE - 5 WHERE post_key = 'k1'")
old_confirmed = row("k1")[1]
db.sent_files_used("k1")
after = row("k1")
check("used: today's day", after[2] == today(), after)
check("...and the confirmation untouched", after[1] == old_confirmed, (after[1], old_confirmed))
q("UPDATE sent_files SET confirmed_at = now() - interval '2 days', last_used = CURRENT_DATE - 5 WHERE post_key = 'k1'")
db.sent_files_confirmed("k1")
after = row("k1")
check("confirmed: the confirmation is now, and the day today", abs((db_now() - after[1]).total_seconds()) < 30 and after[2] == today(), after)
db.sent_files_used("nope")
db.sent_files_confirmed("nope")
check("neither makes a row that is not there", row("nope") is None and count() == 1)
db.sent_files_put("other", '[["video","ZZZ"]]')
db.sent_files_drop("k1")
check("drop deletes it, and only it", row("k1") is None and db.sent_files_get("k1") is None and count() == 1 and row("other") is not None)
db.sent_files_drop("other")
db.sent_files_drop("k1")
check("dropping a row that is not there is no error", count() == 0)

q("INSERT INTO sent_files (post_key, items, confirmed_at) VALUES ('d', '[]', now())")
check("a row made without a day gets today", row("d")[2] == today(), row("d"))
q("TRUNCATE sent_files")

print("== the sweep ==")
for name, age in (("today", 0), ("d89", 89), ("d90", 90), ("d91", 91), ("d200", 200)):
    q("INSERT INTO sent_files (post_key, items, confirmed_at, last_used) VALUES (%s, '[[\"video\",\"x\"]]', now(), CURRENT_DATE - %s)", (name, age))
q("INSERT INTO activity_events (user_id, occurred_at) VALUES (1, now())")
others = q("SELECT count(*) FROM activity_events")[0][0]
check("the sweep keeps 90 days by default", db.SENT_FILES_KEEP_DAYS == 90, db.SENT_FILES_KEEP_DAYS)
removed = db.prune_old_data()
left = sorted(r[0] for r in q("SELECT post_key FROM sent_files"))
check("rows unused for more than 90 days are deleted, the rest kept (90 days is kept)", left == ["d89", "d90", "today"], left)
check("it reports them in its count", removed >= 2, removed)
check("and touches nothing else", q("SELECT count(*) FROM activity_events")[0][0] == others)
db.SENT_FILES_KEEP_DAYS = 30
db.prune_old_data()
left = sorted(r[0] for r in q("SELECT post_key FROM sent_files"))
check("with DBOT_FILEID_KEEP_DAYS=30 it is 30", left == ["today"], left)
q("INSERT INTO sent_files (post_key, items, confirmed_at, last_used) VALUES ('d30', '[[\"video\",\"x\"]]', now(), CURRENT_DATE - 30)")
q("INSERT INTO sent_files (post_key, items, confirmed_at, last_used) VALUES ('d31', '[[\"video\",\"x\"]]', now(), CURRENT_DATE - 31)")
db.prune_old_data()
check("...30 days old is kept, 31 is not", sorted(r[0] for r in q("SELECT post_key FROM sent_files")) == ["d30", "today"],
      q("SELECT post_key FROM sent_files"))
db.SENT_FILES_KEEP_DAYS = 90

print("== /deletemydata ==")
before = q("SELECT post_key, items, confirmed_at, last_used FROM sent_files ORDER BY post_key")
db.erase_user(1)
check("erasing a person leaves the table as it was", q("SELECT post_key, items, confirmed_at, last_used FROM sent_files ORDER BY post_key") == before)

q('DROP SCHEMA IF EXISTS "' + db.DB_SCHEMA + '" CASCADE')
sys.exit(1 if bad else 0)
'''


def main() -> int:
    host = DSN.split("@")[-1].split("/")[0].split(":")[0]
    if host not in ("localhost", "127.0.0.1", "[::1]", "::1"):
        print(f"Refusing to run: this drops a schema, and {host!r} is not this machine.")
        return 2
    failures = 0
    for zone in ("Pacific/Kiritimati", "Pacific/Pago_Pago", "UTC"):
        env = dict(os.environ)
        env.update({"DATABASE_URL": DSN, "DB_SCHEMA": SCHEMA, "ENV_FILES": "off", "DBOT_TOKEN": "1:AA",
                    "PYTHONIOENCODING": "utf-8", "PGTZ": zone})
        env.pop("DBOT_FILEID_KEEP_DAYS", None)
        proc = subprocess.run([sys.executable, "-c", CHILD], cwd=ROOT / "downloader_bot", env=env,
                              capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
        print(proc.stdout.rstrip())
        if proc.returncode != 0:
            failures += 1
            print(proc.stderr.rstrip())
    import psycopg
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute(f'DROP SCHEMA IF EXISTS "{SCHEMA}" CASCADE')
    print("\nFAILURES:", failures)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
