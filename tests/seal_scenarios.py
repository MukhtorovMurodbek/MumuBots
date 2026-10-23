"""Everything that makes the sealed AnonBot safe to run, on a database made the way 2.1.1 left it.

anon_encryption.py proves AnonBot's own tables. This one proves the pieces
around them (shared/lifecycle.py, shared/family_link.py and
scripts/encrypt_personal_data.py):

    A  the key guard         ensure_data_key: a new database is stamped; a
                             database stamped with another key, an unsealed
                             one, one whose AnonBot stage was never stamped,
                             and a missing key file all end the process
    B  the start-up path     attach() and a real bot's main() end on the same
                             answers, before polling and whether or not the
                             family bus is wanted
    C  saved conversations   runtime_state: nothing readable in the table, the
                             original keys and data back on reading, a row
                             sealed under another key / moved to another key /
                             garbage skipped with a warning, forget_user
    D  the update waitlist   pseudonyms and a seal in the table, real ids out
                             of take_held, a bad row skipped, an older table
                             gets its column and loses nothing
    E  the family conversion unsealed rows deleted, sealed ones kept, verify
                             that is not a rubber stamp, a second run that
                             changes nothing, never a commit
    F  the script            a dry run changes nothing; a real run converts
                             AnonBot's 2.1.0 rows and the saved state and
                             stamps both keys; a second run changes nothing;
                             it refuses another key's database and a database
                             whose bots look alive; it prints nothing personal
    F2 the fingerprint, the vacuum   the key file's fingerprint and the stamped
                             one are printed (never the key); a real run
                             vacuums every converted table and the saved-state
                             and waitlist tables, a dry run none; a vacuum that
                             fails ends in exit 3 after the others are done;
                             --vacuum-only converts nothing and refuses a
                             database not stamped with this key
    G  the key guard's availability   a database that does not answer is waited
                             for (2, 4, 8, 16, 30, 30 seconds, a warning each);
                             only then is it a refusal; a wrong key, unsealed
                             data and a missing key file are refused at once;
                             every refusal is a DataKeyRefused (data_key True)

    python tests/seal_scenarios.py

Needs a Postgres (TEST_DATABASE_URL, or the local default) and a data key
(DATA_KEY_FILE; a throwaway one is made when it is not set). It works in
schemas called sealtest_*, a family of its own, and drops them. No network.
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import io
import logging
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "anon_bot"))
sys.path.insert(1, str(ROOT / "shared"))
sys.path.insert(2, str(ROOT / "scripts"))
sys.path.insert(3, str(ROOT))

DSN = os.environ.get("TEST_DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/botfamily_test")
os.environ["DATABASE_URL"] = DSN
os.environ["SEALTEST_URL"] = DSN
BOT_SCHEMA = "sealtest_anon_bot"
FAMILY = "sealtest_family"
os.environ["DB_SCHEMA"] = BOT_SCHEMA
os.environ["DEPLOY_SAFETY"] = "off"
os.environ.setdefault("ABOT_TOKEN", "1:AA")
os.environ["ABOT_USERNAME"] = "anonbot_test"
os.environ["ABOT_ADMIN_ID"] = "5001"
os.environ.setdefault("SIBLING_BOTS", "")
os.environ["FAMILY_BUS"] = "on"
_tmp = Path(tempfile.mkdtemp(prefix="seal_scenarios_"))
if not os.environ.get("DATA_KEY_FILE"):
    (_tmp / "key_a").write_text(base64.b64encode(os.urandom(32)).decode("ascii"), encoding="ascii")
    os.environ["DATA_KEY_FILE"] = str(_tmp / "key_a")
KEY_A = os.environ["DATA_KEY_FILE"]
KEY_B = str(_tmp / "key_b")
Path(KEY_B).write_text(base64.b64encode(os.urandom(32)).decode("ascii"), encoding="ascii")

import psycopg  # noqa: E402
import vault  # noqa: E402
import db  # noqa: E402
import lifecycle  # noqa: E402
import family_link  # noqa: E402
import encrypt_personal_data as script  # noqa: E402
from psycopg.types.json import Jsonb  # noqa: E402

family_link.FAMILY_SCHEMA = FAMILY

failures: list[str] = []
_checks = 0


def check(label: str, condition: bool, detail: str = "") -> bool:
    global _checks
    _checks += 1
    if condition:
        print(f"  ok   {label}")
    else:
        failures.append(label)
        print(f"  FAIL {label}" + (f"\n       {detail}" if detail else ""))
    return bool(condition)


def section(title: str) -> None:
    print(f"\n== {title} ==")


class _Collect(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record):
        self.records.append(record)

    def text(self, level: int = logging.WARNING) -> str:
        return "\n".join(r.getMessage() for r in self.records if r.levelno >= level)

    def clear(self):
        self.records.clear()


collector = _Collect()
logging.getLogger().addHandler(collector)
logging.getLogger().setLevel(logging.INFO)


def use_key(path: str) -> None:
    """Switch the process to another key file, as if it had been started with it."""
    vault.KEY_FILE = path
    vault._keys = None


# Real-looking Telegram ids: long enough that their digits cannot turn up by
# chance in a pseudonym or in base64.
USER_1, USER_2, USER_3 = 5550012345, 5550067890, 5550099991
GROUP = -1001234567890
NAME = "Zhasmina_SEALTEST"
PACK = "PACKNAME-ZX81-SEALTEST"


def admin():
    return psycopg.connect(DSN, autocommit=True)


def drop_world() -> None:
    db.close_pool()
    with admin() as conn:
        for (schema,) in conn.execute(
                "SELECT nspname FROM pg_namespace WHERE nspname LIKE 'sealtest\\_%' OR nspname LIKE 'sealtest2\\_%'"
        ).fetchall():
            conn.execute(psycopg.sql.SQL("DROP SCHEMA {} CASCADE").format(psycopg.sql.Identifier(schema)))


def new_world() -> None:
    """AnonBot's schema and the family's, as this version creates them, empty."""
    drop_world()
    use_key(KEY_A)
    db.init_db()
    lifecycle.init_state_table()
    family_link.init_family_schema()
    lifecycle.init_waitlist_table()


def one(sql: str, params=()):
    with admin() as conn:
        row = conn.execute(sql, params).fetchone()
    return row[0] if row else None


def stamps() -> dict:
    with admin() as conn:
        return dict(conn.execute(
            f"SELECT key, value FROM {FAMILY}.settings WHERE key = 'data_key:v1' OR key LIKE 'sealed:%'").fetchall())


def run_guard() -> "tuple[bool, str]":
    """(it went on, what it said at WARNING or above)."""
    collector.clear()
    try:
        with family_link._connect() as conn:
            family_link.ensure_data_key(conn)
        return True, collector.text()
    except SystemExit as exc:
        run_guard.last_exit = str(exc)
        return False, collector.text(logging.CRITICAL)


run_guard.last_exit = ""


def put_plain_state(schema: str = BOT_SCHEMA) -> None:
    with admin() as conn:
        conn.execute(psycopg.sql.SQL("INSERT INTO {}.runtime_state (kind, key, value) VALUES ('user', %s, %s)")
                     .format(psycopg.sql.Identifier(schema)), (str(USER_1), Jsonb({"lang": "uz"})))


# ---------------------------------------------------------------------------
# A. the key guard
# ---------------------------------------------------------------------------

def group_a() -> None:
    section("A. the key guard: ensure_data_key")
    fingerprint = vault.fingerprint()

    new_world()
    went_on, said = run_guard()
    got = stamps()
    check("a new, empty database is stamped with this key and with AnonBot's stage",
          went_on and got.get("data_key:v1") == fingerprint and got.get("sealed:anon:v1") == "1", f"{said} {got}")
    went_on, said = run_guard()
    check("and the second bot to start finds the stamp and goes on", went_on, said)

    # (a) another key's database
    new_world()
    with admin() as conn:
        conn.execute(f"INSERT INTO {FAMILY}.settings VALUES ('data_key:v1', '0123456789abcdef'), ('sealed:anon:v1', '1')")
    went_on, said = run_guard()
    check("(a) a database stamped with another key refuses to start", not went_on)
    check("...saying the key file is not the key it was sealed with",
          "not the key this database was sealed with" in said, said)
    check("...and the stamp is left exactly as it was", stamps().get("data_key:v1") == "0123456789abcdef")
    check("...the SystemExit carries the same sentence (the C edition's loader shows it as the reason)",
          not went_on and said and run_guard.last_exit == said, run_guard.last_exit)

    new_world()
    went_on, _ = run_guard()
    use_key(KEY_B)
    went_on_b, said_b = run_guard()
    use_key(KEY_A)
    check("(a) the same database started with a second key file refuses",
          went_on and not went_on_b and "not the key this database was sealed with" in said_b, said_b)

    # (b) no stamp, and something to seal
    cases = {
        "an AnonBot table": lambda conn: conn.execute(
            f"INSERT INTO {BOT_SCHEMA}.anon_links (owner_user_id, token, created_at) VALUES (%s, 'tok', 'now')",
            (USER_1,)),
        "saved conversation state": lambda conn: conn.execute(
            f"INSERT INTO {BOT_SCHEMA}.runtime_state (kind, key, value) VALUES ('user', %s, '{{}}'::jsonb)",
            (str(USER_1),)),
        "the update waitlist": lambda conn: conn.execute(
            f"INSERT INTO {FAMILY}.update_waitlist (bot_id, user_id, chat_id) VALUES ('stickerbot', %s, %s)",
            (USER_1, USER_1)),
    }
    for what, put in cases.items():
        new_world()
        with admin() as conn:
            put(conn)
        went_on, said = run_guard()
        check(f"(b) no stamp and {what} holding a row: refuses", not went_on)
        check(f"(b) ...and says to run the conversion script ({what})",
              "not sealed yet" in said and "encrypt_personal_data.py" in said, said)
        check(f"(b) ...without stamping the database ({what})", stamps() == {}, str(stamps()))

    new_world()
    with admin() as conn:
        conn.execute("CREATE SCHEMA sealtest_anon_two")
        conn.execute("CREATE TABLE sealtest_anon_two.anon_conversations (id BIGSERIAL PRIMARY KEY, owner_user_id BIGINT)")
        conn.execute("INSERT INTO sealtest_anon_two.anon_conversations (owner_user_id) VALUES (1)")
    went_on, said = run_guard()
    check("(b) a row in a second schema that has anon_conversations counts too", not went_on, said)

    new_world()
    with admin() as conn:
        conn.execute("CREATE SCHEMA sealtest_convert_bot")
        conn.execute("CREATE TABLE sealtest_convert_bot.star_transactions (user_id BIGINT, username TEXT)")
        conn.execute("INSERT INTO sealtest_convert_bot.star_transactions VALUES (1, 'somebody')")
    went_on, said = run_guard()
    check("(b) tables of a bot this release does not seal are not looked at", went_on, said)

    # (c) the key matches but the stage was never stamped
    for value, what in ((None, "absent"), ("0", "not 1")):
        new_world()
        with admin() as conn:
            conn.execute(f"INSERT INTO {FAMILY}.settings VALUES ('data_key:v1', %s)", (fingerprint,))
            if value is not None:
                conn.execute(f"INSERT INTO {FAMILY}.settings VALUES ('sealed:anon:v1', %s)", (value,))
        went_on, said = run_guard()
        check(f"(c) the key matches but sealed:anon:v1 is {what}: refuses", not went_on)
        check(f"(c) ...with the same instruction ({what})", "not sealed yet" in said and "encrypt_personal_data.py" in said, said)

    # (d) everything in order, rows and all
    new_world()
    with admin() as conn:
        conn.execute(f"INSERT INTO {FAMILY}.settings VALUES ('data_key:v1', %s), ('sealed:anon:v1', '1')", (fingerprint,))
        conn.execute(f"INSERT INTO {BOT_SCHEMA}.anon_links (owner_user_id, token, created_at) VALUES (%s, 't', 'x')",
                     (vault.pseudonym(USER_1),))
    collector.clear()
    went_on, said = run_guard()
    check("(d) both stamps in order, with sealed rows in the tables: goes on, saying so at INFO",
          went_on and any("Data key checked" in r.getMessage() and r.levelno == logging.INFO
                          for r in collector.records), said)

    # the key file itself
    new_world()
    use_key(str(_tmp / "no_such_key"))
    went_on, said = run_guard()
    use_key(KEY_A)
    check("a missing key file ends the process, saying the key is not usable",
          not went_on and "not usable" in said, said)
    (_tmp / "bad_key").write_text("not a key", encoding="ascii")
    use_key(str(_tmp / "bad_key"))
    went_on, said = run_guard()
    use_key(KEY_A)
    check("so does a file that holds no key", not went_on and "not usable" in said, said)

    # whose schemas count
    check("the real family judges every schema but TestBot's",
          family_link.belongs_to_family("anon_bot", "family") and family_link.belongs_to_family("public", "family")
          and not family_link.belongs_to_family("testbot_anon_bot", "family"))
    check("TestBot's family judges only its own", family_link.belongs_to_family("testbot_anon_bot", "testbot_family")
          and not family_link.belongs_to_family("anon_bot", "testbot_family"))


# ---------------------------------------------------------------------------
# B. the start-up path
# ---------------------------------------------------------------------------

class FakeApp:
    job_queue = None


def group_b() -> None:
    section("B. the start-up path: attach() and main() end where the guard says")
    start = datetime.now(timezone.utc)

    def attach_exits(bus: str) -> "tuple[bool, str]":
        os.environ["FAMILY_BUS"] = bus
        collector.clear()
        try:
            family_link.attach(FakeApp(), "anonbot", "AnonBot", start)
            return False, collector.text(logging.CRITICAL)
        except SystemExit:
            return True, collector.text(logging.CRITICAL)
        finally:
            os.environ["FAMILY_BUS"] = "on"

    new_world()
    with admin() as conn:
        conn.execute(f"INSERT INTO {FAMILY}.settings VALUES ('data_key:v1', '0123456789abcdef'), ('sealed:anon:v1', '1')")
    exits, said = attach_exits("on")
    check("attach() with the wrong key ends the process", exits and "not the key" in said, said)
    exits, said = attach_exits("off")
    check("...also when the family bus is off (the guard comes first)", exits and "not the key" in said, said)

    new_world()
    put_plain_state()
    exits, said = attach_exits("on")
    check("attach() on an unsealed database ends the process", exits and "not sealed yet" in said, said)
    exits, said = attach_exits("off")
    check("...also when the family bus is off", exits and "not sealed yet" in said, said)

    new_world()
    use_key(str(_tmp / "no_such_key"))
    exits, said = attach_exits("on")
    use_key(KEY_A)
    check("attach() with no key file ends the process", exits and "not usable" in said, said)

    new_world()
    with admin() as conn:
        conn.execute(f"INSERT INTO {FAMILY}.settings VALUES ('data_key:v1', %s), ('sealed:anon:v1', '1')",
                     (vault.fingerprint(),))
    collector.clear()
    exits, said = attach_exits("off")
    check("with the right key, sealed, and no bus, attach() returns normally", not exits, said)

    # the same answers from a real bot's main(), which is where the guard has
    # to be reached from -- before run_polling.
    from telegram.ext import Application
    os.chdir(ROOT / "anon_bot")
    import bot as ab

    reached = []
    real_run = Application.run_polling
    Application.run_polling = lambda self, *a, **k: reached.append(True)
    try:
        for what, setup in (
            ("the wrong key", lambda c: c.execute(
                f"INSERT INTO {FAMILY}.settings VALUES ('data_key:v1', '0123456789abcdef'), ('sealed:anon:v1', '1')")),
            ("an unsealed database", lambda c: c.execute(
                f"INSERT INTO {BOT_SCHEMA}.runtime_state (kind, key, value) VALUES ('user', %s, '{{}}'::jsonb)",
                (str(USER_1),))),
        ):
            new_world()
            with admin() as conn:
                setup(conn)
            reached.clear()
            try:
                ab.main()
                ended = False
            except SystemExit:
                ended = True
            check(f"AnonBot's main() with {what} ends the process", ended)
            check(f"...before it ever polls ({what})", not reached)
    finally:
        Application.run_polling = real_run


# ---------------------------------------------------------------------------
# C. saved conversations (runtime_state)
# ---------------------------------------------------------------------------

def raw_state_rows() -> list:
    with admin() as conn:
        return conn.execute(f"SELECT kind, key, value::text FROM {BOT_SCHEMA}.runtime_state ORDER BY kind, key").fetchall()


async def group_c() -> None:
    section("C. saved conversations: runtime_state")
    new_world()
    collector.clear()
    rows = [
        ("user", str(USER_1), {"lang": "uz", "pack": PACK, "nested": {"n": [1, 2, 3]}}),
        ("user", str(USER_2), {"lang": "ru"}),
        ("conversation", f"packs|{GROUP}:{USER_1}", {"state": 2}),
        ("conversation", f"packs|{USER_2}", {"state": 1}),
        ("conversation", f"report|{USER_3}:{USER_3}", {"state": 4}),
    ]
    lifecycle._write_state(rows)
    stored = raw_state_rows()
    text = "\n".join(f"{k}|{key}|{v}" for k, key, v in stored)
    check("every kind the persistence stores is written (user and conversation)", len(stored) == 5, str(len(stored)))
    check("no Telegram id, name or value is in the table",
          not any(token in text for token in (str(USER_1), str(USER_2), str(USER_3), str(abs(GROUP)), PACK)),
          text[:200])
    check("every id inside a key is its pseudonym (same shape as before)",
          all(all(vault.is_pseudonym(int(part)) for part in key.partition("|")[2].split(":"))
              for kind, key, _ in stored if kind == "conversation")
          and all(vault.is_pseudonym(int(key)) for kind, key, _ in stored if kind == "user"), str(stored))
    check("every value is just the sealed blob",
          all(v.startswith('{"sealed": "') and v.count(":") == 1 for _, _, v in stored), stored[0][2][:60])
    users = dict(lifecycle._read_state("user"))
    convs = dict(lifecycle._read_state("conversation"))
    check("reading restores the original key and data", users == {str(USER_1): rows[0][2], str(USER_2): rows[1][2]}
          and convs == {rows[2][1]: {"state": 2}, rows[3][1]: {"state": 1}, rows[4][1]: {"state": 4}},
          f"{users} {convs}")
    check("writing the same person again replaces the row rather than adding one",
          (lifecycle._write_state([rows[1]]), len(raw_state_rows()))[1] == 5)

    # the persistence object, the way a bot uses it
    persistence = lifecycle.PostgresPersistence()
    loaded = await persistence.get_user_data()
    check("get_user_data hands back real ids and the original data",
          loaded == {USER_1: rows[0][2], USER_2: rows[1][2]}, str(loaded))
    conversations = await persistence.get_conversations("packs")
    check("get_conversations rebuilds the keys with the arity they were stored with",
          conversations == {(GROUP, USER_1): 2, (USER_2,): 1}, str(conversations))
    await persistence.update_conversation("packs", (USER_3, USER_3), 9)
    check("a conversation state written as it happens is sealed too",
          all("PACK" not in v and str(USER_3) not in k for _, k, v in raw_state_rows())
          and len(raw_state_rows()) == 6)
    await persistence.update_conversation("packs", (USER_3, USER_3), None)
    check("and a finished conversation is deleted by its real key", len(raw_state_rows()) == 5)
    await persistence.update_user_data(USER_3, {"lang": "en", "pack": PACK})
    await persistence.flush()
    check("flush() seals a user's data", len(raw_state_rows()) == 6
          and all(PACK not in v for _, _, v in raw_state_rows()))
    await persistence.drop_user_data(USER_3)
    await persistence.flush()
    check("and a dropped user is deleted by the real id", len(raw_state_rows()) == 5)

    # rows that must not load
    use_key(KEY_B)
    foreign_key, foreign_value = lifecycle._seal_state("user", str(USER_3), {"lang": "en"})
    use_key(KEY_A)
    mine_key, mine_value = lifecycle._seal_state("user", str(USER_3), {"lang": "fr"})
    other_key = lifecycle._pseudo_key("user", str(USER_1))
    moved_value = lifecycle._seal_state("user", str(USER_3), {"lang": "moved"})[1]
    with admin() as conn:
        conn.execute(f"INSERT INTO {BOT_SCHEMA}.runtime_state (kind, key, value) VALUES ('user', %s, %s)",
                     (foreign_key, Jsonb(foreign_value)))
        conn.execute(f"INSERT INTO {BOT_SCHEMA}.runtime_state (kind, key, value) VALUES ('user', '-4611686018427387001', %s)",
                     (Jsonb({"sealed": "!!not base64!!"}),))
        conn.execute(f"INSERT INTO {BOT_SCHEMA}.runtime_state (kind, key, value) VALUES ('user', '-4611686018427387002', %s)",
                     (Jsonb({"lang": "plain, never sealed"}),))
        conn.execute(f"INSERT INTO {BOT_SCHEMA}.runtime_state (kind, key, value) VALUES ('user', '-4611686018427387003', %s)",
                     (Jsonb({"sealed": base64.b64encode(b"\x01" + b"x" * 40).decode()}),))
        conn.execute(f"INSERT INTO {BOT_SCHEMA}.runtime_state (kind, key, value) VALUES ('conversation', 'packs|-4611686018427387004', %s)",
                     (Jsonb(moved_value),))
        # sealed with the right key and purpose, but not what the persistence ever seals: not JSON at all
        conn.execute(f"INSERT INTO {BOT_SCHEMA}.runtime_state (kind, key, value) VALUES ('user', '-4611686018427387005', %s)",
                     (Jsonb({"sealed": base64.b64encode(vault.seal(bytes([0xFF, 0xFE]) + b" not json", lifecycle.STATE_PURPOSE)).decode()}),))
    # the moved one: a valid seal of someone else's row, put under another key
    with admin() as conn:
        conn.execute(f"UPDATE {BOT_SCHEMA}.runtime_state SET value = %s WHERE kind = 'user' AND key = %s",
                     (Jsonb(mine_value), other_key))
    collector.clear()
    persistence = lifecycle.PostgresPersistence()
    try:
        loaded = await persistence.get_user_data()
        conversations = await persistence.get_conversations("packs")
        crashed = False
    except Exception as exc:                         # noqa: BLE001
        loaded, conversations, crashed = {}, {}, repr(exc)
    check("a sealed row of another key, a moved row, garbage, a plain row and a seal that is not JSON do not stop the start-up",
          not crashed, str(crashed))
    check("...the rows that do open still load", loaded == {USER_2: rows[1][2]}
          and conversations == {(GROUP, USER_1): 2, (USER_2,): 1}, f"{loaded} {conversations}")
    warnings = collector.text()
    check("...each skipped row is a warning naming the table and the kind", warnings.count("did not open") == 7
          and "runtime_state" in warnings, warnings)
    check("...and no warning holds an id, a name or a value",
          not any(t in warnings for t in (str(USER_1), str(USER_2), str(USER_3), PACK, "plain, never sealed")), warnings)

    # forget_user
    new_world()
    lifecycle._write_state(rows)
    check("forget_user removes that person's rows: the user row and the conversations they end",
          lifecycle.forget_user(USER_1) == 2, str(raw_state_rows()))
    left = dict(lifecycle._read_state("user")), dict(lifecycle._read_state("conversation"))
    check("...and only theirs", left == ({str(USER_2): {"lang": "ru"}},
                                          {rows[3][1]: {"state": 1}, rows[4][1]: {"state": 4}}), str(left))
    check("a person the table has never seen is a quiet 0", lifecycle.forget_user(777_000_000_001) == 0)
    check("forget_user of a person who ends only a (user,) conversation finds it by its last part",
          lifecycle.forget_user(USER_2) == 2 and len(raw_state_rows()) == 1, str(len(raw_state_rows())))

    # a key that cannot be hidden is not saved in the clear
    new_world()
    collector.clear()
    lifecycle._write_state([("conversation", "packs|not-a-number", {"state": 1}), ("surprise", "1", {}),
                            ("user", str(USER_1), {"lang": "uz"})])
    check("a row whose key cannot be pseudonymised is refused with a warning, never stored plain",
          [(k, v) for k, v, _ in raw_state_rows()] == [("user", lifecycle._pseudo_key("user", str(USER_1)))]
          and collector.text().count("cannot be hidden") == 2, collector.text())


# ---------------------------------------------------------------------------
# D. the update waitlist
# ---------------------------------------------------------------------------

def group_d() -> None:
    section("D. the update waitlist")
    new_world()
    collector.clear()
    lifecycle.hold_for_update(USER_1, USER_1, "stickerbot")
    lifecycle.hold_for_update(USER_2, GROUP, "stickerbot")
    lifecycle.hold_for_update(USER_1, USER_1, "stickerbot")
    lifecycle.hold_for_update(USER_3, USER_3, "convertbot")
    with admin() as conn:
        rows = conn.execute(f"SELECT bot_id, user_id, chat_id, who_sealed FROM {FAMILY}.update_waitlist ORDER BY 1, 2").fetchall()
    check("one row per person and bot, as before", len(rows) == 3, str(len(rows)))
    check("user_id and chat_id hold pseudonyms, not ids",
          all(vault.is_pseudonym(u) and vault.is_pseudonym(c) for _, u, c, _ in rows)
          and {u for _, u, _, _ in rows} == {vault.pseudonym(USER_1), vault.pseudonym(USER_2), vault.pseudonym(USER_3)})
    check("a private chat and its person get the same pseudonym",
          any(u == vault.pseudonym(USER_1) and c == u for _, u, c, _ in rows))
    check("who_sealed holds the real ids, and only a seal",
          all(bytes(w)[:1] == bytes([vault.SEAL_VERSION]) and str(USER_1).encode() not in bytes(w) for _, _, _, w in rows))
    taken = lifecycle.take_held("stickerbot")
    check("take_held returns the real ids, as before", sorted(taken) == sorted([(USER_1, USER_1), (USER_2, GROUP)]), str(taken))
    check("...and removes them (a second /finishupdates tells nobody twice)",
          lifecycle.take_held("stickerbot") == [] and one(f"SELECT count(*) FROM {FAMILY}.update_waitlist") == 1)

    # rows that must not be obeyed
    new_world()
    lifecycle.hold_for_update(USER_1, USER_1, "stickerbot")
    lifecycle.hold_for_update(USER_2, USER_2, "stickerbot")
    use_key(KEY_B)
    foreign = vault.seal_json({"user": USER_3, "chat": USER_3}, lifecycle.WAITLIST_PURPOSE)
    use_key(KEY_A)
    stolen = vault.seal_json({"user": USER_3, "chat": USER_3}, lifecycle.WAITLIST_PURPOSE)
    with admin() as conn:
        conn.execute(f"INSERT INTO {FAMILY}.update_waitlist (bot_id, user_id, chat_id, who_sealed) VALUES ('stickerbot', %s, %s, %s)",
                     (vault.pseudonym(USER_3), vault.pseudonym(USER_3), foreign))
        conn.execute(f"INSERT INTO {FAMILY}.update_waitlist (bot_id, user_id, chat_id, who_sealed) VALUES ('stickerbot', -4611686018427387901, -4611686018427387901, %s)", (stolen,))
        conn.execute(f"INSERT INTO {FAMILY}.update_waitlist (bot_id, user_id, chat_id, who_sealed) VALUES ('stickerbot', -4611686018427387902, -4611686018427387902, %s)", (b"garbage",))
        conn.execute(f"INSERT INTO {FAMILY}.update_waitlist (bot_id, user_id, chat_id) VALUES ('stickerbot', %s, %s)", (USER_3 + 1, USER_3 + 1))
    collector.clear()
    taken = lifecycle.take_held("stickerbot")
    check("a seal of another key, a seal moved to another row, garbage and an unsealed row are skipped; the rest are told",
          sorted(taken) == sorted([(USER_1, USER_1), (USER_2, USER_2)]), str(taken))
    check("...each skipped row is a warning without an id", collector.text().count("did not open") == 4
          and not any(t in collector.text() for t in (str(USER_1), str(USER_3))), collector.text())

    # an older table: gets the column, keeps its rows
    drop_world()
    with admin() as conn:
        conn.execute(f"CREATE SCHEMA {FAMILY}")
        conn.execute(f"CREATE TABLE {FAMILY}.update_waitlist (bot_id TEXT NOT NULL, user_id BIGINT NOT NULL, "
                     f"chat_id BIGINT NOT NULL, held_at TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY (bot_id, user_id))")
        conn.execute(f"INSERT INTO {FAMILY}.update_waitlist (bot_id, user_id, chat_id) VALUES ('stickerbot', 7, 8)")
        conn.execute(f"CREATE SCHEMA {BOT_SCHEMA}")
    use_key(KEY_A)
    family_link.init_family_schema()
    lifecycle.init_waitlist_table()
    with admin() as conn:
        cols = {r[0] for r in conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_schema = %s AND table_name = 'update_waitlist'",
            (FAMILY,))}
    check("a waitlist made by an older version gets who_sealed and loses no row",
          "who_sealed" in cols and one(f"SELECT count(*) FROM {FAMILY}.update_waitlist") == 1, str(cols))


# ---------------------------------------------------------------------------
# E. the family conversion
# ---------------------------------------------------------------------------

def snapshot() -> dict:
    """Every row and every column of every sealtest schema, to compare two states."""
    out = {}
    with admin() as conn:
        for schema, table in conn.execute(
                "SELECT table_schema, table_name FROM information_schema.tables "
                "WHERE table_schema LIKE 'sealtest\\_%' OR table_schema LIKE 'sealtest2\\_%' ORDER BY 1, 2").fetchall():
            columns = [r[0] for r in conn.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_schema = %s AND table_name = %s ORDER BY 1",
                (schema, table))]
            rows = conn.execute(psycopg.sql.SQL("SELECT * FROM {}.{}").format(
                psycopg.sql.Identifier(schema), psycopg.sql.Identifier(table))).fetchall()
            out[f"{schema}.{table}"] = (columns, sorted(repr(r) for r in rows))
    return out


def build_old_state() -> None:
    """Saved state and a waitlist the way 2.1.1 left them: plain, in two bots' schemas and the family's,
    next to one row that is already sealed."""
    new_world()
    with admin() as conn:
        conn.execute("CREATE SCHEMA sealtest_convert_bot")
        conn.execute("CREATE TABLE sealtest_convert_bot.runtime_state (kind TEXT NOT NULL, key TEXT NOT NULL, "
                     "value JSONB NOT NULL, updated_at TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY (kind, key))")
        for schema in (BOT_SCHEMA, "sealtest_convert_bot"):
            conn.execute(psycopg.sql.SQL("INSERT INTO {}.runtime_state (kind, key, value) VALUES "
                                         "('user', %s, %s), ('conversation', %s, %s)").format(psycopg.sql.Identifier(schema)),
                         (str(USER_1), Jsonb({"lang": "uz", "pack": PACK}), f"packs|{GROUP}:{USER_1}", Jsonb({"state": 3})))
        sealed_key, sealed_value = lifecycle._seal_state("user", str(USER_2), {"lang": "ru"})
        conn.execute(f"INSERT INTO {BOT_SCHEMA}.runtime_state (kind, key, value) VALUES ('user', %s, %s)",
                     (sealed_key, Jsonb(sealed_value)))
        # the waitlist of 2.1.1: no who_sealed, plain ids
        conn.execute(f"ALTER TABLE {FAMILY}.update_waitlist DROP COLUMN who_sealed")
        conn.execute(f"INSERT INTO {FAMILY}.update_waitlist (bot_id, user_id, chat_id) VALUES ('stickerbot', %s, %s)",
                     (USER_1, USER_1))
    lifecycle.init_waitlist_table()           # a 2.1.2 bot starting: the column comes back, the old row stays
    lifecycle.hold_for_update(USER_2, USER_2, "convertbot")      # and one row written by the new code


def group_e() -> None:
    section("E. the family conversion")
    build_old_state()

    with psycopg.connect(DSN) as conn:
        autocommit_conn = psycopg.connect(DSN, autocommit=True)
        try:
            family_link.convert_personal_data_v1(autocommit_conn, FAMILY)
            refused = False
        except ValueError:
            refused = True
        autocommit_conn.close()
        check("the conversion refuses a connection in autocommit (it must run in the caller's transaction)", refused)

        try:
            family_link.verify_personal_data_v1(conn, FAMILY)
            raised = False
        except RuntimeError as exc:
            raised = "not sealed" in str(exc)
        check("verify raises while plain saved state is still there", raised)

        result = family_link.convert_personal_data_v1(conn, FAMILY)
        changed = result["changed"]
        check("convert deletes the plain rows: runtime_state in every schema of the family, and the waitlist",
              changed == {f"{FAMILY}.update_waitlist": 1, f"{BOT_SCHEMA}.runtime_state": 2,
                          "sealtest_convert_bot.runtime_state": 2}, str(changed))
        check("...and keeps the rows that are sealed and open",
              conn.execute(f"SELECT count(*) FROM {BOT_SCHEMA}.runtime_state").fetchone()[0] == 1
              and conn.execute(f"SELECT count(*) FROM {FAMILY}.update_waitlist").fetchone()[0] == 1)
        verified = family_link.verify_personal_data_v1(conn, FAMILY)
        check("verify passes afterwards, counting what it checked",
              verified["checked"] == {f"{FAMILY}.update_waitlist": 1, f"{BOT_SCHEMA}.runtime_state": 1,
                                      "sealtest_convert_bot.runtime_state": 0}, str(verified))
        again = family_link.convert_personal_data_v1(conn, FAMILY)
        check("a second run changes nothing", all(v == 0 for v in again["changed"].values()), str(again))
        conn.rollback()
    check("the conversion never commits: rolled back, the plain rows are all still there",
          one(f"SELECT count(*) FROM {BOT_SCHEMA}.runtime_state") == 3
          and one("SELECT count(*) FROM sealtest_convert_bot.runtime_state") == 2
          and one(f"SELECT count(*) FROM {FAMILY}.update_waitlist") == 2)

    # verify is not a rubber stamp
    build_old_state()
    with psycopg.connect(DSN) as conn:
        family_link.convert_personal_data_v1(conn, FAMILY)
        conn.commit()

    def verify_fails(what: str, damage) -> None:
        with psycopg.connect(DSN) as conn:
            damage(conn)
            try:
                family_link.verify_personal_data_v1(conn, FAMILY)
                raised = ""
            except RuntimeError as exc:
                raised = str(exc)
            conn.rollback()
        check(f"verify raises on {what}", "not sealed" in raised, raised)
        check(f"...and its message holds no id or value ({what})",
              not any(t in raised for t in (str(USER_1), str(USER_2), PACK)), raised)

    verify_fails("a plain saved row", lambda c: c.execute(
        f"INSERT INTO {BOT_SCHEMA}.runtime_state (kind, key, value) VALUES ('user', %s, %s)", (str(USER_1), Jsonb({"lang": "uz"}))))
    verify_fails("a saved row in another bot's schema", lambda c: c.execute(
        "INSERT INTO sealtest_convert_bot.runtime_state (kind, key, value) VALUES ('user', 'x', '{}'::jsonb)"))
    verify_fails("a saved row moved to another key", lambda c: c.execute(
        f"UPDATE {BOT_SCHEMA}.runtime_state SET key = %s", (str(vault.pseudonym(USER_3)),)))
    verify_fails("a waitlist row with no seal", lambda c: c.execute(
        f"INSERT INTO {FAMILY}.update_waitlist (bot_id, user_id, chat_id) VALUES ('x', %s, %s)", (vault.pseudonym(USER_3),) * 2))
    verify_fails("a waitlist row with a plain id", lambda c: c.execute(
        f"UPDATE {FAMILY}.update_waitlist SET user_id = %s", (USER_2,)))
    verify_fails("a waitlist seal that belongs to another row", lambda c: c.execute(
        f"UPDATE {FAMILY}.update_waitlist SET who_sealed = %s",
        (vault.seal_json({"user": USER_3, "chat": USER_3}, lifecycle.WAITLIST_PURPOSE),)))

    with psycopg.connect(DSN) as conn:
        result = family_link.convert_personal_data_v1(conn, "sealtest9_family")
        check("a family with no saved-state tables is skipped and said so, not a crash (convert)", result == {"changed": {}, "skipped": ["sealtest9_family: no update_waitlist table",
                                                                      "sealtest9_family: no runtime_state table in any schema"]}, str(result))
        verified = family_link.verify_personal_data_v1(conn, "sealtest9_family")
        check("...(verify)", verified["checked"] == {} and len(verified["skipped"]) == 2, str(verified))


# ---------------------------------------------------------------------------
# F. the script
# ---------------------------------------------------------------------------

T0 = "2026-01-01T10:00:00+00:00"


def make_210_anon(conn, schema: str) -> None:
    """AnonBot's tables as 2.1.0 left them (cut to the columns the conversion reads), with plain ids."""
    q = psycopg.sql.Identifier(schema)
    conn.execute(psycopg.sql.SQL("CREATE SCHEMA {}").format(q))
    conn.execute(psycopg.sql.SQL("SET search_path TO {}").format(q))
    for statement in (
        """CREATE TABLE star_transactions (id BIGSERIAL PRIMARY KEY, user_id BIGINT NOT NULL, username TEXT,
               amount_stars BIGINT NOT NULL, item TEXT NOT NULL, status TEXT NOT NULL, payload TEXT NOT NULL UNIQUE,
               charge_id TEXT, currency TEXT NOT NULL DEFAULT 'XTR', created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""",
        "CREATE TABLE donation_prompts (user_id BIGINT PRIMARY KEY, action_count INTEGER NOT NULL DEFAULT 0, last_shown_at TEXT)",
        "CREATE TABLE user_settings (user_id BIGINT PRIMARY KEY, language TEXT)",
        """CREATE TABLE anon_links (owner_user_id BIGINT PRIMARY KEY, token TEXT NOT NULL UNIQUE,
               is_paused INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL)""",
        """CREATE TABLE anon_conversations (id BIGSERIAL PRIMARY KEY, owner_user_id BIGINT NOT NULL,
               follower_user_id BIGINT NOT NULL, conv_number INTEGER NOT NULL, started_at TEXT NOT NULL,
               last_activity_at TEXT NOT NULL, last_owner_msg_id BIGINT, last_follower_msg_id BIGINT)""",
        "CREATE UNIQUE INDEX idx_c_natural ON anon_conversations (owner_user_id, follower_user_id, conv_number)",
        """CREATE TABLE anon_follower_state (follower_user_id BIGINT PRIMARY KEY, owner_user_id BIGINT NOT NULL,
               conversation_id BIGINT NOT NULL)""",
        """CREATE TABLE anon_relay (chat_id BIGINT NOT NULL, message_id BIGINT NOT NULL, conversation_id BIGINT NOT NULL,
               PRIMARY KEY (chat_id, message_id))""",
        """CREATE TABLE anon_blocks (owner_user_id BIGINT NOT NULL, follower_user_id BIGINT NOT NULL,
               blocked_at TEXT NOT NULL, PRIMARY KEY (owner_user_id, follower_user_id))""",
        "CREATE TABLE activity_events (id BIGSERIAL PRIMARY KEY, user_id BIGINT NOT NULL, occurred_at TIMESTAMPTZ NOT NULL DEFAULT now())",
        # 2.2.0's table of announcements shown, as 2.2.0 made it (plain ids, and the time).
        """CREATE TABLE promo_seen (user_id BIGINT NOT NULL, promo TEXT NOT NULL,
               seen_at TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY (user_id, promo))""",
    ):
        conn.execute(statement)
    conn.execute("INSERT INTO promo_seen (user_id, promo) VALUES (%s,'gift')", (USER_2,))
    conn.execute("INSERT INTO user_settings (user_id, language) VALUES (%s,'en'),(%s,'uz')", (USER_1, USER_2))
    conn.execute("INSERT INTO anon_links (owner_user_id, token, created_at) VALUES (%s,'tok-sealtest',%s)", (USER_1, T0))
    conn.execute("INSERT INTO anon_conversations (id, owner_user_id, follower_user_id, conv_number, started_at, "
                 "last_activity_at) VALUES (1,%s,%s,1,%s,%s)", (USER_1, USER_2, T0, T0))
    conn.execute("INSERT INTO anon_follower_state VALUES (%s,%s,1)", (USER_2, USER_1))
    conn.execute("INSERT INTO anon_relay VALUES (%s,10,1),(%s,20,1)", (USER_1, USER_2))
    conn.execute("INSERT INTO anon_blocks VALUES (%s,%s,%s)", (USER_1, USER_3, T0))
    conn.execute("INSERT INTO donation_prompts (user_id, action_count) VALUES (%s,2)", (USER_2,))
    conn.execute("INSERT INTO activity_events (user_id) VALUES (%s)", (USER_2,))
    conn.execute("INSERT INTO star_transactions (user_id, username, amount_stars, item, status, payload, created_at, updated_at)"
                 " VALUES (%s,%s,50,'donation','paid','p-sealtest',%s,%s)", (USER_2, NAME, T0, T0))


