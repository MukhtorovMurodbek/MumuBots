"""A Pinterest video pin must not come back as its cover image.

    python3 tests/test_pinterest.py

This is the same shape of bug as the silent-audio one `test_formats.py`
guards, and it is the worst shape a download bug can take: nothing failed.
Pinterest stopped emitting `og:video` on pin pages, so the page-scraping
provider found no video, fell through to `og:image` -- which is present,
because it is the video's own cover frame -- and **succeeded** with a photo.
A provider that succeeds ends the chain, so yt-dlp was never reached. The
user asked for a video, waited, and got a still, and nothing anywhere said so.

The rules this pins down:

1. A pin whose payload has a video must resolve to a **video** item.
2. Every kind of video pin counts: a plain one carries `videos.video_list`,
   a story / idea pin hides the same list inside `story_pin_data`.
3. Never an `.m3u8`. Those sit in the same list as the mp4s and are manifests
   rather than files, so uploading one to Telegram sends a few hundred bytes
   of text named `.mp4`.
4. A photo pin still resolves to a photo, at full size.
5. The API provider runs *before* the page scrape, since it is the only one
   that can tell the two apart.
6. Country subdomains are Pinterest links. `co.pinterest.com` was answered
   with "that is not a link I recognise", which reads as the bot not
   supporting Pinterest at all.

Nothing here touches the network: `_get` is replaced with canned payloads
taken from real pins.
"""
import asyncio
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "stubs"))
MIRROR = ROOT / ".mirror" / "downloader_bot"
sys.path.insert(0, str(MIRROR if MIRROR.exists()
                       else ROOT.parent / "downloader_bot"))
sys.path.insert(1, str(ROOT.parent / "shared"))   # beside the real folder

import platforms   # noqa: E402
import resolvers   # noqa: E402

failures = []


def check(name, ok, detail=""):
    print(("  ok  " if ok else "  FAIL  ") + name + (f" -- {detail}" if not ok and detail else ""))
    if not ok:
        failures.append(name)


# The shapes Pinterest actually returns, trimmed to the fields that matter.
VIDEO_LIST = {
    "V_720P": {"url": "https://v1.pinimg.com/videos/mc/720p/aa.mp4",
               "width": 1080, "height": 1920},
    "V_HLSV4": {"url": "https://v1.pinimg.com/videos/mc/hls/aa.m3u8",
                "width": 1080, "height": 1920},
    "V_HLSV3_MOBILE": {"url": "https://v1.pinimg.com/videos/mc/hls/aa.m3u8",
                       "width": 1080, "height": 1920},
}
SMALL_AND_LARGE = {
    "V_EXP3": {"url": "https://v1.pinimg.com/videos/mc/expMp4/bb_t1.mp4",
               "width": 240, "height": 426},
    "V_EXP7": {"url": "https://v1.pinimg.com/videos/mc/720p/bb.mp4",
               "width": 640, "height": 1138},
}
IMAGES = {"orig": {"url": "https://i.pinimg.com/originals/cc/dd/ee/photo.jpg",
                   "width": 1080, "height": 1620},
          "236x": {"url": "https://i.pinimg.com/236x/cc/dd/ee/photo.jpg",
                   "width": 236, "height": 354}}

PAYLOADS = {
    "plain_video": {"videos": {"video_list": VIDEO_LIST}, "images": IMAGES},
    "story_video": {"story_pin_data": {"pages": [
        {"blocks": [{"type": "story_pin_video_block",
                     "video": {"video_list": SMALL_AND_LARGE}},
                    {"type": "story_pin_paragraph_block"}]}]}, "images": IMAGES},
    "two_page_story": {"story_pin_data": {"pages": [
        {"blocks": [{"video": {"video_list": VIDEO_LIST}}]},
        {"blocks": [{"video": {"video_list": SMALL_AND_LARGE}}]}]}, "images": IMAGES},
    "photo": {"images": IMAGES},
    # The pin the owner's friend shared: a story pin whose only video is an
    # HLS manifest, and whose `images` is the cover frame.
    "hls_only_story": {"story_pin_data": {"pages": [
        {"blocks": [{"video": {"video_list": {
            "V_HLSV3_MOBILE": VIDEO_LIST["V_HLSV3_MOBILE"]}}}]}]}, "images": IMAGES},
    "empty": {},
}


