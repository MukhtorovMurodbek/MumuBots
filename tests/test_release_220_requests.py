"""Scenarios for the owner's 2.2.0 requests of 2026-10-28: StickerBot's per-sticker replies,
emoji by reply, public packs and groups; DownloaderBot's quality picker, shorter cache and
groups; the shorter problem note; the nightly report's unique users. Also AnonBot's Cancel
button under the line a link tap opens with (2026-10-31)."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from conftest import connect, load

STICKER = "sticker_bot"
DOWNLOADER = "downloader_bot"
MANAGER = "manager_bot"
ANON = "anon_bot"
MB = 1024 * 1024


# ── the problem note ─────────────────────────────────────────────────────────

def test_problem_note_is_three_short_lines_and_keeps_its_code():
    features = load(STICKER, "shared_features")
    problems = load(STICKER, "problems")
    text = features._with_logged_note("Couldn't download that file: Timed out", "FM-CRASH", "en")
    assert text == "Couldn't download that file: Timed out\nAnonymous report is saved\n🆔 FM-CRASH"
    # The Report button and the problem log find a code on its own line too.
    assert problems.find_code(text) == "FM-CRASH"
    assert problems.strip_code(text) == "Couldn't download that file: Timed out\nAnonymous report is saved"
    # Applied twice, it does not grow.
    assert features._with_logged_note(text, "FM-CRASH", "en") == text


# ── DownloaderBot: the quality picker ────────────────────────────────────────

FORMATS = {"duration": 600, "formats": [
    {"format_id": "160", "vcodec": "avc1.4d400c", "acodec": "none", "height": 144, "filesize": 1 * MB},
    {"format_id": "134", "vcodec": "avc1.4d401e", "acodec": "none", "height": 360, "filesize": 3 * MB},
    {"format_id": "136", "vcodec": "avc1.4d401f", "acodec": "none", "height": 720, "filesize": 30 * MB},
    {"format_id": "137", "vcodec": "avc1.640028", "acodec": "none", "height": 1080, "filesize": 90 * MB},
    {"format_id": "140", "vcodec": "none", "acodec": "mp4a.40.2", "abr": 129, "filesize": 2 * MB},
]}


def test_quality_options_list_every_height_with_its_size():
    video = load(DOWNLOADER, "video")
    assert video.quality_options(FORMATS) == [(144, 3 * MB), (360, 5 * MB), (720, 32 * MB), (1080, 92 * MB)]
    # Picked exactly: that height, or nothing -- never a smaller one in its place.
    assert video.pick_format(FORMATS, 48 * MB, 720, exact=True) == "136+140"
    assert video.pick_format(FORMATS, 48 * MB, 1080, exact=True) is None
    # A group's cap: the best at or under it that fits.
    assert video.pick_format(FORMATS, 48 * MB, 480) == "134+140"
    assert video.pick_format(FORMATS, 1000 * MB) == "137+140"


def test_quality_choices_mark_free_and_paid_and_leave_out_the_rest():
    bot = load(DOWNLOADER, "bot")
    options = [(144, 3 * MB), (720, 32 * MB), (1080, 92 * MB), (2160, 900 * MB)]
    assert bot.quality_choices(options, 48, 0) == [(144, 3 * MB, True), (720, 32 * MB, True)]
    assert bot.quality_choices(options, 48, 500) == [
        (144, 3 * MB, True), (720, 32 * MB, True), (1080, 92 * MB, False)]


def test_a_chosen_height_travels_on_the_link():
    bot = load(DOWNLOADER, "bot")
    link = "https://youtu.be/abc"
    assert bot._split_height(link) == (link, None, False)
    assert bot._split_height(bot._with_height(link, 720)) == (link, 720, True)
    assert bot._split_height(bot._with_height(link, 480, exact=False)) == (link, 480, False)
    # Two qualities of one video are two different keys.
    assert bot._with_height(link, 720) != bot._with_height(link, 1080) != link


def test_the_last_download_is_kept_for_ten_minutes_by_default():
    # The test run sets neither DBOT_CACHE_PER_USER nor DBOT_CACHE_SECONDS.
    cache = load(DOWNLOADER, "delivery_cache")
    assert cache.PER_USER == 1
    assert cache.LIFE_SECONDS == 600


# ── DownloaderBot: group chats ───────────────────────────────────────────────

def test_group_settings_default_to_every_link_as_video():
    groups = load(DOWNLOADER, "groups")
    settings = groups.normalize(None)
    assert settings["on"] and not settings["as_file"]
    assert set(settings["platforms"]) == set(groups.PLATFORMS)
    # Something stored by a later version, or broken, still reads.
    odd = groups.normalize({"quality": 999, "platforms": ["tiktok", "myspace"], "as_file": "yes", "new": 1})
    assert odd["quality"] == 0 and odd["platforms"] == ["tiktok"] and odd["as_file"] is False
    assert "new" not in odd


def test_group_settings_taps():
    groups = load(DOWNLOADER, "groups")
    settings = groups.normalize(None)
    off = groups.apply(settings, "p", "tiktok")
    assert "tiktok" not in off["platforms"] and "tiktok" in settings["platforms"]
    back = groups.apply(off, "p", "tiktok")
    assert back["platforms"] == list(groups.PLATFORMS)
    assert groups.apply(settings, "q", "480")["quality"] == 480
    assert groups.apply(settings, "q", "123") is None
    assert groups.apply(settings, "as_file")["as_file"] is True
    assert groups.apply(settings, "nonsense") is None
    assert groups.parse("gs:p:tiktok") == ("p", "tiktok") and groups.parse("redo:x") is None
    # Anybody may change a setting, unless an admin locked them; the lock is admins' alone.
    assert not groups.needs_admin("p", settings)
    assert groups.needs_admin("admins", settings)
    assert groups.needs_admin("p", groups.apply(settings, "admins"))


def test_group_settings_are_kept_and_forgotten():
    db = load(DOWNLOADER, "db")
    db.init_db()
    groups = load(DOWNLOADER, "groups")
    assert db.get_group_settings(-100123) is None
    db.set_group_settings(-100123, groups.apply(groups.normalize(None), "q", "360"))
    assert groups.normalize(db.get_group_settings(-100123))["quality"] == 360
    db.forget_group(-100123)
    assert db.get_group_settings(-100123) is None


def test_group_commands_for_this_bot_only():
    bot = load(DOWNLOADER, "bot")
    message = lambda text: SimpleNamespace(text=text)
    assert bot._group_command(message("/settings")) == "settings"
    assert bot._group_command(message(f"/settings@{bot.BOT_USERNAME or 'x'}")) == (
        "settings" if bot.BOT_USERNAME else "")
    assert bot._group_command(message("/settings@SomebodyElseBot")) == ""
    assert bot._group_command(message("look https://vt.tiktok.com/x")) is None


# ── StickerBot: which message stands for which sticker ───────────────────────

def _message(chat_id=1, message_id=1, **parts):
    fields = {"photo": [], "animation": None, "video": None, "document": None, "sticker": None}
    fields.update(parts)
    return SimpleNamespace(chat_id=chat_id, message_id=message_id, **fields)


def test_a_reply_finds_its_sticker():
    ops = load(STICKER, "sticker_ops")
    sticker = SimpleNamespace(set_name="pack_by_bot", file_id="STICKER", is_animated=False, is_video=False)
    assert ops.sticker_of(_message(sticker=sticker)) == ("pack_by_bot", "STICKER")
    photo_message = _message(chat_id=5, message_id=42, photo=[SimpleNamespace(file_id="P")])
    assert ops.sticker_of(photo_message) is None
    ops.remember(5, 42, "pack_by_bot", "NEW")
    assert ops.sticker_of(photo_message) == ("pack_by_bot", "NEW")
    assert ops.sticker_of(None) is None


def test_what_can_become_a_sticker():
    ops = load(STICKER, "sticker_ops")
    assert ops.media_of(_message(photo=[SimpleNamespace(file_id="small"), SimpleNamespace(file_id="big")])) == (
        "static", "big")
    assert ops.media_of(_message(animation=SimpleNamespace(file_id="gif"))) == ("video", "gif")
    assert ops.media_of(_message(document=SimpleNamespace(file_id="d", mime_type="image/png"))) == ("static", "d")
    assert ops.media_of(_message(document=SimpleNamespace(file_id="d", mime_type="application/pdf"))) is None
    lottie = SimpleNamespace(set_name=None, file_id="t", is_animated=True, is_video=False)
    assert ops.media_of(_message(sticker=lottie)) is None


# ── StickerBot: public packs and groups ──────────────────────────────────────

OWNER, EDITOR, STRANGER, GROUP = 9001, 9002, 9003, -100777


@pytest.fixture(scope="module")
def packs():
    db = load(STICKER, "db")
    db.init_db()
    link = load(STICKER, "family_link")
    link.init_family_schema()
    assert link.check_data_key(fresh=True)["usable"]
    db.add_pack(OWNER, "open_by_bot", "Open")
    db.add_pack(OWNER, "closed_by_bot", "Closed")
    db.add_editor("closed_by_bot", EDITOR)
    db.set_pack_public("open_by_bot", True)
    return db


def test_packs_are_private_by_default(packs):
    assert not packs.is_pack_public("closed_by_bot")
    assert packs.is_pack_public("open_by_bot")
    assert packs.pack_access("nope_by_bot", OWNER) is None


def test_who_may_add_where(packs):
    # Owner and co-editor: anywhere. A stranger: nowhere, until the pack is public and its
    # owner is known in the group.
    assert packs.pack_access("closed_by_bot", OWNER)["allowed"]
    assert packs.pack_access("closed_by_bot", EDITOR, GROUP)["allowed"]
    assert not packs.pack_access("closed_by_bot", STRANGER, GROUP)["allowed"]
    assert not packs.pack_access("open_by_bot", STRANGER, GROUP)["allowed"]
    assert packs.group_packs(GROUP) == []

    packs.note_group_member(GROUP, OWNER)
    assert packs.pack_access("open_by_bot", STRANGER, GROUP)["allowed"]
    assert not packs.pack_access("open_by_bot", STRANGER)["allowed"]        # a private chat
    assert not packs.pack_access("closed_by_bot", STRANGER, GROUP)["allowed"]
    assert packs.group_packs(GROUP) == [("open_by_bot", "Open")]

    packs.forget_group_member(GROUP, OWNER)
    assert packs.group_packs(GROUP) == []


def test_a_co_editor_brings_a_public_pack_into_their_group(packs):
    packs.set_pack_public("closed_by_bot", True)
    try:
        packs.note_group_member(GROUP, EDITOR)
        assert packs.group_packs(GROUP) == [("closed_by_bot", "Closed")]
        assert packs.pack_access("closed_by_bot", STRANGER, GROUP)["allowed"]
    finally:
        packs.set_pack_public("closed_by_bot", False)
        packs.forget_group(GROUP)
    assert packs.group_packs(GROUP) == []


def test_erasing_a_person_forgets_their_groups(packs):
    packs.note_group_member(GROUP, STRANGER)
    packs.erase_user(STRANGER)
    with connect() as conn:
        assert conn.execute(f"SELECT count(*) FROM {STICKER}.group_members WHERE user_id = %s",
                            (STRANGER,)).fetchone()[0] == 0


def test_sticker_group_commands():
    group_mode = load(STICKER, "group_mode")
    message = lambda text: SimpleNamespace(text=text)
    assert group_mode._command(message("/add 2"), "StickerBot") == ("add", ["2"])
    assert group_mode._command(message("/add@stickerbot https://t.me/addstickers/x"), "StickerBot") == (
        "add", ["https://t.me/addstickers/x"])
    assert group_mode._command(message("/add@OtherBot 2"), "StickerBot") == ("", [])
    assert group_mode._command(message("hello"), "StickerBot") is None
    topic_root = SimpleNamespace(forum_topic_created=object())
    assert group_mode._target(SimpleNamespace(reply_to_message=topic_root)) is None
    picture = SimpleNamespace(forum_topic_created=None)
    assert group_mode._target(SimpleNamespace(reply_to_message=picture)) is picture


# ── ManagerBot: the nightly report ───────────────────────────────────────────

def test_nightly_report_is_named_nightly_and_counts_people():
    bot = load(MANAGER, "bot")
    people = bot.people_section(120, 14, True, (110, 20))
    assert people[1].endswith("120 (+10 since the last report)")
    assert people[2].endswith("14 (-6 since the last report)")
    first = bot.people_section(5, 2, True, None)
    assert first[1].endswith(": 5") and first[2].endswith(": 2")
    assert "AnonBot left out" in bot.people_section(5, 2, False, None)[2]
    report = bot.build_problem_report([], 24, people=people, now=0)
    assert report.startswith("🌙 <b>Nightly report, last 24h</b>")
    assert "Unique users" in report
    assert "nightly" in bot.HELP_PAGES and bot.HELP_ALIASES["problems"] == "nightly"


def test_people_in_the_last_day_counts_each_person_once():
    db = load(MANAGER, "db")
    load(STICKER, "db").init_db()
    load(STICKER, "family_link").init_family_schema()
    load(MANAGER, "family_link").check_data_key(fresh=True)
    now = datetime.now(timezone.utc)
    with connect() as conn:
        conn.execute(f"DELETE FROM {STICKER}.activity_events")
        conn.execute(f"INSERT INTO {STICKER}.activity_events (user_id, occurred_at) "
                     "SELECT unnest(%s::bigint[]), %s", ([501, 502, 502, 777000], now - timedelta(hours=2)))
        conn.execute(f"INSERT INTO {STICKER}.activity_events (user_id, occurred_at) VALUES (503, %s)",
                     (now - timedelta(hours=30),))
    count, complete = db.people_since([STICKER], now - timedelta(hours=24))
    assert (count, complete) == (2, True)


# ── StickerBot: co-edit link limits and invitations ──────────────────────────

FRIEND, OTHER = 9101, 9102


def test_a_link_lets_in_at_most_its_limit_until_it_expires(packs):
    packs.add_pack(OWNER, "linked_by_bot", "Linked")
    view = packs.get_coedit_view("linked_by_bot")
    assert view["max_editors"] == 10 and view["valid_days"] == 7 and view["editors"] == 0
    assert timedelta(days=6, hours=23) < view["expires_at"] - datetime.now(timezone.utc) <= timedelta(days=7, minutes=1)
    token = view["token"]
    assert packs.join_by_token("nope", FRIEND) == ("invalid", None)
    assert packs.join_by_token(token, OWNER) == ("own", "linked_by_bot")

    packs.set_max_editors("linked_by_bot", 1)
    assert packs.join_by_token(token, FRIEND) == ("joined", "linked_by_bot")
    assert packs.join_by_token(token, FRIEND) == ("already", "linked_by_bot")
    assert packs.join_by_token(token, OTHER) == ("full", "linked_by_bot")

    packs.set_max_editors("linked_by_bot", 10)
    with connect() as conn:
        conn.execute(f"UPDATE {STICKER}.pack_share_tokens SET expires_at = now() - interval '1 minute' "
                     "WHERE pack_name = 'linked_by_bot'")
    assert packs.join_by_token(token, OTHER) == ("expired", "linked_by_bot")
    # Somebody already in is never turned away by an expired link.
    assert packs.join_by_token(token, FRIEND) == ("already", "linked_by_bot")
    # A new link works again, for the same period, and the old one stops.
    new_token = packs.reset_share_token("linked_by_bot")
    assert packs.join_by_token(token, OTHER) == ("invalid", None)
    assert packs.join_by_token(new_token, OTHER) == ("joined", "linked_by_bot")

    packs.set_link_valid_days("linked_by_bot", None)
    assert packs.get_coedit_view("linked_by_bot")["expires_at"] is None
    packs.remove_editor("linked_by_bot", OTHER)
    assert packs.pack_editors("linked_by_bot") == [FRIEND]
    assert not packs.pack_access("linked_by_bot", OTHER)["allowed"]
    assert packs.shared_packs(FRIEND) == [("linked_by_bot", "Linked")]


def test_invitations_by_username(packs):
    packs.add_pack(OWNER, "invited_by_bot", "Invited")
    packs.set_max_editors("invited_by_bot", 3)
    invited, full = packs.add_invites("invited_by_bot", ["Alice_one", "bob_two", "carol_3", "dave_four"])
    assert invited == ["Alice_one", "bob_two", "carol_3"] and full == ["dave_four"]
    assert [name for _, name in packs.pack_invites("invited_by_bot")] == ["Alice_one", "bob_two", "carol_3"]
    # Invitations count against the link's limit too.
    assert packs.join_by_token(packs.get_coedit_view("invited_by_bot")["token"], OTHER)[0] == "full"
    # Stored sealed and as a keyed hash: the name is nowhere in the clear.
    with connect() as conn:
        rows = conn.execute(f"SELECT username_mark, username_sealed FROM {STICKER}.pack_invites").fetchall()
    assert rows and all("alice" not in mark.lower() and b"Alice" not in bytes(sealed) for mark, sealed in rows)

    assert packs.claim_invites(FRIEND, "@ALICE_ONE") == [("invited_by_bot", "Invited")]
    assert packs.pack_access("invited_by_bot", FRIEND)["allowed"]
    assert packs.claim_invites(FRIEND, "alice_one") == []
    assert packs.claim_invites(OTHER, None) == []
    mark = packs.pack_invites("invited_by_bot")[0][0]
    packs.remove_invite("invited_by_bot", mark)
    assert [name for _, name in packs.pack_invites("invited_by_bot")] == ["carol_3"]


def test_links_made_before_the_limits_get_the_defaults(packs):
    with connect() as conn:
        conn.execute(f"ALTER TABLE {STICKER}.pack_share_tokens DROP COLUMN max_editors, "
                     "DROP COLUMN valid_days, DROP COLUMN expires_at, DROP COLUMN editors_delete")
        conn.execute(f"INSERT INTO {STICKER}.pack_share_tokens (pack_name, token) VALUES ('old_by_bot', 'oldtoken')")
    packs.add_pack(OWNER, "old_by_bot", "Old")
    packs.init_db()
    view = packs.get_coedit_view("old_by_bot")
    assert view["token"] == "oldtoken" and view["max_editors"] == 10 and view["valid_days"] == 7
    assert timedelta(days=6) < view["expires_at"] - datetime.now(timezone.utc) <= timedelta(days=7, minutes=1)
    # And its co-editors do not delete until the owner says so.
    assert view["editors_delete"] is False


def test_group_pack_list_sends_without_a_preview(packs):
    import asyncio
    group_mode = load(STICKER, "group_mode")
    sent = {}

    async def reply_text(text, **kwargs):
        sent.update(kwargs, text=text)

    user = SimpleNamespace(id=OWNER, username=None)
    update = SimpleNamespace(effective_chat=SimpleNamespace(id=GROUP), effective_user=user,
                             effective_message=SimpleNamespace(reply_text=reply_text))
    context = SimpleNamespace(user_data={"lang": "en"}, chat_data={})
    asyncio.run(group_mode.packs_command(update, context))
    assert "link_preview_options" in sent and "Packs you can add to here" in sent["text"]
    packs.forget_group(GROUP)


# ── the review of 2026-10-28: group chats as the bots really see them ────────

class _Bot:
    """Just enough of a Bot for the handlers below; keeps what they did."""
    id, username = 777, "StickerBot"

    def __init__(self):
        self.edited, self.retagged = [], []

    async def get_chat(self, user_id):
        return SimpleNamespace(username=None, full_name=f"Person {user_id}")

    async def edit_message_text(self, **kwargs):
        self.edited.append(kwargs)

    async def set_sticker_emoji_list(self, sticker, emoji_list):
        self.retagged.append((sticker, emoji_list))


def _update(chat_type, user_id, replies, text="", reply_to=None, chat_id=GROUP):
    async def reply_text(said, **kwargs):
        replies.append(said)
        return SimpleNamespace(chat_id=chat_id, message_id=5000 + len(replies))
    message = SimpleNamespace(chat_id=chat_id, message_id=4000, text=text, caption=None,
                              reply_to_message=reply_to, left_chat_member=None,
                              migrate_from_chat_id=None, reply_text=reply_text)
    return SimpleNamespace(effective_chat=SimpleNamespace(id=chat_id, type=chat_type),
                           effective_user=SimpleNamespace(id=user_id, username=None),
                           effective_message=message, message=message, edited_message=None,
                           callback_query=None)


def test_group_chatter_counts_nobody_and_is_never_throttled():
    import asyncio
    features = load(DOWNLOADER, "shared_features")
    live = load(DOWNLOADER, "live_message")
    replies = []
    chatter = _update("supergroup", 4242, replies, "lol", chat_id=-100555)
    context = SimpleNamespace(user_data={}, application=None)
    asyncio.run(features.track_activity(chatter, context))
    # DownloaderBot reads a whole group: chatting there is not using the bot...
    assert 4242 not in features._activity_buffer and "_last_seen" not in context.user_data
    # ...and does not bury a progress message, which is edited where it is.
    assert -100555 not in live._last_incoming
    # Whoever the bot serves there is counted, by the handler that serves them.
    features.note_active(context, chatter.effective_user)
    assert 4242 in features._activity_buffer and "_last_seen" in context.user_data
    # A private chat counts as it always did.
    private = _update("private", 4343, replies, "hi", chat_id=4343)
    asyncio.run(features.track_activity(private, SimpleNamespace(user_data={}, application=None)))
    assert 4343 in features._activity_buffer and 4343 in live._last_incoming

    # However much somebody says in a group, the flood gate neither counts it nor answers it.
    gate = []
    features.attach_flood_gate(SimpleNamespace(add_handler=lambda handler, group: gate.append(handler)))
    for _ in range(features.FLOOD_UPDATES_PER_MINUTE):
        features.flood_wait_seconds(4242)
    assert features.flood_wait_seconds(4242) > 0
    asyncio.run(gate[0].callback(chatter, context))   # no ApplicationHandlerStop
    assert replies == []


def test_emoji_means_symbols_not_words():
    emoji = load(STICKER, "emoji_utils")
    for text in ("😎", "❤️", "🇺🇿", "👍🏽", "‼️", " 😭😎 "):
        assert emoji.looks_like_emoji_message(text), text
    # Words in any script and punctuation are not: a reply "ок" to the bot's message in a
    # group is not a retag.
    for text in ("ok", "ок", "да", "rahmat", "?", "...", ":)", "😎 ok", ""):
        assert not emoji.looks_like_emoji_message(text), text


def test_emoji_in_a_group(packs):
    import asyncio
    from telegram.ext import ApplicationHandlerStop
    group_mode = load(STICKER, "group_mode")
    ops = load(STICKER, "sticker_ops")
    bot, errors, replies = _Bot(), [], []
    context = SimpleNamespace(bot=bot, user_data={"lang": "en"}, chat_data={},
                              application=SimpleNamespace(create_task=lambda coro, update=None: errors.append(coro)))

    def route(update):
        with pytest.raises(ApplicationHandlerStop):
            asyncio.run(group_mode.group_router(update, context))

    def message(message_id, sender, sticker=None):
        return SimpleNamespace(chat_id=GROUP, message_id=message_id, sticker=sticker,
                               from_user=SimpleNamespace(id=sender), forum_topic_created=None)

    open_sticker = SimpleNamespace(set_name="open_by_bot", file_id="STK", is_animated=False, is_video=False)
    posted = message(10, OWNER, open_sticker)
    # An emoji under a member's sticker is a reaction: nothing happens (the bot sees it only
    # as an admin of the group).
    route(_update("supergroup", OWNER, replies, "😎", posted))
    assert replies == [] and bot.retagged == []

    # /emoji alone, as a reply (what a tap in the command menu sends): asked for the emoji.
    route(_update("supergroup", OWNER, replies, "/emoji", posted))
    assert replies == ["Reply to this message with the new emoji for that sticker."]
    prompt = message(5001, bot.id)
    assert ops.sticker_of(prompt) == ("open_by_bot", "STK")
    # The emoji, as a reply to that question: the sticker is retagged.
    route(_update("supergroup", OWNER, replies, "😎", prompt))
    assert bot.retagged == [("STK", ["😎"])] and replies[-1] == "Retagged as 😎."

    # Somebody without access to a private pack is told so, and nothing is asked.
    closed = message(11, OWNER, SimpleNamespace(set_name="closed_by_bot", file_id="C", is_animated=False,
                                                is_video=False))
    route(_update("supergroup", STRANGER, replies, "/emoji", closed))
    assert "don't have access" in replies[-1] and ops.sticker_of(message(5000 + len(replies), bot.id)) is None
    assert errors == []
    packs.forget_group(GROUP)


def test_a_group_that_becomes_a_supergroup_keeps_its_members_and_settings(packs):
    packs.note_group_member(-500, OWNER)
    packs.move_group(-500, -100500)
    assert packs.group_packs(-100500) == [("open_by_bot", "Open")] and packs.group_packs(-500) == []
    packs.forget_group(-100500)
    db = load(DOWNLOADER, "db")
    db.init_db()
    db.set_group_settings(-501, {"quality": 360})
    db.move_group_settings(-501, -100501)
    assert db.get_group_settings(-501) is None and db.get_group_settings(-100501) == {"quality": 360}
    db.forget_group(-100501)


def test_a_group_download_is_not_remembered_for_buttons():
    bot = load(DOWNLOADER, "bot")
    context = SimpleNamespace(user_data={})
    token = bot._GROUP.set({"as_file": False})
    try:
        assert bot._remember_delivery(context, "tiktok", "https://vt.tiktok.com/x", False) is None
    finally:
        bot._GROUP.reset(token)
    assert context.user_data == {}
    assert bot._remember_delivery(context, "tiktok", "https://vt.tiktok.com/x", False) in context.user_data["redeliver"]


def test_a_kept_page_read_is_handed_out_as_a_copy():
    video = load(DOWNLOADER, "video")
    video._remember_read("https://youtu.be/copy", {"formats": [{"format_id": "18"}]})
    first = video._recall_read("https://youtu.be/copy")
    first["formats"].append({"format_id": "22"})
    assert video._recall_read("https://youtu.be/copy") == {"formats": [{"format_id": "18"}]}


def test_people_who_erased_their_data_leave_the_count_complete():
    db = load(MANAGER, "db")
    vault = load(MANAGER, "vault")
    assert load(MANAGER, "family_link").check_data_key(fresh=True)["usable"]
    since = datetime.now(timezone.utc) - timedelta(hours=24)
    with connect() as conn:
        conn.execute("CREATE SCHEMA IF NOT EXISTS review_anon")
        conn.execute("CREATE TABLE IF NOT EXISTS review_anon.user_settings (user_id BIGINT PRIMARY KEY, "
                     "user_sealed BYTEA)")
        conn.execute("CREATE TABLE IF NOT EXISTS review_anon.activity_events (user_id BIGINT, "
                     "occurred_at TIMESTAMPTZ)")
        conn.execute("INSERT INTO review_anon.activity_events VALUES (%s, now()), (%s, now())",
                     (vault.pseudonym(12345), vault.pseudonym(67890)))
        conn.execute("INSERT INTO review_anon.user_settings VALUES (%s, %s)",
                     (vault.pseudonym(12345), vault.seal_id(12345, "anon.user")))
    try:
        # 67890 has no seal any more (erased): left out, and the count is still whole.
        assert db.people_since(["review_anon"], since) == (1, True)
    finally:
        with connect() as conn:
            conn.execute("DROP SCHEMA review_anon CASCADE")


def test_a_picture_sent_during_a_question_goes_into_the_open_pack():
    import asyncio
    bot = load(STICKER, "bot")
    calls = []

    async def receive(update, context):
        calls.append(update)
        return bot.EDITING

    behind = bot._behind_question(receive)
    open_pack = SimpleNamespace(user_data={bot.STATUS_KEY: {"chat_id": 1, "message_id": 2}})
    # Added, and the question (None: the state it is in) keeps waiting.
    assert asyncio.run(behind("picture", open_pack)) is None and calls == ["picture"]
    # With no pack open, nothing happens, as before.
    assert asyncio.run(behind("other", SimpleNamespace(user_data={}))) is None and calls == ["picture"]


def test_the_co_edit_list_removes_the_invitation_its_button_shows(packs):
    import asyncio
    bot_module = load(STICKER, "bot")
    packs.add_pack(OWNER, "listed_by_bot", "Listed")
    packs.add_editor("listed_by_bot", FRIEND)
    packs.add_invites("listed_by_bot", ["first_one", "second_two"])
    pid = packs.get_coedit_view("listed_by_bot")["pack_id"]
    fake = _Bot()

    async def answer(text=None, **kwargs):
        pass

    def tap(data):
        query = SimpleNamespace(data=data, answer=answer, message=SimpleNamespace(chat_id=OWNER, message_id=99))
        update = SimpleNamespace(callback_query=query, effective_user=SimpleNamespace(id=OWNER, username=None),
                                 effective_chat=SimpleNamespace(id=OWNER, type="private"))
        asyncio.run(bot_module.coedit_button(update, SimpleNamespace(bot=fake, user_data={"lang": "en"})))
        return fake.edited[-1]

    shown = tap(f"co:list:{pid}")
    assert "Person 9101" in shown["text"] and "first_one" in shown["text"]
    second = next(button.callback_data for row in shown["reply_markup"].inline_keyboard for button in row
                  if "second_two" in button.text)
    # An invitation taken up between drawing the list and the tap: the button still means
    # the one it showed.
    packs.claim_invites(OTHER, "first_one")
    tap(second)
    assert packs.pack_invites("listed_by_bot") == []
    assert packs.pack_editors("listed_by_bot") == [FRIEND, OTHER]


# ── StickerBot: a sticker that is already in the pack (owner, 2026-10-28) ────

class _PackBot:
    """A Bot holding one pack's stickers: what was deleted from it and added to it."""
    id, username = 777, "StickerBot"

    def __init__(self, stickers):
        self.stickers = [SimpleNamespace(file_unique_id=unique, file_id=file_id) for unique, file_id in stickers]
        self.deleted, self.added = [], []

    async def get_sticker_set(self, name):
        return SimpleNamespace(stickers=list(self.stickers))

    async def delete_sticker_from_set(self, sticker):
        self.deleted.append(sticker)

    async def get_file(self, file_id):
        import io
        from PIL import Image
        picture = io.BytesIO()
        Image.new("RGB", (32, 32), "red").save(picture, "PNG")

        async def download_as_bytearray():
            return bytearray(picture.getvalue())
        return SimpleNamespace(download_as_bytearray=download_as_bytearray)

    async def add_sticker_to_set(self, user_id, name, sticker, **kwargs):
        self.added.append((user_id, name))
        n = len(self.stickers) + 1
        self.stickers.append(SimpleNamespace(file_unique_id=f"U{n}", file_id=f"S{n}"))


