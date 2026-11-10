"""The relays behind the one platform the bot does not advertise (2.1.5, the owner, 2026-11-10:
"downloading ... is problematic").

Production's own records: every yt-dlp attempt since 2026-09-11 was answered "sign in to confirm
you're not a bot" (the site refuses the host's address), and the relay route behind it accepted
an instance's answer whose file then came to nothing. So: a relay's file is fetched and looked at
inside the route, an instance whose file is no good is passed over, a file over the ceiling is
asked for again smaller, a second family of relays (Invidious) stands behind the first, and the
links people really paste are read. Nothing here touches the network.
"""
import asyncio
import os
import shutil
import subprocess

import httpx
import pytest

from conftest import load

resolvers = load("downloader_bot", "resolvers")
platforms = load("downloader_bot", "platforms")
net = load("downloader_bot", "net")

MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 4096
WATCH = "https://www.youtube.com/watch?v=jNQXAC9IVRw"


@pytest.fixture(autouse=True)
def work_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(resolvers, "_WORK_DIR", str(tmp_path))
    monkeypatch.setattr(resolvers, "_cobalt_last_good", None)
    monkeypatch.setattr(resolvers, "_invidious_last_good", None)
    return tmp_path


# ---- the links people paste ---------------------------------------------------------------

@pytest.mark.parametrize("pasted", [
    "https://www.youtube.com/watch?v=jNQXAC9IVRw",
    "https://youtu.be/jNQXAC9IVRw?si=AbCdEf123",
    "https://www.youtube.com/watch?app=desktop&v=jNQXAC9IVRw",
    "https://youtube.com/watch?list=PLx&v=jNQXAC9IVRw&index=2",
    "https://m.youtube.com/watch?v=jNQXAC9IVRw&t=5s",
    "https://music.youtube.com/watch?v=jNQXAC9IVRw&list=RDAMVM",
    "https://www.youtube.com/shorts/jNQXAC9IVRw?feature=share",
    "https://www.youtube.com/live/jNQXAC9IVRw",
    "https://www.youtube.com/embed/jNQXAC9IVRw?start=3",
    "look: https://youtu.be/jNQXAC9IVRw.",
])
def test_every_pasted_shape_is_one_link(pasted):
    assert platforms.detect_platform(pasted) == ("youtube", WATCH)


@pytest.mark.parametrize("pasted", [
    "https://www.youtube.com/@somebody", "https://www.youtube.com/playlist?list=PLx",
    "https://www.youtube.com/watch?list=PLx", "https://www.youtube.com/results?search_query=v=1",
])
def test_what_is_not_one_video_is_not_taken(pasted):
    assert platforms.detect_platform(pasted) is None


# ---- cobalt: the file is fetched and looked at inside the route ------------------------------

def _cobalt(monkeypatch, tunnels, hosts=("https://one.example", "https://two.example")):
    """Two instances that both answer with a tunnel; `tunnels` says what each tunnel sends:
    bytes, or an exception. Returns the calls made."""
    calls = {"asked": [], "fetched": []}
    monkeypatch.setattr(resolvers, "COBALT_INSTANCES", list(hosts))

    async def one(host, url, platform, key, quality=None):
        calls["asked"].append((host, quality))
        return resolvers.Resolved(platform, "cobalt", [resolvers.MediaItem(
            "video", url=f"{host}/tunnel?q={quality}", filename="youtube.mp4")])

    async def stream_to_file(url, path, limit=0, headers=None):
        calls["fetched"].append(url)
        sent = tunnels(url)
        if isinstance(sent, BaseException):
            raise sent
        with open(path, "wb") as out:
            out.write(sent)
        return len(sent)

    monkeypatch.setattr(resolvers, "_cobalt_one", one)
    monkeypatch.setattr(resolvers.net, "stream_to_file", stream_to_file)
    return calls