class FakeResponse:
    def __init__(self, payload, url):
        self._payload = payload
        self.url = url
        self.status_code = 200
        self.text = ""

    def raise_for_status(self):
        return None

    def json(self):
        return {"resource_response": {"data": self._payload}}


def install(kind):
    """Point resolvers._get at one canned payload."""
    async def fake_get(url, **kwargs):
        return FakeResponse(PAYLOADS[kind], url)
    resolvers._get = fake_get


async def resolve_kind(kind):
    install(kind)
    return await resolvers._pin_api("https://www.pinterest.com/pin/123456789/")


async def scenario():
    real_get = resolvers._get
    try:
        print("=== a video pin resolves to a video ===")
        for kind in ("plain_video", "story_video", "two_page_story"):
            resolved = await resolve_kind(kind)
            kinds = [item.kind for item in resolved.items]
            check(f"{kind}: every item is a video",
                  kinds and all(k == "video" for k in kinds), str(kinds))
            check(f"{kind}: no manifest is offered as a file",
                  all(not (item.url or "").endswith(".m3u8") for item in resolved.items),
                  str([item.url for item in resolved.items]))

        resolved = await resolve_kind("plain_video")
        check("the biggest mp4 wins",
              resolved.items[0].url.endswith("720p/aa.mp4"), resolved.items[0].url)

        resolved = await resolve_kind("story_video")
        check("and it wins inside a story block too",
              resolved.items[0].url.endswith("720p/bb.mp4"), resolved.items[0].url)

        resolved = await resolve_kind("two_page_story")
        check("a story pin of two pages gives two files",
              len(resolved.items) == 2, str(len(resolved.items)))
        check("with distinct filenames, or Telegram shows one name twice",
              len({item.filename for item in resolved.items}) == 2,
              str([item.filename for item in resolved.items]))

        print("\n=== a video served only as a stream is not answered with its cover ===")
        install("hls_only_story")
        try:
            await resolvers._pin_api("https://www.pinterest.com/pin/1196337405501867/")
            check("an HLS-only video pin is refused by the API route", False, "it returned something")
        except resolvers.ProviderFailed as exc:
            check("an HLS-only video pin is refused by the API route", True)
            check("and rules out the page scraper, which would return the cover frame",
                  "pinterest_og" in exc.skip, str(exc.skip))

        calls = []

        def provider(name, outcome):
            async def run(url):
                calls.append(name)
                if isinstance(outcome, Exception):
                    raise outcome
                return outcome
            return name, run

        video = resolvers.Resolved("pinterest", "ytdlp", [resolvers.MediaItem("video", url="v.mp4")])
        cover = resolvers.Resolved("pinterest", "og", [resolvers.MediaItem("photo", url="cover.jpg")])
        real_ordered = resolvers._ordered
        resolvers._ordered = lambda platform: [
            provider("pinterest_api", resolvers.ProviderFailed("stream only", "error", skip=("pinterest_og",))),
            provider("pinterest_og", cover),
            provider("ytdlp:pinterest", video),
        ]
        try:
            resolved = await resolvers.resolve("pinterest", "https://pin.it/6LNqzHUX0")
        finally:
            resolvers._ordered = real_ordered
        check("the chain skips the page scraper after that refusal",
              "pinterest_og" not in calls, str(calls))
        check("and reaches yt-dlp, which returns the video",
              [item.kind for item in resolved.items] == ["video"], str(calls))

        print("\n=== a photo pin still resolves to a photo ===")
        resolved = await resolve_kind("photo")
        check("one photo", [item.kind for item in resolved.items] == ["photo"])
        check("at full size, not a thumbnail",
              resolved.items[0].url == IMAGES["orig"]["url"], resolved.items[0].url)

        print("\n=== a pin with nothing in it fails rather than inventing ===")
        install("empty")
        try:
            await resolvers._pin_api("https://www.pinterest.com/pin/123456789/")
            check("an empty pin is refused", False, "it returned something")
        except resolvers.ProviderFailed as exc:
            check("an empty pin is refused", True)
            check("and says the post looks gone rather than 'error'",
                  exc.kind == "missing", exc.kind)
    finally:
        resolvers._get = real_get


