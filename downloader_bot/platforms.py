"""Which platform a pasted link belongs to, and the two page-scrapes that are
about *text* rather than media.

Media resolution moved to resolvers.py at v1.2.1. What is left here is
recognition -- the regexes bot.py's MessageHandler filters on -- plus the
Reddit and Twitter/X reads that exist to render a card for a post with no
media in it, which is a different job from downloading one.

Instagram and TikTok used to share a single regex, because they shared a
single code path: yt-dlp. They no longer share a code path -- they have
separate provider chains with nothing in common -- so they are separate
platforms here too.
"""
import math
import re
from urllib.parse import parse_qsl, urlsplit

import httpx

import net
from net import FetchError, client, close_client, fetch_bytes  # re-exported

__all__ = [
    "FetchError", "client", "close_client", "fetch_bytes",
    "detect_platform", "canonical_link", "ANY_LINK_RE",
    "fetch_reddit_post", "reddit_is_media", "reddit_direct_image_url",
    "fetch_tweet_syndication",
]

INSTAGRAM_RE = re.compile(
    r"https?://(?:www\.|m\.)?instagram\.com/\S+", re.IGNORECASE
)
TIKTOK_RE = re.compile(
    r"https?://(?:www\.|vm\.|vt\.|m\.)?tiktok\.com/\S+", re.IGNORECASE
)
# Pinterest runs a country subdomain per market -- co.pinterest.com,
# br.pinterest.com, in.pinterest.com, ru.pinterest.com -- and the share sheet
# hands out whichever one the sharer was on. Allowing only `www.` meant a link
# from anywhere but the default host was answered with "that is not a link I
# recognise", which reads as the bot not supporting Pinterest at all.
#
# What follows `pinterest.` is a top-level domain and nothing else: com, a
# country code (pinterest.ca, pinterest.de), or the two-part forms
# (pinterest.co.uk, pinterest.com.au). It used to be `[a-z.]+`, which is any
# domain at all that begins with "pinterest." -- `pinterest.attacker.example`
# was a Pinterest link, and the pin resolvers fetch whatever address they are
# given and follow it wherever it leads, so a stranger's link could point this
# container at any address it could reach, and at the `og:image` that page
# named after that. See net.py, which refuses the addresses no link to a
# public site should ever lead to.
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
# Deliberately undocumented everywhere else (see bot.py's HELP_TEXT and
# BOT_COMMANDS) -- handled if someone pastes one, never mentioned in any
# command list or help text. It is also the one platform with no provider but
# yt-dlp, so it is the one most likely to be refusing a server on any given
# day; advertising it would be promising something the bot cannot keep.
YOUTUBE_RE = re.compile(
    r"https?://(?:www\.|m\.)?(?:youtube\.com/(?:watch\?v=|shorts/|live/)|youtu\.be/)\S+",
    re.IGNORECASE,
)

# Ordered so the most specific patterns are tried first.
_PLATFORM_PATTERNS = [
    ("instagram", INSTAGRAM_RE),
    ("tiktok", TIKTOK_RE),
    ("youtube", YOUTUBE_RE),
    ("reddit", REDDIT_RE),
    ("twitter", TWITTER_RE),
    ("pinterest", PINTEREST_RE),
]

ANY_LINK_RE = re.compile(
    "|".join(f"(?:{p.pattern})" for _, p in _PLATFORM_PATTERNS), re.IGNORECASE
)


# ---------- The same post, written one way ----------
#
# Every share sheet appends its own trackers to a link -- Instagram's `?igsh=`
# and `?igshid=`, YouTube's `?si=`, TikTok's `?is_from_webapp=1&sender_device=pc`,
# `utm_*` everywhere -- and a different one for every person who shares it. So
# the same post arrived as a different string each time, and everything keyed on
# the string (the five-minute delivery cache, the redo memory, the "same link
# again" check, the file-ID store) treated one post as many. detect_platform is
# the single place a link enters the bot, so it is the place to write it one way.
#
# An allow-list, not a block-list: each rule below keeps what identifies the post
# and drops everything else. A parameter nobody has seen yet is dropped, which is
# the safe direction (a post is never identified by a query string we did not
# list). Where a rule cannot find what it needs the link is returned unchanged:
# a link this function does not understand must still go through as it was sent.

# What the `\S+` of the patterns above swallows from prose: "look at this
# https://...ABC/." or "(https://...)". A post code never ends with one.
_TRAILING_PUNCT = ".,;:!?)]}>»\"'"

_IG_HOSTS = {"instagram.com", "www.instagram.com", "m.instagram.com"}
_IG_POST = re.compile(r"/(p|reel|reels|tv)/([A-Za-z0-9_-]+)(?:/|$)")
# `/<username>/reel/<CODE>/`. `share` is Instagram's own prefix for a redirecting
# share link, whose id is not the post's code, so it is not a username.
_IG_USER_POST = re.compile(r"/([^/]+)" + _IG_POST.pattern)
_IG_NOT_USERS = {"share", "stories"}

_TT_HOSTS = {"tiktok.com", "www.tiktok.com", "m.tiktok.com"}
_TT_SHORT_HOSTS = {"vm.tiktok.com", "vt.tiktok.com"}