def test_an_instance_whose_tunnel_sends_nothing_is_passed_over(monkeypatch, work_dir):
    calls = _cobalt(monkeypatch, lambda url: b"" if "one.example" in url else MP4)
    found = asyncio.run(resolvers._cobalt_provider("youtube")(WATCH))
    assert [host for host, _ in calls["asked"]] == ["https://one.example", "https://two.example"]
    assert found.items[0].path and open(found.items[0].path, "rb").read() == MP4
    assert os.listdir(work_dir) == [os.path.basename(found.items[0].path)]     # the empty file is gone
    assert resolvers._cobalt_last_good == "https://two.example"


def test_a_page_in_place_of_a_video_is_not_a_video(monkeypatch, work_dir):
    _cobalt(monkeypatch, lambda url: b"<html>" + b"x" * 5000)
    with pytest.raises(resolvers.ProviderFailed) as failed:
        asyncio.run(resolvers._cobalt_provider("youtube")(WATCH))
    assert failed.value.kind == "blocked" and os.listdir(work_dir) == []


def test_a_file_over_the_ceiling_is_asked_for_smaller(monkeypatch, work_dir):
    calls = _cobalt(monkeypatch, lambda url: net.TooLarge("too big") if "q=720" in url else MP4)
    found = asyncio.run(resolvers._cobalt_provider("youtube")(WATCH))
    assert calls["asked"] == [("https://one.example", "720"), ("https://one.example", "480")]
    assert found.items[0].path


def test_over_the_ceiling_at_every_height_is_too_big_and_ends_the_route(monkeypatch, work_dir):
    calls = _cobalt(monkeypatch, lambda url: net.TooLarge("too big"))
    with pytest.raises(resolvers.ProviderFailed) as failed:
        asyncio.run(resolvers._cobalt_provider("youtube")(WATCH))
    assert failed.value.kind == "too_big"
    assert [quality for _, quality in calls["asked"]] == ["720", "480", "360"]     # one instance only
    assert os.listdir(work_dir) == []


def test_one_exact_height_is_not_walked_down(monkeypatch):
    token = resolvers._HEIGHT.set((1080, True))
    try:
        assert resolvers._cobalt_qualities() == ["1080"]
    finally:
        resolvers._HEIGHT.reset(token)
    assert resolvers._cobalt_qualities() == ["720", "480", "360"]


def test_asking_whether_a_post_is_there_fetches_no_file(monkeypatch, work_dir):
    calls = _cobalt(monkeypatch, lambda url: MP4)
    monkeypatch.setattr(resolvers, "PROVIDERS", {"youtube": [("cobalt:youtube", resolvers._cobalt_provider("youtube"))]})
    assert asyncio.run(resolvers.lookup("youtube", WATCH)) == "present"
    assert calls["fetched"] == [] and os.listdir(work_dir) == []
    assert resolvers._LOOKUP.get() is False


def test_a_route_check_asks_and_reads_the_first_bytes_only(monkeypatch, work_dir):
    calls = _cobalt(monkeypatch, lambda url: MP4)
    fn = resolvers._cobalt_provider("youtube")
    row = asyncio.run(resolvers.probe_route("cobalt:youtube", fn, WATCH, timeout=5))
    assert row["ok"] and calls["fetched"] == [] and os.listdir(work_dir) == []
    assert resolvers._LOOKUP.get() is False

    def handler(request):
        return httpx.Response(200, content=b"" if "one.example" in str(request.url) else MP4)

    async def checked():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            monkeypatch.setattr(net, "client", lambda: client)
            return await resolvers.probe_route("cobalt:youtube", fn, WATCH, fetch=True, timeout=5)

    # The instance asked first answers, and its tunnel sends nothing: the check says so.
    assert asyncio.run(checked())["fetched"] == "fetch failed: it sent nothing"
    monkeypatch.setattr(resolvers, "COBALT_INSTANCES", ["https://two.example"])
    assert asyncio.run(checked())["fetched"].startswith(f"{len(MP4):,} bytes")


