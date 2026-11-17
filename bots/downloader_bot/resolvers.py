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
import platforms
import sandbox
from net import CRAWLER_UA, FetchError

logger = logging.getLogger(__name__)

PROVIDER_TIMEOUT_S = float(os.environ.get("DBOT_PROVIDER_TIMEOUT", "20"))

YTDLP_TIMEOUT_S = float(os.environ.get("DBOT_YTDLP_TIMEOUT", "120"))
YTDLP_SECONDS_PER_MB = float(os.environ.get("DBOT_YTDLP_SECONDS_PER_MB", "0.5"))

COOLDOWN_AFTER_FAILURES = int(os.environ.get("DBOT_PROVIDER_COOLDOWN_AFTER", "3"))
COOLDOWN_BASE_S = float(os.environ.get("DBOT_PROVIDER_COOLDOWN_BASE", "120"))
COOLDOWN_MAX_S = float(os.environ.get("DBOT_PROVIDER_COOLDOWN_MAX", "3600"))


@dataclass
class MediaItem:
    kind: str
    url: str | None = None
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
    def __init__(self, message: str, kind: str = "error", skip=()):
        super().__init__(message)
        self.kind = kind
        self.skip = tuple(skip)


class NothingWorked(Exception):
    def __init__(self, platform: str, failures: dict[str, ProviderFailed]):
        self.platform = platform
        self.failures = failures
        super().__init__(
            f"{platform}: " + "; ".join(f"{n}: {e}" for n, e in failures.items())
        )

    @property
    def kind(self) -> str:
        kinds = {e.kind for e in self.failures.values()}
        for preferred in ("missing", "too_big", "blocked"):
            if preferred in kinds:
                return preferred
        return "error"


@dataclass
class Health:
    ok: int = 0
    failed: int = 0
    streak: int = 0
    last_ok: float | None = None
    last_fail: float | None = None
    last_error: str | None = None

    @property
    def cooling_until(self) -> float:
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
    dirty = {name: _health[name] for name in _health_dirty if name in _health}
    _health_dirty.clear()
    return dirty


def load(rows) -> None:
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


_META_OPEN_RE = re.compile("<meta", re.IGNORECASE)
_META_TAG_MAX = 4096


def _meta_tags(html: str):
    pos = 0
    while True:
        opening = _META_OPEN_RE.search(html, pos)
        if opening is None:
            return
        start = opening.start()
        close = html.find(">", start)
        if close < 0:
            return
        pos = close + 1
        if close - start > 5 and close - start < _META_TAG_MAX:
            yield html[start:close + 1]


def _meta(html: str, prop: str) -> str | None:
    wanted = re.compile(rf'(?:property|name)=["\']{re.escape(prop)}["\']', re.I)
    for tag in _meta_tags(html):
        if wanted.search(tag):
            m = re.search(r'content=["\']([^"\']+)["\']', tag, re.I)
            if m:
                return m.group(1).replace("&amp;", "&")
    return None


PAGE_LIMIT_BYTES = int(float(os.environ.get("DBOT_PAGE_LIMIT_MB", "8")) * 1024 * 1024)


async def _page(method: str, url: str, **kwargs) -> httpx.Response:
    netloc = urlparse(url).netloc

    def declared_too_big(declared: int):
        return ProviderFailed(f"{netloc} answered with {declared:,} bytes, "
                              "more than a page may be", "error")

    def too_big():
        return ProviderFailed(f"{netloc} kept sending past {PAGE_LIMIT_BYTES:,} bytes", "error")

    try:
        return await net.read_page(method, url, limit=PAGE_LIMIT_BYTES, too_big=too_big,
                                   declared_too_big=declared_too_big, **kwargs)
    except net.Trickle:
        raise ProviderFailed(f"{netloc} sent its answer in pieces too small to read", "error") from None
    except net.BadEncoding:
        raise ProviderFailed(f"{netloc} answered in an encoding this bot does not read", "error") from None


