"""One HTTP client for the whole process, and the two ways this bot fetches
media with it.

Every fetch used to build its own `httpx.AsyncClient` inside an `async with`
-- a fresh TCP connection and TLS handshake per request, thrown away straight
after, against hosts considerably further away than the database this family
already learned that lesson on. One client, kept open, reuses connections
across requests and across users. The pool is small on purpose: this bot is
never talking to more than a handful of hosts at once.

Split out of platforms.py at v1.2.1, because resolvers.py needs the same
client and importing it back out of a module about *page scraping* had the
dependency arrow pointing the wrong way.
"""
from __future__ import annotations

import asyncio
import ipaddress
import os
import socket
import zlib

import httpx

# A real browser's UA. Several of the media hosts below serve a challenge page
# to anything that admits to being a script, and a challenge page is not
# something this bot can answer.
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")

# The other half of the story: the "fix the embed" services (tnktok,
# eeinstagram, fxtwitter, ...) exist to serve link-preview crawlers, and they
# decide what to put in the page from the User-Agent. Ask as a browser and you
# get the human-facing page; ask as a crawler and you get the OpenGraph tags
# with the media URL in them. So those providers ask as a crawler, on purpose.
CRAWLER_UA = "TelegramBot (like TwitterBot)"

# Telegram itself refuses uploads over 50 MB without a local Bot API server,
# so anything larger was always going to be fetched and then thrown away.
MAX_RESPONSE_BYTES = int(os.environ.get("DBOT_MAX_DOWNLOAD_MB", "48")) * 1024 * 1024

CONNECT_TIMEOUT = float(os.environ.get("DBOT_CONNECT_TIMEOUT", "10"))
READ_TIMEOUT = float(os.environ.get("DBOT_READ_TIMEOUT", "30"))
# How long an address gets to answer when the host has another to try after it.
FAILOVER_CONNECT_TIMEOUT = 4.0


class FetchError(Exception):
    """Raised with a message that is safe to show the user as-is."""


class BadEncoding(FetchError):
    """The server sent a body in an encoding this bot does not read (or one
    that did not decode). See body_chunks()."""


class Trickle(BadEncoding):
    """The server sent its body in far too many pieces for its size: see
    body_chunks(). A BadEncoding so that whoever already treats an unreadable
    body as a failed route (platforms.py's Reddit and Twitter reads) does the
    same here, without a change of its own."""


# ---------------------------------------------------------------------------
# Only the public internet
# ---------------------------------------------------------------------------
# Every address this client fetches was handed to it by somebody else: a link
# a stranger pasted, a page a stranger's link led to, a media URL a third-party
# resolver service put in its answer, a post a stranger wrote on Reddit. Left
# alone it will fetch any of them -- including `http://127.0.0.1:...`, an
# address on the container's own private network, or a cloud host's metadata
# service at 169.254.169.254 -- and follow a redirect from a public address to
# any of those. What it fetched then comes back to the person as the "photo" or
# the file they asked for (the lossless setting sends the bytes as they are).
#
# So a request is checked where it is made: the name is resolved here, every
# address it resolves to must be on the public internet, and the connection is
# then made to the address that was checked -- not to whatever the name
# resolves to a moment later, which a DNS server built for the purpose can
# arrange to be different. Every hop of a redirect goes through the same
# check, because each is a request of its own.
#
# yt-dlp does its own networking and is not covered by this; what reaches it
# is a link to a site the bot names (platforms.py), which is what it is for.
ALLOW_PRIVATE_HOSTS = os.environ.get("DBOT_ALLOW_PRIVATE_HOSTS", "").strip().lower() in ("1", "true", "yes")


class BlockedAddress(Exception):
    """The name resolves to an address that is not on the public internet."""


def is_public_address(address: str) -> bool:
    """Whether an IP address is somewhere on the public internet: not this
    machine, a private network, a link-local address (which is where cloud
    metadata services live), shared address space, multicast, or unspecified.
    An IPv4 address written as IPv6 is judged as the IPv4 address it is."""
    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return False
    if ip.version == 6 and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return bool(ip.is_global and not ip.is_multicast)


async def _resolve(host: str, port: int) -> list[str]:
    """Every address `host` resolves to. Its own function so that a test can say
    what a name resolves to."""
    infos = await asyncio.wait_for(
        asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM), CONNECT_TIMEOUT)
    addresses: list[str] = []
    for _family, _type, _proto, _canon, sockaddr in infos:
        if sockaddr[0] not in addresses:
            addresses.append(sockaddr[0])
    return addresses


