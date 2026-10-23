"""AnonBot's personal data, sealed -- on a database made the way 2.1.0 made it.

Every other AnonBot suite starts from an empty schema that the current
init_db() builds, so none of them can say what happens to the rows that are
already there on the day this version is deployed. This one builds the 2.1.0
tables (the CREATE TABLE and ALTER statements of `git show d6c723c:anon_bot/db.py`,
with star_transactions.username and anon_relay.sent_at / from_bot), fills them
with plain Telegram ids the way 2.1.0 stored them, and then runs the
conversion and everything that depends on it:

    A  the conversion          pseudonyms in place, seals that open to the
                               original ids and names, the plain columns gone,
                               a second run that changes nothing, verify, and
                               the db functions answering with the real ids
    B  verify is not a rubber stamp   a plain id left anywhere, or a seal that
                               does not match its row, makes it raise
    C  a seal moved to another row    refused: the row is skipped, nothing
                               crashes, no wrong id comes back
    D  /deletemydata           nothing of the person is left, sealed or not
    E  db_merge.py             the same key merges; another key is refused
                               before a single row is written
    F  the log                 nothing real appears at INFO or above
    G  broadcast reach         a settings row made only by activity goes with
                               the activity; one with a chosen language stays
    (and, through A, B and D: a payer is stored under pseudonym_in(id,
    "anon.payer"), never pseudonym(id), so the Stars ledger cannot be lined up
    with the family's star_ledger)

    python tests/anon_encryption.py

Needs a Postgres (TEST_DATABASE_URL, or the local default) and a data key
(DATA_KEY_FILE; a throwaway one is made when it is not set). It works in two
schemas of its own and drops them. No network.
"""
from __future__ import annotations

import logging
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "anon_bot"))
sys.path.insert(1, str(ROOT / "shared"))

DSN = os.environ.get("TEST_DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/botfamily_test")
os.environ["DATABASE_URL"] = DSN
SCHEMA_A, SCHEMA_B = "anon_enc_test_a", "anon_enc_test_b"
os.environ["DB_SCHEMA"] = SCHEMA_A
if not os.environ.get("DATA_KEY_FILE"):
    _key_file = Path(tempfile.mkdtemp(prefix="anon_enc_")) / "key"
    import base64
    _key_file.write_text(base64.b64encode(os.urandom(32)).decode("ascii"), encoding="ascii")
    os.environ["DATA_KEY_FILE"] = str(_key_file)

import psycopg  # noqa: E402
import vault  # noqa: E402
import db  # noqa: E402
import db_merge  # noqa: E402
import sealed_check  # noqa: E402

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


# What the log said at INFO or above, for section F.
class _Collect(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record):
        self.records.append(record)


collector = _Collect()
logging.getLogger().addHandler(collector)
logging.getLogger().setLevel(logging.INFO)

OWNER_1, OWNER_2, GUEST_1, GUEST_2, GUEST_3, NEWCOMER = 5101, 5102, 6101, 6102, 6103, 7101
# Two people who exist only in promo_seen (2.2.0's table of announcements shown): one in the 2.2.0 data, one
# who claims an announcement after the conversion.
PROMO_ONLY, PROMO_NEW = 8201, 8202
EVERYONE = (OWNER_1, OWNER_2, GUEST_1, GUEST_2, GUEST_3, NEWCOMER)
P = vault.pseudonym


def PAYER(uid: int) -> int:
    """What the Stars ledger stores for a payer (anon_bot/db.py PAYER_DOMAIN)."""
    return vault.pseudonym_in(uid, "anon.payer")


T0 = "2026-01-01T10:00:00+00:00"
T1 = "2026-01-02T10:00:00+00:00"


# ---------------------------------------------------------------------------
# 2.1.0, as it was
# ---------------------------------------------------------------------------

def make_210_schema(conn, schema: str) -> None:
    """The tables exactly as 2.1.0's init_db() left them, in a fresh schema -- and 2.2.0's promo_seen, as
    2.2.0 made it (plain ids, and the time each was shown)."""
    conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
    conn.execute(f'CREATE SCHEMA "{schema}"')
    conn.execute(f'SET search_path TO "{schema}"')
    for statement in (
        """CREATE TABLE star_transactions (
               id BIGSERIAL PRIMARY KEY, user_id BIGINT NOT NULL, username TEXT,
               amount_stars BIGINT NOT NULL, item TEXT NOT NULL, status TEXT NOT NULL,
               payload TEXT NOT NULL UNIQUE, charge_id TEXT,
               currency TEXT NOT NULL DEFAULT 'XTR', created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""",
        """CREATE TABLE donation_prompts (
               user_id BIGINT PRIMARY KEY, action_count INTEGER NOT NULL DEFAULT 0, last_shown_at TEXT)""",
        "ALTER TABLE donation_prompts ADD COLUMN IF NOT EXISTS times_shown INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE donation_prompts ADD COLUMN IF NOT EXISTS pinned_message_id BIGINT",
        "CREATE TABLE user_settings (user_id BIGINT PRIMARY KEY, language TEXT)",
        "ALTER TABLE user_settings ADD COLUMN IF NOT EXISTS knows_reactions BOOLEAN",
        "ALTER TABLE user_settings ALTER COLUMN knows_reactions SET DEFAULT TRUE",
        """CREATE TABLE anon_links (
               owner_user_id BIGINT PRIMARY KEY, token TEXT NOT NULL UNIQUE,
               is_paused INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL)""",
        """CREATE TABLE anon_conversations (
               id BIGSERIAL PRIMARY KEY, owner_user_id BIGINT NOT NULL, follower_user_id BIGINT NOT NULL,
               conv_number INTEGER NOT NULL, started_at TEXT NOT NULL, last_activity_at TEXT NOT NULL,
               last_owner_msg_id BIGINT, last_follower_msg_id BIGINT)""",
        "ALTER TABLE anon_conversations ADD COLUMN IF NOT EXISTS follower_first_msg_at TEXT",
        "ALTER TABLE anon_conversations ADD COLUMN IF NOT EXISTS archived_at TEXT",
        "CREATE INDEX idx_anon_conversations_pair ON anon_conversations (owner_user_id, follower_user_id)",
        "CREATE INDEX idx_anon_conversations_owner_activity "
        "ON anon_conversations (owner_user_id, last_activity_at DESC)",
        "CREATE UNIQUE INDEX idx_anon_conversations_natural "
        "ON anon_conversations (owner_user_id, follower_user_id, conv_number)",
        """CREATE TABLE anon_follower_state (
               follower_user_id BIGINT PRIMARY KEY, owner_user_id BIGINT NOT NULL,
               conversation_id BIGINT NOT NULL)""",
        """CREATE TABLE anon_relay (
               chat_id BIGINT NOT NULL, message_id BIGINT NOT NULL, conversation_id BIGINT NOT NULL,
               PRIMARY KEY (chat_id, message_id))""",
        "ALTER TABLE anon_relay ADD COLUMN IF NOT EXISTS is_prompt BOOLEAN NOT NULL DEFAULT FALSE",
        "ALTER TABLE anon_relay ADD COLUMN IF NOT EXISTS peer_message_id BIGINT",
        "ALTER TABLE anon_relay ADD COLUMN IF NOT EXISTS sent_at TIMESTAMPTZ",
        "ALTER TABLE anon_relay ADD COLUMN IF NOT EXISTS from_bot BOOLEAN",
        "CREATE INDEX idx_anon_relay_conversation ON anon_relay (chat_id, conversation_id, message_id)",
        """CREATE TABLE anon_blocks (
               owner_user_id BIGINT NOT NULL, follower_user_id BIGINT NOT NULL, blocked_at TEXT NOT NULL,
               PRIMARY KEY (owner_user_id, follower_user_id))""",
        """CREATE TABLE activity_events (
               id BIGSERIAL PRIMARY KEY, user_id BIGINT NOT NULL,
               occurred_at TIMESTAMPTZ NOT NULL DEFAULT now())""",
        "CREATE INDEX idx_activity_events_occurred_at ON activity_events (occurred_at)",
        """CREATE TABLE promo_seen (
               user_id BIGINT NOT NULL, promo TEXT NOT NULL,
               seen_at TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY (user_id, promo))""",
    ):
        conn.execute(statement)


