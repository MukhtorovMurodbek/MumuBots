"""DownloaderBot's quality picker, as 2.1.7 deployed it, on 2.2.0's footing: a card with the picture, the
title and a button per quality with its size; qualities named the way the site names them (else by the
shorter side); nothing counted until a tap, and the buttons stay; a paid ⚡ row where large files are set up;
a group never sees a card. The chosen quality still travels on the link (#q720), and a group's cap (#upto480)
still picks by the picture's height."""
import asyncio
import contextlib
from types import SimpleNamespace

import pytest

from conftest import load, unadvertised

SITE, SHORT, MEDIA = unadvertised()

DOWNLOADER = "downloader_bot"
MB = 1024 * 1024
LINK = f"https://{SITE}.com/shorts/I-SH-mG1BDg"


def _fmt(fid, width=None, height=None, vcodec="avc1.4d401f", acodec="none", size=None, ext="mp4", **extra):
    fmt = {"format_id": fid, "width": width, "height": height, "vcodec": vcodec, "acodec": acodec,
           "ext": ext, "filesize": size}
    fmt.update(extra)
    return fmt


SHORT = {
    "id": "I-SH-mG1BDg", "title": "A short", "duration": 59, "thumbnail": "https://example.org/t.jpg",
    "formats": [
        _fmt("160", 144, 256, size=1 * MB),
        _fmt("134", 360, 640, size=3 * MB),
        _fmt("135", 480, 854, size=5 * MB),
        _fmt("136", 720, 1280, size=9 * MB),
        _fmt("248", 1080, 1920, vcodec="vp9", size=14 * MB, ext="webm"),
        _fmt("137", 1080, 1920, size=20 * MB),
        _fmt("401", 2160, 3840, vcodec="av01", size=90 * MB),
        _fmt("140", vcodec="none", acodec="mp4a.40.2", size=1 * MB, ext="m4a", abr=128),
        _fmt("251", vcodec="none", acodec="opus", size=1 * MB, ext="webm", abr=160),
    ],
}

REAL = {
    "id": "x", "title": "t", "duration": 105,
    "formats": [
        _fmt("230", 360, 640, size=None, tbr=771.0, protocol="m3u8_native"),
        _fmt("134", 360, 640, size=3574337, tbr=271.9, format_note="360p"),
        _fmt("135", 480, 854, size=6370927, tbr=484.6, format_note="480p"),
        _fmt("779", 608, 1080, vcodec="vp9", size=4280972, ext="webm", format_note="480p"),
        _fmt("270", 1080, 1920, vcodec="avc1.640028", size=None, tbr=3662.0, protocol="m3u8_native"),
        _fmt("137", 1080, 1920, vcodec="avc1.640028", size=25972760, tbr=1975.8, format_note="1080p"),
        _fmt("140-drc", vcodec="none", acodec="mp4a.40.2", size=1703979, ext="m4a", abr=129.5,
             language_preference=10, format_note="English (US) original (default), medium, DRC"),
        _fmt("140-0", vcodec="none", acodec="mp4a.40.2", size=1705386, ext="m4a", abr=129.5,
             language_preference=-1, format_note="Arabic, medium"),
        _fmt("140-20", vcodec="none", acodec="mp4a.40.2", size=1703982, ext="m4a", abr=129.5,
             language_preference=10, format_note="English (US) original (default), medium"),
    ],
}

LANDSCAPE = {"duration": 600, "formats": [
    {"format_id": "160", "vcodec": "avc1.4d400c", "acodec": "none", "height": 144, "filesize": 1 * MB},
    {"format_id": "134", "vcodec": "avc1.4d401e", "acodec": "none", "height": 360, "filesize": 3 * MB},
    {"format_id": "136", "vcodec": "avc1.4d401f", "acodec": "none", "height": 720, "filesize": 30 * MB},
    {"format_id": "137", "vcodec": "avc1.640028", "acodec": "none", "height": 1080, "filesize": 90 * MB},
    {"format_id": "140", "vcodec": "none", "acodec": "mp4a.40.2", "abr": 129, "filesize": 2 * MB},
]}

