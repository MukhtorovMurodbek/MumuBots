"""The daily route check, the yt-dlp swap, the page cap and the cobalt route.

    python3 tests/test_routewatch.py

Four pieces of DownloaderBot's reliability work (2.1.0, from the owner's
"do everything that does not need me" of 2026-10-07), each checked by sending
the thing that hurts:

  1. routewatch.py -- when the owner is told. A platform going down is news,
     the same platform being down tomorrow is not, a recovery is recorded
     without a message, YouTube is never pushed, and a deleted sample link is
     not mistaken for an outage.
  2. yt-dlp can be replaced while the bot runs -- but never under a download
     that is using it, and a copy that does not import as the release it was
     asked for is refused.
  3. resolvers._get() reads a small document: an answer past the cap is
     refused instead of being held in memory, and the body the caller gets is
     still decoded, with its final URL.
  4. The cobalt route for YouTube: another instance after a blocked one, the
     one that worked goes first next time, a too-long clip ends the search,
     and nothing is claimed that every instance did not say.

Runs on the real httpx (MockTransport -- no socket is opened) and says so
where httpx is not installed.
"""
import asyncio
import gzip
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone

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
import resolvers  # noqa: E402
import routewatch  # noqa: E402
import video  # noqa: E402

DAY = 86400.0


# ---------------------------------------------------------------------------
# 1. When the owner is told
# ---------------------------------------------------------------------------

def rule_tests():
    print("=== which state a platform is in ===")
    ok = lambda name: {"provider": name, "ok": True, "detail": "1 item(s)", "fetched": "64 bytes"}   # noqa: E731
    bad = lambda name, why="blocked: nope": {"provider": name, "ok": False, "detail": why, "fetched": None}   # noqa: E731
    check("the first route working is up", routewatch.classify([ok("a")], 3) == routewatch.UP)
    check("a later route working, with the last one not needed, is still up",
          routewatch.classify([bad("a"), ok("b")], 3) == routewatch.UP)
    check("only the last route working is last_resort",
          routewatch.classify([bad("a"), bad("b"), ok("c")], 3) == routewatch.LAST_RESORT)
    check("a chain of one that works is up, not last_resort",
          routewatch.classify([ok("only")], 1) == routewatch.UP)
    check("nothing working is down",
          routewatch.classify([bad("a"), bad("b")], 2) == routewatch.DOWN)
    check("every route saying 'missing' is a deleted sample, not an outage",
          routewatch.classify([bad("a", "missing: gone"), bad("b", "missing: private")], 2) == routewatch.SAMPLE_GONE)
    check("one 'missing' among real failures is still down",
          routewatch.classify([bad("a", "missing: gone"), bad("b")], 2) == routewatch.DOWN)

    print("\n=== when the owner hears about it ===")
    now = 1_000_000.0
    rec, alert = routewatch.transition(None, routewatch.UP, now)
    check("first sight of a healthy platform says nothing", alert is None)
    rec, alert = routewatch.transition(None, routewatch.DOWN, now)
    check("first sight of a dead one is an error", alert == ("error", "down"))
    rec2, alert = routewatch.transition(rec, routewatch.DOWN, now + DAY)
    check("the next day it is not announced again", alert is None)
    rec3, alert = routewatch.transition(rec2, routewatch.DOWN, now + 3.1 * DAY)
    check("but after three days of it, it is", alert == ("error", "still_down"))
    _, alert = routewatch.transition(rec3, routewatch.DOWN, now + 4 * DAY)
    check("and that restarts the clock", alert is None)
    _, alert = routewatch.transition(rec, routewatch.UP, now + DAY)
    check("a recovery is recorded as info, which ManagerBot does not push", alert == ("info", "recovered"))
    rec, alert = routewatch.transition(None, routewatch.LAST_RESORT, now)
    check("being down to the last route is a warning", alert == ("warning", "last_resort"))
    _, alert = routewatch.transition(rec, routewatch.LAST_RESORT, now + DAY)
    check("and only once", alert is None)
    _, alert = routewatch.transition(rec, routewatch.DOWN, now + DAY)
    check("last_resort -> down is an error", alert == ("error", "down"))
    rec_up, _ = routewatch.transition(None, routewatch.UP, now)
    _, alert = routewatch.transition(rec_up, routewatch.UP, now + DAY)
    check("up -> up says nothing", alert is None)
    _, alert = routewatch.transition("garbage", routewatch.DOWN, now)
    check("an unreadable record is treated as first sight", alert == ("error", "down"))

    print("\n=== yt-dlp versions ===")
    vk = routewatch.version_key
    check("2026.08.19 and 2026.8.19 are the same release", vk("2026.08.19") == vk("2026.8.19"))
    check("a '.1' tail is a later release the same day", vk("2026.08.19.1") > vk("2026.08.19"))
    check("not a version is None", vk("nightly") is None and vk(None) is None)
    when = datetime(2026, 9, 29, tzinfo=timezone.utc)
    now_dt = when + timedelta(days=10)
    v = routewatch.ytdlp_verdict("2026.8.19", "2026.09.29", when, now_dt)
    check("41 days behind is behind and stale", v["behind"] and v["stale"] and v["gap_days"] == 41)
    check("a 10-day-old release has cooled", v["cooled"])
    v = routewatch.ytdlp_verdict("2026.8.19", "2026.09.29", when, when + timedelta(days=1))
    check("a day-old release has not (the window in which a bad release is noticed)", not v["cooled"])
    v = routewatch.ytdlp_verdict("2026.9.20", "2026.09.29", when, now_dt)
    check("nine days behind is behind but not stale", v["behind"] and not v["stale"])
    v = routewatch.ytdlp_verdict("2026.09.29", "2026.09.29", when, now_dt)
    check("the same release is not behind", not v["behind"])
    v = routewatch.ytdlp_verdict(None, "2026.09.29", when, now_dt)
    check("an installed version nobody could read is not reported as behind", not v["behind"])


