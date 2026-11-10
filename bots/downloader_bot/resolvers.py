"""Turning a pasted link into files, through whichever route is still working.

WHY THIS EXISTS
    Until v1.2.1 this bot had exactly one way to get a video: yt-dlp, running
    inside the container, fetching from the site directly. That works from a
    phone and does not work from Railway, and the difference is not the code
    -- it is the IP address. Instagram, YouTube and increasingly TikTok decide
    what to serve partly from *where* the request comes from, every cloud
    host's address sits in a published datacenter range, and those ranges get
    a login page where a home connection gets the post. v1.1.3 diagnosed this
    correctly and concluded there were two fixes, cookies or a residential
    proxy, both of which need something the bot cannot supply itself. So
    Instagram simply stopped working, and said so politely.

    There is a third option, and it is the one that holds: do not make the
    request from here at all. The "fix the embed" services and the public
    downloader APIs run their own fetching infrastructure -- their addresses,
    their cookie pools, their problem -- and hand back either a direct CDN URL
    or a copy proxied through themselves. Whether *this* container is on a
    blocked range stops mattering.

    The catch is that any one of those services is mortal. They get bought,
    rate-limited, sued (vxtiktok, as of this writing, answers every request
    with "due to a legal request, this service is no longer available"), or
    just stop resolving in DNS (ddinstagram and kkinstagram both did). A bot
    built on one of them is a bot that breaks in a month with no warning.

    Hence a chain, per platform, with the bot keeping score. Every provider is
    tried in turn until one produces media; whichever one worked is
    remembered, whichever one failed is remembered too, and a provider that
    keeps failing is moved to the back of the queue and retried more and more
    rarely instead of being taken out of service. Nothing here needs a human
    to notice a provider has died. Adding a replacement is a function and one
    line in PROVIDERS.

WHAT IS NOT SOLVED HERE
    Nothing makes a private account public, and nothing makes a deleted post
    come back. When every provider says no, the user gets one sentence saying
    which of those two kinds of "no" it looked like, not five stack traces.

    And the providers were verified from a residential connection, because
    that is where this was written. A service that answers here could still be
    challenged from Railway's range -- which is exactly the failure the health
    scoring is built to absorb: the first user to hit it pays a few seconds,
    the chain moves on, and `/providers` says what happened.
"""
from __future__ import annotations

import asyncio
import contextvars
import base64
import json
import logging
import os
import re
import shutil
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urljoin, urlparse, urlsplit

import httpx

import net
from net import CRAWLER_UA, FetchError

logger = logging.getLogger(__name__)

# How long any single provider gets before the chain gives up on it and tries
# the next. Deliberately short: the whole point of a chain is that waiting out
# a dead provider costs more than moving on, and four providers at 15 s each
# is still inside what a user will sit through.
PROVIDER_TIMEOUT_S = float(os.environ.get("DBOT_PROVIDER_TIMEOUT", "20"))

# ...except yt-dlp, whose route is not a lookup but the download itself: it
# fetches the video while it resolves it, and there is no second step after it
# (the other routes hand back a URL, and the file is fetched after the chain
# has ended). A route that timed out counts as failed, so the one that was
# working would be rested; so it is given time for the download: this long for a
# download under the free ceiling, and for one past it, this many more seconds
# for every megabyte above the ceiling (2 MB/s is the pace it is asked to
# keep). A route that stalls is stopped sooner than that, by yt-dlp's own
# socket timeout.
YTDLP_TIMEOUT_S = float(os.environ.get("DBOT_YTDLP_TIMEOUT", "120"))
YTDLP_SECONDS_PER_MB = float(os.environ.get("DBOT_YTDLP_SECONDS_PER_MB", "0.5"))

# After this many consecutive failures a provider starts being skipped -- not
# dropped. See _ordered(): it goes to the back of the queue and is still tried
# if everything ahead of it fails, which is what lets a service that comes
# back from the dead be noticed without anybody redeploying.
COOLDOWN_AFTER_FAILURES = int(os.environ.get("DBOT_PROVIDER_COOLDOWN_AFTER", "3"))
COOLDOWN_BASE_S = float(os.environ.get("DBOT_PROVIDER_COOLDOWN_BASE", "120"))
COOLDOWN_MAX_S = float(os.environ.get("DBOT_PROVIDER_COOLDOWN_MAX", "3600"))


# ---------------------------------------------------------------------------
# What a resolver hands back
# ---------------------------------------------------------------------------

@dataclass
class MediaItem:
    """One file. Either a URL to fetch (`url`) or something already on disk
    (`path`) -- yt-dlp produces the second kind because it does its own
    downloading and there is no sense making it hand back a URL it has
    already spent the request on."""
    kind: str                      # "video" | "photo" | "audio"
    url: str | None = None
    # Other URLs for the *same* file, tried in order if `url` will not serve.
    # tikwm hands back both TikTok's own CDN and a copy proxied through
    # tikwm: the first is faster when it works and the second is the entire
    # reason the provider is useful when it does not.
    alt_urls: list[str] = field(default_factory=list)
    path: str | None = None
    filename: str = "download"
    headers: dict = field(default_factory=dict)


@dataclass
class Resolved:
    platform: str
    provider: str
    items: list[MediaItem]
    title: str | None = None
    author: str | None = None

    @property
    def has_video(self) -> bool:
        return any(i.kind == "video" for i in self.items)


class ProviderFailed(Exception):
    """This provider could not do it. Try the next one.

    `kind` is a hint for the sentence the user eventually sees if *every*
    provider fails: "blocked" (the source turned the server away), "missing"
    (the post looks gone or private), "too_big", or "error".
    """

    def __init__(self, message: str, kind: str = "error", skip=()):
        super().__init__(message)
        self.kind = kind
        # Providers that cannot help once this one has failed this way. A
        # provider that learns the answer is "a video" knows the page scraper
        # behind it would reply with a picture of that video, and a provider
        # that succeeds ends the chain -- so it has to be able to say "not
        # that one" rather than merely "not me".
        self.skip = tuple(skip)


class NothingWorked(Exception):
    """Every provider for this platform failed. Carries the per-provider
    reasons for the log and the best guess at which sentence to show."""

    def __init__(self, platform: str, failures: dict[str, ProviderFailed]):
        self.platform = platform
        self.failures = failures
        super().__init__(
            f"{platform}: " + "; ".join(f"{n}: {e}" for n, e in failures.items())
        )

    @property
    def kind(self) -> str:
        """The most specific verdict any provider reached.

        "missing" wins over "blocked" wins over the rest, because a provider
        that got far enough to be told "this post does not exist" learned
        something the ones that were bounced at the door did not.
        """
        kinds = {e.kind for e in self.failures.values()}
        for preferred in ("missing", "too_big", "blocked"):
            if preferred in kinds:
                return preferred
        return "error"


# ---------------------------------------------------------------------------
# Keeping score
# ---------------------------------------------------------------------------

@dataclass
class Health:
    ok: int = 0
    failed: int = 0
    streak: int = 0                 # consecutive failures
    last_ok: float | None = None    # unix seconds
    last_fail: float | None = None
    last_error: str | None = None

    @property
    def cooling_until(self) -> float:
        """When this provider becomes a first-class citizen again.

        Exponential from the third consecutive failure, capped at an hour, so
        a service that went away this morning is asked about roughly hourly
        rather than on every single request -- and a service having a bad
        minute is back in the rotation two minutes later.
        """
        if self.streak < COOLDOWN_AFTER_FAILURES or self.last_fail is None:
            return 0.0
        over = self.streak - COOLDOWN_AFTER_FAILURES
        return self.last_fail + min(COOLDOWN_BASE_S * (2 ** over), COOLDOWN_MAX_S)


_health: dict[str, Health] = {}
_health_dirty: set[str] = set()


def health(name: str) -> Health:
    return _health.setdefault(name, Health())


def _record_ok(name: str) -> None:
    h = health(name)
    h.ok += 1
    h.streak = 0
    h.last_ok = time.time()
    h.last_error = None
    _health_dirty.add(name)


def _record_fail(name: str, error: str) -> None:
    h = health(name)
    h.failed += 1
    h.streak += 1
    h.last_fail = time.time()
    h.last_error = error[:300]
    _health_dirty.add(name)


def take_dirty() -> dict[str, Health]:
    """The rows that changed since the last flush, and clears the flag.

    Health is kept in memory and written to Postgres on a timer rather than on
    every attempt, for the same reason activity events are batched: this bot's
    database is a shared one and a write per download is a write per download
    forever. Losing the last minute of counters in a crash costs nothing --
    they are advice about which provider to try first, not user data.
    """
    dirty = {name: _health[name] for name in _health_dirty if name in _health}
    _health_dirty.clear()
    return dirty


def load(rows) -> None:
    """Seed from the database at startup, so a redeploy does not go back to
    hammering a provider that has been dead for a week."""
    for row in rows:
        name = row["provider"]
        _health[name] = Health(
            ok=row.get("ok_count") or 0,
            failed=row.get("fail_count") or 0,
            streak=row.get("consecutive_fails") or 0,
            last_ok=row.get("last_ok_at"),
            last_fail=row.get("last_fail_at"),
            last_error=row.get("last_error"),
        )


# ---------------------------------------------------------------------------
# Small shared helpers
# ---------------------------------------------------------------------------

_META_OPEN_RE = re.compile("<meta", re.IGNORECASE)
# No real meta tag is anywhere near this long; one that is, is not read.
_META_TAG_MAX = 4096


def _meta_tags(html: str):
    """Every `<meta ...>` tag of `html`, found in one pass.

    This was the regex `<meta[^>]+>`, which is quadratic on a page made of
    "<meta" with no ">" after it: each opening scans on to the end of the page,
    and an 8 MB page (what a route may read) held the event loop, and with it
    every bot of the combined edition, for about an hour (review, 2026-10-08).
    Here a tag runs to the first ">" after its opening and the next one is
    looked for only after that, so every character is looked at once; and a
    page with no more ">" ends the scan, so the work is linear in the page."""
    pos = 0
    while True:
        opening = _META_OPEN_RE.search(html, pos)
        if opening is None:
            return
        start = opening.start()
        close = html.find(">", start)
        if close < 0:
            return                               # no ">" left: no tag can end
        pos = close + 1                          # nothing starts inside a tag
        if close - start > 5 and close - start < _META_TAG_MAX:
            yield html[start:close + 1]