INVIDIOUS = {
    "title": "A short", "lengthSeconds": 59,
    "adaptiveFormats": [
        {"url": "https://h/videoplayback?a=1", "type": 'video/mp4; codecs="avc1.4d401e"', "size": "360x640",
         "clen": str(3 * MB), "bitrate": "1"},
        {"url": "https://h/videoplayback?a=2", "type": 'video/mp4; codecs="avc1.4d401f"', "size": "720x1280",
         "clen": str(9 * MB), "bitrate": "2"},
        {"url": "https://h/videoplayback?a=3", "type": 'video/mp4; codecs="avc1.640028"', "size": "1080x1920",
         "clen": str(20 * MB), "bitrate": "3"},
        {"url": "https://h/videoplayback?a=4", "type": 'audio/mp4; codecs="mp4a.40.2"', "clen": str(MB),
         "bitrate": "128000"},
    ],
}


@pytest.fixture()
def video():
    return load(DOWNLOADER, "video")


@pytest.fixture()
def resolvers():
    return load(DOWNLOADER, "resolvers")


def qualities(found):
    return [(q, free) for q, _, free in found["qualities"]]


# ── which versions are offered, and which one is fetched ─────────────────────

def test_choices_are_named_by_the_shorter_side_and_fit_the_limit(video):
    found = video.choices_of(SHORT, 48 * MB)
    assert qualities(found) == [(360, True), (480, True), (720, True), (1080, True)]
    assert {q: size for q, size, _ in found["qualities"]}[1080] == 21 * MB
    assert found["audio"] == 1 * MB


def test_a_version_over_the_free_limit_is_paid_only_where_large_files_are_set_up(video):
    assert qualities(video.choices_of(SHORT, 12 * MB)) == [(360, True), (480, True), (720, True)]
    assert qualities(video.choices_of(SHORT, 12 * MB, 500 * MB)) == [
        (360, True), (480, True), (720, True), (1080, False)]


def test_a_version_with_no_stated_size_is_not_offered(video):
    info = dict(SHORT, formats=[dict(f, filesize=None) if f["format_id"] == "136" else f for f in SHORT["formats"]])
    assert 720 not in [q for q, _, _ in video.choices_of(info, 48 * MB)["qualities"]]


def test_at_most_five_free_qualities_and_paid_ones_never_push_them_off(video):
    heights = (240, 360, 480, 540, 720, 900, 1080)
    info = {"duration": 60, "formats": [_fmt(str(n), h * 2, h, size=(n + 1) * MB) for n, h in enumerate(heights[:6])]
            + [_fmt("big", 2160, 1080, size=200 * MB), _fmt("a", vcodec="none", acodec="mp4a.40.2", size=MB)]}
    found = video.choices_of(info, 48 * MB, 500 * MB)
    assert qualities(found) == [(360, True), (480, True), (540, True), (720, True), (900, True), (1080, False)]


def test_the_pick_is_fetched_exactly(video):
    assert video.pick_choice(SHORT, 720, 48 * MB) == "136+140"
    assert video.pick_choice(SHORT, 1080, 48 * MB) == "137+140"
    assert video.pick_choice(SHORT, video.AUDIO, 48 * MB) == "140"
    with pytest.raises(video.TooLarge):
        video.pick_choice(SHORT, 1440, 48 * MB)
    with pytest.raises(video.TooLarge):
        video.pick_choice(SHORT, 1080, 10 * MB)


def test_real_listing_uses_the_sites_labels_plain_files_and_the_own_sound(video):
    found = video.choices_of(REAL, 48 * MB)
    assert [q for q, _, _ in found["qualities"]] == [360, 480, 1080]
    assert video.pick_choice(REAL, 360, 48 * MB) == "134+140-20"
    assert video.pick_choice(REAL, 1080, 48 * MB) == "137+140-20"
    assert video.pick_choice(REAL, video.AUDIO, 48 * MB) == "140-20"
    assert {q: size for q, size, _ in found["qualities"]}[1080] == 25972760 + 1703982


def test_the_default_download_and_a_groups_cap_are_unchanged(video):
    assert video.pick_format(LANDSCAPE, 48 * MB, 480) == "134+140"
    assert video.pick_format(LANDSCAPE, 1000 * MB) == "137+140"
    chosen = video.pick_format(SHORT, 48 * MB)
    picture = next(f for f in SHORT["formats"] if f["format_id"] == chosen.split("+")[0])
    assert picture["height"] <= video.MAX_HEIGHT