# ---------------------------------------------------------------------------
# 1b. A whole run, with fake routes
# ---------------------------------------------------------------------------

def make_provider(platform, name, behaviour):
    """behaviour: 'ok', 'blocked', 'missing', 'error', 'dead_media'"""
    async def provider(url):
        if behaviour in ("blocked", "missing", "error"):
            raise resolvers.ProviderFailed(f"{name} says {behaviour}", behaviour)
        fd, path = tempfile.mkstemp(suffix=".mp4")
        os.write(fd, b"x" * 100)
        os.close(fd)
        if behaviour == "dead_media":
            os.remove(path)               # resolves, but there is nothing to serve
        return resolvers.Resolved(platform, name, [resolvers.MediaItem("video", path=path)])
    return provider


async def run_tests():
    print("\n=== a whole run ===")
    events = []
    real_event, real_samples, real_providers = family_link.report_event_soon, dict(resolvers.SAMPLES), dict(resolvers.PROVIDERS)
    family_link.report_event_soon = lambda level, kind, message, details=None: events.append((level, kind, message, details))
    family_link._local_settings.clear()

    async def no_ytdlp():
        return None
    routewatch.check_ytdlp = no_ytdlp
    try:
        resolvers.SAMPLES.clear()
        resolvers.SAMPLES.update({"instagram": "http://x/ig", "tiktok": "http://x/tt", "youtube": "http://x/yt"})
        resolvers.PROVIDERS.clear()
        resolvers.PROVIDERS.update({
            "instagram": [("ig1", make_provider("instagram", "ig1", "blocked")),
                          ("ig2", make_provider("instagram", "ig2", "error")),
                          ("ytdlp:instagram", make_provider("instagram", "ytdlp:instagram", "ok"))],
            "tiktok": [("tt1", make_provider("tiktok", "tt1", "ok")),
                       ("tt2", make_provider("tiktok", "tt2", "ok"))],
            "youtube": [("cobalt:youtube", make_provider("youtube", "cobalt", "blocked")),
                        ("ytdlp:youtube", make_provider("youtube", "ytdlp", "blocked"))],
        })
        report = await routewatch.run(force=True)
        check("Instagram, down to yt-dlp alone, is a warning", any(
            e[0] == "warning" and e[1] == "routes" and "Instagram" in e[2] for e in events), str(events))
        check("TikTok, healthy, says nothing", not any("Tiktok" in e[2] for e in events))
        check("YouTube, dead, is NOT pushed (it is unadvertised; refusing a datacenter is its normal state)",
              not any("Youtube" in e[2] for e in events))
        check("but the report still says what YouTube is", "youtube: down" in report)
        check("the chain stopped at the first route that worked (TikTok's second was never asked)",
              "tt2" not in report)
        check("the details name each failing route", any("ig1" in (e[3] or "") for e in events))
        count = len(events)

        await routewatch.run(force=True)
        check("the same trouble the next day is not announced again", len(events) == count, str(events[count:]))

        skipped = await routewatch.run()
        check("a run without force, soon after one, does nothing", skipped.startswith("skipped"))

        # Instagram's first route recovers: back to up, recorded, no message
        resolvers.PROVIDERS["instagram"][0] = ("ig1", make_provider("instagram", "ig1", "ok"))
        await routewatch.run(force=True)
        check("a recovery sends nothing", len(events) == count or all(e[0] == "info" for e in events[count:]))

        # Instagram's every route fails
        resolvers.PROVIDERS["instagram"] = [("ig1", make_provider("instagram", "ig1", "blocked")),
                                            ("ig2", make_provider("instagram", "ig2", "error"))]
        before = len(events)
        await routewatch.run(force=True)
        check("a platform with nothing left is an error", any(
            e[0] == "error" and "Instagram" in e[2] for e in events[before:]), str(events[before:]))

        # a route that resolves to media that will not serve is a failure
        resolvers.PROVIDERS["tiktok"] = [("tt1", make_provider("tiktok", "tt1", "dead_media"))]
        rows = await routewatch.check_platform("tiktok", "http://x/tt")
        check("a route whose media does not serve counts as failed", rows and not rows[0]["ok"], str(rows))

        # a deleted sample
        resolvers.PROVIDERS["pinterest"] = [("p1", make_provider("pinterest", "p1", "missing")),
                                            ("p2", make_provider("pinterest", "p2", "missing"))]
        resolvers.SAMPLES["pinterest"] = "http://x/pin"
        before = len(events)
        await routewatch.run(force=True)
        check("a sample every route calls missing is reported as the sample, not as an outage",
              any("sample" in e[2] for e in events[before:]) and not any(e[0] == "error" and "Pinterest" in e[2] for e in events[before:]),
              str(events[before:]))
    finally:
        family_link.report_event_soon = real_event
        resolvers.SAMPLES.clear(); resolvers.SAMPLES.update(real_samples)
        resolvers.PROVIDERS.clear(); resolvers.PROVIDERS.update(real_providers)
        family_link._local_settings.clear()