def build_2110_world() -> None:
    """A database as 2.1.1 left it: AnonBot's plain rows, plain saved state, a plain waitlist, the family's tables."""
    build_old_state()
    with admin() as conn:
        conn.execute(psycopg.sql.SQL("DROP SCHEMA {} CASCADE").format(psycopg.sql.Identifier(BOT_SCHEMA)))
        make_210_anon(conn, BOT_SCHEMA)
        conn.execute("SET search_path TO DEFAULT")
        conn.execute(f"CREATE TABLE {BOT_SCHEMA}.runtime_state (kind TEXT NOT NULL, key TEXT NOT NULL, "
                     "value JSONB NOT NULL, updated_at TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY (kind, key))")
        conn.execute(f"INSERT INTO {BOT_SCHEMA}.runtime_state (kind, key, value) VALUES ('user', %s, %s)",
                     (str(USER_1), Jsonb({"lang": "uz", "pack": PACK})))
        sealed_key, sealed_value = lifecycle._seal_state("user", str(USER_2), {"lang": "ru"})
        conn.execute(f"INSERT INTO {BOT_SCHEMA}.runtime_state (kind, key, value) VALUES ('user', %s, %s)",
                     (sealed_key, Jsonb(sealed_value)))
    db.close_pool()


def run_script(*extra, env_name: str = "SEALTEST_URL", key: str = KEY_A, families=None, modules=None,
               dry: bool = False, connect=None) -> "tuple[int, str, str]":
    argv = ["--database-url-env", env_name, "--key-file", key, *(["--dry-run"] if dry else []), *extra]
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = script.main(argv, modules=modules, families=families if families is not None else [FAMILY],
                           connect=connect)
    use_key(KEY_A)
    return code, out.getvalue(), err.getvalue()