def test_invidious_lists_by_the_shorter_side_and_picks_exactly(resolvers):
    found = resolvers._invidious_choices(INVIDIOUS, "I-SH-mG1BDg", 0)
    assert [(q, free) for q, _, free in found.qualities] == [(360, True), (720, True), (1080, True)]
    assert found.audio == MB and found.thumbnail.endswith("/I-SH-mG1BDg/hqdefault.jpg")
    picture, sound = resolvers._invidious_pick(INVIDIOUS["adaptiveFormats"], 48 * MB, 720, True)
    assert picture["size"] == "720x1280" and sound["clen"] == str(MB)


def test_cobalt_is_asked_for_just_the_pick(resolvers):
    assert resolvers._cobalt_qualities()[0] == resolvers.COBALT_QUALITY
    token = resolvers._HEIGHT.set((720, True))
    try:
        assert resolvers._cobalt_qualities() == ["720"]
    finally:
        resolvers._HEIGHT.reset(token)


def test_choices_never_raise_and_skip_other_sites(resolvers, video, monkeypatch):
    assert asyncio.run(resolvers.choices("tiktok", "https://www.tiktok.com/@a/video/1")) is None

    async def refused(url, paid):
        raise video.BlockedBySource("Sign in to confirm you're not a bot", "bot_check", SITE)

    monkeypatch.setattr(resolvers, "_choices_ytdlp", refused)
    monkeypatch.setattr(resolvers, "_choices_invidious", refused)
    assert asyncio.run(resolvers.choices(SITE, LINK)) is None

    async def slow(url, paid):
        await asyncio.sleep(5)

    monkeypatch.setattr(resolvers, "_choices_ytdlp", slow)
    monkeypatch.setattr(resolvers, "PICK_LIST_TIMEOUT_S", 0.2)
    assert asyncio.run(resolvers.choices(SITE, LINK)) is None


def test_a_resting_route_is_not_asked_to_list(resolvers, monkeypatch):
    asked = []

    async def lister(url, paid):
        asked.append(url)
        return resolvers.Choices("t", 1, None, [(360, MB, True), (720, 2 * MB, True)], MB)

    monkeypatch.setattr(resolvers, "_choices_ytdlp", lister)
    monkeypatch.setattr(resolvers, "_choices_invidious", lister)
    monkeypatch.setattr(resolvers, "_health", {})
    for _ in range(resolvers.COOLDOWN_AFTER_FAILURES):
        resolvers._record_fail(f"ytdlp:{SITE}", "Sign in to confirm you're not a bot")
        resolvers._record_fail(f"invidious:{SITE}", "timed out")
    assert asyncio.run(resolvers.choices(SITE, LINK)) is None and asked == []
    resolvers._record_ok(f"invidious:{SITE}")
    assert asyncio.run(resolvers.choices(SITE, LINK)) is not None and asked == [LINK]


def test_one_version_and_no_sound_is_not_worth_a_card(resolvers):
    assert not resolvers.Choices("t", 1, None, [(720, MB, True)], None).worth_asking
    assert resolvers.Choices("t", 1, None, [(720, MB, True)], MB).worth_asking


def test_the_old_listing_setting_still_works(resolvers):
    assert resolvers.PICK_LIST_TIMEOUT_S == 10


# ── the card, a tap, and what is counted ─────────────────────────────────────

class Recorder:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        if name.startswith(("reply_", "answer", "delete", "set")):
            async def record(*args, **kwargs):
                self.calls.append((name, args, kwargs))
                return SimpleNamespace(message_id=len(self.calls))
            return record
        raise AttributeError(name)


class FakeLive:
    made = []

    def __init__(self, text):
        self.text, self.deleted = text, False
        FakeLive.made.append(self)

    @classmethod
    async def reply_to(cls, message, text):
        return cls(text)

    async def delete(self, bot):
        self.deleted = True

    async def set(self, bot, text, **kwargs):
        self.text = text


