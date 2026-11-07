"""DownloaderBot in channels (the owner, 2026-11-07): a post that is text with a link gets the
media put into it, or posted anew when it cannot hold it; the settings live in a private chat."""
import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from telegram import (Chat, InputMediaDocument, InputMediaPhoto, InputMediaVideo, Message,
                      MessageEntity, Update)
from telegram.error import Forbidden
from telegram.ext import ApplicationHandlerStop

from conftest import load

DOWNLOADER = "downloader_bot"
CHANNEL = -1001234567890
LINK = "https://www.instagram.com/p/ABC123/"
USERNAME = "mumu_downloader_bot"


def channel_post(text=LINK, message_id=7, entities=None, **more) -> Message:
    chat = Chat(id=CHANNEL, type="channel", title="Memes")
    if entities is None and text:
        entities = [MessageEntity(MessageEntity.URL, 0, len(text))] if text.startswith("http") else []
    return Message(message_id=message_id, date=datetime.now(timezone.utc), chat=chat, sender_chat=chat,
                   text=text, entities=entities, **more)


class FakeBot:
    """The calls the channel code makes, recorded; rights and admins as the test sets them."""

    def __init__(self, edit=True, post=True, delete=True, admins=(), forbidden=False):
        self.id = 999
        self.rights = {"can_edit_messages": edit, "can_post_messages": post, "can_delete_messages": delete}
        self.admins, self.forbidden, self.calls = set(admins), forbidden, []

    async def get_chat_member(self, chat_id, user_id):
        if user_id == self.id:
            return SimpleNamespace(status="administrator", **self.rights)
        return SimpleNamespace(status="administrator" if user_id in self.admins else "member")

    async def get_chat(self, chat_id):
        return SimpleNamespace(title="Memes")

    def __getattr__(self, name):
        if name.startswith(("edit_", "send_", "delete_")):
            async def record(*args, **kwargs):
                if name == "send_message" and self.forbidden:
                    raise Forbidden("bot can't initiate conversation with a user")
                self.calls.append((name, args, kwargs))
                return SimpleNamespace(message_id=1)
            return record
        raise AttributeError(name)

    def names(self):
        return [call[0] for call in self.calls]


@pytest.fixture()
def bot(monkeypatch):
    module = load(DOWNLOADER, "bot")

    async def english(*_a, **_k):
        return "en"

    async def no_probe(path, probing):
        return {"width": 720, "height": 1280, "duration": 9}

    monkeypatch.setattr(module, "BOT_USERNAME", USERNAME)
    monkeypatch.setattr(module.i18n, "get_lang", english)
    monkeypatch.setattr(module, "_video_shape", no_probe)
    db = load(DOWNLOADER, "db")
    db.init_db()
    for chat_id in db.channel_ids():
        db.forget_group(chat_id)
    db.forget_group(CHANNEL)
    return module


def files(tmp_path, *kinds):
    made = []
    for n, kind in enumerate(kinds):
        path = tmp_path / f"{n}.{'mp4' if kind == 'video' else 'jpg'}"
        path.write_bytes(b"x" * 10)
        made.append((SimpleNamespace(kind=kind, filename=path.name), str(path)))
    return made


# ── settings and the plan (channels.py, pure) ───────────────────────────────

def test_channel_settings_defaults_and_taps():
    channels = load(DOWNLOADER, "channels")
    settings = channels.normalize(None)
    assert settings["kind"] == "channel" and settings["on"] and settings["repost"] and settings["credit"]
    assert settings["lang"] == "en" and not settings["as_file"] and settings["quality"] == 0
    assert channels.is_channel(settings) and not channels.is_channel({"on": True}) and not channels.is_channel(None)
    odd = channels.normalize({"lang": "fr", "quality": 999, "platforms": ["tiktok", "myspace"], "repost": "no"})
    assert odd["lang"] == "en" and odd["quality"] == 0 and odd["platforms"] == ["tiktok"] and odd["repost"] is True
    assert channels.apply(settings, "repost")["repost"] is False and settings["repost"] is True
    assert channels.apply(settings, "q", "480")["quality"] == 480
    assert "tiktok" not in channels.apply(settings, "p", "tiktok")["platforms"]
    assert channels.apply(settings, "admins") is None and channels.apply(settings, "q", "5") is None
    data = channels.data(CHANNEL, "p", "instagram")
    assert data == "cs:-1001234567890:p:instagram" and len(data.encode()) <= 64
    assert channels.parse(data) == (CHANNEL, "p", "instagram")
    assert channels.parse(channels.data(CHANNEL, "q", "0")) == (CHANNEL, "q", "0")
    assert channels.parse(channels.data(CHANNEL, "open")) == (CHANNEL, "open", "")
    assert channels.parse("cs:abc:on") is None and channels.parse("gs:p:tiktok") is None


