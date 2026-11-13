"""StickerBot since 2.1.6 (MumuCombined), in 2.2.0: the pack buttons under the message box, pictures
sent with no pack open held for a pack choice, a clip that does not fit offered speeds or a lighter
encode instead of being cut short, a session whose state is gone, and buttons within Telegram's 64
bytes. Telegram and the database are stand-ins; the clips are real (ffmpeg)."""
import asyncio
import json
import os
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from telegram import Animation, CallbackQuery, Chat, Document, Message, PhotoSize, Update, User
from telegram.error import NetworkError, TelegramError
from telegram.ext import ConversationHandler

from conftest import load

bot = load("sticker_bot", "bot")
vs = load("sticker_bot", "video_sticker")

CHAT = 10
PACKS = (("memes_by_bot", "Memes"), ("cats_by_bot", "Cats"), ("cats2_by_bot", "Cats"))


# ── Telegram, and the person ────────────────────────────────────────────────────────────────────

class FakeBot:
    def __init__(self, titles=None):
        self.sent, self.edits, self.retagged = [], [], []
        self._next = 1000
        self.titles = dict(titles or {})  # what Telegram says the packs are called

    async def send_message(self, chat_id, text, reply_markup=None, reply_parameters=None, **_):
        self._next += 1
        msg = Message(message_id=self._next, date=datetime.now(timezone.utc), chat=Chat(id=chat_id, type="private"),
                      text=text, from_user=User(id=1, first_name="Bot", is_bot=True))
        self.sent.append({"id": msg.message_id, "text": text, "markup": reply_markup,
                          "reply_to": getattr(reply_parameters, "message_id", None)})
        return msg

    async def edit_message_text(self, text=None, chat_id=None, message_id=None, reply_markup=None, **_):
        self.edits.append({"id": message_id, "text": text, "markup": reply_markup})
        return True

    async def answer_callback_query(self, *_a, **_k):
        return True

    async def set_sticker_emoji_list(self, sticker, emoji_list):
        self.retagged.append((sticker, list(emoji_list)))

    async def get_sticker_set(self, name, **_):
        if name in self.titles:
            return SimpleNamespace(title=self.titles[name], stickers=[SimpleNamespace(file_id="s", file_unique_id="u")])
        raise TelegramError("no network in tests")


class Context:
    def __init__(self, fake, **data):
        self.bot = fake
        self.user_data = {"_last_seen": time.time(), **data}
        self._chat_id = CHAT
        self.application = SimpleNamespace(update_queue=asyncio.Queue(), update_processor=None)


def editing(fake, title="Memes", pack="memes_by_bot"):
    return Context(fake, owner_id=CHAT, title=title, pack_created=True, target_pack=pack, sticker_count=0,
                   lang="en")


_ids = iter(range(1, 100000))


def _user():
    return User(id=CHAT, first_name="T", is_bot=False)


def message(fake, **content) -> Message:
    msg = Message(message_id=next(_ids), date=datetime.now(timezone.utc), chat=Chat(id=CHAT, type="private"),
                  from_user=_user(), **content)
    msg.set_bot(fake)
    return msg


def update_of(msg) -> Update:
    update = Update(update_id=next(_ids), message=msg)
    update.set_bot(msg.get_bot())
    return update


def gif(fake):
    anim = Animation(file_id="a", file_unique_id="u", width=480, height=854, duration=6, mime_type="video/mp4")
    return update_of(message(fake, animation=anim,
                             document=Document(file_id="a", file_unique_id="u", mime_type="video/mp4")))


def photo(fake, file_id="p"):
    return update_of(message(fake, photo=[PhotoSize(file_id=file_id, file_unique_id=file_id + "u", width=9, height=9)]))


def text(fake, words, reply_to=None):
    return update_of(message(fake, text=words, reply_to_message=reply_to))


def tap(fake, data: str, on_id: int = 50) -> Update:
    on = Message(message_id=on_id, date=datetime.now(timezone.utc), chat=Chat(id=CHAT, type="private"), text="x",
                 from_user=User(id=1, first_name="Bot", is_bot=True))
    on.set_bot(fake)
    query = CallbackQuery(id="q", from_user=_user(), chat_instance="c", data=data, message=on)
    query.set_bot(fake)
    update = Update(update_id=next(_ids), callback_query=query)
    update.set_bot(fake)
    return update


def run(coro):
    return asyncio.run(coro)