def main() -> int:
    asyncio.run(scenario())

    print("\n=== the chain asks the endpoint before scraping the page ===")
    names = [name for name, _ in resolvers.PROVIDERS["pinterest"]]
    check("pinterest_api is registered", "pinterest_api" in names, str(names))
    check("and it comes before pinterest_og",
          names.index("pinterest_api") < names.index("pinterest_og"), str(names))
    check("with yt-dlp still last", names[-1].startswith("ytdlp:"), str(names))

    print("\n=== which links count as Pinterest ===")
    accepted = [
        "https://www.pinterest.com/pin/664281013778109217/",
        "https://pinterest.com/pin/664281013778109217/",
        "https://www.pinterest.ca/pin/441282463481903715/",
        "https://co.pinterest.com/pin/824721750502199491/",
        "https://in.pinterest.com/pin/664281013778109217/",
        "https://br.pinterest.com/pin/1084663891475263837/",
        "https://pin.it/2yLmNoP",
        "https://www.pinterest.com/pin/a-slug-here--2885187256207927",
    ]
    for url in accepted:
        detected = platforms.detect_platform(url)
        check(f"{url[:52]:52} -> pinterest",
              detected is not None and detected[0] == "pinterest",
              str(detected))
    for url in ("https://notpinterest.com/pin/1/", "https://example.com/pinterest/1"):
        check(f"{url[:52]:52} -> not pinterest",
              (platforms.detect_platform(url) or ("", ""))[0] != "pinterest")

    print("\n=== no other provider guesses a video from a cover image ===")
    # The same audit, as a standing rule rather than a one-off reading. Every
    # provider that decides from metadata rather than from an explicit field
    # has to be able to say "I cannot tell" -- because a provider that
    # succeeds ends the chain, and the route that would have worked is never
    # tried. These are the four that get it right and the one that was fixed.
    import inspect

    tnktok = inspect.getsource(resolvers._tt_tnktok)
    check("tnktok refuses rather than returning the cover frame",
          'MediaItem("photo"' not in tnktok,
          "it falls back to og:image on a post it was reading og:video for")
    check("_ig_instafix still checks what the server actually serves",
          "content-type" in inspect.getsource(resolvers._ig_instafix).lower())
    check("_ig_downloadgram still drops cover frames when a video is present",
          'i.kind == "video"' in inspect.getsource(resolvers._ig_downloadgram))
    check("_tt_tikwm still decides from the explicit images field",
          'data.get("images")' in inspect.getsource(resolvers._tt_tikwm))
    check("_tw_api still decides from the explicit media type",
          '"animated_gif"' in inspect.getsource(resolvers._tw_api))
    # _pin_og keeps its og:image path on purpose: a photo pin is the common
    # case and that path is the right answer for it. What makes it safe is
    # that _pin_api runs first and is the one that can tell the two apart --
    # which the ordering check above is what guarantees.
    check("_pin_og is not first in the chain, which is what makes its "
          "og:image fallback safe",
          [name for name, _ in resolvers.PROVIDERS["pinterest"]][0] != "pinterest_og")

    print("\n=== the pin id is found wherever it hides ===")
    for url, expected in (
        ("https://www.pinterest.com/pin/664281013778109217/", "664281013778109217"),
        ("https://in.pinterest.com/pin/123/", "123"),
        ("https://pinterest.com/pin/dive-into-serenity--2885187256207927", "2885187256207927"),
    ):
        found = resolvers._PIN_ID_RE.search(url)
        check(f"id of {url[:48]:48} is {expected}",
              found is not None and found.group(1) == expected,
              found.group(1) if found else "no match")

    print()
    if failures:
        print(f"FAILURES ({len(failures)}):")
        for name in failures:
            print("  -", name)
        return 1
    print("a video pin comes back as a video.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