def seed_plain_a(conn) -> None:
    """Everything a busy 2.1.0 inbox holds, with real Telegram ids in it.

    Two owners, three guests; conversations both ways round (guest 6101 writes
    to both owners); follower state, relays with times and sides, one block,
    donation prompts, one paid donation with a username; and 6102, who has
    activity but never picked a language, so has no user_settings row.
    """
    conn.execute("INSERT INTO user_settings (user_id, language) VALUES (%s,'en'),(%s,'uz'),(%s,'ru')",
                 (OWNER_1, OWNER_2, GUEST_1))
    conn.execute("INSERT INTO anon_links (owner_user_id, token, created_at) VALUES (%s,'tok51',%s),(%s,'tok52',%s)",
                 (OWNER_1, T0, OWNER_2, T0))
    for cid, owner, follower, number, first in (
            (1, OWNER_1, GUEST_1, 1, T0), (2, OWNER_1, GUEST_2, 1, T1),
            (3, OWNER_2, GUEST_1, 1, T0), (4, OWNER_2, GUEST_3, 1, None)):
        conn.execute(
            "INSERT INTO anon_conversations (id, owner_user_id, follower_user_id, conv_number, started_at, "
            "last_activity_at, last_owner_msg_id, last_follower_msg_id, follower_first_msg_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (cid, owner, follower, number, T0, T1, 10 * cid, 10 * cid + 1, first))
    conn.execute("SELECT setval(pg_get_serial_sequence('anon_conversations','id'), 100)")
    conn.execute("INSERT INTO anon_follower_state (follower_user_id, owner_user_id, conversation_id) "
                 "VALUES (%s,%s,1),(%s,%s,2)", (GUEST_1, OWNER_1, GUEST_2, OWNER_1))
    conn.execute(
        "INSERT INTO anon_relay (chat_id, message_id, conversation_id, peer_message_id, sent_at, from_bot) VALUES "
        "(%s,10,1,20,'2026-01-01T10:00:00Z',TRUE),(%s,20,1,10,'2026-01-01T10:00:01Z',FALSE),"
        "(%s,30,2,40,'2026-01-02T10:00:00Z',TRUE),(%s,40,2,30,NULL,NULL)",
        (OWNER_1, GUEST_1, OWNER_1, GUEST_2))
    conn.execute("INSERT INTO anon_blocks (owner_user_id, follower_user_id, blocked_at) VALUES (%s,%s,%s)",
                 (OWNER_2, GUEST_3, T1))
    conn.execute("INSERT INTO donation_prompts (user_id, action_count) VALUES (%s,3),(%s,1)", (GUEST_1, OWNER_1))
    for who in (OWNER_1, GUEST_1, GUEST_2, GUEST_2):
        conn.execute("INSERT INTO activity_events (user_id) VALUES (%s)", (who,))
    conn.execute("INSERT INTO promo_seen (user_id, promo) VALUES (%s,'gift'),(%s,'gift'),(%s,'other'),(%s,'gift')",
                 (OWNER_1, GUEST_1, GUEST_1, PROMO_ONLY))
    conn.execute(
        "INSERT INTO star_transactions (user_id, username, amount_stars, item, status, payload, created_at, updated_at) "
        "VALUES (%s,'alice',50,'donation','paid','p1',%s,%s),(%s,NULL,5,'donation','paid','p2',%s,%s)",
        (GUEST_1, T0, T0, GUEST_2, T0, T0))


def seed_partial(conn) -> None:
    """Rows that are already part-converted -- what a crashed earlier run, or
    the new code writing into a not-yet-converted table, leaves behind: a
    conversation whose follower is a pseudonym with its seal but whose owner
    is still plain, a settings row and an activity row that are done."""
    conn.execute("ALTER TABLE anon_conversations ADD COLUMN IF NOT EXISTS owner_sealed BYTEA")
    conn.execute("ALTER TABLE anon_conversations ADD COLUMN IF NOT EXISTS follower_sealed BYTEA")
    conn.execute("ALTER TABLE user_settings ADD COLUMN IF NOT EXISTS user_sealed BYTEA")
    conn.execute(
        "INSERT INTO anon_conversations (id, owner_user_id, follower_user_id, conv_number, started_at, "
        "last_activity_at, follower_first_msg_at, follower_sealed) VALUES (5,%s,%s,1,%s,%s,%s,%s)",
        (OWNER_2, P(GUEST_2), T0, T1, T0, vault.seal_id(GUEST_2, "anon.conversation.follower")))
    conn.execute("INSERT INTO user_settings (user_id, language, user_sealed) VALUES (%s,'ru',%s)",
                 (P(NEWCOMER), vault.seal_id(NEWCOMER, "anon.user")))
    conn.execute("INSERT INTO activity_events (user_id) VALUES (%s)", (P(NEWCOMER),))
    conn.execute("INSERT INTO promo_seen (user_id, promo) VALUES (%s,'gift')", (P(NEWCOMER),))


def dump(conn, schema: str) -> dict:
    """Every row of every table in the schema, to compare two states."""
    out = {}
    for (table,) in conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = %s ORDER BY 1", (schema,)):
        rows = conn.execute(f'SELECT * FROM "{schema}"."{table}"').fetchall()
        out[table] = sorted(repr(r) for r in rows)
    return out


def converted(counts: dict) -> int:
    return sum(v for k, v in counts.items() if not k.startswith("skipped") and not k.endswith("dropped"))


def columns_of(conn, schema: str, table: str) -> set:
    return {r[0] for r in conn.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_schema = %s AND table_name = %s",
        (schema, table))}


def sql(conn, text: str, params=()):
    return conn.execute(text, params).fetchall()


def traces(conn, schema: str, uid: int, name: str | None = None) -> list[str]:
    """Every place in the schema where `uid` is still to be found: his
    pseudonym in a BIGINT column, a sealed value that opens to him (under any
    purpose, with no `expected`), or his name. star_transactions is the
    documented exception: its row stays, and only the sealed username goes."""
    found = []
    person = P(uid)
    for table, column, kind in conn.execute(
            "SELECT table_name, column_name, data_type FROM information_schema.columns "
            "WHERE table_schema = %s AND data_type IN ('bigint','bytea')", (schema,)).fetchall():
        qualified = f'"{schema}"."{table}"'
        if kind == "bigint" and table != "star_transactions":
            n = conn.execute(f'SELECT count(*) FROM {qualified} WHERE "{column}" = %s', (person,)).fetchone()[0]
            if n:
                found.append(f"{table}.{column} holds the pseudonym x{n}")
        if kind == "bytea":
            for (blob,) in conn.execute(f'SELECT "{column}" FROM {qualified} WHERE "{column}" IS NOT NULL').fetchall():
                for purpose in ("anon.link.owner", "anon.conversation.owner",
                                "anon.conversation.follower", "anon.user"):
                    try:
                        if vault.open_id(blob, purpose) == uid:
                            found.append(f"{table}.{column} opens to the id as {purpose}")
                    except (vault.VaultError, ValueError, TypeError):
                        pass
                if name is not None:
                    try:
                        if vault.open_text(blob, "stars.username") == name:
                            found.append(f"{table}.{column} opens to the name")
                    except (vault.VaultError, ValueError, TypeError):
                        pass
    if "username_sealed" in columns_of(conn, schema, "star_transactions"):
        n = conn.execute(f'SELECT count(*) FROM "{schema}".star_transactions '
                         f'WHERE user_id = %s AND username_sealed IS NOT NULL', (PAYER(uid),)).fetchone()[0]
        if n:
            found.append(f"star_transactions keeps {n} sealed username(s) of the person")
    return found


