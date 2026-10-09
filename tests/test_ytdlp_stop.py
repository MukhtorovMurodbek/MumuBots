"""A download nobody is waiting for stops, and leaves nothing on the disk.

    python3 tests/test_ytdlp_stop.py

resolvers.py gives every route in a chain a time limit and cancels it when it
runs out. Cancelling a coroutine that is waiting on `asyncio.to_thread` stops
the *waiting* and nothing else -- the thread runs to its end. For a route that
looks a link up that costs nothing. For yt-dlp, whose route is the download
itself, it meant a video fetched to the last byte, in the background, for a
file nobody would collect: bandwidth spent, a file left in the work directory
for the life of the container (a download that fails part-way leaves its
`.part` and the halves of a merge, too), and a route marked as failed for
running long. And the limit was twenty seconds, for the whole of a video --
which no large download, up to 2000 MB, could ever meet.

So:
  * the provider tells its thread to stop when it is cancelled, and the thread
    stops at its next chunk and removes what it wrote;
  * whatever ends a download leaves only the file it returns;
  * a yt-dlp route is given time for the download (resolvers._timeout_for),
    and every other route keeps the short limit.

The stop is proved against a real yt-dlp and a local server that serves a video
slowly, and skipped, saying so, where yt-dlp is not installed.
"""
import asyncio
import http.server
import os
import pathlib
import sys
import tempfile
import threading
import time
import types

ROOT = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "stubs"))
MIRROR = ROOT / ".mirror" / "downloader_bot"
sys.path.insert(0, str(MIRROR if MIRROR.exists() else ROOT.parent / "downloader_bot"))
sys.path.insert(1, str(ROOT.parent / "shared"))

import resolvers  # noqa: E402

failures = []


def check(name, ok, detail=""):
    print(("  ok  " if ok else "  FAIL  ") + name + (f" -- {detail}" if not ok and detail else ""))
    if not ok:
        failures.append(name)


def have(module):
    try:
        __import__(module)
        return True
    except ImportError:
        return False


# ---------------------------------------------------------------------------
# The provider hands its thread the way to stop
# ---------------------------------------------------------------------------

async def provider_is_cancelled():
    print("=== a cancelled route stops the thread it started ===")
    seen = {}

    def download_video(url, out_dir, max_mb=None, cancel=None):
        # What video.download_video does, in miniature: work until asked to stop.
        seen["cancel"] = cancel
        seen["started"] = True
        while cancel is not None and not cancel.is_set():
            time.sleep(0.01)
        seen["stopped_at"] = time.monotonic()
        raise RuntimeError("stopped")

    fake = types.ModuleType("video")
    fake.download_video = download_video
    fake.BlockedBySource = type("BlockedBySource", (Exception,), {})
    fake.TooLarge = type("TooLarge", (Exception,), {})
    real = sys.modules.get("video")
    sys.modules["video"] = fake
    try:
        # By name: YouTube's chain starts with cobalt since 2.1.0, and the position of
        # the yt-dlp route is not what this test is about.
        provider = next(fn for name, fn in resolvers.PROVIDERS["youtube"] if name.startswith("ytdlp:"))
        started = time.monotonic()
        try:
            await asyncio.wait_for(provider("https://youtu.be/x"), timeout=0.3)
            outcome = "finished"
        except asyncio.TimeoutError:
            outcome = "timed out"
        check("the route ran out of time", outcome == "timed out", outcome)
        await asyncio.sleep(0.3)
        check("the thread was handed something to be told to stop by",
              seen.get("cancel") is not None and hasattr(seen["cancel"], "is_set"))
        check("...and it was told, when the route was cancelled",
              seen.get("cancel") is not None and seen["cancel"].is_set())
        check("...so it stopped, soon after",
              "stopped_at" in seen and seen["stopped_at"] - started < 1.5,
              f"{seen.get('stopped_at', 0) - started:.2f}s")
    finally:
        if real is None:
            sys.modules.pop("video", None)
        else:
            sys.modules["video"] = real


# ---------------------------------------------------------------------------
# What a route is given
# ---------------------------------------------------------------------------

def budgets():
    print("\n=== how long a route gets ===")
    free_mb = resolvers.net.MAX_RESPONSE_BYTES // (1024 * 1024)
    check("a lookup route keeps the short limit",
          resolvers._timeout_for("tikwm") == resolvers.PROVIDER_TIMEOUT_S
          and resolvers._timeout_for("pinterest_api") == resolvers.PROVIDER_TIMEOUT_S)
    free = resolvers._timeout_for("ytdlp:youtube")
    check("yt-dlp, which is the download, gets time for one", free >= 60 and free > resolvers.PROVIDER_TIMEOUT_S,
          f"{free:.0f}s")
    token = resolvers._LIMIT_MB.set(2000)
    try:
        large = resolvers._timeout_for("ytdlp:youtube")
    finally:
        resolvers._LIMIT_MB.reset(token)
    check("a download of up to 2000 MB gets time in proportion to what it may fetch",
          large > free and large >= (2000 - free_mb) * resolvers.YTDLP_SECONDS_PER_MB,
          f"{large:.0f}s against {free:.0f}s")
    token = resolvers._LIMIT_MB.set(free_mb)
    try:
        same = resolvers._timeout_for("ytdlp:tiktok")
    finally:
        resolvers._LIMIT_MB.reset(token)
    check("...and one at the free ceiling gets what the free one does", same == free)
    check("the limit for a large download does not shrink below the ordinary one",
          resolvers._timeout_for("ytdlp:reddit") >= resolvers.PROVIDER_TIMEOUT_S)