def test_a_fetch_that_is_cancelled_leaves_no_file(monkeypatch, work_dir):
    async def stream_to_file(url, path, limit=0, headers=None):
        with open(path, "wb") as out:
            out.write(MP4)
        await asyncio.sleep(30)

    monkeypatch.setattr(resolvers.net, "stream_to_file", stream_to_file)

    async def scenario():
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(resolvers._take("https://one.example/tunnel", "mp4", 10 ** 6), timeout=0.05)

    asyncio.run(scenario())
    assert os.listdir(work_dir) == []


def test_the_routes_that_fetch_get_time_for_it():
    assert resolvers._timeout_for("cobalt:youtube") == resolvers.COBALT_TIMEOUT_S
    assert resolvers._timeout_for("invidious:youtube") == resolvers.INVIDIOUS_TIMEOUT_S
    assert resolvers._timeout_for("tikwm") == resolvers.PROVIDER_TIMEOUT_S
    token = resolvers._LOOKUP.set(True)
    try:
        assert resolvers._timeout_for("cobalt:youtube") == resolvers.PROVIDER_TIMEOUT_S
    finally:
        resolvers._LOOKUP.reset(token)
    assert [name for name, _ in resolvers.PROVIDERS["youtube"]] == \
        ["ytdlp:youtube", "cobalt:youtube", "invidious:youtube"]


def test_a_guessed_length_well_past_the_ceiling_is_refused_unread(monkeypatch, tmp_path):
    read = []

    def handler(request):
        async def body():
            read.append(1)
            yield MP4

        guessed = request.url.params["guess"]
        return httpx.Response(200, headers={"Estimated-Content-Length": guessed}, content=body())

    async def fetch(guess):
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            monkeypatch.setattr(net, "client", lambda: client)
            return await net.stream_to_file(f"https://one.example/tunnel?guess={guess}",
                                            str(tmp_path / "out.mp4"), limit=10_000)

    with pytest.raises(net.TooLarge):
        asyncio.run(fetch(50_000))
    assert read == []
    assert asyncio.run(fetch(11_000)) == len(MP4)        # a guess a little over is only a guess


# ---- Invidious ------------------------------------------------------------------------------

def _fmt(itag, kind, clen, size=None, bitrate=0, tags=None, host="https://inv.example"):
    url = f"{host}/videoplayback?itag={itag}&host=rr1---sn.googlevideo.com"
    if tags:
        url += f"&xtags={tags}"
    fmt = {"itag": str(itag), "type": kind, "clen": str(clen), "bitrate": str(bitrate), "url": url}
    if size:
        fmt["size"] = size
    return fmt


AVC, AAC = 'video/mp4; codecs="avc1.4D401F"', 'audio/mp4; codecs="mp4a.40.2"'
FORMATS = [
    _fmt(160, AVC, 2_000_000, "256x144"), _fmt(134, AVC, 8_000_000, "640x360"),
    _fmt(136, AVC, 26_000_000, "1280x720"), _fmt(137, AVC, 80_000_000, "1920x1080"),
    _fmt(248, 'video/webm; codecs="vp9"', 20_000_000, "1920x1080"),
    _fmt(399, 'video/mp4; codecs="av01.0.08M.08"', 15_000_000, "1920x1080"),
    _fmt(139, 'audio/mp4; codecs="mp4a.40.5"', 1_300_000, bitrate=50_000, tags="acont%3Doriginal%3Alang%3Den"),
    _fmt(140, AAC, 3_400_000, bitrate=130_000, tags="acont%3Ddubbed-auto%3Alang%3Dde"),
    _fmt(140, AAC, 3_449_447, bitrate=130_000, tags="acont%3Doriginal%3Alang%3Den"),
    _fmt(251, 'audio/webm; codecs="opus"', 3_000_000, bitrate=140_000),
]
MB = 1024 * 1024


