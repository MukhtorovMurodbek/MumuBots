"""DownloaderBot's platforms, providers, the fetching, and large files.

Every section below, from its `# ─── module:` line to the next, is a module of its own: main.py
loads each one separately, for each bot that uses it, so a bot's `import db` is still that
bot's db. This file is never imported whole -- run main.py.
"""
raise ImportError("download.py is loaded section by section by main.py -- run main.py")


# ─── module: downloader_bot.platforms ────────────────────────────────────────
"""Which platform a pasted link belongs to, and the two page-scrapes that are about *text* rather than media."""
import math
import re

import httpx

import net
from net import FetchError, client, close_client, fetch_bytes

__all__ = [
    "FetchError", "client", "close_client", "fetch_bytes",
    "detect_platform", "ANY_LINK_RE",
    "fetch_reddit_post", "reddit_is_media", "reddit_direct_image_url",
    "fetch_tweet_syndication",
]

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

YOUTUBE_RE = re.compile(
    r"https?://(?:www\.|m\.)?(?:youtube\.com/(?:watch\?v=|shorts/|live/)|youtu\.be/)\S+",
    re.IGNORECASE,
)

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

def detect_platform(text: str) -> tuple[str, str] | None:
    """Returns (platform, matched_url) for the first recognized link in text, or None if nothing matches."""
    for name, pattern in _PLATFORM_PATTERNS:
        m = pattern.search(text)
        if m:
            return name, m.group(0)
    return None

TEXT_PAGE_LIMIT_BYTES = 8 * 1024 * 1024

async def fetch_reddit_post(url: str) -> dict:
    """Reddit's own public .json endpoint on any post URL -- what old.reddit and countless tools already rely on, no login needed for public subreddits."""
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
    """A plain-image post's direct URL, if this isn't a gallery/video."""
    if post.get("post_hint") == "image":
        return post.get("url_overridden_by_dest") or post.get("url")
    return None

def _tweet_id_from_url(url: str) -> str | None:
    m = re.search(r"/status(?:es)?/(\d+)", url)
    return m.group(1) if m else None

def _base36(x: float, precision: int = 60) -> str:
    """Python doesn't have JS's Number.prototype.toString(36) built in -- this reimplements it (integer part + fractional digits) just precisely enough to reproduce the syndication token below."""
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
    """X's embed widget computes this the same way to authorize an otherwise open syndication request -- not a secret, just an obfuscation step: ((id / 1e15) * pi) in base 36, with zeros and the decimal point stripped."""
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

# ─── module: downloader_bot.net ──────────────────────────────────────────────
"""One HTTP client for the whole process, and the two ways this bot fetches media with it."""
from __future__ import annotations

import asyncio
import ipaddress
import os
import socket
import zlib

import httpx

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")

CRAWLER_UA = "TelegramBot (like TwitterBot)"

MAX_RESPONSE_BYTES = int(os.environ.get("DBOT_MAX_DOWNLOAD_MB", "48")) * 1024 * 1024

CONNECT_TIMEOUT = float(os.environ.get("DBOT_CONNECT_TIMEOUT", "10"))
READ_TIMEOUT = float(os.environ.get("DBOT_READ_TIMEOUT", "30"))

FAILOVER_CONNECT_TIMEOUT = 4.0

class FetchError(Exception):
    """Raised with a message that is safe to show the user as-is."""

class BadEncoding(FetchError):
    """The server sent a body in an encoding this bot does not read (or one that did not decode). See body_chunks()."""

class Trickle(BadEncoding):
    """The server sent its body in far too many pieces for its size: see body_chunks()."""

ALLOW_PRIVATE_HOSTS = os.environ.get("DBOT_ALLOW_PRIVATE_HOSTS", "").strip().lower() in ("1", "true", "yes")

class BlockedAddress(Exception):
    """The name resolves to an address that is not on the public internet."""

def is_public_address(address: str) -> bool:
    """Whether an IP address is somewhere on the public internet: not this machine, a private network, a link-local address (which is where cloud metadata services live), shared address space, multicast, or unspecified."""
    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return False
    if ip.version == 6 and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return bool(ip.is_global and not ip.is_multicast)

async def _resolve(host: str, port: int) -> list[str]:
    """Every address `host` resolves to. Its own function so that a test can say what a name resolves to."""
    infos = await asyncio.wait_for(
        asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM), CONNECT_TIMEOUT)
    addresses: list[str] = []
    for _family, _type, _proto, _canon, sockaddr in infos:
        if sockaddr[0] not in addresses:
            addresses.append(sockaddr[0])
    return addresses

async def public_addresses(host: str, port: int) -> list[str]:
    """The addresses to try for `host`, all of them public -- or BlockedAddress."""
    addresses = await _resolve(host, port)
    if not addresses:
        raise OSError(f"no address for {host}")
    if not ALLOW_PRIVATE_HOSTS:
        for address in addresses:
            if not is_public_address(address):
                raise BlockedAddress(f"{host} is not a public address")
    return addresses

def _guarded_transport(inner=None):
    """The transport `client()` uses: `inner` (an ordinary httpx transport) behind public_addresses()."""

    class Refused(httpx.ConnectError):
        pass

    class PublicOnlyTransport(httpx.AsyncBaseTransport):
        def __init__(self):
            self._inner = inner or httpx.AsyncHTTPTransport(
                limits=httpx.Limits(max_connections=8, max_keepalive_connections=4,
                                    keepalive_expiry=30.0))

        async def handle_async_request(self, request):
            url = request.url
            host = url.raw_host.decode("ascii")
            port = url.port or (443 if url.scheme == "https" else 80)
            try:
                addresses = await public_addresses(host, port)
            except BlockedAddress as exc:
                raise Refused(str(exc), request=request) from None
            except (OSError, asyncio.TimeoutError) as exc:
                raise httpx.ConnectError(f"could not resolve {host}: {exc}", request=request) from exc

            addresses = sorted(addresses, key=lambda address: ":" in address)
            failure = None
            for index, address in enumerate(addresses):

                extensions = dict(request.extensions)
                if url.scheme == "https":
                    extensions["sni_hostname"] = host
                if index < len(addresses) - 1:

                    timeouts = dict(extensions.get("timeout") or {})
                    if timeouts.get("connect") is not None:
                        timeouts["connect"] = min(timeouts["connect"], FAILOVER_CONNECT_TIMEOUT)
                        extensions["timeout"] = timeouts
                pinned = httpx.Request(request.method, url.copy_with(host=address),
                                       headers=request.headers, stream=request.stream,
                                       extensions=extensions)
                try:
                    response = await self._inner.handle_async_request(pinned)
                except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                    failure = exc
                    continue
                return await _without_redirect_body(response)
            raise failure

        async def aclose(self):
            await self._inner.aclose()

    return PublicOnlyTransport()

_REDIRECTS = (301, 302, 303, 307, 308)

async def _without_redirect_body(response):
    """A redirect, with its body thrown away unread; anything else, as it came."""
    if response.status_code not in _REDIRECTS or "location" not in response.headers:
        return response
    await response.aclose()
    kept = [(k, v) for k, v in response.headers.multi_items()
            if k.lower() not in ("content-encoding", "content-length", "transfer-encoding")]
    return httpx.Response(response.status_code, headers=kept, content=b"",
                          extensions=response.extensions)

_client: httpx.AsyncClient | None = None

