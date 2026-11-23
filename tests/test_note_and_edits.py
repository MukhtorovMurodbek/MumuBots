"""Since 2.2.1 (MumuCombined, 2026-11-23): DownloaderBot's copyright note can be turned off per chat with
/disclaimer, only from a message that shows it in full and a tap on "Yes", and in a group only by an
admin; AnonBot carries edits and /delete over to the other side for whoever /editing allows (everyone,
by default)."""
import asyncio
from types import SimpleNamespace

import pytest

from conftest import load

dl = load("downloader_bot", "bot")
dl_db = load("downloader_bot", "db")
anon = load("anon_bot", "bot")
anon_db = load("anon_bot", "db")
logic = load("anon_bot", "anon_logic")

OWNER, GUEST, ADMIN = 7001, 7002, 7003


@pytest.fixture(scope="module", autouse=True)
def databases():
    dl_db.init_db()
    anon_db.init_db()
    yield
    with dl_db.pooled() as conn:
        conn.execute("DELETE FROM copyright_note_off")
        conn.commit()
    people = [anon_db.vault.pseudonym(OWNER), anon_db.vault.pseudonym(GUEST)]
    with anon_db.pooled() as conn:
        conn.execute("DELETE FROM anon_relay WHERE conversation_id IN "
                     "(SELECT id FROM anon_conversations WHERE owner_user_id = ANY(%s))", (people,))
        conn.execute("DELETE FROM anon_conversations WHERE owner_user_id = ANY(%s)", (people,))
        conn.execute("DELETE FROM anon_follower_state WHERE follower_user_id = ANY(%s)", (people,))
        conn.execute("DELETE FROM anon_blocks WHERE owner_user_id = ANY(%s)", (people,))
        conn.commit()


def run(coro):
    return asyncio.run(coro)



def test_the_switch_is_kept_per_chat_with_who_and_when():
    assert dl_db.get_copyright_note_off(-100) is None
    dl_db.set_copyright_note_off(-100, 5, True)
    assert dl_db.get_copyright_note_off(-100).startswith("20")
    dl_db.set_copyright_note_off(-100, 5, False)
    assert dl_db.get_copyright_note_off(-100) is None


def test_a_group_s_switch_moves_with_it_and_goes_when_the_bot_leaves():
    dl_db.set_copyright_note_off(-400, 6, True)
    dl_db.move_group_settings(-400, -1000400)
    assert dl_db.get_copyright_note_off(-400) is None and dl_db.get_copyright_note_off(-1000400) is not None
    dl_db.forget_group(-1000400)
    assert dl_db.get_copyright_note_off(-1000400) is None


def test_a_group_never_lands_among_the_people_messaged_unprompted():
    dl_db.set_copyright_note_off(-200, 6, True)
    assert -200 not in dl_db.list_all_users()


def test_erasing_a_person_brings_the_note_back_where_they_turned_it_off():
    dl_db.set_copyright_note_off(8, 8, True)
    dl_db.set_copyright_note_off(-300, 8, True)
    dl_db.set_copyright_note_off(-301, 9, True)
    dl_db.erase_user(8)
    assert dl_db.get_copyright_note_off(8) is None and dl_db.get_copyright_note_off(-300) is None
    assert dl_db.get_copyright_note_off(-301) is not None


def _caption(monkeypatch, note_off_chats, credit, chat_id=1):
    monkeypatch.setattr(dl, "COPYRIGHT_URL", "")
    monkeypatch.setattr(dl, "BOT_USERNAME", "mumu_downloader_bot")
    monkeypatch.setattr(dl, "get_copyright_note_off", lambda chat: "2026-11-23" if chat in note_off_chats else None)

    async def wanted(user_id):
        return credit

    monkeypatch.setattr(dl, "_credit_wanted", wanted)
    update = SimpleNamespace(effective_user=SimpleNamespace(id=1), effective_chat=SimpleNamespace(id=chat_id))
    return run(dl._caption_for(update, "en"))


def test_the_caption_follows_the_chat_s_switch(monkeypatch):
    note = "<i>This material may be protected by copyright.</i>"
    assert _caption(monkeypatch, set(), credit=False) == note
    assert _caption(monkeypatch, {1}, credit=False) is None
    assert _caption(monkeypatch, {1}, credit=True) == "⬇️ via @mumu_downloader_bot"
    assert _caption(monkeypatch, {1}, credit=False, chat_id=-5) == note
    assert _caption(monkeypatch, {-5}, credit=True, chat_id=-5) == "⬇️ via @mumu_downloader_bot"