@pytest.fixture()
def picker(monkeypatch):
    bot = load(DOWNLOADER, "bot")
    seen = {"counted": [], "sent": [], "spawned": [], "taps": [], "real_send": bot._resolve_and_send}

    async def english(*_a, **_k):
        return "en"

    async def no_refusal(*_a, **_k):
        return None

    async def resolve_and_send(update, context, platform, url, status=None, **kwargs):
        seen["sent"].append((platform, url, status))
        return True

    def spawn(context, update, work, in_flight=None):
        seen["spawned"].append(in_flight)

        async def run():
            try:
                await work()
            finally:
                if in_flight is not None:
                    bot._in_flight.pop(in_flight, None)

        seen.setdefault("work", []).append(run)

    monkeypatch.setattr(bot.i18n, "get_lang", english)
    monkeypatch.setattr(bot, "refuse_new_work", no_refusal)
    monkeypatch.setattr(bot, "record_download", lambda user_id, platform: seen["counted"].append(platform))
    monkeypatch.setattr(bot, "download_allowance", lambda user_id: {"allowed": True})
    monkeypatch.setattr(bot, "_resolve_and_send", resolve_and_send)
    monkeypatch.setattr(bot, "_spawn_download", spawn)
    monkeypatch.setattr(bot, "note_tap", lambda name: seen["taps"].append(name))
    monkeypatch.setattr(bot, "LiveMessage", FakeLive)
    monkeypatch.setattr(bot, "sibling_bots_keyboard_row", lambda *a, **k: [])
    monkeypatch.setattr(bot.delivery_cache, "note_link", lambda *a, **k: None)
    bot._in_flight.clear()
    FakeLive.made = []
    return bot, seen


def update_for(user=4242, chat=4242, data=None):
    message = Recorder()
    query = None
    if data is not None:
        query = Recorder()
        query.data, query.message = data, message
    return SimpleNamespace(effective_user=SimpleNamespace(id=user), effective_chat=SimpleNamespace(id=chat),
                           effective_message=message, callback_query=query, message=message)


def context_for():
    return SimpleNamespace(user_data={}, bot=SimpleNamespace())


def offered(bot, monkeypatch, found):
    async def choices(platform, url, paid_bytes=0):
        return found

    monkeypatch.setattr(bot.resolvers, "choices", choices)


def found_card(resolvers, paid=False):
    rows = [(360, 3 * MB, True), (720, 9 * MB, True)] + ([(1080, 120 * MB, False)] if paid else [])
    return resolvers.Choices("A short", 59, "https://example.org/t.jpg", rows, None)


def test_a_link_gets_a_card_and_nothing_is_counted_yet(picker, resolvers, monkeypatch):
    bot, seen = picker
    offered(bot, monkeypatch, found_card(resolvers))
    update, context = update_for(), context_for()
    asyncio.run(bot._offer_choices(update, context, SITE, LINK))
    name, args, kwargs = update.effective_message.calls[-1]
    assert name == "reply_photo" and args[0] == "https://example.org/t.jpg"
    assert kwargs["caption"] == "🎞 A short\n⏱ 0:59\n\nPick a quality:"
    labels = [b.text for row in kwargs["reply_markup"].inline_keyboard for b in row]
    assert labels == ["🎬 360p · 3.0 MB", "🎬 720p · 9.0 MB"]
    assert seen["counted"] == [] and seen["sent"] == [] and FakeLive.made[0].deleted
    assert "pick.offered" in seen["taps"]


def test_a_tap_counts_once_and_fetches_that_version_and_the_buttons_stay(picker, resolvers, monkeypatch):
    bot, seen = picker
    offered(bot, monkeypatch, found_card(resolvers))
    update, context = update_for(), context_for()
    asyncio.run(bot._offer_choices(update, context, SITE, LINK))
    token = next(iter(context.user_data["picks"]))
    for quality in (720, 360):
        tap = update_for(data=f"pick:{token}:{quality}")
        asyncio.run(bot.pick_callback(tap, context))
        asyncio.run(seen["work"][-1]())
    assert seen["counted"] == [SITE, SITE]
    assert [url for _, url, _ in seen["sent"]] == [LINK + "#q720", LINK + "#q360"]
    assert token in context.user_data["picks"]