def test_the_tallest_h264_that_fits_with_the_videos_own_sound():
    picture, sound = resolvers._invidious_pick(FORMATS, 48 * MB)
    assert picture["itag"] == "136" and sound["clen"] == "3449447"
    picture, _ = resolvers._invidious_pick(FORMATS, 2000 * MB)
    assert picture["itag"] == "137"
    picture, _ = resolvers._invidious_pick(FORMATS, 2000 * MB, max_height=720)
    assert picture["itag"] == "136"
    picture, _ = resolvers._invidious_pick(FORMATS, 12 * MB)
    assert picture["itag"] == "134"
    picture, _ = resolvers._invidious_pick(FORMATS, 2000 * MB, height=360, exact=True)
    assert picture["itag"] == "134"
    picture, _ = resolvers._invidious_pick(FORMATS, 2000 * MB, height=480)       # a cap: the best under it
    assert picture["itag"] == "134"


def test_nothing_that_fits_is_too_big_and_nothing_usable_is_an_error():
    with pytest.raises(resolvers.ProviderFailed) as failed:
        resolvers._invidious_pick(FORMATS, 4 * MB)
    assert failed.value.kind == "too_big"
    for formats in ([], [f for f in FORMATS if "audio/mp4" not in f["type"]],
                    [f for f in FORMATS if "avc1" not in f["type"]]):
        with pytest.raises(resolvers.ProviderFailed) as failed:
            resolvers._invidious_pick(formats, 48 * MB)
        assert failed.value.kind == "error"
    with pytest.raises(resolvers.ProviderFailed) as failed:
        resolvers._invidious_pick(FORMATS, 48 * MB, height=480, exact=True)
    assert failed.value.kind == "error"


def test_a_tall_video_is_measured_by_its_height():
    assert resolvers._invidious_height({"size": "720x1280", "resolution": "1280p", "qualityLabel": "720p"}) == 1280
    assert resolvers._invidious_height({"resolution": "360p"}) == 360
    assert resolvers._invidious_height({}) == 0


def test_only_open_https_instances_are_taken_from_the_directory():
    listed = [
        ["a.example", {"type": "https", "api": True, "uri": "https://a.example/"}],
        ["b.example", {"type": "https", "api": False, "uri": "https://b.example"}],
        ["c.onion", {"type": "onion", "api": True, "uri": "http://c.onion"}],
        ["d.example", {"type": "https", "api": True, "uri": "https://d.example/path"}],
        ["e.example", {"type": "https", "api": None, "uri": "https://e.example"}],
        ["f.example", {"type": "https", "api": True, "uri": "https://f.example:8443"}],
        "nonsense", ["g.example"],
    ]
    assert resolvers._invidious_from_directory(listed) == ["https://a.example", "https://f.example:8443"]
    assert resolvers._invidious_from_directory({"not": "a list"}) == []
    many = [[f"h{n}", {"type": "https", "api": True, "uri": f"https://h{n}.example"}] for n in range(20)]
    assert len(resolvers._invidious_from_directory(many)) == resolvers.INVIDIOUS_DIRECTORY_MAX


def test_a_file_is_only_ever_asked_of_the_instance_itself():
    local = resolvers._invidious_local
    assert local("https://inv.example", "https://inv.example/videoplayback?itag=18&host=rr1.googlevideo.com") \
        == "https://inv.example/videoplayback?itag=18&host=rr1.googlevideo.com"
    assert local("https://inv.example", "https://rr1---sn.googlevideo.com/videoplayback?itag=18&x=1") \
        == "https://inv.example/videoplayback?itag=18&x=1&host=rr1---sn.googlevideo.com"
    assert local("https://inv.example", "https://169.254.169.254/videoplayback?itag=18") \
        == "https://inv.example/videoplayback?itag=18"
    for odd in ("https://inv.example/latest/meta-data?x=1", "https://inv.example/videoplayback", "file:///etc/passwd"):
        with pytest.raises(resolvers.ProviderFailed):
            local("https://inv.example", odd)


class _Answer:
    def __init__(self, status, data):
        self.status_code, self._data = status, data

    def json(self):
        if isinstance(self._data, Exception):
            raise self._data
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPError(f"HTTP {self.status_code}")