_YT_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com"}
_YT_ID = re.compile(r"[A-Za-z0-9_-]{11}")

_TW_HOSTS = {"twitter.com", "www.twitter.com", "mobile.twitter.com",
             "x.com", "www.x.com", "mobile.x.com"}
_TW_STATUS = re.compile(r"/(\w+)/status(?:es)?/(\d+)(?:/|$)")

_RD_HOSTS = {"reddit.com", "www.reddit.com", "old.reddit.com", "m.reddit.com"}

# The same shape PINTEREST_RE accepts as a host: an optional country prefix, then
# pinterest + a top-level domain, or pin.it.
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

    if platform == "youtube":
        video = ""
        if host in _YT_HOSTS:
            segments = [s for s in path.split("/") if s]
            if path.rstrip("/") == "/watch":
                values = [v for k, v in parse_qsl(parts.query, keep_blank_values=True) if k == "v"]
                video = values[0] if values else ""
            elif len(segments) >= 2 and segments[0] in ("shorts", "live"):
                video = segments[1]
        elif host == "youtu.be":
            segments = [s for s in path.split("/") if s]
            video = segments[0] if segments else ""
        if _YT_ID.fullmatch(video):
            return f"https://www.youtube.com/watch?v={video}"
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


def canonical_link(platform: str, url: str) -> str:
    """The link written the one way that identifies the post: share trackers,
    query strings and fragments gone, the host and path normalised, the
    punctuation a sentence leaves on the end of a link taken off. Pure, no
    network, and it never raises -- anything it cannot read comes back as it
    was given. Writing the result through it again changes nothing."""
    try:
        return _canonical(platform, url)
    except Exception:  # noqa: BLE001 - a link we cannot read goes through as sent
        return url


def detect_platform(text: str) -> tuple[str, str] | None:
    """Returns (platform, canonical_link) for the first recognized link in
    text, or None if nothing matches. The link comes back with its share
    trackers removed (canonical_link), so one post is one string wherever it
    was shared from."""
    for name, pattern in _PLATFORM_PATTERNS:
        m = pattern.search(text)
        if m:
            return name, canonical_link(name, m.group(0))
    return None


# What a Reddit or X answer may weigh: the 8 MB that resolvers.PAGE_LIMIT_BYTES
# allows a provider's page, as a constant of its own because this module
# depends on net.py alone and should not start importing the provider chain
# (and a post's JSON or a tweet is far under it anyway).
TEXT_PAGE_LIMIT_BYTES = 8 * 1024 * 1024


# ---------- Reddit: public .json endpoint, no auth ----------

async def fetch_reddit_post(url: str) -> dict:
    """Reddit's own public .json endpoint on any post URL -- what old.reddit
    and countless tools already rely on, no login needed for public
    subreddits. Reddit's anti-scraping has gotten more aggressive about this
    over time though (IP-reputation based, not just User-Agent), so this can
    403 from some hosts even for a perfectly public post -- that's surfaced
    as a FetchError with a clear message, not a stack trace."""
    json_url = url.split("?")[0].rstrip("/") + ".json"
    headers = {
        "Accept": "application/json,text/html;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    # Read through net.read_page, not `.get()`: `.get()` holds whatever the
    # server sends, decoded however it was encoded, and a post's JSON is a
    # page like any other (see net.body_chunks for what that can cost).
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
    """A plain-image post's direct URL, if this isn't a gallery/video."""
    if post.get("post_hint") == "image":
        return post.get("url_overridden_by_dest") or post.get("url")
    return None


# ---------- Twitter/X: public syndication endpoint, no auth ----------
# The same unauthenticated endpoint X's own embed widget
# (platform.twitter.com/widgets.js) calls to render an embedded tweet -- not a
# private or reverse-engineered API. Kept for the *card*: resolvers.py handles
# a tweet's media through fxtwitter/vxtwitter, but a text-only tweet has no
# media to resolve and this is where its text, author and like count come
# from. Best-effort by nature: X can rate-limit or change it without notice,
# which is why the caller falls back to handing back the link.

def _tweet_id_from_url(url: str) -> str | None:
    m = re.search(r"/status(?:es)?/(\d+)", url)
    return m.group(1) if m else None


def _base36(x: float, precision: int = 60) -> str:
    """Python doesn't have JS's Number.prototype.toString(36) built in --
    this reimplements it (integer part + fractional digits) just precisely
    enough to reproduce the syndication token below."""
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
    """X's embed widget computes this the same way to authorize an otherwise
    open syndication request -- not a secret, just an obfuscation step:
    ((id / 1e15) * pi) in base 36, with zeros and the decimal point
    stripped."""
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
    except (httpx.HTTPError, FetchError):        # too big and unreadable encodings are FetchErrors
        return None
    if resp.status_code != 200:
        return None
    try:
        data = resp.json()
    except ValueError:
        return None
    # Deleted/protected/age-restricted tweets come back as a "tombstone" with
    # a moderation notice instead of real content -- treat that the same as
    # "couldn't fetch it" rather than rendering the notice as if it were the
    # tweet.
    if not data or data.get("__typename") != "Tweet":
        return None
    return data
