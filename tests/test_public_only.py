"""DownloaderBot fetches the public internet, and nothing else.

    python3 tests/test_public_only.py

Every address the download path fetches was handed to it by somebody else --
a link a stranger pasted, a page that link led to, a media URL a third-party
resolver put in its answer, a Reddit post's own link -- and it fetches what it
is given and follows redirects. So a stranger's link could aim it at
`http://127.0.0.1:...`, at the private network the container sits on, or at a
cloud host's metadata service, and what came back was sent to them as the
file they asked for. Two things closed that:

  1. The Pinterest pattern was `pinterest\\.[a-z.]+`, which is any domain that
     begins "pinterest." -- `pinterest.attacker.example` was a Pinterest
     link. That is the way a stranger's own address became a fetch.
  2. net.py's client now refuses any name that resolves to an address off the
     public internet, on every request and every hop of a redirect, and
     connects to the address it checked, so a name cannot be answered one way
     for the check and another way for the connection.

Runs on the real httpx (a local server on 127.0.0.1 is the only thing it
connects to) and says so where httpx is not installed.
"""
import asyncio
import http.server
import pathlib
import sys
import threading

HERE = pathlib.Path(__file__).resolve().parent
MIRROR = HERE / ".mirror" / "downloader_bot"
sys.path.insert(0, str(MIRROR if MIRROR.exists() else HERE.parent / "downloader_bot"))
sys.path.insert(1, str(HERE.parent / "shared"))
sys.path.append(str(HERE / "stubs"))             # after site-packages: a real httpx beats the stand-in

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
    return httpx if hasattr(httpx, "AsyncBaseTransport") else None


# ---------------------------------------------------------------------------
# Which addresses are the public internet
# ---------------------------------------------------------------------------

def addresses(net):
    print("=== which addresses are the public internet ===")
    if not hasattr(net, "is_public_address"):
        check("net.py can tell a public address from a private one", False, "it has no is_public_address")
        return
    public = ["8.8.8.8", "93.184.216.34", "1.1.1.1", "2606:4700:4700::1111", "::ffff:8.8.8.8"]
    private = [
        ("127.0.0.1", "this machine"), ("127.1.2.3", "this machine, anywhere in 127/8"),
        ("10.0.0.5", "private (10/8)"), ("172.16.0.1", "private (172.16/12)"),
        ("192.168.1.1", "private (192.168/16)"),
        ("169.254.169.254", "link-local: the cloud metadata address"),
        ("100.64.0.1", "shared address space"), ("0.0.0.0", "unspecified"),
        ("224.0.0.1", "multicast"), ("255.255.255.255", "broadcast"),
        ("::1", "this machine, IPv6"), ("fe80::1", "link-local, IPv6"),
        ("fd12:3456::1", "unique-local, IPv6 (Railway's private network)"),
        ("::ffff:127.0.0.1", "IPv4 loopback written as IPv6"),
        ("::ffff:10.0.0.5", "a private IPv4 written as IPv6"),
        ("::", "unspecified, IPv6"), ("not an address", "not an address"), ("", "empty"),
    ]
    for address in public:
        check(f"{address} is public", net.is_public_address(address))
    for address, why in private:
        check(f"{address or '(empty)'} is not public ({why})", not net.is_public_address(address))


# ---------------------------------------------------------------------------
# The links the pattern accepts
# ---------------------------------------------------------------------------

def links():
    print("\n=== what counts as a Pinterest link ===")
    import platforms
    real = [
        "https://www.pinterest.com/pin/664281013778109217/", "https://pinterest.com/pin/1/",
        "https://www.pinterest.ca/pin/441282463481903715/", "https://www.pinterest.co.uk/pin/1/",
        "https://www.pinterest.com.au/pin/1/", "https://www.pinterest.de/pin/1/",
        "https://co.pinterest.com/pin/824721750502199491/", "https://in.pinterest.com/pin/1/",
        "https://br.pinterest.com/pin/1084663891475263837/", "https://pin.it/abc123",
        "http://www.pinterest.com/pin/1/",
    ]
    lookalike = [
        "https://pinterest.attacker.example/pin/1/", "https://pinterest.localhost/pin/1/",
        "https://pinterest.com.attacker.io/pin/1/", "https://pinterest.evil.com/x",
        "https://pinterest.co.uk.attacker.io/x", "https://notpinterest.com/pin/1/",
        "https://pinterest.internal/pin/1/", "https://pinterest.com@attacker.example/pin/1/",
    ]
    for url in real:
        found = platforms.detect_platform(url)
        check(f"a real Pinterest link: {url}", bool(found) and found[0] == "pinterest",
              str(found))
    for url in lookalike:
        found = platforms.detect_platform(url)
        check(f"not Pinterest: {url}", not (found and found[0] == "pinterest"), str(found))


# ---------------------------------------------------------------------------
# The client
# ---------------------------------------------------------------------------