class _Said:
    """One of the bot's own messages, as it was last edited."""

    def __init__(self, chat_id, message_id, text, kwargs):
        self.chat_id, self.message_id, self.text, self.kwargs = chat_id, message_id, text, kwargs

    async def edit_text(self, text, **kwargs):
        self.text, self.kwargs = text, kwargs


IN_CLOSED = SimpleNamespace(set_name="closed_by_bot", file_unique_id="U1", file_id="S1", is_video=False,
                            is_animated=False)


def _sent(user_id, chat_id, said, sticker=None, reply_to=None, text=None):
    async def reply_text(text, **kwargs):
        said.append(_Said(chat_id, 6000 + len(said), text, kwargs))
        return said[-1]
    message = SimpleNamespace(chat_id=chat_id, message_id=10, sticker=sticker, photo=[], animation=None, video=None,
                              document=None, text=text, reply_to_message=reply_to, reply_text=reply_text)
    return SimpleNamespace(message=message, effective_message=message, callback_query=None,
                           effective_user=SimpleNamespace(id=user_id, username=None),
                           effective_chat=SimpleNamespace(id=chat_id, type="private" if chat_id > 0 else "supergroup"))


def _tap(bot_module, data, user_id, message, context, state=None):
    """Taps a button of the "already in the pack" question; the alerts it answered with. The
    state it returned goes into `state`, when given."""
    import asyncio
    alerts = []

    async def answer(text=None, **kwargs):
        alerts.append(text)
    query = SimpleNamespace(data=data, message=message, answer=answer)
    returned = asyncio.run(bot_module.already_in_pack_chosen(SimpleNamespace(
        callback_query=query, effective_user=SimpleNamespace(id=user_id, username=None),
        effective_chat=SimpleNamespace(id=message.chat_id, type="private")), context))
    if state is not None:
        state.append(returned)
    return alerts


