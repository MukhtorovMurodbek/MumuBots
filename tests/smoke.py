"""Exercises every bot's db.py + family_link against a real Postgres.

Run from the repo root:  python tests/smoke.py
Each bot is imported in its own subprocess, because all five have modules
named db.py / i18n.py and they would collide in one interpreter.
"""
import os
import subprocess
import sys
from pathlib import Path

# The bots' own output is full of emoji (a /status starts with a chart, an
# error summary with a tick), and a Windows console still defaults to cp1252
# -- printing a child's reply there raised UnicodeEncodeError and took the
# whole run down before it reached the assertions. The child processes are
# already told to speak UTF-8; this is the same instruction for the runner's
# own stdout.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
DSN = os.environ.get("TEST_DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/botfamily_test")

SCHEMAS = {
    "sticker_bot": "sticker_bot",
    "convert_bot": "convert_bot",
    "downloader_bot": "downloader_bot",
    "anon_bot": "anon_bot",
    "manager_bot": "manager_bot",
}

CHILD = r'''
import sys, os
from datetime import datetime, timedelta, timezone
sys.path.insert(0, os.getcwd())
sys.path.insert(1, os.path.join(os.path.dirname(os.getcwd()), "shared"))
import db

db.init_db()
print("  init_db ok")

now = datetime.now(timezone.utc)
db.record_activity_batch([111, 222, 111])
print("  activity batch ok, hour count =", db.count_active_users_since(now - timedelta(hours=1)))
print("  prune removed", db.prune_old_data(), "row(s)")

BOT = os.environ["SMOKE_BOT"]

if BOT != "manager_bot":
    n, last, shown, donated = db.bump_donation_action(555)
    assert n == 1 and last is None and shown == 0 and donated is False, (n, last, shown, donated)
    n, last, shown, _ = db.bump_donation_action(555)
    assert n == 2 and shown == 0, (n, shown)
    db.reset_donation_prompt(555)
    n, last, shown, _ = db.bump_donation_action(555)
    # Counter back to zero, the showing stamped, and times_shown moved on --
    # which is what turns the cadence from a loop into a schedule that runs
    # out (DONATION_STEPS in shared_features.py).
    assert n == 1 and last is not None and shown == 1, (n, last, shown)
    db.reset_donation_prompt(555)
    assert db.bump_donation_action(555)[2] == 2

    # A paid row is what stops the nudge for good. Read in the same
    # statement as the counter, so it costs nothing on the hot path.
    db.record_star_invoice(556, "giver", 50, "donation", "donate:smoke1")
    assert db.bump_donation_action(556)[3] is False
    db.update_star_transaction("donate:smoke1", "paid")
    assert db.bump_donation_action(556)[3] is True
    # Taken back out again: ConvertBot's ledger assertions below count every
    # paid row in this schema, and a donation left here would be counted as
    # one of its conversions.
    with db.pooled() as _conn:
        _conn.execute("DELETE FROM star_transactions WHERE payload = 'donate:smoke1'")
        _conn.commit()

    # The pin bookkeeping, whether or not DONATION_PIN is ever turned on.
    assert db.get_pinned_donation_message(557) is None
    db.set_pinned_donation_message(557, 4242)
    assert db.get_pinned_donation_message(557) == 4242
    db.set_pinned_donation_message(557, None)
    assert db.get_pinned_donation_message(557) is None
    print("  donation counter ok")

if BOT == "sticker_bot":
    db.set_user_language(9, "uz"); assert db.get_user_language(9) == "uz"
    db.add_pack(9, "p_test_by_x", "Test pack", "someone", "Some One")
    assert db.get_pack_owner("p_test_by_x") == 9
    tok, title, editors = db.get_coedit_view("p_test_by_x")
    assert title == "Test pack" and editors == 0, (title, editors)
    db.add_editor("p_test_by_x", 10)
    tok2, _, editors2 = db.get_coedit_view("p_test_by_x")
    assert tok2 == tok and editors2 == 1, (tok, tok2, editors2)
    assert db.get_pack_by_token(tok) == "p_test_by_x"
    new_tok = db.reset_share_token("p_test_by_x")
    assert new_tok != tok
    db.set_pack_title("p_test_by_x", "Renamed")
    assert db.get_pack_title("p_test_by_x") == "Renamed"
    assert db.get_pack_creator_info("p_test_by_x")["owner_username"] == "someone"
    assert db.get_user_packs(9) == [("p_test_by_x", "Renamed")]
    db.delete_pack_records("p_test_by_x")
    assert db.get_pack_owner("p_test_by_x") is None
    print("  packs / co-edit ok")

if BOT == "convert_bot":
    db.set_user_language(9, "ru"); assert db.get_user_language(9) == "ru"
    db.record_star_invoice(9, "u", 30, "png->jpg", "convert:abc")
    db.update_star_transaction("convert:abc", "paid", "chg1")
    tx = db.get_star_transaction("convert:abc")
    assert tx["status"] == "paid" and tx["charge_id"] == "chg1", tx
    db.record_star_invoice(9, "u", 0, "free one", "free:xyz", status="free")
    db.record_star_invoice(9, "u", 500, "donation", "donate:d1", status="invoiced", currency="USD")
    total, history = db.get_user_star_summary(9)
    assert total == 30, total
    assert len(history) == 3, history
    summary, recent = db.get_star_ledger(10)
    assert summary["paid_total"] == 30 and summary["paid_count"] == 1, summary
    assert summary["free_count"] == 1 and summary["paying_users"] == 1, summary
    assert len(recent) == 3
    print("  stars ledger ok")

if BOT == "downloader_bot":
    db.set_user_language(9, "en"); assert db.get_user_language(9) == "en"
    assert db.get_caption_enabled(9) is True
    db.set_caption_enabled(9, False)
    assert db.get_caption_enabled(9) is False
    print("  caption prefs ok")

    # The per-person share of the day. Driven at the ceiling rather than at
    # the default, because what matters is what happens on the download that
    # crosses it -- and the answer has to carry a wait, or the user is told
    # "no" with nowhere to go.
    db.DOWNLOADS_PER_HOUR, db.DOWNLOADS_PER_DAY = 3, 5
    quota_user = 5150
    state = db.download_allowance(quota_user)
    assert state["allowed"] and state["hour"] == 0 and not state["donor"], state
    for _ in range(3):
        db.record_download(quota_user, "tiktok")
    state = db.download_allowance(quota_user)
    assert not state["allowed"] and state["scope"] == "hour", state
    assert 1 <= state["wait_min"] <= 61, state
    # A donation moves the ceiling rather than removing it, and the same
    # three downloads are suddenly inside it.
    db.record_star_invoice(quota_user, "someone", 50, "donate", "donate:x1")
    db.update_star_transaction("donate:x1", "paid")
    state = db.download_allowance(quota_user)
    assert state["allowed"] and state["donor"] and state["hour_max"] > 3, state
    # The day ceiling wins over the hour ceiling, because "come back in forty
    # minutes" would be a lie when the real answer is tomorrow.
    db.DOWNLOADS_PER_HOUR, db.DOWNLOADS_PER_DAY, db.DONOR_MULTIPLIER = 100, 2, 1
    other = 5151
    for _ in range(2):
        db.record_download(other, "reddit")
    state = db.download_allowance(other)
    assert not state["allowed"] and state["scope"] == "day", state
    db.DOWNLOADS_PER_HOUR, db.DOWNLOADS_PER_DAY, db.DONOR_MULTIPLIER = 12, 40, 4
    print("  download quota ok")

if BOT == "anon_bot":
    tok, paused = db.get_or_create_anon_link(1)
    assert paused is False
    tok_again, _ = db.get_or_create_anon_link(1)
    assert tok == tok_again
    owner, is_paused, blocked = db.get_link_view(tok, 2)
    assert owner == 1 and not is_paused and not blocked, (owner, is_paused, blocked)
    db.set_anon_blocked(1, 2, True)
    assert db.get_link_view(tok, 2)[2] is True
    db.set_anon_blocked(1, 2, False)
    db.set_anon_link_paused(1, True)
    assert db.get_link_view(tok, 2)[1] is True
    db.set_anon_link_paused(1, False)

    conv = db.start_new_anon_conversation(1, 2)
    assert conv["conv_number"] == 1, conv
    conv2 = db.start_new_anon_conversation(1, 2)
    assert conv2["conv_number"] == 2, conv2

    db.set_user_language(1, "uz")
    blocked, exists, lang = db.get_delivery_context(1, 2)
    assert blocked is False and exists is True and lang == "uz", (blocked, exists, lang)

    db.record_anon_relays(1, [500, 501], conv2["id"])
    assert db.get_conversation_for_relay(1, 500) == conv2["id"]
    assert db.get_conversation_for_relay(1, 501) == conv2["id"]
    db.record_anon_relays(1, [], conv2["id"])  # must be a no-op, not an error
    db.record_anon_relays(1, [503], conv2["id"])
    assert db.get_conversation_for_relay(1, 503) == conv2["id"]
    db.forget_anon_relay(1, 503)
    assert db.get_conversation_for_relay(1, 503) is None
    # Both chats in one statement: the owner's copies and the guest's own
    # message. Without a row of their own the guest could neither be told an
    # edit had not travelled nor quote themselves.
    db.record_relay_and_touch(conv2["id"], 1, [502], 2, [601], 7, 8)
    assert db.get_conversation_for_relay(1, 502) == conv2["id"]
    assert db.get_conversation_for_relay(2, 601) == conv2["id"]
    # ...and each row says which message in the OTHER chat it is the same
    # message as, taken from the anchor for that side. It is what makes a
    # reply to an older message arrive on the right bubble; a row written
    # without one (record_anon_relays, above -- the bookmarks and the line
    # announcing a conversation) has no counterpart to name.
    assert db.get_relay_row(1, 502) == {"conversation_id": conv2["id"], "peer_message_id": 8}
    assert db.get_relay_row(2, 601) == {"conversation_id": conv2["id"], "peer_message_id": 7}
    assert db.get_relay_row(1, 500)["peer_message_id"] is None
    assert db.get_relay_row(1, 504) is None
    # Empty on both sides is still a valid touch of the anchors.
    db.record_relay_and_touch(conv2["id"], 1, [], 2, [], 7, 8)
    got = db.get_conversation(conv2["id"])
    assert got["last_owner_msg_id"] == 7 and got["last_follower_msg_id"] == 8, got
    active = db.get_active_conversation_for_follower(2)
    assert active["id"] == conv2["id"], active
    # Only conv2 has had anything said in it (record_relay_and_touch above);
    # conv1 was a link tap and nothing more, and does not count as a
    # conversation anybody had.
    stats = db.get_anon_stats(1)
    assert stats == {"distinct_followers": 1, "conversations": 1}, stats
    assert db.count_owner_conversations(1) == 1, db.count_owner_conversations(1)

    # Numbers are per OWNER now, not per (owner, follower) pair: a second
    # guest must not arrive as "Conversation #1" alongside the first.
    third = db.start_new_anon_conversation(1, 99)
    assert third["conv_number"] == 3, third

    db.set_anon_blocked(1, 3, True)
    # follower 3 has no conversation with owner 1, so the list still names
    # them but with no conversation to address them by -- /blocked's other
    # branch, and the row that gets no button.
    assert db.list_anon_blocks_with_conversations(1) == [(None, [])],         db.list_anon_blocks_with_conversations(1)
    db.set_anon_blocked(1, 2, True)
    # Keyed by a conversation id, never by the guest's Telegram user id --
    # callback_data travels to the client, and that id must not.
    rows = db.list_anon_blocks_with_conversations(1)
    by_conv = {cid: nums for cid, nums in rows if cid is not None}
    assert list(by_conv.values()) == [[1, 2]], rows
    assert conv["id"] in by_conv, (conv, rows)
    db.set_anon_blocked(1, 2, False)
    new_tok = db.regenerate_anon_link(1)
    assert new_tok != tok
    print("  anon links / conversations / relays ok")

    # Runs last on purpose: it starts a third conversation, and the counts
    # asserted above are written for exactly two.
    # /cancel on an open ask session drops the routing state; the conversation
    # itself stays, being a record of messages that really were sent.
    assert db.get_active_conversation_for_follower(2)["id"] == conv2["id"]
    assert db.clear_follower_state(2) is True
    assert db.get_active_conversation_for_follower(2) is None
    assert db.clear_follower_state(2) is False, "clearing twice must be a no-op"
    assert db.get_conversation(conv2["id"]) is not None, "conversation must survive"
    conv3 = db.start_new_anon_conversation(1, 2)   # tapping the link again
    # 4, not 3: numbering runs per owner now, and the conversation opened
    # above for a different guest (99) took 3. Under the old per-pair
    # numbering that guest would also have been "#1", which is precisely the
    # collision this changed to stop.
    assert conv3["conv_number"] == 4, conv3
    assert db.get_active_conversation_for_follower(2)["id"] == conv3["id"]
    print("  follower-state clear (/cancel) ok")

    # 2.1.0: where a reaction on a bubble goes -- its twin, both sides and
    # whether the guest is blocked, in one query.
    route = db.get_reaction_route(1, 502)
    assert route == {"peer_message_id": 8, "conversation_id": conv2["id"], "owner_user_id": 1,
                     "follower_user_id": 2, "blocked": False}, route
    db.set_anon_blocked(1, 2, True)
    assert db.get_reaction_route(2, 601)["blocked"] is True
    db.set_anon_blocked(1, 2, False)
    assert db.get_reaction_route(1, 500) is None, "a row with no twin has nowhere to send it"
    assert db.get_reaction_route(1, 504) is None
    print("  reaction route ok")

    # 2.1.0: the one-time note that reactions travel. Who is owed it is
    # decided by the order of two ALTERs, so the migration is run here the way
    # it runs on the live database: the column gone (a table from before it),
    # two people already in it, then init_db again.
    with db.pooled() as _conn:
        _conn.execute("ALTER TABLE user_settings DROP COLUMN IF EXISTS knows_reactions")
        # user_id holds vault.pseudonym(id) since 2.1.1; a row from before the
        # note has no user_sealed either, which claim_reactions_note never reads.
        import vault
        _p = vault.pseudonym
        _conn.execute("DELETE FROM user_settings WHERE user_id IN (%s, %s, %s)", (_p(801), _p(802), _p(803)))
        _conn.execute("INSERT INTO user_settings (user_id, language) VALUES (%s, 'en'), (%s, 'ru')",
                      (_p(801), _p(802)))
        _conn.commit()
    db.init_db()
    assert db.claim_reactions_note(801) is True, "somebody already here is owed the note"
    assert db.claim_reactions_note(801) is False, "once"
    db.set_user_language(803, "uz")
    assert db.claim_reactions_note(803) is False, "somebody new is not"
    db.set_user_language(802, "en")
    assert db.claim_reactions_note(802) is True, "changing language does not use it up"
    assert db.claim_reactions_note(999999) is False, "nobody, no note"
    db.init_db()
    assert db.claim_reactions_note(801) is False and db.claim_reactions_note(803) is False, \
        "a second run of the migration owes nobody anything again"
    print("  reactions note: owed to those already here, once; never to anyone new")

if BOT == "manager_bot":
    import family_link
    family_link.init_family_schema()
    schemas = ["sticker_bot", "convert_bot", "downloader_bot", "anon_bot", "manager_bot"]
    snap = db.status_snapshot(schemas, now - timedelta(hours=1))
    assert "beats" in snap and "database" in snap, snap
    assert snap["database"]["name"] == "botfamily_test", snap["database"]
    print("  status_snapshot ok:", snap["database"], "| active:", snap["active"])
    by = db.active_users_by_schema(schemas, now - timedelta(hours=1), True)
    print("  active_users_by_schema:", by)
    don = db.donations_by_schema(schemas)
    print("  donations_by_schema:", don)
    assert don.get("convert_bot"), don
    cid = db.queue_command("stickerbot", "ping", "", 1, 1)
    assert isinstance(cid, int)
    db.set_setting("alerts", "off")
    assert db.get_setting("alerts", "on") == "off"
    db.set_setting("alerts", "on")
    db.set_known_state("stickerbot", True)
    assert db.get_known_state()["stickerbot"] is True
    db.log_event("managerbot", "warning", "smoke", "hello")
    assert db.recent_events(5)[0]["message"] == "hello"
    cols, rows = db.run_readonly_query("SELECT 1 AS one")
    assert rows == [(1,)], rows
    # /sql's ten seconds must NOT have leaked onto the pooled connection, which
    # keeps the pool's own minute (every pooled connection is started with
    # -c statement_timeout=60s since 2.1.0; it was unlimited, "0", before)
    with db.pooled() as conn:
        left = conn.execute("SHOW statement_timeout").fetchone()[0]
    assert left == "1min", f"statement_timeout leaked onto the pool: {left}"
    print("  manager queries ok, statement_timeout not leaked")
    # 2.1.0: prepare=True sends the query over the extended protocol, where
    # Postgres refuses more than one statement -- so a second one can no
    # longer end the read-only transaction and run outside it. Until this ran,
    # that was checked against psycopg's source only.
    for attempt in ("SELECT 1; SELECT 2",
                    "SELECT 1; COMMIT; CREATE TABLE smoke_escape (a int)",
                    "CREATE TABLE smoke_escape (a int)"):
        try:
            db.run_readonly_query(attempt)
        except Exception:
            pass
        else:
            raise AssertionError(f"/sql ran this: {attempt}")
    with db.pooled() as conn:
        escaped = conn.execute("SELECT to_regclass('smoke_escape')").fetchone()[0]
    assert escaped is None, "a write got out of /sql"
    assert db.run_readonly_query("SELECT 2 AS two")[1] == [(2,)], "/sql still works after a refusal"
    print("  /sql: one statement, read-only, nothing got out")
    # 2.1.0: family.feedback -- kept, taken once, read back, pruned.
    family_link.init_family_schema()
    with db.pooled() as conn:
        conn.execute("DELETE FROM family.feedback")
        conn.commit()
    family_link._bot_id = "convertbot"
    fid = family_link.save_feedback("uz", "smoke feedback")
    assert family_link.feedback_count_since(1) == 1
    taken = family_link.take_undelivered_feedback(10)
    assert [(r["id"], r["bot_id"], r["lang"], r["body"]) for r in taken] == \
        [(fid, "convertbot", "uz", "smoke feedback")], taken
    assert family_link.take_undelivered_feedback(10) == [], "taken once"
    assert family_link.recent_feedback(5)[0]["delivered_at"] is not None
    with db.pooled() as conn:
        conn.execute("UPDATE family.feedback SET created_at = now() - interval '31 days'")
        conn.commit()
    assert family_link.prune_feedback() == 1 and family_link.recent_feedback(5) == []
    print("  feedback: kept, taken once, gone after 30 days")
    expired = db.expire_stale_commands(0)
    assert any(r["command"] == "ping" for r in expired), expired
    finished = db.take_finished_commands(20)
    assert any(r["status"] == "timeout" and r["command"] == "ping" for r in finished),         [(r["command"], r["status"]) for r in finished]
    print("  command queue ok")

# family_link's own tables + housekeeping, in every bot
import family_link
family_link.init_family_schema()
family_link._bot_id = BOT
family_link._display_name = BOT
family_link._start_time = now
family_link._enabled = True
family_link.write_heartbeat()
family_link.report_event("info", "smoke", "hello from " + BOT)
print("  heartbeat + event ok, housekeeping pruned:", family_link._prune())

data = db.dump_database_csv_zip()
assert data[:2] == b"PK", data[:8]
print(f"  dbdump ok ({len(data)} bytes)")

# pooled_read hands out the same pooled connection in autocommit, which is
# what removes the BEGIN and the COMMIT from a single SELECT. It has to give
# it back exactly as it found it: a connection left in autocommit would make
# the next pooled() caller's multi-statement write four separate ones, with
# no transaction around them and no way to notice until half of one landed.
with db.pooled_read() as _c:
    assert _c.autocommit is True, "pooled_read must be autocommit"
    assert _c.execute("SELECT 1").fetchone()[0] == 1
with db.pooled() as _c:
    assert _c.autocommit is False, "pooled() got a connection still in autocommit"
    _c.execute("SELECT 1")
    _c.commit()
# A read that raises must not strand the connection in autocommit either.
try:
    with db.pooled_read() as _c:
        raise RuntimeError("boom")
except RuntimeError:
    pass
with db.pooled() as _c:
    assert _c.autocommit is False, "a failed read left the connection in autocommit"
print("  pooled_read round-trips and restores the connection")

db.close_pool()
print("  pool closed")
'''


