"""A body is read under a ceiling on what it becomes, however it was encoded.

    python3 tests/test_bounded_bodies.py

The review of 2026-10-08 sent DownloaderBot a 421-byte body that decoded to
512 MB, and the page reader with its 8 MB cap peaked at 1,069 MB: httpx decodes
a whole raw chunk before the caller can count any of it, and `yt-dlp[default]`
puts brotli on the machine, so `Content-Encoding: br` was decoded with no limit
at all. gzip does the same at a thousand to one. Also found there: a cobalt
instance's answer was read by `client().post()` whole (a 200 MB body peaked at
400 MB), pasted cookies whose tabs had become spaces were printed by yt-dlp into
the log with their secret values, the daily route check ran its platforms one
after another and could not answer ManagerBot inside 90 seconds, and yt-dlp's
freshness was asked of an API that allows 60 requests an hour per address.

Each is checked by sending the thing that hurt, on the real httpx (MockTransport
-- no socket is opened). The bodies are STREAMED from the fake server on
purpose: `httpx.Response(content=<bytes>)` is read and decoded when it is built,
which would hide exactly the thing under test.

Said where httpx is not installed, and skipped.
"""
import asyncio
import functools
import json
import logging
import os
import pathlib
import sys
import tempfile
import time
import tracemalloc
import zlib
from datetime import datetime, timezone

HERE = pathlib.Path(__file__).resolve().parent
MIRROR = HERE / ".mirror" / "downloader_bot"
sys.path.insert(0, str(MIRROR if MIRROR.exists() else HERE.parent / "downloader_bot"))
sys.path.insert(1, str(HERE.parent / "shared"))
sys.path.append(str(HERE / "stubs"))      # after site-packages: a real httpx beats the stand-in

failures = []


def check(name, ok, detail=""):
    print(("  ok  " if ok else "  FAIL  ") + name + (f" -- {detail}" if not ok and detail else ""))
    if not ok:
        failures.append(name)


def real_httpx():
    try:
        import httpx
    except ImportError:
        return None
    return httpx if hasattr(httpx, "MockTransport") else None


import family_link  # noqa: E402
import net  # noqa: E402
import platforms  # noqa: E402
import resolvers  # noqa: E402
import routewatch  # noqa: E402
import video  # noqa: E402

MB = 1024 * 1024
UNDER = 40          # MB: what the process may grow by while it refuses a bomb. The bombs decode to 100-200.


# ---------------------------------------------------------------------------
# Bodies, and a server that streams them
# ---------------------------------------------------------------------------

def sample(lines=100_000) -> bytes:
    """About 1.6 MB that compresses, so one decompress call has more output
    than one block of it."""
    return "".join(f"line {i} {i * i % 9973} some text to repeat\n" for i in range(lines)).encode()


def gzip_of(data: bytes) -> bytes:
    c = zlib.compressobj(6, zlib.DEFLATED, 31)
    return c.compress(data) + c.flush()


def zlib_of(data: bytes) -> bytes:
    return zlib.compress(data)


def raw_deflate_of(data: bytes) -> bytes:
    c = zlib.compressobj(6, zlib.DEFLATED, -zlib.MAX_WBITS)
    return c.compress(data) + c.flush()


@functools.lru_cache(maxsize=None)
def bomb_of(wbits: int, megabytes: int) -> bytes:
    """`megabytes` of zeros, compressed as a stream so the test itself stays small."""
    c = zlib.compressobj(6, zlib.DEFLATED, wbits)
    zeros = b"\0" * MB
    return b"".join(c.compress(zeros) for _ in range(megabytes)) + c.flush()


def split(data: bytes, size: int) -> list:
    return [data[i:i + size] for i in range(0, len(data), size)]


class Wire:
    """What the fake server did: how many chunks of a body were actually asked for."""

    def __init__(self, httpx):
        self.httpx = httpx
        self.reset()

    def reset(self):
        self.chunks = 0
        self.bytes = 0
        self.requests = []

    def body(self, pieces):
        wire = self

        async def gen():
            for piece in pieces:
                wire.chunks += 1
                wire.bytes += len(piece)
                yield piece
        return gen()

    def response(self, pieces, encoding=None, length=None, status=200, content_type=None):
        headers = []
        if encoding is not None:
            headers.append(("content-encoding", encoding))
        if length is not None:
            headers.append(("content-length", str(length)))
        if content_type:
            headers.append(("content-type", content_type))
        return self.httpx.Response(status, headers=headers, content=self.body(pieces))

    def install(self, handler):
        def seen(request):
            self.requests.append(request)
            return handler(request)
        net._client = self.httpx.AsyncClient(transport=self.httpx.MockTransport(seen),
                                             follow_redirects=True)


async def uninstall():
    if net._client is not None:
        await net._client.aclose()
    net._client = None


async def measured(coro):
    """(what the call returned or raised, MB the process grew by at its peak)."""
    tracemalloc.start()
    base = tracemalloc.get_traced_memory()[0]
    tracemalloc.reset_peak()
    try:
        outcome = await coro
    except Exception as exc:
        outcome = exc
    peak = (tracemalloc.get_traced_memory()[1] - base) / MB
    tracemalloc.stop()
    return outcome, peak


def tmp_path(suffix=".bin") -> str:
    fd, path = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    return path


# ---------------------------------------------------------------------------
# 1. Encodings that are not read
# ---------------------------------------------------------------------------

async def refusal_tests(httpx):
    print("\n=== an encoding this bot does not read is refused before a byte of it is read ===")
    wire = Wire(httpx)
    pieces = lambda: split(b"\x8b\x00\x80" * 400, 100)             # noqa: E731  (421-ish bytes, whatever they are)
    real_brotli = None
    try:
        import brotli
        real_brotli = brotli.compress(b"\0" * (50 * MB))
    except Exception:
        pass

    refused = ["br", "BR", "zstd", "gzip, gzip", "gzip,gzip", "gzip, br", "br, gzip", "deflate, deflate",
               "compress", "x-compress", "gzip, deflate"]
    for value in refused:
        wire.reset()
        wire.install(lambda request, value=value: wire.response(pieces(), encoding=value))
        try:
            await net.fetch_bytes("https://example.test/f")
            check(f"Content-Encoding: {value} is refused", False, "it was read")
        except net.BadEncoding as exc:
            check(f"Content-Encoding: {value} is refused (BadEncoding, a FetchError)",
                  isinstance(exc, net.FetchError))
        except Exception as exc:
            check(f"Content-Encoding: {value} is refused", False, f"{type(exc).__name__}: {exc}")
        check(f"...and not one chunk of it was read ({value})", wire.chunks == 0, str(wire.chunks))

    # The header on two lines is the same as two encodings.
    wire.reset()
    wire.install(lambda request: httpx.Response(
        200, headers=[("content-encoding", "gzip"), ("content-encoding", "gzip")],
        content=wire.body(pieces())))
    try:
        await net.fetch_bytes("https://example.test/f")
        check("gzip stated twice on two header lines is refused", False)
    except net.BadEncoding:
        check("gzip stated twice on two header lines is refused", wire.chunks == 0)

    if real_brotli is not None:
        wire.reset()
        wire.install(lambda request: wire.response([real_brotli], encoding="br"))
        outcome, peak = await measured(net.fetch_bytes("https://example.test/f"))
        check("a real brotli body (50 MB of zeros in a few dozen bytes) is refused, undecoded",
              isinstance(outcome, net.BadEncoding) and wire.chunks == 0 and peak < 5, f"{outcome!r} {peak}")
    else:
        print("  (brotli is not installed here: the real brotli body is skipped; the fake one above stands in)")

    wire.reset()
    wire.install(lambda request: wire.response(pieces(), encoding="br"))
    outcome, peak = await measured(net.fetch_bytes("https://example.test/f"))
    check("a br body of a few hundred bytes costs the process next to nothing to refuse",
          isinstance(outcome, net.BadEncoding) and wire.bytes == 0 and peak < 2, f"{outcome!r} {wire.bytes} {peak:.1f} MB")

    # The same refusal, in each reader's own words.
    wire.reset()
    wire.install(lambda request: wire.response(pieces(), encoding="br"))
    try:
        await resolvers._get("https://example.test/f")
        check("a provider page in br is a provider failure", False)
    except resolvers.ProviderFailed as exc:
        check("a provider page in br is a provider failure, kind error, saying why",
              exc.kind == "error" and "answered in an encoding this bot does not read" in str(exc)
              and "example.test" in str(exc), f"{exc.kind}: {exc}")
    check("...refused before reading", wire.chunks == 0)
    try:
        await resolvers._post("https://example.test/f", json={})
        check("a cobalt answer in zstd is a provider failure", False)
    except resolvers.ProviderFailed as exc:
        check("a cobalt answer in zstd is a provider failure", exc.kind == "error")
    path = tmp_path()
    try:
        await net.stream_to_file("https://example.test/f", path)
        check("a download in br is refused", False)
    except net.BadEncoding:
        check("a download in br is refused, and nothing was written", os.path.getsize(path) == 0)
    finally:
        os.remove(path)

    print("\n=== gzip and deflate are read; so is a body that says it is not encoded ===")
    data = sample(5000)
    accepted = {"gzip": gzip_of(data), "GZIP": gzip_of(data), " gzip ": gzip_of(data),
                "x-gzip": gzip_of(data), "identity, gzip": gzip_of(data), "gzip, identity": gzip_of(data),
                "deflate": zlib_of(data), "identity": data, "": data}
    for value, body in accepted.items():
        wire.install(lambda request, value=value, body=body: wire.response(split(body, 4096), encoding=value))
        got = await net.fetch_bytes("https://example.test/f", limit=10 * MB)
        check(f"Content-Encoding: {value!r} decodes to the original", got == data)
    wire.install(lambda request: wire.response(split(data, 4096)))
    check("no Content-Encoding at all is read as it is", await net.fetch_bytes("https://example.test/f", limit=10 * MB) == data)
    await uninstall()


# ---------------------------------------------------------------------------
# 2. Honest bodies come out whole
# ---------------------------------------------------------------------------