@pytest.fixture()
def packs(monkeypatch):
    """No database, no network: the packs above, every download gives b"raw", every add succeeds
    (as #11, #12, ...: an existing pack; a new one is named as Telegram would). What was added, in
    order; `.raw` is what a download gives."""
    class Added(list):
        raw = b"raw"
    added = Added()
    store = [list(pack) for pack in PACKS]  # the bot's own record; renames land here

    async def english(*_a, **_k):
        return "en"

    async def nothing(*_a, **_k):
        return None

    async def fetched(*_a, **_k):
        return added.raw

    async def static(raw):
        return SimpleNamespace(format="static")

    async def add(context, user, sticker, *a, **k):
        if not context.user_data.get("pack_created"):
            context.user_data.update(target_pack="new_by_bot", pack_created=True)
        added.append(sticker)
        return f"sticker{len(added)}", len(added) + 10

    def set_title(name, title):
        for pack in store:
            if pack[0] == name:
                pack[1] = title

    def set_titles(changed):
        for name, title in changed.items():
            set_title(name, title)

    def access(name, user_id, *a):
        return ({"allowed": True, "owner_id": CHAT, "can_delete": True, "title": dict(map(tuple, store))[name]}
                if name in dict(map(tuple, store)) else None)

    monkeypatch.setattr(bot.i18n, "get_lang", english)
    monkeypatch.setattr(bot.sticker_ops, "download", fetched)
    monkeypatch.setattr(bot.sticker_ops, "static_sticker", static)
    monkeypatch.setattr(bot, "refuse_new_work", nothing)
    monkeypatch.setattr(bot, "record_error", lambda exc: None)
    monkeypatch.setattr(bot, "_add_input_sticker", add)
    monkeypatch.setattr(bot, "maybe_donation_nudge", nothing)
    monkeypatch.setattr(bot, "get_user_packs", lambda user_id: [tuple(pack) for pack in store])
    monkeypatch.setattr(bot, "shared_packs", lambda user_id: [])
    monkeypatch.setattr(bot, "get_pack_owner", lambda name: CHAT if name in dict(map(tuple, store)) else None)
    monkeypatch.setattr(bot, "get_pack_title", lambda name: dict(map(tuple, store)).get(name))
    monkeypatch.setattr(bot, "set_pack_title", set_title)
    monkeypatch.setattr(bot, "set_pack_titles", set_titles)
    monkeypatch.setattr(bot, "pack_access", access)
    monkeypatch.setattr(bot, "is_pack_public", lambda name: False)
    bot.sticker_ops._titles.clear()
    return added


def labels(markup):
    return [button.text for row in markup.keyboard for button in row] if hasattr(markup, "keyboard") else None


def answers_to_items(fake):
    return [s for s in fake.sent if s["text"].startswith("✅")]


# ── the pack buttons ────────────────────────────────────────────────────────────────────────────

def test_two_buttons_per_pack_and_a_repeated_title_is_numbered_oldest_first():
    kb = bot.pack_reply_keyboard(PACKS, "en")
    assert [[b.text for b in row] for row in kb.keyboard] == [
        ["✏️ Edit Memes", "👁 View Memes"],
        ["✏️ Edit Cats · 2", "👁 View Cats · 2"],
        ["✏️ Edit Cats", "👁 View Cats"],
        ["➕ New pack"],
    ]
    assert kb.is_persistent and kb.resize_keyboard
    newer = (("cats3_by_bot", "Cats"),) + PACKS  # a newer pack does not renumber the older ones
    names = {name: label for label, name in bot._pack_labels(newer)}
    assert names["cats2_by_bot"] == "Cats" and names["cats_by_bot"] == "Cats · 2"


def test_buttons_are_understood_in_every_language():
    assert bot._pack_button_action("✏️ Edit Cats · 2", PACKS) == ("edit", "cats_by_bot")
    assert bot._pack_button_action("👁 View Memes", PACKS) == ("view", "memes_by_bot")
    assert bot._pack_button_action("✏️ Изменить «Memes»", PACKS) == ("edit", "memes_by_bot")
    assert bot._pack_button_action("👁 Ko'rish: Cats", PACKS) == ("view", "cats2_by_bot")
    assert bot._pack_button_action("✏ Edit Memes", PACKS) == ("edit", "memes_by_bot")  # no variation selector
    assert bot._pack_button_action("✏️ Edit Dogs", PACKS) is None