def _buttons(said):
    return [(button.text, button.callback_data) for button in said.kwargs["reply_markup"].inline_keyboard[0]]


def _filling_closed(bot_module, fake, user_id):
    return SimpleNamespace(bot=fake, chat_data={}, user_data={
        "lang": "en", "target_pack": "closed_by_bot", "owner_id": OWNER, "title": "Closed", "pack_created": True,
        bot_module.STATUS_KEY: {"chat_id": user_id, "message_id": 1}})


def test_only_the_owner_deletes_unless_they_allow_it(packs):
    assert packs.pack_access("closed_by_bot", OWNER)["can_delete"]
    assert not packs.pack_access("closed_by_bot", EDITOR)["can_delete"]
    packs.set_editors_delete("closed_by_bot", True)
    try:
        assert packs.get_coedit_view("closed_by_bot")["editors_delete"] is True
        assert packs.pack_access("closed_by_bot", EDITOR)["can_delete"]
        assert not packs.pack_access("closed_by_bot", STRANGER)["can_delete"]
        # Somebody let in by a public pack, in a group, never deletes.
        packs.note_group_member(GROUP, OWNER)
        assert packs.pack_access("open_by_bot", STRANGER, GROUP)["allowed"]
        assert not packs.pack_access("open_by_bot", STRANGER, GROUP)["can_delete"]
    finally:
        packs.set_editors_delete("closed_by_bot", False)
        packs.forget_group(GROUP)