# ---------------------------------------------------------------------------
# 2. Replacing yt-dlp
# ---------------------------------------------------------------------------

def fake_ytdlp(folder: pathlib.Path, version: str):
    pkg = folder / "yt_dlp"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "version.py").write_text(f"__version__ = '{version}'\n")


async def swap_tests():
    print("\n=== replacing yt-dlp while the bot runs ===")
    saved_path, saved_mods = list(sys.path), {k: v for k, v in sys.modules.items() if k.split(".")[0] == "yt_dlp"}
    with tempfile.TemporaryDirectory() as tmp:
        tmp = pathlib.Path(tmp)
        try:
            os.environ["DBOT_YTDLP_DIR"] = str(tmp)
            fake_ytdlp(tmp / "yt-dlp-2099.01.01", "2099.01.01")
            done, what = await routewatch.update_ytdlp("2099.01.01")
            check("a copy that imports as the release asked for is taken", done and what == "2099.01.01", what)
            import yt_dlp.version as v
            check("and the next import is that copy", v.__version__ == "2099.01.01", v.__version__)
            check("video.ytdlp_version() agrees", video.ytdlp_version() == "2099.01.01")

            fake_ytdlp(tmp / "yt-dlp-2099.01.02", "2099.01.01")      # says 01.01 inside a 01.02 folder
            done, what = await routewatch.update_ytdlp("2099.01.02")
            check("a copy that is not the release it was asked for is refused", not done, what)
            check("and nothing changed", video.ytdlp_version() == "2099.01.01")

            (tmp / "yt-dlp-2099.01.03").mkdir()
            (tmp / "yt-dlp-2099.01.03" / "yt_dlp").mkdir()
            (tmp / "yt-dlp-2099.01.03" / "yt_dlp" / "version.py").write_text("__version__ = '2099.01.03'\n")
            (tmp / "yt-dlp-2099.01.03" / "yt_dlp" / "__init__.py").write_text("raise RuntimeError('broken')\n")
            done, what = await routewatch.update_ytdlp("2099.01.03")
            check("a copy that does not even import is refused, in a child process", not done and "did not import" in what, what)
            check("and this process is untouched", video.ytdlp_version() == "2099.01.01")
        finally:
            os.environ.pop("DBOT_YTDLP_DIR", None)
            sys.path[:] = saved_path
            for k in [k for k in sys.modules if k.split(".")[0] == "yt_dlp"]:
                del sys.modules[k]
            sys.modules.update(saved_mods)

    print("\n=== never under a download that is using it ===")
    saved_path = list(sys.path)
    release = threading.Event()
    held = threading.Event()

    def download():
        with video._slot():
            held.set()
            release.wait(5)

    t = threading.Thread(target=download)
    t.start()
    held.wait(2)
    video.SWAP_WAIT_S = 0.3
    check("a swap waits, gives up, and changes nothing while a download is running",
          video.use_ytdlp_from("/nowhere/at/all") is False and "/nowhere/at/all" not in sys.path)
    release.set()
    t.join()
    video.SWAP_WAIT_S = 300.0
    check("once it has finished the swap goes through", video.use_ytdlp_from("/nowhere/at/all") is True
          and sys.path[0] == "/nowhere/at/all")
    sys.path[:] = saved_path
    got = []
    started = threading.Event()

    def swapper():
        started.set()
        got.append(video.use_ytdlp_from("/elsewhere"))

    release2 = threading.Event()
    held2 = threading.Event()

    def long_download():
        with video._slot():
            held2.set()
            release2.wait(5)

    t = threading.Thread(target=long_download)
    t.start()
    held2.wait(2)
    s = threading.Thread(target=swapper)
    s.start()
    started.wait(2)
    time.sleep(0.2)
    check("a swap that is waiting has not happened yet", got == [] and "/elsewhere" not in sys.path)
    release2.set()
    t.join()
    s.join(5)
    check("and happens the moment the download is done", got == [True])
    sys.path[:] = saved_path


