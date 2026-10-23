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
    the schema has no such table;
  * AnonBot keeps people as pseudonyms (2.1.1) and each real id sealed beside it: the count opens those, so a person
    who is also in ConvertBot is one, a settings row whose seal does not open is skipped with a warning that names
    the table and no id, and on a database not yet converted the answer is the one the old SQL gave.

Needs a Postgres (TEST_DATABASE_URL, or the local default). No network. To see a check catch breakage, copy the
folders into a scratch folder, break the copy and run this again from there.
"""
import base64
import logging
import os
import pathlib
import sys
import tempfile

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
# The data key (shared/vault.py reads it when first imported): a throwaway one unless the run brings its own.
if not os.environ.get("DATA_KEY_FILE"):
    _key_file = pathlib.Path(tempfile.mkdtemp(prefix="promo_counter_")) / "key"
    _key_file.write_text(base64.b64encode(os.urandom(32)).decode("ascii"), encoding="ascii")
    os.environ["DATA_KEY_FILE"] = str(_key_file)

import db  # noqa: E402  (ManagerBot's)
import family_link  # noqa: E402
import vault  # noqa: E402

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


# --- AnonBot's sealed people -------------------------------------------------------------------------------
SEALED_ANON, SEALED_CONVERT, OLD_ANON, OLD_CONVERT = "pc_sealed_anon", "pc_sealed_convert", "pc_old_anon", "pc_old_convert"
SEAL_SCHEMAS = [SEALED_ANON, SEALED_CONVERT, OLD_ANON, OLD_CONVERT]
BOTH, ONLY_ANON, ANON_ACTIVITY, ONLY_CONVERT = 1001, 1002, 1003, 1004     # real ids of the people below
BROKEN, SWAPPED, UNSEALED_PSEUDO = 1005, 1006, 1007
PEOPLE_ONLY_IN_OLD = (2001, 2002, 2003)


def old_unique_people(schemas):
    """The SQL unique_people was before AnonBot's ids were sealed (2.2.0), kept here as the thing to equal on
    data that is not sealed: every id of every activity_events / user_settings / settings that has a user_id."""
    with db.pooled() as conn:
        parts = []
        for table in db.PEOPLE_TABLES:
            for schema in sorted(db._existing_tables(conn, schemas, table)):
                if conn.execute("SELECT 1 FROM information_schema.columns WHERE table_schema = %s AND table_name = %s "
                                "AND column_name = 'user_id'", (schema, table)).fetchone():
                    parts.append(f'SELECT user_id FROM "{schema}".{table}')
        if not parts:
            return 0
        return conn.execute("SELECT COUNT(*) FROM (" + " UNION ".join(parts) + ") AS everyone "
                            "WHERE user_id > 0 AND user_id <> ALL(%s)",
                            (sorted(family_link._GIFT_NOT_PEOPLE),)).fetchone()[0]


def build_sealed():
    P = vault.pseudonym
    with db.pooled() as conn:
        for schema in SEAL_SCHEMAS:
            conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            conn.execute(f'CREATE SCHEMA "{schema}"')
        for schema in (SEALED_ANON, OLD_ANON):
            conn.execute(f'CREATE TABLE "{schema}".anon_conversations (id BIGSERIAL PRIMARY KEY)')
            conn.execute(f'CREATE TABLE "{schema}".activity_events (id BIGSERIAL PRIMARY KEY, user_id BIGINT NOT NULL, '
                         f'occurred_at TIMESTAMPTZ NOT NULL DEFAULT now())')
            conn.execute(f'CREATE TABLE "{schema}".user_settings (user_id BIGINT PRIMARY KEY, language TEXT, user_sealed BYTEA)')
        for schema in (SEALED_CONVERT, OLD_CONVERT):
            conn.execute(f'CREATE TABLE "{schema}".activity_events (id BIGSERIAL PRIMARY KEY, user_id BIGINT NOT NULL, '
                         f'occurred_at TIMESTAMPTZ NOT NULL DEFAULT now())')
            conn.execute(f'CREATE TABLE "{schema}".user_settings (user_id BIGINT PRIMARY KEY, language TEXT)')
        ins = f'INSERT INTO "{SEALED_ANON}".user_settings (user_id, language, user_sealed) VALUES (%s, %s, %s)'
        # Sealed the way AnonBot's conversion and init write them: the pseudonym, and the id sealed as "anon.user".
        for person in (BOTH, ONLY_ANON, ANON_ACTIVITY, 777000, 0):
            conn.execute(ins, (P(person), "en", vault.seal_id(person, "anon.user")))
        conn.execute(ins, (P(BROKEN), "en", b"not a seal at all"))                           # does not open
        conn.execute(ins, (P(SWAPPED), "en", vault.seal_id(ONLY_ANON, "anon.user")))         # opens, but to another row
        conn.execute(ins, (P(UNSEALED_PSEUDO), "en", None))                                  # nothing to open
        # AnonBot's activity is pseudonymized too, and is not read: the settings row of each person is.
        conn.cursor().executemany(f'INSERT INTO "{SEALED_ANON}".activity_events (user_id) VALUES (%s)',
                                  [(P(ANON_ACTIVITY),), (P(BOTH),)])
        conn.cursor().executemany(f'INSERT INTO "{SEALED_CONVERT}".user_settings (user_id) VALUES (%s)',
                                  [(BOTH,), (ONLY_CONVERT,)])
        conn.cursor().executemany(f'INSERT INTO "{SEALED_CONVERT}".activity_events (user_id) VALUES (%s)', [(ONLY_CONVERT,)])
        # A database that has not been converted: plain ids, the sealed column there and empty, and a person with
        # activity and no settings row (which the old count includes, and so must this one).
        conn.cursor().executemany(
            f'INSERT INTO "{OLD_ANON}".user_settings (user_id, language) VALUES (%s, %s)',
            [(BOTH, "en"), (ONLY_ANON, "uz"), (PEOPLE_ONLY_IN_OLD[0], "ru")])
        conn.cursor().executemany(f'INSERT INTO "{OLD_ANON}".activity_events (user_id) VALUES (%s)',
                                  [(ONLY_ANON,), (PEOPLE_ONLY_IN_OLD[1],), (777000,), (-4,)])
        conn.cursor().executemany(f'INSERT INTO "{OLD_CONVERT}".user_settings (user_id) VALUES (%s)',
                                  [(BOTH,), (PEOPLE_ONLY_IN_OLD[2],)])
        conn.commit()


def drop_sealed():
    with db.pooled() as conn:
        for schema in SEAL_SCHEMAS:
            conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        conn.commit()


def seal_state():
    """What the sealed-people schemas hold, to see that counting wrote nothing."""
    with db.pooled() as conn:
        return [conn.execute(f'SELECT count(*), COALESCE(sum(user_id % 1000), 0) FROM "{schema}".{table}').fetchone()
                for schema, table in ((SEALED_ANON, "user_settings"), (SEALED_ANON, "activity_events"),
                                      (SEALED_CONVERT, "user_settings"), (OLD_ANON, "user_settings"))]


class _Warnings(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.WARNING)
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())


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

    print("\n== unique_people: AnonBot's people are sealed ==")
    build_sealed()
    seal_before = seal_state()
    collector = _Warnings()
    db_logger = logging.getLogger(db.__name__)
    db_logger.addHandler(collector)
    try:
        both = db.unique_people([SEALED_ANON, SEALED_CONVERT])
        anon_only = db.unique_people([SEALED_ANON])
        convert_only = db.unique_people([SEALED_CONVERT])
    finally:
        db_logger.removeHandler(collector)
    check("AnonBot's sealed people are opened and counted: 1001, 1002 and 1003, each once (Telegram's 777000 and 0 "
          "are sealed there too and are not people)", anon_only == 3, anon_only)
    check("... the person who is in ConvertBot as well is one person, not two: 1001 to 1004 is 4 (ConvertBot alone: 2)",
          both == 4 and convert_only == 2, (both, convert_only))
    check("... in any order, and among a schema that does not exist, the same",
          db.unique_people([SEALED_CONVERT, SEALED_ANON, MISSING]) == 4)
    check("the activity_events of AnonBot are not needed: its pseudonyms there are not counted as people",
          db.unique_people([SEALED_ANON]) == 3 and seal_state()[1][0] == 2)
    check("a settings row whose seal does not open, one whose seal belongs to another row and one with nothing sealed "
          "are skipped without a crash, and not counted", anon_only == 3 and both == 4)
    check("... with a warning that names the table, and no id and no pseudonym",
          any(SEALED_ANON in line and "user_settings" in line for line in collector.lines)
          and not any(str(n) in line for line in collector.lines
                      for n in (BOTH, ONLY_ANON, ANON_ACTIVITY, ONLY_CONVERT, BROKEN, SWAPPED, UNSEALED_PSEUDO,
                                vault.pseudonym(BOTH), vault.pseudonym(BROKEN), vault.pseudonym(SWAPPED))),
          collector.lines)
    check("... nothing was written or changed by the counting", seal_state() == seal_before, (seal_before, seal_state()))
    old = db.unique_people([OLD_ANON, OLD_CONVERT])
    check("on a database that is not converted (plain ids, the sealed column empty) the answer is the old SQL's: 5",
          old == old_unique_people([OLD_ANON, OLD_CONVERT]) == 5, (old, old_unique_people([OLD_ANON, OLD_CONVERT])))
    check("... a person with activity and no settings row is in it, as before (AnonBot alone: 4)",
          db.unique_people([OLD_ANON]) == old_unique_people([OLD_ANON]) == 4, db.unique_people([OLD_ANON]))
    check("... and for the family built above, whatever the shape, the old SQL and the new agree",
          db.unique_people([A, B, C, D, E, MIXED]) == old_unique_people([A, B, C, D, E, MIXED]) == 8)
    drop_sealed()
finally:
    drop()
    drop_sealed()
    db.close_pool()

print()
print(f"{checks} checks, FAILURES: {len(failures)}")
for label in failures:
    print("  -", label)
sys.exit(1 if failures else 0)