def test_an_unreadable_switch_keeps_the_note(monkeypatch):
    def broken(chat):
        raise RuntimeError("db down")

    monkeypatch.setattr(dl, "get_copyright_note_off", broken)
    assert run(dl._note_wanted(1)) is True


class _Bot:
    def __init__(self, status="member"):
        self.sent, self.status = [], status

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append((chat_id, text, kwargs))
        return SimpleNamespace(message_id=900 + len(self.sent))

    async def get_chat_member(self, chat_id, user_id):
        return SimpleNamespace(status=self.status)


def _chat(chat_id, kind="private"):
    return SimpleNamespace(id=chat_id, type=kind)


def _labels(markup):
    return [button.text for row in markup.inline_keyboard for button in row] if markup else []


def test_the_screen_shows_the_whole_note_and_asks_before_turning_it_off(monkeypatch):
    monkeypatch.setattr(dl, "get_copyright_note_off", lambda chat: None)
    text, markup = run(dl._note_screen(_chat(1), "en", True))
    assert "may be protected by copyright" in text and "responsible" in text and text.endswith("?")
    assert _labels(markup) == ["Yes, turn it off", "No, keep it"]
    text, markup = run(dl._note_screen(_chat(-5, "supergroup"), "en", False))
    assert "this group" in text and markup is None and "admins" in text
    monkeypatch.setattr(dl, "get_copyright_note_off", lambda chat: "2026-11-23T10:00:00+00:00")
    text, markup = run(dl._note_screen(_chat(1), "en", True))
    assert "(since 2026-11-23)" in text and _labels(markup) == ["Turn the note back on"]


def test_only_a_group_s_admins_may_change_it():
    user = SimpleNamespace(id=4)
    assert run(dl._may_change_note(_Bot("member"), _chat(4), user))
    assert not run(dl._may_change_note(_Bot("member"), _chat(-5, "group"), user))
    assert run(dl._may_change_note(_Bot("administrator"), _chat(-5, "group"), user))
    assert run(dl._may_change_note(_Bot("member"), _chat(-5, "group"), user, sender_chat=_chat(-5)))


class _Query:
    def __init__(self, data, chat):
        self.data, self.message = data, SimpleNamespace(chat=chat)
        self.answers, self.markup_removed = [], False

    async def answer(self, text=None, show_alert=False):
        self.answers.append((text, show_alert))

    async def edit_message_reply_markup(self, reply_markup=None):
        self.markup_removed = True


def _tap(monkeypatch, data, chat, status="member"):
    writes = []
    monkeypatch.setattr(dl, "set_copyright_note_off", lambda *a: writes.append(a))
    monkeypatch.setattr(dl, "get_copyright_note_off", lambda chat: None)

    async def lang(user_id, context):
        return "en"

    async def edit(message, bot, text, **kwargs):
        writes.append(("screen", text))

    monkeypatch.setattr(dl.i18n, "get_lang", lang)
    monkeypatch.setattr(dl, "edit_in_place", edit)
    query = _Query(data, chat)
    update = SimpleNamespace(callback_query=query, effective_user=SimpleNamespace(id=4))
    run(dl.note_callback(update, SimpleNamespace(bot=_Bot(status))))
    return query, writes


def test_yes_turns_it_off_for_the_chat_and_records_who(monkeypatch):
    query, writes = _tap(monkeypatch, "note:off", _chat(4))
    assert writes[0] == (4, 4, True) and query.answers[0][0] == "The note is off."


def test_a_member_s_tap_in_a_group_changes_nothing(monkeypatch):
    query, writes = _tap(monkeypatch, "note:off", _chat(-5, "group"))
    assert writes == [] and query.answers == [("Only the group's admins can change this.", True)]
    query, writes = _tap(monkeypatch, "note:off", _chat(-5, "group"), status="creator")
    assert writes[0] == (-5, 4, True)


def test_no_takes_the_buttons_away(monkeypatch):
    query, writes = _tap(monkeypatch, "note:keep", _chat(4))
    assert writes == [] and query.markup_removed and query.answers[0][0] == "The note stays."


def test_disclaimer_is_answered_in_a_group(monkeypatch):
    message = SimpleNamespace(text="/disclaimer@mumu_downloader_bot", sender_chat=None)
    monkeypatch.setattr(dl, "BOT_USERNAME", "mumu_downloader_bot")
    assert dl._group_command(message) == "disclaimer"


