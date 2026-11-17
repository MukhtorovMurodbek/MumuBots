"""2.1.7's fix, ported: the one video site is read afresh before its file is fetched."""
from unittest import mock

import pytest

from conftest import load, unadvertised

SITE, SHORT, MEDIA = unadvertised()

DOWNLOADER = "downloader_bot"
MB = 1024 * 1024
LINK = f"https://www.{SITE}.com/watch?v=abcdefghijk"
FORMATS = {"duration": 600, "formats": [
    {"format_id": "134", "vcodec": "avc1.4d401e", "acodec": "none", "height": 360, "filesize": 3 * MB},
    {"format_id": "136", "vcodec": "avc1.4d401f", "acodec": "none", "height": 720, "filesize": 30 * MB},
    {"format_id": "140", "vcodec": "none", "acodec": "mp4a.40.2", "abr": 129, "filesize": 2 * MB},
]}


class _FakeYDL:
    log = []

    def __init__(self, opts):
        self.opts = opts

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def extract_info(self, url, download=False):
        self.log.append(("extract", download, self.opts.get("format")))
        return dict(FORMATS, webpage_url=url)

    def process_ie_result(self, info, download=False):
        self.log.append(("process", download, self.opts.get("format")))
        return info

    def prepare_filename(self, info):
        return self.opts["outtmpl"].replace("%(ext)s", "mp4")


@pytest.mark.parametrize("url,second", [(LINK, ("extract", True, "136+140")),
                                        ("https://www.instagram.com/reel/abc/", ("process", True, "136+140"))])
def test_the_fetch_reads_that_site_afresh_and_others_as_before(tmp_path, url, second):
    video = load(DOWNLOADER, "video")
    _FakeYDL.log = []
    out = str(tmp_path / "x.%(ext)s")
    (tmp_path / "x.mp4").write_bytes(b"0")
    video._download(mock.Mock(YoutubeDL=_FakeYDL), url, 48, out, None, None, 720, True)
    assert _FakeYDL.log == [("extract", False, video._format_selector(48)), second]


def test_a_pick_from_the_picker_is_fetched_from_a_fresh_read(tmp_path):
    video = load(DOWNLOADER, "video")
    video._remember_read(LINK, dict(FORMATS))
    _FakeYDL.log = []
    out = str(tmp_path / "y.%(ext)s")
    (tmp_path / "y.mp4").write_bytes(b"0")
    video._download(mock.Mock(YoutubeDL=_FakeYDL), LINK, 48, out, None, None, 720, True)
    assert _FakeYDL.log == [("extract", True, "136+140")]


@pytest.mark.parametrize("url,site", [
    ("https://www.instagram.com/reel/abc/", "instagram"),
    ("https://scontent-fra5-1.cdninstagram.com/v/t51.jpg", "instagram"),
    ("https://www.tiktok.com/@a/video/1", "tiktok"),
    ("https://v16-webapp.tiktokcdn.com/x/video.mp4", "tiktok"),
    ("https://www.reddit.com/r/instagram/comments/abc/x/", "reddit"),
    ("https://v.redd.it/abc123", "reddit"),
    ("https://x.com/a/status/1", "twitter"),
    ("https://mobile.twitter.com/a/status/1", "twitter"),
    ("https://example.org/?next=https://twitter.com/a", "other"),
    ("https://evil.example/x.com/a", "other"),
    ("https://pin.it/abc", "pinterest"),
    ("https://br.pinterest.com/pin/1/", "pinterest"),
    ("not a link", "other"),
])
def test_a_link_s_site_is_read_from_its_host(url, site):
    assert load(DOWNLOADER, "video")._site_of(url) == site


def test_the_unadvertised_site_and_its_short_links_keep_their_site():
    video = load(DOWNLOADER, "video")
    assert video._site_of(f"https://www.{SITE}.com/watch?v=abcdefghijk") == SITE
    assert video._site_of(f"https://{SHORT}/abcdefghijk") == SITE
    assert video._site_of(f"https://www.reddit.com/r/{SITE}/comments/x/") == "reddit"
