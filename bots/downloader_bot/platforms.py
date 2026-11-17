import base64
import math
import re
from urllib.parse import parse_qsl, urlsplit

import httpx

import net
from net import FetchError, client, close_client, fetch_bytes

__all__ = [
    "FetchError", "client", "close_client", "fetch_bytes",
    "detect_platform", "canonical_link", "ANY_LINK_RE",
    "fetch_reddit_post", "reddit_is_media", "reddit_direct_image_url",
    "fetch_tweet_syndication", "UNADVERTISED", "UNADVERTISED_SHORT_HOST",
]


def _spelled(encoded: str) -> str:
    return base64.b64decode(encoded).decode("ascii")


UNADVERTISED = _spelled("eW91dHViZQ==")
UNADVERTISED_SHORT_HOST = _spelled("eW91dHUuYmU=")
UNADVERTISED_THUMBNAIL_HOST = "i." + _spelled("eXRpbWcuY29t")
UNADVERTISED_MEDIA_HOST = _spelled("Z29vZ2xldmlkZW8uY29t")

INSTAGRAM_RE = re.compile(
    r"https?://(?:www\.|m\.)?instagram\.com/\S+", re.IGNORECASE
)
TIKTOK_RE = re.compile(
    r"https?://(?:www\.|vm\.|vt\.|m\.)?tiktok\.com/\S+", re.IGNORECASE
)
PINTEREST_RE = re.compile(
    r"https?://(?:[a-z0-9-]{1,8}\.)?"
    r"(?:pinterest\.(?:com|co\.[a-z]{2}|com\.[a-z]{2}|[a-z]{2})|pin\.it)/\S+",
    re.IGNORECASE,
)
REDDIT_RE = re.compile(
    r"https?://(?:www\.|old\.|m\.)?reddit\.com/r/\S+|https?://redd\.it/\S+",
    re.IGNORECASE,
)
TWITTER_RE = re.compile(
    r"https?://(?:www\.|mobile\.)?(?:twitter\.com|x\.com)/\w+/status(?:es)?/\d+",
    re.IGNORECASE,
)
UNADVERTISED_RE = re.compile(
    r"https?://(?:www\.|m\.|music\.)?(?:" + re.escape(UNADVERTISED) + r"\.com/"
    r"(?:watch\?(?:[^\s#]*&)?v=|shorts/|live/|embed/)"
    r"|" + re.escape(UNADVERTISED_SHORT_HOST) + r"/)\S+",
    re.IGNORECASE,
)

_PLATFORM_PATTERNS = [
    ("instagram", INSTAGRAM_RE),
    ("tiktok", TIKTOK_RE),
    (UNADVERTISED, UNADVERTISED_RE),
    ("reddit", REDDIT_RE),
    ("twitter", TWITTER_RE),
    ("pinterest", PINTEREST_RE),
]

ANY_LINK_RE = re.compile(
    "|".join(f"(?:{p.pattern})" for _, p in _PLATFORM_PATTERNS), re.IGNORECASE
)


_TRAILING_PUNCT = ".,;:!?)]}>»\"'"

_IG_HOSTS = {"instagram.com", "www.instagram.com", "m.instagram.com"}
_IG_POST = re.compile(r"/(p|reel|reels|tv)/([A-Za-z0-9_-]+)(?:/|$)")
_IG_USER_POST = re.compile(r"/([^/]+)" + _IG_POST.pattern)
_IG_NOT_USERS = {"share", "stories"}

_TT_HOSTS = {"tiktok.com", "www.tiktok.com", "m.tiktok.com"}
_TT_SHORT_HOSTS = {"vm.tiktok.com", "vt.tiktok.com"}

_UA_HOSTS = {prefix + UNADVERTISED + ".com" for prefix in ("", "www.", "m.", "music.")}
_UA_ID = re.compile(r"[A-Za-z0-9_-]{11}")

_TW_HOSTS = {"twitter.com", "www.twitter.com", "mobile.twitter.com",
             "x.com", "www.x.com", "mobile.x.com"}
_TW_STATUS = re.compile(r"/(\w+)/status(?:es)?/(\d+)(?:/|$)")

_RD_HOSTS = {"reddit.com", "www.reddit.com", "old.reddit.com", "m.reddit.com"}

_PIN_HOST = re.compile(
    r"(?:[a-z0-9-]{1,8}\.)?"
    r"(?:pinterest\.(?:com|co\.[a-z]{2}|com\.[a-z]{2}|[a-z]{2})|pin\.it)"
)
_PIN_POST = re.compile(r"/pin/(\d+)(?:/|$)")


def _trim(text: str) -> str:
    return text.rstrip(_TRAILING_PUNCT)


def _keep_path(host: str, path: str) -> str:
    return f"https://{host}{_trim(path)}"