def group_f() -> None:
    section("F. scripts/encrypt_personal_data.py")
    PERSONAL = (str(USER_1), str(USER_2), str(USER_3), NAME, PACK, "tok-sealtest")

    build_2110_world()
    before = snapshot()
    code, out, err = run_script(dry=True)
    check("a dry run succeeds and says so", code == 0 and "Dry run: rolled back" in out, out + err)
    check("...changes nothing: every row, every column, as before", snapshot() == before)
    check("...and stamps nothing", stamps() == {})
    check("...but reports counts for the tables it would have converted",
          "anon_links: 1" in out and "anon_conversations: 1" in out and "runtime_state: 1" in out, out)

    code, out, err = run_script()
    check("a real run succeeds and says to start the bots", code == 0 and "Done: committed" in out, out + err)
    with admin() as conn:
        owner = conn.execute(f"SELECT owner_user_id FROM {BOT_SCHEMA}.anon_links").fetchone()[0]
        conv = conn.execute(f"SELECT owner_user_id, follower_user_id, owner_sealed, follower_sealed "
                            f"FROM {BOT_SCHEMA}.anon_conversations").fetchone()
        stars = conn.execute(f"SELECT user_id, username_sealed FROM {BOT_SCHEMA}.star_transactions").fetchone()
        cols = {r[0] for r in conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_schema = %s AND table_name = 'star_transactions'",
            (BOT_SCHEMA,))}
    check("AnonBot's identities are pseudonyms", owner == vault.pseudonym(USER_1)
          and conv[0] == vault.pseudonym(USER_1) and conv[1] == vault.pseudonym(USER_2))
    check("...the sealed copies open to the original ids",
          vault.open_id(conv[2], "anon.conversation.owner", expected=conv[0]) == USER_1
          and vault.open_id(conv[3], "anon.conversation.follower", expected=conv[1]) == USER_2)
    check("...the ledger's username is sealed and the plain column is gone",
          "username" not in cols and vault.open_text(stars[1], "stars.username") == NAME, str(cols))
    check("...and AnonBot, asked with a real id, finds the converted link",
          db.get_or_create_anon_link(USER_1)[0] == "tok-sealtest")
    db.close_pool()
    check("the saved state that was not sealed is gone, the sealed row is kept",
          one(f"SELECT count(*) FROM {BOT_SCHEMA}.runtime_state") == 1
          and one("SELECT count(*) FROM sealtest_convert_bot.runtime_state") == 0
          and one(f"SELECT count(*) FROM {FAMILY}.update_waitlist") == 1)
    check("the database is stamped with this key and with the AnonBot stage",
          stamps() == {"data_key:v1": vault.fingerprint(), "sealed:anon:v1": "1"}, str(stamps()))
    went_on, said = run_guard()
    check("...which is what the bots check at start: the guard lets them in", went_on, said)
    check("the output holds no id, name, token or database address",
          not any(t in out + err for t in PERSONAL) and "localhost" not in out + err and DSN not in out + err, out)

    after_first = snapshot()
    code, out2, err2 = run_script()
    converting = out2.split("Converting:")[1].split("Verifying:")[0]
    counts = [int(line.rsplit(": ", 1)[1]) for line in converting.splitlines() if line.startswith("  ")]
    check("a second run succeeds and everything it converts is zero rows",
          code == 0 and len(counts) >= 10 and all(c == 0 for c in counts), out2 + err2)
    check("...and changes nothing, down to the last sealed byte", snapshot() == after_first)

    # refusals
    build_2110_world()
    with admin() as conn:
        conn.execute(f"INSERT INTO {FAMILY}.settings VALUES ('data_key:v1', '0123456789abcdef')")
    before = snapshot()
    code, out, err = run_script()
    check("a database stamped with another key is refused (exit 2), changing nothing",
          code == 2 and "another data key" in err and snapshot() == before, err)
    build_2110_world()
    code, out, err = run_script()
    done = snapshot()
    code_b, out_b, err_b = run_script(key=KEY_B)
    check("...and so is a database this very script sealed, when run again with a second key file",
          code == 0 and code_b == 2 and "another data key" in err_b and snapshot() == done, err_b)

    build_2110_world()
    with admin() as conn:
        conn.execute(f"INSERT INTO {FAMILY}.heartbeats (bot_id, started_at, last_seen) VALUES ('anonbot', now(), now() - interval '20 seconds')")
        conn.execute(f"INSERT INTO {FAMILY}.heartbeats (bot_id, started_at, last_seen) VALUES ('stickerbot', now(), now() - interval '10 minutes')")
    before = snapshot()
    code, out, err = run_script()
    check("a bot with a heartbeat from the last minutes is refused (exit 2), changing nothing",
          code == 2 and snapshot() == before, err)
    check("...naming the bot that looks alive and not the one that stopped", "anonbot" in err and "stickerbot" not in err, err)
    code, out, err = run_script(dry=True)
    check("...a dry run is refused too", code == 2 and "anonbot" in err and snapshot() == before, err)
    with admin() as conn:
        conn.execute(f"DELETE FROM {FAMILY}.heartbeats WHERE bot_id = 'anonbot'")
    code, out, err = run_script()
    check("once only stale heartbeats are left it goes ahead", code == 0 and stamps().get("sealed:anon:v1") == "1", out + err)

    # failure in the middle: everything rolls back
    build_2110_world()
    before = snapshot()

    class BreaksOnVerify:
        convert_personal_data_v1 = staticmethod(script.load_bot_db("anon_bot").convert_personal_data_v1)

        @staticmethod
        def verify_personal_data_v1(conn, schema):
            raise RuntimeError("verification failed (test)")

    code, out, err = run_script(modules={"anon_bot": BreaksOnVerify, "family_link": family_link})
    check("a verification that fails rolls the whole conversion back (exit 1)",
          code == 1 and "rolled back" in err and snapshot() == before and stamps() == {}, out + err)

    saved = family_link.SEAL_STAGES
    family_link.SEAL_STAGES = ("anon", "convert")
    code, out, err = run_script()
    family_link.SEAL_STAGES = saved
    check("a stage the bots require and the script cannot convert is refused before anything is touched",
          code == 2 and "convert" in err and snapshot() == before, err)

    # the command line
    code, out, err = run_script(env_name="SEALTEST_NOT_SET")
    check("an environment variable that is not set is an error (exit 1)", code == 1 and "not set" in err, err)
    code, out, err = run_script(key=str(_tmp / "no_such_key"))
    check("a missing key file is an error (exit 1), before the database is touched",
          code == 1 and "cannot be used" in err and snapshot() == before, err)
    for what, argv in (("an address given as an argument", ["--database-url", DSN]),
                       ("an address given as the variable's name", ["--database-url-env", DSN, "--key-file", KEY_A])):
        o, e = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(o), contextlib.redirect_stderr(e):
            code = script.main(argv, families=[FAMILY])
        check(f"{what} is refused (exit 2) and never echoed", code == 2 and DSN not in o.getvalue() + e.getvalue()
              and "localhost" not in o.getvalue() + e.getvalue() and snapshot() == before, e.getvalue())

    # a family that has AnonBot's schema but no settings table (TestBot's, on a database that never ran it)
    build_2110_world()
    with admin() as conn:
        make_210_anon(conn, "sealtest2_anon_bot")
        conn.execute("SET search_path TO DEFAULT")
    code, out, err = run_script(families=["sealtest2_family"])
    check("a family with AnonBot's schema and no settings table is converted and stamped; the script says it made one",
          code == 0 and "no settings table here; made one" in out and one("SELECT count(*) FROM sealtest2_family.settings") == 2
          and one("SELECT value FROM sealtest2_family.settings WHERE key = 'sealed:anon:v1'") == "1", out + err)
    check("...and only its own schemas were touched (the other family is still plain)",
          one(f"SELECT owner_user_id FROM {BOT_SCHEMA}.anon_links") == USER_1)

    # which families, by default
    with admin() as conn:
        before_names = script.default_families(conn)
        conn.execute("CREATE SCHEMA testbot_sealtest_probe")
        conn.execute("CREATE TABLE testbot_sealtest_probe.t (x int)")
    try:
        with admin() as conn:
            names = script.default_families(conn)
    finally:
        with admin() as conn:
            conn.execute("DROP SCHEMA testbot_sealtest_probe CASCADE")
    check("by default the real family, and TestBot's when any of its schemas is here",
          names == ["family", "testbot_family"], str(names))
    check("...and only the real one when TestBot has no schema of its own on the database",
          before_names in (["family"], ["family", "testbot_family"]) and before_names[0] == "family", str(before_names))