async def _get(url: str, *, crawler: bool = False, **kwargs) -> httpx.Response:
    headers = dict(kwargs.pop("headers", {}) or {})
    if crawler:
        headers.setdefault("User-Agent", CRAWLER_UA)
    return await _page("GET", url, headers=headers, **kwargs)


async def _post(url: str, **kwargs) -> httpx.Response:
    return await _page("POST", url, **kwargs)


def _ext_of(name: str, default: str) -> str:
    _, dot, ext = name.rpartition(".")
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


INSTAGRAM_CODE_RE = re.compile(
    r"instagram\.com/(?:[^/]+/)?(?:reels?|p|tv)/([A-Za-z0-9_-]+)", re.IGNORECASE
)


def instagram_code(url: str) -> str | None:
    m = INSTAGRAM_CODE_RE.search(url)
    return m.group(1) if m else None


def _jwt_payload(token: str) -> dict:
    part = token.split(".")[1]
    part += "=" * (-len(part) % 4)
    return json.loads(base64.urlsafe_b64decode(part))


async def _ig_downloadgram(url: str) -> Resolved:
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

    if any(i.kind == "video" for i in items):
        items = [i for i in items if i.kind == "video"]
    return Resolved("instagram", "downloadgram", items)


async def _ig_instafix(url: str) -> Resolved:
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


async def _tt_tikwm(url: str) -> Resolved:
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
    resp = await _get(re.sub(r"https?://(?:www\.|vm\.|vt\.|m\.)?tiktok\.com",
                             "https://tnktok.com", url, flags=re.I), crawler=True)
    resp.raise_for_status()
    video = _meta(resp.text, "og:video") or _meta(resp.text, "twitter:player:stream")
    if not video:
        raise ProviderFailed("tnktok exposed no video for that link", "missing")
    return Resolved("tiktok", "tnktok",
                    [MediaItem("video", url=video, filename="tiktok.mp4")])


TWEET_ID_RE = re.compile(r"/status(?:es)?/(\d+)")


def tweet_id(url: str) -> str | None:
    m = TWEET_ID_RE.search(url)
    return m.group(1) if m else None


async def _tw_api(url: str, host: str, provider: str) -> Resolved:
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


_PIN_RESOURCE = "https://www.pinterest.com/resource/PinResource/get/"
_PIN_ID_RE = re.compile(r"/pin/(?:[^/]*?--)?(\d+)")


def _pin_best_video(video_list: dict | None) -> str | None:
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
    top_list = (data.get("videos") or {}).get("video_list")
    saw_video = bool(top_list)
    video = _pin_best_video(top_list)
    if video:
        items.append(MediaItem("video", url=video, filename="pinterest.mp4"))

    for page in ((data.get("story_pin_data") or {}).get("pages") or []):
        for block in (page.get("blocks") or []):
            block_list = (block.get("video") or {}).get("video_list")
            saw_video = saw_video or bool(block_list)
            block_video = _pin_best_video(block_list)
            if block_video:
                items.append(MediaItem("video", url=block_video,
                                       filename=f"pinterest_{len(items) + 1}.mp4"))

    if not items and saw_video:
        raise ProviderFailed(
            "that pin is a video Pinterest serves only as a stream, which this route cannot fetch",
            "error", skip=("pinterest_og",))

    if not items:
        original = ((data.get("images") or {}).get("orig") or {}).get("url")
        if not original:
            raise ProviderFailed("that pin has no video or image", "missing")
        items.append(MediaItem("photo", url=original,
                               filename=f"pinterest.{_ext_of(urlparse(original).path, 'jpg')}"))
    return Resolved("pinterest", "api", items)


async def _pin_og(url: str) -> Resolved:
    resp = await _get(url)
    resp.raise_for_status()
    video = _meta(resp.text, "og:video") or _meta(resp.text, "og:video:secure_url")
    if video:
        return Resolved("pinterest", "og", [MediaItem("video", url=video,
                                                      filename="pinterest.mp4")])
    image = _meta(resp.text, "og:image")
    if not image:
        raise ProviderFailed("that pin has no image or video tag", "missing")
    original = re.sub(r"/(?:\d{2,4}x\d{0,4}|originals)/", "/originals/", image, count=1)
    routes = [original, image] if original != image else [image]
    return Resolved("pinterest", "og",
                    [MediaItem("photo", url=routes[0], alt_urls=routes[1:],
                               filename=f"pinterest.{_ext_of(urlparse(image).path, 'jpg')}")])


