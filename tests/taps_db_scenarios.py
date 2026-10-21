"""family.button_taps against a real Postgres: the rows the button-tap counts are kept in. Needs Postgres.

    python tests/taps_db_scenarios.py

shared_features.note_tap counts the taps of a bot's buttons in memory, and every USAGE_SAMPLE_MINUTES the
usage sample job swaps the counts out and family_link.record_taps writes them (tests/test_button_taps.py
checks the counting and the swap with no database). This is the SQL, which a stub cannot check:

  * the table is created like family.usage_samples (CREATE TABLE IF NOT EXISTS, no ALTER) with exactly
    bot_id, day, button and taps -- nothing about a person -- and (bot_id, day, button) as its key; a second
    init_family_schema() changes nothing and keeps the rows;
  * a flush adds to the day's row (taps = taps + the new count), a second flush adds again, and another bot's
    row for the same button is a row of its own;
  * the day is the UTC day, whatever time zone the database session runs in (checked in two zones either
    side of UTC);
  * what is not a key and a positive count is ignored; a bot that is not attached to the family writes nothing;
  * button_taps_history(days) is today and the days-1 before it (a tap 7 days ago is not in the last 7 days,
    it is in the last 8), summed per bot and button, the most tapped first within a bot;
  * housekeeping (the same job that prunes usage_samples, with the same USAGE_RETENTION_DAYS) deletes a bot's
    own rows older than that and no other bot's.

It drops and recreates the `family` schema, so it refuses to run against anything but a database on this
machine -- the scratch one in TempTools/postgres (`source TempTools/postgres/pg.sh; pg_start`).
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

CHILD = r'''
import os, sys
from datetime import datetime, timezone
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


def q(sql, params=()):
    with psycopg.connect(db.DATABASE_URL, autocommit=True) as conn:
        cursor = conn.execute(sql, params)
        return cursor.fetchall() if cursor.description else []


def rows(bot=None):
    return {(b, str(d), k): n for b, d, k, n in q(
        "SELECT bot_id, day, button, taps FROM family.button_taps" + (" WHERE bot_id = %s" if bot else ""),
        (bot,) if bot else ())}


def utc_today():
    return q("SELECT (now() AT TIME ZONE 'UTC')::date")[0][0]


def put(bot, offset, button, taps):
    q("INSERT INTO family.button_taps (bot_id, day, button, taps) "
      "VALUES (%s, (now() AT TIME ZONE 'UTC')::date - %s, %s, %s)", (bot, offset, button, taps))


q("DROP SCHEMA IF EXISTS family CASCADE")
family_link.init_family_schema()
family_link._enabled = True
family_link._bot_id = "downloaderbot"
zone = q("SHOW timezone")[0][0]
print(f"== button_taps (session time zone {zone}) ==")

columns = {c: (t, n) for c, t, n in q(
    "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
    "WHERE table_schema = 'family' AND table_name = 'button_taps'")}
check("exactly bot_id, day, button and taps: nothing about a person",
      columns == {"bot_id": ("text", "NO"), "day": ("date", "NO"), "button": ("text", "NO"), "taps": ("integer", "NO")},
      columns)
key = [r[0] for r in q(
    "SELECT a.attname FROM pg_index i JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey) "
    "WHERE i.indrelid = 'family.button_taps'::regclass AND i.indisprimary")]
check("(bot_id, day, button) is the key", sorted(key) == ["bot_id", "button", "day"], key)

family_link.record_taps({"help:full": 2, "redo": 3})
today = str(utc_today())
check("a flush writes a row per button for today (UTC)",
      rows("downloaderbot") == {("downloaderbot", today, "help:full"): 2, ("downloaderbot", today, "redo"): 3}, rows())
family_link.record_taps({"help:full": 5, "convmore": 1})
check("the next flush adds to the day's row, and a new button is a new row",
      rows("downloaderbot") == {("downloaderbot", today, "help:full"): 7, ("downloaderbot", today, "redo"): 3,
                                ("downloaderbot", today, "convmore"): 1}, rows())
family_link._bot_id = "convertbot"
family_link.record_taps({"help:full": 1})
family_link._bot_id = "downloaderbot"
check("another bot's tap of the same button is a row of its own",
      rows("convertbot") == {("convertbot", today, "help:full"): 1} and rows("downloaderbot")[("downloaderbot", today, "help:full")] == 7)
family_link.init_family_schema()
check("a second init_family_schema() keeps the rows (and does not fail)", len(rows()) == 4, rows())

before = rows()
family_link.record_taps({"ok": 1, 5: 2, "zero": 0, "negative": -3, "boolean": True, "float": 1.5, "text": "3"})
after = rows()
check("only a text key with a positive whole count is written",
      {k for k in after if k not in before} == {("downloaderbot", today, "ok")}, set(after) - set(before))
before = rows()
family_link.record_taps({})
family_link.record_taps({"x": 0})
check("nothing to write: nothing written", rows() == before)
family_link._enabled = False
family_link.record_taps({"never": 1})
family_link._enabled = True
check("a bot that is not attached to the family writes nothing", ("downloaderbot", today, "never") not in rows())

now_utc = datetime.now(timezone.utc).date()
check("the day is the UTC day, in whatever zone the session runs",
      str(now_utc) in (today, str(utc_today())) , (now_utc, today, zone))

q("TRUNCATE family.button_taps")
put("convertbot", 0, "old", 1)
put("convertbot", 6, "old", 10)
put("convertbot", 7, "old", 100)
put("convertbot", 0, "big", 50)
put("stickerbot", 3, "zzz", 4)
put("stickerbot", 3, "aaa", 4)
put("stickerbot", 30, "ancient", 9)
def history(days):
    return [(r["bot_id"], r["button"], r["taps"]) for r in family_link.button_taps_history(days)]
check("the last day is today only", history(1) == [("convertbot", "big", 50), ("convertbot", "old", 1)], history(1))
check("the last 7 days are today and the six before it: a tap 7 days ago is not in them",
      history(7) == [("convertbot", "big", 50), ("convertbot", "old", 11), ("stickerbot", "aaa", 4), ("stickerbot", "zzz", 4)],
      history(7))
check("the last 8 days have it", ("convertbot", "old", 111) in history(8), history(8))
check("summed per bot and button, bots in order, the most tapped first and ties by name",
      history(90) == [("convertbot", "old", 111), ("convertbot", "big", 50), ("stickerbot", "ancient", 9),
                      ("stickerbot", "aaa", 4), ("stickerbot", "zzz", 4)], history(90))
family_link._enabled = False
check("not attached: no history", family_link.button_taps_history(7) == [])
family_link._enabled = True

q("TRUNCATE family.button_taps")
for offset in (0, 44, 45, 46, 100):
    put("downloaderbot", offset, "button", 1)
put("convertbot", 100, "button", 1)
put("convertbot", 2, "button", 1)
check("the retention is the usage samples' own", family_link.USAGE_RETENTION_DAYS == 45, family_link.USAGE_RETENTION_DAYS)
result = family_link._prune_family_rows()
left = sorted((b, (utc_today() - __import__("datetime").date.fromisoformat(d)).days) for b, d, k in rows())
check("housekeeping reports how many tap rows it removed", len(result) == 4 and result[3] == 2, result)
check("it keeps this bot's rows up to 45 days old and deletes the older ones; another bot's rows are its own to prune",
      left == [("convertbot", 2), ("convertbot", 100), ("downloaderbot", 0), ("downloaderbot", 44), ("downloaderbot", 45)], left)
db.init_db()                                   # the bot's own tables, which the same housekeeping tidies
check("and says so in its log line", "button tap row(s)" in family_link._prune())

q("DROP SCHEMA IF EXISTS family CASCADE")
sys.exit(1 if bad else 0)
'''


def main() -> int:
    host = DSN.split("@")[-1].split("/")[0].split(":")[0]
    if host not in ("localhost", "127.0.0.1", "[::1]", "::1"):
        print(f"Refusing to run: this drops the family schema, and {host!r} is not this machine.")
        return 2
    failures = 0
    for zone in ("Pacific/Kiritimati", "Pacific/Pago_Pago", "UTC"):
        env = dict(os.environ)
        env.update({"DATABASE_URL": DSN, "DB_SCHEMA": "manager_bot_taps", "ENV_FILES": "off",
                    "PYTHONIOENCODING": "utf-8", "PGTZ": zone})
        proc = subprocess.run([sys.executable, "-c", CHILD], cwd=ROOT / "manager_bot", env=env,
                              capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
        print(proc.stdout.rstrip())
        if proc.returncode != 0:
            failures += 1
            print(proc.stderr.rstrip())
    import psycopg
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute('DROP SCHEMA IF EXISTS "manager_bot_taps" CASCADE')
    print("\nFAILURES:", failures)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
