"""ManagerBot's unique-people counter for the 220 ⚡ gift, run for real against a Postgres.

    python tests/promo_counter_scenarios.py

`db.unique_people(schemas)` is the number the owner is told about when the bots "pass 220 unique users", and what
/users and /promo show. It is SQL over other bots' tables, which only a real database can check: a typo in it is
a guess no reading catches (see erase_scenarios.py for the same reason). Throwaway schemas are built here, shaped
the way the bots' are (activity_events everywhere, user_settings in three bots and settings in DownloaderBot, which
is the one with a different name), seeded with people who overlap, and the count has to be each person once:

  * the same person in two bots, or in both tables of one bot, is one;
  * activity_events alone is not enough: somebody who only has a settings row counts (the mutant that reads
    activity_events only gets a smaller number);
  * `settings` counts only where it has a user_id column (a table of that name holding something else is skipped);
  * zero, negative ids and the three that are not people (Telegram, anonymous admins, channels) are left out;
  * a schema with none of the tables, and a schema that does not exist, are skipped without error;
  * schema names are quoted (a mixed-case one with a dash works), and nothing is written;
  * promo_seen_count: the people AnonBot's table says were shown an announcement, per announcement, and None when
    the schema has no such table.

Needs a Postgres (TEST_DATABASE_URL, or the local default). No network. To see a check catch breakage, copy the
folders into a scratch folder, break the copy and run this again from there.
"""
import os
import pathlib
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "manager_bot"))
sys.path.insert(1, str(ROOT / "shared"))
os.chdir(ROOT / "manager_bot")

# Never whatever DATABASE_URL the shell happens to hold: this test creates and drops schemas.
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/botfamily_test")
os.environ["DB_SCHEMA"] = "manager_bot_promosim"
os.environ["ENV_FILES"] = "off"
for var in ("MBOT", "ABOT", "CBOT", "DBOT", "SBOT"):
    os.environ.setdefault(f"{var}_TOKEN", "1:AA")
    os.environ.setdefault(f"{var}_USERNAME", "test")

import db  # noqa: E402  (ManagerBot's)
import family_link  # noqa: E402

failures = []
checks = 0


def check(label, ok, detail=""):
    global checks
    checks += 1
    print(("  ok   " if ok else "  FAIL ") + label + (f"\n       {detail}" if not ok and detail != "" else ""))
    if not ok:
        failures.append(label)


A, B, C, D, E = "pc_a_bot", "pc_b_bot", "pc_c_bot", "pc_d_bot", "pc_e_bot"
MIXED = "PC-Mixed Bot"
MISSING = "pc_missing_bot"
SCHEMAS = [A, B, C, D, E, MIXED]
TELEGRAM, ANON_ADMINS, CHANNELS = sorted(family_link._GIFT_NOT_PEOPLE)[0], 1087968824, 136817688


def build():
    with db.pooled() as conn:
        for schema in SCHEMAS:
            conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            conn.execute(f'CREATE SCHEMA "{schema}"')
        # AnonBot, ConvertBot, StickerBot: user_settings. DownloaderBot: settings.
        conn.execute(f'CREATE TABLE "{A}".activity_events (id BIGSERIAL PRIMARY KEY, user_id BIGINT NOT NULL, occurred_at TIMESTAMPTZ NOT NULL DEFAULT now())')
        conn.execute(f'CREATE TABLE "{A}".user_settings (user_id BIGINT PRIMARY KEY, language TEXT)')
        conn.execute(f'CREATE TABLE "{A}".promo_seen (user_id BIGINT NOT NULL, promo TEXT NOT NULL, seen_at TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY (user_id, promo))')
        conn.execute(f'CREATE TABLE "{B}".activity_events (id BIGSERIAL PRIMARY KEY, user_id BIGINT NOT NULL, occurred_at TIMESTAMPTZ NOT NULL DEFAULT now())')
        conn.execute(f'CREATE TABLE "{B}".settings (user_id BIGINT PRIMARY KEY, caption_enabled INTEGER NOT NULL DEFAULT 1)')
        conn.execute(f'CREATE TABLE "{C}".activity_events (id BIGSERIAL PRIMARY KEY, user_id BIGINT NOT NULL, occurred_at TIMESTAMPTZ NOT NULL DEFAULT now())')
        # A table that goes by the name but is not a list of people.
        conn.execute(f'CREATE TABLE "{D}".settings (key TEXT PRIMARY KEY, value TEXT)')
        conn.execute(f"INSERT INTO \"{D}\".settings VALUES ('theme', 'dark')")
        conn.execute(f'CREATE TABLE "{MIXED}".activity_events (id BIGSERIAL PRIMARY KEY, user_id BIGINT NOT NULL, occurred_at TIMESTAMPTZ NOT NULL DEFAULT now())')

        def fill(table, ids):
            conn.cursor().executemany(f"INSERT INTO {table} (user_id) VALUES (%s)", [(i,) for i in ids])

        # Overlaps on purpose: 1 is in A and B, 2 in A and B, 3 in both tables of A, 5 in B and C.
        fill(f'"{A}".activity_events', [1, 2, 3, 3, 3, 0, -5, 777000])
        fill(f'"{A}".user_settings', [3, 4, ANON_ADMINS])
        fill(f'"{B}".activity_events', [2, 5, 5])
        fill(f'"{B}".settings', [1, 6, CHANNELS])
        fill(f'"{C}".activity_events', [5, 7, TELEGRAM, 1087968824])
        fill(f'"{MIXED}".activity_events', [8])
        conn.cursor().executemany(f'INSERT INTO "{A}".promo_seen (user_id, promo) VALUES (%s, %s)',
                         [(1, "gift-220-users"), (2, "gift-220-users"), (3, "gift-220-users"), (3, "another"), (4, "another")])
        conn.commit()