def test_only_button_shaped_text_is_taken_for_a_button():
    looks = bot.PACK_BUTTON_TEXT.filter
    assert looks(SimpleNamespace(text="✏️ Edit Memes")) and looks(SimpleNamespace(text="👁 Открыть «Cats»"))
    for words in ("👁", "✏️", "✏️ Edit", "👁‍🗨 hello", "hello"):
        assert not looks(SimpleNamespace(text=words)), words


def test_a_pack_button_opens_that_pack(packs):
    fake = FakeBot()
    ctx = Context(fake, lang="en")
    assert run(bot.pack_button(text(fake, "✏️ Edit Cats · 2"), ctx)) == bot.EDITING
    assert ctx.user_data["target_pack"] == "cats_by_bot"
    status = fake.sent[-1]
    assert status["text"].startswith('📝 Editing "Cats"') and "/done" not in status["text"]
    assert labels(status["markup"])[:2] == ["✏️ Edit Memes", "👁 View Memes"]  # the buttons, up to date


def test_view_shows_the_pack_and_leaves_the_session_alone(packs):
    fake = FakeBot()
    ctx = editing(fake)
    assert run(bot.pack_button(text(fake, "👁 View Cats"), ctx)) is None
    assert fake.sent[-1]["text"] == "👁 “Cats”"
    assert fake.sent[-1]["markup"].inline_keyboard[0][0].url == "https://t.me/addstickers/cats2_by_bot"
    assert ctx.user_data["target_pack"] == "memes_by_bot"


def test_a_button_for_a_pack_that_is_gone(packs):
    fake = FakeBot()
    ctx = Context(fake, lang="en")
    assert run(bot.pack_button(text(fake, "✏️ Edit Deleted"), ctx)) is None
    assert fake.sent[-1]["text"] == bot.i18n.t("en", "pack_button_gone")
    assert labels(fake.sent[-1]["markup"])[0] == "✏️ Edit Memes"


def test_each_sticker_is_answered_as_a_reply_to_itself_with_the_buttons(packs):
    fake = FakeBot()
    ctx = editing(fake)
    first, second = photo(fake, "a"), photo(fake, "b")
    run(bot.receive_media(first, ctx))
    run(bot.receive_media(second, ctx))
    answers = answers_to_items(fake)
    assert [a["reply_to"] for a in answers] == [first.message.message_id, second.message.message_id]
    assert answers[1]["text"] == "✅ Added to “Memes” as #12 with 😭. Reply to this with an emoji to change it."
    assert labels(answers[1]["markup"])[0] == "✏️ Edit Memes"
    # A reply to either the picture or the answer stands for its sticker (sticker_ops), for retagging.
    assert bot.sticker_ops.sticker_of(SimpleNamespace(chat_id=CHAT, message_id=answers[0]["id"], sticker=None)) \
        == ("memes_by_bot", "sticker1")
    json.dumps(ctx.user_data)  # persistence can save the session


def test_reopening_the_open_pack_keeps_its_last_sticker(packs):
    """TestBot, 2026-11-13: the pack's button tapped mid-session, then plain emoji -> "Add a sticker first"."""
    fake = FakeBot()
    ctx = editing(fake)
    run(bot.receive_media(photo(fake), ctx))
    run(bot.pack_button(text(fake, "✏️ Edit Memes"), ctx))
    run(bot.maybe_emoji_override(text(fake, "🐶"), ctx))
    assert fake.retagged == [("sticker1", ["🐶"])]
    run(bot.pack_button(text(fake, "✏️ Edit Cats"), ctx))  # another pack: nothing to retag by plain emoji
    run(bot.maybe_emoji_override(text(fake, "🐱"), ctx))
    assert fake.sent[-1]["text"] == bot.i18n.t("en", "no_sticker_to_tag")


# ── held: sent with no pack open ────────────────────────────────────────────────────────────────