def test_the_plan_for_a_link_post():
    channels = load(DOWNLOADER, "channels")
    short = "https://x.com/a/status/1"
    assert channels.plan(1, short, True, False, False, True) == "edit"
    assert channels.plan(1, short, False, True, True, True) == "repost"  # no edit right: posted anew
    assert channels.plan(3, short, True, False, False, True) is None     # an album needs post + delete
    assert channels.plan(3, short, True, True, True, True) == "repost"
    assert channels.plan(3, short, True, True, True, False) is None      # reposting turned off
    assert channels.plan(0, short, True, True, True, True) is None
    # Telegram counts a caption in UTF-16: 513 emoji are 1026 of them, over 1024.
    assert channels.plan(1, "😀" * 512, True, True, True, True) == "edit"
    assert channels.plan(1, "😀" * 513, True, True, True, True) is None
    assert channels.caption_for("link", "⬇️ via @bot") == "link\n\n⬇️ via @bot"
    assert channels.caption_for("a" * 1020, "⬇️ via @bot") == "a" * 1020  # no room: the text alone
    assert channels.missing_rights(True, True, True, True) == []
    assert channels.missing_rights(False, True, False, True) == ["edit", "delete"]
    assert channels.missing_rights(True, False, False, False) == []


def test_channels_are_listed_and_forgotten():
    db = load(DOWNLOADER, "db")
    db.init_db()
    channels, groups = load(DOWNLOADER, "channels"), load(DOWNLOADER, "groups")
    db.set_group_settings(-100777, channels.normalize(None))
    db.set_group_settings(-100123, groups.normalize(None))
    assert -100777 in db.channel_ids() and -100123 not in db.channel_ids()
    db.forget_group(-100777)
    db.forget_group(-100123)
    assert -100777 not in db.channel_ids()


# ── channel_router ───────────────────────────────────────────────────────────

def routed(bot, update, monkeypatch):
    """The _channel_job calls a channel update leads to, as (platform, link, settings)."""
    jobs, tasks = [], []

    async def job(_bot, post, platform, url, settings):
        jobs.append((platform, url, settings))

    monkeypatch.setattr(bot, "_channel_job", job)
    context = SimpleNamespace(bot=FakeBot(), application=SimpleNamespace(
        create_task=lambda coro, update=None: tasks.append(coro)))

    async def scenario():
        with pytest.raises(ApplicationHandlerStop):
            await bot.channel_router(update, context)
        for task in tasks:
            await task

    asyncio.run(scenario())
    return jobs


def test_a_text_post_with_a_link_gets_its_media(bot, monkeypatch):
    jobs = routed(bot, Update(1, channel_post=channel_post()), monkeypatch)
    assert [(platform, url) for platform, url, _ in jobs] == [("instagram", LINK)]
    db = load(DOWNLOADER, "db")
    assert CHANNEL in db.channel_ids()  # saved on the first post, so /channels lists it


def test_other_channel_updates_get_nothing(bot, monkeypatch):
    photo = SimpleNamespace(text=None, caption=LINK, chat_id=CHANNEL, forward_origin=None)
    forwarded = SimpleNamespace(text=LINK, chat_id=CHANNEL, forward_origin=object())
    assert routed(bot, SimpleNamespace(channel_post=photo), monkeypatch) == []
    assert routed(bot, SimpleNamespace(channel_post=forwarded), monkeypatch) == []
    assert routed(bot, Update(2, edited_channel_post=channel_post()), monkeypatch) == []
    assert routed(bot, Update(3, channel_post=channel_post("no link here")), monkeypatch) == []
    channels, db = load(DOWNLOADER, "channels"), load(DOWNLOADER, "db")
    db.set_group_settings(CHANNEL, channels.apply(channels.normalize(None), "p", "instagram"))
    assert routed(bot, Update(4, channel_post=channel_post()), monkeypatch) == []
    db.set_group_settings(CHANNEL, channels.apply(channels.normalize(None), "on"))
    assert routed(bot, Update(5, channel_post=channel_post()), monkeypatch) == []