# ---------------------------------------------------------------------------
# 3. The page cap
# ---------------------------------------------------------------------------

async def page_tests(httpx):
    import net
    print("\n=== a page may only be so big ===")

    sent = {"chunks": 0}
    chunk = b"x" * 65536

    def endless():
        async def gen():
            for _ in range(resolvers.PAGE_LIMIT_BYTES // len(chunk) + 40):
                sent["chunks"] += 1
                yield chunk
        return gen()

    def handler(request):
        path = request.url.path
        if path == "/small":
            return httpx.Response(200, json={"ok": True})
        if path == "/big-declared":
            # Says how big it is, and would send that much if asked.
            return httpx.Response(200, content=endless(),
                                  headers={"content-length": str(resolvers.PAGE_LIMIT_BYTES + 10)})
        if path == "/big-undeclared":
            # Never says: chunked, and goes on well past the cap.
            return httpx.Response(200, content=endless())
        if path == "/gz":
            return httpx.Response(200, content=gzip.compress(json.dumps({"zipped": 1}).encode()),
                                  headers={"content-encoding": "gzip", "content-type": "application/json"})
        if path == "/a":
            return httpx.Response(302, headers={"location": "https://example.test/b"})
        if path == "/b":
            return httpx.Response(200, text="there")
        if path == "/404":
            return httpx.Response(404, text="nope")
        return httpx.Response(500)

    net._client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)
    try:
        r = await resolvers._get("https://example.test/small")
        check("an ordinary answer comes back whole", r.json() == {"ok": True} and r.status_code == 200)
        sent["chunks"] = 0
        try:
            await resolvers._get("https://example.test/big-declared")
            check("an answer that says it is too big is refused", False)
        except resolvers.ProviderFailed as exc:
            check("an answer that says it is too big is refused, as a provider failure the chain moves past",
                  exc.kind == "error")
        check("and refused before a single byte of it was read", sent["chunks"] == 0, str(sent))
        sent["chunks"] = 0
        try:
            await resolvers._get("https://example.test/big-undeclared")
            check("an answer that never says how big it is is cut off", False)
        except resolvers.ProviderFailed as exc:
            check("an answer that never says how big it is is cut off", exc.kind == "error")
        total = resolvers.PAGE_LIMIT_BYTES // 65536
        check("at the cap, not after the whole stream", total <= sent["chunks"] <= total + 2, str(sent))
        r = await resolvers._get("https://example.test/gz")
        check("a gzipped answer arrives decoded", r.json() == {"zipped": 1})
        r = await resolvers._get("https://example.test/a")
        check("a redirect is followed and the final address is kept", r.text == "there" and r.url.path == "/b", str(r.url))
        r = await resolvers._get("https://example.test/404")
        check("a status is the caller's to read, as before", r.status_code == 404)
    finally:
        await net._client.aclose()
        net._client = None