async def through_the_client(net, httpx):
    print("\n=== the client checks what a name resolves to ===")
    if not hasattr(net, "_guarded_transport"):
        check("net.py has a transport that checks what a name resolves to", False, "it has none")
        return
    names = {
        "cdn.example": ["93.184.216.34"],
        "both.example": ["93.184.216.34", "93.184.216.35"],
        "dual.example": ["2606:4700:4700::1111", "93.184.216.36"],
        "v6.example": ["2606:4700:4700::1111"],
        "internal.example": ["10.0.0.5"],
        "loop.example": ["127.0.0.1"],
        "metadata.example": ["169.254.169.254"],
        # One private answer among public ones is how a checked address and a
        # connected address are made to differ.
        "mixed.example": ["93.184.216.34", "10.0.0.5"],
    }

    async def fake_resolve(host, port):
        if host not in names:
            raise OSError(f"no such name: {host}")
        return list(names[host])

    real_resolve = net._resolve
    net._resolve = fake_resolve
    net.ALLOW_PRIVATE_HOSTS = False
    seen = []

    def serve(request):
        seen.append(request)
        if request.url.path == "/hop":
            return httpx.Response(302, headers={"location": "http://internal.example/secret"})
        if request.url.path == "/relative":
            return httpx.Response(302, headers={"location": "/landed"})
        if request.url.path == "/onward":
            return httpx.Response(302, headers={"location": "https://cdn.example/arrived"})
        return httpx.Response(200, content=b"ok")

    def client(inner=None):
        return httpx.AsyncClient(
            follow_redirects=True, timeout=httpx.Timeout(10.0),
            transport=net._guarded_transport(inner or httpx.MockTransport(serve)))

    try:
        # -- a public name goes through, to the address that was checked --
        seen.clear()
        async with client() as http:
            response = await http.get("https://cdn.example/a?x=1")
        request = seen[0]
        check("a public name is fetched", response.status_code == 200 and response.content == b"ok")
        check("...at the address that was checked", request.url.host == "93.184.216.34",
              request.url.host)
        check("...with the name it was asked for as the Host header",
              request.headers["host"] == "cdn.example", request.headers.get("host"))
        check("...and as the name TLS verifies (SNI)",
              request.extensions.get("sni_hostname") == "cdn.example",
              str(request.extensions.get("sni_hostname")))
        check("...path and query untouched", request.url.path == "/a" and request.url.query == b"x=1")
        check("...and the caller's request still names the site, not the address",
              response.request.url.host == "cdn.example", response.request.url.host)

        seen.clear()
        async with client() as http:
            await http.get("http://cdn.example:8080/plain")
        request = seen[0]
        check("a plain http request keeps its port in the Host header",
              request.headers["host"] == "cdn.example:8080" and request.url.port == 8080,
              f"{request.headers.get('host')} {request.url.port}")
        check("...and asks for no SNI", "sni_hostname" not in request.extensions)

        seen.clear()
        async with client() as http:
            await http.get("https://v6.example/x")
        check("an IPv6 address is connected to as one",
              seen[0].url.host == "2606:4700:4700::1111" and "[2606:4700:4700::1111]" in str(seen[0].url),
              str(seen[0].url))

        # -- the ones that must not go through --
        for name in ("internal.example", "loop.example", "metadata.example", "mixed.example"):
            seen.clear()
            async with client() as http:
                try:
                    await http.get(f"http://{name}/x")
                    outcome = "fetched"
                except httpx.ConnectError as exc:
                    outcome = "refused"
                    check(f"{name}: the refusal says why", "not a public address" in str(exc), str(exc))
            check(f"{name} is refused, and nothing was sent", outcome == "refused" and not seen,
                  f"{outcome}, {len(seen)} sent")

        # -- and a redirect from a public name to one of them --
        seen.clear()
        async with client() as http:
            try:
                await http.get("https://cdn.example/hop")
                outcome = "followed"
            except httpx.ConnectError:
                outcome = "refused"
        check("a redirect from a public site to an internal one is refused",
              outcome == "refused", outcome)
        check("...after exactly one request: the public one", len(seen) == 1, str(len(seen)))

        seen.clear()
        async with client() as http:
            response = await http.get("https://cdn.example/relative")
        check("a relative redirect is followed, still to the site named",
              response.status_code == 200 and len(seen) == 2
              and seen[1].url.path == "/landed" and seen[1].headers["host"] == "cdn.example",
              f"{[str(r.url) for r in seen]} host {seen[-1].headers.get('host')}")

        seen.clear()
        async with client() as http:
            response = await http.get("http://cdn.example/onward")
        check("http redirected to https on the same name works",
              response.status_code == 200 and len(seen) == 2
              and seen[1].extensions.get("sni_hostname") == "cdn.example",
              str([str(r.url) for r in seen]))

        # -- the next address is tried when one does not answer --
        attempts = []

        def first_is_down(request):
            attempts.append(request.url.host)
            if request.url.host == "93.184.216.34":
                raise httpx.ConnectError("down", request=request)
            return httpx.Response(200, content=b"second")

        async with client(httpx.MockTransport(first_is_down)) as http:
            response = await http.get("https://both.example/")
        check("an address that does not answer is followed by the next",
              response.content == b"second" and attempts == ["93.184.216.34", "93.184.216.35"],
              str(attempts))

        # IPv4 goes first, and an address that has another behind it is not given
        # the whole connect timeout: the client this replaced raced them, so a
        # dead one cost a quarter of a second.
        tries = []

        def record_tries(request):
            tries.append((request.url.host, request.extensions["timeout"]["connect"]))
            raise httpx.ConnectError("down", request=request)

        async with client(httpx.MockTransport(record_tries)) as http:
            try:
                await http.get("https://dual.example/")
            except httpx.ConnectError:
                pass
        check("a host with both kinds of address is tried on IPv4 first",
              [host for host, _ in tries] == ["93.184.216.36", "2606:4700:4700::1111"], str(tries))
        check("an address with another behind it gets a short connect timeout, the last the client's own",
              [seconds for _, seconds in tries] == [net.FAILOVER_CONNECT_TIMEOUT, 10.0], str(tries))

        async with client(httpx.MockTransport(lambda r: (_ for _ in ()).throw(
                httpx.ConnectError("down", request=r)))) as http:
            try:
                await http.get("https://both.example/")
                outcome = "answered"
            except httpx.ConnectError:
                outcome = "failed"
        check("when none of them answers, the request fails as a connection error",
              outcome == "failed", outcome)

        async with client() as http:
            try:
                await http.get("https://nowhere.example/")
                outcome = "answered"
            except httpx.ConnectError as exc:
                outcome = "failed" if "could not resolve" in str(exc) else f"failed: {exc}"
        check("a name that does not resolve fails as a connection error",
              outcome == "failed", outcome)

        # -- the off switch, for a developer's own machine --
        net.ALLOW_PRIVATE_HOSTS = True
        seen.clear()
        async with client() as http:
            response = await http.get("http://internal.example/x")
        check("DBOT_ALLOW_PRIVATE_HOSTS lets a private address through",
              response.status_code == 200 and seen[0].url.host == "10.0.0.5")
        net.ALLOW_PRIVATE_HOSTS = False
    finally:
        net._resolve = real_resolve
        net.ALLOW_PRIVATE_HOSTS = False


