"""Scenarios for what changed in 2.2.0."""
import asyncio
import base64
import json
import os
from datetime import datetime, timedelta, timezone

import psycopg
import pytest

from conftest import connect, load

STICKER = "sticker_bot"
ANON = "anon_bot"
MANAGER = "manager_bot"


@pytest.fixture(scope="module")
def family():
    sticker_db = load(STICKER, "db")
    sticker_db.init_db()
    load(ANON, "db").init_db()
    load(MANAGER, "db").init_db()
    link = load(STICKER, "family_link")
    link.init_family_schema()
    assert link.check_data_key(fresh=True)["usable"]
    return link


# ── unique people ────────────────────────────────────────────────────────────

BOTS = [(STICKER, STICKER), (ANON, ANON), (MANAGER, MANAGER)]
EVERYONE_SCHEMAS = [STICKER, ANON]


def _activity(schema: str, users, when: datetime) -> None:
    with connect() as conn:
        conn.execute(
            f'INSERT INTO "{schema}".activity_events (user_id, occurred_at) '
            f"SELECT unnest(%s::bigint[]), %s", (list(users), when))


@pytest.fixture(scope="module")
def activity(family):
    load(MANAGER, "family_link").check_data_key(fresh=True)
    now = datetime.now(timezone.utc)
    _activity(STICKER, range(1, 301), now - timedelta(days=2))
    _activity(STICKER, range(1, 11), now - timedelta(days=1))
    _activity(STICKER, [1, 1, 1], now - timedelta(days=1))
    load(ANON, "db").record_activity_batch([5, 400])
    _activity(MANAGER, [777000, 9], now)
    return now


def test_people_live_counts_each_person_once(activity):
    manager_db = load(MANAGER, "db")
    today = activity.date()
    two_days_ago = today - timedelta(days=2)
    assert manager_db.people_live(BOTS, EVERYONE_SCHEMAS, two_days_ago, two_days_ago)[STICKER] == 300

    counts = manager_db.people_live(BOTS, EVERYONE_SCHEMAS, two_days_ago, today)
    assert counts[STICKER] == 300
    assert counts[ANON] == 2
    assert counts[MANAGER] == 2
    # 5 used both bots, 400 only AnonBot; the anon pseudonyms open to the same people.
    assert counts[manager_db.EVERYONE] == 301

    counts = manager_db.people_live(BOTS, EVERYONE_SCHEMAS, today, today)
    assert counts.get(STICKER, 0) == 0 and counts[ANON] == 2