def _meta(html: str, prop: str) -> str | None:
    """Read one OpenGraph/Twitter-card meta tag's content.

    Attribute order is not guaranteed -- Instagram's own markup puts
    `content=` before `property=` -- so this isolates the whole tag first and
    then pulls `content` out of it, rather than assuming an order.
    """
    wanted = re.compile(rf'(?:property|name)=["\']{re.escape(prop)}["\']', re.I)
    for tag in _meta_tags(html):
        if wanted.search(tag):
            m = re.search(r'content=["\']([^"\']+)["\']', tag, re.I)
            if m:
                return m.group(1).replace("&amp;", "&")
    return None


# What a page or an API answer may weigh before it is refused. Every route
# below reads a small document -- an embed page, a JSON answer -- and a
# provider is somebody else's server: one that answers with a gigabyte would
# otherwise be read whole into this process. Media goes through net.py's
# streaming helpers, never through here.
PAGE_LIMIT_BYTES = int(float(os.environ.get("DBOT_PAGE_LIMIT_MB", "8")) * 1024 * 1024)


async def _page(method: str, url: str, **kwargs) -> httpx.Response:
    """One small document, read under PAGE_LIMIT_BYTES (net.read_page), with
    this module's way of failing: a ProviderFailed, kind "error", that the
    chain moves past. The size is counted *after* decoding, and only gzip and
    deflate are read at all -- see net.body_chunks for the 421 bytes that
    decoded to 512 MB."""
    netloc = urlparse(url).netloc

    def declared_too_big(declared: int):
        return ProviderFailed(f"{netloc} answered with {declared:,} bytes, "
                              "more than a page may be", "error")

    def too_big():
        return ProviderFailed(f"{netloc} kept sending past {PAGE_LIMIT_BYTES:,} bytes", "error")

    try:
        return await net.read_page(method, url, limit=PAGE_LIMIT_BYTES, too_big=too_big,
                                   declared_too_big=declared_too_big, **kwargs)
    except net.Trickle:                         # before BadEncoding, which it is one of
        raise ProviderFailed(f"{netloc} sent its answer in pieces too small to read", "error") from None
    except net.BadEncoding:
        raise ProviderFailed(f"{netloc} answered in an encoding this bot does not read", "error") from None


async def _get(url: str, *, crawler: bool = False, **kwargs) -> httpx.Response:
    headers = dict(kwargs.pop("headers", {}) or {})
    if crawler:
        headers.setdefault("User-Agent", CRAWLER_UA)
    return await _page("GET", url, headers=headers, **kwargs)


async def _post(url: str, **kwargs) -> httpx.Response:
    """The same for a POST (cobalt). An instance is somebody else's server
    like any other provider, and `client().post()` would read a 200 MB answer
    whole: it peaked at 400 MB in the review of 2026-10-08."""
    return await _page("POST", url, **kwargs)


def _ext_of(name: str, default: str) -> str:
    """The extension to give a downloaded file, or `default`.

    The name this reads is not ours and is not the user's either: it comes
    out of whatever a resolver service or a remote URL said the file was
    called. It ends up in `os.path.join(work_dir, f"{uuid}.{ext}")`, where a
    "/" is a directory separator and ".." is a level up -- so anything that
    is not plainly an extension is refused rather than trimmed. The length
    cap was already doing most of this by accident; being explicit about it
    is what makes it a rule instead of a coincidence.
    """
    _, dot, ext = name.rpartition(".")
    # rpartition puts the WHOLE string in the third slot when the separator is
    # absent, so a dotless "video" used to come back as the extension "video".
    # That reached _kind_of, which then found "video" in neither the video nor
    # the audio list and called it a photo -- and a clip sent as a photo is a
    # Telegram error, not a slightly wrong label.
    if not dot:
        return default
    ext = ext.lower()
    return ext if ext and len(ext) <= 5 and ext.isalnum() and ext.isascii() else default


def _kind_of(name: str) -> str:
    ext = _ext_of(name, "")
    if ext in ("mp4", "mov", "webm", "mkv", "m4v"):
        return "video"
    if ext in ("mp3", "m4a", "ogg", "opus", "wav"):
        return "audio"
    return "photo"


# ---------------------------------------------------------------------------
# Instagram
# ---------------------------------------------------------------------------

INSTAGRAM_CODE_RE = re.compile(
    r"instagram\.com/(?:[^/]+/)?(?:reels?|p|tv)/([A-Za-z0-9_-]+)", re.IGNORECASE
)


def instagram_code(url: str) -> str | None:
    m = INSTAGRAM_CODE_RE.search(url)
    return m.group(1) if m else None


def _jwt_payload(token: str) -> dict:
    """downloadgram wraps every media URL in an unsigned-to-us JWT whose
    payload carries the filename and the real source URL. Read, never
    verified -- it is not this bot's token and the signature is between them
    and their own CDN. Only the filename is used, to tell a video from a
    cover image."""
    part = token.split(".")[1]
    part += "=" * (-len(part) % 4)
    return json.loads(base64.urlsafe_b64decode(part))


async def _ig_downloadgram(url: str) -> Resolved:
    """downloadgram.org's public endpoint.

    The most useful property is not that it works -- it is that the media
    comes back proxied through `cdn.downloadgram.org`, so the *second* request
    (actually fetching the file) does not touch Instagram's CDN from this
    container either. Both halves of the problem are somebody else's.

    The response is an obfuscated JavaScript blob that assigns innerHTML; the
    URLs are in there with their forward slashes hex-escaped. Parsed by
    pulling the token links out rather than by pretending to understand the
    JavaScript.
    """
    resp = await _get("https://api.downloadgram.org/media", params={"url": url})
    if resp.status_code == 400:
        raise ProviderFailed("downloadgram rejected the link", "missing")
    resp.raise_for_status()
    body = re.sub(r"\\x([0-9a-fA-F]{2})", lambda m: chr(int(m.group(1), 16)), resp.text)

    tokens: list[str] = []
    for tok in re.findall(r"https://cdn\.downloadgram\.org/\?token=([A-Za-z0-9_.-]+)", body):
        if tok not in tokens:
            tokens.append(tok)
    if not tokens:
        raise ProviderFailed("downloadgram returned no media", "missing")

    chosen: dict[str, tuple[str, str, bool]] = {}
    for tok in tokens:
        try:
            payload = _jwt_payload(tok)
        except Exception:
            payload = {}
        name = str(payload.get("filename") or "")
        media = str(payload.get("url") or "").split("?", 1)[0] or name or tok
        forced = bool(payload.get("force"))
        if media not in chosen or (forced and not chosen[media][2]):
            chosen[media] = (tok, name, forced)

    items: list[MediaItem] = []
    for tok, name, _forced in chosen.values():
        items.append(MediaItem(
            kind=_kind_of(name),
            url="https://cdn.downloadgram.org/?token=" + tok,
            filename=name or f"instagram.{_ext_of(name, 'mp4')}",
        ))

    # A reel comes back as [cover.jpg, video.mp4]. Sending both would mean
    # handing someone a still of the video they just asked for, next to the
    # video. If there is any video in the set, the photos are thumbnails.
    if any(i.kind == "video" for i in items):
        items = [i for i in items if i.kind == "video"]
    return Resolved("instagram", "downloadgram", items)


async def _ig_instafix(url: str) -> Resolved:
    """An InstaFix instance (`eeinstagram.com`).

    InstaFix is the open-source thing behind the ddinstagram/kkinstagram
    links people paste to make Instagram embeds work in Discord and Telegram;
    it answers a crawler User-Agent with OpenGraph tags pointing at the media.
    Both of its better-known domains stopped resolving, which is the whole
    argument for this file.

    Its `/videos/<code>/N` endpoint sometimes redirects to the post's *cover
    frame* rather than the video -- a JPEG where an MP4 was promised. So what
    it says is checked against what it serves before the item is accepted,
    rather than sending the user a still and calling it a download.
    """
    code = instagram_code(url)
    if not code:
        raise ProviderFailed("not an Instagram post URL", "missing")
    host = os.environ.get("DBOT_INSTAFIX_HOST", "https://eeinstagram.com")
    resp = await _get(f"{host}/p/{code}/", crawler=True)
    resp.raise_for_status()
    html = resp.text

    video = _meta(html, "og:video") or _meta(html, "twitter:player:stream")
    image = _meta(html, "og:image")
    target = video or image
    if not target:
        raise ProviderFailed("InstaFix had no media for that post", "missing")
    target = urljoin(host, target)

    # Ask for one byte. Enough to read the Content-Type without pulling the
    # file twice, and cheaper than trusting the meta tag. Streamed, so that
    # only the headers are read: a server that ignores Range answers with the
    # whole video, and `.get()` would hold all of it in memory to look at one
    # header of it.
    async with net.client().stream("GET", target, headers={"Range": "bytes=0-0"}) as probe:
        ctype = probe.headers.get("content-type", "")
    if video and not ctype.startswith("video/"):
        raise ProviderFailed(
            f"InstaFix promised a video and served {ctype or 'nothing'}", "blocked"
        )
    kind = "video" if ctype.startswith("video/") else "photo"
    ext = "mp4" if kind == "video" else "jpg"
    return Resolved("instagram", "instafix",
                    [MediaItem(kind, url=target, filename=f"instagram_{code}.{ext}")])


# ---------------------------------------------------------------------------
# TikTok
# ---------------------------------------------------------------------------

async def _tt_tikwm(url: str) -> Resolved:
    """tikwm.com's open API. Resolves `vm.`/`vt.` short links itself, strips
    the watermark, and -- the part that matters here -- also exposes the same
    media through its own host at `/video/media/<play|hdplay>/<id>.mp4`, which
    is used as the fallback when TikTok's CDN will not serve this container
    directly. Photo slideshows come back in `images`."""
    resp = await _get("https://tikwm.com/api/", params={"url": url, "hd": 1})
    resp.raise_for_status()
    payload = resp.json()
    if payload.get("code") != 0:
        raise ProviderFailed(payload.get("msg") or "tikwm said no", "missing")
    data = payload.get("data") or {}
    vid = str(data.get("id") or "")

    images = data.get("images") or []
    if images:
        items = [MediaItem("photo", url=u, filename=f"tiktok_{vid}_{n}.jpg")
                 for n, u in enumerate(images[:20], start=1)]
    else:
        play = data.get("hdplay") or data.get("play") or data.get("wmplay")
        if not play:
            raise ProviderFailed("tikwm returned no playable media", "missing")
        which = "hdplay" if data.get("hdplay") else "play"
        alts = [f"https://tikwm.com/video/media/{which}/{vid}.mp4"] if vid else []
        items = [MediaItem("video", url=play, alt_urls=alts,
                           filename=f"tiktok_{vid or 'video'}.mp4")]
    author = (data.get("author") or {}).get("unique_id")
    return Resolved("tiktok", "tikwm", items, title=data.get("title"),
                    author=f"@{author}" if author else None)