class _Recorder(http.server.BaseHTTPRequestHandler):
    hits = []

    def do_GET(self):
        _Recorder.hits.append((self.path, self.headers.get("Host")))
        body = b"the local server answered"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


async def on_a_real_socket(net, httpx):
    print("\n=== on a real socket, to a server on this machine ===")
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Recorder)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    real_resolve = getattr(net, "_resolve", None)
    real_allow = getattr(net, "ALLOW_PRIVATE_HOSTS", False)
    try:
        net.ALLOW_PRIVATE_HOSTS = False
        _Recorder.hits.clear()
        try:
            await net.client().get(f"http://127.0.0.1:{port}/secret")
            outcome = "fetched"
        except httpx.ConnectError:
            outcome = "refused"
        check("net.client() will not fetch this machine's own address", outcome == "refused", outcome)
        check("...and the server never heard from it", not _Recorder.hits, str(_Recorder.hits))
        await net.close_client()

        # A name that resolves to this machine: with the switch off it is
        # refused, and with it on the connection goes where the name was
        # resolved to, carrying the name in Host -- the pinning, proved on a
        # real connection rather than a mock.
        async def local(host, _port):
            return ["127.0.0.1"]
        net._resolve = local
        _Recorder.hits.clear()
        try:
            await net.client().get(f"http://pinned.test:{port}/x")
            outcome = "fetched"
        except httpx.ConnectError:
            outcome = "refused"
        check("a name that resolves to this machine is refused", outcome == "refused", outcome)
        await net.close_client()

        net.ALLOW_PRIVATE_HOSTS = True
        response = await net.client().get(f"http://pinned.test:{port}/x")
        check("with the switch on it is fetched, over a real connection",
              response.status_code == 200 and response.content == b"the local server answered")
        check("...with the name it was asked for in the Host header",
              _Recorder.hits == [("/x", f"pinned.test:{port}")], str(_Recorder.hits))
    finally:
        net._resolve, net.ALLOW_PRIVATE_HOSTS = real_resolve, real_allow
        await net.close_client()
        server.shutdown()


def main() -> int:
    httpx = real_httpx()
    if httpx is None:
        print("skipped: httpx is not installed here (only the stand-in in tests/stubs is).")
        return 0
    import net
    addresses(net)
    links()
    asyncio.run(through_the_client(net, httpx))
    asyncio.run(on_a_real_socket(net, httpx))

    print()
    if failures:
        print(f"FAILURES ({len(failures)}):")
        for name in failures:
            print("  -", name)
        return 1
    print("the download path fetches the public internet, and nothing else.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