async def public_addresses(host: str, port: int) -> list[str]:
    """The addresses to try for `host`, all of them public -- or BlockedAddress.

    One address that is not public refuses the name: a name that answers with a
    mixture is how the checked address and the connected address are made to
    differ."""
    addresses = await _resolve(host, port)
    if not addresses:
        raise OSError(f"no address for {host}")
    if not ALLOW_PRIVATE_HOSTS:
        for address in addresses:
            if not is_public_address(address):
                raise BlockedAddress(f"{host} is not a public address")
    return addresses


def _guarded_transport(inner=None):
    """The transport `client()` uses: `inner` (an ordinary httpx transport)
    behind public_addresses(). Built here rather than at import so that this
    module can still be imported where httpx is a stand-in."""

    class Refused(httpx.ConnectError):
        pass

    class PublicOnlyTransport(httpx.AsyncBaseTransport):
        def __init__(self):
            self._inner = inner or httpx.AsyncHTTPTransport(
                limits=httpx.Limits(max_connections=8, max_keepalive_connections=4,
                                    keepalive_expiry=30.0))

        async def handle_async_request(self, request):
            url = request.url
            host = url.raw_host.decode("ascii")          # the ASCII form: it is also what TLS names
            port = url.port or (443 if url.scheme == "https" else 80)
            try:
                addresses = await public_addresses(host, port)
            except BlockedAddress as exc:
                raise Refused(str(exc), request=request) from None
            except (OSError, asyncio.TimeoutError) as exc:
                raise httpx.ConnectError(f"could not resolve {host}: {exc}", request=request) from exc
            # IPv4 first, the order kept otherwise. The client this replaces
            # raced a host's addresses against each other, so an address that
            # was not answering cost a quarter of a second; these are tried in
            # turn, and a container with an IPv6 route that goes nowhere would
            # spend its whole connect timeout on the first.
            addresses = sorted(addresses, key=lambda address: ":" in address)
            failure = None
            for index, address in enumerate(addresses):
                # The request is sent to the address that was checked, with the
                # name it was asked for kept where the server and the
                # certificate look for it: the Host header, and the name TLS
                # verifies. Which is why this is a request of its own rather
                # than a change to the one it was handed.
                extensions = dict(request.extensions)
                if url.scheme == "https":
                    extensions["sni_hostname"] = host
                if index < len(addresses) - 1:
                    # Another address is waiting: this one is not given all day.
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
                    failure = exc            # the next address may answer
                    continue
                return await _without_redirect_body(response)
            raise failure

        async def aclose(self):
            await self._inner.aclose()

    return PublicOnlyTransport()


_REDIRECTS = (301, 302, 303, 307, 308)


async def _without_redirect_body(response):
    """A redirect, with its body thrown away unread; anything else, as it came.

    When httpx follows a redirect it first reads the redirect's own body whole
    -- `aread()`, decoded by its own decoders, with no limit -- and only then
    goes where the Location header says. That body is never used, and it is
    the one body body_chunks() never sees: a server answering 302 with a
    brotli or gzip bomb would decode it in this process before the next hop.
    The status and the headers are all a redirect is for, so that is all that
    is passed on."""
    if response.status_code not in _REDIRECTS or "location" not in response.headers:
        return response
    await response.aclose()
    kept = [(k, v) for k, v in response.headers.multi_items()
            if k.lower() not in ("content-encoding", "content-length", "transfer-encoding")]
    return httpx.Response(response.status_code, headers=kept, content=b"",
                          extensions=response.extensions)


_client: httpx.AsyncClient | None = None

# The only encodings this bot asks for, and the only two body_chunks() reads.
# httpx would otherwise advertise everything it can decode: `br` the moment
# brotli is installed, and yt-dlp[default] installs it; `zstd` likewise. See
# body_chunks() for why those two are not wanted.
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


# ---------------------------------------------------------------------------
# Reading a body, with a ceiling on what it may become
# ---------------------------------------------------------------------------
# A size limit that counts what the caller receives is not a limit on memory
# when something in between decompresses. httpx decodes a whole raw chunk
# before the caller sees any of it, so a 421-byte brotli body that decodes to
# 512 MB was 512 MB in this process before the first `len(chunk)` ran: the
# page reader's 8 MB cap peaked at 1,069 MB on exactly that (review,
# 2026-10-08), in a container that five bots share 1 GB of. gzip does the same
# at a thousand to one, so it is not only a brotli problem.
#
# So nothing here lets httpx decode. The bytes are read raw (aiter_raw) and
# decompressed by zlib, which can be told how much output one call may
# produce (max_length); the rest of the input waits in `unconsumed_tail`
# until the caller has counted what came out. Only gzip and deflate are read,
# because those are the two zlib can do that for. brotli and zstd cannot be
# capped from here, so they are refused outright -- and are not asked for, see
# ACCEPT_ENCODING; a server that sends one regardless is refused, not trusted.