def test_pictures_with_no_pack_open_are_held_and_asked_about_once(packs):
    fake = FakeBot()
    ctx = Context(fake, lang="en")
    album = [photo(fake, f"p{i}") for i in range(3)]
    for item in album:
        run(bot.media_without_pack(item, ctx))
    questions = [s for s in fake.sent if "Which pack" in s["text"]]
    assert len(questions) == 1 and questions[0]["reply_to"] == album[0].message.message_id
    assert fake.edits[-1]["text"].startswith("Which pack should 3 item(s)")
    data = [b.callback_data for row in fake.edits[-1]["markup"].inline_keyboard for b in row]
    assert data[:3] == [f"hp:{bot.pack_ref(name)}" for name, _ in PACKS] and "hp-" in data
    json.dumps(ctx.user_data)

    state = run(bot.held_pack_chosen(tap(fake, f"hp:{bot.pack_ref('cats_by_bot')}"), ctx))
    assert state == bot.EDITING and len(packs) == 3 and bot.HELD_KEY not in ctx.user_data
    answers = [s for s in fake.sent if s["text"].startswith("✅ Added to “Cats”")]
    assert [a["reply_to"] for a in answers] == [m.message.message_id for m in album]


def test_held_too_long_is_not_added(packs):
    fake = FakeBot()
    ctx = Context(fake, lang="en")
    run(bot.media_without_pack(photo(fake), ctx))
    ctx.user_data[bot.HELD_KEY][0]["at"] = time.time() - bot.HOLD_SECONDS - 1
    run(bot.held_pack_chosen(tap(fake, "hp:memes_by_bot"), ctx))  # a full name still works
    assert not packs and fake.sent[-1]["text"] == bot.i18n.t("en", "held_expired")


def test_held_items_go_into_a_new_pack_once_it_is_named(packs):
    fake = FakeBot()
    ctx = Context(fake, lang="en")
    run(bot.media_without_pack(photo(fake), ctx))
    assert run(bot.held_new_pack(tap(fake, "hp+"), ctx)) == bot.TITLE
    assert bot.HELD_KEY in ctx.user_data
    assert run(bot.receive_title(text(fake, "Dogs"), ctx)) == bot.EDITING
    assert len(packs) == 1 and "✅ Added to “Dogs”" in fake.sent[-1]["text"]


def test_discard(packs, monkeypatch):
    fake = FakeBot()
    ctx = Context(fake, lang="en")
    run(bot.media_without_pack(photo(fake), ctx))
    run(bot.held_discard(tap(fake, "hp-"), ctx))
    assert bot.HELD_KEY not in ctx.user_data and not packs
    assert (fake.sent + fake.edits)[-1]["text"] == bot.i18n.t("en", "held_discarded")


def test_past_the_limit_an_item_is_refused_with_a_word(packs, monkeypatch):
    monkeypatch.setattr(bot, "HOLD_MAX", 2)
    fake = FakeBot()
    ctx = Context(fake, lang="en")
    for name in ("a", "b", "c"):
        run(bot.media_without_pack(photo(fake, name), ctx))
    assert len(ctx.user_data[bot.HELD_KEY]) == 2
    assert fake.sent[-1]["text"] == bot.i18n.t("en", "held_full", n=2)


def test_a_held_sticker_already_in_the_pack_is_left_alone(packs, monkeypatch):
    """2.1.6's review: a held sticker was deleted from the pack picked for it."""
    async def in_it(*_a, **_k):
        return True
    monkeypatch.setattr(bot.sticker_ops, "in_pack", in_it)
    fake = FakeBot()
    ctx = Context(fake, lang="en")
    ctx.user_data[bot.HELD_KEY] = [{"chat": CHAT, "msg": 5, "kind": "static", "file_id": "s", "set": "memes_by_bot",
                                    "uid": "u", "at": time.time()}]
    run(bot.held_pack_chosen(tap(fake, f"hp:{bot.pack_ref('memes_by_bot')}"), ctx))
    assert not packs and fake.sent[-1]["text"] == bot.i18n.t("en", "held_already_in_pack", title="Memes")


def test_a_held_item_that_fails_is_answered_and_the_rest_still_go_in(packs, monkeypatch):
    fake = FakeBot()
    ctx = Context(fake, lang="en")
    for name in ("good1", "bad", "good2"):
        run(bot.media_without_pack(photo(fake, name), ctx))
    real = bot._add_static_item

    async def picky(update, context, lang, item):
        if item["file_id"] == "bad":
            raise RuntimeError("Telegram hiccup")
        return await real(update, context, lang, item)
    monkeypatch.setattr(bot, "_add_static_item", picky)
    run(bot.held_pack_chosen(tap(fake, f"hp:{bot.pack_ref('memes_by_bot')}"), ctx))
    texts = [s["text"] for s in fake.sent]
    assert len(packs) == 2 and any("Not added" in t for t in texts)