ACCEPT_ENCODING = "gzip, deflate"

def client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            follow_redirects=True,
            timeout=httpx.Timeout(READ_TIMEOUT, connect=CONNECT_TIMEOUT),
            headers={"User-Agent": UA, "Accept-Encoding": ACCEPT_ENCODING},
            transport=_guarded_transport(),
        )
    return _client

async def close_client() -> None:
    """Called from the bot's shutdown hook."""
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client = None

_DECODE_BLOCK = 64 * 1024

_KNOWN_ENCODINGS = ("gzip", "x-gzip", "deflate", "br", "zstd", "compress", "x-compress")

def _content_encodings(response) -> list[str]:
    """The real encodings a body was sent in, lower-cased: "identity" (which means none), empty entries and tokens that are no encoding at all are dropped."""
    names = [part.strip() for part in response.headers.get("content-encoding", "").lower().split(",")]
    return [name for name in names if name in _KNOWN_ENCODINGS]

class _Inflater:
    """One gzip or deflate body, decompressed a bounded piece at a time."""

    def __init__(self, limit: int, too_big, deflate: bool):
        self.limit = limit
        self.too_big = too_big
        self.deflate = deflate
        self.total = 0
        self.finished = False
        self._first = deflate
        self._head = b""
        self.d = zlib.decompressobj() if deflate else zlib.decompressobj(16 + zlib.MAX_WBITS)

    def _room(self) -> int:
        """max_length for the next call: one byte more than what may still be accepted, so that a body over the limit shows as over rather than as exactly at it."""
        return max(min(self.limit - self.total + 1, _DECODE_BLOCK), 1)

    def _decompress(self, data: bytes, room: int) -> bytes:
        try:
            return self.d.decompress(data, room)
        except zlib.error:
            if not self._first:
                raise BadEncoding("The server sent a body that did not decode.") from None

        self._first = False
        self.d = zlib.decompressobj(-zlib.MAX_WBITS)
        try:
            return self.d.decompress(data, room)
        except zlib.error:
            raise BadEncoding("The server sent a body that did not decode.") from None

    def _drain(self, data: bytes):
        """Decoded pieces of `data`, none over _DECODE_BLOCK, until zlib has nothing more to give for it."""
        room = self._room()
        out = self._decompress(data, room)
        if data:
            self._first = False
        while True:
            self.total += len(out)
            if self.total > self.limit:
                raise self.too_big()
            if out:
                yield out
            if self.d.eof:

                self.finished = True
                return

            if self.d.unconsumed_tail or len(out) == room:
                room = self._room()
                out = self._decompress(self.d.unconsumed_tail, room)
            else:
                return

    def feed(self, data: bytes):
        if self._first and data:

            data = self._head + data
            if len(data) < 2:
                self._head = data
                return
            self._head = b""
        if data and not self.finished:
            yield from self._drain(data)

    def finish(self):

        if not self.finished:
            yield from self._drain(b"")

async def body_chunks(response, limit: int, too_big):
    """The body of a streamed `response`, decoded, in pieces; `too_big()` is called (it returns the exception to raise) the moment more than `limit` decoded bytes have arrived."""
    names = _content_encodings(response)
    if not names:
        inflater = None
    elif names in (["gzip"], ["x-gzip"]):
        inflater = _Inflater(limit, too_big, deflate=False)
    elif names == ["deflate"]:
        inflater = _Inflater(limit, too_big, deflate=True)
    else:
        raise BadEncoding("The server sent a body in an encoding this bot does not read.")
    total = 0
    if response.is_stream_consumed:

        content = response.content
        if len(content) > limit:
            raise too_big()
        if content:
            yield content
        return

    raw_cap = limit + limit // 50 + 64 * 1024
    raw_total = 0

    pieces_left = limit // 1024 + 1024
    raw_chunks = response.aiter_raw()
    try:
        async for raw in raw_chunks:
            pieces_left -= 1
            if pieces_left < 0:
                raise Trickle("The server sent its answer in pieces too small to read.")
            if inflater is None:
                total += len(raw)
                if total > limit:
                    raise too_big()
                if raw:
                    yield raw
                continue
            raw_total += len(raw)
            if raw_total > raw_cap:
                raise too_big()
            for piece in inflater.feed(raw):
                yield piece
            if inflater.finished:
                return
        if inflater is not None:
            for piece in inflater.finish():
                yield piece
    finally:
        await raw_chunks.aclose()

async def read_page(method: str, url: str, *, limit: int, too_big, declared_too_big=None,
                    **kwargs) -> httpx.Response:
    """A whole small document, read under a ceiling, as an ordinary `httpx.Response`: status, final URL, history, the body decoded."""
    async with client().stream(method, url, **kwargs) as resp:
        declared = resp.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > limit:
            raise (declared_too_big(int(declared)) if declared_too_big else too_big())
        body = bytearray()
        async for chunk in body_chunks(resp, limit, too_big):
            body += chunk

        kept = [(k, v) for k, v in resp.headers.multi_items()
                if k.lower() not in ("content-encoding", "content-length", "transfer-encoding")]
        done = httpx.Response(resp.status_code, headers=kept, content=bytes(body),
                              request=resp.request)
        done.history = list(resp.history)
        return done

async def fetch_bytes(url: str, limit: int = MAX_RESPONSE_BYTES,
                      headers: dict | None = None) -> bytes:
    """GET a URL into memory, refusing anything that would not fit in a Telegram upload anyway."""
    def too_big():
        return FetchError("That file is too big to send through Telegram.")

    async with client().stream("GET", url, headers=headers or {}) as response:
        response.raise_for_status()
        declared = response.headers.get("content-length")
        if declared and int(declared) > limit:
            raise too_big()
        chunks: list[bytes] = []
        async for chunk in body_chunks(response, limit, too_big):
            chunks.append(chunk)
    return b"".join(chunks)

class TooLarge(FetchError):
    """Over the size ceiling this download was given."""

async def stream_to_file(url: str, path: str, limit: int = MAX_RESPONSE_BYTES,
                         headers: dict | None = None) -> int:
    """Same, but to disk, for the things that are too big to want in memory."""
    def too_big():
        return TooLarge("That file is too big to send through Telegram.")

    total = 0
    with open(path, "wb") as out:
        async with client().stream("GET", url, headers=headers or {}) as response:
            response.raise_for_status()
            declared = response.headers.get("content-length")
            if declared and int(declared) > limit:
                raise too_big()
            async for chunk in body_chunks(response, limit, too_big):
                total += len(chunk)
                out.write(chunk)
    return total

# ─── module: downloader_bot.cards ────────────────────────────────────────────
"""Renders a plain, consistent 'quote card' image for text-based Reddit/ Twitter posts -- the actual reason to download these instead of just screenshotting: no UI chrome, no notification banners caught mid-shot, no light/dark-mode mismatch, and it's already square-ish and clean enough to repost as-is."""
from functools import lru_cache
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont

WIDTH = 1080
PADDING = 56
LINE_HEIGHT = 50
MAX_BODY_LINES = 16

BG = (22, 24, 28)
FG = (235, 236, 240)
MUTED = (145, 150, 160)
ACCENT = {
    "reddit": (255, 69, 0),
    "twitter": (29, 155, 240),
}