async def _tt_tnktok(url: str) -> Resolved:
    """tnktok.com, the TikTok embed-fixer. Same crawler-UA trick as InstaFix,
    and it serves the video off its own `offload.` host rather than TikTok's,
    which is the useful half."""
    resp = await _get(re.sub(r"https?://(?:www\.|vm\.|vt\.|m\.)?tiktok\.com",
                             "https://tnktok.com", url, flags=re.I), crawler=True)
    resp.raise_for_status()
    video = _meta(resp.text, "og:video") or _meta(resp.text, "twitter:player:stream")
    if not video:
        # It used to fall back to og:image here and return a photo. That is
        # the Pinterest bug: og:image on a *video* post is the cover frame, so
        # a tnktok that is rate-limited or broken for this post would have
        # ended the chain with a still, and ytdlp:tiktok -- which handles both
        # videos and photo slideshows -- would never have been tried.
        #
        # Refusing is right in both readings of a missing og:video. If the
        # post is a video, the image is the wrong file. If it is a slideshow,
        # og:image is only the first of several and the answer is incomplete.
        # tikwm, ahead of this in the chain, is the provider that can tell
        # them apart, because it reads an explicit `images` field.
        raise ProviderFailed("tnktok exposed no video for that link", "missing")
    return Resolved("tiktok", "tnktok",
                    [MediaItem("video", url=video, filename="tiktok.mp4")])


# ---------------------------------------------------------------------------
# Twitter / X
# ---------------------------------------------------------------------------

TWEET_ID_RE = re.compile(r"/status(?:es)?/(\d+)")


def tweet_id(url: str) -> str | None:
    m = TWEET_ID_RE.search(url)
    return m.group(1) if m else None


async def _tw_api(url: str, host: str, provider: str) -> Resolved:
    """fxtwitter and vxtwitter share a response shape close enough to read
    with one function. Both are the community embed-fixers for X, both expose
    a JSON API their own front ends use, and neither needs a key."""
    tid = tweet_id(url)
    if not tid:
        raise ProviderFailed("not a tweet URL", "missing")
    resp = await _get(f"{host}/i/status/{tid}")
    if resp.status_code == 403:
        raise ProviderFailed(f"{provider} is behind a challenge", "blocked")
    if resp.status_code == 404:
        raise ProviderFailed("that post is gone or protected", "missing")
    resp.raise_for_status()
    try:
        data = resp.json()
    except ValueError as exc:
        raise ProviderFailed(f"{provider} did not return JSON", "blocked") from exc

    tweet = data.get("tweet") if isinstance(data.get("tweet"), dict) else data
    items: list[MediaItem] = []
    media = (tweet.get("media") or {}).get("all") or tweet.get("media_extended") or []
    for n, m in enumerate(media, start=1):
        mtype = (m.get("type") or "").lower()
        murl = m.get("url")
        if not murl:
            continue
        if mtype in ("video", "gif", "animated_gif"):
            items.append(MediaItem("video", url=murl, filename=f"tweet_{tid}_{n}.mp4"))
        else:
            items.append(MediaItem("photo", url=murl, filename=f"tweet_{tid}_{n}.jpg"))
    if not items:
        for murl in tweet.get("mediaURLs") or []:
            items.append(MediaItem(_kind_of(urlparse(murl).path), url=murl,
                                   filename=f"tweet_{tid}.{_ext_of(urlparse(murl).path, 'jpg')}"))
    if not items:
        raise ProviderFailed("that post has no media", "missing")

    author = tweet.get("author") or {}
    handle = author.get("screen_name") or tweet.get("user_screen_name")
    return Resolved("twitter", provider, items, title=tweet.get("text"),
                    author=f"@{handle}" if handle else None)


async def _tw_fxtwitter(url: str) -> Resolved:
    return await _tw_api(url, "https://api.fxtwitter.com", "fxtwitter")


async def _tw_vxtwitter(url: str) -> Resolved:
    return await _tw_api(url, "https://api.vxtwitter.com", "vxtwitter")


# ---------------------------------------------------------------------------
# Pinterest
# ---------------------------------------------------------------------------

# Pinterest's own read endpoint, which is what the site's front end calls and
# what yt-dlp uses. It answers without a login, and it is the only source that
# distinguishes the three kinds of pin from each other:
#
#   a plain video pin   `videos.video_list`
#   a story / idea pin  `story_pin_data.pages[].blocks[].video.video_list`
#   a photo pin         neither, just `images`
#
# It is asked first because the page scrape below cannot tell a video pin from
# a photo one -- see the comment on _pin_og.
_PIN_RESOURCE = "https://www.pinterest.com/resource/PinResource/get/"
_PIN_ID_RE = re.compile(r"/pin/(?:[^/]*?--)?(\d+)")


def _pin_best_video(video_list: dict | None) -> str | None:
    """The biggest .mp4 in one of Pinterest's video_list objects.

    Only mp4: the same list carries HLS playlists (V_HLSV4, V_HLSV3_MOBILE),
    and an .m3u8 is a manifest rather than a file -- handing one to Telegram
    would upload a few hundred bytes of text named .mp4.
    """
    best, best_pixels = None, -1
    for entry in (video_list or {}).values():
        candidate = (entry or {}).get("url") or ""
        if not candidate.endswith(".mp4"):
            continue
        pixels = (entry.get("width") or 0) * (entry.get("height") or 0)
        if pixels > best_pixels:
            best, best_pixels = candidate, pixels
    return best


async def _pin_api(url: str) -> Resolved:
    """Ask Pinterest what the pin actually is."""
    # A pin.it shortlink carries no id, and a slug URL buries it after a
    # double dash. One redirect-following GET settles both, and httpx has
    # already followed it by the time this reads resp.url.
    resolved_url = str(url)
    if "/pin/" not in resolved_url or not _PIN_ID_RE.search(resolved_url):
        try:
            resolved_url = str((await _get(url)).url)
        except Exception as exc:
            raise ProviderFailed(f"couldn't follow that link: {exc}") from exc
    found = _PIN_ID_RE.search(resolved_url)
    if not found:
        raise ProviderFailed("that link has no pin id in it", "missing")
    pin_id = found.group(1)

    payload = json.dumps({"options": {"id": pin_id,
                                      "field_set_key": "unauth_react_main_pin"},
                          "context": {}})
    try:
        resp = await _get(_PIN_RESOURCE, params={"data": payload},
                          headers={"X-Pinterest-PWS-Handler": "www/[username].js"})
        resp.raise_for_status()
        data = resp.json()["resource_response"]["data"]
    except Exception as exc:
        raise ProviderFailed(f"pinterest would not describe that pin: {exc}") from exc
    if not isinstance(data, dict):
        raise ProviderFailed("pinterest returned no pin", "missing")

    items: list[MediaItem] = []
    # Whether the pin is a video at all, whatever Pinterest serves it as. A
    # pin can carry a video_list with no mp4 in it -- only an HLS manifest,
    # V_HLSV3_MOBILE -- and _pin_best_video rightly refuses a manifest. What
    # it must not then do is describe the pin with `images`, which is its
    # cover frame: that is how a friend's shared video came back as a still.
    top_list = (data.get("videos") or {}).get("video_list")
    saw_video = bool(top_list)
    video = _pin_best_video(top_list)
    if video:
        items.append(MediaItem("video", url=video, filename="pinterest.mp4"))

    # A story pin is several pages, each with blocks. Every video block is a
    # separate file, in order, the same way a carousel is.
    for page in ((data.get("story_pin_data") or {}).get("pages") or []):
        for block in (page.get("blocks") or []):
            block_list = (block.get("video") or {}).get("video_list")
            saw_video = saw_video or bool(block_list)
            block_video = _pin_best_video(block_list)
            if block_video:
                items.append(MediaItem("video", url=block_video,
                                       filename=f"pinterest_{len(items) + 1}.mp4"))

    if not items and saw_video:
        # yt-dlp fetches HLS and muxes it, which is what it is for. The page
        # scraper would only find the same cover frame in og:image and end
        # the chain with it, so it is ruled out rather than tried.
        raise ProviderFailed(
            "that pin is a video Pinterest serves only as a stream, which this route cannot fetch",
            "error", skip=("pinterest_og",))

    if not items:
        # A photo pin, or a kind this does not know. `orig` is the full-size
        # image and is what _pin_og reconstructs by hand.
        original = ((data.get("images") or {}).get("orig") or {}).get("url")
        if not original:
            raise ProviderFailed("that pin has no video or image", "missing")
        items.append(MediaItem("photo", url=original,
                               filename=f"pinterest.{_ext_of(urlparse(original).path, 'jpg')}"))
    return Resolved("pinterest", "api", items)


async def _pin_og(url: str) -> Resolved:
    """The pin page's own OpenGraph tags -- the fallback for when the endpoint
    above changes shape.

    It cannot be the first route any more. Pinterest stopped emitting
    `og:video`, so on a video pin this finds no video, falls through to
    `og:image` -- which is present, because it is the video's cover frame --
    and *succeeds* with a photo. A provider that succeeds ends the chain, so
    yt-dlp was never reached and a video pin quietly downloaded a still.
    Nothing errored; the user just got the wrong file.
    """
    resp = await _get(url)
    resp.raise_for_status()
    video = _meta(resp.text, "og:video") or _meta(resp.text, "og:video:secure_url")
    if video:
        return Resolved("pinterest", "og", [MediaItem("video", url=video,
                                                      filename="pinterest.mp4")])
    image = _meta(resp.text, "og:image")
    if not image:
        raise ProviderFailed("that pin has no image or video tag", "missing")
    # og:image points at a resized copy -- `.../736x/ab/cd/....jpg`. The same
    # file is on the same host under `originals`, usually several times the
    # resolution, and downloading someone a thumbnail when the full picture is
    # one path segment away is the sort of thing nobody notices until they
    # try to print it. Not every pin has an `originals` copy, hence the
    # resized one as the fallback rather than the only option.
    original = re.sub(r"/(?:\d{2,4}x\d{0,4}|originals)/", "/originals/", image, count=1)
    routes = [original, image] if original != image else [image]
    return Resolved("pinterest", "og",
                    [MediaItem("photo", url=routes[0], alt_urls=routes[1:],
                               filename=f"pinterest.{_ext_of(urlparse(image).path, 'jpg')}")])