def test_a_dropped_session_holds_the_picture_instead_of_failing(packs):
    """The conversation still in EDITING, its state pruned: a picture is held and asked about."""
    fake = FakeBot()
    ctx = Context(fake, lang="en")  # no owner_id: pruned
    assert run(bot._needs_session(bot.receive_media)(photo(fake), ctx)) == ConversationHandler.END
    assert len(ctx.user_data[bot.HELD_KEY]) == 1 and "Which pack" in fake.sent[-1]["text"]
    assert run(bot._needs_session(bot.maybe_emoji_override)(text(fake, "😀"), ctx)) == ConversationHandler.END
    assert fake.sent[-1]["text"] == bot.i18n.t("en", "session_lost")


def test_a_new_session_keeps_what_has_to_outlive_one():
    ctx = SimpleNamespace(user_data={"lang": "uz", "_last_seen": 5, bot.HELD_KEY: [1], bot.KB_TITLES: {"a": "A"},
                                     "target_pack": "x", "status": {}, "rename_pack": "y"})
    bot._fresh_session(ctx, {"mode": "new"})
    assert ctx.user_data == {"lang": "uz", "_last_seen": 5, bot.HELD_KEY: [1], bot.KB_TITLES: {"a": "A"},
                             "mode": "new"}


# ── the status message ──────────────────────────────────────────────────────────────────────────

def test_the_editing_line_is_sent_again_when_no_connection_was_made(packs, monkeypatch):
    """TestBot, 2026-11-13: a pack tap met "httpx.ConnectError" and the person saw nothing."""
    real_sleep = asyncio.sleep
    monkeypatch.setattr(bot.asyncio, "sleep", lambda seconds: real_sleep(0))
    fake = FakeBot()
    real_send, failures = fake.send_message, [NetworkError("httpx.ConnectError: ")]

    async def flaky(chat_id, text, **kwargs):
        if failures:
            raise failures.pop()
        return await real_send(chat_id, text, **kwargs)
    fake.send_message = flaky
    ctx = Context(fake, lang="en")
    assert run(bot.pack_button(text(fake, "✏️ Edit Memes"), ctx)) == bot.EDITING
    assert fake.sent[-1]["text"].startswith('📝 Editing "Memes"') and bot.STATUS_KEY in ctx.user_data


def test_a_pack_that_cannot_be_opened_reaches_the_error_handler_instead_of_silence(packs):
    """"It has left me on seen": a failure that is not a lost connection is no longer swallowed."""
    from telegram.error import BadRequest
    fake = FakeBot()

    async def refused(chat_id, text, **kwargs):
        raise BadRequest("Bad Request: something Telegram did not like")
    fake.send_message = refused
    ctx = Context(fake, lang="en")
    with pytest.raises(BadRequest):  # error_handler tells the person (FM-CRASH)
        run(bot.pack_button(text(fake, "✏️ Edit Memes"), ctx))


def test_cancel_while_editing_says_it_once(packs):
    fake = FakeBot()
    ctx = editing(fake)
    ctx.user_data[bot.STATUS_KEY] = {"chat_id": CHAT, "message_id": 5, "watermark": 0}
    before = len(fake.sent) + len(fake.edits)
    stopped, pack_stopped = run(bot._apply_cancel(text(fake, "/cancel"), ctx, "en", ["editing"]))
    assert pack_stopped and len(fake.sent) + len(fake.edits) == before  # finish_cancel's line is the only one


# ── renamed in Telegram; the buttons come back ──────────────────────────────────────────────────

def test_a_pack_renamed_in_telegram_is_called_by_its_new_name(packs):
    """TestBot, 2026-11-13: "testpack1" renamed "TestNameChange" in Telegram; the bot kept the old name."""
    fake = FakeBot(titles={"memes_by_bot": "Renamed"})
    ctx = Context(fake, lang="en")
    ctx.user_data[bot.KB_TITLES] = {"memes_by_bot": "Memes"}  # the buttons on screen say Memes
    assert run(bot.pack_button(text(fake, "✏️ Edit Memes"), ctx)) == bot.EDITING
    status = fake.sent[-1]
    assert status["text"].startswith('📝 Editing "Renamed"')
    assert labels(status["markup"])[:2] == ["✏️ Edit Renamed", "👁 View Renamed"]
    run(bot.receive_media(photo(fake), ctx))
    assert "“Renamed”" in answers_to_items(fake)[-1]["text"]