COBALT_INSTANCES = [
    host.strip().rstrip("/") for host in os.environ.get(
        "DBOT_COBALT_INSTANCES",
        "https://api.cobalt.liubquanti.click,https://cobaltapi.cjs.nz,https://dwnld.nichind.dev",
    ).split(",") if host.strip()
]
COBALT_API_KEY = os.environ.get("DBOT_COBALT_API_KEY") or None
COBALT_QUALITY = os.environ.get("DBOT_COBALT_QUALITY", "720")
COBALT_INSTANCE_TIMEOUT_S = float(os.environ.get("DBOT_COBALT_INSTANCE_TIMEOUT", "15"))
COBALT_TIMEOUT_S = float(os.environ.get("DBOT_COBALT_TIMEOUT", "240"))
COBALT_STEP_DOWN = (480, 360)

_COBALT_MISSING = ("content.video.unavailable", "content.post.unavailable",
                   "content.video.private", "content.post.private",
                   "content.video.age", "content.post.age", "link.invalid",
                   "link.unsupported", "content.video.live")
_COBALT_TOO_BIG = ("content.too_long", "content.too_large")
_COBALT_BLOCKED = (".login", "auth.", "fetch.rate", "rate_exceeded", "too_many_requests",
                   "content.video.region", platforms.UNADVERTISED + ".decipher",
                   platforms.UNADVERTISED + ".token",
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
            kind = "photo" if entry.get("type") == "photo" else "video"
            items.append(MediaItem(kind, url=url,
                                   filename=f"{platform}.{'jpg' if kind == 'photo' else 'mp4'}"))
        return items
    if status == "local-processing":
        tunnels = data.get("tunnel") or []
        if len(tunnels) == 1:
            return [MediaItem("video", url=tunnels[0], filename=f"{platform}.mp4")]
    return []


async def _cobalt_one(host: str, url: str, platform: str, key: str | None,
                      quality: "str | None" = None) -> Resolved:
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Api-Key {key}"
    if quality == COBALT_AUDIO:
        body = {"url": url, "downloadMode": "audio", "audioFormat": "mp3", "filenameStyle": "basic"}
    else:
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
    if quality == COBALT_AUDIO:
        items = [i for i in items if i.kind == "audio"]
        if not items:
            raise ProviderFailed(f"{urlparse(host).netloc}: no sound in its answer", "error")
    elif any(i.kind == "video" for i in items) and platform == platforms.UNADVERTISED:
        items = [i for i in items if i.kind == "video"]
    return Resolved(platform, "cobalt", items)


COBALT_HEIGHTS = (144, 240, 360, 480, 720, 1080, 1440, 2160)
COBALT_AUDIO = "audio"


def _cobalt_quality() -> str:
    asked = _HEIGHT.get()
    if not asked or not asked[0]:
        return COBALT_QUALITY
    under = [h for h in COBALT_HEIGHTS if h <= asked[0]]
    return str(under[-1] if under else COBALT_HEIGHTS[0])


MIN_MEDIA_BYTES = 1024


def _unlink(path: "str | None") -> None:
    if path:
        try:
            os.remove(path)
        except OSError:
            pass


def _looks_like_video(path: str) -> bool:
    try:
        with open(path, "rb") as handle:
            head = handle.read(12)
    except OSError:
        return False
    return head[4:8] == b"ftyp" or head[:4] == b"\x1a\x45\xdf\xa3"


def _limit_bytes() -> int:
    asked = _LIMIT_MB.get()
    return asked * 1024 * 1024 if asked else net.MAX_RESPONSE_BYTES


async def _take(url: str, ext: str, limit: int) -> str:
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
    kinds = {f.kind for f in failures}
    return "blocked" if "blocked" in kinds else "missing" if kinds == {"missing"} else "error"


async def _cobalt_take(resolved: Resolved, netloc: str) -> None:
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
    if _SOUND.get():
        return [COBALT_AUDIO]
    first = _cobalt_quality()
    asked = _HEIGHT.get()
    if asked and asked[0] and asked[1]:
        return [first]
    top = int(first) if first.isdigit() else COBALT_HEIGHTS[-1]
    return [first] + [str(height) for height in COBALT_STEP_DOWN if height < top]


async def _cobalt_fetch(host: str, url: str, platform: str, key: "str | None") -> Resolved:
    netloc = urlparse(host).netloc
    qualities = _cobalt_qualities()
    for number, quality in enumerate(qualities, start=1):
        try:
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
    raise ProviderFailed(f"{netloc}: nothing asked for", "error")


def _cobalt_provider(platform: str):
    async def provider(url: str) -> Resolved:
        global _cobalt_last_good
        if not COBALT_INSTANCES:
            raise ProviderFailed("no cobalt instances configured", "error")
        hosts = list(COBALT_INSTANCES)
        if _cobalt_last_good in hosts:
            hosts.remove(_cobalt_last_good)
            hosts.insert(0, _cobalt_last_good)
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


INVIDIOUS_INSTANCES = [
    host.strip().rstrip("/") for host in os.environ.get(
        "DBOT_INVIDIOUS_INSTANCES", "https://yt.chocolatemoo53.com").split(",") if host.strip()
]
INVIDIOUS_DIRECTORY = os.environ.get("DBOT_INVIDIOUS_DIRECTORY", "https://api.invidious.io/instances.json").strip()
INVIDIOUS_DIRECTORY_MAX = 6
INVIDIOUS_DIRECTORY_EVERY_S = 6 * 3600.0
INVIDIOUS_DIRECTORY_RETRY_S = 900.0
INVIDIOUS_INSTANCE_TIMEOUT_S = float(os.environ.get("DBOT_INVIDIOUS_INSTANCE_TIMEOUT", "15"))
INVIDIOUS_TIMEOUT_S = float(os.environ.get("DBOT_INVIDIOUS_TIMEOUT", "240"))
INSTANCE_SHARE = 0.75
INVIDIOUS_SIZE_MARGIN = 0.96
MUX_TIMEOUT_S = 120.0

_invidious_listed: list[str] = []
_invidious_listed_next = 0.0
_invidious_last_good: str | None = None

_UA_ID_RE = re.compile(r"(?:[?&]v=|" + re.escape(platforms.UNADVERTISED_SHORT_HOST)
                       + r"/|/shorts/|/live/|/embed/)([A-Za-z0-9_-]{11})(?![A-Za-z0-9_-])")

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
    global _invidious_listed, _invidious_listed_next
    now = time.monotonic()
    if INVIDIOUS_DIRECTORY not in ("", "0") and now >= _invidious_listed_next:
        _invidious_listed_next = now + INVIDIOUS_DIRECTORY_RETRY_S
        try:
            resp = await asyncio.wait_for(_get(INVIDIOUS_DIRECTORY, timeout=10.0), timeout=10.0)
            resp.raise_for_status()
            _invidious_listed = _invidious_from_directory(resp.json())
            _invidious_listed_next = now + INVIDIOUS_DIRECTORY_EVERY_S
        except Exception as exc:
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
    size = re.fullmatch(r"(\d+)x(\d+)", str(fmt.get("size") or ""))
    if size:
        return int(size.group(2))
    label = re.match(r"(\d+)p", str(fmt.get("resolution") or fmt.get("qualityLabel") or ""))
    return int(label.group(1)) if label else 0


def _invidious_quality(fmt: dict) -> int:
    label = re.match(r"(\d{3,4})p", str(fmt.get("qualityLabel") or fmt.get("resolution") or ""))
    if label:
        return int(label.group(1))
    size = re.fullmatch(r"(\d+)x(\d+)", str(fmt.get("size") or ""))
    if size:
        return min(int(size.group(1)), int(size.group(2)))
    return _invidious_height(fmt)


def _invidious_track(fmt: dict) -> int:
    tags = (parse_qs(urlsplit(str(fmt.get("url") or "")).query).get("xtags") or [""])[0]
    if "acont=" not in tags:
        return 1
    return 2 if "acont=original" in tags else 0


def _invidious_pick(formats, budget_bytes: int, height: "int | None" = None, exact: bool = False,
                    max_height: int = 1080) -> "tuple[dict, dict]":
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
                and (_invidious_quality(fmt) == height if exact and height is not None
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


def _invidious_sound_format(formats) -> dict:
    sounds = [fmt for fmt in formats or () if isinstance(fmt, dict) and fmt.get("url")
              and str(fmt.get("type") or "").startswith("audio/mp4") and _number(fmt.get("clen"))]
    if not sounds:
        raise ProviderFailed("no AAC sound is listed", "error")
    return max(sounds, key=lambda fmt: (_invidious_track(fmt), min(_number(fmt.get("bitrate")), 192_000)))


def _invidious_local(host: str, media_url: str) -> str:
    parts = urlsplit(media_url)
    if parts.path != "/videoplayback" or not parts.query:
        raise ProviderFailed(f"{urlparse(host).netloc}: a file address that is not a videoplayback one", "error")
    query = parts.query
    theirs = (parts.hostname or "").lower()
    if theirs.endswith("." + platforms.UNADVERTISED_MEDIA_HOST) and "host=" not in query:
        query += f"&host={theirs}"
    return f"{host}/videoplayback?{query}"


async def _mux(picture: str, sound: str, out: str) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise ProviderFailed("ffmpeg is not installed", "error")
    argv = [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y"]
    for path in (picture, sound):
        argv += ["-protocol_whitelist", "file", "-f", "mp4", "-i", path]
    argv += ["-map", "0:v:0", "-map", "1:a:0", "-c", "copy", "-movflags", "+faststart", "-f", "mp4", out]
    folders = sorted({os.path.dirname(os.path.abspath(p)) for p in (picture, sound, out)})
    proc = await asyncio.create_subprocess_exec(
        *sandbox.wrap(argv, folders), stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL,
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
    limit = _limit_bytes()
    parts: list = [None, None]
    out = None
    tasks = [asyncio.ensure_future(_take(picture_url, "mp4", limit)),
             asyncio.ensure_future(_take(sound_url, "m4a", limit))]
    try:
        try:
            parts = list(await asyncio.gather(*tasks))
        except BaseException:
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
    import video
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
    if _SOUND.get():
        return await _invidious_sound(host, data, platform)
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


async def _invidious_sound(host: str, data: dict, platform: str) -> Resolved:
    netloc = urlparse(host).netloc
    sound = _invidious_sound_format(data.get("adaptiveFormats"))
    expected = _number(sound.get("clen"))
    if expected > _limit_bytes() * INVIDIOUS_SIZE_MARGIN:
        raise ProviderFailed("the sound alone is over the size limit", "too_big")
    sound_url = _invidious_local(host, sound["url"])
    if _LOOKUP.get():
        return Resolved(platform, "invidious", [MediaItem("audio", url=sound_url, filename=f"{platform}.m4a")])
    path = await _take(sound_url, "m4a", _limit_bytes())
    size = os.path.getsize(path)
    if size < MIN_MEDIA_BYTES or size < expected or not _looks_like_video(path):
        _unlink(path)
        raise ProviderFailed(f"{netloc}: sent {size:,} of the sound's {expected:,} bytes", "blocked")
    return Resolved(platform, "invidious", [MediaItem("audio", path=path, filename=f"{platform}.m4a")])


def _invidious_provider(platform: str):
    async def provider(url: str) -> Resolved:
        global _invidious_last_good
        found = _UA_ID_RE.search(url)
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
                if exc.kind == "too_big":
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


def _ytdlp_provider(platform: str):
    async def provider(url: str) -> Resolved:
        import video
        stop = threading.Event()
        try:
            height, exact = _HEIGHT.get() or (None, False)
            path = await asyncio.to_thread(video.download_video, url, _WORK_DIR, _LIMIT_MB.get(), stop,
                                           height, exact, _SOUND.get())
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
                        [MediaItem("audio" if _SOUND.get() else "video", path=path,
                                   filename=os.path.basename(path))])
    return provider


_WORK_DIR = os.environ.get("DBOT_WORK_DIR") or tempfile.gettempdir()


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
    "pinterest": [
        ("pinterest_api", _pin_api),
        ("pinterest_og", _pin_og),
        ("ytdlp:pinterest", _ytdlp_provider("pinterest")),
    ],
    "reddit": [
        ("ytdlp:reddit", _ytdlp_provider("reddit")),
    ],
    platforms.UNADVERTISED: [
        (f"ytdlp:{platforms.UNADVERTISED}", _ytdlp_provider(platforms.UNADVERTISED)),
        (f"cobalt:{platforms.UNADVERTISED}", _cobalt_provider(platforms.UNADVERTISED)),
        (f"invidious:{platforms.UNADVERTISED}", _invidious_provider(platforms.UNADVERTISED)),
    ],
}


def _ordered(platform: str) -> list[tuple[str, object]]:
    now = time.time()
    ready, cooling = [], []
    for entry in PROVIDERS.get(platform, []):
        h = health(entry[0])
        (cooling if h.cooling_until > now else ready).append(entry)
    return ready + cooling


_LIMIT_MB: contextvars.ContextVar = contextvars.ContextVar("download_limit_mb", default=None)
_HEIGHT: contextvars.ContextVar = contextvars.ContextVar("download_height", default=None)
_SOUND: contextvars.ContextVar = contextvars.ContextVar("download_sound", default=False)
_LOOKUP: contextvars.ContextVar = contextvars.ContextVar("lookup_only", default=False)


def _timeout_for(name: str) -> float:
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
                  height: "int | None" = None, exact: bool = False, sound: bool = False) -> Resolved:
    token = _LIMIT_MB.set(max_mb)
    wanted = _HEIGHT.set((height, exact) if height else None)
    heard = _SOUND.set(sound)
    try:
        return await _resolve(platform, url)
    finally:
        _SOUND.reset(heard)
        _HEIGHT.reset(wanted)
        _LIMIT_MB.reset(token)


PICK_PLATFORMS = (platforms.UNADVERTISED,)
PICK_LIST_TIMEOUT_S = float(os.environ.get("DBOT_PICK_LIST_TIMEOUT")
                            or os.environ.get("DBOT_QUALITY_LOOKUP_TIMEOUT") or 10)
PICK_INVIDIOUS_HOSTS = 2
AUDIO = "audio"


@dataclass
class Choices:
    title: "str | None"
    duration: "int | None"
    thumbnail: "str | None"
    qualities: "list[tuple[int, int, bool]]"
    audio: "int | None"

    @property
    def worth_asking(self) -> bool:
        return len(self.qualities) + (1 if self.audio else 0) >= 2

    def offers(self, choice) -> bool:
        if choice == AUDIO:
            return bool(self.audio)
        return any(quality == choice for quality, _, _ in self.qualities)


def _thumbnail_for(video_id: "str | None") -> "str | None":
    return f"https://{platforms.UNADVERTISED_THUMBNAIL_HOST}/vi/{video_id}/hqdefault.jpg" if video_id else None


async def _choices_ytdlp(url: str, paid_bytes: int) -> Choices:
    import video
    info = await asyncio.to_thread(video.read_info, url)
    if info.get("is_live"):
        raise ProviderFailed("a live stream", "missing")
    if (info.get("duration") or 0) > video.MAX_DURATION_S:
        raise ProviderFailed("over the length limit", "too_big")
    found = video.choices_of(info, _limit_bytes(), paid_bytes)
    duration = info.get("duration")
    return Choices(info.get("title"), int(duration) if isinstance(duration, (int, float)) else None,
                   info.get("thumbnail") or _thumbnail_for(info.get("id")), found["qualities"], found["audio"])


def _invidious_choices(data: dict, video_id: str, paid_bytes: int) -> Choices:
    import video
    formats = [fmt for fmt in data.get("adaptiveFormats") or () if isinstance(fmt, dict) and fmt.get("url")]
    free_room = _limit_bytes() * INVIDIOUS_SIZE_MARGIN
    paid_room = paid_bytes * INVIDIOUS_SIZE_MARGIN
    try:
        sound = _invidious_sound_format(formats)
    except ProviderFailed:
        sound = None
    sound_bytes = _number(sound.get("clen")) if sound else 0
    free, paid = [], []
    found = sorted({_invidious_quality(fmt) for fmt in formats
                    if str(fmt.get("type") or "").startswith("video/mp4") and "avc1" in str(fmt.get("type"))
                    and _number(fmt.get("clen"))})
    for quality in found:
        if not video.PICK_MIN_QUALITY <= quality <= video.MAX_HEIGHT or not sound:
            continue
        try:
            picture, _ = _invidious_pick(formats, max(_limit_bytes(), paid_bytes), quality, True,
                                         video.MAX_HEIGHT)
        except ProviderFailed:
            continue
        size = _number(picture.get("clen")) + sound_bytes
        if size <= free_room:
            free.append((quality, size, True))
        elif size <= paid_room:
            paid.append((quality, size, False))
    seconds = _number(data.get("lengthSeconds"))
    return Choices(data.get("title"), seconds or None, _thumbnail_for(video_id),
                   free[-video.PICK_MAX_QUALITIES:] + paid[:video.PICK_MAX_QUALITIES],
                   sound_bytes if sound and sound_bytes <= free_room else None)


async def _choices_invidious(url: str, paid_bytes: int) -> Choices:
    import video
    found = _UA_ID_RE.search(url)
    if not found:
        raise ProviderFailed("no video id in that link", "missing")
    for host in (await _invidious_hosts())[:PICK_INVIDIOUS_HOSTS]:
        try:
            resp = await _get(f"{host}/api/v1/videos/{found.group(1)}", params={"local": "true"},
                              timeout=INVIDIOUS_INSTANCE_TIMEOUT_S)
            data = resp.json()
        except Exception as exc:
            logger.info("picker: %s did not list the versions (%s)", urlparse(host).netloc, type(exc).__name__)
            continue
        if resp.status_code != 200 or not isinstance(data, dict) or data.get("error") \
                or data.get("liveNow") or data.get("isUpcoming"):
            continue
        if _number(data.get("lengthSeconds")) > video.MAX_DURATION_S:
            raise ProviderFailed("over the length limit", "too_big")
        return _invidious_choices(data, found.group(1), paid_bytes)
    raise ProviderFailed("no Invidious instance listed the versions", "error")


_PICK_LISTERS = ((f"ytdlp:{platforms.UNADVERTISED}", "_choices_ytdlp"),
                 (f"invidious:{platforms.UNADVERTISED}", "_choices_invidious"))


async def _list_choices(url: str, paid_bytes: int) -> "Choices | None":
    now = time.time()
    for name, fn_name in _PICK_LISTERS:
        if health(name).cooling_until > now:
            logger.info("picker: %s is resting, not asked", name)
            continue
        try:
            found = await globals()[fn_name](url, paid_bytes)
        except Exception as exc:
            logger.info("picker: %s could not list the versions (%s: %s)", name, type(exc).__name__,
                        str(exc)[:150])
            continue
        if found.worth_asking:
            return found
        logger.info("picker: %s listed nothing worth a choice", name)
    return None


async def choices(platform: str, url: str, paid_bytes: int = 0) -> "Choices | None":
    if platform not in PICK_PLATFORMS:
        return None
    try:
        return await asyncio.wait_for(_list_choices(url, paid_bytes), timeout=PICK_LIST_TIMEOUT_S)
    except asyncio.TimeoutError:
        logger.info("picker: nothing listed the versions in %.0fs", PICK_LIST_TIMEOUT_S)
    except Exception:
        logger.warning("picker: listing the versions failed", exc_info=True)
    return None


MAX_ITEMS_PER_POST = max(1, int(os.environ.get("DBOT_MAX_ITEMS_PER_POST", "40")))


def _cut_items(resolved: Resolved) -> None:
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
        except Exception as exc:
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
    asked = said_missing = 0
    skipped: set[str] = set()
    only_asking = _LOOKUP.set(True)
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
            except Exception as exc:
                logger.info("lookup: route %s gave no answer for %s (%s)", name, platform, type(exc).__name__)
                continue
            if found.items:
                return "present"
            said_missing += 1
    finally:
        _LOOKUP.reset(only_asking)
    return "missing" if asked and said_missing == asked else "unknown"


SAMPLES = {
    "instagram": "https://www.instagram.com/reel/DTxk5orCKEv/",
    "tiktok": "https://www.tiktok.com/@scout2015/video/6718335390845095173",
    "twitter": "https://x.com/SpaceX/status/2042988940756480302",
    "pinterest": "https://www.pinterest.com/pin/27725353928390009/",
    platforms.UNADVERTISED: f"https://www.{platforms.UNADVERTISED}.com/shorts/W05mCB2bDAU",
}

PROBE_FETCH_BYTES = 64 * 1024
PROBE_FETCH_TIMEOUT_S = 20.0


async def probe_route(name, fn, url: str, fetch: bool = False,
                      timeout: float | None = None) -> dict:
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
                row["fetched"] = f"fetch failed: no media in {PROBE_FETCH_TIMEOUT_S:.0f}s"
    finally:
        _LOOKUP.reset(only_asking)
    row["seconds"] = round(time.perf_counter() - started, 1)
    return row


async def _probe_fetch(item: MediaItem) -> str:
    if item.path:
        try:
            size = os.path.getsize(item.path)
            os.remove(item.path)
            return f"{size:,} bytes on disk"
        except OSError as exc:
            return f"file gone: {exc}"
    last = "no address to fetch"

    class CapReached(Exception):
        pass

    for route in [u for u in [item.url, *item.alt_urls] if u]:
        try:
            async with net.client().stream("GET", route) as resp:
                resp.raise_for_status()
                got = 0
                pieces = net.body_chunks(resp, PROBE_FETCH_BYTES, CapReached)
                try:
                    async for chunk in pieces:
                        got += len(chunk)
                        if got >= PROBE_FETCH_BYTES:
                            break
                except CapReached:
                    got = max(got, PROBE_FETCH_BYTES)
                finally:
                    await pieces.aclose()
                if not got:
                    last = "it sent nothing"
                    continue
                return f"{got:,} bytes, {resp.headers.get('content-type', '?')}"
        except Exception as exc:
            last = f"{type(exc).__name__}: {exc}"
    return f"fetch failed: {last}"


async def probe_all(urls: dict[str, str] | None = None, fetch: bool = False,
                    timeout: float | None = None) -> dict:
    urls = urls or SAMPLES

    async def one(platform: str, url: str) -> tuple[str, list[dict]]:
        rows = []
        for name, fn in PROVIDERS.get(platform, []):
            rows.append(await probe_route(name, fn, url, fetch=fetch, timeout=timeout))
        return platform, rows

    done = await asyncio.gather(*(one(p, u) for p, u in urls.items()))
    return {platform: rows for platform, rows in done}


_URL_RE = re.compile(r"https?://\S+")
_NOISE_MARKERS = ((re.compile("please report this issue on", re.IGNORECASE), True),
                  (re.compile("confirm you are on the latest version", re.IGNORECASE), False))
_TIDY_READS = 4000


def tidy_error(text: str, limit: int = 150) -> str:
    text = (text or "")[:_TIDY_READS]
    cuts = [(m.start(), semicolon) for marker, semicolon in _NOISE_MARKERS
            for m in [marker.search(text)] if m]
    if cuts:
        at, semicolon = min(cuts)
        text = text[:at].rstrip()
        if semicolon:
            text = text[:-1].rstrip() if text.endswith(";") else text
    text = _URL_RE.sub("<link>", text)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def format_probe(results: dict) -> str:
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


async def download(item: MediaItem, work_dir: str | None = None, max_mb: int | None = None) -> str:
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