# ---------------------------------------------------------------------------
# cobalt, for the platforms with nothing else
# ---------------------------------------------------------------------------
# cobalt (github.com/imputnet/cobalt) is an open-source downloader whose whole
# product is an API: POST a link, get back a URL that serves the file. The
# official instance wants a Turnstile token, but the project is built to be
# self-hosted, and a number of people run public instances that answer
# without one. Like the embed-fixers above, the fetching happens from *their*
# address -- and the file comes back through their `/tunnel`, so the second
# request never touches YouTube's CDN from this container either.
#
# Written in the cloud session of 2026-09-25 for YouTube, which had yt-dlp and
# nothing else: a redeploy can land on an address YouTube challenges ("sign in
# to confirm you're not a bot") and a link that downloaded the day before is
# then DL-BLOCKED. Ported here at 2.1.0 (that branch was never merged). With
# cobalt ahead of it, yt-dlp becomes what it is everywhere else -- the last
# resort, and the route that gets better if cookies or a proxy are set.
#
# Checked 2026-10-07 from a residential address: of the three default
# instances one answered, one said error.api.youtube.login and one
# content.no_valid_content. Every one is somebody's hobby server, which is why
# there are several, tried in order inside this one provider, with whichever
# answered last going first next time -- so a dead instance costs one request
# per restart rather than one per download. To use an instance of your own
# (the robust answer; a cobalt container is small), put it first in
# DBOT_COBALT_INSTANCES and its key in DBOT_COBALT_API_KEY, which is sent to
# that first instance only: a key is a secret, and the rest are strangers.

COBALT_INSTANCES = [
    host.strip().rstrip("/") for host in os.environ.get(
        "DBOT_COBALT_INSTANCES",
        "https://api.cobalt.liubquanti.click,https://cobaltapi.cjs.nz,https://dwnld.nichind.dev",
    ).split(",") if host.strip()
]
COBALT_API_KEY = os.environ.get("DBOT_COBALT_API_KEY") or None
# 720p rather than "max": the bot sends through the cloud Bot API's 50 MB, and
# a 1080p minute of video is most of that on its own.
COBALT_QUALITY = os.environ.get("DBOT_COBALT_QUALITY", "720")
# Per instance, for its answer (the address of the file, not the file). Production,
# 2026-11-07: the one instance that still fetched was given up on at 10 s.
COBALT_INSTANCE_TIMEOUT_S = float(os.environ.get("DBOT_COBALT_INSTANCE_TIMEOUT", "15"))
# For the whole route, file included. Since 2.1.5 the file is fetched here, inside the route
# (see _cobalt_take), and a public instance's tunnel is slow: about 100 KB/s when measured on
# 2026-11-10. A file over the free ceiling gets YTDLP_SECONDS_PER_MB more for each megabyte.
COBALT_TIMEOUT_S = float(os.environ.get("DBOT_COBALT_TIMEOUT", "240"))
# The heights asked for next when the file at COBALT_QUALITY is over the size ceiling: cobalt
# has no "the best that fits", so the bot walks down itself.
COBALT_STEP_DOWN = (480, 360)

# cobalt's error codes, sorted into the verdicts the chain understands. The
# codes are namespaced ("error.api.content.video.unavailable",
# "error.api.youtube.login"), so these match on parts rather than whole codes
# -- new codes under a known namespace land in the right bucket unaided.
_COBALT_MISSING = ("content.video.unavailable", "content.post.unavailable",
                   "content.video.private", "content.post.private",
                   "content.video.age", "content.post.age", "link.invalid",
                   "link.unsupported", "content.video.live")
_COBALT_TOO_BIG = ("content.too_long", "content.too_large")
_COBALT_BLOCKED = (".login", "auth.", "fetch.rate", "rate_exceeded", "too_many_requests",
                   "content.video.region", "youtube.decipher", "youtube.token",
                   "no_valid_content", "fetch.critical")

_cobalt_last_good: str | None = None


def _cobalt_verdict(code: str) -> str:
    code = code.lower()
    if any(part in code for part in _COBALT_MISSING):
        return "missing"
    if any(part in code for part in _COBALT_TOO_BIG):
        return "too_big"
    if any(part in code for part in _COBALT_BLOCKED):
        return "blocked"
    return "error"


def _cobalt_items(data: dict, platform: str) -> list[MediaItem]:
    status = data.get("status")
    if status in ("tunnel", "redirect", "stream"):
        name = str(data.get("filename") or "")
        kind = _kind_of(name) if name else "video"
        return [MediaItem(kind, url=data["url"],
                          filename=f"{platform}.{_ext_of(name, 'mp4' if kind == 'video' else 'jpg')}")]
    if status == "picker":
        items = []
        for entry in data.get("picker") or []:
            url = entry.get("url")
            if not url:
                continue
            # "gif" is a short muted mp4 in cobalt's vocabulary, which is a
            # video to Telegram.
            kind = "photo" if entry.get("type") == "photo" else "video"
            items.append(MediaItem(kind, url=url,
                                   filename=f"{platform}.{'jpg' if kind == 'photo' else 'mp4'}"))
        return items
    if status == "local-processing":
        # Newer instances can hand back separate streams for the client to
        # merge. One stream is already a whole file; several would need
        # ffmpeg here, which is what yt-dlp is further down the chain for.
        tunnels = data.get("tunnel") or []
        if len(tunnels) == 1:
            return [MediaItem("video", url=tunnels[0], filename=f"{platform}.mp4")]
    return []


async def _cobalt_one(host: str, url: str, platform: str, key: str | None,
                      quality: "str | None" = None) -> Resolved:
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Api-Key {key}"
    body = {"url": url, "videoQuality": quality or _cobalt_quality(), "filenameStyle": "basic"}
    resp = await _post(host + "/", json=body, headers=headers, timeout=COBALT_INSTANCE_TIMEOUT_S)
    try:
        data = resp.json()
    except ValueError:
        raise ProviderFailed(f"{urlparse(host).netloc} answered HTTP {resp.status_code}, not JSON",
                             "blocked" if resp.status_code in (401, 403, 429) else "error")
    if not isinstance(data, dict):
        raise ProviderFailed(f"{urlparse(host).netloc} answered something that is not an object", "error")
    if data.get("status") == "error":
        code = str((data.get("error") or {}).get("code") or "unknown")
        raise ProviderFailed(f"{urlparse(host).netloc}: {code}", _cobalt_verdict(code))
    items = _cobalt_items(data, platform)
    if not items:
        raise ProviderFailed(f"{urlparse(host).netloc}: nothing usable ({data.get('status')})", "error")
    if any(i.kind == "video" for i in items) and platform == "youtube":
        items = [i for i in items if i.kind == "video"]
    return Resolved(platform, "cobalt", items)


# The heights cobalt takes for "videoQuality".
COBALT_HEIGHTS = (144, 240, 360, 480, 720, 1080, 1440, 2160)


def _cobalt_quality() -> str:
    """COBALT_QUALITY, unless a height was asked for (_HEIGHT): then that height, or the
    nearest one under it that cobalt knows. An exact height (the quality picker) asked of
    cobalt gets that height when the video has it; cobalt has no way to refuse the others."""
    asked = _HEIGHT.get()
    if not asked or not asked[0]:
        return COBALT_QUALITY
    under = [h for h in COBALT_HEIGHTS if h <= asked[0]]
    return str(under[-1] if under else COBALT_HEIGHTS[0])


# ---------------------------------------------------------------------------
# Files fetched inside a route
# ---------------------------------------------------------------------------
# Up to 2.1.4 a relay's answer was an address, and the file behind it was fetched after the
# chain had ended (download(), below). Production, 2026-11-07, and a check from a datacenter
# address on 2026-11-10: an instance YouTube has blocked still answers with a tunnel address,
# and the tunnel then sends nothing. The chain had already stopped at that instance, so the
# person got an empty file's error while the next instance, or the next route, would have
# worked. The relays that front YouTube therefore fetch the file themselves and look at it,
# and an instance whose file is no good is one more instance that failed.

# Under this a "video" is an error page or nothing at all.
MIN_MEDIA_BYTES = 1024


def _unlink(path: "str | None") -> None:
    if path:
        try:
            os.remove(path)
        except OSError:
            pass


def _looks_like_video(path: str) -> bool:
    """Does the file start the way an MP4 (the "ftyp" box) or a WebM/Matroska file does."""
    try:
        with open(path, "rb") as handle:
            head = handle.read(12)
    except OSError:
        return False
    return head[4:8] == b"ftyp" or head[:4] == b"\x1a\x45\xdf\xa3"


def _limit_bytes() -> int:
    """The size ceiling of the download being resolved (see _LIMIT_MB)."""
    asked = _LIMIT_MB.get()
    return asked * 1024 * 1024 if asked else net.MAX_RESPONSE_BYTES


async def _take(url: str, ext: str, limit: int) -> str:
    """`url` into a new file in the work folder; its path. On any failure, a cancellation
    included (the chain's time limit, the bot stopping), nothing is left on disk."""
    os.makedirs(_WORK_DIR, exist_ok=True)
    path = os.path.join(_WORK_DIR, f"{uuid.uuid4().hex}.{ext}")
    parsed = urlparse(url)
    try:
        await net.stream_to_file(url, path, limit=limit,
                                 headers={"Referer": f"{parsed.scheme}://{parsed.netloc}/"})
    except BaseException:
        _unlink(path)
        raise
    return path


def _instances_verdict(failures: "list[ProviderFailed]") -> str:
    """What a route says when every one of its instances failed."""
    kinds = {f.kind for f in failures}
    return "blocked" if "blocked" in kinds else "missing" if kinds == {"missing"} else "error"