def test_view_says_the_new_name(packs):
    fake = FakeBot(titles={"cats2_by_bot": "Kittens"})
    ctx = Context(fake, lang="en")
    ctx.user_data[bot.KB_TITLES] = {"cats2_by_bot": "Cats"}
    run(bot.pack_button(text(fake, "👁 View Cats"), ctx))
    assert fake.sent[-1]["text"] == "👁 “Kittens”"


def test_a_sticker_added_brings_the_name_telegram_has(packs):
    """The pack is read back after every sticker anyway (sticker_ops.last_sticker); its title comes along."""
    fake = FakeBot()
    ctx = editing(fake)
    bot.sticker_ops._note_title("memes_by_bot", "Renamed")
    run(bot.receive_media(photo(fake), ctx))
    assert ctx.user_data["title"] == "Renamed" and "“Renamed”" in answers_to_items(fake)[-1]["text"]
    assert dict(bot.get_user_packs(CHAT))["memes_by_bot"] == "Renamed"


def test_the_buttons_come_back_with_plain_answers(packs):
    """TestBot, 2026-11-13: /cancel's answer took the pack buttons away for good."""
    fake = FakeBot()
    ctx = Context(fake, lang="en")
    run(bot.unrecognized_message(text(fake, "j"), ctx))
    assert labels(fake.sent[-1]["markup"])[0] == "✏️ Edit Memes"
    run(bot.cancel_command(text(fake, "/cancel"), ctx))  # nothing pending: "Nothing to cancel"
    assert fake.sent[-1]["text"] == bot.i18n.t("en", "cancel_nothing")
    assert labels(fake.sent[-1]["markup"])[0] == "✏️ Edit Memes"  # not ReplyKeyboardRemove
    run(bot.unrecognized_message(text(fake, "😀"), ctx))  # emoji with no pack open: how to retag
    assert fake.sent[-1]["text"] == bot.i18n.t("en", "no_sticker_to_tag")


def test_done_is_off_the_menus_and_out_of_the_help():
    assert "done" not in [command.command for command in bot.BOT_COMMANDS]
    for lang in ("uz", "ru"):
        assert "done" not in bot.i18n.COMMAND_MENU[lang]
    for lang in ("en", "uz", "ru"):
        assert "/done" not in bot.i18n.t(lang, "help_text") and "/done" not in bot.i18n.t(lang, "help_brief")
        assert "✏️" in bot.i18n.t(lang, "help_text")


def test_every_language_has_the_new_words():
    keys = ("btn_kb_edit", "btn_kb_view", "pack_view", "packs_keyboard_note", "pack_button_gone", "held_offer",
            "btn_held_discard", "held_expired", "held_discarded", "held_full", "held_already_in_pack",
            "video_too_long", "video_too_long_no_speed", "video_too_heavy", "video_too_heavy_last",
            "btn_speed_fit", "btn_speed", "btn_lower_res", "btn_lower_fps", "btn_skip_clip", "video_fit_expired",
            "video_fit_skipped", "fit_lower_res", "fit_lower_fps", "session_lost", "sticker_added_to_fit",
            "sticker_added_unnumbered_fit")
    for lang in ("en", "uz", "ru"):
        for key in keys:
            assert bot.i18n.t(lang, key, title="T", emoji="😭", how="2×", n=2, minutes=10, seconds="6.4",
                              limit=3, speed="2", longest="12", kb=256, fps=15) != key, (lang, key)


# ── within Telegram's 64 bytes ──────────────────────────────────────────────────────────────────

LONG = "a" * 30 + "_0123456789_by_Personal_Sticker_Creator_bot"  # longer than any real pack name


def _data(markup):
    return [b.callback_data for row in markup.inline_keyboard for b in row if b.callback_data]