# The most one decompress() call may return, whatever room is left under the
# limit. Without it a body allowed 48 MB would come back in one 48 MB piece,
# and stream_to_file, which exists to hold a chunk at a time, would hold all of it.
_DECODE_BLOCK = 64 * 1024


# Every Content-Encoding token that names a real encoding. A token that is not
# one of these ("none", "utf-8", "binary" -- servers do send them) means no
# encoding, as it does to httpx, and the body is read as it comes, still under
# the ceiling. Of the real ones only gzip and deflate are read.
_KNOWN_ENCODINGS = ("gzip", "x-gzip", "deflate", "br", "zstd", "compress", "x-compress")


def _content_encodings(response) -> list[str]:
    """The real encodings a body was sent in, lower-cased: "identity" (which
    means none), empty entries and tokens that are no encoding at all are
    dropped. The header, when it repeats, is already joined with commas by
    httpx."""
    names = [part.strip() for part in response.headers.get("content-encoding", "").lower().split(",")]
    return [name for name in names if name in _KNOWN_ENCODINGS]


class _Inflater:
    """One gzip or deflate body, decompressed a bounded piece at a time."""

    def __init__(self, limit: int, too_big, deflate: bool):
        self.limit = limit
        self.too_big = too_big
        self.deflate = deflate
        self.total = 0                      # decoded bytes handed out so far
        self.finished = False               # the compressed stream has ended
        self._first = deflate               # the first call still gets a second chance
        self._head = b""                    # deflate's first bytes, held until there are two to judge by
        self.d = zlib.decompressobj() if deflate else zlib.decompressobj(16 + zlib.MAX_WBITS)

    def _room(self) -> int:
        """max_length for the next call: one byte more than what may still be
        accepted, so that a body over the limit shows as over rather than as
        exactly at it."""
        return max(min(self.limit - self.total + 1, _DECODE_BLOCK), 1)

    def _decompress(self, data: bytes, room: int) -> bytes:
        try:
            return self.d.decompress(data, room)
        except zlib.error:
            if not self._first:
                raise BadEncoding("The server sent a body that did not decode.") from None
        # "deflate" is zlib-wrapped by the standard and raw by habit; servers
        # send both. The first bytes tell, and a failure on them means raw, as
        # in httpx's own decoder. The same input is given to the new decoder.
        self._first = False
        self.d = zlib.decompressobj(-zlib.MAX_WBITS)
        try:
            return self.d.decompress(data, room)
        except zlib.error:
            raise BadEncoding("The server sent a body that did not decode.") from None

    def _drain(self, data: bytes):
        """Decoded pieces of `data`, none over _DECODE_BLOCK, until zlib has
        nothing more to give for it. Raises too_big() the moment the total is
        over the limit."""
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
                # The stream is complete. Whatever follows it is not part of
                # it and is ignored, as httpx does -- and not fed in, because
                # zlib keeps all of it in `unused_data`, without a bound.
                self.finished = True
                return
            # More to take out when input is left over because the output was
            # capped, or when the cap was hit exactly and zlib may be holding
            # output it has not been asked for yet.
            if self.d.unconsumed_tail or len(out) == room:
                room = self._room()
                out = self._decompress(self.d.unconsumed_tail, room)
            else:
                return

    def feed(self, data: bytes):
        if self._first and data:
            # Wrapped or raw is told by the first two bytes (the zlib header),
            # and a first piece of one byte cannot tell: judged on it alone
            # the second chance was spent, and a raw stream failed on its next
            # byte as a body that "did not decode".
            data = self._head + data
            if len(data) < 2:
                self._head = data
                return
            self._head = b""
        if data and not self.finished:
            yield from self._drain(data)

    def finish(self):
        # (A single byte still held back here is the whole body: no deflate
        # stream is that short, so it is a truncated one, which is accepted
        # as it always was -- it gives nothing.)
        if not self.finished:
            yield from self._drain(b"")