def _invidious(monkeypatch, answers, files=None, hosts=("https://inv.example",)):
    """Instances whose API answers `answers[host]` and whose files are `files(url)`."""
    calls = {"asked": [], "fetched": [], "joined": 0}
    monkeypatch.setattr(resolvers, "INVIDIOUS_INSTANCES", list(hosts))
    monkeypatch.setattr(resolvers, "INVIDIOUS_DIRECTORY", "")

    async def get(url, **kwargs):
        calls["asked"].append(url)
        return answers[url.split("/api/")[0]]

    async def stream_to_file(url, path, limit=0, headers=None):
        calls["fetched"].append(url)
        sent = files(url) if files else None
        if isinstance(sent, BaseException):
            raise sent
        size = 26_000_000 if "itag=136" in url else 3_449_447 if "itag=140" in url else 8_000_000
        with open(path, "wb") as out:
            out.write(MP4 if sent is None else sent)
            if sent is None:
                out.truncate(size)
        return size

    async def mux(picture, sound, out):
        calls["joined"] += 1
        with open(out, "wb") as handle:
            handle.write(MP4)

    monkeypatch.setattr(resolvers, "_get", get)
    monkeypatch.setattr(resolvers.net, "stream_to_file", stream_to_file)
    monkeypatch.setattr(resolvers, "_mux", mux)
    return calls


def _video(**changes):
    return _Answer(200, {"lengthSeconds": 213, "liveNow": False, "adaptiveFormats": FORMATS, **changes})


def test_picture_and_sound_are_fetched_from_the_instance_and_joined(monkeypatch, work_dir):
    calls = _invidious(monkeypatch, {"https://inv.example": _video()})
    found = asyncio.run(resolvers._invidious_provider("youtube")(WATCH))
    assert calls["asked"] == ["https://inv.example/api/v1/videos/jNQXAC9IVRw"]
    assert sorted(calls["fetched"]) == sorted([
        "https://inv.example/videoplayback?itag=136&host=rr1---sn.googlevideo.com",
        "https://inv.example/videoplayback?itag=140&host=rr1---sn.googlevideo.com&xtags=acont%3Doriginal%3Alang%3Den"])
    assert calls["joined"] == 1 and found.provider == "invidious"
    assert os.listdir(work_dir) == [os.path.basename(found.items[0].path)]     # the two parts are gone


def test_the_next_instance_is_asked_when_one_is_blocked_or_cuts_the_file_short(monkeypatch, work_dir):
    calls = _invidious(
        monkeypatch,
        {"https://a.example": _Answer(403, ValueError("not json")),
         "https://b.example": _video(), "https://c.example": _video()},
        files=lambda url: MP4 if "b.example" in url else None,           # b sends 4 KB of a 26 MB file
        hosts=("https://a.example", "https://b.example", "https://c.example"))
    found = asyncio.run(resolvers._invidious_provider("youtube")(WATCH))
    assert [url.split("/api/")[0] for url in calls["asked"]] == \
        ["https://a.example", "https://b.example", "https://c.example"]
    assert found.items[0].path and resolvers._invidious_last_good == "https://c.example"
    assert len(os.listdir(work_dir)) == 1


def test_what_an_instance_says_about_a_video(monkeypatch, work_dir):
    for answer, kind in ((_Answer(404, {"error": "Video unavailable"}), "missing"),
                         (_Answer(200, {"error": "Sign in to confirm you're not a bot"}), "blocked"),
                         (_Answer(429, {"error": "Too many requests"}), "blocked"),
                         (_Answer(500, {"error": "Something broke"}), "error"),
                         (_video(liveNow=True), "missing"),
                         (_video(lengthSeconds=10 ** 6), "too_big"),
                         (_video(adaptiveFormats=[]), "error")):
        calls = _invidious(monkeypatch, {"https://inv.example": answer})
        with pytest.raises(resolvers.ProviderFailed) as failed:
            asyncio.run(resolvers._invidious_provider("youtube")(WATCH))
        assert failed.value.kind == kind, (answer._data, failed.value)
        assert calls["fetched"] == [] and os.listdir(work_dir) == []