def reset() -> None:
    """The tests assert on exact counts, so they need a clean database.

    Creates TEST_DATABASE_URL's database if it isn't there yet, then drops
    every schema in it -- this is a scratch database, never the real one."""
    import psycopg
    from urllib.parse import urlsplit
    name = urlsplit(DSN).path.lstrip("/")
    admin = DSN.rsplit("/", 1)[0] + "/postgres"
    try:
        with psycopg.connect(admin, autocommit=True) as conn:
            try:
                conn.execute(f'CREATE DATABASE "{name}"')
                print(f"(created {name})")
            except psycopg.errors.DuplicateDatabase:
                pass
    except Exception as exc:
        print(f"(could not check for {name}: {exc})")
    with psycopg.connect(DSN, autocommit=True) as conn:
        for schema in list(SCHEMAS.values()) + ["family"]:
            conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
    print("(schemas dropped)\n")


def main() -> int:
    reset()
    failures = 0
    for bot, schema in SCHEMAS.items():
        print(f"== {bot} ({schema}) ==")
        env = dict(os.environ)
        env.update({
            "DATABASE_URL": DSN,
            "DB_SCHEMA": schema,
            "SMOKE_BOT": bot,
            "FAMILY_BUS": "on",
            "PYTHONIOENCODING": "utf-8",
        })
        proc = subprocess.run(
            [sys.executable, "-c", CHILD], cwd=ROOT / bot, env=env,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        print(proc.stdout.rstrip())
        if proc.returncode != 0:
            failures += 1
            print(proc.stderr.rstrip())
    print("\nFAILURES:", failures)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