def test_a_sticker_already_in_someone_elses_pack_is_offered_again(packs):
    import asyncio
    bot_module = load(STICKER, "bot")
    fake, said = _PackBot([("U1", "S1"), ("U2", "S2")]), []
    context = _filling_closed(bot_module, fake, EDITOR)
    assert asyncio.run(bot_module.receive_media(_sent(EDITOR, EDITOR, said, IN_CLOSED), context)) == bot_module.EDITING
    assert fake.deleted == []
    assert said[-1].text == "This sticker is already in “Closed”. Add it again?"
    (yes_text, yes), (no_text, no) = _buttons(said[-1])
    assert (yes_text, no_text) == ("➕ Add again", "✖ Don't add")
    # Only whoever was asked answers, and only once.
    assert _tap(bot_module, yes, STRANGER, said[-1], context) == ["Only the person who sent it can answer."]
    assert _tap(bot_module, yes, EDITOR, said[-1], context) == [None]
    assert fake.added == [(OWNER, "closed_by_bot")] and fake.deleted == []
    assert said[-1].text.startswith("✅ Added to “Closed” as #3") and context.user_data["last_sticker_id"] == "S3"
    assert _tap(bot_module, no, EDITOR, said[-1], context) == ["That question has expired. Send the sticker again."]


def test_whoever_may_delete_is_asked_to_add_it_again_or_remove_it(packs):
    import asyncio
    bot_module = load(STICKER, "bot")

    def send(user_id, stickers, pack="closed_by_bot", title="Closed"):
        fake, said = _PackBot(stickers), []
        context = _filling_closed(bot_module, fake, user_id)
        context.user_data.update(target_pack=pack, title=title)
        sticker = SimpleNamespace(set_name=pack, file_unique_id="U1", file_id="S1", is_video=False, is_animated=False)
        asyncio.run(bot_module.receive_media(_sent(user_id, user_id, said, sticker), context))
        assert fake.deleted == []           # nothing happens before a button is tapped
        return fake, said[-1], context

    two, one = [("U1", "S1"), ("U2", "S2")], [("U1", "S1")]
    # The owner: both choices, and "Remove it" takes the sticker out.
    fake, question, context = send(OWNER, two)
    assert question.text == "This sticker is already in “Closed”. Add it again, or remove it from the pack?"
    (_, add), (remove_text, remove) = _buttons(question)
    assert remove_text == "🗑 Remove it"
    assert _tap(bot_module, remove, OWNER, question, context) == [None]
    assert fake.deleted == ["S1"] and question.text == "🗑️ Removed from the pack."

    # A co-editor: "Remove it" only once the owner allows it, and never for the last sticker.
    fake, question, context = send(EDITOR, two)
    assert [text for text, _ in _buttons(question)] == ["➕ Add again", "✖ Don't add"]
    packs.set_editors_delete("closed_by_bot", True)
    try:
        fake, question, context = send(EDITOR, two)
        _tap(bot_module, _buttons(question)[1][1], EDITOR, question, context)
        assert fake.deleted == ["S1"]
        fake, question, context = send(EDITOR, one)
        _tap(bot_module, _buttons(question)[1][1], EDITOR, question, context)
        assert fake.deleted == [] and question.text.startswith("That's the last sticker.")
        # Switched off again between the question and the tap: not removed.
        fake, question, context = send(EDITOR, two)
        packs.set_editors_delete("closed_by_bot", False)
        _tap(bot_module, _buttons(question)[1][1], EDITOR, question, context)
        assert fake.deleted == [] and question.text == "Only the pack's owner can delete its stickers."
    finally:
        packs.set_editors_delete("closed_by_bot", False)

    # The owner's last sticker: asked once more, since the whole pack goes with it.
    packs.add_pack(OWNER, "solo_by_bot", "Solo")
    fake, question, context = send(OWNER, one, pack="solo_by_bot", title="Solo")
    _tap(bot_module, _buttons(question)[1][1], OWNER, question, context)
    assert fake.deleted == [] and question.text.startswith("That's the last sticker — removing it")
    (sure_text, sure), (_, keep) = _buttons(question)
    assert sure_text == "🗑️ Yes, delete the pack"
    state = []
    _tap(bot_module, sure, OWNER, question, context, state)
    assert fake.deleted == ["S1"] and question.text == "🗑️ Pack deleted (it had no stickers left)."
    assert packs.pack_access("solo_by_bot", OWNER) is None
    assert state == [bot_module.ConversationHandler.END] and "target_pack" not in context.user_data