def test_one_failed_part_stops_the_other_and_leaves_nothing(monkeypatch, work_dir):
    _invidious(monkeypatch, {"https://inv.example": _video()},
               files=lambda url: httpx.ReadError("cut") if "itag=140" in url else None)
    with pytest.raises(resolvers.ProviderFailed):
        asyncio.run(resolvers._invidious_provider("youtube")(WATCH))
    assert os.listdir(work_dir) == []


def test_lookup_asks_the_instance_and_fetches_nothing(monkeypatch, work_dir):
    calls = _invidious(monkeypatch, {"https://inv.example": _video()})
    monkeypatch.setattr(resolvers, "PROVIDERS",
                        {"youtube": [("invidious:youtube", resolvers._invidious_provider("youtube"))]})
    assert asyncio.run(resolvers.lookup("youtube", WATCH)) == "present"
    assert calls["fetched"] == [] and calls["joined"] == 0


def test_the_directory_adds_instances_behind_the_configured_ones(monkeypatch):
    monkeypatch.setattr(resolvers, "INVIDIOUS_INSTANCES", ["https://mine.example"])
    monkeypatch.setattr(resolvers, "INVIDIOUS_DIRECTORY", "https://directory.example/instances.json")
    monkeypatch.setattr(resolvers, "_invidious_listed", [])
    monkeypatch.setattr(resolvers, "_invidious_listed_next", 0.0)
    asked = []

    async def get(url, **kwargs):
        asked.append(url)
        if len(asked) > 1:
            raise httpx.ConnectError("down")
        return _Answer(200, [["a", {"type": "https", "api": True, "uri": "https://a.example"}],
                             ["m", {"type": "https", "api": True, "uri": "https://mine.example"}]])

    monkeypatch.setattr(resolvers, "_get", get)
    assert asyncio.run(resolvers._invidious_hosts()) == ["https://mine.example", "https://a.example"]
    assert asyncio.run(resolvers._invidious_hosts()) == ["https://mine.example", "https://a.example"]
    assert len(asked) == 1                                   # not read again for hours
    monkeypatch.setattr(resolvers, "_invidious_listed_next", 0.0)
    assert asyncio.run(resolvers._invidious_hosts()) == ["https://mine.example", "https://a.example"]
    assert len(asked) == 2                                   # unreadable: the list from last time stays


# ---- the join itself, with the real ffmpeg -----------------------------------------------------

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")


@needs_ffmpeg
def test_ffmpeg_joins_a_picture_and_a_sound_into_one_mp4(tmp_path):
    picture, sound, out = (str(tmp_path / name) for name in ("p.mp4", "s.m4a", "out.mp4"))
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=64x64:rate=5:duration=1",
                    "-c:v", "mpeg4", "-an", picture], check=True)
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
                    "-c:a", "aac", "-vn", sound], check=True)
    asyncio.run(resolvers._mux(picture, sound, out))
    assert resolvers._looks_like_video(out)
    streams = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0", out],
                             capture_output=True, text=True, check=True).stdout.split()
    assert sorted(streams) == ["audio", "video"]


@needs_ffmpeg
def test_a_playlist_sent_as_a_video_makes_ffmpeg_open_nothing(tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("the data key\n")
    playlist = tmp_path / "p.mp4"
    playlist.write_text(f"#EXTM3U\n#EXT-X-MEDIA-SEQUENCE:0\n#EXTINF:1.0,\nfile://{secret}\n#EXT-X-ENDLIST\n")
    sound = str(tmp_path / "s.m4a")
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
                    "-c:a", "aac", "-vn", sound], check=True)
    assert not resolvers._looks_like_video(str(playlist))        # refused before ffmpeg is started...
    with pytest.raises(resolvers.ProviderFailed):                # ...and by ffmpeg itself, told it is MP4
        asyncio.run(resolvers._mux(str(playlist), sound, str(tmp_path / "out.mp4")))