def test_a_paid_quality_is_marked_and_a_tap_on_it_charges_nothing_by_itself(picker, resolvers, monkeypatch):
    bot, seen = picker
    offered(bot, monkeypatch, found_card(resolvers, paid=True))
    update, context = update_for(), context_for()
    asyncio.run(bot._offer_choices(update, context, SITE, LINK))
    _, _, kwargs = update.effective_message.calls[-1]
    labels = [b.text for row in kwargs["reply_markup"].inline_keyboard for b in row]
    assert labels[-1] == "⚡ 1080p · 120 MB" and "⚡ — over" in kwargs["caption"]
    token = next(iter(context.user_data["picks"]))
    for _ in range(2):
        asyncio.run(bot.pick_callback(update_for(data=f"pick:{token}:1080"), context))
        asyncio.run(seen["work"][-1]())
    assert seen["counted"] == [SITE, SITE]
    assert [url for _, url, _ in seen["sent"]] == [LINK + "#q1080", LINK + "#q1080"]


def test_an_old_card_says_so(picker):
    bot, seen = picker
    tap = update_for(data="pick:0123456789:720")
    asyncio.run(bot.pick_callback(tap, context_for()))
    name, args, kwargs = tap.callback_query.calls[-1]
    assert name == "answer" and args[0] == "These buttons are too old now — send the link again."
    assert kwargs.get("show_alert") and seen["counted"] == [] and seen["sent"] == []


def test_when_nothing_lists_the_versions_the_link_downloads_at_once(picker, monkeypatch):
    bot, seen = picker
    offered(bot, monkeypatch, None)
    update = update_for()
    asyncio.run(bot._offer_choices(update, context_for(), SITE, LINK))
    assert seen["counted"] == [SITE] and [(p, u) for p, u, _ in seen["sent"]] == [(SITE, LINK)]
    assert "pick.not_listed" in seen["taps"]


def test_only_a_private_chat_link_to_that_site_waits_for_a_tap(picker, monkeypatch):
    bot, seen = picker
    asked = []

    async def offer(update, context, platform, url):
        asked.append(url)

    monkeypatch.setattr(bot, "_offer_choices", offer)
    monkeypatch.setattr(bot, "_already_in_flight", lambda key: False)

    def link_update(text):
        update = update_for()
        update.message = SimpleNamespace(text=text, caption=None, reply_text=update.effective_message.reply_text)
        return update

    asyncio.run(bot.handle_link(link_update(LINK), context_for()))
    asyncio.run(seen["work"][-1]())
    assert len(asked) == 1 and "I-SH-mG1BDg" in asked[0] and seen["counted"] == []

    asyncio.run(bot.handle_link(link_update("https://www.instagram.com/reel/abc/"), context_for()))
    asyncio.run(seen["work"][-1]())
    assert seen["counted"] == ["instagram"] and seen["sent"][-1][0] == "instagram"

    token = bot._GROUP.set({"quality": 0, "on": True})
    try:
        asyncio.run(bot.handle_link(link_update(LINK), context_for()))
        asyncio.run(seen["work"][-1]())
    finally:
        bot._GROUP.reset(token)
    assert len(asked) == 1 and seen["counted"] == ["instagram", SITE] and seen["sent"][-1][0] == SITE


# ── just the sound ───────────────────────────────────────────────────────────

def test_the_sound_travels_on_the_link_too(picker):
    bot, _ = picker
    assert bot._split_choice(bot._with_sound(LINK)) == (LINK, None, False, True)
    assert bot._split_choice(bot._with_height(LINK, 720)) == (LINK, 720, True, False)
    assert bot._split_height(bot._with_sound(LINK)) == (LINK, None, False)