# ---------------------------------------------------------------------------
# The groups
# ---------------------------------------------------------------------------

def group_a(admin) -> None:
    section("A. converting a 2.1.0 database")
    make_210_schema(admin, SCHEMA_A)
    seed_plain_a(admin)
    seed_partial(admin)
    admin.commit()

    leaks, _cols, rows = sealed_check.real_ids_left(admin, SCHEMA_A)
    check("before: the check finds the plain ids (so a clean result later means something)",
          len(leaks) >= 8 and rows > 15, "; ".join(leaks))
    check("before: star_transactions.username and anon_relay.sent_at / from_bot exist",
          "username" in columns_of(admin, SCHEMA_A, "star_transactions")
          and {"sent_at", "from_bot"} <= columns_of(admin, SCHEMA_A, "anon_relay"))

    try:
        counts = db.convert_personal_data_v1(admin, SCHEMA_A)
    except Exception as exc:
        admin.rollback()
        check("the conversion runs on 2.1.0 data, including rows that are already part-converted",
              False, f"{type(exc).__name__}: {exc}")
        return
    admin.commit()
    print("       counts:", {k: v for k, v in counts.items() if v})
    expect = {"anon_links": 2, "anon_conversations": 5, "anon_follower_state": 2, "anon_relay": 4,
              "anon_blocks": 1, "user_settings": 3, "donation_prompts": 2, "activity_events": 4,
              "star_transactions": 2, "star_transactions.username_sealed": 1,
              "star_transactions.username_dropped": 1, "anon_relay.columns_dropped": 2,
              "user_settings.added_for_activity": 1, "promo_seen": 4, "promo_seen.columns_dropped": 1}
    check("the conversion reports what it changed, table by table",
          all(counts.get(k) == v for k, v in expect.items()),
          str({k: (counts.get(k), v) for k, v in expect.items() if counts.get(k) != v}))
    check("the part-converted rows were left alone, not converted twice",
          counts["user_settings"] == 3 and counts["activity_events"] == 4)

    leaks, columns, rows = sealed_check.real_ids_left(admin, SCHEMA_A)
    check(f"after: no identity column holds a real id ({columns} columns, {rows} values)",
          not leaks and columns >= 10, "; ".join(leaks))
    check("pseudonyms are in place (links, blocks, settings, activity)",
          {r[0] for r in sql(admin, f'SELECT owner_user_id FROM "{SCHEMA_A}".anon_links')} == {P(OWNER_1), P(OWNER_2)}
          and sql(admin, f'SELECT owner_user_id, follower_user_id FROM "{SCHEMA_A}".anon_blocks')
          == [(P(OWNER_2), P(GUEST_3))]
          and {r[0] for r in sql(admin, f'SELECT user_id FROM "{SCHEMA_A}".user_settings')}
          == {P(OWNER_1), P(OWNER_2), P(GUEST_1), P(GUEST_2), P(NEWCOMER)}
          and {r[0] for r in sql(admin, f'SELECT user_id FROM "{SCHEMA_A}".activity_events')}
          == {P(OWNER_1), P(GUEST_1), P(GUEST_2), P(NEWCOMER)})
    promo = sorted(sql(admin, f'SELECT user_id, promo FROM "{SCHEMA_A}".promo_seen'))
    check("promo_seen (2.2.0): the plain ids are pseudonyms, the announcements are the same, and the part-converted "
          "row was left alone",
          promo == sorted([(P(OWNER_1), "gift"), (P(GUEST_1), "gift"), (P(GUEST_1), "other"),
                           (P(PROMO_ONLY), "gift"), (P(NEWCOMER), "gift")]), str(promo))
    check("promo_seen (2.2.0): the time each was shown is gone",
          "seen_at" not in columns_of(admin, SCHEMA_A, "promo_seen"))
    check("a private chat and its person get the same pseudonym in anon_relay",
          {r[0] for r in sql(admin, f'SELECT chat_id FROM "{SCHEMA_A}".anon_relay')}
          == {P(OWNER_1), P(GUEST_1), P(GUEST_2)})

    opened_ok = True
    for cid, owner, follower in sql(admin, f'SELECT id, owner_user_id, follower_user_id FROM "{SCHEMA_A}".anon_conversations'):
        o, f, os_, fs = sql(admin, f'SELECT owner_user_id, follower_user_id, owner_sealed, follower_sealed '
                                   f'FROM "{SCHEMA_A}".anon_conversations WHERE id = %s', (cid,))[0]
        try:
            opened_ok &= vault.open_id(os_, "anon.conversation.owner", expected=o) in (OWNER_1, OWNER_2)
            opened_ok &= vault.open_id(fs, "anon.conversation.follower", expected=f) in (GUEST_1, GUEST_2, GUEST_3)
        except Exception:
            opened_ok = False
    check("conversation seals open to the original ids", opened_ok)
    link = sql(admin, f"SELECT owner_user_id, owner_sealed FROM \"{SCHEMA_A}\".anon_links WHERE token = 'tok51'")[0]
    check("a link's seal opens to its owner (with and without expected=)",
          vault.open_id(link[1], "anon.link.owner", expected=link[0]) == OWNER_1
          and vault.open_id(link[1], "anon.link.owner") == OWNER_1)
    name = sql(admin, f"SELECT username_sealed FROM \"{SCHEMA_A}\".star_transactions WHERE payload = 'p1'")[0][0]
    check("the username opens to the original name", vault.open_text(name, "stars.username") == "alice")
    check("a payment with no username has nothing sealed",
          sql(admin, f"SELECT username_sealed FROM \"{SCHEMA_A}\".star_transactions WHERE payload = 'p2'")
          == [(None,)])
    check("star_transactions.username is gone", "username" not in columns_of(admin, SCHEMA_A, "star_transactions"))
    paid = dict(sql(admin, f'SELECT payload, user_id FROM "{SCHEMA_A}".star_transactions'))
    check("H1: the conversion stores each payer under pseudonym_in(id, 'anon.payer')",
          paid == {"p1": PAYER(GUEST_1), "p2": PAYER(GUEST_2)}, str(paid))
    check("H1: ...which is a pseudonym by range, and is not pseudonym(id) of the same person",
          all(vault.is_pseudonym(v) for v in paid.values())
          and paid["p1"] != P(GUEST_1) and paid["p2"] != P(GUEST_2))
    identity = set()
    for table, column in (("user_settings", "user_id"), ("donation_prompts", "user_id"),
                          ("activity_events", "user_id"), ("promo_seen", "user_id"), ("anon_links", "owner_user_id"),
                          ("anon_conversations", "owner_user_id"), ("anon_conversations", "follower_user_id"),
                          ("anon_blocks", "owner_user_id"), ("anon_blocks", "follower_user_id"),
                          ("anon_follower_state", "follower_user_id"), ("anon_follower_state", "owner_user_id"),
                          ("anon_relay", "chat_id")):
        identity |= {r[0] for r in sql(admin, f'SELECT "{column}" FROM "{SCHEMA_A}"."{table}"')}
    check("H1: no payer stand-in equals any pseudonym() held by another table of the schema "
          f"(so the ledger cannot be matched to a person's rows; {len(identity)} values compared)",
          len(identity) >= 6 and not (set(paid.values()) & identity))
    check("anon_relay.sent_at and from_bot are gone",
          not ({"sent_at", "from_bot"} & columns_of(admin, SCHEMA_A, "anon_relay")))
    flags = dict(sql(admin, f'SELECT user_id, from_activity FROM "{SCHEMA_A}".user_settings'))
    check("item 4: the settings row the conversion made for a person with activity only is marked from_activity; "
          "the ones people had before are not",
          flags.get(P(GUEST_2)) is True and flags.get(P(GUEST_1)) is False and flags.get(P(OWNER_1)) is False,
          str(flags))
    row = sql(admin, f'SELECT language, user_sealed, knows_reactions FROM "{SCHEMA_A}".user_settings '
                     f'WHERE user_id = %s', (P(GUEST_2),))
    check("the person with activity but no settings row now has one, sealed, with no language",
          len(row) == 1 and row[0][0] is None and row[0][2] is True
          and vault.open_id(row[0][1], "anon.user", expected=P(GUEST_2)) == GUEST_2, str(row))

    before = dump(admin, SCHEMA_A)
    again = db.convert_personal_data_v1(admin, SCHEMA_A)
    admin.commit()
    check("a second run reports 0 everywhere", all(v == 0 for v in again.values()), str(again))
    check("a second run changes no row", dump(admin, SCHEMA_A) == before)

    verified = db.verify_personal_data_v1(admin, SCHEMA_A)
    admin.rollback()
    check("verify passes and counts what it opened",
          verified.get("anon_conversations.owner_sealed") == 5 and verified.get("user_settings.user_sealed") == 5
          and verified.get("star_transactions.username_sealed") == 1, str(verified))

    # The bot starting up on the converted database is the next thing that happens.
    db.init_db()
    check("init_db() on the converted database changes nothing it should not",
          dump(admin, SCHEMA_A) == before
          and not ({"sent_at", "from_bot"} & columns_of(admin, SCHEMA_A, "anon_relay"))
          and "username" not in columns_of(admin, SCHEMA_A, "star_transactions"))
    admin.commit()

    # The ids a person sees are the ones that were stored.
    c1 = db.get_conversation(1)
    check("get_conversation returns the real owner and follower",
          c1 is not None and (c1["owner_user_id"], c1["follower_user_id"]) == (OWNER_1, GUEST_1), str(c1))
    check("get_conversation(5), the part-converted row, returns the real ids too",
          (db.get_conversation(5) or {}).get("owner_user_id") == OWNER_2
          and (db.get_conversation(5) or {}).get("follower_user_id") == GUEST_2)
    check("get_link_view returns the real owner, and sees a block",
          db.get_link_view("tok51", GUEST_1) == (OWNER_1, False, False)
          and db.get_link_view("tok52", GUEST_3) == (OWNER_2, False, True)
          and db.get_link_view("nope", GUEST_1) == (None, False, False))
    route = db.get_reaction_route(GUEST_1, 20)
    check("get_reaction_route finds the twin and both real ids",
          route == {"peer_message_id": 10, "conversation_id": 1, "owner_user_id": OWNER_1,
                    "follower_user_id": GUEST_1, "blocked": False}, str(route))
    ctx = db.get_routing_context(GUEST_1, GUEST_1)
    check("get_routing_context routes a guest to the real owner",
          (ctx["active"] or {}).get("owner_user_id") == OWNER_1
          and (ctx["active"] or {}).get("follower_user_id") == GUEST_1, str(ctx))
    check("get_routing_context counts what an owner owns", db.get_routing_context(OWNER_1, OWNER_1)["owned"] == 2)
    active = db.get_active_conversation_for_follower(GUEST_2)
    check("get_active_conversation_for_follower returns the real owner",
          (active or {}).get("owner_user_id") == OWNER_1, str(active))
    check("list_owner_conversations lists the owner's live threads",
          sorted(c["id"] for c in db.list_owner_conversations(OWNER_1)) == [1, 2]
          and sorted(c["id"] for c in db.list_owner_conversations(OWNER_2)) == [3, 5])
    check("list_anon_blocks_with_conversations finds the blocked guest through the pseudonyms",
          db.list_anon_blocks_with_conversations(OWNER_2) == [(4, [1])])
    check("get_relay_row / get_conversation_for_relay find a bubble by its chat",
          db.get_conversation_for_relay(GUEST_1, 20) == 1 and db.get_conversation_for_relay(OWNER_1, 30) == 2)
    check("the owner's counters count by pseudonym",
          db.count_owner_conversations(OWNER_1) == 2 and db.get_user_language(OWNER_1) == "en"
          and db.get_user_language(GUEST_2) is None)

    # 2.1.0 broadcast reach: settings UNION activity, and activity alone.
    since = db_now_minus_a_day()
    check("list_all_users returns the real ids, activity-only people included (the 2.1.0 set)",
          sorted(db.list_all_users()) == sorted({OWNER_1, OWNER_2, GUEST_1, GUEST_2, NEWCOMER}),
          str(sorted(db.list_all_users())))
    check("active_user_ids_since returns the real ids of everyone with activity (the 2.1.0 set)",
          sorted(db.active_user_ids_since(since)) == sorted({OWNER_1, GUEST_1, GUEST_2, NEWCOMER}),
          str(sorted(db.active_user_ids_since(since))))
    check("count_active_users_since still counts people, not events", db.count_active_users_since(since) == 4)

    # New activity keeps new people reachable.
    db.record_activity_batch({GUEST_3, OWNER_1})
    check("record_activity_batch gives a new person a sealed settings row",
          GUEST_3 in db.list_all_users() and GUEST_3 in db.active_user_ids_since(since)
          and sql(admin, f'SELECT language FROM "{SCHEMA_A}".user_settings WHERE user_id = %s', (P(GUEST_3),))
          == [(None,)])
    admin.commit()
    db.set_user_language(GUEST_3, "uz")
    db.record_activity_batch([GUEST_3, GUEST_3])
    rows = sql(admin, f'SELECT language, user_sealed FROM "{SCHEMA_A}".user_settings WHERE user_id = %s', (P(GUEST_3),))
    admin.commit()
    check("recording activity again keeps one row and does not touch the language",
          len(rows) == 1 and rows[0][0] == "uz" and vault.open_id(rows[0][1], "anon.user") == GUEST_3)
    admin.execute(f'UPDATE "{SCHEMA_A}".user_settings SET user_sealed = NULL WHERE user_id = %s', (P(GUEST_3),))
    admin.commit()
    db.record_activity_batch([GUEST_3])
    rows = sql(admin, f'SELECT user_sealed FROM "{SCHEMA_A}".user_settings WHERE user_id = %s', (P(GUEST_3),))
    admin.commit()
    check("a settings row that lacks its seal is filled in the next time the person is active",
          rows[0][0] is not None and vault.open_id(rows[0][0], "anon.user", expected=P(GUEST_3)) == GUEST_3)
    check("record_activity_batch with nobody does nothing and does not raise",
          db.record_activity_batch([]) is None and db.record_activity_batch(set()) is None)
    # Put the bystander back the way the later groups expect him: no row at all.
    admin.execute(f'DELETE FROM "{SCHEMA_A}".user_settings WHERE user_id = %s', (P(GUEST_3),))
    admin.execute(f'DELETE FROM "{SCHEMA_A}".activity_events WHERE user_id = %s', (P(GUEST_3),))
    admin.commit()

    # H1, the writer and the reader: after the conversion, new payments go in the same way.
    db.record_star_invoice(NEWCOMER, "newbie", 5, "donation", "p3", status="paid")
    stored = sql(admin, f'SELECT user_id, username_sealed FROM "{SCHEMA_A}".star_transactions WHERE payload = %s', ("p3",))
    admin.commit()
    check("H1: a payment written now is stored under the payer stand-in, not pseudonym(), and its name is sealed",
          len(stored) == 1 and stored[0][0] == PAYER(NEWCOMER) and stored[0][0] != P(NEWCOMER)
          and vault.open_text(stored[0][1], "stars.username") == "newbie", str(stored))
    check("H1: bump_donation_action finds a payer by that stand-in (paid: yes), and a non-payer (no)",
          db.bump_donation_action(NEWCOMER)[3] is True and db.bump_donation_action(GUEST_1)[3] is True
          and db.bump_donation_action(OWNER_2)[3] is False)
    admin.execute(f'DELETE FROM "{SCHEMA_A}".star_transactions WHERE payload = %s', ("p3",))
    admin.commit()
    clash = sql(admin, f'SELECT count(*) FROM "{SCHEMA_A}".star_transactions t WHERE t.user_id IN '
                       f'(SELECT user_id FROM "{SCHEMA_A}".donation_prompts)')
    check("H1: ...and the donation counters, which do hold pseudonym(), never match a payment row", clash == [(0,)])