def test_nothing_is_deleted_in_a_group(packs):
    import asyncio
    group_mode = load(STICKER, "group_mode")
    fake, said = _PackBot([("U1", "S1"), ("U2", "S2")]), []
    posted = SimpleNamespace(chat_id=GROUP, message_id=20, sticker=IN_CLOSED, photo=[], animation=None, video=None,
                             document=None, from_user=SimpleNamespace(id=OWNER), forum_topic_created=None)
    link = "https://t.me/addstickers/closed_by_bot"
    context = SimpleNamespace(bot=fake, user_data={"lang": "en"}, chat_data={})
    # Even the owner: in a group a sticker already in the pack is only offered again.
    asyncio.run(group_mode.add_command(_sent(OWNER, GROUP, said, reply_to=posted, text=f"/add {link}"), context,
                                       [link]))
    assert fake.deleted == [] and fake.added == []
    assert said[-1].text == "This sticker is already in “Closed”. Add it again?"
    no = said[-1].kwargs["reply_markup"].inline_keyboard[0][1].callback_data
    assert [text for text, _ in _buttons(said[-1])] == ["➕ Add again", "✖ Don't add"]
    assert _tap(load(STICKER, "bot"), no, OWNER, said[-1], context) == [None] and said[-1].text == "OK, not added."
    assert fake.added == []
    packs.forget_group(GROUP)