def test_the_note_texts_are_complete_and_impersonal():
    for lang in ("en", "uz", "ru"):
        for key in ("note_on", "note_on_group", "note_off", "note_off_group", "note_admins_only",
                    "note_settings_line", "group_cmd_disclaimer", "note_button_off", "note_button_keep"):
            text = dl.i18n.t(lang, key, note="N", since="2026-11-23", state="ON")
            assert text and "{" not in text, (lang, key)
        assert "/disclaimer" in dl.i18n.t(lang, "help_text") and "/disclaimer" in dl.i18n.t(lang, "privacy_stored")
    english = " ".join(dl.i18n.t("en", k, note="", since="", state="") for k in ("note_on", "note_off"))
    assert " you" not in english.lower() and " i " not in english.lower()



class _AnonBot:
    def __init__(self, fail=None):
        self.calls, self.fail = [], fail

    def __getattr__(self, name):
        async def call(**kwargs):
            self.calls.append((name, kwargs))
            if self.fail and name in self.fail:
                raise self.fail[name]
            return SimpleNamespace(message_id=555)
        return call

    async def send_message(self, chat_id, text, **kwargs):
        self.calls.append(("send_message", {"chat_id": chat_id, "text": text, **kwargs}))
        return SimpleNamespace(message_id=555)


def _message(text=None, caption=None, **media):
    fields = {"text": text, "caption": caption, "photo": (), "animation": None, "video": None,
              "audio": None, "document": None, "voice": None, "chat_id": GUEST, "message_id": 10}
    fields.update(media)
    return SimpleNamespace(**fields)


def test_an_edited_text_rebuilds_its_twin_with_header_and_buttons():
    bot = _AnonBot()
    run(logic.edit_relayed(bot, _message(text="fixed <b>"), OWNER, 99, header="📩 #3", reply_markup="KB"))
    name, args = bot.calls[0]
    assert name == "edit_message_text" and args["message_id"] == 99 and args["reply_markup"] == "KB"
    assert args["text"] == "📩 #3\n\n<i>fixed &lt;b&gt;</i>"


def test_an_edit_too_long_for_one_message_is_refused():
    with pytest.raises(logic.EditTooLong):
        run(logic.edit_relayed(_AnonBot(), _message(text="x" * 4100), OWNER, 99, header="h"))
    with pytest.raises(logic.EditTooLong):
        run(logic.edit_relayed(_AnonBot(), _message(caption="x" * 1100, photo=(SimpleNamespace(file_id="p"),)),
                               OWNER, 99))


def test_an_edited_photo_swaps_the_file_and_the_caption():
    bot = _AnonBot()
    run(logic.edit_relayed(bot, _message(caption="new", photo=(SimpleNamespace(file_id="small"),
                                                               SimpleNamespace(file_id="big"))),
                           OWNER, 99, header="h"))
    name, args = bot.calls[0]
    assert name == "edit_message_media" and args["media"].media == "big"
    assert args["media"].caption == "h\n\n<i>new</i>"


def test_a_voice_caption_is_edited_in_place():
    bot = _AnonBot()
    run(logic.edit_relayed(bot, _message(caption="c", voice=SimpleNamespace(file_id="v")), OWNER, 99))
    assert bot.calls[0][0] == "edit_message_caption"


def _route(twins=(77,), blocked=False):
    return {"conversation_id": 3, "conv_number": 4, "owner_user_id": OWNER, "follower_user_id": GUEST,
            "blocked": blocked, "twins": list(twins)}


@pytest.fixture
def anon_env(monkeypatch):
    state = {"route": _route(), "forgotten": [], "recorded": [], "shown": []}
    monkeypatch.setattr(anon, "get_edit_route", lambda chat, message: state["route"])
    monkeypatch.setattr(anon, "get_user_language", lambda user: "en")
    monkeypatch.setattr(anon, "forget_anon_relays", lambda chat, ids: state["forgotten"].append((chat, list(ids))))
    monkeypatch.setattr(anon, "record_anon_relays", lambda *a: state["recorded"].append(a))
    monkeypatch.setattr(anon, "message_allowed", lambda user: 0)
    monkeypatch.setattr(anon, "note_stat", lambda *a: None)

    async def lang(user_id, context):
        return "en"

    async def not_updating(*a):
        return False

    async def shown(context, chat_id, text):
        state["shown"].append(text)

    async def denied(update, context):
        state["shown"].append("unknown command")

    monkeypatch.setattr(anon.i18n, "get_lang", lang)
    monkeypatch.setattr(anon, "_refuse_if_updating", not_updating)
    monkeypatch.setattr(anon, "_show_briefly", shown)
    monkeypatch.setattr(anon, "_deny", denied)
    monkeypatch.setattr(anon, "ADMIN_IDS", {GUEST})
    anon._edits_cache.update(at=None, mode="admins")
    return state