def db_now_minus_a_day():
    from datetime import datetime, timedelta, timezone
    return datetime.now(timezone.utc) - timedelta(days=1)


def rolled_back_verify(admin, change, wants: str, label: str) -> None:
    """Apply `change` in a transaction, expect verify to raise and name `wants`, roll back."""
    try:
        change(admin)
        try:
            db.verify_personal_data_v1(admin, SCHEMA_A)
            check(label, False, "verify accepted it")
        except db.PersonalDataError as exc:
            message = str(exc)
            check(label, wants in message, message)
            check(f"{label}: and the message names no id or name",
                  not re.search(r"\b(5101|5102|6101|6102|6103|7101|alice)\b", message))
    finally:
        admin.rollback()


def group_b(admin) -> None:
    section("B. verify refuses what is not sealed")
    q = f'"{SCHEMA_A}".'
    ok = db.verify_personal_data_v1(admin, SCHEMA_A)
    admin.rollback()
    check("the converted data verifies (the baseline for what follows)", bool(ok))

    plain_cases = [
        ("anon_links", "owner_user_id", "WHERE token = 'tok51'"),
        ("anon_conversations", "owner_user_id", "WHERE id = 1"),
        ("anon_conversations", "follower_user_id", "WHERE id = 1"),
        ("anon_follower_state", "follower_user_id", f"WHERE follower_user_id = {P(GUEST_1)}"),
        ("anon_follower_state", "owner_user_id", f"WHERE follower_user_id = {P(GUEST_1)}"),
        ("anon_relay", "chat_id", f"WHERE message_id = 10"),
        ("anon_blocks", "owner_user_id", ""),
        ("anon_blocks", "follower_user_id", ""),
        ("user_settings", "user_id", f"WHERE user_id = {P(GUEST_1)}"),
        ("donation_prompts", "user_id", f"WHERE user_id = {P(GUEST_1)}"),
        ("activity_events", "user_id", f"WHERE user_id = {P(OWNER_1)}"),
        ("promo_seen", "user_id", f"WHERE user_id = {P(GUEST_1)} AND promo = 'other'"),
        ("star_transactions", "user_id", "WHERE payload = 'p1'"),
    ]
    for table, column, where in plain_cases:
        rolled_back_verify(
            admin, lambda c, t=table, col=column, w=where: c.execute(f"UPDATE {q}{t} SET {col} = 424242 {w}"),
            f"{table}.{column}", f"a plain id left in {table}.{column} fails verify")

    rolled_back_verify(
        admin, lambda c: c.execute(f"UPDATE {q}star_transactions SET user_id = {P(GUEST_1)} WHERE payload = 'p1'"),
        "star_transactions.user_id", "H1: a payment stored under the payer's pseudonym() (linkable) fails verify")
    rolled_back_verify(
        admin, lambda c: c.execute(f"UPDATE {q}star_transactions SET user_id = {P(PROMO_ONLY)} WHERE payload = 'p1'"),
        "star_transactions.user_id", "H1: a payment stored under a pseudonym() that only promo_seen holds fails verify")
    rolled_back_verify(
        admin, lambda c: c.execute(f"UPDATE {q}anon_conversations a SET owner_sealed = b.owner_sealed "
                                   f"FROM {q}anon_conversations b WHERE a.id = 1 AND b.id = 3"),
        "anon_conversations.owner_sealed", "an owner seal copied from another conversation fails verify")
    rolled_back_verify(
        admin, lambda c: c.execute(f"UPDATE {q}anon_conversations a SET follower_sealed = b.follower_sealed "
                                   f"FROM {q}anon_conversations b WHERE a.id = 1 AND b.id = 2"),
        "anon_conversations.follower_sealed", "a follower seal copied from another conversation fails verify")
    rolled_back_verify(
        admin, lambda c: c.execute(f"UPDATE {q}anon_conversations SET follower_sealed = NULL WHERE id = 1"),
        "anon_conversations.follower_sealed", "a missing follower seal fails verify")
    rolled_back_verify(
        admin, lambda c: c.execute(f"UPDATE {q}anon_links a SET owner_sealed = b.owner_sealed "
                                   f"FROM {q}anon_links b WHERE a.token = 'tok51' AND b.token = 'tok52'"),
        "anon_links.owner_sealed", "a link's seal copied from another link fails verify")
    rolled_back_verify(
        admin, lambda c: c.execute(f"UPDATE {q}user_settings a SET user_sealed = b.user_sealed FROM {q}user_settings b "
                                   f"WHERE a.user_id = {P(OWNER_1)} AND b.user_id = {P(OWNER_2)}"),
        "user_settings.user_sealed", "a settings seal copied from another person fails verify")
    rolled_back_verify(
        admin, lambda c: c.execute(f"UPDATE {q}star_transactions SET username_sealed = %s WHERE payload = 'p1'",
                                   (b"not a seal",)),
        "star_transactions.username_sealed", "a username seal that does not open fails verify")
    rolled_back_verify(
        admin, lambda c: c.execute(f"UPDATE {q}star_transactions SET username_sealed = %s WHERE payload = 'p1'",
                                   (vault.seal_text("alice", "anon.user"),)),
        "star_transactions.username_sealed", "a username sealed under the wrong purpose fails verify")
    rolled_back_verify(
        admin, lambda c: c.execute(f"ALTER TABLE {q}star_transactions ADD COLUMN username TEXT"),
        "star_transactions.username", "a plain username column that is still there fails verify")
    rolled_back_verify(
        admin, lambda c: c.execute(f"ALTER TABLE {q}anon_relay ADD COLUMN sent_at TIMESTAMPTZ"),
        "anon_relay.sent_at", "anon_relay.sent_at still there fails verify")
    rolled_back_verify(
        admin, lambda c: c.execute(f"ALTER TABLE {q}anon_relay ADD COLUMN from_bot BOOLEAN"),
        "anon_relay.from_bot", "anon_relay.from_bot still there fails verify")
    rolled_back_verify(
        admin, lambda c: c.execute(f"DELETE FROM {q}user_settings WHERE user_id = {P(GUEST_2)}"),
        "activity_events", "a person with activity and no settings row (unreachable) fails verify")
    ok = db.verify_personal_data_v1(admin, SCHEMA_A)
    admin.rollback()
    check("and after all those rollbacks the data still verifies", bool(ok))

    # An old testbot schema: a table or a column that is not there is reported, not a crash.
    admin.execute('DROP SCHEMA IF EXISTS "anon_enc_test_old" CASCADE')
    admin.execute('CREATE SCHEMA "anon_enc_test_old"')
    admin.execute('CREATE TABLE "anon_enc_test_old".anon_links (owner_user_id BIGINT PRIMARY KEY, token TEXT NOT NULL)')
    admin.execute('INSERT INTO "anon_enc_test_old".anon_links VALUES (%s, %s)', (OWNER_1, "t"))
    admin.commit()
    old = db.convert_personal_data_v1(admin, "anon_enc_test_old")
    check("an old schema is converted as far as it goes, and the rest is named as skipped",
          old["anon_links"] == 1 and "skipped:anon_conversations" in old and "skipped:star_transactions" in old, str(old))
    check("...and verify of it passes on what exists", bool(db.verify_personal_data_v1(admin, "anon_enc_test_old")))
    admin.rollback()
    check("the conversion never committed: rolled back, the old schema still holds the plain id",
          sql(admin, 'SELECT owner_user_id FROM "anon_enc_test_old".anon_links') == [(OWNER_1,)]
          and "owner_sealed" not in columns_of(admin, "anon_enc_test_old", "anon_links"))
    admin.rollback()