@lru_cache(maxsize=8)
def _font(size: int) -> ImageFont.FreeTypeFont:
    """Cached: there are exactly four sizes in this file, and rebuilding the same four font objects for every card is work with no result."""
    return ImageFont.load_default(size=size)

def _wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    lines = []
    for paragraph in text.splitlines() or [""]:
        words = paragraph.split()
        cur = ""
        if not words:
            lines.append("")
            continue
        for word in words:
            trial = f"{cur} {word}".strip()
            if draw.textlength(trial, font=font) <= max_width:
                cur = trial
            else:
                if cur:
                    lines.append(cur)
                cur = word
        if cur:
            lines.append(cur)
    return lines

def render_card(platform: str, source: str, author: str, body: str, meta: str = "") -> bytes:
    """platform: "reddit" or "twitter" (picks the accent color)."""
    font_source = _font(34)
    font_author = _font(26)
    font_body = _font(38)
    font_meta = _font(26)

    scratch = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    max_text_width = WIDTH - PADDING * 2 - 16

    body = (body or "").strip() or "(no text)"
    body_lines = _wrap(scratch, body, font_body, max_text_width)
    truncated = len(body_lines) > MAX_BODY_LINES
    body_lines = body_lines[:MAX_BODY_LINES]
    if truncated:
        body_lines[-1] = body_lines[-1].rstrip() + "…"

    header_h = 100
    body_h = max(len(body_lines), 1) * LINE_HEIGHT
    footer_h = 56 if meta else 0
    height = PADDING + header_h + body_h + footer_h + PADDING

    img = Image.new("RGB", (WIDTH, height), BG)
    draw = ImageDraw.Draw(img)

    accent = ACCENT.get(platform, (130, 130, 130))
    draw.rectangle([0, 0, 12, height], fill=accent)

    x = PADDING + 16
    draw.text((x, PADDING), source, font=font_source, fill=accent)
    draw.text((x, PADDING + 44), author, font=font_author, fill=MUTED)

    y = PADDING + header_h
    for line in body_lines:
        draw.text((x, y), line, font=font_body, fill=FG)
        y += LINE_HEIGHT

    if meta:
        draw.text((x, y + 12), meta, font=font_meta, fill=MUTED)

    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()

# ─── module: downloader_bot.probe ────────────────────────────────────────────
#!/usr/bin/env python3
"""Ask every download route whether it still works, and say which ones do."""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import net
import platforms
import resolvers

SAMPLES = list(resolvers.SAMPLES.values())

GREEN, RED, YELLOW, DIM, OFF = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"
if os.name == "nt" and not os.environ.get("WT_SESSION"):
    GREEN = RED = YELLOW = DIM = OFF = ""

async def probe_one(url: str, fetch: bool) -> None:
    detected = platforms.detect_platform(url)
    if not detected:
        print(f"{RED}?{OFF} {url}\n    not a link this bot recognises")
        return
    platform, matched = detected
    print(f"\n{platform}  {DIM}{matched}{OFF}")

    for name, fn in resolvers.PROVIDERS.get(platform, []):
        row = await resolvers.probe_route(name, fn, matched, fetch=fetch)
        mark = f"{GREEN}v{OFF}" if row["ok"] else f"{RED}x{OFF}"
        line = f"  {mark} {name:<18} {row['seconds']:>4.1f}s  {row['detail']}"
        if row["fetched"]:
            line += f"  {DIM}[{row['fetched']}]{OFF}"
        print(line)

async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("urls", nargs="*", help="links to probe (default: the samples)")
    parser.add_argument("--fetch", action="store_true",
                        help="also pull the first 256 KB back, to prove the media URL serves")
    parser.add_argument("--platform", help="only probe the samples for this platform")
    args = parser.parse_args()

    urls = args.urls or SAMPLES
    if args.platform:
        urls = [u for u in urls
                if (platforms.detect_platform(u) or ("", ""))[0] == args.platform]
        if not urls:
            print(f"No sample link for platform {args.platform!r}.")
            return 2

    print("Probing download routes. A green row means that provider answered "
          "from THIS machine;\nthe container is on a different network and can "
          "be told something else.")
    try:
        for url in urls:
            await probe_one(url, args.fetch)
    finally:
        await net.close_client()
    print()
    return 0

if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

# ─── module: downloader_bot.video ────────────────────────────────────────────
"""The yt-dlp provider: download a video with yt-dlp so the bot can re-send it."""
import contextlib
import glob
import logging
import os
import re
import sys
import tempfile
import threading
import time
import uuid

MAX_DOWNLOAD_MB = int(os.environ.get("DBOT_MAX_DOWNLOAD_MB", "48"))

MAX_DURATION_S = int(os.environ.get("DBOT_MAX_DURATION_SECONDS", "1800"))

SOCKET_TIMEOUT_S = int(os.environ.get("DBOT_SOCKET_TIMEOUT", "30"))

logger = logging.getLogger(__name__)

YT_PLAYER_CLIENTS = [
    c.strip() for c in
    os.environ.get("DBOT_YT_PLAYER_CLIENTS", "tv,web_safari,android_vr,web").split(",")
    if c.strip()
]

COOKIEFILE = os.environ.get("DBOT_COOKIES_FILE") or None
SITE_COOKIEFILES = {
    "youtube": os.environ.get("DBOT_YT_COOKIES_FILE") or None,
    "instagram": os.environ.get("DBOT_IG_COOKIES_FILE") or None,
    "tiktok": os.environ.get("DBOT_TT_COOKIES_FILE") or None,
    "twitter": os.environ.get("DBOT_TW_COOKIES_FILE") or None,
    "reddit": os.environ.get("DBOT_RD_COOKIES_FILE") or None,
}

_COOKIE_TEXT_VARS = {"youtube": "DBOT_YT_COOKIES", "instagram": "DBOT_IG_COOKIES",
                     "tiktok": "DBOT_TT_COOKIES", "twitter": "DBOT_TW_COOKIES",
                     "reddit": "DBOT_RD_COOKIES", "other": "DBOT_COOKIES"}
_cookie_text_files: dict = {}

_cookie_text_refused: dict = {}

def _cookie_text_problem(text: str) -> "str | None":
    """Why `text` is not a cookies.txt, or None if it is one."""

    text = text.removeprefix("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    for number, line in enumerate(lines, 1):
        if not line.strip() or (line.strip().startswith("#") and not line.startswith("#HttpOnly_")):
            continue
        line = line.removeprefix("#HttpOnly_")
        fields = line.split("\t")
        if len(fields) != 7:
            return (f"line {number} of {len(lines)} has {len(fields)} tab-separated field(s), not 7 "
                    "(were the tabs pasted as spaces?)")
        domain, flag, path, secure, expires, name, _value = fields

        if not domain:
            what = "an empty domain"
        elif flag != ("TRUE" if domain.startswith(".") else "FALSE"):
            what = "a subdomains flag (field 2) that does not match its domain"
        elif not path.startswith("/"):
            what = "a path (field 3) that does not start with /"
        elif secure not in ("TRUE", "FALSE"):
            what = "a secure flag (field 4) that is not TRUE or FALSE"
        elif not re.fullmatch(r"[0-9]*", expires):
            what = "an expiry (field 5) that is not digits"
        elif not name:
            what = "an empty name (field 6)"
        else:
            continue
        return f"line {number} of {len(lines)} has {what}"
    return None

_COOKIE_HEADER_RE = re.compile(r"#( Netscape)? HTTP Cookie File")

def _cookiefile_from_text(site: str) -> "str | None":
    var = _COOKIE_TEXT_VARS.get(site)
    text = os.environ.get(var) if var else None
    if not text or not text.strip():
        return None

    text = text.removeprefix("\ufeff")
    if not text.strip():
        return None
    path = _cookie_text_files.get(site)
    if path and os.path.exists(path):
        return path
    if _cookie_text_refused.get(site) == os.environ.get(var):
        return None

    if "\\n" in text and "\n" not in text:
        text = text.replace("\\n", "\n").replace("\\t", "\t")
    problem = _cookie_text_problem(text)
    if problem:
        _cookie_text_refused[site] = os.environ.get(var)
        logger.warning("%s is not a cookies.txt: %s. Those cookies are not used.", var, problem)
        return None

    text = text.lstrip("\r\n \t")
    if not _COOKIE_HEADER_RE.match(text):
        text = "# Netscape HTTP Cookie File\n" + text
    fd, path = tempfile.mkstemp(prefix=f"cookies-{site}-", suffix=".txt")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text if text.endswith("\n") else text + "\n")
    _cookie_text_files[site] = path
    return path