def test_a_card_offers_the_sound_and_a_tap_fetches_only_that(picker, resolvers, monkeypatch):
    bot, seen = picker
    found = resolvers.Choices("A short", 59, None, [(360, 3 * MB, True), (720, 9 * MB, True)], MB)
    offered(bot, monkeypatch, found)
    update, context = update_for(), context_for()
    asyncio.run(bot._offer_choices(update, context, SITE, LINK))
    name, args, kwargs = update.effective_message.calls[-1]
    assert name == "reply_text" and args[0].endswith("Pick a quality, or just the sound:")
    labels = [b.text for row in kwargs["reply_markup"].inline_keyboard for b in row]
    assert labels[-1] == "🎵 Sound only · 1.0 MB"
    token = next(iter(context.user_data["picks"]))
    asked = {}

    async def resolve_and_send(update, context, platform, url, status=None, title=None, duration=None, **kw):
        asked.update(url=url, title=title, duration=duration)
        return True

    monkeypatch.setattr(bot, "_resolve_and_send", resolve_and_send)
    asyncio.run(bot.pick_callback(update_for(data=f"pick:{token}:a"), context))
    asyncio.run(seen["work"][-1]())
    assert asked == {"url": LINK + "#sound", "title": "A short", "duration": 59}
    assert seen["counted"] == [SITE] and "pick.audio" in seen["taps"]


def test_every_route_takes_just_the_sound(resolvers, video, monkeypatch):
    token = resolvers._SOUND.set(True)
    try:
        assert resolvers._cobalt_qualities() == [resolvers.COBALT_AUDIO]
    finally:
        resolvers._SOUND.reset(token)
    sound = resolvers._invidious_sound_format(INVIDIOUS["adaptiveFormats"])
    assert sound["clen"] == str(MB)
    seen = {}

    def download_video(url, out_dir, max_mb=None, cancel=None, height=None, exact=False, sound=False):
        seen["sound"] = sound
        return out_dir + "/x.m4a"

    monkeypatch.setattr(video, "download_video", download_video)
    token = resolvers._SOUND.set(True)
    try:
        resolved = asyncio.run(resolvers._ytdlp_provider(SITE)(LINK))
    finally:
        resolvers._SOUND.reset(token)
    assert seen["sound"] is True and resolved.items[0].kind == "audio"


def test_the_sound_comes_as_an_audio_file_with_the_title_and_skips_the_caches(picker, resolvers, monkeypatch,
                                                                             tmp_path):
    bot, seen = picker
    path = tmp_path / "x.m4a"
    path.write_bytes(b"\0\0\0\x18ftypM4A " + b"x" * 64)

    def must_not(*_a, **_k):
        raise AssertionError("a sound file is never kept or resent from a stored id")

    async def must_not_async(*_a, **_k):
        must_not()

    async def resolve(platform, url, max_mb=None, height=None, exact=False, sound=False):
        assert sound and url == LINK
        return resolvers.Resolved(SITE, "ytdlp", [resolvers.MediaItem("audio", path=str(path),
                                                                           filename="x.m4a")])

    @contextlib.asynccontextmanager
    async def nothing(*_a, **_k):
        yield

    async def quiet(*_a, **_k):
        return None

    async def no_credit(*_a, **_k):
        return False

    monkeypatch.setattr(bot, "_resolve_and_send", seen["real_send"])
    monkeypatch.setattr(bot, "_resend_by_file_id", must_not_async)
    monkeypatch.setattr(bot, "_store_sent_files", must_not_async)
    monkeypatch.setattr(bot.delivery_cache, "take", must_not)
    monkeypatch.setattr(bot.delivery_cache, "keep", must_not)
    monkeypatch.setattr(bot.resolvers, "resolve", resolve)
    monkeypatch.setattr(bot.lifecycle, "busy", nothing)
    monkeypatch.setattr(bot, "_disk_room", lambda mb: True)
    monkeypatch.setattr(bot, "_hold_disk", lambda mb: None)
    monkeypatch.setattr(bot.resources, "release_disk", lambda held: None)
    monkeypatch.setattr(bot, "_credit_wanted", no_credit)
    monkeypatch.setattr(bot, "_nudge_kb", lambda *a, **k: None)
    monkeypatch.setattr(bot, "_after_send", quiet)
    monkeypatch.setattr(bot, "note_job", lambda *a, **k: None)
    monkeypatch.setattr(bot, "note_stat", lambda *a, **k: None)
    update = update_for()
    assert asyncio.run(bot._resolve_and_send(update, context_for(), SITE, LINK + "#sound",
                                             title="A short: the song", duration=59))
    name, args, kwargs = update.effective_message.calls[-1]
    assert name == "reply_audio" and kwargs["title"] == "A short: the song"
    assert kwargs["filename"] == "A short the song.m4a" and kwargs["duration"] == 59