def group_c(admin) -> None:
    section("C. a seal moved onto another row is refused")
    q = f'"{SCHEMA_A}".'
    mark = len(collector.records)
    saved = {
        "conv": sql(admin, f"SELECT id, owner_sealed, follower_sealed FROM {q}anon_conversations ORDER BY id"),
        "links": sql(admin, f"SELECT token, owner_sealed FROM {q}anon_links"),
        "users": sql(admin, f"SELECT user_id, user_sealed FROM {q}user_settings"),
    }
    conv = {r[0]: r for r in saved["conv"]}
    # Conversation 1 (owner 5101, guest 6101) gets the sealed owner and the
    # sealed follower of conversation 3 (owner 5102, guest 6101: same guest).
    admin.execute(f"UPDATE {q}anon_conversations SET owner_sealed = %s WHERE id = 1", (conv[3][1],))
    admin.execute(f"UPDATE {q}anon_conversations SET follower_sealed = %s WHERE id = 2", (conv[4][2],))
    admin.execute(f"UPDATE {q}anon_links SET owner_sealed = (SELECT owner_sealed FROM {q}anon_links WHERE token = 'tok52') "
                  f"WHERE token = 'tok51'")
    admin.execute(f"UPDATE {q}user_settings SET user_sealed = (SELECT user_sealed FROM {q}user_settings WHERE user_id = %s) "
                  f"WHERE user_id = %s", (P(OWNER_2), P(OWNER_1)))
    admin.commit()
    try:
        check("get_conversation refuses a conversation whose owner seal belongs to another row",
              db.get_conversation(1) is None)
        check("...and one whose follower seal does", db.get_conversation(2) is None)
        check("...while the untouched conversations still answer",
              (db.get_conversation(3) or {}).get("owner_user_id") == OWNER_2)
        check("get_reaction_route refuses it, rather than send to the wrong person",
              db.get_reaction_route(GUEST_1, 20) is None and db.get_reaction_route(OWNER_1, 30) is None)
        check("get_link_view treats a link with a moved seal as no link at all, never the other owner",
              db.get_link_view("tok51", GUEST_1) == (None, False, False)
              and db.get_link_view("tok52", GUEST_1) == (OWNER_2, False, False))
        check("get_routing_context / get_active_conversation_for_follower give no wrong owner",
              (db.get_routing_context(GUEST_1, GUEST_1)["active"]) is None
              and db.get_active_conversation_for_follower(GUEST_1) is None)
        users = db.list_all_users()
        check("list_all_users skips the person whose seal moved, and returns no wrong id",
              OWNER_1 not in users and sorted(users) == sorted({OWNER_2, GUEST_1, GUEST_2, NEWCOMER}), str(sorted(users)))
        check("active_user_ids_since the same",
              OWNER_1 not in db.active_user_ids_since(db_now_minus_a_day()))
        said = [r for r in collector.records[mark:] if r.levelno >= logging.WARNING]
        text = " | ".join(r.getMessage() for r in said)
        check("each refusal is a warning naming the table and the row",
              "anon_conversations" in text and "anon_links" in text and "user_settings" in text, text)
        check("...and none of them contains a real id or a name",
              not re.search(r"\b(5101|5102|6101|6102|6103|7101|alice)\b", text), text)
        try:
            db.verify_personal_data_v1(admin, SCHEMA_A)
            check("verify names the same rows as unfit", False)
        except db.PersonalDataError as exc:
            check("verify names the same rows as unfit", "owner_sealed" in str(exc) and "follower_sealed" in str(exc))
        admin.rollback()
    finally:
        for cid, o, f in saved["conv"]:
            admin.execute(f"UPDATE {q}anon_conversations SET owner_sealed = %s, follower_sealed = %s WHERE id = %s", (o, f, cid))
        for token, sealed in saved["links"]:
            admin.execute(f"UPDATE {q}anon_links SET owner_sealed = %s WHERE token = %s", (sealed, token))
        for user, sealed in saved["users"]:
            admin.execute(f"UPDATE {q}user_settings SET user_sealed = %s WHERE user_id = %s", (sealed, user))
        admin.commit()
    check("restored: everything answers again",
          (db.get_conversation(1) or {}).get("owner_user_id") == OWNER_1
          and db.get_link_view("tok51", GUEST_1)[0] == OWNER_1 and OWNER_1 in db.list_all_users())