def cookiefile_for(site: str) -> "str | None":
    """The cookie jar for this site: its own file, its own pasted text, then the every-site file, then the every-site text."""
    for candidate in (SITE_COOKIEFILES.get(site), _cookiefile_from_text(site),
                      COOKIEFILE, _cookiefile_from_text("other")):
        if candidate and os.path.exists(candidate):
            return candidate
    return None

PROXY = os.environ.get("DBOT_PROXY") or None
SITE_PROXIES = {
    "youtube": os.environ.get("DBOT_YT_PROXY") or None,
    "instagram": os.environ.get("DBOT_IG_PROXY") or None,
    "tiktok": os.environ.get("DBOT_TT_PROXY") or None,
    "twitter": os.environ.get("DBOT_TW_PROXY") or None,
    "reddit": os.environ.get("DBOT_RD_PROXY") or None,
}

BOT_CHECK_MARKERS = ("sign in to confirm", "confirm you're not a bot",
                     "confirm you are not a bot")

LOGIN_WALL_MARKERS = (
    "redirected to the login page",
    "rate-limit for accessing posts anonymously",
    "requested content is not available, rate-limit reached",
    "login required",
    "you need to log in",
    "use --cookies",
    "--cookies-from-browser",
)

def _site_of(url: str) -> str:
    """Which per-site cookie jar and proxy apply."""
    lowered = url.lower()
    for site in ("instagram", "tiktok", "youtube", "pinterest", "reddit"):
        if site in lowered:
            return site
    if "youtu.be" in lowered:
        return "youtube"
    if "twitter.com" in lowered or "//x.com" in lowered:
        return "twitter"
    if "redd.it" in lowered:
        return "reddit"
    if "pin.it" in lowered:
        return "pinterest"
    return "other"

class BlockedBySource(Exception):
    """The site refused the server, not the link."""

    def __init__(self, message: str, kind: str = "bot_check", site: str = "other"):
        super().__init__(message)
        self.kind = kind
        self.site = site

class TooLarge(Exception):
    """The clip is over the size/duration ceiling. Message is user-facing."""

def _reject_long(info, *, incomplete):
    """yt-dlp's match_filter hook: runs on the *metadata*, before a single byte of media is fetched."""
    duration = info.get("duration")
    if duration and duration > MAX_DURATION_S:
        return (
            f"that clip is {int(duration) // 60} minutes long, and this bot "
            f"caps downloads at {MAX_DURATION_S // 60}"
        )
    return None

def _format_selector(max_mb: int | None = None) -> str:
    mb = MAX_DOWNLOAD_MB if max_mb is None else max_mb
    return (

        f"bv*[filesize<{mb}M]+ba/"
        f"bv*[filesize_approx<{mb}M]+ba/"

        f"b[filesize<{mb}M]/"
        f"b[filesize_approx<{mb}M]/"

        "bv*+ba/b"
    )

def _remove_leftovers(out_dir: str, unique_id: str, keep: str | None = None) -> None:
    """Everything yt-dlp wrote for this download except `keep`."""
    for path in glob.glob(os.path.join(glob.escape(out_dir), f"{unique_id}.*")):
        if keep is not None and os.path.abspath(path) == os.path.abspath(keep):
            continue
        try:
            os.remove(path)
        except OSError:
            pass

_slots = threading.Condition()
_slots_held = 0
_swapping = False
SWAP_WAIT_S = 300.0

@contextlib.contextmanager
def _slot():
    global _slots_held
    with _slots:
        while _swapping:
            _slots.wait(timeout=5.0)
        _slots_held += 1
    try:
        yield
    finally:
        with _slots:
            _slots_held -= 1
            _slots.notify_all()

def use_ytdlp_from(path: str) -> bool:
    """Make `path` (a folder holding a newer yt_dlp package) the one every later download imports."""
    global _swapping
    with _slots:
        _swapping = True
        try:
            deadline = time.monotonic() + SWAP_WAIT_S
            while _slots_held:
                left = deadline - time.monotonic()
                if left <= 0:
                    return False
                _slots.wait(timeout=min(left, 5.0))
            if path in sys.path:
                sys.path.remove(path)
            sys.path.insert(0, path)

            for name in [n for n in sys.modules if n == "yt_dlp" or n.startswith("yt_dlp.")]:
                del sys.modules[name]
            return True
        finally:
            _swapping = False
            _slots.notify_all()

def ytdlp_version() -> "str | None":
    """The version a download would use right now, without importing it."""
    for entry in sys.path:
        candidate = os.path.join(entry, "yt_dlp", "version.py")
        if os.path.isfile(candidate):
            try:
                with open(candidate, encoding="utf-8") as f:
                    for line in f:
                        if line.startswith("__version__"):
                            return line.split("=", 1)[1].strip().strip("'\"")
            except OSError:
                return None
    return None

def download_video(url: str, out_dir: str, max_mb: int | None = None, cancel=None) -> str:
    """Downloads the video at `url` into `out_dir`, returns the local file path."""
    mb = MAX_DOWNLOAD_MB if max_mb is None else max_mb
    with _slot():
        import yt_dlp

        os.makedirs(out_dir, exist_ok=True)
        unique_id = uuid.uuid4().hex
        outtmpl = os.path.join(out_dir, f"{unique_id}.%(ext)s")

        def stop_if_asked(*_ignored):
            if cancel is not None and cancel.is_set():
                raise yt_dlp.utils.DownloadCancelled("nobody is waiting for this download any more")

        def match_filter(info, *, incomplete):

            stop_if_asked()
            return _reject_long(info, incomplete=incomplete)

        path = None
        try:
            path = _download(yt_dlp, url, mb, outtmpl, stop_if_asked, match_filter)
            stop_if_asked()
            return path
        except BaseException:
            path = None
            raise
        finally:
            _remove_leftovers(out_dir, unique_id, keep=path)