# ── putting the media in ─────────────────────────────────────────────────────

def put(bot, fake, post, items, **settings):
    channels = load(DOWNLOADER, "channels")
    chosen = dict(channels.normalize(None), **settings)
    return asyncio.run(bot._put_into_channel(fake, post, items, chosen))


def test_one_video_is_edited_into_the_post_with_the_credit(bot, tmp_path):
    channels = load(DOWNLOADER, "channels")
    fake = FakeBot()
    assert put(bot, fake, channel_post(), files(tmp_path, "video")) == "edit"
    assert fake.names() == ["edit_message_media"]
    kwargs = fake.calls[0][2]
    assert (kwargs["chat_id"], kwargs["message_id"]) == (CHANNEL, 7)
    media = kwargs["media"]
    assert isinstance(media, InputMediaVideo) and (media.width, media.height) == (720, 1280)
    assert media.caption == f"{LINK}\n\n⬇️ via @{USERNAME}"
    link, mention = media.caption_entities
    assert (link.type, link.offset, link.length) == (MessageEntity.URL, 0, len(LINK))
    at = media.caption.index("@")
    assert (mention.type, mention.offset, mention.length) == (
        MessageEntity.MENTION, channels.utf16_length(media.caption[:at]), len(USERNAME) + 1)


def test_an_album_is_posted_anew_and_the_link_post_deleted(bot, tmp_path):
    fake = FakeBot()
    assert put(bot, fake, channel_post(), files(tmp_path, "photo", "photo", "video")) == "repost"
    assert fake.names() == ["send_media_group", "delete_message"]
    args, kwargs = fake.calls[0][1], fake.calls[0][2]
    album = args[1]
    assert [type(m) for m in album] == [InputMediaPhoto, InputMediaPhoto, InputMediaVideo]
    assert album[0].caption.startswith(LINK) and album[1].caption is None
    assert kwargs["disable_notification"] is True  # the link post already notified everybody
    assert fake.calls[1][1] == (CHANNEL, 7)


def test_an_album_stays_when_reposting_is_off(bot, tmp_path):
    fake = FakeBot()
    assert put(bot, fake, channel_post(), files(tmp_path, "photo", "photo"), repost=False) is None
    assert fake.calls == []


def test_without_the_edit_right_one_file_is_posted_anew(bot, tmp_path):
    fake = FakeBot(edit=False)
    assert put(bot, fake, channel_post(), files(tmp_path, "video"), credit=False) == "repost"
    assert fake.names() == ["send_video", "delete_message"]
    kwargs = fake.calls[0][2]
    assert kwargs["caption"] == LINK and kwargs["disable_notification"] is True
    assert (kwargs["width"], kwargs["height"]) == (720, 1280)


def test_files_and_a_long_text(bot, tmp_path):
    fake = FakeBot()
    assert put(bot, fake, channel_post(), files(tmp_path, "video"), as_file=True) == "edit"
    assert isinstance(fake.calls[0][2]["media"], InputMediaDocument)
    long_post = channel_post(LINK + " " + "a" * 1100)
    fake = FakeBot()
    assert put(bot, fake, long_post, files(tmp_path, "video")) is None  # the text would not fit: left alone
    assert fake.calls == []


def test_a_failed_download_says_nothing_in_the_channel(bot, monkeypatch):
    resolvers = load(DOWNLOADER, "resolvers")

    async def nothing(*a, **k):
        raise resolvers.NothingWorked("instagram", {"downloadgram": resolvers.ProviderFailed("a bot check", "blocked")})

    monkeypatch.setattr(bot.resolvers, "resolve", nothing)
    fake = FakeBot()
    channels = load(DOWNLOADER, "channels")
    asyncio.run(bot._channel_job(fake, channel_post(), "instagram", LINK, channels.normalize(None)))
    assert fake.calls == []


# ── the private chat ─────────────────────────────────────────────────────────