def group_d(admin) -> None:
    section("D. /deletemydata leaves nothing of the person")
    q = f'"{SCHEMA_A}".'
    before = traces(admin, SCHEMA_A, GUEST_1, "alice")
    check("before: the person is findable (so the search means something)",
          len(before) >= 8 and any("opens to the name" in t for t in before)
          and any("anon.conversation.follower" in t for t in before), "; ".join(before))
    check("before: the person was shown two announcements, and OWNER_1 one",
          sql(admin, f"SELECT count(*) FROM {q}promo_seen WHERE user_id = %s", (P(GUEST_1),)) == [(2,)]
          and sql(admin, f"SELECT count(*) FROM {q}promo_seen WHERE user_id = %s", (P(OWNER_1),)) == [(1,)])
    removed = db.erase_user(GUEST_1)
    admin.commit()
    after = traces(admin, SCHEMA_A, GUEST_1, "alice")
    check("after erase_user: no pseudonym, no sealed id, no sealed name of the person anywhere",
          not after, "; ".join(after))
    check("the payment row is kept (a refund is issued against it) without the name",
          sql(admin, f"SELECT count(*) FROM {q}star_transactions WHERE payload = 'p1'") == [(1,)]
          and sql(admin, f"SELECT username_sealed FROM {q}star_transactions WHERE payload = 'p1'") == [(None,)])
    check("promo_seen: the erased person's announcements are gone and nobody else's",
          sql(admin, f"SELECT count(*) FROM {q}promo_seen WHERE user_id = %s", (P(GUEST_1),)) == [(0,)]
          and sql(admin, f"SELECT count(*) FROM {q}promo_seen WHERE user_id = %s", (P(OWNER_1),)) == [(1,)])
    check("erase_user counted what it removed", removed >= 8, str(removed))
    check("their conversations are gone from both sides",
          db.get_conversation(1) is None and db.get_conversation(3) is None
          and db.get_reaction_route(GUEST_1, 20) is None)
    check("the person is no longer in the broadcast lists",
          GUEST_1 not in db.list_all_users() and GUEST_1 not in db.active_user_ids_since(db_now_minus_a_day()))
    check("people next to them are untouched",
          (db.get_conversation(2) or {}).get("follower_user_id") == GUEST_2
          and (db.get_conversation(4) or {}).get("follower_user_id") == GUEST_3
          and db.get_link_view("tok51", GUEST_2)[0] == OWNER_1
          and {OWNER_1, OWNER_2, GUEST_2} <= set(db.list_all_users()))
    check("an owner who erases loses the link too, and their threads",
          db.erase_user(OWNER_1) > 0 and db.get_link_view("tok51", GUEST_2) == (None, False, False)
          and not traces(admin, SCHEMA_A, OWNER_1))
    admin.commit()
    leaks, _c, _r = sealed_check.real_ids_left(admin, SCHEMA_A)
    check("still no real id anywhere after erasing", not leaks)