def test_the_guest_s_edit_reaches_the_owner_s_copy(anon_env):
    bot = _AnonBot()
    answer = run(anon._relay_edit(SimpleNamespace(bot=bot), _message(text="new"), GUEST))
    assert answer == "edit_relayed"
    name, args = bot.calls[0]
    assert args["chat_id"] == OWNER and args["message_id"] == 77
    assert args["text"].startswith("📩") and args["reply_markup"].inline_keyboard[0][1].callback_data == "aq_block:3"


def test_the_owner_s_edit_reaches_the_guest_with_the_guest_s_buttons(anon_env):
    bot = _AnonBot()
    run(anon._relay_edit(SimpleNamespace(bot=bot), _message(text="new", chat_id=OWNER), OWNER))
    args = bot.calls[0][1]
    assert args["chat_id"] == GUEST and len(args["reply_markup"].inline_keyboard[0]) == 1


def test_a_message_sent_in_parts_and_a_blocked_guest_are_not_edited(anon_env):
    bot = _AnonBot()
    anon_env["route"] = _route(twins=(77, 78))
    assert run(anon._relay_edit(SimpleNamespace(bot=bot), _message(text="x"), GUEST)) == "edit_not_relayed_parts"
    anon_env["route"] = _route(blocked=True)
    assert run(anon._relay_edit(SimpleNamespace(bot=bot), _message(text="x"), GUEST)) is None
    anon_env["route"] = None
    assert run(anon._relay_edit(SimpleNamespace(bot=bot), _message(text="x"), GUEST)) is None
    assert bot.calls == []


def test_unchanged_text_is_not_an_error(anon_env):
    bot = _AnonBot(fail={"edit_message_text": anon.BadRequest("Message is not modified")})
    assert run(anon._relay_edit(SimpleNamespace(bot=bot), _message(text="same"), GUEST)) is None


class _Reply:
    def __init__(self):
        self.replies = []

    async def reply_text(self, text, **_):
        self.replies.append(text)


def _edited_update(user_id):
    edited = _message(text="new", chat_id=user_id)
    edited.reply_text = _Reply().reply_text
    edited.replies = edited.reply_text.__self__.replies
    return SimpleNamespace(edited_message=edited, effective_user=SimpleNamespace(id=user_id),
                           effective_chat=SimpleNamespace(id=user_id, type="private"))


def test_who_may_edit_follows_the_switch(anon_env, monkeypatch):
    for mode, expected in (("admins", {GUEST}), ("everyone", {GUEST, OWNER}), ("off", set())):
        anon._edits_cache.update(at=None, mode="admins")
        monkeypatch.setattr(anon.family_link, "get_setting", lambda key, default=None, m=mode: m)
        assert {u for u in (GUEST, OWNER) if run(anon._edits_allowed(u))} == expected
    monkeypatch.setattr(anon.family_link, "get_setting", lambda key, default=None: default)
    anon._edits_cache.update(at=None)
    assert anon._edits_mode() == "everyone"


def test_everyone_else_keeps_the_old_answer_to_an_edit(anon_env, monkeypatch):
    monkeypatch.setattr(anon.family_link, "get_setting", lambda key, default=None: "admins")
    monkeypatch.setattr(anon, "get_conversation_for_relay", lambda chat, message: 3)
    update = _edited_update(OWNER)
    with pytest.raises(anon.ApplicationHandlerStop):
        run(anon.edited_message(update, SimpleNamespace(bot=_AnonBot())))
    assert update.edited_message.replies == [anon.i18n.t("en", "edit_not_relayed")]

    update = _edited_update(GUEST)
    with pytest.raises(anon.ApplicationHandlerStop):
        run(anon.edited_message(update, SimpleNamespace(bot=_AnonBot())))
    assert update.edited_message.replies == [] and anon_env["shown"] == ["✏️ The edit has been passed on."]


def _delete_update(user_id, target_from=None, reply=True):
    message = SimpleNamespace(chat_id=user_id, message_id=50, replies=[])

    async def reply_text(text, **_):
        message.replies.append(text)

    message.reply_text = reply_text
    message.reply_to_message = (SimpleNamespace(message_id=10, from_user=SimpleNamespace(id=target_from or user_id))
                                if reply else None)
    return SimpleNamespace(message=message, effective_user=SimpleNamespace(id=user_id),
                           effective_chat=SimpleNamespace(id=user_id, type="private"))