def drop():
    with db.pooled() as conn:
        for schema in SCHEMAS:
            conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        conn.execute(f'DROP SCHEMA IF EXISTS "{os.environ["DB_SCHEMA"]}" CASCADE')
        conn.commit()


def state():
    """Every row of the seeded tables, to see that counting wrote nothing."""
    with db.pooled() as conn:
        return [conn.execute(f'SELECT count(*), COALESCE(sum(user_id), 0) FROM "{schema}".{table}').fetchone()
                for schema, table in ((A, "activity_events"), (A, "user_settings"), (A, "promo_seen"), (B, "activity_events"),
                                      (B, "settings"), (C, "activity_events"), (MIXED, "activity_events"))]


db.init_db()
try:
    build()
    print("\n== unique_people ==")
    before = state()
    check("the three that are not people are the ones family_link names",
          family_link._GIFT_NOT_PEOPLE == {TELEGRAM, ANON_ADMINS, CHANNELS} == {777000, 1087968824, 136817688})
    check("everything: each of 1 to 8 once, however many bots and tables they are in",
          db.unique_people([A, B, C, D, E, MIXED]) == 8, db.unique_people([A, B, C, D, E, MIXED]))
    check("... in the order the schemas are given, or twice in the list, the same",
          db.unique_people([MIXED, C, B, A]) == 8 and db.unique_people([A, A, B, B, C, MIXED]) == 8)
    check("one bot with two tables (A: activity 1, 2, 3 and settings 3, 4): 4, the person in both tables once",
          db.unique_people([A]) == 4, db.unique_people([A]))
    check("two bots that share people (A and B: 1, 2, 3, 4 and 2, 5, 1, 6): 6", db.unique_people([A, B]) == 6, db.unique_people([A, B]))
    check("DownloaderBot's settings counts, not only user_settings (B alone: 2, 5, 1, 6 = 4)", db.unique_people([B]) == 4, db.unique_people([B]))
    check("a bot with activity_events only counts them (C: 5 and 7; Telegram's and the anonymous admins' ids left out)",
          db.unique_people([C]) == 2, db.unique_people([C]))
    check("zero and negative ids are not people, nor are Telegram (777000), the anonymous admins and the channels",
          db.unique_people([A]) == 4 and db.unique_people([C]) == 2)
    check("a settings table without a user_id column is skipped (D holds no people)", db.unique_people([D]) == 0)
    check("a schema without any of the tables, and one that does not exist, count nothing and raise nothing",
          db.unique_people([E]) == 0 and db.unique_people([MISSING]) == 0 and db.unique_people([E, MISSING]) == 0)
    check("... and among others they change nothing", db.unique_people([A, E, MISSING, B]) == db.unique_people([A, B]) == 6)
    check("no schemas: 0", db.unique_people([]) == 0)
    check("a schema name with capitals, a dash and a space is quoted and works", db.unique_people([MIXED]) == 1)
    check("it returns a plain int", type(db.unique_people([A, B])) is int)
    check("counting wrote nothing", state() == before, (before, state()))

    print("\n== promo_seen_count ==")
    check("AnonBot's table, for the gift: 3 people were shown it", db.promo_seen_count(A, "gift-220-users") == 3)
    check("... for another announcement: 2", db.promo_seen_count(A, "another") == 2)
    check("... for one nobody was shown: 0", db.promo_seen_count(A, "never") == 0)
    check("a schema with no promo_seen table: None, not 0", db.promo_seen_count(B, "gift-220-users") is None
          and db.promo_seen_count(E, "gift-220-users") is None)
    check("a schema that does not exist: None", db.promo_seen_count(MISSING, "gift-220-users") is None)
    check("the announcement's name is a value, not SQL", db.promo_seen_count(A, "x'; DROP TABLE promo_seen; --") == 0
          and db.promo_seen_count(A, "gift-220-users") == 3)
finally:
    drop()
    db.close_pool()

print()
print(f"{checks} checks, FAILURES: {len(failures)}")
for label in failures:
    print("  -", label)
sys.exit(1 if failures else 0)