def group_e(admin) -> None:
    section("E. db_merge.py")
    q_b = f'"{SCHEMA_B}".'
    # Source: schema A, but erased people have gone; rebuild it so the merge has something to carry.
    make_210_schema(admin, SCHEMA_A)
    seed_plain_a(admin)
    db_convert(admin, SCHEMA_A)
    # Target: a second database, here a second schema, with one conversation and one link in common,
    # one person who disagrees about a language, and one conversation of its own.
    make_210_schema(admin, SCHEMA_B)
    admin.execute("INSERT INTO user_settings (user_id, language) VALUES (%s,'uz'),(%s,'ru')", (OWNER_1, GUEST_1))
    admin.execute("INSERT INTO anon_links (owner_user_id, token, created_at) VALUES (%s,'tok51',%s),(%s,'tok53',%s)",
                  (OWNER_1, T0, 5103, T0))
    admin.execute(
        "INSERT INTO anon_conversations (id, owner_user_id, follower_user_id, conv_number, started_at, last_activity_at) "
        "VALUES (50,%s,%s,1,%s,%s),(51,%s,%s,1,%s,%s)", (OWNER_1, GUEST_1, T0, T1, 5103, GUEST_1, T0, T1))
    admin.execute("SELECT setval(pg_get_serial_sequence('anon_conversations','id'), 100)")
    db_convert(admin, SCHEMA_B)

    stamp_before = sql(admin, "SELECT value FROM family.settings WHERE key = 'data_key:v1'")
    real = vault.fingerprint()

    def set_stamp(value):
        admin.execute("DELETE FROM family.settings WHERE key = 'data_key:v1'")
        if value is not None:
            admin.execute("INSERT INTO family.settings (key, value) VALUES ('data_key:v1', %s)", (value,))
        admin.commit()

    real_connect, real_ensure, real_stamp = db.connect, db.ensure_schema, db_merge.key_stamp
    routes = {"src": SCHEMA_A, "dst": SCHEMA_B}

    def routed(dsn=None):
        return psycopg.connect(DSN, options=f"-c search_path={routes[dsn]},public")

    db.connect, db.ensure_schema = routed, (lambda dsn=None: None)

    def counts_b():
        return {t: sql(admin, f"SELECT count(*) FROM {q_b}{t}")[0][0] for t in
                ("anon_conversations", "anon_links", "anon_relay", "anon_follower_state", "user_settings",
                 "anon_blocks", "star_transactions", "donation_prompts")}

    try:
        # --- refusals: nothing may be written ---
        start = counts_b()
        columns_start = columns_of(admin, SCHEMA_B, "anon_conversations")
        set_stamp(None)
        refused = False
        try:
            db_merge.run_merge("src", "dst", dry_run=False)
        except db_merge.MergeRefused:
            refused = True
        check("no data_key:v1 stamp: refused", refused)

        set_stamp("0123456789abcdef")
        refused = False
        try:
            db_merge.run_merge("src", "dst", dry_run=False)
        except db_merge.MergeRefused:
            refused = True
        check("the same stamp in both, but not this machine's key: refused", refused)

        set_stamp(real)
        calls = []

        def other_key_on_dst(conn):
            calls.append(1)
            return real if len(calls) % 2 == 1 else "ffffffffffffffff"   # source first, then target

        db_merge.key_stamp = other_key_on_dst
        refused = False
        try:
            db_merge.run_merge("src", "dst", dry_run=False)
        except db_merge.MergeRefused as exc:
            refused = "different data keys" in str(exc)
        db_merge.key_stamp = real_stamp
        check("databases stamped with different data keys: refused before anything is written", refused)
        check("...and not one row, nor one column, was written to the target by any refusal",
              counts_b() == start and columns_of(admin, SCHEMA_B, "anon_conversations") == columns_start)

        # --- the same key: merged ---
        set_stamp(real)
        dry = db_merge.run_merge("src", "dst", dry_run=True)
        check("a dry run reports what it would add and writes nothing", counts_b() == start and bool(dry), str(dry))
        report = db_merge.run_merge("src", "dst", dry_run=False)
        text = "\n".join(report)
        print("       " + text.replace("\n", "\n       "))
        after = counts_b()
        check("conversations: the shared one is matched by pseudonyms, the other three are added",
              after["anon_conversations"] == start["anon_conversations"] + 3 and "+3 added, 1 already identical" in text,
              text)
        check("links: the shared one is identical, the other is added",
              after["anon_links"] == start["anon_links"] + 1)
        check("a settings row both have and disagree on is a conflict kept as the target's, never overwritten",
              "conflict on user_settings" in text
              and sql(admin, f"SELECT language FROM {q_b}user_settings WHERE user_id = %s", (P(OWNER_1),)) == [("uz",)])
        check("follower state and relays arrive, pointing at the right conversations",
              after["anon_follower_state"] == 2 and after["anon_relay"] == 4)
        shared = sql(admin, f"SELECT id FROM {q_b}anon_conversations WHERE owner_user_id = %s AND follower_user_id = %s "
                            f"AND started_at = %s", (P(OWNER_1), P(GUEST_1), T0))
        check("the shared conversation kept the target's own id, and the follower state points at it",
              shared == [(50,)]
              and sql(admin, f"SELECT conversation_id FROM {q_b}anon_follower_state WHERE follower_user_id = %s",
                      (P(GUEST_1),)) == [(50,)])
        opened = True
        for owner, follower, os_, fs in sql(admin, f"SELECT owner_user_id, follower_user_id, owner_sealed, follower_sealed "
                                                   f"FROM {q_b}anon_conversations"):
            try:
                vault.open_id(os_, "anon.conversation.owner", expected=owner)
                vault.open_id(fs, "anon.conversation.follower", expected=follower)
            except Exception:
                opened = False
        check("every conversation in the target carries seals that open to its own pseudonyms", opened)
        leaks, _c, _r = sealed_check.real_ids_left(admin, SCHEMA_B)
        check("the merged database has no real id in it", not leaks, "; ".join(leaks))
        try:
            db.verify_personal_data_v1(admin, SCHEMA_B)
            check("...and it verifies", True)
        except db.PersonalDataError as exc:
            check("...and it verifies", False, str(exc))
        admin.rollback()
        second = db_merge.run_merge("src", "dst", dry_run=False)
        check("merging again adds nothing", counts_b() == after and not any("+1" in l or "+2" in l or "+3" in l for l in second))
    finally:
        db.connect, db.ensure_schema, db_merge.key_stamp = real_connect, real_ensure, real_stamp
        admin.rollback()
        admin.execute("DELETE FROM family.settings WHERE key = 'data_key:v1'")
        for (value,) in stamp_before:
            admin.execute("INSERT INTO family.settings (key, value) VALUES ('data_key:v1', %s)", (value,))
        admin.commit()


def db_convert(admin, schema: str) -> dict:
    counts = db.convert_personal_data_v1(admin, schema)
    db.verify_personal_data_v1(admin, schema)
    admin.commit()
    return counts