async def body_chunks(response, limit: int, too_big):
    """The body of a streamed `response`, decoded, in pieces; `too_big()` is
    called (it returns the exception to raise) the moment more than `limit`
    decoded bytes have arrived.

    A body in an encoding this reads only as identity, gzip and deflate is
    refused with BadEncoding before a byte of it is read, whether this bot
    asked for it or not (br, zstd and compress) -- and so is two encodings
    stacked, which is a way of asking for more than one decoder's worth. A
    Content-Encoding token that names no encoding at all ("none", "utf-8")
    is no encoding, as it is to httpx, and the body is read as it is.
    Nothing about the response is trusted past what a status line says: not
    Content-Length, which a server can leave out or get wrong, and not the
    encoding's own idea of how big it will be. And a body that arrives in
    more pieces than its ceiling can explain is refused with Trickle.

    Use as `async for piece in body_chunks(...)`. A caller that stops early
    should `await pieces.aclose()` on it, so that the connection is let go now
    rather than whenever the interpreter gets to it."""
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
        # Already read whole, and decoded by httpx when that happened: what a
        # test's MockTransport hands back for `Response(content=...)`. A real
        # connection never gives one of these, and for it there is nothing
        # left to bound but the size.
        content = response.content
        if len(content) > limit:
            raise too_big()
        if content:
            yield content
        return
    # What a compressed body may weigh *before* decoding. A stream of empty
    # blocks decodes to nothing for ever and would never reach the limit; real
    # compressors stay within a fraction of a percent above the size of their
    # input, so this is a ceiling with a wide margin, not a measurement.
    raw_cap = limit + limit // 50 + 64 * 1024
    raw_total = 0
    # And how many pieces it may arrive in. Each piece costs the event loop a
    # few microseconds whatever its size, so a server that sends one byte at a
    # time makes a 2 MB body 12 seconds of this process's one thread (48 MB:
    # five minutes) -- for every bot of the combined edition at once. A real
    # server sends kilobytes at a time; this allows a piece per KB of the
    # ceiling and a thousand more.
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
    """A whole small document, read under a ceiling, as an ordinary
    `httpx.Response`: status, final URL, history, the body decoded.

    `too_big()` builds the exception for a body that grew past `limit`.
    A Content-Length that already says it will is refused before a byte is
    read, with `declared_too_big(declared)` if given (so that it can say how
    much) and `too_big()` if not. BadEncoding, see body_chunks, is the caller's
    to translate. The status is the caller's too: nothing is raised for a 404.
    """
    async with client().stream(method, url, **kwargs) as resp:
        declared = resp.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > limit:
            raise (declared_too_big(int(declared)) if declared_too_big else too_big())
        body = bytearray()
        async for chunk in body_chunks(resp, limit, too_big):
            body += chunk
        # The body is decoded already (gzip and friends), so the headers that
        # described the encoded one come off before it is wrapped again.
        kept = [(k, v) for k, v in resp.headers.multi_items()
                if k.lower() not in ("content-encoding", "content-length", "transfer-encoding")]
        done = httpx.Response(resp.status_code, headers=kept, content=bytes(body),
                              request=resp.request)
        done.history = list(resp.history)
        return done


async def fetch_bytes(url: str, limit: int = MAX_RESPONSE_BYTES,
                      headers: dict | None = None) -> bytes:
    """GET a URL into memory, refusing anything that would not fit in a
    Telegram upload anyway.

    Streamed and counted as it arrives, so a server that lies about (or omits)
    Content-Length still cannot make this process hold an unbounded amount of
    data -- and counted after decoding, by body_chunks, so that a small
    compressed body cannot become a large one on the way.
    """
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
    """Over the size ceiling this download was given. A FetchError still, so
    whoever only cares that it failed catches it as before; DownloaderBot
    catches this one first, because a file over the free ceiling may be one
    somebody has switched on large files for (large_files.py)."""


async def stream_to_file(url: str, path: str, limit: int = MAX_RESPONSE_BYTES,
                         headers: dict | None = None) -> int:
    """Same, but to disk, for the things that are too big to want in memory.

    Returns the byte count. Videos go this way and images do not, because
    python-telegram-bot streams a file object off disk to Telegram rather than
    reading it in first -- so a 45 MB video is never resident, while a 2 MB
    JPEG is not worth the round trip through the filesystem.

    HEAD is deliberately never used to size-check first. TikTok's CDN answers
    HEAD with a 503 while serving the identical GET perfectly, which turned a
    working download into a "that file is unavailable" for no reason at all.
    Content-Length on the GET says the same thing a beat later and is always
    there when it matters.
    """
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