def _download(yt_dlp, url: str, mb: int, outtmpl: str, progress_hook, match_filter) -> str:
    ydl_opts = {
        "outtmpl": outtmpl,
        "format": _format_selector(mb),
        "max_filesize": mb * 1024 * 1024,
        "match_filter": match_filter,
        "progress_hooks": [progress_hook],
        "socket_timeout": SOCKET_TIMEOUT_S,
        "retries": 2,
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "noprogress": True,

        "cachedir": False,
        "merge_output_format": "mp4",
        "extractor_args": {"youtube": {"player_client": YT_PLAYER_CLIENTS}},
    }
    site = _site_of(url)
    cookiefile = cookiefile_for(site)
    if cookiefile:
        ydl_opts["cookiefile"] = cookiefile
    proxy = SITE_PROXIES.get(site) or PROXY
    if proxy:
        ydl_opts["proxy"] = proxy
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            info = ydl.extract_info(url, download=True)
        except Exception as exc:
            lowered = str(exc).lower()
            if any(marker in lowered for marker in BOT_CHECK_MARKERS):

                raise BlockedBySource(str(exc), "bot_check", site) from exc
            if any(marker in lowered for marker in LOGIN_WALL_MARKERS):
                raise BlockedBySource(str(exc), "login_required", site) from exc
            raise
        if info is None:

            raise TooLarge(
                f"That one is too long -- this bot caps downloads at "
                f"{MAX_DURATION_S // 60} minutes and {mb} MB."
            )
        path = ydl.prepare_filename(info)

        if not os.path.exists(path):
            base, _ = os.path.splitext(path)
            mp4_path = base + ".mp4"
            if os.path.exists(mp4_path):
                path = mp4_path
    if not os.path.exists(path):
        raise TooLarge(
            f"That file is over this bot's {mb} MB limit, so the "
            "download was stopped."
        )
    return path

# ─── module: downloader_bot.resolvers ────────────────────────────────────────
"""Turning a pasted link into files, through whichever route is still working."""
from __future__ import annotations

import asyncio
import contextvars
import base64
import json
import logging
import os
import re
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

import httpx

import net
from net import CRAWLER_UA, FetchError

logger = logging.getLogger(__name__)

PROVIDER_TIMEOUT_S = float(os.environ.get("DBOT_PROVIDER_TIMEOUT", "20"))

YTDLP_TIMEOUT_S = float(os.environ.get("DBOT_YTDLP_TIMEOUT", "90"))
YTDLP_SECONDS_PER_MB = float(os.environ.get("DBOT_YTDLP_SECONDS_PER_MB", "0.5"))

COOLDOWN_AFTER_FAILURES = int(os.environ.get("DBOT_PROVIDER_COOLDOWN_AFTER", "3"))
COOLDOWN_BASE_S = float(os.environ.get("DBOT_PROVIDER_COOLDOWN_BASE", "120"))
COOLDOWN_MAX_S = float(os.environ.get("DBOT_PROVIDER_COOLDOWN_MAX", "3600"))

@dataclass
class MediaItem:
    """One file."""
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
    """This provider could not do it. Try the next one."""

    def __init__(self, message: str, kind: str = "error", skip=()):
        super().__init__(message)
        self.kind = kind

        self.skip = tuple(skip)

class NothingWorked(Exception):
    """Every provider for this platform failed."""

    def __init__(self, platform: str, failures: dict[str, ProviderFailed]):
        self.platform = platform
        self.failures = failures
        super().__init__(
            f"{platform}: " + "; ".join(f"{n}: {e}" for n, e in failures.items())
        )

    @property
    def kind(self) -> str:
        """The most specific verdict any provider reached."""
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
        """When this provider becomes a first-class citizen again."""
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
    """The rows that changed since the last flush, and clears the flag."""
    dirty = {name: _health[name] for name in _health_dirty if name in _health}
    _health_dirty.clear()
    return dirty

def load(rows) -> None:
    """Seed from the database at startup, so a redeploy does not go back to hammering a provider that has been dead for a week."""
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
    """Every `<meta ...>` tag of `html`, found in one pass."""
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
    """Read one OpenGraph/Twitter-card meta tag's content."""
    wanted = re.compile(rf'(?:property|name)=["\']{re.escape(prop)}["\']', re.I)
    for tag in _meta_tags(html):
        if wanted.search(tag):
            m = re.search(r'content=["\']([^"\']+)["\']', tag, re.I)
            if m:
                return m.group(1).replace("&amp;", "&")
    return None

PAGE_LIMIT_BYTES = int(float(os.environ.get("DBOT_PAGE_LIMIT_MB", "8")) * 1024 * 1024)

async def _page(method: str, url: str, **kwargs) -> httpx.Response:
    """One small document, read under PAGE_LIMIT_BYTES (net.read_page), with this module's way of failing: a ProviderFailed, kind "error", that the chain moves past."""
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
    """The same for a POST (cobalt)."""
    return await _page("POST", url, **kwargs)

def _ext_of(name: str, default: str) -> str:
    """The extension to give a downloaded file, or `default`."""
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
    """downloadgram wraps every media URL in an unsigned-to-us JWT whose payload carries the filename and the real source URL."""
    part = token.split(".")[1]
    part += "=" * (-len(part) % 4)
    return json.loads(base64.urlsafe_b64decode(part))

async def _ig_downloadgram(url: str) -> Resolved:
    """downloadgram.org's public endpoint."""
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

    items: list[MediaItem] = []
    for tok in tokens:
        try:
            name = str(_jwt_payload(tok).get("filename") or "")
        except Exception:
            name = ""
        items.append(MediaItem(
            kind=_kind_of(name),
            url="https://cdn.downloadgram.org/?token=" + tok,
            filename=name or f"instagram.{_ext_of(name, 'mp4')}",
        ))

    if any(i.kind == "video" for i in items):
        items = [i for i in items if i.kind == "video"]
    return Resolved("instagram", "downloadgram", items)

async def _ig_instafix(url: str) -> Resolved:
    """An InstaFix instance (`eeinstagram.com`)."""
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
    """tikwm.com's open API."""
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
    """tnktok.com, the TikTok embed-fixer."""
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
    """fxtwitter and vxtwitter share a response shape close enough to read with one function."""
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
    """The biggest .mp4 in one of Pinterest's video_list objects."""
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
    """The pin page's own OpenGraph tags -- the fallback for when the endpoint above changes shape."""
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

COBALT_INSTANCE_TIMEOUT_S = float(os.environ.get("DBOT_COBALT_INSTANCE_TIMEOUT", "10"))

_COBALT_MISSING = ("content.video.unavailable", "content.post.unavailable",
                   "content.video.private", "content.post.private",
                   "content.video.age", "content.post.age", "link.invalid",
                   "link.unsupported", "content.video.live")