def group_g(admin) -> None:
    section("G. broadcast reach: an activity-only settings row goes with the activity")
    make_210_schema(admin, SCHEMA_A)
    ACTIVE_ONLY, CHOSE_EARLY, CHOSE_LATER, RECENT, NO_ACTIVITY = 8101, 8102, 8103, 8104, 8105
    admin.execute("INSERT INTO user_settings (user_id, language) VALUES (%s,'en')", (CHOSE_EARLY,))
    for who in (ACTIVE_ONLY, CHOSE_EARLY):
        admin.execute("INSERT INTO activity_events (user_id) VALUES (%s)", (who,))
    admin.commit()
    counts = db.convert_personal_data_v1(admin, SCHEMA_A)
    admin.commit()
    check("the conversion gives the activity-only person a settings row", counts["user_settings.added_for_activity"] == 1,
          str(counts))
    db.init_db()
    flags = dict(sql(admin, f'SELECT user_id, from_activity FROM "{SCHEMA_A}".user_settings'))
    check("item 4: from_activity is TRUE for the converted activity-only row, FALSE for the chosen one",
          flags == {P(ACTIVE_ONLY): True, P(CHOSE_EARLY): False}, str(flags))
    default = sql(admin, "SELECT column_default, is_nullable FROM information_schema.columns "
                         "WHERE table_schema = %s AND table_name = 'user_settings' AND column_name = 'from_activity'",
                  (SCHEMA_A,))
    check("...the column is NOT NULL DEFAULT FALSE, and init_db() on the converted schema left it so",
          default == [("false", "NO")], str(default))

    db.record_activity_batch([CHOSE_LATER, RECENT])
    db.set_user_language(CHOSE_LATER, "ru")
    db.set_user_language(NO_ACTIVITY, "uz")
    flags = dict(sql(admin, f'SELECT user_id, from_activity FROM "{SCHEMA_A}".user_settings'))
    admin.commit()
    check("record_activity_batch marks the rows it creates; choosing a language clears the mark; "
          "a row made by choosing is never marked",
          flags[P(CHOSE_LATER)] is False and flags[P(RECENT)] is True and flags[P(NO_ACTIVITY)] is False, str(flags))
    db.record_activity_batch([CHOSE_EARLY])
    check("activity from a person who already has a row does not mark it",
          dict(sql(admin, f'SELECT user_id, from_activity FROM "{SCHEMA_A}".user_settings'))[P(CHOSE_EARLY)] is False)
    admin.commit()

    everyone = {ACTIVE_ONLY, CHOSE_EARLY, CHOSE_LATER, RECENT, NO_ACTIVITY}
    check("before the prune everyone is reachable by a broadcast", set(db.list_all_users()) == everyone,
          str(sorted(db.list_all_users())))
    check("a prune with nothing aged out removes nothing", db.prune_old_data() == 0)
    # Age the activity of everyone but RECENT past the retention.
    admin.execute(f'UPDATE "{SCHEMA_A}".activity_events SET occurred_at = now() - make_interval(days => %s) '
                  f'WHERE user_id <> %s', (db.ACTIVITY_RETENTION_DAYS + 1, P(RECENT)))
    admin.commit()
    aged = sql(admin, f'SELECT count(*) FROM "{SCHEMA_A}".activity_events WHERE user_id <> %s', (P(RECENT),))[0][0]
    removed = db.prune_old_data()
    left = {r[0] for r in sql(admin, f'SELECT user_id FROM "{SCHEMA_A}".user_settings')}
    admin.commit()
    check("item 4: the settings row made only by activity goes once its activity is gone; "
          "rows with a chosen language stay (before or after the activity), and so does a recent one",
          left == {P(CHOSE_EARLY), P(CHOSE_LATER), P(RECENT), P(NO_ACTIVITY)}, str(len(left)))
    check("...and the number returned counts those rows as well as the aged events",
          removed == aged + 1, f"{removed} vs {aged} events + 1 row")
    check("...so the broadcast reaches who it reached in 2.1.0 (settings union activity, activity pruned)",
          set(db.list_all_users()) == everyone - {ACTIVE_ONLY}, str(sorted(db.list_all_users())))
    check("a second prune finds nothing more", db.prune_old_data() == 0)


def group_h(admin) -> None:
    section("H. promo_seen, 2.2.0's announcements table, is sealed too")
    q = f'"{SCHEMA_A}".'
    check("a person claims an announcement: True once, False after",
          db.claim_promo_seen(PROMO_NEW, "gift") is True and db.claim_promo_seen(PROMO_NEW, "gift") is False)
    check("the row holds the pseudonym of the person, once",
          sql(admin, f"SELECT user_id, promo FROM {q}promo_seen WHERE user_id = %s", (P(PROMO_NEW),))
          == [(P(PROMO_NEW), "gift")])
    check("no row of promo_seen holds a real id, and no time is kept",
          sql(admin, f"SELECT count(*) FROM {q}promo_seen WHERE user_id >= %s", (vault.PSEUDONYM_CEILING,)) == [(0,)]
          and "seen_at" not in columns_of(admin, SCHEMA_A, "promo_seen"))
    verified = db.verify_personal_data_v1(admin, SCHEMA_A)
    admin.rollback()
    check("verify passes with the new row, and counts promo_seen among the identity columns it looked at",
          verified.get("promo_seen", 0) >= 1
          and verified.get("promo_seen") == sql(admin, f"SELECT count(*) FROM {q}promo_seen")[0][0], str(verified))
    admin.execute(f"ALTER TABLE {q}promo_seen ADD COLUMN seen_at TIMESTAMPTZ")
    try:
        db.verify_personal_data_v1(admin, SCHEMA_A)
        refused = ""
    except db.PersonalDataError as exc:
        refused = str(exc)
    admin.rollback()
    check("verify refuses a promo_seen that still has its seen_at column", "promo_seen.seen_at" in refused, refused)
    check("before erasing, the person is findable in promo_seen",
          any(t.startswith("promo_seen.user_id") for t in traces(admin, SCHEMA_A, PROMO_NEW)))
    admin.rollback()
    check("erase_user takes the row, and counts it",
          db.erase_user(PROMO_NEW) >= 1 and not traces(admin, SCHEMA_A, PROMO_NEW)
          and sql(admin, f"SELECT count(*) FROM {q}promo_seen WHERE user_id = %s", (P(PROMO_NEW),)) == [(0,)])
    admin.commit()
    # A database that has no promo_seen yet gets one without a time column.
    admin.execute(f"DROP TABLE {q}promo_seen")
    admin.commit()
    db.init_db()
    check("init_db makes promo_seen with a user_id and a promo and nothing else",
          columns_of(admin, SCHEMA_A, "promo_seen") == {"user_id", "promo"})


def group_f() -> None:
    section("F. the log")
    text = "\n".join(r.getMessage() for r in collector.records if r.levelno >= logging.INFO)
    check("nothing logged at INFO or above holds a real id, a name or a pseudonym",
          not re.search(r"\b(5101|5102|5103|6101|6102|6103|7101|810[1-5]|8201|8202|alice|newbie)\b", text), text[:400])


def main() -> int:
    only = [a.lower() for a in sys.argv[1:]]
    with psycopg.connect(DSN) as admin:
        admin.execute("CREATE SCHEMA IF NOT EXISTS family")
        admin.execute("CREATE TABLE IF NOT EXISTS family.settings (key TEXT PRIMARY KEY, value TEXT)")
        admin.commit()
        try:
            for letter, fn in (("a", group_a), ("b", group_b), ("c", group_c), ("d", group_d), ("e", group_e),
                               ("g", group_g), ("h", group_h)):
                if only and letter not in only:
                    continue
                # Groups B-D work on what A built, so a lone run of them builds it quietly.
                if letter in ("b", "c", "d", "h") and only and "a" not in only:
                    _quiet_setup(admin)
                fn(admin)
            if not only or "f" in only:
                group_f()
        finally:
            admin.rollback()
            for schema in (SCHEMA_A, SCHEMA_B, "anon_enc_test_old"):
                admin.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            admin.commit()
    db.close_pool()
    print(f"\n{_checks} checks, FAILURES: {len(failures)}")
    for name in failures:
        print("  -", name)
    return 1 if failures else 0


_quiet_done = False


def _quiet_setup(admin) -> None:
    global _quiet_done
    if _quiet_done:
        return
    _quiet_done = True
    make_210_schema(admin, SCHEMA_A)
    seed_plain_a(admin)
    seed_partial(admin)
    db.convert_personal_data_v1(admin, SCHEMA_A)
    admin.commit()
    db.init_db()


if __name__ == "__main__":
    raise SystemExit(main())
