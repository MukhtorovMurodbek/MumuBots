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