_COBALT_TOO_BIG = ("content.too_long", "content.too_large")
_COBALT_BLOCKED = (".login", "auth.", "fetch.rate", "rate_exceeded",
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

            kind = "photo" if entry.get("type") == "photo" else "video"
            items.append(MediaItem(kind, url=url,
                                   filename=f"{platform}.{'jpg' if kind == 'photo' else 'mp4'}"))
        return items
    if status == "local-processing":

        tunnels = data.get("tunnel") or []
        if len(tunnels) == 1:
            return [MediaItem("video", url=tunnels[0], filename=f"{platform}.mp4")]
    return []

async def _cobalt_one(host: str, url: str, platform: str, key: str | None) -> Resolved:
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Api-Key {key}"
    body = {"url": url, "videoQuality": COBALT_QUALITY, "filenameStyle": "basic"}
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

def _cobalt_provider(platform: str):
    async def provider(url: str) -> Resolved:
        global _cobalt_last_good
        if not COBALT_INSTANCES:
            raise ProviderFailed("no cobalt instances configured", "error")
        hosts = list(COBALT_INSTANCES)
        if _cobalt_last_good in hosts:
            hosts.remove(_cobalt_last_good)
            hosts.insert(0, _cobalt_last_good)
        failures: list[ProviderFailed] = []
        for host in hosts:
            key = COBALT_API_KEY if host == COBALT_INSTANCES[0] else None
            try:

                resolved = await asyncio.wait_for(_cobalt_one(host, url, platform, key),
                                                  timeout=COBALT_INSTANCE_TIMEOUT_S)
            except ProviderFailed as exc:
                failures.append(exc)
                if exc.kind == "too_big":

                    raise
                continue
            except asyncio.TimeoutError:
                failures.append(ProviderFailed(
                    f"{urlparse(host).netloc}: no answer in {COBALT_INSTANCE_TIMEOUT_S:.0f}s", "error"))
                continue
            except (httpx.HTTPError, KeyError, TypeError) as exc:
                failures.append(ProviderFailed(
                    f"{urlparse(host).netloc}: {type(exc).__name__}: {exc}", "error"))
                continue
            _cobalt_last_good = host
            return resolved
        kinds = {f.kind for f in failures}
        verdict = ("blocked" if "blocked" in kinds
                   else "missing" if kinds == {"missing"} else "error")
        raise ProviderFailed("; ".join(str(f) for f in failures), verdict)
    return provider

def _ytdlp_provider(platform: str):
    async def provider(url: str) -> Resolved:
        import video

        stop = threading.Event()
        try:
            path = await asyncio.to_thread(video.download_video, url, _WORK_DIR, _LIMIT_MB.get(), stop)
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

    "youtube": [
        ("cobalt:youtube", _cobalt_provider("youtube")),
        ("ytdlp:youtube", _ytdlp_provider("youtube")),
    ],
}

def _ordered(platform: str) -> list[tuple[str, object]]:
    """Registry order, with the currently-unwell moved to the back."""
    now = time.time()
    ready, cooling = [], []
    for entry in PROVIDERS.get(platform, []):
        h = health(entry[0])
        (cooling if h.cooling_until > now else ready).append(entry)
    return ready + cooling

_LIMIT_MB: contextvars.ContextVar = contextvars.ContextVar("download_limit_mb", default=None)

def _timeout_for(name: str) -> float:
    """How long the route called `name` gets before the chain moves on."""
    if not name.startswith("ytdlp:"):
        return PROVIDER_TIMEOUT_S
    free_mb = net.MAX_RESPONSE_BYTES // (1024 * 1024)
    over_mb = max(0, (_LIMIT_MB.get() or free_mb) - free_mb)
    return max(PROVIDER_TIMEOUT_S, YTDLP_TIMEOUT_S + over_mb * YTDLP_SECONDS_PER_MB)

async def resolve(platform: str, url: str, max_mb: int | None = None) -> Resolved:
    """Walk the chain until something produces media, with `max_mb` as the ceiling for anything downloaded on the way."""
    token = _LIMIT_MB.set(max_mb)
    try:
        return await _resolve(platform, url)
    finally:
        _LIMIT_MB.reset(token)

MAX_ITEMS_PER_POST = max(1, int(os.environ.get("DBOT_MAX_ITEMS_PER_POST", "40")))

def _cut_items(resolved: Resolved) -> None:
    """Keep the first MAX_ITEMS_PER_POST items of `resolved`."""
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

SAMPLES = {
    "instagram": "https://www.instagram.com/reel/DTxk5orCKEv/",
    "tiktok": "https://www.tiktok.com/@scout2015/video/6718335390845095173",
    "twitter": "https://x.com/SpaceX/status/2042988940756480302",
    "pinterest": "https://www.pinterest.com/pin/27725353928390009/",
    "youtube": "https://www.youtube.com/shorts/W05mCB2bDAU",
}

PROBE_FETCH_BYTES = 64 * 1024

PROBE_FETCH_TIMEOUT_S = 20.0

async def probe_route(name, fn, url: str, fetch: bool = False,
                      timeout: float | None = None) -> dict:
    """One route, one link. Never raises."""
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
    row["seconds"] = round(time.perf_counter() - started, 1)
    return row

async def _probe_fetch(item: MediaItem) -> str:
    """Prove the media URL actually serves, without pulling the whole file."""
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
                return f"{got:,} bytes, {resp.headers.get('content-type', '?')}"
        except Exception as exc:
            last = f"{type(exc).__name__}: {exc}"
    return f"fetch failed: {last}"

async def probe_all(urls: dict[str, str] | None = None, fetch: bool = False,
                    timeout: float | None = None) -> dict:
    """Every route for every platform in `urls` (default: SAMPLES)."""
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
    """The report, as plain text -- it goes to a Telegram message and to a terminal, and both want the same thing."""
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
    """Put one MediaItem on disk and return the path, no bigger than `max_mb` (the free ceiling if None)."""
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

# ─── module: downloader_bot.routewatch ───────────────────────────────────────
"""Once a day, ask every download route whether it still works -- and whether yt-dlp is still current -- from inside the container, and tell the owner when something is about to run out rather than after a user has hit it."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import family_link
import net
import resolvers

logger = logging.getLogger(__name__)

_TRUE = ("1", "true", "yes", "on")
_FALSE = ("0", "false", "no", "off", "")

def _flag(name: str, default: str) -> bool:
    """A switch from the environment."""
    value = os.environ.get(name, default).strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    return default.strip().lower() in _TRUE

ENABLED = _flag("DBOT_ROUTEWATCH", "1")
FIRST_RUN_S = float(os.environ.get("DBOT_ROUTEWATCH_FIRST_S", "600"))
TICK_S = 6 * 3600
MIN_GAP_S = 20 * 3600
PROBE_TIMEOUT_S = float(os.environ.get("DBOT_PROBE_TIMEOUT", "12"))
REALERT_DOWN_S = 3 * 86400
YTDLP_STALE_DAYS = int(os.environ.get("DBOT_YTDLP_STALE_DAYS", "14"))
YTDLP_COOLDOWN_DAYS = int(os.environ.get("DBOT_YTDLP_COOLDOWN_DAYS", "3"))
AUTOUPDATE = _flag("DBOT_YTDLP_AUTOUPDATE", "0")
PYPI_JSON = "https://pypi.org/pypi/yt-dlp/json"

REPLY_WITHIN_S = 75.0

ALERT_PLATFORMS = ("instagram", "tiktok", "twitter", "pinterest")

UP, LAST_RESORT, DOWN, SAMPLE_GONE = "up", "last_resort", "down", "sample_gone"
_KEY = "routewatch:state:"
_LAST_RUN_KEY = "routewatch:last_run"
_YTDLP_ALERTED_KEY = "routewatch:ytdlp_alerted"

_lock = asyncio.Lock()

_ytdlp_lock = asyncio.Lock()

_background: set = set()

STILL_RUNNING = "A route check is already running; /nightly shows the states once it is done."

async def check_platform(platform: str, url: str, timeout: float = PROBE_TIMEOUT_S) -> list[dict]:
    """The chain's routes in order, stopping at the first that works."""
    rows: list[dict] = []
    for name, fn in resolvers.PROVIDERS.get(platform, []):

        if name.startswith("ytdlp:"):
            allowed = max(timeout, resolvers._timeout_for(name))

            async with _ytdlp_lock:
                row = await resolvers.probe_route(name, fn, url, fetch=True, timeout=allowed)
        else:
            row = await resolvers.probe_route(name, fn, url, fetch=True, timeout=timeout)
        fetched = str(row.get("fetched") or "")
        if row["ok"] and fetched.startswith(("fetch failed", "file gone")):
            row["ok"] = False
            row["detail"] = f"resolved, but its media did not serve: {fetched}"
        rows.append(row)
        if row["ok"]:
            break
    return rows