def test_delete_removes_both_copies_and_leaves_the_owner_a_line_to_block_from(anon_env, monkeypatch):
    monkeypatch.setattr(anon.family_link, "get_setting", lambda key, default=None: "admins")
    bot = _AnonBot()
    update = _delete_update(GUEST)
    run(anon.delete_command(update, SimpleNamespace(bot=bot)))
    deleted = [(a["chat_id"], a["message_id"]) for n, a in bot.calls if n == "delete_message"]
    assert deleted == [(OWNER, 77), (GUEST, 10), (GUEST, 50)]
    assert anon_env["forgotten"] == [(OWNER, [77]), (GUEST, [10])]
    marker = [a for n, a in bot.calls if n == "send_message"][0]
    assert marker["chat_id"] == OWNER and "deleted by its sender" in marker["text"]
    assert marker["disable_notification"] and marker["reply_markup"].inline_keyboard[0][1].text == "\U0001f6ab Block"
    assert anon_env["recorded"] == [(OWNER, [555], 3)] and anon_env["shown"] == ["🗑 Deleted on both sides."]


def test_delete_refuses_what_it_should(anon_env, monkeypatch):
    monkeypatch.setattr(anon.family_link, "get_setting", lambda key, default=None: "admins")
    update = _delete_update(GUEST, reply=False)
    run(anon.delete_command(update, SimpleNamespace(bot=_AnonBot())))
    assert update.message.replies == [anon.i18n.t("en", "delete_how")]

    update = _delete_update(GUEST, target_from=12345)
    run(anon.delete_command(update, SimpleNamespace(bot=_AnonBot())))
    assert update.message.replies == [anon.i18n.t("en", "delete_only_own")]

    bot = _AnonBot(fail={"delete_message": anon.BadRequest("Message can't be deleted for everyone")})
    update = _delete_update(GUEST)
    run(anon.delete_command(update, SimpleNamespace(bot=bot)))
    assert update.message.replies == [anon.i18n.t("en", "delete_too_old")] and anon_env["forgotten"] == []

    anon_env["route"] = _route(blocked=True)
    update = _delete_update(GUEST)
    run(anon.delete_command(update, SimpleNamespace(bot=_AnonBot())))
    assert update.message.replies == [anon.i18n.t("en", "delete_failed")]


def test_delete_does_not_exist_for_those_without_it(anon_env, monkeypatch):
    monkeypatch.setattr(anon.family_link, "get_setting", lambda key, default=None: "admins")
    run(anon.delete_command(_delete_update(OWNER), SimpleNamespace(bot=_AnonBot())))
    assert anon_env["shown"] == ["unknown command"]


def test_the_menus_follow_the_switch():
    public, admin = anon._menus("admins")
    assert "delete" not in [c.command for c in public] and "delete" in [c.command for c in admin]
    public, admin = anon._menus("everyone")
    assert "delete" in [c.command for c in public] and "delete" not in [c.command for c in admin]
    assert "editing" in [c.command for c in anon._menus("off")[1]]


def test_the_edit_texts_are_complete():
    for lang in ("en", "uz", "ru"):
        for key in ("edit_relayed", "edit_not_relayed_parts", "delete_how", "delete_only_own",
                    "delete_not_relayed", "delete_too_old", "delete_failed", "deleted_both",
                    "delete_marker", "help_edits"):
            assert anon.i18n.t(lang, key) and "{" not in anon.i18n.t(lang, key), (lang, key)
        if lang != "en":
            assert "delete" in anon.i18n.COMMAND_MENU[lang]


def test_help_mentions_edits_only_once_they_are_for_everyone():
    anon._edits_cache.update(mode="admins")
    assert "/delete as a reply" not in anon.build_help_text("en", full=True)
    anon._edits_cache.update(mode="everyone")
    assert "/delete as a reply" in anon.build_help_text("en", full=True)
    anon._edits_cache.update(mode="admins", at=None)



def test_the_route_names_every_twin_in_the_other_chat_only():
    conv = anon_db.start_new_anon_conversation(OWNER, GUEST)
    anon_db.record_relay_and_touch(conv["id"], OWNER, [200, 201], GUEST, [10], 201, 10)
    anon_db.record_relay_and_touch(conv["id"], OWNER, [202], GUEST, [11], 202, 11)
    route = anon_db.get_edit_route(GUEST, 10)
    assert route["twins"] == [200, 201] and route["owner_user_id"] == OWNER
    assert route["follower_user_id"] == GUEST and not route["blocked"]
    assert anon_db.get_edit_route(OWNER, 202)["twins"] == [11]
    assert anon_db.get_edit_route(GUEST, 999) is None

    anon_db.set_anon_blocked(OWNER, GUEST, True)
    assert anon_db.get_edit_route(GUEST, 10)["blocked"]

    anon_db.forget_anon_relays(OWNER, [200, 201])
    assert anon_db.get_edit_route(GUEST, 10)["twins"] == []