# ---------------------------------------------------------------------------
# 4. cobalt
# ---------------------------------------------------------------------------

async def cobalt_tests(httpx):
    import net
    print("\n=== the cobalt route ===")
    seen = []

    def handler(request):
        host = request.url.host
        seen.append(host)
        body = json.loads(request.content or b"{}")
        if host == "blocked.test":
            return httpx.Response(400, json={"status": "error", "error": {"code": "error.api.youtube.login"}})
        if host == "html.test":
            return httpx.Response(200, text="<html>challenge</html>")
        if host == "long.test":
            return httpx.Response(400, json={"status": "error", "error": {"code": "error.api.content.too_long"}})
        if host == "gone.test":
            return httpx.Response(400, json={"status": "error", "error": {"code": "error.api.content.video.unavailable"}})
        if host == "good.test":
            assert body.get("videoQuality") == resolvers.COBALT_QUALITY
            return httpx.Response(200, json={"status": "tunnel", "url": "https://good.test/tunnel/abc",
                                             "filename": "clip (720p, h264).mp4"})
        if host == "picker.test":
            return httpx.Response(200, json={"status": "picker", "picker": [
                {"type": "photo", "url": "https://p.test/1.jpg"}, {"type": "video", "url": "https://p.test/2.mp4"}]})
        return httpx.Response(500)

    net._client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)
    saved = list(resolvers.COBALT_INSTANCES)
    try:
        provider = resolvers._cobalt_provider("youtube")
        resolvers.COBALT_INSTANCES[:] = ["https://blocked.test", "https://html.test", "https://good.test"]
        resolvers._cobalt_last_good = None
        r = await provider("https://youtu.be/x")
        check("a blocked instance and a challenge page are skipped; the next one answers",
              r.provider == "cobalt" and r.items[0].url == "https://good.test/tunnel/abc", str(r))
        check("the item is a video with a sane name", r.items[0].kind == "video" and r.items[0].filename == "youtube.mp4")
        seen.clear()
        await provider("https://youtu.be/x")
        check("the instance that worked is tried first next time", seen[0] == "good.test", str(seen))

        resolvers._cobalt_last_good = None
        resolvers.COBALT_INSTANCES[:] = ["https://long.test", "https://good.test"]
        seen.clear()
        try:
            await provider("https://youtu.be/x")
            check("a clip that is too long ends the search", False)
        except resolvers.ProviderFailed as exc:
            check("a clip that is too long ends the search", exc.kind == "too_big" and seen == ["long.test"], f"{exc.kind} {seen}")

        resolvers.COBALT_INSTANCES[:] = ["https://gone.test", "https://good.test"]
        r = await provider("https://youtu.be/x")
        check("'unavailable' from one instance is not trusted over another that has it", r.provider == "cobalt")

        resolvers.COBALT_INSTANCES[:] = ["https://blocked.test", "https://gone.test"]
        try:
            await provider("https://youtu.be/x")
            check("every instance refusing is a failure", False)
        except resolvers.ProviderFailed as exc:
            check("a mix of blocked and unavailable is reported as blocked, not as a missing video", exc.kind == "blocked", exc.kind)
        resolvers.COBALT_INSTANCES[:] = ["https://gone.test"]
        try:
            await provider("https://youtu.be/x")
        except resolvers.ProviderFailed as exc:
            check("all of them saying unavailable is reported as missing", exc.kind == "missing", exc.kind)
        resolvers.COBALT_INSTANCES[:] = []
        try:
            await provider("https://youtu.be/x")
        except resolvers.ProviderFailed as exc:
            check("no instances configured is a plain failure, not a crash", exc.kind == "error")

        resolvers.COBALT_INSTANCES[:] = ["https://picker.test"]
        r = await provider("https://youtu.be/x")
        check("a picker keeps only the video for YouTube", [i.kind for i in r.items] == ["video"])
    finally:
        resolvers.COBALT_INSTANCES[:] = saved
        resolvers._cobalt_last_good = None
        await net._client.aclose()
        net._client = None

    check("YouTube's chain is cobalt first, yt-dlp last",
          [n for n, _ in resolvers.PROVIDERS["youtube"]] == ["cobalt:youtube", "ytdlp:youtube"])
    check("yt-dlp is still last in every chain",
          all(chain[-1][0].startswith("ytdlp:") for chain in resolvers.PROVIDERS.values()))
    check("YouTube has a sample link, so the daily check can ask about it", "youtube" in resolvers.SAMPLES)