def test_every_pack_button_fits_64_bytes_even_for_the_longest_name(packs, monkeypatch):
    """TestBot, 2026-11-14: Co-edit and Delete did nothing -- "packdelconfirm1:<name>" was over 64
    bytes, and Telegram refused the message carrying it."""
    long_packs = [(LONG, "A long one")] + list(PACKS)
    monkeypatch.setattr(bot, "get_user_packs", lambda user_id: long_packs)
    monkeypatch.setattr(bot, "shared_packs", lambda user_id: [(LONG + "x", "Shared")])
    monkeypatch.setattr(bot, "get_pack_owner", lambda name: CHAT)
    monkeypatch.setattr(bot, "get_pack_title", lambda name: "A long one")
    markups = [bot.packs_keyboard(long_packs, "en", [(LONG + "x", "Shared")]), bot.pack_detail_keyboard(LONG, "en")]
    fake = FakeBot()
    ctx = Context(fake, lang="en")
    ref = bot.pack_ref(LONG)
    for data, handler in ((f"packdel:{ref}", bot.delete_pack_start), (f"packdelconfirm1:{ref}", bot.delete_pack_confirm1)):
        before = len(fake.sent) + len(fake.edits)
        run(handler(tap(fake, data), ctx))
        shown = (fake.sent + fake.edits)[before:]
        assert shown, f"{data}: nothing shown"
        markups += [s["markup"] for s in shown if s["markup"] is not None]
    run(bot.media_without_pack(photo(fake), ctx))
    markups.append(fake.sent[-1]["markup"])
    for markup in markups:
        for data in _data(markup):
            assert len(data.encode()) <= 64, data


def test_a_button_drawn_with_the_full_name_still_works(packs):
    assert run(bot._pack_of(CHAT, "cats_by_bot")) == "cats_by_bot"
    assert run(bot._pack_of(CHAT, bot.pack_ref("cats_by_bot"))) == "cats_by_bot"
    assert run(bot._pack_of(CHAT, bot.pack_ref("someone_elses_by_bot"))) is None
    fake = FakeBot()
    run(bot.pack_detail(tap(fake, "packopen:memes_by_bot"), Context(fake, lang="en")))
    assert (fake.sent + fake.edits)[-1]["text"]


# ── a clip that does not fit ────────────────────────────────────────────────────────────────────

def clip(seconds: float, noisy: bool = False, size: str = "480x854") -> bytes:
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "c.gif" if not noisy else "c.mp4")
        source = (f"nullsrc=s={size}:d={seconds},geq=random(1)*255:128:128" if noisy
                  else f"testsrc=s={size}:d={seconds}:r=15")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", source, "-r", "30", out], check=True)
        return Path(out).read_bytes()


def probe(webm: bytes) -> dict:
    with tempfile.NamedTemporaryFile(suffix=".webm") as f:
        f.write(webm)
        f.flush()
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                              "format=duration:stream=width,height,codec_name", "-of", "json", f.name],
                             capture_output=True, text=True).stdout
    data = json.loads(out)
    stream = data["streams"][0]
    return {"duration": float(data["format"]["duration"]), "w": stream["width"], "h": stream["height"],
            "codec": stream["codec_name"], "bytes": len(webm)}


def test_speeds():
    assert vs.speed_choices(3.0) == [] and vs.speed_choices(2.0) == []  # fits already
    assert vs.speed_choices(3.2) == [1.1, 1.5, 2.0]  # the least change first
    assert vs.speed_choices(6.4) == [2.2, 3.0, 4.0]
    assert vs.speed_choices(6.0) == [2.0, 3.0, 4.0]
    assert vs.speed_choices(4.4) == [1.5, 2.0, 3.0]  # 1.4666 -> 1.5, and 1.5 is not offered twice
    assert vs.speed_choices(11.9) == [4.0]
    assert vs.speed_choices(57.0) == []  # never 19x
    for d in (3.1, 4.4, 6.4, 7.77, 11.9):
        for speed in vs.speed_choices(d):
            assert d / speed <= vs.MAX_DURATION_S + 1e-9 and speed <= vs.MAX_SPEED


def test_a_long_gif_is_measured_and_sped_up_whole():
    raw = clip(6.4)
    assert vs.clip_duration(raw) == pytest.approx(6.4, abs=0.15)
    out = probe(vs.to_video_sticker_webm(raw, "en", speed=vs.fit_speed(vs.clip_duration(raw))))
    assert out["codec"] == "vp9" and max(out["w"], out["h"]) == 512 and out["bytes"] <= vs.MAX_BYTES
    assert 2.7 <= out["duration"] <= 3.05


def test_too_heavy_is_its_own_error():
    raw = clip(3, noisy=True, size="512x512")
    with pytest.raises(vs.TooBig):
        vs.to_video_sticker_webm(raw, "en")