# ── AnonBot: cancelling a link tap ───────────────────────────────────────────

GUEST, INBOX_OWNER = 4242, 4343


def _tapped_link(opening_line):
    db = load(ANON, "db")
    db.init_db()
    assert load(ANON, "family_link").check_data_key(fresh=True)["usable"]
    conv = db.start_new_anon_conversation(INBOX_OWNER, GUEST)
    db.record_anon_relays(GUEST, [opening_line], conv["id"])
    assert db.get_routing_context(GUEST, GUEST)["active"]["id"] == conv["id"]
    return db, conv["id"]


def test_a_cancelled_link_tap_leaves_nothing_to_send_into():
    db, conv = _tapped_link(10)
    assert db.cancel_unopened_conversation(conv, INBOX_OWNER) is False  # not their tap
    assert db.cancel_unopened_conversation(conv, GUEST) is True
    # The next plain message is not an opening one, and a reply to the
    # "Cancelled" line reaches nobody.
    assert db.get_routing_context(GUEST, GUEST)["active"] is None
    assert db.get_relay_row(GUEST, 10) is None
    assert db.get_conversation(conv) is None
    assert db.cancel_unopened_conversation(conv, GUEST) is False


def test_cancel_is_too_late_once_the_first_message_went():
    db, conv = _tapped_link(20)
    # The first message returns the opening line, so its button can come off.
    assert db.mark_follower_opened(conv, GUEST) == 20
    assert db.mark_follower_opened(conv, GUEST) is None
    assert db.cancel_unopened_conversation(conv, GUEST) is False
    assert db.get_routing_context(GUEST, GUEST)["active"]["id"] == conv


def test_the_cancel_button_changes_the_line_and_undoes_itself_when_too_late():
    import asyncio
    bot = load(ANON, "bot")

    def tap(conv):
        edits = []

        class Query:
            data = f"aq_fcancel:{conv}"

            async def answer(self, *args, **kwargs):
                pass

            async def edit_message_text(self, text, **kwargs):
                edits.append(text)

        update = SimpleNamespace(callback_query=Query(), effective_user=SimpleNamespace(id=GUEST))
        asyncio.run(bot.follow_cancel_callback(update, SimpleNamespace(user_data={}, bot=None)))
        return edits

    db, conv = _tapped_link(30)
    assert tap(conv) == ["Cancelled."]
    assert db.get_conversation(conv) is None

    # A first message beat the tap: the line goes back to what it said.
    db, conv = _tapped_link(40)
    db.mark_follower_opened(conv, GUEST)
    assert tap(conv)[-1].startswith("You're writing to someone anonymously")