# ---------------------------------------------------------------------------
# Leftovers
# ---------------------------------------------------------------------------

def leftovers():
    print("\n=== what a download leaves behind ===")
    import video
    for label, folder_name in (("an ordinary folder", "work"), ("a folder with glob characters in its name", "w[or]k")):
        folder = pathlib.Path(tempfile.mkdtemp()) / folder_name
        folder.mkdir()
        mine = "a" * 32
        others = "b" * 32
        for name in (f"{mine}.mp4", f"{mine}.mp4.part", f"{mine}.f137.mp4", f"{mine}.f140.m4a",
                     f"{mine}.mp4.ytdl", f"{others}.mp4", f"{others}.mp4.part"):
            (folder / name).write_bytes(b"x")
        video._remove_leftovers(str(folder), mine, keep=str(folder / f"{mine}.mp4"))
        left = sorted(path.name for path in folder.iterdir())
        check(f"{label}: only the file it returns is kept of its own",
              left == sorted([f"{mine}.mp4", f"{others}.mp4", f"{others}.mp4.part"]), str(left))
        check(f"{label}: another download's files are not touched",
              f"{others}.mp4" in left and f"{others}.mp4.part" in left)
        video._remove_leftovers(str(folder), mine)
        check(f"{label}: with nothing to keep, nothing of its own is left",
              not [name for name in os.listdir(folder) if name.startswith(mine)])


# ---------------------------------------------------------------------------
# The real thing: yt-dlp, and a video that arrives slowly
# ---------------------------------------------------------------------------

class _SlowVideo(http.server.BaseHTTPRequestHandler):
    total = 30 * 1024 * 1024
    sent = 0

    def _headers(self):
        self.send_response(200)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Content-Length", str(self.total))
        self.send_header("Accept-Ranges", "none")
        self.end_headers()

    def do_HEAD(self):
        self._headers()

    def do_GET(self):
        self._headers()
        try:
            while _SlowVideo.sent < self.total:
                self.wfile.write(b"\0" * 65536)
                self.wfile.flush()
                _SlowVideo.sent += 65536
                time.sleep(0.05)
        except OSError:
            pass

    def log_message(self, *args):
        pass


def a_real_download_is_stopped():
    print("\n=== a real download, stopped part-way ===")
    if not have("yt_dlp"):
        print("  skipped  yt-dlp is not installed here.")
        return
    import video
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _SlowVideo)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    out = tempfile.mkdtemp()
    stop = threading.Event()
    result = {}

    def work():
        try:
            result["path"] = video.download_video(
                f"http://127.0.0.1:{server.server_address[1]}/clip.mp4", out, 48, stop)
        except BaseException as exc:                    # noqa: BLE001
            result["error"] = f"{type(exc).__name__}"

    thread = threading.Thread(target=work)
    thread.start()
    try:
        deadline = time.monotonic() + 15
        while not os.listdir(out) and time.monotonic() < deadline:
            time.sleep(0.05)
        time.sleep(0.5)
        check("the download is under way: a partial file is on the disk", bool(os.listdir(out)), str(os.listdir(out)))
        stopped_at = time.monotonic()
        stop.set()
        thread.join(timeout=30)
        took = time.monotonic() - stopped_at
        check("told to stop, it stops within a few seconds, not at the end of the video",
              not thread.is_alive() and took < 5, f"{took:.1f}s, alive={thread.is_alive()}")
        check("...having fetched a fraction of it", _SlowVideo.sent < _SlowVideo.total // 2,
              f"{_SlowVideo.sent / 1e6:.1f} of {_SlowVideo.total / 1e6:.0f} MB")
        check("...by being cancelled, not by succeeding", result.get("error") == "DownloadCancelled", str(result))
        check("...and nothing of it is left in the work directory", os.listdir(out) == [], str(os.listdir(out)))
    finally:
        stop.set()
        server.shutdown()


def main() -> int:
    asyncio.run(provider_is_cancelled())
    budgets()
    leftovers()
    a_real_download_is_stopped()

    print()
    if failures:
        print(f"FAILURES ({len(failures)}):")
        for name in failures:
            print("  -", name)
        return 1
    print("a download nobody waits for is stopped and cleaned up.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