class Recorder:
    """A connection that is the real one in every way but one: it notes each VACUUM it is given (and
    whether it was in autocommit mode, which the statement needs), raises for the table named in `broken`,
    and quietly does nothing for the one named in `skipped` (as Postgres does, with only a warning, for a
    table the role does not own)."""

    def __init__(self, real, broken: "str | None" = None, skipped: "str | None" = None):
        self._real, self.broken, self.skipped, self.vacuums = real, broken, skipped, []

    def __getattr__(self, name):
        return getattr(self._real, name)

    @property
    def autocommit(self):
        return self._real.autocommit

    @autocommit.setter
    def autocommit(self, value):
        self._real.autocommit = value

    def execute(self, query, *args, **kwargs):
        text = query.as_string(self._real) if hasattr(query, "as_string") else str(query)
        if text.startswith("VACUUM"):
            self.vacuums.append((text, self._real.autocommit))
            if self.broken and self.broken in text:
                raise psycopg.errors.OperationalError("canceling statement due to lock timeout (test)")
            if self.skipped and self.skipped in text:
                return self._real.execute("SELECT 1")
        return self._real.execute(query, *args, **kwargs)


def vacuumed_in(out: str) -> set:
    return {line.split()[1] for line in out.splitlines() if line.startswith("  vacuumed ")}