def test_people_counts_keep_numbers_and_nothing_about_who(activity):
    manager_db = load(MANAGER, "db")
    today = activity.date()
    two_days_ago = today - timedelta(days=2)
    with connect() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS family.people_by_day (day DATE, marks BYTEA)")

    assert manager_db.record_people_counts(BOTS, EVERYONE_SCHEMAS, now=activity) > 0
    days = manager_db.people_stored(manager_db.DAY, two_days_ago, today)
    assert days[two_days_ago][STICKER] == 300
    assert today not in days                       # not over yet
    assert manager_db.record_people_counts(BOTS, EVERYONE_SCHEMAS, now=activity) == 0

    # Once the month is over, its count is written too.
    month = two_days_ago.replace(day=1)
    after = datetime.combine(manager_db._next_month(month), datetime.min.time(),
                             tzinfo=timezone.utc) + timedelta(hours=2)
    manager_db.record_people_counts(BOTS, EVERYONE_SCHEMAS, now=after)
    assert manager_db.people_stored(manager_db.MONTH, month, month)[month][STICKER] == 300

    with connect() as conn:
        columns = {name: kind for name, kind in conn.execute(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_schema = 'family' AND table_name = 'people_counts'").fetchall()}
        old_table = conn.execute("SELECT to_regclass('family.people_by_day')").fetchone()[0]
    assert columns == {"period": "text", "start": "date", "bot": "text", "people": "integer"}
    assert old_table is None


def test_people_range_arguments():
    bot = load(MANAGER, "bot")
    today = datetime(2026, 10, 24).date()
    assert bot._people_range([], today) is None
    assert bot._people_range(["7"], today) == (datetime(2026, 10, 18).date(), today, False)
    assert bot._people_range(["2026-02"], today) == (
        datetime(2026, 2, 1).date(), datetime(2026, 2, 28).date(), True)
    assert bot._people_range(["2026-09-15", "2026-09-01"], today) == (
        datetime(2026, 9, 1).date(), datetime(2026, 9, 15).date(), False)
    assert bot._people_range(["2026-10-01"], today) == (datetime(2026, 10, 1).date(), today, False)
    with pytest.raises(ValueError):
        bot._people_range(["soon"], today)


# ── payments ─────────────────────────────────────────────────────────────────

def test_refunded_payment_stays_refunded(family):
    db = load(STICKER, "db")
    db.record_star_invoice(11, "someone", 50, "donation", "pay-refund")
    db.update_star_transaction("pay-refund", "refunded", "charge-r")
    db.update_star_transaction("pay-refund", "paid", "charge-r")
    with connect() as conn:
        status, sealed = conn.execute(
            "SELECT status, username_sealed FROM sticker_bot.star_transactions WHERE payload = %s",
            ("pay-refund",)).fetchone()
    assert status == "refunded"
    assert db.open_name(sealed, db.NAME_PURPOSE) == "someone"


def test_topup_whose_answer_was_lost_is_not_credited_twice(family, monkeypatch):
    real = family._connect
    commits = {"n": 0}

    class LosesFirstAnswer:
        def __enter__(self):
            self._cm = real()
            self._conn = self._cm.__enter__()
            return self

        def __exit__(self, *exc):
            return self._cm.__exit__(*exc)

        def __getattr__(self, name):
            return getattr(self._conn, name)

        def commit(self):
            self._conn.commit()
            commits["n"] += 1
            if commits["n"] == 1:
                raise psycopg.OperationalError("server closed the connection unexpectedly")

    monkeypatch.setattr(family, "_connect", LosesFirstAnswer)
    monkeypatch.setattr(family, "_TOPUP_BACKOFF_SECONDS", (0, 0))
    result = family.topup(4242, 100, "test", charge_id="charge-lost")
    monkeypatch.setattr(family, "_connect", real)

    assert result["recovered"] is True and result["credited"] > 0
    assert family.star_balance(4242) == result["credited"] + result["bonus"]
    assert family.topup(4242, 100, "test", charge_id="charge-lost") is None
    assert family.star_balance(4242) == result["credited"] + result["bonus"]
    assert json.loads(family.charge_claim("charge-lost"))["credited"] == result["credited"]


def test_alert_made_while_the_database_is_down_is_sent_later(family, monkeypatch):
    real = family._connect

    def down():
        raise psycopg.OperationalError("could not connect to server")

    monkeypatch.setattr(family, "_enabled", True)
    monkeypatch.setattr(family, "_bot_id", STICKER)
    monkeypatch.setattr(family, "_connect", down)
    family.report_event_soon("critical", "payment", "fix this charge by hand", keep=True)
    family.report_event_soon("info", "noise", "may be lost", keep=False)
    assert len(family._outbox) == 1

    monkeypatch.setattr(family, "_connect", real)
    assert family.flush_outbox() == 1 and not family._outbox
    with connect() as conn:
        message, = conn.execute("SELECT message FROM family.events WHERE kind = 'payment'").fetchone()
    assert message.startswith("fix this charge by hand (held")


def test_free_conversion_never_reads_the_wallet(monkeypatch):
    runner = load("convert_bot", "convert_runner")
    link = load("convert_bot", "family_link")

    def wallet(*args, **kwargs):
        raise AssertionError("the wallet was touched")

    monkeypatch.setattr(link, "spend_stars", wallet)
    job = type("Job", (), {"price": 0, "user_id": 1, "size": 10})()
    assert asyncio.run(runner.ConvertRunner.charge(None, job)) == 0


# ── update waitlist ──────────────────────────────────────────────────────────

def test_waitlist_hands_back_recent_people_and_forgets_on_request(family):
    lifecycle = load(STICKER, "lifecycle")
    vault = load(STICKER, "vault")
    lifecycle.init_waitlist_table()
    lifecycle.hold_for_update(101, 101, STICKER)
    lifecycle.hold_for_update(102, 102, STICKER)
    with connect() as conn:
        conn.execute(
            "INSERT INTO family.update_waitlist (bot_id, user_id, chat_id, held_at, who_sealed) "
            "VALUES (%s, %s, %s, now() - interval '30 days', %s)",
            (STICKER, vault.pseudonym(103), vault.pseudonym(103),
             vault.seal_json({"user": 103, "chat": 103}, lifecycle.WAITLIST_PURPOSE)))
    lifecycle.forget_user(102)
    assert lifecycle.take_held(STICKER) == [(101, 101)]
    assert lifecycle.prune_waitlist() == 1


def test_process_groups(monkeypatch):
    import main
    everything = main.discover()[0]
    monkeypatch.delenv("MUMU_CHILD", raising=False)

    def groups(setting, bots=everything, flags=()):
        monkeypatch.setenv("MUMU_PROCESSES", setting)
        found = main.process_groups(bots, everything, set(flags))
        return None if found is None else [[bot.folder.removesuffix("_bot") for bot in group] for group in found]

    assert groups("manager;convert") == [["manager"], ["convert"], ["sticker", "downloader", "anon"]]
    assert groups("manager;convert;sticker,anon") == [["manager"], ["convert"], ["sticker", "anon"], ["downloader"]]
    assert groups("one") is None and groups("") is None
    assert groups("separate") == [[bot.folder.removesuffix("_bot")] for bot in everything]
    assert groups("", flags=["--separate"]) == groups("separate")
    chosen = main.select(everything, ["convert", "sticker"])
    assert groups("manager;convert", bots=chosen) == [["convert"], ["sticker"]]
    for wrong in ("manager;manager,convert", "manager;nobody"):
        with pytest.raises(SystemExit):
            groups(wrong)

    command, env = main.child_command(main.select(everything, ["sticker", "anon"]))
    assert command[-2:] == ["sticker", "anon"] and env["MUMU_CHILD"] == "1" and "MUMU_PROCESSES" not in env


def test_startup_waits_for_the_database_instead_of_ending(monkeypatch):
    import main
    clock = {"now": 0.0}
    pauses = []

    class GaveUp(Exception):
        pass

    def sleep(seconds):
        pauses.append(seconds)
        clock["now"] += seconds
        if len(pauses) == 40:
            raise GaveUp

    def refused(*args, **kwargs):
        raise psycopg.OperationalError("connection to server at 10.0.0.1 failed: Connection refused")

    monkeypatch.setattr(psycopg, "connect", refused)
    with pytest.raises(GaveUp):
        main._database_problem("postgresql://u:p@db.internal:5432/x", sleep=sleep, clock=lambda: clock["now"])
    assert max(pauses) == main.DB_RETRY_MAX_SECONDS and clock["now"] > 25 * 60

    def wrong_password(*args, **kwargs):
        raise psycopg.OperationalError('FATAL:  password authentication failed for user "u"')

    monkeypatch.setattr(psycopg, "connect", wrong_password)
    assert "password" in main._database_problem("postgresql://u:p@db.internal:5432/x", sleep=sleep)


# ── data key and the names stage ─────────────────────────────────────────────

def test_key_problem_only_for_the_stages_a_bot_needs(family):
    good = {"usable": True, "sealed": frozenset({"anon"}), "problem": None}
    assert family.key_problem(good, ()) is None
    assert family.key_problem(good, ("anon",)) is None
    assert family.key_problem(good, ("names",)) is not None
    broken = {"usable": False, "sealed": frozenset(), "problem": "no key"}
    assert family.key_problem(broken, ()) == "no key"


def test_names_stage_seals_and_drops_plain_columns(family):
    vault = load(STICKER, "vault")
    with connect() as conn:
        conn.execute('CREATE SCHEMA "names_case"')
        conn.execute("CREATE TABLE names_case.star_transactions (id SERIAL PRIMARY KEY, username TEXT)")
        conn.execute("CREATE TABLE names_case.packs (id SERIAL PRIMARY KEY, owner_username TEXT, owner_name TEXT)")
        conn.execute("INSERT INTO names_case.star_transactions (username) VALUES ('alice'), (NULL)")
        conn.execute("INSERT INTO names_case.packs (owner_username, owner_name) VALUES ('bob', 'Bob B')")
        conn.commit()
        counts = family.convert_names_v1(conn, "names_case")
        conn.commit()
        assert counts == {"star_transactions.username_sealed": 1, "packs.owner_sealed": 1}
        assert family.verify_names_v1(conn, "names_case") == counts
        sealed = conn.execute("SELECT username_sealed FROM names_case.star_transactions "
                              "WHERE username_sealed IS NOT NULL").fetchone()[0]
        owner = conn.execute("SELECT owner_sealed FROM names_case.packs").fetchone()[0]
        conn.execute("ALTER TABLE names_case.packs ADD COLUMN owner_name TEXT")
        with pytest.raises(RuntimeError, match="still there"):
            family.verify_names_v1(conn, "names_case")
        conn.rollback()
        conn.execute('DROP SCHEMA "names_case" CASCADE')
        conn.commit()
    assert vault.open_text(sealed, family.NAME_PURPOSE) == "alice"
    assert vault.open_json(owner, family.OWNER_PURPOSE) == {"username": "bob", "name": "Bob B"}


# ── downloader ───────────────────────────────────────────────────────────────

def _token(payload: dict) -> str:
    part = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return f"eyJhbGciOiJIUzI1NiJ9.{part}.c2ln"


def test_instagram_post_photos_come_once(monkeypatch):
    resolvers = load("downloader_bot", "resolvers")
    tokens = []
    for n in (1, 2):
        for force in (False, True):
            tokens.append(_token({"url": f"https://scontent.cdninstagram.com/p{n}.jpg?stp=x{force}",
                                  "filename": f"photo{n}.jpg", "force": force}))
    body = " ".join(f'"https://cdn.downloadgram.org/?token={t}"' for t in tokens)

    class Response:
        status_code = 200
        text = body

        def raise_for_status(self):
            pass

    async def fake_get(*args, **kwargs):
        return Response()

    monkeypatch.setattr(resolvers, "_get", fake_get)
    found = asyncio.run(resolvers._ig_downloadgram("https://www.instagram.com/p/De2cXNogTft/"))
    assert [item.filename for item in found.items] == ["photo1.jpg", "photo2.jpg"]
    assert all(item.url.endswith(tokens[i]) for item, i in zip(found.items, (1, 3)))


def test_format_picker_fits_the_budget_and_prefers_h264():
    video = load("downloader_bot", "video")
    mb = 1024 * 1024
    info = {"duration": 3600, "formats": [
        {"format_id": "137", "vcodec": "avc1.640028", "acodec": "none", "height": 1080, "filesize": 400 * mb},
        {"format_id": "248", "vcodec": "vp9", "acodec": "none", "height": 1080, "filesize": 300 * mb},
        {"format_id": "136", "vcodec": "avc1.4d401f", "acodec": "none", "height": 720, "filesize": 150 * mb},
        {"format_id": "140", "vcodec": "none", "acodec": "mp4a.40.2", "abr": 129, "filesize": 20 * mb},
        {"format_id": "251", "vcodec": "none", "acodec": "opus", "abr": 160, "filesize": 18 * mb},
        {"format_id": "sb0", "vcodec": "none", "acodec": "none", "ext": "mhtml", "format_note": "storyboard"},
    ]}
    assert video.pick_format(info, 1000 * mb) == "137+140"
    # H.264 plays in every Telegram app, so it wins over a sharper VP9 that also fits.
    assert video.pick_format(info, 340 * mb) == "136+140"
    info["formats"] = [fmt for fmt in info["formats"] if fmt["format_id"] != "136"]
    assert video.pick_format(info, 340 * mb) == "248+140"
    assert video.pick_format(info, 10 * mb) is None
    assert video.sizes_known(info)


def test_delivery_cache_reuses_and_evicts(tmp_path, monkeypatch):
    cache = load("downloader_bot", "delivery_cache")
    clock = {"now": 1000.0}
    monkeypatch.setattr(cache, "_clock", lambda: clock["now"])
    cache.configure(str(tmp_path))
    user = 31337

    def deliver(url: str) -> None:
        cache.note_link(user, "youtube", url)
        path = tmp_path / f"{abs(hash(url))}.mp4"
        path.write_bytes(b"x" * 1000)
        assert cache.keep(user, "youtube", url, [("item", str(path))])

    for n in range(cache.PER_USER + 1):
        deliver(f"https://youtu.be/{n}")
        clock["now"] += 1
    assert not cache.has(user, "youtube", "https://youtu.be/0")
    assert cache.has(user, "youtube", f"https://youtu.be/{cache.PER_USER}")

    entry = cache.take(user, "youtube", "https://youtu.be/1")
    assert entry is not None and os.path.exists(entry.items[0][1])
    cache.release(entry)

    clock["now"] += cache.MAX_LIFE_SECONDS + 1
    assert cache.take(user, "youtube", "https://youtu.be/1") is None
    cache.drop(user)


# ── resources and updates ────────────────────────────────────────────────────

def test_memory_leaves_out_the_disk_cache():
    resources = load(STICKER, "resources")
    assert resources._reclaimable_cache({"active_file": 100, "inactive_file": 50, "shmem": 30}) == 120
    assert resources._reclaimable_cache(
        {"total_active_file": 10, "total_inactive_file": 5, "total_shmem": 0}, "total_") == 15


def test_per_user_updates_drop_extras_but_keep_payments():
    features = load(STICKER, "shared_features")

    class Thing:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    def update(paid=False, checkout=False):
        return Thing(effective_user=Thing(id=7), effective_chat=Thing(id=7),
                     message=Thing(successful_payment=object() if paid else None),
                     pre_checkout_query=object() if checkout else None)

    async def scenario():
        processor = features.PerUserUpdateProcessor(waiting_max=2)
        gate = asyncio.Event()
        ran = []

        async def work(name):
            await gate.wait()
            ran.append(name)

        tasks = [asyncio.create_task(processor.do_process_update(update(), work(n))) for n in range(5)]
        tasks.append(asyncio.create_task(processor.do_process_update(update(paid=True), work("paid"))))
        await asyncio.sleep(0)
        await processor.do_process_update(update(checkout=True), asyncio.sleep(0))
        gate.set()
        await asyncio.gather(*tasks)
        return processor.dropped, ran

    dropped, ran = asyncio.run(scenario())
    assert dropped == 2
    assert "paid" in ran and len(ran) == 4


# ── the data key from a sealed variable ──────────────────────────────────────

def test_data_key_variable_becomes_a_private_file(monkeypatch):
    import main
    from conftest import TEST_KEY
    monkeypatch.setattr(main, "_KEY_COPY", None)
    monkeypatch.setenv("DATA_KEY_FILE", "/run/secrets/mumu_data_key")
    monkeypatch.setenv("DATA_KEY", TEST_KEY)
    main._key_from_variable()
    path = os.environ["DATA_KEY_FILE"]
    try:
        assert "DATA_KEY" not in os.environ and path == main._KEY_COPY
        assert open(path, encoding="ascii").read().strip() == TEST_KEY
    finally:
        os.remove(path)

    monkeypatch.setenv("DATA_KEY_FILE", "/run/secrets/mumu_data_key")
    monkeypatch.setenv("DATA_KEY", "not a key")
    main._key_from_variable()
    assert os.environ["DATA_KEY_FILE"] == "/run/secrets/mumu_data_key" and "DATA_KEY" not in os.environ


def test_encryption_script_takes_the_key_from_a_variable(family, monkeypatch, capsys):
    import importlib.util
    from conftest import DATABASE_URL, ROOT, TEST_KEY
    spec = importlib.util.spec_from_file_location("encrypt_script", ROOT / "scripts" / "encrypt_personal_data.py")
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    monkeypatch.setenv("REHEARSAL_DB", DATABASE_URL)
    monkeypatch.setenv("REHEARSAL_KEY", TEST_KEY)
    assert script.main(["--database-url-env", "REHEARSAL_DB", "--key-env", "REHEARSAL_KEY", "--dry-run"]) == 0
    said = capsys.readouterr().out
    assert "Dry run: rolled back" in said
    assert not [name for name in os.listdir(tempfile_dir()) if name.startswith("mumu-data-key-")]
    with pytest.raises(SystemExit):
        script.main(["--database-url-env", "REHEARSAL_DB", "--key-env", "REHEARSAL_KEY", "--key-file", "x"])


def tempfile_dir() -> str:
    import tempfile
    return tempfile.gettempdir()