def _canonical(platform: str, url: str) -> str:
    parts = urlsplit(_trim(url.strip()))
    host = (parts.hostname or "").lower()
    path = parts.path

    if platform == "instagram" and host in _IG_HOSTS:
        m = _IG_USER_POST.match(path)
        if m and m.group(1).lower() in _IG_NOT_USERS:
            m = None
        if m:
            kind, code = m.group(2), m.group(3)
        else:
            m = _IG_POST.match(path)
            kind, code = (m.group(1), m.group(2)) if m else ("", "")
        if kind:
            return f"https://www.instagram.com/{'reel' if kind == 'reels' else kind}/{code}/"
        return _keep_path("www.instagram.com", path)

    if platform == "tiktok":
        if host in _TT_HOSTS:
            return _keep_path("www.tiktok.com", path)
        if host in _TT_SHORT_HOSTS:
            return _keep_path(host, path)

    if platform == UNADVERTISED:
        video = ""
        if host in _UA_HOSTS:
            segments = [s for s in path.split("/") if s]
            if path.rstrip("/") == "/watch":
                values = [v for k, v in parse_qsl(parts.query, keep_blank_values=True) if k == "v"]
                video = values[0] if values else ""
            elif len(segments) >= 2 and segments[0] in ("shorts", "live", "embed"):
                video = segments[1]
        elif host == UNADVERTISED_SHORT_HOST:
            segments = [s for s in path.split("/") if s]
            video = segments[0] if segments else ""
        if _UA_ID.fullmatch(video):
            return watch_link(video)
        return url

    if platform == "twitter" and host in _TW_HOSTS:
        m = _TW_STATUS.match(path)
        if m:
            return f"https://x.com/{m.group(1)}/status/{m.group(2)}"
        return url

    if platform == "reddit":
        if host in _RD_HOSTS:
            return _keep_path("www.reddit.com", path)
        if host == "redd.it":
            return _keep_path("redd.it", path)

    if platform == "pinterest" and _PIN_HOST.fullmatch(host):
        m = _PIN_POST.match(path)
        if m and host != "pin.it":
            return f"https://www.pinterest.com/pin/{m.group(1)}/"
        return _keep_path(host, path)

    return url


def watch_link(video_id: str) -> str:
    return f"https://www.{UNADVERTISED}.com/watch?v={video_id}"


def canonical_link(platform: str, url: str) -> str:
    try:
        return _canonical(platform, url)
    except Exception:  # noqa: BLE001 - an unreadable link goes through as sent
        return url


def detect_platform(text: str) -> tuple[str, str] | None:
    for name, pattern in _PLATFORM_PATTERNS:
        m = pattern.search(text)
        if m:
            return name, canonical_link(name, m.group(0))
    return None


TEXT_PAGE_LIMIT_BYTES = 8 * 1024 * 1024


async def fetch_reddit_post(url: str) -> dict:
    json_url = url.split("?")[0].rstrip("/") + ".json"
    headers = {
        "Accept": "application/json,text/html;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    unreadable = "Couldn't read that Reddit post -- it may have been removed."
    try:
        resp = await net.read_page("GET", json_url, headers=headers, timeout=15,
                                   limit=TEXT_PAGE_LIMIT_BYTES, too_big=lambda: FetchError(unreadable))
    except net.BadEncoding as exc:
        raise FetchError(unreadable) from exc
    if resp.status_code in (403, 429):
        raise FetchError(
            "Reddit blocked this request (their anti-scraping, not a bug here) -- "
            "try again later, or open the link directly."
        )
    resp.raise_for_status()
    data = resp.json()
    try:
        return data[0]["data"]["children"][0]["data"]
    except (IndexError, KeyError, TypeError) as exc:
        raise FetchError("Couldn't read that Reddit post -- it may have been removed.") from exc


def reddit_is_media(post: dict) -> bool:
    hint = post.get("post_hint", "")
    return bool(post.get("is_video")) or hint in ("image", "hosted:video", "rich:video")


def reddit_direct_image_url(post: dict) -> str | None:
    if post.get("post_hint") == "image":
        return post.get("url_overridden_by_dest") or post.get("url")
    return None


def _tweet_id_from_url(url: str) -> str | None:
    m = re.search(r"/status(?:es)?/(\d+)", url)
    return m.group(1) if m else None


def _base36(x: float, precision: int = 60) -> str:
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    int_part = int(x)
    frac_part = x - int_part
    int_str = "0"
    if int_part > 0:
        int_str = ""
        n = int_part
        while n > 0:
            int_str = digits[n % 36] + int_str
            n //= 36
    if frac_part <= 0:
        return int_str
    frac_str = ""
    f = frac_part
    for _ in range(precision):
        f *= 36
        d = int(f)
        frac_str += digits[d]
        f -= d
        if f <= 0:
            break
    return f"{int_str}.{frac_str}"


def _syndication_token(tweet_id: str) -> str:
    n = (int(tweet_id) / 1e15) * math.pi
    return re.sub(r"(0+|\.)", "", _base36(n))


async def fetch_tweet_syndication(url: str) -> dict | None:
    tweet_id = _tweet_id_from_url(url)
    if not tweet_id:
        return None
    token = _syndication_token(tweet_id)
    api_url = f"https://cdn.syndication.twimg.com/tweet-result?id={tweet_id}&token={token}&lang=en"
    try:
        resp = await net.read_page("GET", api_url, timeout=15, limit=TEXT_PAGE_LIMIT_BYTES,
                                   too_big=lambda: FetchError("That tweet's answer was too big."))
    except (httpx.HTTPError, FetchError):
        return None
    if resp.status_code != 200:
        return None
    try:
        data = resp.json()
    except ValueError:
        return None
    if not data or data.get("__typename") != "Tweet":
        return None
    return data
