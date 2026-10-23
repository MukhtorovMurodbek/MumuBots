"""`erase_user` run for real, in every bot, against a real Postgres.

`policy_scenarios.py` drives the whole `/deletemydata` conversation, but only
through AnonBot. The other three bots' `erase_user` is SQL that had never been
executed anywhere -- and SQL that has never run is a guess, with a column name
typo waiting in it that no amount of reading catches.

Each bot gets a throwaway schema of its own, built by its own `init_db()`, so
nothing here can touch a schema anything else uses. It is seeded through the
bot's own writers rather than by hand, which means the rows are shaped the way
the bot really shapes them.

The assertion is deliberately not a list of tables. It asks the database which
tables in this schema have a user column at all, and then requires that after
an erase, **none of them mentions the erased id** -- with `star_transactions`
as the one documented exception, which must keep the row and must have dropped
the username. That way a table added in a later version is covered by this
test on the day it is created, rather than on the day somebody remembers to
add it to `erase_user`.

    python tests/erase_scenarios.py            # all four bots
    python tests/erase_scenarios.py anon_bot   # just one

Needs a Postgres (TEST_DATABASE_URL, or the local default). No network.
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
BOTS = ["sticker_bot", "convert_bot", "downloader_bot", "anon_bot"]

DEFAULT_URL = "postgresql://postgres:postgres@localhost:5432/botfamily_test"

# The user being forgotten, and somebody sitting next to them who must come
# through it untouched.
TARGET, BYSTANDER = 8101, 8102

# Kept on purpose: a payment record outlives the payer asking to be forgotten,
# because a refund is issued against it. The username on it must still go.
KEPT_TABLE = "star_transactions"


def run_all() -> int:
    failed = []
    for bot in BOTS:
        print(f"\n=== {bot} ===", flush=True)
        result = subprocess.run([sys.executable, str(pathlib.Path(__file__)), bot])
        if result.returncode != 0:
            failed.append(bot)
    print()
    if failed:
        print("FAILED: " + ", ".join(failed))
        return 1
    print(f"All {len(BOTS)} bots erase cleanly.")
    return 0


def main(bot: str) -> int:
    schema = f"{bot}_erase_sim"
    os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL", DEFAULT_URL)
    os.environ["DB_SCHEMA"] = schema
    for prefix in ("SBOT", "CBOT", "DBOT", "ABOT", "MBOT"):
        os.environ.setdefault(f"{prefix}_TOKEN", "1:AA")
        os.environ.setdefault(f"{prefix}_USERNAME", "test")
    os.chdir(ROOT / bot)
    sys.path.insert(0, str(ROOT / bot))
    sys.path.insert(1, str(ROOT / "shared"))

    import db  # noqa: E402  -- after DB_SCHEMA is set, which db reads at import
    import vault  # noqa: E402

    def stored(user_id: int, table: str = "") -> int:
        """What an identity column holds for this person. AnonBot (2.1.1)
        stores vault.pseudonym(id), except that its Stars ledger keeps the
        payer under their own stand-in (2.1.2, so it can't be lined up with
        the family's ledger); the other three bots keep their ids."""
        if bot != "anon_bot":
            return user_id
        if table == KEPT_TABLE:
            return vault.pseudonym_in(user_id, "anon.payer")
        return vault.pseudonym(user_id)

    failures = []

    def check(label, ok, detail=""):
        print(("  ok   " if ok else "  FAIL ") + label + (f"\n       {detail}" if not ok and detail else ""))
        if not ok:
            failures.append(label)

    db.init_db()

    with db.pooled() as conn:
        # The username is a sealed column since 2.1.1 (username_sealed); a bot
        # that has not moved yet still has the plain one.
        name_column = "username_sealed" if conn.execute(
            "SELECT 1 FROM information_schema.columns WHERE table_schema = %s "
            "AND table_name = 'star_transactions' AND column_name = 'username_sealed'",
            (schema,)).fetchone() else "username"

    def user_columns() -> list[tuple[str, str]]:
        """(table, column) for every place in this schema that holds a user
        id. Asked of the database, so a new table is covered automatically."""
        with db.pooled() as conn:
            return conn.execute(
                "SELECT table_name, column_name FROM information_schema.columns "
                "WHERE table_schema = %s AND data_type = 'bigint' "
                "AND (column_name = 'user_id' OR column_name LIKE '%%_user_id') "
                "ORDER BY table_name, column_name",
                (schema,),
            ).fetchall()

    def rows_for(table: str, column: str, user_id: int) -> int:
        with db.pooled() as conn:
            return conn.execute(
                f'SELECT count(*) FROM "{schema}"."{table}" WHERE "{column}" = %s',
                (stored(user_id, table),),
            ).fetchone()[0]

    def wipe() -> None:
        with db.pooled() as conn:
            for table, _ in user_columns():
                conn.execute(f'DELETE FROM "{schema}"."{table}"')
            conn.commit()

    def seed(user_id: int, username: str) -> None:
        """Through the bot's own writers, so the rows are the real shape."""
        db.set_user_language(user_id, "en")
        db.bump_donation_action(user_id)
        db.record_activity_batch([user_id])
        db.record_star_invoice(user_id, username, 25, "donation",
                               f"donate:{user_id}", status="paid")
        if hasattr(db, "record_download"):                       # DownloaderBot
            db.record_download(user_id, "tiktok")
        if hasattr(db, "add_pack"):                              # StickerBot
            name = f"pack_{user_id}_by_test"
            db.add_pack(user_id, name, "A pack", owner_username=username,
                        owner_name="Someone")
            db.reset_share_token(name)
            db.add_editor(name, user_id)
        if hasattr(db, "get_or_create_anon_link"):               # AnonBot
            db.get_or_create_anon_link(user_id)
            db.claim_promo_seen(user_id, "gift-220-users")       # the 220 gift's ad, shown once

    wipe()
    seed(TARGET, "target")
    seed(BYSTANDER, "bystander")

    # The ⚡ balance lives in the shared family schema, which the table scan
    # below never looks at -- so it is checked by name. It is kept on
    # purpose, for the same reason the payment ledger is, and PRIVACY.md says
    # so; this is what stops an erase from quietly starting to take it.
    import family_link
    family_link.init_family_schema()
    family_link.set_star_balance(TARGET, 50, "adjustment", "erase_scenarios seed")
    if hasattr(db, "start_new_anon_conversation"):
        # AnonBot's conversation is the row most likely to be missed, because
        # it belongs to two people at once: one as owner, one as follower.
        # Both directions are seeded, so erasing the target has to reach it
        # whichever end of it they are on -- and has to leave the one that is
        # purely the bystander's alone.
        mine = db.start_new_anon_conversation(BYSTANDER, TARGET)
        db.record_anon_relays(BYSTANDER, [4242], mine["id"])
        db.start_new_anon_conversation(TARGET, BYSTANDER)
        db.block_follower(TARGET, BYSTANDER) if hasattr(db, "block_follower") else None

    columns = user_columns()
    check(f"{bot}: the schema has user-bearing tables to check ({len(columns)})", bool(columns))

    seeded = [(t, c) for t, c in columns if rows_for(t, c, TARGET)]
    check(f"{bot}: the seed reached {len(seeded)} of them",
          len(seeded) >= 4, ", ".join(f"{t}.{c}" for t, c in columns))
    print("       seeded: " + ", ".join(f"{t}.{c}" for t, c in seeded))

    removed = db.erase_user(TARGET)
    check(f"{bot}: erase_user ran and reported rows", removed > 0, str(removed))
    check(f"{bot}: the ⚡ credit balance survives the erase, as PRIVACY.md says",
          family_link.star_balance(TARGET) == 50, str(family_link.star_balance(TARGET)))
    check(f"{bot}: and so does its ledger",
          len(family_link.star_ledger_for(TARGET, 5)) >= 1)
    with db.pooled() as conn:
        conn.execute("DELETE FROM family.star_ledger WHERE user_id = %s", (TARGET,))
        conn.execute("DELETE FROM family.star_balances WHERE user_id = %s", (TARGET,))
        conn.commit()

    for table, column in columns:
        left = rows_for(table, column, TARGET)
        if table == KEPT_TABLE:
            check(f"{bot}: {table} keeps the payment record", left > 0)
            with db.pooled() as conn:
                names = conn.execute(
                    f'SELECT count(*) FROM "{schema}"."{table}" '
                    f"WHERE user_id = %s AND {name_column} IS NOT NULL", (stored(TARGET, KEPT_TABLE),)).fetchone()[0]
            check(f"{bot}: {table} no longer carries the username", names == 0, str(names))
            continue
        check(f"{bot}: {table}.{column} has nothing left for them", left == 0, f"{left} row(s)")

    # The bystander is the whole point of the exercise -- but "untouched" has
    # to be said precisely, because some rows are not one person's to keep.
    # An AnonBot conversation is the pair of people in it: it exists only to
    # say that these two are talking, so it cannot survive for one side and
    # not the other, and PRIVACY.md says so. Everything that is the
    # bystander's alone must come through unchanged.
    joint = {"anon_conversations", "anon_follower_state", "anon_relay", "anon_blocks"}
    own = [(t, c) for t, c in columns if t not in joint]
    survived = [(t, c) for t, c in own if rows_for(t, c, BYSTANDER)]
    expected = [(t, c) for t, c in seeded if t not in joint]
    check(f"{bot}: the bystander keeps all {len(expected)} things that are theirs alone",
          sorted(survived) == sorted(expected),
          f"survived {survived}, expected {expected}")
    for table in sorted(joint & {t for t, _ in columns}):
        check(f"{bot}: {table} is jointly held, so it went with the erase",
              all(rows_for(table, column, BYSTANDER) == 0
                  for t, column in columns if t == table))
    with db.pooled() as conn:
        kept = conn.execute(
            f'SELECT {name_column} FROM "{schema}"."{KEPT_TABLE}" WHERE user_id = %s',
            (stored(BYSTANDER, KEPT_TABLE),)).fetchone()
    if kept is not None and name_column == "username_sealed":
        kept = (vault.open_text(kept[0], "stars.username"),)       # sealed since 2.1.1
    check(f"{bot}: the bystander's username is untouched",
          kept is not None and kept[0] == "bystander", "the row is missing or the name changed")

    # Erasing somebody who was never here is a no-op, not an error -- somebody
    # will send /deletemydata as their first ever message.
    try:
        again = db.erase_user(999_999)
        check(f"{bot}: erasing a stranger is a quiet no-op", again == 0, str(again))
    except Exception as exc:
        check(f"{bot}: erasing a stranger is a quiet no-op", False, repr(exc))

    # And twice in a row, which is what a double tap does.
    try:
        check(f"{bot}: erasing twice is a quiet no-op", db.erase_user(TARGET) == 0)
    except Exception as exc:
        check(f"{bot}: erasing twice is a quiet no-op", False, repr(exc))

    with db.pooled() as conn:
        conn.execute(f'DROP SCHEMA "{schema}" CASCADE')
        conn.commit()
    print(f"       (dropped {schema})")

    print(f"\n  FAILURES: {len(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    if len(sys.argv) > 1:
        raise SystemExit(main(sys.argv[1]))
    raise SystemExit(run_all())