def stats_text() -> str:
    """Every sampled value Postgres keeps for the bot schema's columns (pg_stats: most common values and
    histogram bounds), as one string."""
    return one("SELECT coalesce(string_agg(concat_ws(' ', most_common_vals::text, histogram_bounds::text), ' '), '') "
               f"FROM pg_stats WHERE schemaname = '{BOT_SCHEMA}'")


def plain_in_stats() -> list:
    text = stats_text()
    return [user for user in (USER_1, USER_2, USER_3) if re.search(rf"(?<!\d){user}(?!\d)", text)]


def group_f2() -> None:
    section("F2. the fingerprint, and the vacuum after a commit")
    fingerprint = vault.fingerprint()
    key_text = Path(KEY_A).read_text(encoding="ascii").strip()
    must_have = {f"{BOT_SCHEMA}.{t}" for t in ("anon_links", "anon_conversations", "anon_follower_state", "anon_relay",
                                              "anon_blocks", "user_settings", "donation_prompts", "promo_seen",
                                              "activity_events", "star_transactions", "runtime_state")}
    must_have |= {"sealtest_convert_bot.runtime_state", f"{FAMILY}.update_waitlist"}

    build_2110_world()
    with admin() as conn:
        for (table,) in conn.execute("SELECT tablename FROM pg_tables WHERE schemaname = %s",
                                     (BOT_SCHEMA,)).fetchall():
            conn.execute(f'ANALYZE "{BOT_SCHEMA}"."{table}"')
    sampled_before = plain_in_stats()
    before = snapshot()
    recorder: dict = {}

    def connect(address):
        recorder["conn"] = Recorder(psycopg.connect(address))
        return recorder["conn"]

    code, out, err = run_script(dry=True, connect=connect)
    check("M4: the script starts by printing the key file's fingerprint (16 hex digits)",
          code == 0 and out.startswith(f"Key fingerprint: {fingerprint}\n") and len(fingerprint) == 16, out[:120])
    check("M4: ...and the fingerprint the database is stamped with: none yet",
          f"Stamped fingerprint, {FAMILY}: none" in out, out[:300])
    check("M4: ...and the key itself is nowhere in the output", key_text not in out + err)
    check("a dry run vacuums nothing: no VACUUM issued, no 'vacuum' in the output, nothing changed",
          recorder["conn"].vacuums == [] and "vacuum" not in (out + err).lower() and snapshot() == before, out + err)

    code, out, err = run_script(connect=connect)
    done = vacuumed_in(out)
    check("M1: a real run commits, then vacuums, and ends the way it did before",
          code == 0 and "Done: committed" in out and out.index("Committed.") < out.index("Vacuuming"), out + err)
    check("M1: ...every table it converted, and the saved-state and waitlist tables, were vacuumed (printed one by one)",
          must_have <= done, str(sorted(must_have - done)))
    check("M1: ...nothing outside the sealtest schemas, and each table once",
          all(name.startswith(("sealtest_", "sealtest2_")) for name in done)
          and len(done) == len(recorder["conn"].vacuums), str(sorted(done)))
    check("M1: ...with VACUUM (FULL, ANALYZE), on an autocommit connection (the statement cannot run in a transaction)",
          len(recorder["conn"].vacuums) == len(done) and recorder["conn"].vacuums
          and all(text.startswith("VACUUM (FULL, ANALYZE) \"") and auto for text, auto in recorder["conn"].vacuums),
          str(recorder["conn"].vacuums[:2]))
    check("M1: ...and the column statistics no longer sample a plain id (they did before the run)",
          sampled_before != [] and plain_in_stats() == [], f"before {sampled_before}, after {plain_in_stats()}")
    check("M4: ...and now says the stamp it found afterwards (a second run, below, prints it)",
          stamps().get("data_key:v1") == fingerprint)

    code, out2, err2 = run_script(connect=connect)
    check("M4: a run on a database already stamped prints that stamp, equal to the key file's",
          code == 0 and f"Stamped fingerprint, {FAMILY}: {fingerprint}" in out2, out2[:300])

    # a vacuum that fails: the others are still done, the data is committed, exit 3
    build_2110_world()

    def connect_broken(address):
        recorder["conn"] = Recorder(psycopg.connect(address), broken='"anon_relay"')
        return recorder["conn"]

    code, out, err = run_script(connect=connect_broken)
    done = vacuumed_in(out)
    check("M1: a VACUUM that fails ends in exit 3, saying how to finish", code == 3
          and "Converted; vacuum incomplete: rerun with --vacuum-only" in err, out + err)
    check("M1: ...the failed table is named, and every other table was still vacuumed",
          f"vacuum FAILED {BOT_SCHEMA}.anon_relay" in out and f"{BOT_SCHEMA}.anon_relay" not in done
          and (must_have - {f"{BOT_SCHEMA}.anon_relay"}) <= done, out)
    check("M1: ...and the conversion itself was kept: stamped, converted",
          stamps() == {"data_key:v1": fingerprint, "sealed:anon:v1": "1"}
          and one(f"SELECT owner_user_id FROM {BOT_SCHEMA}.anon_links") == vault.pseudonym(USER_1))
    after_conversion = snapshot()
    with admin() as conn:
        conn.execute(f"INSERT INTO {FAMILY}.heartbeats (bot_id, started_at, last_seen) "
                     "VALUES ('anonbot', now(), now() - interval '20 seconds')")
    code, out, err = run_script("--vacuum-only", connect=connect)
    check("M1: --vacuum-only while a bot looks alive is refused (exit 2) and vacuums nothing",
          code == 2 and "look alive" in err and recorder["conn"].vacuums == [], out + err)
    with admin() as conn:
        conn.execute(f"DELETE FROM {FAMILY}.heartbeats WHERE bot_id = 'anonbot'")
    code, out, err = run_script("--vacuum-only", connect=connect)
    check("M1: --vacuum-only then vacuums everything, converts nothing, changes no row",
          code == 0 and must_have <= vacuumed_in(out) and "Converting:" not in out
          and "Stamping:" not in out and snapshot() == after_conversion, out + err)
    check("M1: ...and it also printed both fingerprints",
          out.startswith(f"Key fingerprint: {fingerprint}\n") and f"Stamped fingerprint, {FAMILY}: {fingerprint}" in out)

    # a VACUUM that Postgres skips without an error (a table the role does not own): failed, not "vacuumed"
    build_2110_world()

    def connect_skipped(address):
        recorder["conn"] = Recorder(psycopg.connect(address), skipped='"anon_relay"')
        return recorder["conn"]

    code, out, err = run_script(connect=connect_skipped)
    check("M1: a VACUUM that leaves the table's file as it was counts as failed: exit 3, the table named",
          code == 3 and f"vacuum FAILED {BOT_SCHEMA}.anon_relay (not rewritten" in out
          and f"{BOT_SCHEMA}.anon_relay" not in vacuumed_in(out)
          and "Converted; vacuum incomplete: rerun with --vacuum-only" in err, out + err)

    # --vacuum-only on a database that is not converted with this key: refused, nothing done
    build_2110_world()
    before = snapshot()
    code, out, err = run_script("--vacuum-only", connect=connect)
    check("M1: --vacuum-only on a database with no stamps is refused (exit 2) and vacuums nothing",
          code == 2 and "not stamped" in err and recorder["conn"].vacuums == [] and snapshot() == before, err)
    with admin() as conn:
        conn.execute(f"INSERT INTO {FAMILY}.settings VALUES ('data_key:v1', %s)", (fingerprint,))
    before = snapshot()
    code, out, err = run_script("--vacuum-only", connect=connect)
    check("M1: ...and so is one stamped with the key but with no stage stamp",
          code == 2 and "stage anon" in err and recorder["conn"].vacuums == [] and snapshot() == before, err)
    build_2110_world()
    code, _o, _e = run_script()
    code_b, out_b, err_b = run_script("--vacuum-only", key=KEY_B, connect=connect)
    check("M1: ...and one stamped with another key",
          code == 0 and code_b == 2 and "another data key" in err_b and recorder["conn"].vacuums == [], err_b)
    check("M1: ...with the stamped fingerprint printed, so the operator can see which key it is",
          f"Stamped fingerprint, {FAMILY}: {fingerprint}" in out_b and out_b.startswith("Key fingerprint: ")
          and out_b.split("\n")[0] != f"Key fingerprint: {fingerprint}", out_b[:200])