# ---------------------------------------------------------------------------
# cookies from a variable
# ---------------------------------------------------------------------------

def cookie_tests():
    print("\n=== cookies pasted into a variable ===")
    saved = {k: os.environ.get(k) for k in ("DBOT_YT_COOKIES", "DBOT_COOKIES")}
    video._cookie_text_files.clear()
    try:
        os.environ.pop("DBOT_COOKIES", None)
        os.environ["DBOT_YT_COOKIES"] = ".youtube.com\\tTRUE\\t/\\tTRUE\\t0\\tSID\\tabc\\n.youtube.com\\tTRUE\\t/\\tTRUE\\t0\\tHSID\\tdef"
        path = video.cookiefile_for("youtube")
        text = pathlib.Path(path).read_text() if path else ""
        check("pasted text becomes a file", bool(path) and "SID" in text)
        # Three lines: the two cookies, under the header the paste did not have.
        check("a host that flattened the lines into literal \\n gets them back", text.count("\n") == 3 and "\\n" not in text
              and text.startswith("# Netscape HTTP Cookie File\n"), repr(text))
        check("it is only readable by this process", (os.stat(path).st_mode & 0o077) == 0 or os.name == "nt")
        check("and the same file is reused", video.cookiefile_for("youtube") == path)
        check("another site does not get YouTube's cookies", video.cookiefile_for("instagram") is None)
        os.environ.pop("DBOT_YT_COOKIES")
        video._cookie_text_files.clear()
        check("with nothing set there is no cookie file", video.cookiefile_for("youtube") is None)
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        video._cookie_text_files.clear()


