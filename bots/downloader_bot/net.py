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
    pass


class BadEncoding(FetchError):
    pass


class Trickle(BadEncoding):
    pass


ALLOW_PRIVATE_HOSTS = os.environ.get("DBOT_ALLOW_PRIVATE_HOSTS", "").strip().lower() in ("1", "true", "yes")


class BlockedAddress(Exception):
    pass


def is_public_address(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return False
    if ip.version == 6 and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return bool(ip.is_global and not ip.is_multicast)


async def _resolve(host: str, port: int) -> list[str]:
    infos = await asyncio.wait_for(
        asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM), CONNECT_TIMEOUT)
    addresses: list[str] = []
    for _family, _type, _proto, _canon, sockaddr in infos:
        if sockaddr[0] not in addresses:
            addresses.append(sockaddr[0])
    return addresses


async def public_addresses(host: str, port: int) -> list[str]:
    addresses = await _resolve(host, port)
    if not addresses:
        raise OSError(f"no address for {host}")
    if not ALLOW_PRIVATE_HOSTS:
        for address in addresses:
            if not is_public_address(address):
                raise BlockedAddress(f"{host} is not a public address")
    return addresses


def _guarded_transport(inner=None):
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
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client = None


_DECODE_BLOCK = 64 * 1024


_KNOWN_ENCODINGS = ("gzip", "x-gzip", "deflate", "br", "zstd", "compress", "x-compress")


def _content_encodings(response) -> list[str]:
    names = [part.strip() for part in response.headers.get("content-encoding", "").lower().split(",")]
    return [name for name in names if name in _KNOWN_ENCODINGS]


class _Inflater:
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


ESTIMATE_SLACK = 1.25


class TooLarge(FetchError):
    pass


async def stream_to_file(url: str, path: str, limit: int = MAX_RESPONSE_BYTES,
                         headers: dict | None = None) -> int:
    def too_big():
        return TooLarge("That file is too big to send through Telegram.")

    total = 0
    with open(path, "wb") as out:
        async with client().stream("GET", url, headers=headers or {}) as response:
            response.raise_for_status()
            declared = response.headers.get("content-length")
            if declared and int(declared) > limit:
                raise too_big()
            guessed = response.headers.get("estimated-content-length")
            if guessed and guessed.isdigit() and int(guessed) > limit * ESTIMATE_SLACK:
                raise too_big()
            async for chunk in body_chunks(response, limit, too_big):
                total += len(chunk)
                out.write(chunk)
    return total