async def _cobalt_take(resolved: Resolved, netloc: str) -> None:
    """Fetch every item of a cobalt answer to disk and check it; the items then carry paths.
    net.TooLarge goes up as it is (the caller asks for a smaller version)."""
    taken: list[str] = []
    try:
        for item in resolved.items:
            ext = _ext_of(item.filename, "mp4" if item.kind == "video" else "jpg")
            taken.append(await _take(item.url, ext, _limit_bytes()))
            size = os.path.getsize(taken[-1])
            if size < MIN_MEDIA_BYTES or (item.kind == "video" and not _looks_like_video(taken[-1])):
                raise ProviderFailed(f"{netloc}: its tunnel sent {size:,} bytes that are not a {item.kind}",
                                     "blocked")
    except BaseException:
        for path in taken:
            _unlink(path)
        raise
    for item, path in zip(resolved.items, taken):
        item.path = path


def _cobalt_qualities() -> "list[str]":
    """The qualities to ask one instance for, in turn: the first one, then -- unless one height
    and no other was asked for -- the lower ones of COBALT_STEP_DOWN."""
    first = _cobalt_quality()
    asked = _HEIGHT.get()
    if asked and asked[0] and asked[1]:
        return [first]
    top = int(first) if first.isdigit() else COBALT_HEIGHTS[-1]
    return [first] + [str(height) for height in COBALT_STEP_DOWN if height < top]


async def _cobalt_fetch(host: str, url: str, platform: str, key: "str | None") -> Resolved:
    """One instance, as far as files on disk (as far as its answer, for lookup())."""
    netloc = urlparse(host).netloc
    qualities = _cobalt_qualities()
    for number, quality in enumerate(qualities, start=1):
        try:
            # A hard deadline, not httpx's: its timeout is per read, and
            # an instance trickling bytes would never trip it.
            resolved = await asyncio.wait_for(_cobalt_one(host, url, platform, key, quality),
                                              timeout=COBALT_INSTANCE_TIMEOUT_S)
        except asyncio.TimeoutError:
            raise ProviderFailed(f"{netloc}: no answer in {COBALT_INSTANCE_TIMEOUT_S:.0f}s", "error") from None
        if _LOOKUP.get():
            return resolved
        try:
            await _cobalt_take(resolved, netloc)
        except net.TooLarge:
            if number < len(qualities):
                logger.info("cobalt %s: over the size limit at %s, asking for %s",
                            netloc, quality, qualities[number])
                continue
            raise ProviderFailed(f"{netloc}: over the size limit at {quality}", "too_big") from None
        return resolved
    raise ProviderFailed(f"{netloc}: nothing asked for", "error")      # qualities is never empty


def _cobalt_provider(platform: str):
    async def provider(url: str) -> Resolved:
        global _cobalt_last_good
        if not COBALT_INSTANCES:
            raise ProviderFailed("no cobalt instances configured", "error")
        hosts = list(COBALT_INSTANCES)
        if _cobalt_last_good in hosts:
            hosts.remove(_cobalt_last_good)
            hosts.insert(0, _cobalt_last_good)
        # One instance may not use up the whole route: the next one is still asked.
        budget = _timeout_for("cobalt:") * INSTANCE_SHARE
        failures: list[ProviderFailed] = []
        for host in hosts:
            key = COBALT_API_KEY if host == COBALT_INSTANCES[0] else None
            netloc = urlparse(host).netloc
            try:
                resolved = await asyncio.wait_for(_cobalt_fetch(host, url, platform, key), timeout=budget)
            except ProviderFailed as exc:
                failures.append(exc)
                if exc.kind == "too_big":
                    # The clip itself is the problem; another instance will
                    # say the same thing about it. "missing" is not trusted
                    # the same way: an instance YouTube has blocked can
                    # report a perfectly good video as unavailable.
                    raise
                continue
            except asyncio.TimeoutError:
                failures.append(ProviderFailed(f"{netloc}: its file was not here in {budget:.0f}s", "error"))
                continue
            except (httpx.HTTPError, FetchError, OSError, KeyError, TypeError) as exc:
                failures.append(ProviderFailed(f"{netloc}: {type(exc).__name__}: {exc}", "error"))
                continue
            _cobalt_last_good = host
            return resolved
        raise ProviderFailed("; ".join(str(f) for f in failures), _instances_verdict(failures))
    return provider


# ---------------------------------------------------------------------------
# Invidious: a second family of relays for YouTube
# ---------------------------------------------------------------------------
# Written 2026-11-10 for 2.1.5. A check from a datacenter address that day: YouTube refused
# every one of yt-dlp's 14 player clients (a bot check, or HTTP 403 on the file), with and
# without a token provider, and of 24 public cobalt instances one still fetched -- the rest
# want a browser's Turnstile token, or are blocked themselves. One hobby server is not a route
# to rely on, so this is another kind of server doing the same job: Invidious, a front end
# people run for themselves, whose API lists a video's versions and, asked with local=true,
# hands out addresses on the instance itself, which passes the bytes on from YouTube.
#
# It lists picture and sound as separate files (the joined ones YouTube used to offer are
# gone), so this route fetches two and joins them with ffmpeg, without re-encoding. It reads
# the sizes first, so, unlike cobalt, it can choose the best version that fits.
#
# Instances come and go, so there are two sources: DBOT_INVIDIOUS_INSTANCES (yours first), and
# the project's own list of public instances (DBOT_INVIDIOUS_DIRECTORY), read a few times a day
# and used only for the instances that say their API is open.

INVIDIOUS_INSTANCES = [
    host.strip().rstrip("/") for host in os.environ.get(
        "DBOT_INVIDIOUS_INSTANCES", "https://yt.chocolatemoo53.com").split(",") if host.strip()
]
# "" or "0" turns the directory off: then only the instances above are asked.
INVIDIOUS_DIRECTORY = os.environ.get("DBOT_INVIDIOUS_DIRECTORY", "https://api.invidious.io/instances.json").strip()
INVIDIOUS_DIRECTORY_MAX = 6
INVIDIOUS_DIRECTORY_EVERY_S = 6 * 3600.0
INVIDIOUS_DIRECTORY_RETRY_S = 900.0
INVIDIOUS_INSTANCE_TIMEOUT_S = float(os.environ.get("DBOT_INVIDIOUS_INSTANCE_TIMEOUT", "15"))
INVIDIOUS_TIMEOUT_S = float(os.environ.get("DBOT_INVIDIOUS_TIMEOUT", "240"))
# The share of a route's time one instance may use (cobalt's instances too).
INSTANCE_SHARE = 0.75
# The same margin video.py keeps under the ceiling (SIZE_MARGIN): the joined file is a little
# longer than its two parts.
INVIDIOUS_SIZE_MARGIN = 0.96
MUX_TIMEOUT_S = 120.0

_invidious_listed: list[str] = []
_invidious_listed_next = 0.0
_invidious_last_good: str | None = None

_YT_ID_RE = re.compile(r"(?:[?&]v=|youtu\.be/|/shorts/|/live/|/embed/)([A-Za-z0-9_-]{11})(?![A-Za-z0-9_-])")

_INVIDIOUS_BLOCKED = ("sign in", "not a bot", "login", "rate limit", "too many", "captcha", "blocked")
_INVIDIOUS_MISSING = ("unavailable", "private", "removed", "not available", "does not exist",
                      "terminated", "not found")


def _invidious_verdict(text: str) -> str:
    text = text.lower()
    if any(part in text for part in _INVIDIOUS_BLOCKED):
        return "blocked"
    if any(part in text for part in _INVIDIOUS_MISSING):
        return "missing"
    return "error"


def _invidious_from_directory(data) -> "list[str]":
    """The instances of the directory's answer that are reached over https and say their API is
    open, in the directory's order (it sorts by health), at most INVIDIOUS_DIRECTORY_MAX. Pure."""
    hosts: list[str] = []
    for entry in data if isinstance(data, list) else ():
        meta = entry[1] if isinstance(entry, (list, tuple)) and len(entry) == 2 else None
        if not isinstance(meta, dict) or meta.get("type") != "https" or meta.get("api") is not True:
            continue
        uri = str(meta.get("uri") or "").rstrip("/")
        if re.fullmatch(r"https://[A-Za-z0-9.-]+(?::\d+)?", uri) and uri not in hosts:
            hosts.append(uri)
    return hosts[:INVIDIOUS_DIRECTORY_MAX]


async def _invidious_hosts() -> "list[str]":
    """The instances to ask, in order: the one that answered last, the configured ones, then
    the directory's. The directory is read again every few hours; when it cannot be read, the
    list from last time stays."""
    global _invidious_listed, _invidious_listed_next
    now = time.monotonic()
    if INVIDIOUS_DIRECTORY not in ("", "0") and now >= _invidious_listed_next:
        _invidious_listed_next = now + INVIDIOUS_DIRECTORY_RETRY_S
        try:
            resp = await asyncio.wait_for(_get(INVIDIOUS_DIRECTORY, timeout=10.0), timeout=10.0)
            resp.raise_for_status()
            _invidious_listed = _invidious_from_directory(resp.json())
            _invidious_listed_next = now + INVIDIOUS_DIRECTORY_EVERY_S
        except Exception as exc:        # the directory is a convenience: the configured instances remain
            logger.info("the Invidious directory was not read (%s: %s)", type(exc).__name__, exc)
    hosts = list(INVIDIOUS_INSTANCES) + [h for h in _invidious_listed if h not in INVIDIOUS_INSTANCES]
    if _invidious_last_good in hosts:
        hosts.remove(_invidious_last_good)
        hosts.insert(0, _invidious_last_good)
    return hosts


def _number(value) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _invidious_height(fmt: dict) -> int:
    """A version's picture height: from its size ("720x1280" is 1280 high, the way yt-dlp
    counts it and MAX_HEIGHT caps it), else from its label ("720p")."""
    size = re.fullmatch(r"(\d+)x(\d+)", str(fmt.get("size") or ""))
    if size:
        return int(size.group(2))
    label = re.match(r"(\d+)p", str(fmt.get("resolution") or fmt.get("qualityLabel") or ""))
    return int(label.group(1)) if label else 0


def _invidious_track(fmt: dict) -> int:
    """How much a sound track is the video's own: 2 the original, 1 the only one (nothing
    said), 0 a dubbed or described one. YouTube says which in the address ("xtags")."""
    tags = (parse_qs(urlsplit(str(fmt.get("url") or "")).query).get("xtags") or [""])[0]
    if "acont=" not in tags:
        return 1
    return 2 if "acont=original" in tags else 0