def review_tests():
    """Found by the review of 2026-10-07."""
    print("\n== switches, and yt-dlp's time in the probe ==")
    cases = [("disabled", "0", False), ("n", "0", False), ("none", "0", False), ("yes", "0", True),
             ("ON", "0", True), ("1", "0", True), ("garbage", "1", True), ("0", "1", False), ("", "1", False)]
    for value, default, want in cases:
        os.environ["RW_TEST_FLAG"] = value
        got = routewatch._flag("RW_TEST_FLAG", default)
        check(f"{value!r} with default {default!r} reads as {want}", got is want, got)
    os.environ.pop("RW_TEST_FLAG", None)

    seen = {}
    real_probe, real_providers = resolvers.probe_route, dict(resolvers.PROVIDERS)

    async def probe(name, fn, url, fetch=True, timeout=None):
        seen[name] = timeout
        return {"route": name, "ok": False, "detail": "blocked", "fetched": ""}
    resolvers.probe_route = probe
    resolvers.PROVIDERS["tiktok"] = [("tikwm", None), ("ytdlp:tiktok", None)]
    try:
        asyncio.run(routewatch.check_platform("tiktok", "http://x/tt"))
    finally:
        resolvers.probe_route = real_probe
        resolvers.PROVIDERS.clear(); resolvers.PROVIDERS.update(real_providers)
    check("an ordinary route keeps the probe's short timeout", seen.get("tikwm") == routewatch.PROBE_TIMEOUT_S, seen)
    check("yt-dlp gets the time it gets in real use, not 12 s",
          seen.get("ytdlp:tiktok", 0) >= resolvers.YTDLP_TIMEOUT_S > routewatch.PROBE_TIMEOUT_S, seen)


# ---------------------------------------------------------------------------
# A hostile cobalt instance's error code on its way into the report
# ---------------------------------------------------------------------------

HOSTILE_CODE_CHILD = r"""
import asyncio, json, os, sys, time
sys.path[:0] = json.loads(os.environ["T_PATH"])
import family_link
import httpx, net, resolvers, routewatch

code = "error.api.x" + " " * 2500 + "x;please report this issue on y"
def handler(request):
    return httpx.Response(400, json={"status": "error", "error": {"code": code}})
net._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
resolvers.COBALT_INSTANCES[:] = ["https://hostile.test"]
provider = resolvers._cobalt_provider("youtube")

async def go():
    started = time.perf_counter()
    row = await resolvers.probe_route("cobalt:youtube", provider, "https://youtu.be/x", timeout=5)
    lines = routewatch._lines([row])
    text = resolvers.format_probe({"youtube": [row]})
    return {"seconds": time.perf_counter() - started, "lines": lines, "text": text, "detail_len": len(row["detail"])}

print(json.dumps(asyncio.run(go())))
"""


def hostile_code_tests():
    print("\n=== an error code made to be slow to tidy ===")
    env = dict(os.environ, T_PATH=json.dumps(sys.path), PYTHONDONTWRITEBYTECODE="1")
    try:
        done = subprocess.run([sys.executable, "-c", HOSTILE_CODE_CHILD], env=env, capture_output=True,
                              text=True, timeout=30)
        out = json.loads(done.stdout.strip().splitlines()[-1])
    except (subprocess.TimeoutExpired, ValueError, IndexError):
        check("a cobalt error code of 2,500 blanks, a word and the advice goes through the probe and the report at once "
              "(it did not come back in 30 s)", False, (locals().get("done") and done.stderr[-300:]) or "")
        return
    check("the code reached the probe row whole (it is the report that has to cope)", out["detail_len"] > 2500, str(out["detail_len"]))
    check(f"...and the report lines are made in {out['seconds']:.2f}s", out["seconds"] < 0.5, str(out["seconds"]))
    check("...as one short line each, with the advice cut off",
          len(out["lines"]) == 1 and out["lines"][0].startswith("FAIL cobalt:youtube: ") and "please report" not in out["lines"][0]
          and "please report" not in out["text"] and len(out["lines"][0]) < 200, str(out["lines"]))


async def main():
    hostile_code_tests()
    rule_tests()
    await asyncio.to_thread(review_tests)
    await run_tests()
    await swap_tests()
    httpx = real_httpx()
    if httpx is None:
        print("\n(the real httpx is not installed: the page cap and cobalt checks are skipped)")
    else:
        await page_tests(httpx)
        await cobalt_tests(httpx)
    cookie_tests()


if __name__ == "__main__":
    asyncio.run(main())
    print()
    if failures:
        print(f"FAILURES: {len(failures)}")
        for f in failures:
            print("  -", f)
        sys.exit(1)
    print("All routewatch checks passed.")