def group_g() -> None:
    section("G. the key guard waits for a database that does not answer, and refuses at once for anything else")
    import psycopg_pool

    check("a pool timeout is a psycopg.OperationalError, so it is retried like one",
          issubclass(psycopg_pool.PoolTimeout, psycopg.OperationalError))
    check("DataKeyRefused is a SystemExit with data_key True",
          issubclass(family_link.DataKeyRefused, SystemExit) and family_link.DataKeyRefused.data_key is True)

    real_connect, real_sleep = family_link._connect, family_link._sleep
    slept: list = []

    def flaky(times: int, error=psycopg.OperationalError):
        """_connect that fails with `error` for its first `times` calls, then is the real one."""
        state = {"calls": 0}

        def connect():
            state["calls"] += 1
            if state["calls"] <= times:
                raise error("the server closed the connection unexpectedly (test)")
            return real_connect()
        return connect

    def guard(connect=None) -> "tuple[bool, str, SystemExit | None]":
        collector.clear()
        slept.clear()
        family_link._sleep = slept.append
        family_link._connect = connect or real_connect
        try:
            family_link.guard_data_key()
            return True, collector.text(), None
        except SystemExit as exc:
            return False, collector.text(), exc
        finally:
            family_link._connect, family_link._sleep = real_connect, real_sleep

    def retries(text_records) -> int:
        return sum(1 for r in collector.records
                   if r.levelno == logging.WARNING and "could not be checked yet" in r.getMessage())

    new_world()
    ok, said, exc = guard(flaky(2))
    check("H2: a database that fails twice and then answers: the guard passes, and the bot goes on",
          ok and exc is None and stamps().get("data_key:v1") == vault.fingerprint(), said)
    check("H2: ...it waited 2 s and then 4 s (patched, so nobody waited), with one WARNING for each failure",
          slept == [2, 4] and retries(None) == 2, f"{slept} {retries(None)}")
    check("H2: ...and the warnings hold no id or key", all("sealtest" not in r.getMessage() and str(USER_1) not in r.getMessage()
                                                          for r in collector.records))

    new_world()
    ok, said, exc = guard(flaky(1, psycopg_pool.PoolTimeout))
    check("H2: a pool timeout is waited for as well", ok and slept == [2] and retries(None) == 1, f"{slept} {said}")

    new_world()
    ok, said, exc = guard(flaky(10_000))
    check("H2: a database that never answers: the guard refuses with DataKeyRefused after the whole schedule",
          not ok and isinstance(exc, family_link.DataKeyRefused) and exc.data_key is True, repr(exc))
    check("H2: ...having waited 2, 4, 8, 16, 30, 30 seconds, with a WARNING each time",
          slept == [2, 4, 8, 16, 30, 30] and retries(None) == 6, f"{slept} {retries(None)}")
    check("H2: ...saying the database is not answering, at CRITICAL",
          str(exc) == "The data key could not be checked: the database is not answering"
          and any(r.levelno == logging.CRITICAL and "not answering" in r.getMessage() for r in collector.records), str(exc))

    # refused at once: no waiting, DataKeyRefused
    new_world()
    with admin() as conn:
        conn.execute(f"INSERT INTO {FAMILY}.settings VALUES ('data_key:v1', '0123456789abcdef'), ('sealed:anon:v1', '1')")
    ok, said, exc = guard()
    check("H2: a wrong key is refused at once (no waiting): DataKeyRefused, data_key True",
          not ok and isinstance(exc, family_link.DataKeyRefused) and exc.data_key is True and slept == []
          and "not the key this database was sealed with" in str(exc), repr(exc))
    new_world()
    put_plain_state()
    ok, said, exc = guard()
    check("H2: unsealed data is refused at once", not ok and isinstance(exc, family_link.DataKeyRefused)
          and "not sealed yet" in str(exc) and slept == [], repr(exc))
    new_world()
    use_key(str(_tmp / "no_such_key"))
    ok, said, exc = guard()
    use_key(KEY_A)
    check("H2: a missing key file is refused at once", not ok and isinstance(exc, family_link.DataKeyRefused)
          and "data key is not usable" in str(exc) and slept == [], repr(exc))
    new_world()
    ok, said, exc = guard(flaky(1, RuntimeError))
    check("H2: any other error while checking is refused at once too, naming its kind and not its text",
          not ok and isinstance(exc, family_link.DataKeyRefused) and "RuntimeError" in str(exc)
          and "the server closed" not in str(exc) and slept == [], repr(exc))


async def main() -> int:
    try:
        group_a()
        group_b()
        await group_c()
        group_d()
        group_e()
        group_f()
        group_f2()
        group_g()
    finally:
        use_key(KEY_A)
        try:
            drop_world()
        except Exception as exc:                        # noqa: BLE001
            print(f"(could not drop the sealtest schemas: {type(exc).__name__})")
        print("\n(dropped the sealtest schemas)")
    print(f"\n{_checks} checks, FAILURES: {len(failures)}")
    for name in failures:
        print("  -", name)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