def classify(rows: list[dict], chain_length: int) -> str:
    working = [i for i, row in enumerate(rows) if row["ok"]]
    if working:
        return LAST_RESORT if chain_length > 1 and working[0] == chain_length - 1 else UP
    if rows and all(row["detail"].startswith("missing") for row in rows):
        return SAMPLE_GONE
    return DOWN

def _parse(record: "str | None") -> "tuple[str, float, float] | None":
    try:
        state, since, alerted = (record or "").split("|")
        return state, float(since), float(alerted)
    except ValueError:
        return None

def transition(record: "str | None", new_state: str, now: float):
    """What a platform's new state means, given what it was."""
    old = _parse(record)
    if old is None:
        old_state, since, alerted = UP, now, 0.0
    else:
        old_state, since, alerted = old
    alert = None
    if new_state == old_state:
        if new_state == DOWN and now - alerted >= REALERT_DOWN_S:
            alert, alerted = ("error", "still_down"), now
    else:
        since = now
        if new_state == DOWN:
            alert, alerted = ("error", "down"), now
        elif new_state == LAST_RESORT:
            alert, alerted = ("warning", "last_resort"), now
        elif new_state == SAMPLE_GONE:
            alert, alerted = ("warning", "sample_gone"), now
        elif old_state in (DOWN, LAST_RESORT):
            alert = ("info", "recovered")
    return f"{new_state}|{since:.0f}|{alerted:.0f}", alert

def _lines(rows: list[dict]) -> list[str]:
    return [f"{'ok  ' if row['ok'] else 'FAIL'} {row['provider']}: {resolvers.tidy_error(row['detail'])}"
            for row in rows]

def _message(platform: str, key: str, rows: list[dict], since_days: float) -> str:
    name = platform.capitalize()
    if key == "down":
        return (f"No download route works for {name}, asked from inside the container. "
                f"People who paste a {name} link get DL-ALL-ROUTES-FAILED until one comes back.")
    if key == "still_down":
        return f"{name} still has no working download route -- {since_days:.0f} days now."
    if key == "last_resort":
        return (f"{name} is down to its last download route ({rows[-1]['provider']}); "
                "the ones ahead of it failed. One more failure and it is down.")
    if key == "sample_gone":
        return (f"Every {name} route says the sample link is gone. Probably the post was "
                "deleted: replace it in resolvers.SAMPLES.")
    return f"{name} has a working download route again."

_VERSION_RE = re.compile(r"(\d{4})\.(\d{1,2})\.(\d{1,2})(?:\.(\d+))?")

def version_key(text: "str | None") -> "tuple | None":
    """'2026.08.19' and '2026.8.19' are the same release; a '.1' tail is a later one the same day."""
    m = _VERSION_RE.search(text or "")
    if not m:
        return None
    return tuple(int(g or 0) for g in m.groups())

def _installed_ytdlp() -> "str | None":
    try:
        import video
        found = video.ytdlp_version()
    except Exception:
        found = None
    if found:
        return found
    try:
        from importlib import metadata
        return metadata.version("yt-dlp")
    except Exception:
        return None

async def latest_ytdlp() -> "tuple[str, datetime] | None":
    """(version, when it was published) of yt-dlp's newest release, or None if PyPI did not say."""
    try:
        raw = await net.fetch_bytes(PYPI_JSON, limit=8 * 1024 * 1024)
        data = json.loads(raw)
        tag = str(data["info"]["version"])

        uploads = [datetime.fromisoformat(str(f["upload_time_iso_8601"]).replace("Z", "+00:00"))
                   for f in data.get("urls") or []]

        uploads = [t if t.tzinfo else t.replace(tzinfo=timezone.utc) for t in uploads]
        if not uploads:
            return None
        return tag, min(uploads)
    except Exception as exc:
        logger.warning("Could not ask PyPI for yt-dlp's newest release: %s: %s", type(exc).__name__, exc)
        return None

def ytdlp_verdict(installed: "str | None", latest_tag: str, published: datetime,
                  now: "datetime | None" = None) -> dict:
    """Pure: is the container behind, by how much, and is the release old enough to take (it has been out YTDLP_COOLDOWN_DAYS)."""
    now = now or datetime.now(timezone.utc)
    have, want = version_key(installed), version_key(latest_tag)
    behind = bool(have and want and have < want)
    gap_days = 0
    if behind:
        gap_days = (datetime(*want[:3], tzinfo=timezone.utc) - datetime(*have[:3], tzinfo=timezone.utc)).days
    age_days = (now - published).total_seconds() / 86400
    return {"behind": behind, "gap_days": gap_days, "age_days": age_days,
            "stale": behind and gap_days >= YTDLP_STALE_DAYS,
            "cooled": age_days >= YTDLP_COOLDOWN_DAYS}

async def update_ytdlp(tag: str) -> "tuple[bool, str]":
    """Fetch yt-dlp `tag` from PyPI into a folder of its own and make every later download use it."""
    version = tag.lstrip("v")
    base = Path(os.environ.get("DBOT_YTDLP_DIR") or tempfile.gettempdir())
    target = base / f"yt-dlp-{version}"
    if not (target / "yt_dlp" / "version.py").is_file():
        cmd = [sys.executable, "-m", "pip", "install", "--no-deps", "--only-binary=:all:",
               "--disable-pip-version-check", "--no-input", "--quiet",
               "--target", str(target), f"yt-dlp=={version}"]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=300)
        except asyncio.TimeoutError:
            proc.kill()
            return False, "pip did not finish in 5 minutes"
        except OSError as exc:
            return False, f"pip could not be started: {exc}"
        if proc.returncode != 0:
            return False, "pip failed: " + resolvers.tidy_error(out.decode("utf-8", "replace")[-400:])

    probe = ("import sys; sys.path.insert(0, sys.argv[1]); "
             "import yt_dlp, yt_dlp.version as v; print(v.__version__)")
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-c", probe, str(target),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        out, err = await asyncio.wait_for(proc.communicate(), timeout=60)
    except (asyncio.TimeoutError, OSError) as exc:
        return False, f"the new yt-dlp could not be tried: {type(exc).__name__}"
    got = out.decode().strip()
    if proc.returncode != 0 or version_key(got) != version_key(version):
        return False, "the new yt-dlp did not import as " + version + ": " + \
            resolvers.tidy_error((err or out).decode("utf-8", "replace")[-300:])
    import video
    swapped = await asyncio.to_thread(video.use_ytdlp_from, str(target))
    if not swapped:
        return False, "downloads were still running after 5 minutes; will try again"
    return True, got