def membership(old: str, new: str, who=SimpleNamespace(id=42, is_bot=False)):
    return SimpleNamespace(my_chat_member=SimpleNamespace(
        chat=SimpleNamespace(id=CHANNEL, type="channel", title="Memes"), from_user=who,
        old_chat_member=SimpleNamespace(status=old), new_chat_member=SimpleNamespace(status=new)))


def test_whoever_adds_the_bot_gets_the_settings(bot):
    db, channels = load(DOWNLOADER, "db"), load(DOWNLOADER, "channels")
    fake = FakeBot(edit=False)
    asyncio.run(bot.bot_membership_changed(membership("left", "administrator"), SimpleNamespace(bot=fake)))
    assert fake.names() == ["send_message"]
    args, kwargs = fake.calls[0][1], fake.calls[0][2]
    assert args[0] == 42 and "“Memes”" in args[1]
    assert "Edit messages of others" in args[1]  # the right it was not given
    buttons = [b.callback_data for row in kwargs["reply_markup"].inline_keyboard for b in row]
    assert all(data.startswith(f"cs:{CHANNEL}:") for data in buttons)
    assert channels.is_channel(db.get_group_settings(CHANNEL))
    # Somebody who never started the bot cannot be written to: nothing breaks.
    asyncio.run(bot.bot_membership_changed(membership("administrator", "administrator"),
                                           SimpleNamespace(bot=FakeBot(forbidden=True))))
    # Removed: the settings go.
    asyncio.run(bot.bot_membership_changed(membership("administrator", "left"), SimpleNamespace(bot=fake)))
    assert db.get_group_settings(CHANNEL) is None


def tap(bot, fake, data, user_id=42):
    answers = []

    async def answer(text=None, show_alert=False):
        answers.append((text, show_alert))

    edits = []

    async def edit(text, reply_markup=None):
        edits.append(text)

    query = SimpleNamespace(data=data, answer=answer, edit_message_text=edit)
    update = SimpleNamespace(callback_query=query, effective_user=SimpleNamespace(id=user_id),
                             effective_chat=SimpleNamespace(type="private"))
    context = SimpleNamespace(bot=fake, user_data={}, application=SimpleNamespace(create_task=lambda c: c.close()))
    asyncio.run(bot.channel_settings_callback(update, context))
    return answers, edits


def test_only_the_channels_admins_change_its_settings(bot, monkeypatch):
    db, channels = load(DOWNLOADER, "db"), load(DOWNLOADER, "channels")
    monkeypatch.setattr(bot, "note_active", lambda *a: None)
    answers, _ = tap(bot, FakeBot(admins={42}), channels.data(CHANNEL, "repost"))
    assert answers == [("I'm not in that channel any more.", True)]
    db.set_group_settings(CHANNEL, channels.normalize(None))
    answers, edits = tap(bot, FakeBot(admins=set()), channels.data(CHANNEL, "repost"))
    assert answers[0][1] is True and edits == []
    assert channels.normalize(db.get_group_settings(CHANNEL))["repost"] is True
    answers, edits = tap(bot, FakeBot(admins={42}), channels.data(CHANNEL, "repost"))
    assert answers == [("Saved ✅", False)] and len(edits) == 1
    assert channels.normalize(db.get_group_settings(CHANNEL))["repost"] is False


def test_channels_lists_only_what_the_person_runs(bot):
    db, channels = load(DOWNLOADER, "db"), load(DOWNLOADER, "channels")
    replies = []

    async def reply_text(text, reply_markup=None):
        replies.append((text, reply_markup))

    update = SimpleNamespace(effective_chat=SimpleNamespace(type="private"), effective_user=SimpleNamespace(id=42),
                             effective_message=SimpleNamespace(reply_text=reply_text))
    asyncio.run(bot.channels_command(update, SimpleNamespace(bot=FakeBot(admins={42}))))
    assert replies[-1][0].startswith("I'm not in any channel you run")
    db.set_group_settings(CHANNEL, channels.normalize(None))
    asyncio.run(bot.channels_command(update, SimpleNamespace(bot=FakeBot(admins={42}))))
    assert replies[-1][0].startswith("⚙️ What I do in “Memes”") and replies[-1][1] is not None
    asyncio.run(bot.channels_command(update, SimpleNamespace(bot=FakeBot(admins={7}))))
    assert replies[-1][0].startswith("I'm not in any channel you run")