def test_a_long_gif_is_offered_speeds_and_a_tap_adds_it(packs):
    packs.raw = clip(6.4)
    fake = FakeBot()
    ctx = editing(fake)
    update = gif(fake)
    run(bot.receive_video_media(update, ctx))
    assert not packs, "it was added cut short, as before"
    offer = fake.sent[-1]
    assert offer["reply_to"] == update.message.message_id and "6.4 s" in offer["text"] and "3 s" in offer["text"]
    data = _data(offer["markup"])
    assert [d.rsplit(":", 1)[1] for d in data] == ["s2.2", "s3", "s4", "x"]
    assert all(len(d.encode()) <= 64 for d in data)
    json.dumps(ctx.user_data)
    assert ctx.user_data["batch_failed"] == 0  # waiting is not failing

    run(bot.video_fit_chosen(tap(fake, data[0], on_id=offer["id"]), ctx))
    assert len(packs) == 1 and ctx.user_data["sticker_count"] == 1
    sticker = packs[0].sticker
    assert probe(getattr(sticker, "input_file_content", sticker))["duration"] <= 3.05
    assert fake.edits[-1]["id"] == offer["id"]
    assert fake.edits[-1]["text"] == bot.i18n.t("en", "sticker_added_to_fit", title="Memes", n=11, emoji="😭",
                                                how="2.2×")
    run(bot.video_fit_chosen(tap(fake, data[1], on_id=offer["id"]), ctx))  # the same offer again
    assert len(packs) == 1


def test_skip(packs):
    packs.raw = clip(6.4)
    fake = FakeBot()
    ctx = editing(fake)
    run(bot.receive_video_media(gif(fake), ctx))
    offer = fake.sent[-1]
    run(bot.video_fit_chosen(tap(fake, _data(offer["markup"])[-1], on_id=offer["id"]), ctx))
    assert not packs and fake.edits[-1]["text"] == bot.i18n.t("en", "video_fit_skipped")


def test_too_heavy_offers_resolution_then_frame_rate_then_trim(packs, monkeypatch):
    packs.raw = clip(2.5)
    calls = []

    async def heavy(raw, lang, speed=1.0, low_detail=False, low_fps=False):
        calls.append((speed, low_detail, low_fps))
        raise vs.TooBig("too big")
    monkeypatch.setattr(bot.sticker_ops, "video_sticker", heavy)
    fake = FakeBot()
    ctx = editing(fake)
    run(bot.receive_video_media(gif(fake), ctx))
    converting = fake.sent[-1]
    assert converting["text"] == bot.i18n.t("en", "converting_video") and converting["markup"] is None
    offer = fake.edits[-1]  # the same answer, now the choices
    assert offer["id"] == converting["id"]
    assert [d.rsplit(":", 1)[1] for d in _data(offer["markup"])] == ["res", "fps", "x"]
    assert "Lower resolution (recommended)" in offer["markup"].inline_keyboard[0][0].text
    run(bot.video_fit_chosen(tap(fake, _data(offer["markup"])[0], on_id=offer["id"]), ctx))
    assert calls[-1] == (1.0, True, False)
    data = _data(fake.edits[-1]["markup"])
    assert [d.rsplit(":", 1)[1] for d in data] == ["fps", "x"]
    run(bot.video_fit_chosen(tap(fake, data[0], on_id=offer["id"]), ctx))
    assert calls[-1] == (1.0, True, True)
    assert fake.edits[-1]["text"] == bot.i18n.t("en", "video_too_heavy_last", kb=256)
    assert fake.edits[-1]["markup"] is None and not packs


def test_a_short_gif_goes_straight_in(packs):
    packs.raw = clip(2.0)
    fake = FakeBot()
    ctx = editing(fake)
    update = gif(fake)
    run(bot.receive_video_media(update, ctx))
    assert len(packs) == 1
    answer = fake.sent[-1]
    assert answer["reply_to"] == update.message.message_id and fake.edits[-1]["id"] == answer["id"]
    assert fake.edits[-1]["text"] == bot.i18n.t("en", "sticker_added_to", title="Memes", n=11, emoji="😭")


def test_a_choice_whose_clip_is_gone_is_answered_and_adds_nothing(packs):
    fake = FakeBot()
    assert run(bot.video_fit_chosen(tap(fake, "vfit:abcd:s2"), editing(fake))) == bot.EDITING  # no such offer
    run(bot.video_fit_stale(tap(fake, "vfit:abcd:s2"), Context(fake, lang="en")))  # after the session
    assert not packs