async def check_ytdlp() -> "str | None":
    """Compare, say so once, and update if allowed. Returns a line for the report."""
    latest = await latest_ytdlp()
    if latest is None:
        return None
    tag, published = latest
    installed = _installed_ytdlp()
    verdict = ytdlp_verdict(installed, tag, published)
    if not verdict["behind"]:
        return f"yt-dlp {installed} is current ({tag})."
    line = (f"yt-dlp {installed} is {verdict['gap_days']} days behind {tag} "
            f"(released {verdict['age_days']:.0f} days ago).")
    if AUTOUPDATE and verdict["cooled"]:
        done, what = await update_ytdlp(tag)
        if done:
            family_link.report_event_soon("info", "ytdlp", f"DownloaderBot updated yt-dlp {installed} -> {what}.")
            return line + f" Updated to {what}."
        line += f" Update failed: {what}."
        problem = f"The automatic yt-dlp update to {tag} failed: {what}"
    elif AUTOUPDATE:

        return line + f" Waiting {YTDLP_COOLDOWN_DAYS} days before taking it."
    elif verdict["stale"]:
        problem = (f"yt-dlp in the container is {installed}; {tag} has been out {verdict['age_days']:.0f} "
                   "days. TikTok, Instagram and YouTube extractors break between releases. Redeploy "
                   "without the build cache, or set DBOT_YTDLP_AUTOUPDATE=1.")
    else:
        return line

    already = await asyncio.to_thread(family_link.get_setting, _YTDLP_ALERTED_KEY)
    if already != tag:
        await asyncio.to_thread(family_link.set_setting, _YTDLP_ALERTED_KEY, tag)
        family_link.report_event_soon("warning", "ytdlp", problem)
    return line

def _header() -> str:
    return ("Download routes, asked from inside this container "
            f"({os.environ.get('RAILWAY_ENVIRONMENT') or 'local'}):")

class _Progress:
    """What a run has finished so far, for a forced run that has to answer before the run does (see run())."""

    def __init__(self):
        self.order = list(resolvers.SAMPLES)
        self.waiting = list(self.order)
        self.blocks: dict = {}

    def partial(self) -> str:
        lines = [_header()]
        for platform in self.order:
            lines += self.blocks.get(platform, [])

        names = ", ".join(self.waiting) or "yt-dlp's newest release"
        lines.append(f"Still checking: {names}. The check carries on; any change is alerted "
                     "as usual, and /nightly shows the states once it is done.")
        return "\n".join(lines)

async def _check(force: bool, progress: _Progress) -> str:
    """The run itself. The caller holds _lock."""
    now = time.time()
    if not force:
        last = await asyncio.to_thread(family_link.get_setting, _LAST_RUN_KEY)
        try:
            if last and now - float(last) < MIN_GAP_S:
                return "skipped: checked less than 20 hours ago"
        except ValueError:
            pass
    await asyncio.to_thread(family_link.set_setting, _LAST_RUN_KEY, f"{now:.0f}")
    samples = dict(resolvers.SAMPLES)
    progress.order, progress.waiting = list(samples), list(samples)

    async def one(platform: str, url: str) -> None:
        try:
            chain = resolvers.PROVIDERS.get(platform, [])
            try:
                rows = await check_platform(platform, url)
            except Exception:
                logger.warning("route check for %s failed", platform, exc_info=True)
                return
            state = classify(rows, len(chain))
            key = _KEY + platform
            old = await asyncio.to_thread(family_link.get_setting, key)
            record, alert = transition(old, state, now)
            await asyncio.to_thread(family_link.set_setting, key, record)
            progress.blocks[platform] = [f"{platform}: {state}"] + ["  " + line for line in _lines(rows)]
            if alert and platform in ALERT_PLATFORMS:
                level, what = alert
                since = (now - (_parse(record) or ("", now, 0))[1]) / 86400
                family_link.report_event_soon(level, "routes", _message(platform, what, rows, since),
                                              "\n".join(_lines(rows)))
        finally:
            progress.waiting.remove(platform)

    results = await asyncio.gather(*(one(p, u) for p, u in samples.items()), return_exceptions=True)
    for result in results:
        if isinstance(result, BaseException):
            raise result
    report = [_header()]
    for platform in progress.order:
        report += progress.blocks.get(platform, [])
    line = await check_ytdlp()
    if line:
        report.append(line)
    text = "\n".join(report)
    logger.info("%s", text)
    return text

async def _check_logged(force: bool, progress: _Progress) -> str:
    """_check as a task of its own."""
    try:
        return await _check(force, progress)
    except Exception:
        logger.warning("the route check failed", exc_info=True)
        raise

def _background_done(task) -> None:
    """However the run ended -- and even if it was cancelled before it began, when its own code would never have run -- the lock it was started holding is let go."""
    _background.discard(task)
    _lock.release()
    if not task.cancelled():
        task.exception()

async def run(force: bool = False, reply_within: "float | None" = None) -> str:
    """The whole check. Returns the report as text (the bus command shows it)."""
    if force and _lock.locked():
        return STILL_RUNNING
    progress = _Progress()
    if reply_within is None:
        async with _lock:
            return await _check(force, progress)
    await _lock.acquire()
    task = asyncio.ensure_future(_check_logged(force, progress))
    _background.add(task)
    task.add_done_callback(_background_done)
    done, _ = await asyncio.wait({task}, timeout=reply_within)
    if task in done:
        return task.result()
    return progress.partial()

async def _tick(_context=None) -> None:
    try:
        await run()
    except Exception:
        logger.warning("the daily route check failed", exc_info=True)

def schedule(application) -> None:
    """Called from _post_init."""
    if not ENABLED or application.job_queue is None:
        return
    application.job_queue.run_repeating(_tick, interval=TICK_S, first=FIRST_RUN_S)

# ─── module: downloader_bot.large_files ──────────────────────────────────────
"""Downloads over 48 MB: fetched anyway, sent over MTProto, and paid for -- only for somebody who has said yes."""
from __future__ import annotations

import math
import os

import big_files
import pricing

MB = 1024 * 1024
big_files.configure("DBOT", "downloaderbot")

FREE_MAX_MB = int(os.environ.get("DBOT_MAX_DOWNLOAD_MB", "48"))

LARGE_MAX_MB = min(int(os.environ.get("DBOT_LARGE_MAX_MB") or big_files.MTPROTO_MAX_BYTES // MB),
                   big_files.MTPROTO_MAX_BYTES // MB)

RUNNING_SECONDS = 60

BOT_API_SEND_BYTES = big_files.BOT_API_SEND_BYTES
CONFIGURED = big_files.CONFIGURED

def _refused() -> None:
    """Telegram refused the API id and hash (big_files.verify): no switch, and FREE_MAX_MB is the limit, as if they had never been set."""
    global CONFIGURED
    CONFIGURED = False

big_files.on_disabled(_refused)

def price_for(size_bytes: int) -> int:
    """⚡ for a large download of this size, by the 100 MB begun."""
    blocks = max(1, math.ceil(size_bytes / (100 * MB)))
    return pricing.price(pricing.cost(RUNNING_SECONDS, blocks * 100 * MB))

def first_price() -> int:
    """What the first 100 MB cost, and the least any large download does."""
    return price_for(1)

def top_price() -> int:
    """What the largest download there is costs."""
    return price_for(LARGE_MAX_MB * MB)

def available(chat_id: int) -> bool:
    """Whether a file over the free ceiling can be had in this chat at all: the route is on, and this is a private chat."""
    return big_files.usable(chat_id)