async def honest_tests(httpx):
    print("\n=== an honest body decodes byte for byte, however it arrives ===")
    wire = Wire(httpx)
    big = sample()
    small = sample(1500)
    forms = {"gzip": gzip_of, "deflate (zlib-wrapped)": zlib_of, "deflate (raw)": raw_deflate_of}
    for label, make in forms.items():
        encoding = "gzip" if label == "gzip" else "deflate"
        body, little = make(big), make(small)
        for chunking, size in (("one chunk", len(body)), ("64 KiB chunks", 65536), ("1000-byte chunks", 1000)):
            wire.install(lambda request, body=body, size=size, encoding=encoding:
                         wire.response(split(body, size), encoding=encoding))
            got = await net.fetch_bytes("https://example.test/f", limit=10 * MB)
            check(f"{label}, {chunking}: {len(big):,} bytes out of {len(body):,}", got == big)
        wire.install(lambda request, little=little, encoding=encoding:
                     wire.response(split(little, 7), encoding=encoding))
        check(f"{label}, 7-byte chunks", await net.fetch_bytes("https://example.test/f", limit=10 * MB) == small)
        path = tmp_path()
        try:
            wire.install(lambda request, body=body, encoding=encoding:
                         wire.response(split(body, 30000), encoding=encoding))
            written = await net.stream_to_file("https://example.test/f", path, limit=10 * MB)
            with open(path, "rb") as f:
                check(f"{label}: a download is written decoded, and the count is what was written",
                      f.read() == big and written == len(big))
        finally:
            os.remove(path)
        wire.install(lambda request, body=body, encoding=encoding:
                     wire.response(split(body, 65536), encoding=encoding, content_type="application/json"))
        page = await net.read_page("GET", "https://example.test/f", limit=10 * MB, too_big=lambda: net.FetchError("big"))
        check(f"{label}: read_page hands back an ordinary Response, decoded, without the encoding's headers",
              page.content == big and page.status_code == 200 and "content-encoding" not in page.headers
              and page.headers.get("content-type") == "application/json" and str(page.url) == "https://example.test/f",
              str(dict(page.headers)))
    for label, make, encoding in (("gzip", gzip_of, "gzip"), ("zlib-wrapped deflate", zlib_of, "deflate")):
        wire.install(lambda request, make=make, encoding=encoding:
                     wire.response(split(make(small), 1), encoding=encoding))
        check(f"{label}, one byte at a time", await net.fetch_bytes("https://example.test/f", limit=10 * MB) == small)

    plain = sample(3000)
    wire.install(lambda request: wire.response([b"", plain[:1000], b"", plain[1000:], b""]))
    check("an identity body is passed through, empty chunks and all",
          await net.fetch_bytes("https://example.test/f", limit=10 * MB) == plain)

    print("\n=== the limit is on what the body becomes, to the byte ===")
    body = gzip_of(plain)
    for encoding, payload in (("gzip", body), (None, plain)):
        wire.install(lambda request, payload=payload, encoding=encoding: wire.response(split(payload, 5000), encoding=encoding))
        label = encoding or "identity"
        check(f"{label}: a body exactly at the limit is accepted",
              await net.fetch_bytes("https://example.test/f", limit=len(plain)) == plain)
        try:
            await net.fetch_bytes("https://example.test/f", limit=len(plain) - 1)
            check(f"{label}: one byte over the limit is refused", False)
        except net.FetchError as exc:
            check(f"{label}: one byte over the limit is refused, in the usual words",
                  not isinstance(exc, net.BadEncoding) and "too big" in str(exc), str(exc))

    print("\n=== what follows the end of a compressed stream is ignored, and not even read ===")
    member = gzip_of(plain)
    junk = (b"\xde\xad\xbe\xef" * (MB // 4) for _ in range(30))        # 30 MB, lazily
    wire.reset()
    wire.install(lambda request: wire.response(split(member, 65536) + [j for j in junk], encoding="gzip"))
    got = await net.fetch_bytes("https://example.test/f", limit=10 * MB)
    check("a gzip stream with 30 MB of trailing junk gives the stream", got == plain)
    check("...and the junk was not read (zlib would keep all of it in unused_data)",
          wire.bytes < 2 * MB, f"{wire.bytes:,} bytes were asked for")

    print("\n=== a body that does not decode is refused as such ===")
    bad = {"not gzip at all": (b"this is plainly not a gzip stream " * 20, "gzip"),
           "a gzip stream with its check value spoiled": (member[:-6] + bytes([member[-6] ^ 0xFF]) + member[-5:], "gzip"),
           "not deflate": (b"\xff\xff\xff\xff not deflate " * 20, "deflate")}
    for label, (payload, encoding) in bad.items():
        wire.install(lambda request, payload=payload, encoding=encoding: wire.response(split(payload, 100), encoding=encoding))
        try:
            await net.fetch_bytes("https://example.test/f")
            check(f"{label} is refused", False)
        except net.BadEncoding:
            check(f"{label} is refused (BadEncoding)", True)

    print("\n=== a stream of empty blocks decodes to nothing for ever; it is cut off on size ===")
    header = bytes([0x1f, 0x8b, 8, 0, 0, 0, 0, 0, 0, 3])
    empty_block = bytes([0, 0, 0, 0xff, 0xff])             # a stored block of length 0, not the last
    sent = lambda: (empty_block * 1000 for _ in range(1000))   # noqa: E731  5 MB of them, lazily
    wire.reset()
    wire.install(lambda request: wire.response([header] + [p for p in sent()], encoding="gzip"))
    try:
        await net.fetch_bytes("https://example.test/f", limit=100_000)
        check("a gzip stream of empty blocks is refused", False)
    except net.FetchError as exc:
        check("a gzip stream of empty blocks is refused as too big", not isinstance(exc, net.BadEncoding), str(exc))
    check("...after a bounded amount of it, not the whole 5 MB", wire.bytes < 1 * MB, f"{wire.bytes:,}")
    await uninstall()


# ---------------------------------------------------------------------------
# 3. The bombs
# ---------------------------------------------------------------------------

async def bomb_tests(httpx):
    print("\n=== a small body that decodes to hundreds of MB is stopped at the limit ===")
    wire = Wire(httpx)
    bombs = {"gzip": (bomb_of(31, 200), "gzip", 200), "deflate (zlib)": (bomb_of(15, 100), "deflate", 100),
             "deflate (raw)": (bomb_of(-15, 100), "deflate", 100)}
    for label, (bomb, encoding, megabytes) in bombs.items():
        print(f"  ({label}: {len(bomb):,} bytes would decode to {megabytes} MB)")
        wire.install(lambda request, bomb=bomb, encoding=encoding: wire.response([bomb], encoding=encoding))

        outcome, peak = await measured(resolvers._get("https://example.test/f"))
        check(f"{label}: a provider page is refused as a provider failure",
              isinstance(outcome, resolvers.ProviderFailed) and outcome.kind == "error"
              and "kept sending past" in str(outcome), repr(outcome))
        check(f"{label}: ...the process grew by {peak:.1f} MB at most", peak < UNDER, f"{peak:.1f} MB")

    bomb = bombs["gzip"][0]
    wire.install(lambda request: wire.response([bomb], encoding="gzip"))
    outcome, peak = await measured(net.fetch_bytes("https://example.test/f", limit=4 * MB))
    check("fetch_bytes refuses it, in its usual words", isinstance(outcome, net.FetchError)
          and str(outcome) == "That file is too big to send through Telegram.", repr(outcome))
    check(f"...growing by {peak:.1f} MB (the limit was 4)", peak < UNDER, f"{peak:.1f} MB")

    path = tmp_path()
    try:
        outcome, peak = await measured(net.stream_to_file("https://example.test/f", path, limit=4 * MB))
        check("stream_to_file refuses it as TooLarge", isinstance(outcome, net.TooLarge), repr(outcome))
        check(f"...growing by {peak:.1f} MB", peak < UNDER, f"{peak:.1f} MB")
        check("...having written no more than the limit", os.path.getsize(path) <= 4 * MB, str(os.path.getsize(path)))
    finally:
        os.remove(path)

    outcome, peak = await measured(resolvers._post("https://example.test/f", json={"a": 1}))
    check("a cobalt answer that is a bomb is refused as a provider failure",
          isinstance(outcome, resolvers.ProviderFailed) and "kept sending past" in str(outcome), repr(outcome))
    check(f"...growing by {peak:.1f} MB", peak < UNDER, f"{peak:.1f} MB")

    item = resolvers.MediaItem("video", url="https://cdn.test/v.mp4")
    outcome, peak = await measured(resolvers._probe_fetch(item))
    check("the probe's media fetch stops at its cap and calls that a success",
          outcome == f"{resolvers.PROBE_FETCH_BYTES:,} bytes, ?", repr(outcome))
    check(f"...growing by {peak:.1f} MB", peak < UNDER, f"{peak:.1f} MB")

    outcome, peak = await measured(platforms.fetch_reddit_post("https://www.reddit.com/r/x/comments/abc/t/"))
    check("a Reddit answer that is a bomb is 'couldn't read that post'",
          isinstance(outcome, net.FetchError) and str(outcome) == "Couldn't read that Reddit post -- it may have been removed.",
          repr(outcome))
    check(f"...growing by {peak:.1f} MB", peak < UNDER, f"{peak:.1f} MB")
    outcome, peak = await measured(platforms.fetch_tweet_syndication("https://x.com/a/status/1234567890"))
    check("a syndication answer that is a bomb is None, like its other failures", outcome is None, repr(outcome))
    check(f"...growing by {peak:.1f} MB", peak < UNDER, f"{peak:.1f} MB")
    await uninstall()


# ---------------------------------------------------------------------------
# 4. What a Content-Length already says
# ---------------------------------------------------------------------------

async def declared_tests(httpx):
    print("\n=== a length that is already over the cap is refused before a byte is read ===")
    wire = Wire(httpx)
    over = resolvers.PAGE_LIMIT_BYTES + 10
    pieces = lambda: split(b"x" * 100_000, 10_000)           # noqa: E731
    wire.install(lambda request: wire.response(pieces(), length=over))

    try:
        await resolvers._get("https://example.test/f")
        check("_get refuses a declared size over the cap", False)
    except resolvers.ProviderFailed as exc:
        check("_get refuses it, saying how big it said it was",
              f"{over:,} bytes, more than a page may be" in str(exc) and exc.kind == "error", str(exc))
    check("...before reading", wire.chunks == 0)
    try:
        await resolvers._post("https://example.test/f", json={})
        check("_post refuses a declared size over the cap", False)
    except resolvers.ProviderFailed as exc:
        check("_post refuses it too", "more than a page may be" in str(exc))
    check("...before reading", wire.chunks == 0)
    try:
        await net.read_page("GET", "https://example.test/f", limit=resolvers.PAGE_LIMIT_BYTES,
                            too_big=lambda: net.FetchError("the generic one"))
        check("read_page without a declared-size message uses too_big()", False)
    except net.FetchError as exc:
        check("read_page without a declared-size message uses too_big()", str(exc) == "the generic one", str(exc))
    wire.install(lambda request: wire.response(pieces(), length=60 * MB))
    try:
        await net.fetch_bytes("https://example.test/f")
        check("fetch_bytes refuses a declared size over its limit", False)
    except net.FetchError as exc:
        check("fetch_bytes refuses it, in its usual words", str(exc) == "That file is too big to send through Telegram.")
    check("...before reading", wire.chunks == 0)
    path = tmp_path()
    try:
        await net.stream_to_file("https://example.test/f", path)
        check("stream_to_file refuses a declared size over its limit", False)
    except net.TooLarge:
        check("stream_to_file refuses it as TooLarge, before reading, writing nothing",
              wire.chunks == 0 and os.path.getsize(path) == 0)
    finally:
        os.remove(path)

    wire.install(lambda request: wire.response(pieces(), length="12abc"))
    page = await net.read_page("GET", "https://example.test/f", limit=1 * MB, too_big=lambda: net.FetchError("big"))
    check("a Content-Length that is not all digits is not believed, and not a reason to refuse",
          len(page.content) == 100_000)

    print("\n=== and one that is not declared is cut off at the cap ===")
    chunk = b"x" * 65536
    endless = lambda: (chunk for _ in range(resolvers.PAGE_LIMIT_BYTES // len(chunk) + 40))   # noqa: E731
    wire.reset()
    wire.install(lambda request: wire.response(endless()))
    try:
        await resolvers._get("https://example.test/f")
        check("_get cuts an undeclared answer off", False)
    except resolvers.ProviderFailed as exc:
        check("_get cuts it off, in its own words",
              f"kept sending past {resolvers.PAGE_LIMIT_BYTES:,} bytes" in str(exc), str(exc))
    cap_chunks = resolvers.PAGE_LIMIT_BYTES // len(chunk)
    check("...at the cap, not after the whole stream", cap_chunks <= wire.chunks <= cap_chunks + 2, str(wire.chunks))
    await uninstall()


# ---------------------------------------------------------------------------
# 5. cobalt
# ---------------------------------------------------------------------------

async def cobalt_tests(httpx):
    print("\n=== cobalt's answer is read like any page ===")
    wire = Wire(httpx)
    chunk = b"y" * 65536
    cap_chunks = resolvers.PAGE_LIMIT_BYTES // len(chunk)
    seen = {}

    def handler(request):
        seen["method"] = request.method
        seen["body"] = request.content
        host = request.url.host
        if host == "endless.test":
            return wire.response((chunk for _ in range(cap_chunks * 4)), status=200)
        if host == "declared.test":
            return wire.response((chunk for _ in range(cap_chunks * 4)), length=cap_chunks * len(chunk) * 4)
        if host == "bomb.test":
            return wire.response([bomb_of(31, 200)], encoding="gzip")
        if host == "br.test":
            return wire.response([b"\x00" * 50], encoding="br")
        return wire.response([json.dumps({"status": "tunnel", "url": "https://good.test/t/1",
                                          "filename": "clip.mp4"}).encode()])

    wire.install(handler)
    ok = await resolvers._cobalt_one("https://good.test", "https://youtu.be/x", "youtube", None)
    check("an ordinary cobalt answer still works, sent as a POST with its JSON body",
          ok.items[0].url == "https://good.test/t/1" and seen["method"] == "POST" and b"youtu.be" in seen["body"])

    for host, label, words in (("endless.test", "an answer of 4x the cap and no length", "kept sending past"),
                               ("declared.test", "an answer that says it is 4x the cap", "more than a page may be"),
                               ("bomb.test", "a 200 MB gzip bomb", "kept sending past")):
        wire.reset()
        outcome, peak = await measured(resolvers._cobalt_one(f"https://{host}", "https://youtu.be/x", "youtube", None))
        check(f"{label}: refused as a provider failure the chain moves past",
              isinstance(outcome, resolvers.ProviderFailed) and outcome.kind == "error" and words in str(outcome), repr(outcome))
        check(f"{label}: read no further than the cap (chunks: {wire.chunks})", wire.chunks <= cap_chunks + 2, str(wire.chunks))
        check(f"{label}: the process grew by {peak:.1f} MB at most", peak < UNDER, f"{peak:.1f} MB")
    wire.reset()
    try:
        await resolvers._cobalt_one("https://br.test", "https://youtu.be/x", "youtube", None)
        check("a cobalt answer in br is a provider failure", False)
    except resolvers.ProviderFailed as exc:
        check("a cobalt answer in br is a provider failure, unread", "encoding this bot does not read" in str(exc) and wire.chunks == 0)

    # ...and through the provider, the next instance is tried, as for any failure.
    saved = list(resolvers.COBALT_INSTANCES)
    resolvers.COBALT_INSTANCES[:] = ["https://endless.test", "https://good.test"]
    resolvers._cobalt_last_good = None
    try:
        r = await resolvers._cobalt_provider("youtube")("https://youtu.be/x")
        check("an instance that sends too much is skipped for the next one", r.items[0].url == "https://good.test/t/1")
    finally:
        resolvers.COBALT_INSTANCES[:] = saved
        resolvers._cobalt_last_good = None
    await uninstall()


# ---------------------------------------------------------------------------
# 6. Reddit and X
# ---------------------------------------------------------------------------

async def text_page_tests(httpx):
    print("\n=== Reddit's and X's answers are read under the cap too ===")
    wire = Wire(httpx)
    unreadable = "Couldn't read that Reddit post -- it may have been removed."
    post = [{"data": {"children": [{"data": {"title": "hello", "is_video": False}}]}}]
    tweet = {"__typename": "Tweet", "text": "hi"}
    chunk = b"z" * 65536
    over = lambda: (chunk for _ in range(8 * MB // len(chunk) + 40))         # noqa: E731
    mode = {"v": "ok"}

    def handler(request):
        m = mode["v"]
        data = json.dumps(tweet if "syndication" in request.url.host else post).encode()
        if m == "ok":
            return wire.response([data], content_type="application/json")
        if m == "gzip":
            return wire.response(split(gzip_of(data), 20), encoding="gzip")
        if m == "over":
            return wire.response(over())
        if m == "declared":
            return wire.response(over(), length=9 * MB)
        if m == "bomb":
            return wire.response([bomb_of(31, 200)], encoding="gzip")
        if m == "br":
            return wire.response([data], encoding="br")
        if m == "403":
            return wire.response([b"no"], status=403)
        return wire.response([b"{}"], status=500)

    wire.install(handler)
    reddit = lambda: platforms.fetch_reddit_post("https://www.reddit.com/r/x/comments/abc/t/?utm=1")   # noqa: E731
    tweet_call = lambda: platforms.fetch_tweet_syndication("https://x.com/a/status/1234567890")        # noqa: E731

    check("an ordinary Reddit answer is read", (await reddit())["title"] == "hello")
    mode["v"] = "gzip"
    check("...gzipped, too", (await reddit())["title"] == "hello")
    check("an ordinary syndication answer is read (gzipped)", (await tweet_call()) == tweet)
    mode["v"] = "ok"
    check("...and plain", (await tweet_call()) == tweet)
    for m, label in (("over", "an answer past the cap with no length"), ("declared", "an answer that says it is past the cap"),
                     ("bomb", "a gzip bomb"), ("br", "an answer in brotli")):
        mode["v"] = m
        wire.reset()
        outcome, peak = await measured(reddit())
        check(f"Reddit, {label}: 'couldn't read that post'",
              isinstance(outcome, net.FetchError) and str(outcome) == unreadable, repr(outcome))
        check(f"Reddit, {label}: read no further than the cap and grew by {peak:.1f} MB",
              wire.chunks <= 8 * MB // len(chunk) + 2 and peak < UNDER, f"{wire.chunks} chunks, {peak:.1f} MB")
        wire.reset()
        outcome, peak = await measured(tweet_call())
        check(f"syndication, {label}: None", outcome is None, repr(outcome))
        check(f"syndication, {label}: read no further than the cap and grew by {peak:.1f} MB",
              wire.chunks <= 8 * MB // len(chunk) + 2 and peak < UNDER, f"{wire.chunks} chunks, {peak:.1f} MB")
    mode["v"] = "403"
    try:
        await reddit()
        check("Reddit's 403 is still its own sentence", False)
    except net.FetchError as exc:
        check("Reddit's 403 is still its own sentence", str(exc).startswith("Reddit blocked this request"), str(exc))
    check("a non-200 from syndication is still None", await tweet_call() is None)
    mode["v"] = "500"
    check("...and a 500", await tweet_call() is None)
    await uninstall()


# ---------------------------------------------------------------------------
# 7. The probe's media fetch
# ---------------------------------------------------------------------------

async def probe_tests(httpx):
    print("\n=== the probe fetches a media address, under a cap and a deadline ===")
    wire = Wire(httpx)
    item = resolvers.MediaItem("video", url="https://cdn.test/v.mp4")
    big = b"v" * (3 * MB)
    mode = {"v": "chunks"}

    def handler(request):
        m = mode["v"]
        if m == "chunks":
            return wire.response(split(big, 16384), content_type="video/mp4")
        if m == "one":
            return wire.response([big], content_type="video/mp4")
        if m == "small":
            return wire.response([b"x" * 1000], content_type="video/mp4")
        if m == "gzip-small":
            return wire.response(split(gzip_of(b"y" * 5000), 100), encoding="gzip", content_type="video/mp4")
        if m == "br":
            return wire.response([b"\0" * 100], encoding="br")
        if m == "404":
            return wire.response([b"gone"], status=404)
        if m == "drip":
            async def drip():
                yield b"a" * 100
                await asyncio.sleep(30)
                yield b"b"
            return httpx.Response(200, content=drip())
        return httpx.Response(500)

    wire.install(handler)
    cap = resolvers.PROBE_FETCH_BYTES
    check("the probe's fetch cap is 64 KiB and its deadline 20 s", cap == 64 * 1024 and resolvers.PROBE_FETCH_TIMEOUT_S == 20.0)

    got = await resolvers._probe_fetch(item)
    check("a file bigger than the cap is a success: the cap's worth is reported", got == f"{cap:,} bytes, video/mp4", got)
    check("...and the rest of the file was not pulled", wire.bytes <= cap + 16384, f"{wire.bytes:,}")
    mode["v"] = "one"
    got = await resolvers._probe_fetch(item)
    check("a single chunk far bigger than the cap is a success too, not an error", got == f"{cap:,} bytes, video/mp4", got)
    mode["v"] = "small"
    check("a file smaller than the cap reports its size", await resolvers._probe_fetch(item) == "1,000 bytes, video/mp4")
    mode["v"] = "gzip-small"
    check("a gzipped file reports what it decodes to", await resolvers._probe_fetch(item) == "5,000 bytes, video/mp4")
    mode["v"] = "br"
    wire.reset()
    got = await resolvers._probe_fetch(item)
    check("a file in an encoding that is not read is 'fetch failed', and unread",
          got.startswith("fetch failed: BadEncoding") and wire.bytes == 0, f"{got} / {wire.bytes}")
    mode["v"] = "404"
    check("a 404 is 'fetch failed' as before", (await resolvers._probe_fetch(item)).startswith("fetch failed: HTTPStatusError"))
    check("an item with no address at all is 'fetch failed', not a crash",
          (await resolvers._probe_fetch(resolvers.MediaItem("video"))).startswith("fetch failed"))

    mode["v"] = "drip"
    real = resolvers.PROBE_FETCH_TIMEOUT_S
    resolvers.PROBE_FETCH_TIMEOUT_S = 1.0
    try:
        async def provider(url):
            return resolvers.Resolved("tiktok", "fake", [item])
        started = time.perf_counter()
        row = await resolvers.probe_route("fake", provider, "http://x/tt", fetch=True, timeout=5)
        took = time.perf_counter() - started
    finally:
        resolvers.PROBE_FETCH_TIMEOUT_S = real
    check("a media address that sends a byte and then nothing is given up on, as 'fetch failed: no media in 1s'",
          row["fetched"] == "fetch failed: no media in 1s", str(row))
    check("...in about the deadline, not the 30 s of the server", 0.8 < took < 4, f"{took:.1f}s")
    check("...and the route itself did resolve", row["ok"] is True)

    saved_providers = dict(resolvers.PROVIDERS)
    mode["v"] = "br"
    resolvers.PROVIDERS["tiktok"] = [("fake", provider)]
    try:
        rows = await routewatch.check_platform("tiktok", "http://x/tt")
    finally:
        resolvers.PROVIDERS.clear(); resolvers.PROVIDERS.update(saved_providers)
    check("routewatch counts a route whose media is in an encoding that is not read as not serving",
          rows and not rows[0]["ok"] and "did not serve" in rows[0]["detail"], str(rows))
    await uninstall()


# ---------------------------------------------------------------------------
# 8. What is asked for
# ---------------------------------------------------------------------------

async def request_header_tests(httpx):
    print("\n=== the client asks for gzip and deflate, and for nothing it cannot bound ===")
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, headers={"content-type": "application/json"}, content=b'{"ok": true}')

    real_transport = net._guarded_transport
    net._guarded_transport = lambda inner=None: httpx.MockTransport(handler)
    net._client = None
    # httpx offers whatever it can decode: `gzip, deflate` where brotli and zstd are
    # not installed, and `gzip, deflate, br, zstd` where they are -- which is every
    # machine yt-dlp[default] is installed on, this bot's among them. Without them
    # here the default would be right by accident, so this is the production case.
    import httpx._client as httpx_client
    offered = getattr(httpx_client, "ACCEPT_ENCODING", None)
    if offered is not None:
        httpx_client.ACCEPT_ENCODING = "gzip, deflate, br, zstd"
    try:
        client = net.client()
        check("the client is built with a default of exactly 'gzip, deflate'",
              client.headers.get("accept-encoding") == "gzip, deflate", str(client.headers.get("accept-encoding")))
        await net.fetch_bytes("https://example.test/a")
        await net.fetch_bytes("https://example.test/b", headers={"Referer": "https://example.test/"})
        await resolvers._get("https://example.test/c", params={"q": 1}, crawler=True)
        await resolvers._post("https://example.test/d", json={"a": 1})
        path = tmp_path()
        try:
            await net.stream_to_file("https://example.test/e", path)
        finally:
            os.remove(path)
        wrong = [(r.url.path, r.headers.get("accept-encoding")) for r in seen if r.headers.get("accept-encoding") != "gzip, deflate"]
        check("every request this bot makes through the client sent exactly that", len(seen) == 5 and not wrong, str(wrong))
        check("...and a caller's own headers did not displace it", seen[1].headers.get("referer") == "https://example.test/")
        check("br and zstd are never mentioned",
              all("br" not in r.headers.get("accept-encoding", "") and "zstd" not in r.headers.get("accept-encoding", "")
                  for r in seen))
    finally:
        if offered is not None:
            httpx_client.ACCEPT_ENCODING = offered
        net._guarded_transport = real_transport
        await uninstall()


# ---------------------------------------------------------------------------
# 9. Cookies pasted into a variable
# ---------------------------------------------------------------------------

class Capture(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.records = []

    def emit(self, record):
        self.records.append(record)


def cookie_validation_tests():
    print("\n=== pasted cookies are checked before yt-dlp can print them ===")
    names = ("DBOT_YT_COOKIES", "DBOT_IG_COOKIES", "DBOT_COOKIES")
    saved = {k: os.environ.get(k) for k in names}
    handler = Capture()
    video.logger.addHandler(handler)
    old_level = video.logger.level
    video.logger.setLevel(logging.DEBUG)
    video._cookie_text_files.clear()
    video._cookie_text_refused.clear()
    made = []
    try:
        for k in names:
            os.environ.pop(k, None)
        good = ("# Netscape HTTP Cookie File\n"
                "# a comment with spaces in it\n"
                "\n"
                ".youtube.com\tTRUE\t/\tTRUE\t0\tSID\tSECRETVALUE1\n"
                "#HttpOnly_.youtube.com\tTRUE\t/\tTRUE\t0\tHSID\tSECRETVALUE2\n")
        os.environ["DBOT_YT_COOKIES"] = good
        path = video.cookiefile_for("youtube")
        made.append(path)
        check("a real cookies.txt (comments, a blank line, an #HttpOnly_ line) is written", bool(path))
        check("...exactly as pasted", path and pathlib.Path(path).read_text(encoding="utf-8") == good)
        check("...readable by this process only", path and ((os.stat(path).st_mode & 0o077) == 0 or os.name == "nt"))
        check("...and nothing was logged", not handler.records, str([r.getMessage() for r in handler.records]))
        no_newline = good.rstrip("\n")
        video._cookie_text_files.clear()
        os.environ["DBOT_YT_COOKIES"] = no_newline
        path2 = video.cookiefile_for("youtube")
        made.append(path2)
        check("a final newline is added when it was missing, as before", path2 and pathlib.Path(path2).read_text(encoding="utf-8") == good)
        # yt-dlp reads no cookie at all unless the first line is the header.
        cookie_lines = good.split("\n", 1)[1]
        for label, pasted in (("without the header", cookie_lines),
                              ("with blank lines before the header", "\n\n" + good)):
            video._cookie_text_files.clear()
            os.environ["DBOT_YT_COOKIES"] = pasted
            written = video.cookiefile_for("youtube")
            made.append(written)
            body = pathlib.Path(written).read_text(encoding="utf-8") if written else ""
            check(f"cookies pasted {label} are written with the header as the first line",
                  body.startswith("# Netscape HTTP Cookie File\n") and body.endswith(cookie_lines), repr(body[:40]))
            try:
                from yt_dlp.cookies import YoutubeDLCookieJar
            except ImportError:
                print(f"  SKIP yt-dlp's loader reads both cookies ({label}): yt-dlp is not installed")
            else:
                jar = YoutubeDLCookieJar(written)
                try:
                    jar.load(ignore_discard=True, ignore_expires=True)
                except Exception as exc:          # LoadError: "does not look like a Netscape format cookies file"
                    print("   ", type(exc).__name__)
                check(f"...and yt-dlp's own loader reads both cookies from it ({label})", len(jar) == 2, str(len(jar)))

        video._cookie_text_files.clear()
        handler.records.clear()
        os.environ["DBOT_YT_COOKIES"] = good.replace("\t", " ")
        results = [video.cookiefile_for("youtube") for _ in range(3)]
        check("the same lines with the tabs turned into spaces give no cookie file", results == [None, None, None], str(results))
        check("...none was written", not video._cookie_text_files)
        warnings = [r for r in handler.records if r.levelno == logging.WARNING]
        check("...and the reason is logged once, however many downloads ask", len(warnings) == 1, str(len(handler.records)))
        text = warnings[0].getMessage() if warnings else ""
        check("...in words that name the variable and the line, and that those cookies are not used",
              text == ("DBOT_YT_COOKIES is not a cookies.txt: line 4 of 5 has 1 tab-separated field(s), not 7 "
                       "(were the tabs pasted as spaces?). Those cookies are not used."), text)
        every = " ".join(r.getMessage() + " " + str(r.args) + " " + (r.exc_text or "") for r in handler.records)
        check("...with no part of the cookies in it", all(s not in every for s in ("SECRETVALUE", "SID", "HSID", "youtube.com")), every)

        handler.records.clear()
        os.environ["DBOT_YT_COOKIES"] = good.replace("\t", "\\t").replace("\n", "\\n")
        video._cookie_text_files.clear()
        flat = video.cookiefile_for("youtube")
        made.append(flat)
        check("a host that flattened the lines into literal \\n and \\t still gets a file", flat and pathlib.Path(flat).read_text(encoding="utf-8") == good)
        os.environ["DBOT_YT_COOKIES"] = good.replace("\t", " ").replace("\n", "\\n")
        video._cookie_text_files.clear()
        check("...but not when the flattened tabs were spaces", video.cookiefile_for("youtube") is None)

        handler.records.clear()
        for label, line, fields in (("six", ".a.com\tTRUE\t/\tTRUE\t0\tSID", 6),
                                    ("eight", ".a.com\tTRUE\t/\tTRUE\t0\tSID\tv\textra", 8)):
            os.environ["DBOT_IG_COOKIES"] = "# Netscape HTTP Cookie File\n" + line + "\n"
            video._cookie_text_refused.clear()
            check(f"a line of {label} fields is refused", video.cookiefile_for("instagram") is None)
            msgs = [r.getMessage() for r in handler.records]
            check(f"...saying so ({fields} fields, line 2 of 2)",
                  msgs and f"line 2 of 2 has {fields} tab-separated field(s), not 7" in msgs[-1], str(msgs))
        os.environ["DBOT_IG_COOKIES"] = "#HttpOnly_.a.com TRUE / TRUE 0 SID secret\n"
        video._cookie_text_refused.clear()
        check("an #HttpOnly_ line is a cookie, so one with spaces is refused (not skipped as a comment)",
              video.cookiefile_for("instagram") is None)

        handler.records.clear()
        os.environ["DBOT_IG_COOKIES"] = "# only comments\n\n   \n"
        video._cookie_text_refused.clear()
        ig = video.cookiefile_for("instagram")
        if ig:
            made.append(ig)
        check("a file of nothing but comments and blank lines is not a problem", ig is not None and not handler.records)

        # A refused variable is remembered per text: a corrected value is looked at again.
        video._cookie_text_files.clear()
        video._cookie_text_refused.clear()
        handler.records.clear()
        os.environ["DBOT_YT_COOKIES"] = "nothing like a cookie file"
        video.cookiefile_for("youtube"); video.cookiefile_for("youtube")
        check("a different bad value is a different warning, once", len(handler.records) == 1)
        os.environ["DBOT_YT_COOKIES"] = good
        again = video.cookiefile_for("youtube")
        made.append(again)
        check("...and the value fixed is accepted at once", bool(again))

        # The every-site variable.
        video._cookie_text_files.clear()
        video._cookie_text_refused.clear()
        os.environ.pop("DBOT_YT_COOKIES"); os.environ.pop("DBOT_IG_COOKIES")
        handler.records.clear()
        os.environ["DBOT_COOKIES"] = "also not cookies"
        check("DBOT_COOKIES that is not a cookies.txt gives no jar to any site",
              video.cookiefile_for("tiktok") is None and video.cookiefile_for("youtube") is None)
        check("...warned once for the variable, not once per site", len(handler.records) == 1
              and handler.records[0].getMessage().startswith("DBOT_COOKIES is not a cookies.txt"), str(len(handler.records)))
    finally:
        video.logger.removeHandler(handler)
        video.logger.setLevel(old_level)
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        video._cookie_text_files.clear()
        video._cookie_text_refused.clear()
        for p in made:
            if p and os.path.exists(p):
                os.remove(p)


# ---------------------------------------------------------------------------
# 10. The route check, side by side
# ---------------------------------------------------------------------------

class Tracker:
    """How many fake routes of a kind were running at once."""

    def __init__(self):
        self.running = {}
        self.peak = {}
        self.calls = 0

    def enter(self, kind):
        self.calls += 1
        self.running[kind] = self.running.get(kind, 0) + 1
        self.peak[kind] = max(self.peak.get(kind, 0), self.running[kind])

    def leave(self, kind):
        self.running[kind] -= 1


def fake_route(platform, name, tracker, delay, behaviour="ok"):
    async def provider(url):
        kind = "ytdlp" if name.startswith("ytdlp:") else "other"
        tracker.enter(kind)
        try:
            await asyncio.sleep(delay)
        finally:
            tracker.leave(kind)
        if behaviour != "ok":
            raise resolvers.ProviderFailed(f"{name} says {behaviour}", behaviour)
        fd, path = tempfile.mkstemp(suffix=".mp4")
        os.write(fd, b"x" * 100)
        os.close(fd)
        return resolvers.Resolved(platform, name, [resolvers.MediaItem("video", path=path)])
    return provider


async def routewatch_tests():
    print("\n=== the daily check asks the platforms side by side, yt-dlp one at a time ===")
    handler = Capture()
    routewatch.logger.addHandler(handler)
    events = []
    real_event, real_samples, real_providers = family_link.report_event_soon, dict(resolvers.SAMPLES), dict(resolvers.PROVIDERS)
    real_ytdlp_check = routewatch.check_ytdlp
    family_link.report_event_soon = lambda level, kind, message, details=None: events.append((level, kind, message, details))
    family_link._local_settings.clear()
    routewatch._lock = asyncio.Lock()
    routewatch._ytdlp_lock = asyncio.Lock()
    routewatch._background.clear()

    async def no_ytdlp():
        return None
    routewatch.check_ytdlp = no_ytdlp
    names = ["instagram", "tiktok", "twitter", "pinterest"]

    def arrange(chains):
        resolvers.SAMPLES.clear()
        resolvers.SAMPLES.update({p: f"http://x/{p}" for p in chains})
        resolvers.PROVIDERS.clear()
        resolvers.PROVIDERS.update(chains)

    try:
        # -- side by side, in the report's order --
        t = Tracker()
        delays = {"instagram": 0.6, "tiktok": 0.3, "twitter": 0.3, "pinterest": 0.3}
        arrange({p: [(f"{p}1", fake_route(p, f"{p}1", t, delays[p]))] for p in names})
        started = time.perf_counter()
        report = await routewatch.run(force=True)
        took = time.perf_counter() - started
        check(f"four platforms of 0.6 s, 0.3 s, 0.3 s and 0.3 s take about the slowest ({took:.2f} s), not the sum (1.5 s)",
              took < 1.1, f"{took:.2f}s")
        check("...they really did overlap", t.peak.get("other", 0) >= 3, str(t.peak))
        order = [report.index(f"{p}: up") for p in names if f"{p}: up" in report]
        check("the report lists them in SAMPLES' order even though the first one finished last",
              len(order) == 4 and order == sorted(order), report)
        check("the report starts with its header and has every platform's route lines",
              report.startswith("Download routes, asked from inside this container") and report.count("ok   ") == 4, report)
        check("each platform's state was recorded", all((family_link.get_setting(f"routewatch:state:{p}") or "").startswith("up|") for p in names))

        # -- alerts and a platform that raises --
        events.clear()
        family_link._local_settings.clear()
        t = Tracker()

        async def explodes(url):
            raise RuntimeError("the probe itself broke")
        real_check = routewatch.check_platform

        async def check_platform(platform, url, timeout=routewatch.PROBE_TIMEOUT_S):
            if platform == "tiktok":
                raise RuntimeError("check_platform broke")
            return await real_check(platform, url, timeout)
        routewatch.check_platform = check_platform
        arrange({"instagram": [("ig1", fake_route("instagram", "ig1", t, 0.05, "blocked")),
                               ("ig2", fake_route("instagram", "ig2", t, 0.05, "error"))],
                 "tiktok": [("tt1", fake_route("tiktok", "tt1", t, 0.05))],
                 "twitter": [("tw1", fake_route("twitter", "tw1", t, 0.05))]})
        handler.records.clear()
        try:
            report = await routewatch.run(force=True)
        finally:
            routewatch.check_platform = real_check
        check("a platform that raises is logged and left out; the others are reported",
              "tiktok" not in report.replace("Download routes", "") and "twitter: up" in report and "instagram: down" in report, report)
        check("...with the warning log it had", any(r.levelno == logging.WARNING and "route check for tiktok failed" in r.getMessage()
                                                    and r.exc_info for r in handler.records))
        check("the alert for a platform with nothing left still goes out", any(e[0] == "error" and "Instagram" in e[2] for e in events), str(events))
        check("...once", sum(1 for e in events if "Instagram" in e[2]) == 1)

        # -- one yt-dlp at a time --
        t = Tracker()
        arrange({p: [(f"{p}1", fake_route(p, f"{p}1", t, 0.05, "blocked")),
                     (f"ytdlp:{p}", fake_route(p, f"ytdlp:{p}", t, 0.2))] for p in names})
        started = time.perf_counter()
        report = await routewatch.run(force=True)
        took = time.perf_counter() - started
        check("yt-dlp probes of four platforms never overlapped", t.peak.get("ytdlp") == 1, str(t.peak))
        check("...though the ordinary routes ahead of them did", t.peak.get("other", 0) >= 3, str(t.peak))
        check("...all four platforms still finished (their yt-dlp route was asked, in turn)",
              all(f"{p}: last_resort" in report for p in names), report)
        check(f"...taking at least four yt-dlp turns ({took:.2f} s)", took >= 0.75, f"{took:.2f}s")

        # -- a forced run that answers before it is done --
        print("\n=== a forced run answers in time and goes on ===")
        events.clear()
        family_link._local_settings.clear()
        t = Tracker()
        arrange({"instagram": [("ig1", fake_route("instagram", "ig1", t, 0.05))],
                 "tiktok": [("tt1", fake_route("tiktok", "tt1", t, 1.0))],
                 "twitter": [("tw1", fake_route("twitter", "tw1", t, 1.2, "blocked")),
                             ("tw2", fake_route("twitter", "tw2", t, 0.05, "blocked"))]})
        started = time.perf_counter()
        partial = await routewatch.run(force=True, reply_within=0.4)
        took = time.perf_counter() - started
        check(f"it answers at reply_within, not when the check is done ({took:.2f} s)", 0.3 < took < 0.9, f"{took:.2f}s")
        check("...with the platforms finished so far, in the report's own lines", "instagram: up\n  ok   ig1: " in partial, partial)
        check("...and the ones still being asked, named, with what happens next",
              partial.endswith("Still checking: tiktok, twitter. The check carries on; any change is alerted as usual, "
                               "and /nightly shows the states once it is done."), partial)
        check("...without a line for a platform that is not finished", "tiktok: " not in partial.split("Still checking")[0]
              and "twitter: " not in partial.split("Still checking")[0])
        check("the run goes on holding the lock", routewatch._lock.locked())
        check("...in a task that something keeps a reference to", len(routewatch._background) == 1)
        calls_before = t.calls
        started = time.perf_counter()
        second = await routewatch.run(force=True, reply_within=routewatch.REPLY_WITHIN_S)
        took = time.perf_counter() - started
        check("a second forced run meanwhile is answered at once", took < 0.15 and second == routewatch.STILL_RUNNING, f"{took:.2f}s {second!r}")
        check("...in the words it was given", second == "A route check is already running; /nightly shows the states once it is done.")
        second = await routewatch.run(force=True)
        check("...whether or not it asked for an early answer", second == routewatch.STILL_RUNNING)
        check("...and it started nothing", t.calls == calls_before, f"{t.calls} vs {calls_before}")
        background = list(routewatch._background)
        await asyncio.wait(background, timeout=10)
        await asyncio.sleep(0.05)
        check("the background run finished, and let the lock go", not routewatch._lock.locked() and not routewatch._background)
        states = {p: family_link.get_setting(f"routewatch:state:{p}") for p in ("instagram", "tiktok", "twitter")}
        check("it recorded every state as usual", states["instagram"].startswith("up|") and states["tiktok"].startswith("up|")
              and states["twitter"].startswith("down|"), str(states))
        check("...and sent the alert for the platform that is down", any(e[0] == "error" and "Twitter" in e[2] for e in events), str(events))
        full = background[0].result()
        check("the finished run's own report is the full one", "tiktok: up" in full and "twitter: down" in full and "Still checking" not in full, full)
        again = await routewatch.run(force=True)
        check("a forced run after it is a normal run", again.startswith("Download routes") and "Still checking" not in again)

        # -- a run that is quick enough --
        family_link._local_settings.clear()
        t = Tracker()
        arrange({p: [(f"{p}1", fake_route(p, f"{p}1", t, 0.05))] for p in ("instagram", "tiktok")})
        full = await routewatch.run(force=True, reply_within=routewatch.REPLY_WITHIN_S)
        check("done inside reply_within: the whole report, not a partial one",
              "instagram: up" in full and "tiktok: up" in full and "Still checking" not in full, full)
        await asyncio.sleep(0.05)
        check("...with the lock free", not routewatch._lock.locked() and not routewatch._background)

        # -- the yt-dlp comparison is what is left --
        slow_ytdlp_started = []

        async def slow_ytdlp():
            slow_ytdlp_started.append(1)
            await asyncio.sleep(0.8)
            return "yt-dlp 2026.01.01 is current (2026.01.01)."
        routewatch.check_ytdlp = slow_ytdlp
        partial = await routewatch.run(force=True, reply_within=0.3)
        check("with every platform done and the yt-dlp comparison still asking, that is what it says is left",
              "instagram: up" in partial and "Still checking: yt-dlp's newest release." in partial, partial)
        await asyncio.wait(list(routewatch._background), timeout=5)
        await asyncio.sleep(0.05)

        # -- a background run that fails is logged --
        async def broken_ytdlp():
            await asyncio.sleep(0.3)
            raise RuntimeError("the comparison broke")
        routewatch.check_ytdlp = broken_ytdlp
        handler.records.clear()
        partial = await routewatch.run(force=True, reply_within=0.1)
        check("a slow run answered early", "Still checking" in partial)
        await asyncio.wait(list(routewatch._background), timeout=5)
        await asyncio.sleep(0.05)
        logged = [r for r in handler.records if r.levelno == logging.WARNING and r.exc_info and "RuntimeError" in repr(r.exc_info[1])]
        check("...and when it then fails, the failure is logged as a warning with its traceback", len(logged) >= 1, str([r.getMessage() for r in handler.records]))
        check("...and the lock is let go", not routewatch._lock.locked() and not routewatch._background)
        routewatch.check_ytdlp = no_ytdlp
        check("so the next run works", (await routewatch.run(force=True)).startswith("Download routes"))

        # -- the daily path is unchanged: it waits for the whole thing --
        family_link._local_settings.clear()
        t = Tracker()
        arrange({"instagram": [("ig1", fake_route("instagram", "ig1", t, 0.5))]})
        started = time.perf_counter()
        out = await routewatch.run()
        check("run() without force waits for the whole run, as the daily tick does",
              time.perf_counter() - started >= 0.45 and "instagram: up" in out, out)
        out = await routewatch.run()
        check("...and a second one soon after is skipped", out.startswith("skipped"), out)
    finally:
        routewatch.logger.removeHandler(handler)
        routewatch.check_ytdlp = real_ytdlp_check
        family_link.report_event_soon = real_event
        resolvers.SAMPLES.clear(); resolvers.SAMPLES.update(real_samples)
        resolvers.PROVIDERS.clear(); resolvers.PROVIDERS.update(real_providers)
        family_link._local_settings.clear()

    print("\n=== the bus command asks for an early answer ===")
    source = (pathlib.Path(__file__).resolve().parent.parent / "downloader_bot" / "bot.py").read_text(encoding="utf-8")
    check("_bus_routewatch passes reply_within=REPLY_WITHIN_S",
          "routewatch.run(force=True, reply_within=routewatch.REPLY_WITHIN_S)" in source)
    check("...which is 75 s, inside ManagerBot's 90", routewatch.REPLY_WITHIN_S == 75.0)


# ---------------------------------------------------------------------------
# 11. yt-dlp's newest release, from PyPI
# ---------------------------------------------------------------------------

async def latest_tests(httpx):
    print("\n=== yt-dlp's newest release is asked of PyPI ===")
    handler = Capture()
    routewatch.logger.addHandler(handler)
    asked = []
    mode = {"v": "ok"}

    def answer(request):
        asked.append(str(request.url))
        m = mode["v"]
        if m == "ok":
            return httpx.Response(200, json={
                "info": {"version": "2026.10.07", "name": "yt-dlp"},
                "urls": [{"upload_time_iso_8601": "2026-10-07T12:00:00.250000Z", "filename": "b.whl"},
                         {"upload_time_iso_8601": "2026-10-07T11:59:58.100000Z", "filename": "a.tar.gz"}]})
        if m == "empty":
            return httpx.Response(200, json={"info": {"version": "2026.10.07"}, "urls": []})
        if m == "no-urls":
            return httpx.Response(200, json={"info": {"version": "2026.10.07"}})
        if m == "no-info":
            return httpx.Response(200, json={"urls": [{"upload_time_iso_8601": "2026-10-07T11:59:58Z"}]})
        if m == "no-time":
            return httpx.Response(200, json={"info": {"version": "1"}, "urls": [{"filename": "x"}]})
        if m == "html":
            return httpx.Response(200, text="<html>hi</html>")
        if m == "429":
            return httpx.Response(429, text="slow down")
        return httpx.Response(500)

    net._client = httpx.AsyncClient(transport=httpx.MockTransport(answer), follow_redirects=True)
    try:
        check("the address is PyPI's JSON for yt-dlp, and the old GitHub one is gone",
              routewatch.PYPI_JSON == "https://pypi.org/pypi/yt-dlp/json" and not hasattr(routewatch, "GITHUB_LATEST"))
        got = await routewatch.latest_ytdlp()
        check("a PyPI answer gives the version and the earliest upload of its files",
              got == ("2026.10.07", datetime(2026, 10, 7, 11, 59, 58, 100000, tzinfo=timezone.utc)), str(got))
        check("...asked at PyPI", asked == ["https://pypi.org/pypi/yt-dlp/json"], str(asked))
        check("...and that is a usable answer for the comparison",
              routewatch.ytdlp_verdict("2026.09.01", got[0], got[1], now=datetime(2026, 10, 8, tzinfo=timezone.utc))["behind"])
        handler.records.clear()
        for m in ("empty", "no-urls"):
            mode["v"] = m
            check(f"a release with no files ({m}) is no answer, None", await routewatch.latest_ytdlp() is None)
        for m, label in (("no-info", "an answer without 'info'"), ("no-time", "files without an upload time"),
                         ("html", "something that is not JSON"), ("429", "a refusal"), ("500", "a server error")):
            mode["v"] = m
            handler.records.clear()
            got = await routewatch.latest_ytdlp()
            records = [r for r in handler.records if "Could not ask PyPI" in r.getMessage()]
            check(f"{label}: None", got is None)
            check(f"{label}: logged as a WARNING, in the words it was given",
                  len(records) == 1 and records[0].levelno == logging.WARNING
                  and records[0].getMessage().startswith("Could not ask PyPI for yt-dlp's newest release: "),
                  str([(r.levelname, r.getMessage()) for r in handler.records]))
        mode["v"] = "429"
        handler.records.clear()
        await routewatch.latest_ytdlp()
        check("...with the kind of failure in it", "HTTPStatusError" in handler.records[-1].getMessage(), handler.records[-1].getMessage())
    finally:
        routewatch.logger.removeHandler(handler)
        await uninstall()


async def redirect_body_tests(httpx):
    """httpx reads a redirect's own body whole, with its own decoders, before
    following it -- the one body body_chunks never sees. The guarded transport
    hands httpx the redirect without it (review of the bounded-decoding fix,
    2026-10-08)."""
    print("\n== a redirect's body is never read ==")
    bomb = bomb_of(16 + zlib.MAX_WBITS, 200)            # 200 MB of zeros as gzip, ~200 KB
    final = b"the page itself"

    # Streamed, as a real server's body is: given bytes, httpx.Response decodes
    # them the moment it is built, which would be the test exploding, not the bot.
    wire = Wire(httpx)

    def handler(request):
        if request.url.path == "/start":
            return httpx.Response(302, headers=[("location", "https://example.com/end"),
                                                ("content-encoding", "gzip")],
                                  content=wire.body(split(bomb, 16 * 1024)))
        if request.url.path == "/brotli":
            return httpx.Response(301, headers=[("location", "/end"), ("content-encoding", "br")],
                                  content=wire.body([b"\x1b" * 4096]))
        return httpx.Response(200, content=final)

    async def fake_resolve(host, port):
        return ["93.184.216.34"]

    real_resolve = net._resolve
    net._resolve = fake_resolve
    net._client = httpx.AsyncClient(transport=net._guarded_transport(httpx.MockTransport(handler)),
                                    follow_redirects=True)
    try:
        outcome, peak = await measured(net.fetch_bytes("https://example.com/start"))
        check("a 302 carrying a 200 MB gzip bomb is followed without decoding it",
              outcome == final and peak < 20 and wire.chunks <= 1,
              f"{type(outcome).__name__}, peak {peak:.0f} MB, {wire.chunks} chunk(s) of it read")
        outcome, peak = await measured(resolvers._get("https://example.com/brotli"))
        check("a 301 in brotli is followed, its body unread",
              getattr(outcome, "content", None) == final, repr(outcome))
        response = await net._without_redirect_body(httpx.Response(200, content=b"kept"))
        check("an ordinary answer passes through untouched", response.content == b"kept")
    finally:
        net._resolve = real_resolve
        await uninstall()


# ---------------------------------------------------------------------------
# 11. The second fresh review (A7, 2026-10-08)
# ---------------------------------------------------------------------------

async def encoding_token_tests(httpx):
    print("\n=== a Content-Encoding token that names no encoding is no encoding ===")
    wire = Wire(httpx)
    data = sample(5000)
    # httpx served these as identity; so does this, still under the ceiling.
    for value in ("none", "utf-8", "UTF-8", "binary", "exi", "something-new", "none, none", "identity, binary"):
        wire.install(lambda request, value=value: wire.response(split(data, 4096), encoding=value))
        got = await net.fetch_bytes("https://example.test/f", limit=10 * MB)
        check(f"Content-Encoding: {value!r} is read as it comes", got == data)
    # ...beside a real one, the real one is what counts.
    mixed = {"none, gzip": gzip_of(data), "gzip, none": gzip_of(data), "utf-8, deflate": zlib_of(data),
             "identity, binary, gzip": gzip_of(data)}
    for value, body in mixed.items():
        wire.install(lambda request, value=value, body=body: wire.response(split(body, 4096), encoding=value))
        got = await net.fetch_bytes("https://example.test/f", limit=10 * MB)
        check(f"Content-Encoding: {value!r} is the real encoding in it, decoded", got == data)
    for value in ("none, br", "br, none", "zstd, binary", "gzip, none, gzip", "gzip, utf-8, deflate", "x-compress, none"):
        wire.reset()
        wire.install(lambda request, value=value: wire.response(split(data, 4096), encoding=value))
        try:
            await net.fetch_bytes("https://example.test/f", limit=10 * MB)
            check(f"Content-Encoding: {value!r} is still refused", False, "it was read")
        except net.BadEncoding:
            check(f"Content-Encoding: {value!r} is still refused, unread", wire.chunks == 0, str(wire.chunks))
    # A body that says "none" is held to the ceiling like any other.
    big = b"y" * (3 * MB)
    wire.reset()
    wire.install(lambda request: wire.response(split(big, 64 * 1024), encoding="binary"))
    try:
        await net.fetch_bytes("https://example.test/f", limit=1 * MB)
        check("a body called 'binary' is still capped", False, "it was read")
    except net.FetchError as exc:
        check("a body called 'binary' is still capped, as too big", not isinstance(exc, net.BadEncoding) and "too big" in str(exc), str(exc))
    check("...and stopped near the ceiling, not read to the end", wire.bytes < 2 * MB, f"{wire.bytes:,}")
    await uninstall()


async def trickle_tests(httpx):
    print("\n=== a body sent in specks costs the event loop too much to be read ===")
    wire = Wire(httpx)
    limit = 1 * MB                              # so a body may arrive in 1024 + 1024 pieces
    allowed = limit // 1024 + 1024
    check("the piece allowance is a piece per KB of the ceiling and a thousand more", allowed == 2048)
    check("a Trickle is a failed body: a BadEncoding, and so a FetchError",
          issubclass(net.Trickle, net.BadEncoding) and issubclass(net.Trickle, net.FetchError))

    specks = lambda n: [b"x"] * n                # noqa: E731
    wire.install(lambda request: wire.response(specks(allowed)))
    got = await net.fetch_bytes("https://example.test/f", limit=limit)
    check(f"exactly {allowed} pieces are read", got == b"x" * allowed)
    wire.reset()
    wire.install(lambda request: wire.response(specks(allowed + 1)))
    try:
        await net.fetch_bytes("https://example.test/f", limit=limit)
        check(f"{allowed + 1} pieces are refused", False, "it was read")
    except net.Trickle as exc:
        check(f"{allowed + 1} pieces are refused, in plain words",
              str(exc) == "The server sent its answer in pieces too small to read.", str(exc))
    check("...at the piece that passed the allowance", wire.chunks == allowed + 1, str(wire.chunks))

    # Stopped at once: the 2 MB the review sent byte by byte took 12.7 s.
    wire.reset()
    wire.install(lambda request: wire.response(specks(2 * MB)))
    started = time.perf_counter()
    outcome = await asyncio.gather(net.fetch_bytes("https://example.test/f", limit=4 * MB), return_exceptions=True)
    took = time.perf_counter() - started
    check(f"2 MB of 1-byte pieces under a 4 MB ceiling: refused in {took:.2f}s", isinstance(outcome[0], net.Trickle) and took < 0.5,
          f"{outcome!r} {took:.2f}s")
    wire.reset()
    outcome = await asyncio.gather(net.fetch_bytes("https://example.test/f", limit=48 * MB), return_exceptions=True)
    check("...and under the default 48 MB ceiling it is refused after the allowance for 48 MB and no more",
          isinstance(outcome[0], net.Trickle) and wire.chunks == 48 * 1024 + 1024 + 1, f"{outcome!r} {wire.chunks}")

    wire.reset()
    wire.install(lambda request: wire.response(specks(100_000)))
    path = tmp_path()
    try:
        started = time.perf_counter()
        try:
            await net.stream_to_file("https://example.test/f", path, limit=limit)
            check("a download in specks is refused", False, "it was written")
        except net.Trickle:
            check("a download in specks is refused (Trickle), after no more than the allowance written",
                  os.path.getsize(path) <= allowed and time.perf_counter() - started < 0.5, str(os.path.getsize(path)))
    finally:
        os.remove(path)

    # A compressed body counts the pieces it arrived in, not what they decode to.
    for label, make, enc in (("gzip", gzip_of, "gzip"), ("deflate", zlib_of, "deflate")):
        body = make(sample(20000))
        wire.reset()
        wire.install(lambda request, body=body, enc=enc: wire.response(split(body, 1), encoding=enc))
        try:
            await net.fetch_bytes("https://example.test/f", limit=limit)
            check(f"a {label} body in 1-byte pieces ({len(body):,} of them) is refused", False, "it was read")
        except net.Trickle:
            check(f"a {label} body in 1-byte pieces ({len(body):,} of them) is refused", wire.chunks == allowed + 1, str(wire.chunks))

    # Each reader's words for it.
    wire.reset()
    wire.install(lambda request: wire.response(specks(100_000)))
    saved_limit = resolvers.PAGE_LIMIT_BYTES
    resolvers.PAGE_LIMIT_BYTES = limit
    try:
        await resolvers._get("https://example.test/f")
        check("a provider page in specks is a provider failure", False)
    except resolvers.ProviderFailed as exc:
        check("a provider page in specks is a provider failure, kind error, saying why",
              exc.kind == "error" and "sent its answer in pieces too small to read" in str(exc) and "example.test" in str(exc),
              f"{exc.kind}: {exc}")
    finally:
        resolvers.PAGE_LIMIT_BYTES = saved_limit
    # (platforms.py reads Reddit's answer with TEXT_PAGE_LIMIT_BYTES; a Trickle there is a BadEncoding, so the
    # same sentence as for a bomb -- see the bomb test above -- and the syndication read gives None.)
    wire.install(lambda request: wire.response(specks(200_000)))
    reddit = await measured(platforms.fetch_reddit_post("https://www.reddit.com/r/x/comments/abc/t/"))
    check("a Reddit answer in specks is 'couldn't read that post', not a raw English line",
          isinstance(reddit[0], net.FetchError) and str(reddit[0]) == "Couldn't read that Reddit post -- it may have been removed.",
          repr(reddit[0]))
    tweet = await platforms.fetch_tweet_syndication("https://x.com/a/status/1234567890")
    check("a syndication answer in specks is None, like its other failures", tweet is None, repr(tweet))
    item = resolvers.MediaItem("video", url="https://cdn.test/v.mp4")
    wire.install(lambda request: wire.response(specks(200_000)))
    outcome = await resolvers._probe_fetch(item)
    check("the probe's media fetch of a body in specks is 'fetch failed' or has reached its cap, and ends at once",
          isinstance(outcome, str) and (outcome.startswith("fetch failed") or "bytes" in outcome), repr(outcome))

    print("\n=== honest bodies come through untouched ===")
    honest = b"h" * limit
    for size in (1024, 4096, 65536, limit):
        wire.install(lambda request, size=size: wire.response(split(honest, size)))
        got = await net.fetch_bytes("https://example.test/f", limit=limit)
        check(f"a body of exactly the ceiling in {size}-byte pieces is read whole", got == honest)
    comp = gzip_of(sample(100_000))
    for size in (1024, 4096):
        wire.install(lambda request, size=size: wire.response(split(comp, size), encoding="gzip"))
        got = await net.fetch_bytes("https://example.test/f", limit=10 * MB)
        check(f"a gzip body in {size}-byte pieces is read whole", got == sample(100_000))
    await uninstall()


async def deflate_head_tests(httpx):
    print("\n=== a raw deflate body whose first piece is a single byte ===")
    wire = Wire(httpx)
    small = sample(1500)
    raw, wrapped = raw_deflate_of(small), zlib_of(small)
    plans = {"1 byte, then the rest": lambda b: [b[:1], b[1:]],
             "an empty piece, 1 byte, then the rest": lambda b: [b"", b[:1], b[1:]],
             "1 byte, 1 byte, then the rest": lambda b: [b[:1], b[1:2], b[2:]],
             "one byte at a time": lambda b: split(b, 1)}
    for label, plan in plans.items():
        for kind, body in (("raw", raw), ("zlib-wrapped", wrapped)):
            wire.install(lambda request, body=body, plan=plan: wire.response(plan(body), encoding="deflate"))
            try:
                got = await net.fetch_bytes("https://example.test/f", limit=10 * MB)
                check(f"{kind} deflate in pieces of {label}", got == small)
            except net.FetchError as exc:
                check(f"{kind} deflate in pieces of {label}", False, f"{type(exc).__name__}: {exc}")
    wire.install(lambda request: wire.response([raw[:1], raw[1:]], encoding="deflate"))
    path = tmp_path()
    try:
        written = await net.stream_to_file("https://example.test/f", path, limit=10 * MB)
        check("...also to a file", written == len(small))
    finally:
        os.remove(path)
    # Not a deflate stream at all, in 1-byte pieces, is still refused as one that does not decode.
    wire.install(lambda request: wire.response(split(b"\xff\xff\xff\xff not deflate " * 20, 1), encoding="deflate"))
    try:
        await net.fetch_bytes("https://example.test/f")
        check("garbage labelled deflate, a byte at a time, is refused", False)
    except net.BadEncoding:
        check("garbage labelled deflate, a byte at a time, is refused (BadEncoding)", True)
    # One byte, and no more, labelled deflate: a clean outcome whichever it is.
    wire.install(lambda request: wire.response([b"x"], encoding="deflate"))
    try:
        await net.fetch_bytes("https://example.test/f")
        check("a single byte labelled deflate ends cleanly", True)
    except net.BadEncoding:
        check("a single byte labelled deflate ends cleanly", True)
    except Exception as exc:
        check("a single byte labelled deflate ends cleanly", False, f"{type(exc).__name__}: {exc}")
    await uninstall()


async def download_chain_tests(httpx):
    print("\n=== a download tries its other addresses unless the file was too big ===")
    wire = Wire(httpx)
    good = b"the picture" * 200
    work = tempfile.mkdtemp()
    hosts = lambda: [r.url.host for r in wire.requests]     # noqa: E731
    ugly = {"br": lambda: wire.response([b"\x1b" * 300], encoding="br"),
            "zstd": lambda: wire.response([b"\x28\xb5" * 100], encoding="zstd"),
            "stacked": lambda: wire.response([good], encoding="gzip, gzip"),
            "specks": lambda: wire.response([b"x"] * 5000),
            "not deflate": lambda: wire.response(split(b"\xff\xff\xff\xff not deflate " * 20, 7), encoding="deflate")}

    def serve(first):
        def handler(request):
            if request.url.host == "a.test":
                return first()
            return wire.response(split(good, 4096), content_type="image/jpeg")
        return handler

    try:
        for label, first in ugly.items():
            wire.reset()
            wire.install(serve(first))
            item = resolvers.MediaItem("photo", url="https://a.test/p.jpg", alt_urls=["https://b.test/p.jpg"])
            try:
                path = await resolvers.download(item, work_dir=work, max_mb=1)
                with open(path, "rb") as f:
                    body = f.read()
                check(f"{label} from the first address: the second one is tried, and serves the file",
                      body == good and hosts() == ["a.test", "b.test"] and item.path == path, str(hosts()))
                check("...and the failed attempt left no file behind", os.listdir(work) == [os.path.basename(path)], str(os.listdir(work)))
                os.remove(path)
            except net.FetchError as exc:
                check(f"{label} from the first address: the second one is tried", False, f"{type(exc).__name__}: {exc} {hosts()}")

        # Too big is different: the same file at another address is as big.
        wire.reset()
        wire.install(serve(lambda: wire.response(split(b"b" * (3 * MB), 64 * 1024))))
        item = resolvers.MediaItem("photo", url="https://a.test/p.jpg", alt_urls=["https://b.test/p.jpg"])
        try:
            await resolvers.download(item, work_dir=work, max_mb=1)
            check("a file over the ceiling stops the download", False, "it was written")
        except net.TooLarge:
            check("a file over the ceiling stops the download as TooLarge, and the other address is not asked",
                  hosts() == ["a.test"], str(hosts()))
        check("...with nothing left on disk", os.listdir(work) == [], str(os.listdir(work)))

        # Every address failing: the failure is what it was before.
        for label, first in (("br", ugly["br"]), ("specks", ugly["specks"])):
            wire.reset()
            wire.install(lambda request, first=first: first())
            item = resolvers.MediaItem("photo", url="https://a.test/p.jpg", alt_urls=["https://b.test/p.jpg"])
            try:
                await resolvers.download(item, work_dir=work, max_mb=1)
                check(f"every address in {label}: the download fails", False, "it was written")
            except net.TooLarge:
                check(f"every address in {label}: the download fails", False, "as TooLarge")
            except net.FetchError as exc:
                last = "The server sent its answer in pieces too small to read." if label == "specks" \
                    else "The server sent a body in an encoding this bot does not read."
                check(f"every address in {label}: all are tried, then one FetchError with the last one's words",
                      hosts() == ["a.test", "b.test"] and str(exc) == last, f"{hosts()} {exc}")
            check("...with nothing left on disk", os.listdir(work) == [], str(os.listdir(work)))
    finally:
        for name in os.listdir(work):
            os.remove(os.path.join(work, name))
        os.rmdir(work)
        await uninstall()


# --- cookies: the fields, not only their number ----------------------------

TAB = "\t"
HEADER = "# Netscape HTTP Cookie File\n"


def cookie_line(domain=".a.com", flag="TRUE", path="/", secure="TRUE", expires="0", name="SID",
                value="SECRETVALUE", prefix=""):
    return prefix + TAB.join((domain, flag, path, secure, expires, name, value)) + "\n"


def real_loader():
    """yt-dlp's own cookie loader, or None where yt-dlp is not installed:
    text -> (the secret was printed, cookies loaded)."""
    try:
        import contextlib
        import io
        from yt_dlp.cookies import YoutubeDLCookieJar
    except Exception:
        return None

    def load(text):
        fd, path = tempfile.mkstemp(suffix=".txt")
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        said, jar = io.StringIO(), YoutubeDLCookieJar()
        try:
            with contextlib.redirect_stdout(said), contextlib.redirect_stderr(said):
                try:
                    jar.load(path)
                except Exception as exc:        # its LoadError quotes the line too
                    said.write(str(exc))
        finally:
            os.remove(path)
        return "SECRETVALUE" in said.getvalue(), len(list(jar))
    return load


def cookie_rule_tests():
    print("\n=== a cookie line is checked field by field, against what yt-dlp prints ===")
    problem = video._cookie_text_problem
    load = real_loader()
    if load is None:
        print("  (yt-dlp is not installed here: the lines are not also run through its loader)")

    # Each rule, both ways. (label, line, accepted)
    cases = [
        ("a real line", cookie_line(), True),
        ("a host-only line (no leading dot, flag FALSE)", cookie_line("a.com", "FALSE"), True),
        ("an #HttpOnly_ line", cookie_line(prefix="#HttpOnly_"), True),
        ("an #HttpOnly_ host-only line", cookie_line("a.com", "FALSE", prefix="#HttpOnly_"), True),
        ("a path below the root", cookie_line(path="/a/b"), True),
        ("secure FALSE", cookie_line(secure="FALSE"), True),
        ("an expiry of a long number", cookie_line(expires="1892160000"), True),
        ("an empty expiry (a session cookie)", cookie_line(expires=""), True),
        ("an empty value", cookie_line(value=""), True),
        ("a value with spaces and signs", cookie_line(value="a b==%3D"), True),
        # the flag
        ("a leading dot with the flag FALSE", cookie_line(".a.com", "FALSE"), False),
        ("no leading dot with the flag TRUE", cookie_line("a.com", "TRUE"), False),
        ("a flag in lower case", cookie_line(flag="true"), False),
        ("an empty flag", cookie_line(flag=""), False),
        ("a flag that is neither word", cookie_line(flag="1"), False),
        ("the flag wrong on an #HttpOnly_ line", cookie_line(".a.com", "FALSE", prefix="#HttpOnly_"), False),
        # the domain
        ("an empty domain, flag TRUE", cookie_line("", "TRUE"), False),
        ("an empty domain, flag FALSE", cookie_line("", "FALSE"), False),
        # the path
        ("a path that does not begin with /", cookie_line(path="x"), False),
        ("an empty path", cookie_line(path=""), False),
        # the secure flag
        ("secure in lower case", cookie_line(secure="true"), False),
        ("secure empty", cookie_line(secure=""), False),
        ("secure that is yes", cookie_line(secure="yes"), False),
        # the expiry
        ("an expiry of -1", cookie_line(expires="-1"), False),
        ("an expiry of 1.89e9", cookie_line(expires="1.89e9"), False),
        ("an expiry with a decimal point", cookie_line(expires="123.5"), False),
        ("an expiry that is a word", cookie_line(expires="abc"), False),
        ("an expiry with a space", cookie_line(expires="12 "), False),
        ("a bad expiry on an #HttpOnly_ line", cookie_line(expires="-1", prefix="#HttpOnly_"), False),
        # the name
        ("an empty name", cookie_line(name=""), False),
    ]
    for label, line, accepted in cases:
        why = problem(HEADER + line)
        check(f"{label}: {'accepted' if accepted else 'refused'}", (why is None) == accepted, str(why))
        if why:
            check("...in words that carry no part of the line",
                  all(s not in why for s in ("SECRETVALUE", "SID", "a.com", "a b")) and why.startswith("line 2 of 2 has "), why)

    # What each refusal says, so a rule cannot be swapped for another unseen.
    says = {"a subdomains flag (field 2) that does not match its domain": cookie_line(".a.com", "FALSE"),
            "an empty domain": cookie_line(""),
            "a path (field 3) that does not start with /": cookie_line(path="x"),
            "a secure flag (field 4) that is not TRUE or FALSE": cookie_line(secure="yes"),
            "an expiry (field 5) that is not digits": cookie_line(expires="-1"),
            "an empty name (field 6)": cookie_line(name="")}
    for words, line in says.items():
        check(f"the reason for {words!r} is given", problem(line) == f"line 1 of 1 has {words}", str(problem(line)))

    # Comments.
    check("a comment is a comment, however it is indented", problem("# c\n   # c with\ttabs\n\t# c\n" + cookie_line()) is None)
    check("...but #HttpOnly_ is not a comment: a wrong line behind it is refused",
          problem("# c\n" + cookie_line(".a.com", "FALSE", prefix="#HttpOnly_")) == "line 2 of 2 has a subdomains flag (field 2) that does not match its domain")
    check("blank lines and lines of spaces are nothing", problem("\n   \n" + cookie_line() + "\n\n") is None)

    # Line endings are read the way yt-dlp reads them, so numbering and checks agree.
    check("CRLF lines are accepted", problem((HEADER + cookie_line()).replace("\n", "\r\n")) is None)
    check("CRLF lines are checked too",
          problem((HEADER + cookie_line(".a.com", "FALSE")).replace("\n", "\r\n")) == "line 2 of 2 has a subdomains flag (field 2) that does not match its domain")
    check("a bare CR ends a line, as it does for yt-dlp: the bad line behind it is found",
          problem((HEADER + cookie_line() + cookie_line(expires="-1")).replace("\n", "\r")) == "line 3 of 3 has an expiry (field 5) that is not digits")

    # The byte-order mark.
    check("a byte-order mark in front of the header is not a problem", problem("\ufeff" + HEADER + cookie_line()) is None)
    check("...nor in front of the first cookie", problem("\ufeff" + cookie_line()) is None)
    check("...and the lines behind it are still checked",
          problem("\ufeff" + HEADER + cookie_line(expires="-1")) == "line 2 of 2 has an expiry (field 5) that is not digits")

    # Through the real loader: what is accepted is silent and loads, and what the loader would print is refused.
    if load is not None:
        for label, line, accepted in cases:
            leaked, loaded = load(HEADER + line)
            if accepted:
                check(f"yt-dlp's loader reads {label} silently, as a cookie", not leaked and loaded == 1, f"leaked={leaked} loaded={loaded}")
            elif leaked:
                check(f"yt-dlp's loader would have printed {label} with its secret: it is refused", True)
        printed = [label for label, line, accepted in cases if load(HEADER + line)[0]]
        check("the loader prints the secret for the flag, expiry and empty-domain lines (the reason for these rules)",
              {"a leading dot with the flag FALSE", "no leading dot with the flag TRUE", "an expiry of -1",
               "an expiry of 1.89e9", "an empty domain, flag TRUE"} <= set(printed), str(printed))
        check("...and for none of the lines accepted", all(not accepted for label, line, accepted in cases if label in printed))
        # The property that matters, over every combination of the awkward values:
        # whatever is accepted, the loader reads without printing anything.
        import itertools
        loud = []
        checked = 0
        for prefix, domain, flag, path, secure, expires in itertools.product(
                ("", "#HttpOnly_"), (".a.com", "a.com", ""), ("TRUE", "FALSE", "true", ""), ("/", "x"),
                ("TRUE", "FALSE", "yes"), ("0", "-1", "1.5", "1e9", "99999999999999999999")):
            text = HEADER + cookie_line(domain, flag, path, secure, expires, prefix=prefix)
            if problem(text) is None:
                checked += 1
                leaked, loaded = load(text)
                if leaked or loaded != 1:
                    loud.append(text)
        check(f"of {checked} accepted combinations of awkward fields, none makes yt-dlp print or drop the line",
              checked >= 10 and not loud, str(loud[:2]))

    # End to end: the variable, the file that is written, and the loader.
    names = ("DBOT_YT_COOKIES", "DBOT_IG_COOKIES", "DBOT_COOKIES")
    saved = {k: os.environ.get(k) for k in names}
    handler = Capture()
    video.logger.addHandler(handler)
    old_level = video.logger.level
    video.logger.setLevel(logging.DEBUG)
    video._cookie_text_files.clear()
    video._cookie_text_refused.clear()
    made = []
    try:
        for k in names:
            os.environ.pop(k, None)
        good = HEADER + cookie_line() + cookie_line(prefix="#HttpOnly_", name="HSID")
        os.environ["DBOT_YT_COOKIES"] = "\ufeff" + good
        path = video.cookiefile_for("youtube")
        made.append(path)
        written = pathlib.Path(path).read_bytes().replace(b"\r\n", b"\n") if path else b""
        check("pasted text that begins with a byte-order mark gives a file", bool(path), str(handler.records))
        check("...written without the mark", written == good.encode("utf-8"), repr(written[:30]))
        if load is not None and path:
            leaked, loaded = load(written.decode("utf-8"))
            check("...which yt-dlp's loader reads as two cookies, silently", not leaked and loaded == 2, f"{leaked} {loaded}")
        check("...with nothing logged", not handler.records)

        for label, text in (("a flag that disagrees with its domain", HEADER + cookie_line(".a.com", "FALSE")),
                            ("an expiry of -1", HEADER + cookie_line(expires="-1")),
                            ("a byte-order mark and an expiry of -1", "\ufeff" + HEADER + cookie_line(expires="1.89e9"))):
            handler.records.clear()
            video._cookie_text_files.clear()
            video._cookie_text_refused.clear()
            os.environ["DBOT_YT_COOKIES"] = text
            check(f"{label}: no cookie file", video.cookiefile_for("youtube") is None and not video._cookie_text_files)
            warnings = [r for r in handler.records if r.levelno == logging.WARNING]
            every = " ".join(r.getMessage() + " " + str(r.args) for r in handler.records)
            check("...one warning, naming the variable and the line, with no part of the cookies in it",
                  len(warnings) == 1 and warnings[0].getMessage().startswith("DBOT_YT_COOKIES is not a cookies.txt: line 2 of 2 has ")
                  and all(s not in every for s in ("SECRETVALUE", "SID", "a.com", "-1", "1.89e9")), every)
            video.cookiefile_for("youtube")
            check("...and not another on the next download", len([r for r in handler.records if r.levelno == logging.WARNING]) == 1)

        # A variable that is only a byte-order mark is empty, not a file with nothing in it.
        handler.records.clear()
        video._cookie_text_refused.clear()
        os.environ["DBOT_YT_COOKIES"] = "\ufeff\n  \n"
        check("a variable of only a byte-order mark and blanks gives no file", video.cookiefile_for("youtube") is None)
    finally:
        video.logger.removeHandler(handler)
        video.logger.setLevel(old_level)
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        video._cookie_text_files.clear()
        video._cookie_text_refused.clear()
        for p in made:
            if p and os.path.exists(p):
                os.remove(p)


async def main():
    httpx = real_httpx()
    if httpx is None:
        print("(the real httpx is not installed: every check here needs it, and is skipped)")
        return
    await refusal_tests(httpx)
    await honest_tests(httpx)
    await bomb_tests(httpx)
    await declared_tests(httpx)
    await cobalt_tests(httpx)
    await text_page_tests(httpx)
    await probe_tests(httpx)
    await request_header_tests(httpx)
    await redirect_body_tests(httpx)
    cookie_validation_tests()
    cookie_rule_tests()
    await encoding_token_tests(httpx)
    await trickle_tests(httpx)
    await deflate_head_tests(httpx)
    await download_chain_tests(httpx)
    await routewatch_tests()
    await latest_tests(httpx)


if __name__ == "__main__":
    asyncio.run(main())
    print()
    if failures:
        print(f"FAILURES: {len(failures)}")
        for f in failures:
            print("  -", f)
        sys.exit(1)
    print("All bounded-body checks passed.")