def _invidious_pick(formats, budget_bytes: int, height: "int | None" = None, exact: bool = False,
                    max_height: int = 1080) -> "tuple[dict, dict]":
    """(picture, sound) for the best version that fits `budget_bytes`: H.264 with AAC, which
    every Telegram app plays and which go into one MP4 as they are; the tallest picture up to
    `max_height` (or `height`; with `exact`, that height and no other). Pure. ProviderFailed
    when there is none: "too_big" when the size is why, "error" otherwise."""
    formats = [fmt for fmt in formats or () if isinstance(fmt, dict) and fmt.get("url")]
    sounds = [fmt for fmt in formats
              if str(fmt.get("type") or "").startswith("audio/mp4") and _number(fmt.get("clen"))]
    if not sounds:
        raise ProviderFailed("no AAC sound is listed", "error")
    sound = max(sounds, key=lambda fmt: (_invidious_track(fmt), min(_number(fmt.get("bitrate")), 192_000)))
    cap = max_height if height is None else min(height, max_height)
    pictures = [fmt for fmt in formats
                if str(fmt.get("type") or "").startswith("video/mp4") and "avc1" in str(fmt.get("type"))
                and _number(fmt.get("clen")) and _invidious_height(fmt)
                and (_invidious_height(fmt) == height if exact and height is not None
                     else _invidious_height(fmt) <= cap)]
    if not pictures:
        raise ProviderFailed("no H.264 picture of that height is listed", "error")
    room = budget_bytes * INVIDIOUS_SIZE_MARGIN - _number(sound.get("clen"))
    fitting = [fmt for fmt in pictures if _number(fmt.get("clen")) <= room]
    if not fitting:
        raise ProviderFailed("even the smallest version listed is over the size limit", "too_big")
    picture = max(fitting, key=lambda fmt: (_invidious_height(fmt), _number(fmt.get("fps")),
                                            _number(fmt.get("bitrate"))))
    return picture, sound


def _invidious_local(host: str, media_url: str) -> str:
    """The address of a file on the instance itself. With local=true the instance writes these
    itself; whatever it wrote, the request goes to the instance asked and to nowhere it names:
    its /videoplayback, with YouTube's parameters, and YouTube's server as the `host` parameter
    when the instance left the address pointing at YouTube."""
    parts = urlsplit(media_url)
    if parts.path != "/videoplayback" or not parts.query:
        raise ProviderFailed(f"{urlparse(host).netloc}: a file address that is not a videoplayback one", "error")
    query = parts.query
    theirs = (parts.hostname or "").lower()
    if theirs.endswith(".googlevideo.com") and "host=" not in query:
        query += f"&host={theirs}"
    return f"{host}/videoplayback?{query}"


async def _mux(picture: str, sound: str, out: str) -> None:
    """One MP4 from a picture file and a sound file, nothing re-encoded, the index at the
    front (Telegram plays it while it loads). The inputs are files a stranger's server sent:
    ffmpeg is told they are MP4 and may open files and nothing else, so one that is really a
    playlist cannot make it fetch an address or read another file."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise ProviderFailed("ffmpeg is not installed", "error")
    argv = [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y"]
    for path in (picture, sound):
        argv += ["-protocol_whitelist", "file", "-f", "mp4", "-i", path]
    argv += ["-map", "0:v:0", "-map", "1:a:0", "-c", "copy", "-movflags", "+faststart", "-f", "mp4", out]
    proc = await asyncio.create_subprocess_exec(
        *argv, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE)
    try:
        _, said = await asyncio.wait_for(proc.communicate(), timeout=MUX_TIMEOUT_S)
    except asyncio.TimeoutError:
        raise ProviderFailed(f"ffmpeg had not joined the two files in {MUX_TIMEOUT_S:.0f}s", "error") from None
    finally:
        if proc.returncode is None:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
    if proc.returncode != 0:
        raise ProviderFailed("ffmpeg could not join the picture and the sound: "
                             + (said or b"").decode("utf-8", "replace").strip()[-200:], "error")


async def _invidious_fetch(netloc: str, picture_url: str, sound_url: str,
                           picture_bytes: int, sound_bytes: int) -> str:
    """Both files, checked, joined; the path of the MP4. Nothing else is left on disk."""
    limit = _limit_bytes()
    parts: list = [None, None]
    out = None
    tasks = [asyncio.ensure_future(_take(picture_url, "mp4", limit)),
             asyncio.ensure_future(_take(sound_url, "m4a", limit))]
    try:
        try:
            parts = list(await asyncio.gather(*tasks))
        except BaseException:
            # gather leaves the other fetch running when one fails. It is stopped here, and
            # _take removes what it had written; a fetch that had already finished left a
            # whole file, which goes too.
            for task in tasks:
                task.cancel()
            for ended in await asyncio.gather(*tasks, return_exceptions=True):
                if isinstance(ended, str):
                    _unlink(ended)
            raise
        for path, expected, what in ((parts[0], picture_bytes, "picture"), (parts[1], sound_bytes, "sound")):
            size = os.path.getsize(path)
            if size < MIN_MEDIA_BYTES or size < expected or not _looks_like_video(path):
                raise ProviderFailed(f"{netloc}: sent {size:,} of the {what}'s {expected:,} bytes", "blocked")
        out = os.path.join(_WORK_DIR, f"{uuid.uuid4().hex}.mp4")
        await _mux(parts[0], parts[1], out)
        if os.path.getsize(out) < MIN_MEDIA_BYTES or not _looks_like_video(out):
            raise ProviderFailed("ffmpeg wrote no video", "error")
        done, out = out, None
        return done
    finally:
        for path in (parts[0], parts[1], out):
            _unlink(path)


async def _invidious_one(host: str, video_id: str, platform: str) -> Resolved:
    import video  # for its ceilings; deferred as in _ytdlp_provider
    netloc = urlparse(host).netloc
    try:
        resp = await asyncio.wait_for(
            _get(f"{host}/api/v1/videos/{video_id}", params={"local": "true"},
                 timeout=INVIDIOUS_INSTANCE_TIMEOUT_S),
            timeout=INVIDIOUS_INSTANCE_TIMEOUT_S)
    except asyncio.TimeoutError:
        raise ProviderFailed(f"{netloc}: no answer in {INVIDIOUS_INSTANCE_TIMEOUT_S:.0f}s", "error") from None
    try:
        data = resp.json()
    except ValueError:
        raise ProviderFailed(f"{netloc} answered HTTP {resp.status_code}, not JSON",
                             "blocked" if resp.status_code in (401, 403, 429) else "error") from None
    if not isinstance(data, dict):
        raise ProviderFailed(f"{netloc} answered something that is not an object", "error")
    if resp.status_code != 200 or data.get("error"):
        said = str(data.get("error") or f"HTTP {resp.status_code}")[:120]
        raise ProviderFailed(f"{netloc}: {said}", _invidious_verdict(said))
    if data.get("liveNow") or data.get("isUpcoming"):
        raise ProviderFailed(f"{netloc}: a live stream, not a video", "missing")
    seconds = _number(data.get("lengthSeconds"))
    if seconds > video.MAX_DURATION_S:
        raise ProviderFailed(f"that clip is {seconds // 60} minutes long, and this bot caps downloads "
                             f"at {video.MAX_DURATION_S // 60}", "too_big")
    height, exact = _HEIGHT.get() or (None, False)
    picture, sound = _invidious_pick(data.get("adaptiveFormats"), _limit_bytes(), height, exact,
                                     video.MAX_HEIGHT)
    picture_url, sound_url = _invidious_local(host, picture["url"]), _invidious_local(host, sound["url"])
    if _LOOKUP.get():
        return Resolved(platform, "invidious", [MediaItem("video", url=picture_url, filename=f"{platform}.mp4")])
    logger.info("invidious %s: %ss, %sp picture, %s + %s bytes", netloc, seconds, _invidious_height(picture),
                picture.get("clen"), sound.get("clen"))
    path = await _invidious_fetch(netloc, picture_url, sound_url,
                                  _number(picture.get("clen")), _number(sound.get("clen")))
    return Resolved(platform, "invidious", [MediaItem("video", path=path, filename=f"{platform}.mp4")])


def _invidious_provider(platform: str):
    async def provider(url: str) -> Resolved:
        global _invidious_last_good
        found = _YT_ID_RE.search(url)
        if not found:
            raise ProviderFailed("no video id in that link", "missing")
        hosts = await _invidious_hosts()
        if not hosts:
            raise ProviderFailed("no Invidious instances configured", "error")
        budget = _timeout_for("invidious:") * INSTANCE_SHARE
        failures: list[ProviderFailed] = []
        for host in hosts:
            netloc = urlparse(host).netloc
            try:
                resolved = await asyncio.wait_for(_invidious_one(host, found.group(1), platform), timeout=budget)
            except ProviderFailed as exc:
                failures.append(exc)
                if exc.kind == "too_big":       # the clip's own size: as for cobalt
                    raise
                continue
            except asyncio.TimeoutError:
                failures.append(ProviderFailed(f"{netloc}: its files were not here in {budget:.0f}s", "error"))
                continue
            except (httpx.HTTPError, FetchError, OSError, KeyError, TypeError, ValueError) as exc:
                failures.append(ProviderFailed(f"{netloc}: {type(exc).__name__}: {exc}", "error"))
                continue
            _invidious_last_good = host
            return resolved
        raise ProviderFailed("; ".join(str(f) for f in failures), _instances_verdict(failures))
    return provider


# ---------------------------------------------------------------------------
# yt-dlp, the universal last resort
# ---------------------------------------------------------------------------

def _ytdlp_provider(platform: str):
    async def provider(url: str) -> Resolved:
        import video  # deferred with yt-dlp itself; see video.py's docstring
        # A thread cannot be cancelled, only asked. This provider is cancelled
        # by the chain's time limit and by the bot stopping, and the download
        # in its thread would carry on to the end -- a file nobody collects,
        # left on disk and paid for in bandwidth -- unless it is told.
        stop = threading.Event()
        try:
            height, exact = _HEIGHT.get() or (None, False)
            path = await asyncio.to_thread(video.download_video, url, _WORK_DIR, _LIMIT_MB.get(), stop,
                                           height, exact)
        except asyncio.CancelledError:
            stop.set()
            raise
        except video.BlockedBySource as exc:
            raise ProviderFailed(str(exc), "blocked") from exc
        except video.TooLarge as exc:
            raise ProviderFailed(str(exc), "too_big") from exc
        except Exception as exc:
            raise ProviderFailed(str(exc), "error") from exc
        return Resolved(platform, "ytdlp",
                        [MediaItem("video", path=path,
                                   filename=os.path.basename(path))])
    return provider


_WORK_DIR = os.environ.get("DBOT_WORK_DIR") or tempfile.gettempdir()


# ---------------------------------------------------------------------------
# The chains
# ---------------------------------------------------------------------------
# Order is the order they are tried on a healthy day: verified-working first,
# then the ones that are known to be more fragile, then yt-dlp -- which is
# last everywhere rather than first because it is the one route that makes the
# request from this container's own address, and that address is the problem.
# It is still in every chain, because it is also the only one that gets better
# the moment DBOT_*_COOKIES_FILE or DBOT_*_PROXY is set.

PROVIDERS: dict[str, list[tuple[str, object]]] = {
    "instagram": [
        ("downloadgram", _ig_downloadgram),
        ("instafix", _ig_instafix),
        ("ytdlp:instagram", _ytdlp_provider("instagram")),
    ],
    "tiktok": [
        ("tikwm", _tt_tikwm),
        ("tnktok", _tt_tnktok),
        ("ytdlp:tiktok", _ytdlp_provider("tiktok")),
    ],
    "twitter": [
        ("vxtwitter", _tw_vxtwitter),
        ("fxtwitter", _tw_fxtwitter),
        ("ytdlp:twitter", _ytdlp_provider("twitter")),
    ],
    # api first: it is the only one of the three that can tell a video pin
    # from a photo pin, and getting that wrong is silent rather than loud.
    "pinterest": [
        ("pinterest_api", _pin_api),
        ("pinterest_og", _pin_og),
        ("ytdlp:pinterest", _ytdlp_provider("pinterest")),
    ],
    # Reddit has one entry on purpose. bot.py reads the post itself -- it
    # needs the title, author and score for the card anyway -- and sends a
    # plain image post straight from `url_overridden_by_dest`. What is left
    # for the chain is hosted video, which is DASH with a separate audio
    # track, and yt-dlp is the only thing here that muxes it.
    "reddit": [
        ("ytdlp:reddit", _ytdlp_provider("reddit")),
    ],
    # yt-dlp first (with deno it can answer YouTube's challenges), the relays when it
    # fails: yt-dlp asks from this container's own address, which is the address
    # YouTube challenges, and a relay asks from its own. See the cobalt and Invidious
    # sections. On a host YouTube refuses, yt-dlp fails in a second or two and is then
    # rested by its health score, so the order costs little there.
    "youtube": [
        ("ytdlp:youtube", _ytdlp_provider("youtube")),
        ("cobalt:youtube", _cobalt_provider("youtube")),
        ("invidious:youtube", _invidious_provider("youtube")),
    ],
}


def _ordered(platform: str) -> list[tuple[str, object]]:
    """Registry order, with the currently-unwell moved to the back.

    Not filtered -- moved. A provider in cooldown is still tried if
    everything ahead of it failed, so a chain whose every member is having a
    bad day still does the most useful thing available instead of refusing on
    the strength of yesterday's scores.
    """
    now = time.time()
    ready, cooling = [], []
    for entry in PROVIDERS.get(platform, []):
        h = health(entry[0])
        (cooling if h.cooling_until > now else ready).append(entry)
    return ready + cooling


# The size ceiling for the download being resolved right now: None is the
# free one (video.MAX_DOWNLOAD_MB); a large download (large_files.py) asks for
# more. A context variable rather than an argument to every provider, because
# only yt-dlp -- which downloads while it resolves -- has a use for it.
_LIMIT_MB: contextvars.ContextVar = contextvars.ContextVar("download_limit_mb", default=None)
# The picture height asked for, as (height, exact), or None for the best that fits: the
# quality picker asks for one height exactly, a group's setting caps it. Only yt-dlp and
# cobalt can choose, so, like _LIMIT_MB, a context variable rather than an argument.
_HEIGHT: contextvars.ContextVar = contextvars.ContextVar("download_height", default=None)
# True while lookup() asks: the routes that fetch the file inside the route (the relays for
# YouTube) then stop at the instance's answer and fetch nothing.
_LOOKUP: contextvars.ContextVar = contextvars.ContextVar("lookup_only", default=False)


def _timeout_for(name: str) -> float:
    """How long the route called `name` gets before the chain moves on: PROVIDER_TIMEOUT_S,
    except for the routes that fetch the file while they resolve it (yt-dlp, and the relays for
    YouTube unless lookup() is asking), which get their own time and more for a large file."""
    if name.startswith("ytdlp:"):
        base = YTDLP_TIMEOUT_S
    elif _LOOKUP.get():
        return PROVIDER_TIMEOUT_S
    elif name.startswith("cobalt:"):
        base = COBALT_TIMEOUT_S
    elif name.startswith("invidious:"):
        base = INVIDIOUS_TIMEOUT_S
    else:
        return PROVIDER_TIMEOUT_S
    free_mb = net.MAX_RESPONSE_BYTES // (1024 * 1024)
    over_mb = max(0, (_LIMIT_MB.get() or free_mb) - free_mb)
    return max(PROVIDER_TIMEOUT_S, base + over_mb * YTDLP_SECONDS_PER_MB)


async def resolve(platform: str, url: str, max_mb: int | None = None,
                  height: "int | None" = None, exact: bool = False) -> Resolved:
    """Walk the chain until something produces media, with `max_mb` as the
    ceiling for anything downloaded on the way, and `height` the picture height
    (see _HEIGHT)."""
    token = _LIMIT_MB.set(max_mb)
    wanted = _HEIGHT.set((height, exact) if height else None)
    try:
        return await _resolve(platform, url)
    finally:
        _HEIGHT.reset(wanted)
        _LIMIT_MB.reset(token)


# Most files one post may deliver. The bot downloads every item of a delivery to
# disk before it sends the first, each up to the download ceiling, and a
# provider is somebody else's server: a cobalt picker or a resolver that lists
# a thousand items would fill the container's disk (review, 2026-10-08). A
# TikTok slideshow carries up to 35 photos, so the default leaves room.
MAX_ITEMS_PER_POST = max(1, int(os.environ.get("DBOT_MAX_ITEMS_PER_POST", "40")))


def _cut_items(resolved: Resolved) -> None:
    """Keep the first MAX_ITEMS_PER_POST items of `resolved`. A file yt-dlp
    already wrote for an item that is cut is deleted: nothing else knows of it."""
    if len(resolved.items) <= MAX_ITEMS_PER_POST:
        return
    logger.warning("a delivery of %d items was cut to %d", len(resolved.items), MAX_ITEMS_PER_POST)
    for item in resolved.items[MAX_ITEMS_PER_POST:]:
        if item.path:
            try:
                os.remove(item.path)
            except OSError:
                pass
    del resolved.items[MAX_ITEMS_PER_POST:]


async def _resolve(platform: str, url: str) -> Resolved:
    """Walk the chain until something produces media."""
    failures: dict[str, ProviderFailed] = {}
    chain = _ordered(platform)
    if not chain:
        raise NothingWorked(platform, {})

    skipped: set[str] = set()
    for name, fn in chain:
        if name in skipped:
            logger.info("provider %s skipped for %s: an earlier route ruled it out", name, platform)
            continue
        started = time.perf_counter()
        timeout = _timeout_for(name)
        try:
            resolved = await asyncio.wait_for(fn(url), timeout=timeout)
            if not resolved.items:
                raise ProviderFailed("returned nothing", "missing")
        except ProviderFailed as exc:
            failures[name] = exc
            skipped.update(exc.skip)
            _record_fail(name, str(exc))
            logger.info("provider %s failed for %s: %s", name, platform, exc)
        except asyncio.TimeoutError:
            exc = ProviderFailed(f"timed out after {timeout:.0f}s", "error")
            failures[name] = exc
            _record_fail(name, str(exc))
            logger.info("provider %s timed out for %s", name, platform)
        except Exception as exc:  # a provider's own bug must not end the chain
            wrapped = ProviderFailed(f"{type(exc).__name__}: {exc}", "error")
            failures[name] = wrapped
            _record_fail(name, str(wrapped))
            logger.warning("provider %s raised for %s", name, platform, exc_info=True)
        else:
            _cut_items(resolved)
            _record_ok(name)
            logger.info("provider %s resolved %s in %.1fs (%d item(s))",
                        name, platform, time.perf_counter() - started,
                        len(resolved.items))
            return resolved

    raise NothingWorked(platform, failures)


async def lookup(platform: str, url: str) -> str:
    """Is the post still public? "present", "missing" or "unknown" -- asked of the
    routes without taking any media (sent_files.py: a stored file id is trusted
    only while the post was seen public in the last few hours).

    The platform's routes in their usual order, each under its usual limit, but
    yt-dlp is never asked: it downloads inside resolve (video.py), and the whole
    point here is to not download. And nothing is recorded -- no health score, no
    cooldown -- because a deleted post is the post's failure, not the route's, and
    a question is not traffic (the same rule as probe_all).

    "present" as soon as one route returns an item. "missing" only if every route
    asked said the post is missing: one route's word is not enough (tikwm reports
    a rate limit as missing). Anything else -- a timeout, a network error, a mix of
    answers, or no route left once yt-dlp is left out (Reddit has only yt-dlp) --
    is "unknown", and the caller then does not trust the stored file ids."""
    asked = said_missing = 0
    skipped: set[str] = set()
    only_asking = _LOOKUP.set(True)     # the relays that fetch inside the route: the answer, no file
    try:
        for name, fn in _ordered(platform):
            if name.startswith("ytdlp:") or name in skipped:
                continue
            asked += 1
            try:
                found = await asyncio.wait_for(fn(url), timeout=_timeout_for(name))
            except ProviderFailed as exc:
                skipped.update(exc.skip)
                if exc.kind == "missing":
                    said_missing += 1
                continue
            except Exception as exc:        # a timeout, a network error, a route's own bug: no answer
                logger.info("lookup: route %s gave no answer for %s (%s)", name, platform, type(exc).__name__)
                continue
            if found.items:
                return "present"
            said_missing += 1               # a route that returns nothing says what _resolve counts it as
    finally:
        _LOOKUP.reset(only_asking)
    return "missing" if asked and said_missing == asked else "unknown"


# ---------------------------------------------------------------------------
# Asking every route, on purpose
# ---------------------------------------------------------------------------
# resolve() stops at the first route that works, which is what a user wants
# and exactly the wrong thing for finding out what is going on. This asks all
# of them and reports each answer.
#
# It deliberately does not touch the health scores. A probe is a question, not
# traffic, and letting an operator's curiosity reorder the chain -- or worse,
# rest a route that a real user could have used -- would make the scores mean
# something other than "what happens to users".

# Public posts, each one confirmed to resolve when this was written. If a row
# comes back "missing" for EVERY route at once, suspect the sample rather than
# the internet: a real outage looks like one route failing, not all of them
# agreeing.
SAMPLES = {
    "instagram": "https://www.instagram.com/reel/DTxk5orCKEv/",
    "tiktok": "https://www.tiktok.com/@scout2015/video/6718335390845095173",
    "twitter": "https://x.com/SpaceX/status/2042988940756480302",
    "pinterest": "https://www.pinterest.com/pin/27725353928390009/",
    "youtube": "https://www.youtube.com/shorts/W05mCB2bDAU",
}

PROBE_FETCH_BYTES = 64 * 1024
# How long the whole media fetch of a probe row may take, however many
# addresses it tries. The route itself has its own timeout; this one is for
# what the route *returned*: a CDN address that accepts the connection and
# then sends a byte a minute never trips httpx's per-read timeout, and a
# probe is reachable over the bus, where ManagerBot gives up at 90 s.
PROBE_FETCH_TIMEOUT_S = 20.0


async def probe_route(name, fn, url: str, fetch: bool = False,
                      timeout: float | None = None) -> dict:
    """One route, one link. Never raises.

    The relays that fetch the file inside the route are asked the way lookup() asks them:
    for their answer. A check is given seconds, not the minutes a whole file may take, and
    `fetch` then proves the address serves with its first bytes, as for every other route."""
    only_asking = _LOOKUP.set(True)
    timeout = _timeout_for(name) if timeout is None else timeout
    started = time.perf_counter()
    row = {"provider": name, "url": url, "ok": False, "detail": "", "items": 0,
           "seconds": 0.0, "fetched": None}
    try:
        resolved = await asyncio.wait_for(fn(url), timeout=timeout)
    except ProviderFailed as exc:
        row["detail"] = f"{exc.kind}: {exc}"
    except asyncio.TimeoutError:
        row["detail"] = f"timed out after {timeout:.0f}s"
    except Exception as exc:
        row["detail"] = f"{type(exc).__name__}: {exc}"
    else:
        row["ok"] = True
        row["items"] = len(resolved.items)
        kinds = ", ".join(sorted({i.kind for i in resolved.items}))
        row["detail"] = f"{len(resolved.items)} item(s) [{kinds}]"
        if fetch and resolved.items:
            try:
                row["fetched"] = await asyncio.wait_for(_probe_fetch(resolved.items[0]),
                                                        PROBE_FETCH_TIMEOUT_S)
            except asyncio.TimeoutError:
                # "fetch failed" exactly: routewatch.check_platform reads the
                # start of this text to tell a route whose media does not serve.
                row["fetched"] = f"fetch failed: no media in {PROBE_FETCH_TIMEOUT_S:.0f}s"
    finally:
        _LOOKUP.reset(only_asking)
    row["seconds"] = round(time.perf_counter() - started, 1)
    return row


async def _probe_fetch(item: MediaItem) -> str:
    """Prove the media URL actually serves, without pulling the whole file.

    A GET abandoned after the first chunk, never a HEAD -- see net.py for the
    TikTok CDN's 503-on-HEAD. yt-dlp's items are already on disk, so they are
    measured and deleted instead.
    """
    if item.path:
        try:
            size = os.path.getsize(item.path)
            os.remove(item.path)
            return f"{size:,} bytes on disk"
        except OSError as exc:
            return f"file gone: {exc}"
    last = "no address to fetch"

    class CapReached(Exception):
        """Not an error here: the cap is what this fetch is for."""

    for route in [u for u in [item.url, *item.alt_urls] if u]:
        try:
            async with net.client().stream("GET", route) as resp:
                resp.raise_for_status()
                got = 0
                # Through body_chunks like every other body, so that a media
                # address which answers with a compressed bomb is stopped at the
                # cap instead of decoded whole -- this is the one place that used
                # to read a CDN's body without counting what it became.
                pieces = net.body_chunks(resp, PROBE_FETCH_BYTES, CapReached)
                try:
                    async for chunk in pieces:
                        got += len(chunk)
                        if got >= PROBE_FETCH_BYTES:
                            break
                except CapReached:
                    got = max(got, PROBE_FETCH_BYTES)     # more than the cap arrived: success
                finally:
                    await pieces.aclose()
                if not got:
                    # A relay the site has blocked answers with an address that sends nothing.
                    last = "it sent nothing"
                    continue
                return f"{got:,} bytes, {resp.headers.get('content-type', '?')}"
        except Exception as exc:
            last = f"{type(exc).__name__}: {exc}"
    return f"fetch failed: {last}"


async def probe_all(urls: dict[str, str] | None = None, fetch: bool = False,
                    timeout: float | None = None) -> dict:
    """Every route for every platform in `urls` (default: SAMPLES).

    Platforms run concurrently and the routes within one run in order, so the
    whole thing costs about as long as the slowest single chain rather than
    the sum of all of them. That matters because this is reachable over the
    family bus, where ManagerBot gives a command 90 seconds before calling it
    a timeout -- and a probe that cannot finish inside that window is a probe
    nobody can run from their phone.
    """
    urls = urls or SAMPLES

    async def one(platform: str, url: str) -> tuple[str, list[dict]]:
        rows = []
        for name, fn in PROVIDERS.get(platform, []):
            rows.append(await probe_route(name, fn, url, fetch=fetch, timeout=timeout))
        return platform, rows

    done = await asyncio.gather(*(one(p, u) for p, u in urls.items()))
    return {platform: rows for platform, rows in done}


# yt-dlp's errors are a paragraph each and every one of them quotes its own
# issue tracker. Telegram expands the first URL in a message into a full-width
# preview card, which is how a failed download once arrived as a GitHub
# repository with a logo (v1.1.3). A probe report can carry a dozen of those,
# so the URLs come out and the rest is trimmed to a line.
_URL_RE = re.compile(r"https?://\S+")
# The two places yt-dlp's text turns to its own advice. The text is cut at the
# first of them by a plain search for the words: the regex this replaced
# (`\s*;?\s*please report this issue on.*|...`) backtracked cubically on a run
# of whitespace -- 1000 spaces took a second, 10,000 about a quarter of an
# hour -- and the text can be a hostile cobalt instance's error code, on its
# way through a probe into the daily check's report (review, 2026-10-08).
_NOISE_MARKERS = ((re.compile("please report this issue on", re.IGNORECASE), True),     # True: and the ";" before it
                  (re.compile("confirm you are on the latest version", re.IGNORECASE), False))
# More than this is never shown, and none of it needs searching.
_TIDY_READS = 4000


def tidy_error(text: str, limit: int = 150) -> str:
    text = (text or "")[:_TIDY_READS]
    cuts = [(m.start(), semicolon) for marker, semicolon in _NOISE_MARKERS
            for m in [marker.search(text)] if m]
    if cuts:
        at, semicolon = min(cuts)
        text = text[:at].rstrip()
        if semicolon:                        # "...; please report this issue on ..."
            text = text[:-1].rstrip() if text.endswith(";") else text
    text = _URL_RE.sub("<link>", text)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def format_probe(results: dict) -> str:
    """The report, as plain text -- it goes to a Telegram message and to a
    terminal, and both want the same thing."""
    lines = []
    for platform, rows in results.items():
        lines.append(f"\n{platform}:")
        for row in rows:
            mark = "OK  " if row["ok"] else "FAIL"
            line = (f"  {mark} {row['provider']:<18} {row['seconds']:>4.1f}s  "
                    f"{tidy_error(row['detail'])}")
            if row["fetched"]:
                line += f"  [{row['fetched']}]"
            lines.append(line)
    return "\n".join(lines).strip()


# ---------------------------------------------------------------------------
# Getting the bytes
# ---------------------------------------------------------------------------

async def download(item: MediaItem, work_dir: str | None = None, max_mb: int | None = None) -> str:
    """Put one MediaItem on disk and return the path, no bigger than `max_mb`
    (the free ceiling if None).

    Already-local items (yt-dlp's) are handed straight back. Everything else
    is streamed, with a `Referer` some CDNs want and a size ceiling that stops
    a mis-detected link from filling the container's disk. `alt_urls` are
    other routes to the same file and are tried in order.
    """
    if item.path:
        return item.path
    routes = [u for u in [item.url, *item.alt_urls] if u]
    if not routes:
        raise FetchError("Nothing to download.")

    work_dir = work_dir or _WORK_DIR
    os.makedirs(work_dir, exist_ok=True)
    ext = _ext_of(item.filename, "mp4" if item.kind == "video" else "jpg")

    last: Exception | None = None
    for route in routes:
        path = os.path.join(work_dir, f"{uuid.uuid4().hex}.{ext}")
        headers = dict(item.headers)
        parsed = urlparse(route)
        headers.setdefault("Referer", f"{parsed.scheme}://{parsed.netloc}/")
        try:
            await net.stream_to_file(route, path, headers=headers,
                                     limit=max_mb * 1024 * 1024 if max_mb else net.MAX_RESPONSE_BYTES)
        except net.TooLarge:
            # Over the size ceiling. Another route to the same file will be
            # the same size, so there is nothing to gain by trying it. Only
            # this: any other FetchError (an encoding this bot does not read,
            # a body sent in specks) is one route failing, and the next may
            # serve the same file in a way that reads.
            if os.path.exists(path):
                os.remove(path)
            raise
        except Exception as exc:
            if os.path.exists(path):
                os.remove(path)
            last = exc
            logger.info("media route failed (%s: %s), trying the next one",
                        type(exc).__name__, exc)
            continue
        item.path = path
        return path
    raise FetchError(str(last) if last else "Nothing to download.")
