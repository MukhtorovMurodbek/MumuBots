"""The download chain has to keep trying after a provider dies.

That is the entire promise of resolvers.py, and it is a promise about
behaviour under failure -- the state nothing exercises on a good day and
everything depends on during a bad one. Three things are checked, none of
them touching the network:

1. A provider that fails is skipped for a while and then tried again. Skipped
   permanently would mean one bad afternoon costs a route forever; never
   skipped would mean every user pays the timeout of a service that has been
   gone for a week.
2. A chain whose members are *all* in cooldown still tries all of them. This
   is the case that turns "Instagram is slow today" into "the bot is broken":
   if resting providers were filtered out rather than reordered, a bad hour
   would leave some platform with an empty chain and an instant refusal.
3. yt-dlp is last in every chain that has it. It is the only route that makes
   the request from the container's own address, which is the thing the sites
   object to, so putting it anywhere else means paying its timeout before
   trying the routes that work.

Run directly (`python tests/test_resolvers.py`) or through tests/run.py,
which mirrors the bot folders into tests/.mirror with the stubs on the path.
"""
import asyncio
import json
import logging
import os
import pathlib
import subprocess
import sys
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "stubs"))
MIRROR = ROOT / ".mirror" / "downloader_bot"
sys.path.insert(0, str(MIRROR if MIRROR.exists()
                      else ROOT.parent / "downloader_bot"))
sys.path.insert(1, str(ROOT.parent / "shared"))   # beside the real folder

import resolvers  # noqa: E402

failures = []


def check(name, ok, detail=""):
    print(("  ok  " if ok else "  FAIL  ") + name + (f" -- {detail}" if not ok and detail else ""))
    if not ok:
        failures.append(name)


def reset():
    resolvers._health.clear()
    resolvers._health_dirty.clear()


async def scenario():
    calls = []

    async def always_fails(url):
        calls.append("bad")
        raise resolvers.ProviderFailed("nope", "blocked")

    async def always_works(url):
        calls.append("good")
        return resolvers.Resolved("test", "good",
                                  [resolvers.MediaItem("video", url="http://x/y.mp4")])

    resolvers.PROVIDERS["test"] = [("bad", always_fails), ("good", always_works)]

    # 1. the chain does not stop at the first failure
    reset()
    resolved = await resolvers.resolve("test", "http://example/1")
    check("a dead provider does not end the chain", resolved.provider == "good")
    check("and the working one is what came back", calls == ["bad", "good"])

    # 2. failures accumulate into a cooldown, and the cooldown reorders
    reset()
    for _ in range(resolvers.COOLDOWN_AFTER_FAILURES):
        await resolvers.resolve("test", "http://example/2")
    order = [name for name, _ in resolvers._ordered("test")]
    check(f"after {resolvers.COOLDOWN_AFTER_FAILURES} failures it goes to the back",
          order == ["good", "bad"])
    check("and it is resting, not removed", len(order) == 2)

    # 3. a resting provider is still tried when nothing ahead of it works
    reset()
    resolvers._record_fail("bad", "x")
    resolvers._record_fail("bad", "x")
    resolvers._record_fail("bad", "x")
    resolvers._record_fail("good", "x")
    resolvers._record_fail("good", "x")
    resolvers._record_fail("good", "x")
    calls.clear()
    resolved = await resolvers.resolve("test", "http://example/3")
    check("every provider resting still means every provider tried",
          sorted(calls) == ["bad", "good"] and resolved.provider == "good")

    # 4. a success clears the streak, so recovery is immediate
    check("one success takes it off the naughty step",
          resolvers.health("good").streak == 0
          and resolvers.health("good").cooling_until == 0.0)

    # 5. nothing worked -> the most specific verdict wins
    reset()
    resolvers.PROVIDERS["test"] = [("bad", always_fails)]
    try:
        await resolvers.resolve("test", "http://example/4")
    except resolvers.NothingWorked as exc:
        check("a total failure reports the reason, not a stack trace",
              exc.kind == "blocked")
    else:
        check("a total failure reports the reason, not a stack trace", False)

    del resolvers.PROVIDERS["test"]


# ---------------------------------------------------------------------------
# Text from a stranger's server must not be able to hold the event loop
# ---------------------------------------------------------------------------
# A regex that backtracks cannot be interrupted: it holds the interpreter, and
# in the combined edition that is all five bots. So the sweep over hostile
# text runs in a child process with a deadline, and a hang is a failure rather
# than a stuck test run.

HOSTILE_CHILD = r"""
import asyncio, json, os, sys, time, types
sys.path[:0] = json.loads(os.environ["T_PATH"])
import resolvers
from resolvers import ProviderFailed

N = 2 * 1024 * 1024
inputs = {
    "all spaces": " " * N,
    "all <meta": "<meta" * (N // 5),
    "<meta then spaces": "<meta" + " " * N,
    "<meta ... one > at the end": "<meta" * (N // 5) + ">",
    "all &amp;": "&amp;" * (N // 5),
    "one long line": "a" * N,
    "<meta tags with quotes": "<meta property='og:video' content='" * (N // 36),
    "all backslash-x": "\\x" * (N // 2),
    "all /pin/": "/pin/" * (N // 5),
    "/pin/ then dashes": "/pin/" + "-" * N,
    "all instagram.com/": "instagram.com/" * (N // 14),
    "all /status/": "/status/" * (N // 8),
    "all https://": "https://" * (N // 8),
    "all tiktok urls": "https://tiktok.com" * (N // 18),
    "a token that never ends": "https://cdn.downloadgram.org/?token=" + "a" * N,
    "all /123x/": "/123x/" * (N // 6),
    "please report ...": "please report this issue on " * (N // 27),
    "tabs and newlines": "\t \n" * (N // 3),
    "all semicolons": ";" * N,
}
slow = []


def timed(label, key, fn):
    t = time.perf_counter()
    try:
        fn()
    except ProviderFailed:
        pass
    took = time.perf_counter() - t
    if took > 0.5:
        slow.append(f"{label} on {key}: {took:.2f}s")


def fake_get(text, url="https://example.test/page"):
    async def _get(u, **kwargs):
        return types.SimpleNamespace(status_code=200, text=text, url=url, json=lambda: {},
                                     raise_for_status=lambda: None)
    return _get


def run(coro):
    return asyncio.run(coro)


for key, text in inputs.items():
    timed("_meta", key, lambda: resolvers._meta(text, "og:video"))
    timed("tidy_error", key, lambda: resolvers.tidy_error(text))
    timed("format_probe", key, lambda: resolvers.format_probe({"x": [
        {"ok": False, "provider": "p", "seconds": 0.1, "detail": "blocked: " + text, "fetched": None}]}))
    timed("instagram_code", key, lambda: resolvers.instagram_code(text))
    timed("tweet_id", key, lambda: resolvers.tweet_id(text))
    timed("_PIN_ID_RE", key, lambda: resolvers._PIN_ID_RE.search(text))
    resolvers._get = fake_get(text)
    timed("_ig_downloadgram", key, lambda: run(resolvers._ig_downloadgram("https://www.instagram.com/p/abc/")))
    timed("_ig_instafix", key, lambda: run(resolvers._ig_instafix("https://www.instagram.com/p/abc/")))
    timed("_tt_tnktok (page)", key, lambda: run(resolvers._tt_tnktok("https://www.tiktok.com/@a/video/1")))
    timed("_tt_tnktok (url)", key, lambda: run(resolvers._tt_tnktok(text)))
    timed("_pin_og (page)", key, lambda: run(resolvers._pin_og("https://www.pinterest.com/pin/1/")))
    resolvers._get = fake_get("", url=text)
    timed("_pin_api (url)", key, lambda: run(resolvers._pin_api(text)))
    # An og:image address as long as a tag may be, in each of the shapes above.
    resolvers._get = fake_get('<meta property="og:image" content="' + text[:4000].replace('"', "") + '">')
    timed("_pin_og (long image address)", key, lambda: run(resolvers._pin_og("https://www.pinterest.com/pin/1/")))
print(json.dumps(slow))
"""


def hostile_tests():
    print("\n=== text from somebody else's server cannot hold the process ===")
    env = dict(os.environ, T_PATH=json.dumps(sys.path), PYTHONDONTWRITEBYTECODE="1")
    started = time.perf_counter()
    try:
        done = subprocess.run([sys.executable, "-c", HOSTILE_CHILD], env=env, capture_output=True,
                              text=True, timeout=120)
    except subprocess.TimeoutExpired:
        check("every regex-reading helper answers 2 MB of hostile text in under half a second "
              "(one of them did not finish in 120 s)", False)
        return
    slow = None
    if done.returncode == 0:
        try:
            slow = json.loads(done.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            slow = None
    check("the sweep ran (every helper, every hostile input)", slow is not None,
          (done.stderr or done.stdout)[-400:])
    check("every regex-reading helper answers 2 MB of hostile text in under half a second "
          f"(the whole sweep took {time.perf_counter() - started:.0f}s)", slow == [], str(slow))


# What a tidy error reads like: the same as before the regex was replaced.
TIDY_CASES = [
    ("", ""),
    ("plain", "plain"),
    ("ERROR: [youtube] abc: Video unavailable. This video is private; please report this issue on "
     "https://github.com/yt-dlp/yt-dlp/issues?q= , filling out the appropriate issue template. "
     "Confirm you are on the latest version using yt-dlp -U",
     "ERROR: [youtube] abc: Video unavailable. This video is private"),
    ("ERROR: Unable to download webpage: HTTP Error 429 Confirm you are on the latest version using  yt-dlp -U",
     "ERROR: Unable to download webpage: HTTP Error 429"),
    ("blocked: error; please report this issue on x", "blocked: error"),
    ("blocked: error  ;   Please Report This Issue On x", "blocked: error"),
    ("blocked: error;; please report this issue on x", "blocked: error;"),      # one ";" goes, as before
    ("blocked: error; CONFIRM YOU ARE ON THE LATEST VERSION", "blocked: error;"),
    ("a\nb \t c https://x.test/a?b=c d", "a b c <link> d"),
    ("please report this issue on x", ""),
    ("see https://github.com/yt-dlp/yt-dlp/issues?q= now", "see <link> now"),
    ("x" * 300, "x" * 149 + "…"),
    ("first Confirm you are on the latest version, then ; please report this issue on y", "first"),
    ("first ; please report this issue on y, then Confirm you are on the latest version", "first"),
]


def tidy_tests():
    print("\n=== tidy_error reads the same, and cannot be made to take minutes ===")
    for text, want in TIDY_CASES:
        got = resolvers.tidy_error(text)
        check(f"tidy_error({text[:48]!r}) -> {want[:40]!r}", got == want, repr(got))
    # Run in a child: the old pattern, when the advice does NOT follow the
    # blanks, took 15 seconds for 2,500 of them (27 s for 3,000) and never came
    # back for 200,000. (It is quick when the advice does follow them.)
    code = """
import json, os, sys, time
sys.path[:0] = json.loads(os.environ['T_PATH'])
import resolvers
out = []
for text in ('blocked: error' + ' ' * 2500 + 'x; please report this issue on y',
             ' ' * 200000 + 'x',
             ' ' * 200000 + 'x please report this issue on y',
             'a' * 1000000 + ' please report this issue on x'):
    t = time.perf_counter()
    got = resolvers.tidy_error(text)
    out.append([got[:20], len(got), time.perf_counter() - t])
print(json.dumps(out))
"""
    env = dict(os.environ, T_PATH=json.dumps(sys.path), PYTHONDONTWRITEBYTECODE="1")
    try:
        done = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=30)
        out = json.loads(done.stdout.strip().splitlines()[-1])
    except (subprocess.TimeoutExpired, ValueError, IndexError):
        check("2,500 and 200,000 blanks before a word, and a megabyte of text, are tidied at once "
              "(no answer in 30 s)", False)
        return
    check("2,500 blanks inside an error, then the advice: the text before it is what is left",
          out[0][0] == "blocked: error x" and out[0][1] == 16 and out[0][2] < 0.5, str(out[0]))
    check("200,000 blanks and a word: only the first 4,000 characters are ever read, so nothing, at once",
          out[1][1] == 0 and out[1][2] < 0.5, str(out[1]))
    check("200,000 blanks, a word and the advice: likewise nothing, at once", out[2][1] == 0 and out[2][2] < 0.5, str(out[2]))
    check("a megabyte of text: one line, at once", out[3][1] == 150 and out[3][2] < 0.5, str(out[3]))
    check("None is an empty line", resolvers.tidy_error(None) == "")


# What a page's meta tags give back: the same as before the scan replaced the regex.
META_PAGE = (
    '<html><head><META NAME="twitter:player:stream" CONTENT="https://v.test/a.mp4?x=1&amp;y=2">'
    "<meta content='https://i.test/c.jpg' property='og:image'>\n"
    '<meta property="og:video" content="https://v.test/b.mp4">'
    '<meta property="og:title" content="">'
    '<meta\nproperty="og:description"\ncontent="two\nlines">'
    "</head></html>")


def meta_tests():
    print("\n=== _meta finds a tag in a page, in one pass ===")
    m = resolvers._meta
    check("an attribute before the property, single quotes", m(META_PAGE, "og:image") == "https://i.test/c.jpg")
    check("upper case tag and attributes, &amp; undone", m(META_PAGE, "twitter:player:stream") == "https://v.test/a.mp4?x=1&y=2")
    check("an ordinary double-quoted tag", m(META_PAGE, "og:video") == "https://v.test/b.mp4")
    check("an empty content is not an answer", m(META_PAGE, "og:title") is None)
    check("a tag that runs over lines", m(META_PAGE, "og:description") == "two\nlines")
    check("a property the page does not have", m(META_PAGE, "og:audio") is None)
    check("no tags, no text", m("", "og:image") is None and m("no tags here", "og:image") is None)
    check("a tag that is never closed ends the search quietly",
          m('<meta property="og:video" content="https://v.test/a.mp4"', "og:video") is None)
    check("...but a tag before it still counts",
          m('<meta property="og:image" content="https://i.test/x.jpg"><meta property="og:video" content="u', "og:image")
          == "https://i.test/x.jpg")
    check("<meta> with nothing in it is not a tag", list(resolvers._meta_tags("<meta><META >")) == ["<META >"])
    check("a tag does not start inside another",
          list(resolvers._meta_tags("<meta a <meta b>")) == ["<meta a <meta b>"])
    padding = "x" * (resolvers._META_TAG_MAX + 10)
    long_tag = f'<meta property="og:video" content="https://v.test/{padding}">'
    check("a tag over the size no real one reaches is not read", m(long_tag, "og:video") is None)
    check("...and the tag after it still is",
          m(long_tag + '<meta property="og:video" content="https://v.test/ok">', "og:video") == "https://v.test/ok")
    just = f'<meta property="og:video" content="{"y" * 3900}">'
    check("a tag of a few KB is read", m(just, "og:video") == "y" * 3900)
    # In a child: the old pattern took 9 seconds on 80,000 of these.
    code = ("import json, os, sys, time\n"
            "sys.path[:0] = json.loads(os.environ['T_PATH'])\n"
            "import resolvers\n"
            "out = []\n"
            "for text in ('<meta' * 400000, '<meta' * 400000 + '>', '<META ' * 300000 + 'x'):\n"
            "    t = time.perf_counter()\n"
            "    got = resolvers._meta(text, 'og:video')\n"
            "    out.append([got, time.perf_counter() - t])\n"
            "print(json.dumps(out))\n")
    env = dict(os.environ, T_PATH=json.dumps(sys.path), PYTHONDONTWRITEBYTECODE="1")
    try:
        done = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=30)
        out = json.loads(done.stdout.strip().splitlines()[-1])
    except (subprocess.TimeoutExpired, ValueError, IndexError):
        check("2 MB of '<meta' (no >, one > at the end) is read at once (no answer in 30 s)", False)
        return
    for label, (got, took) in zip(("2 MB of <meta with no > anywhere", "2 MB of <meta with one > at the very end",
                                   "2 MB of <META with a space"), out):
        check(f"{label}: no answer, and quickly ({took:.2f}s)", got is None and took < 0.5, f"{got!r} {took:.2f}s")


# ---------------------------------------------------------------------------
# A delivery has a number of files, not any number
# ---------------------------------------------------------------------------

class Records(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.records = []

    def emit(self, record):
        self.records.append(record)


async def item_cap_scenario():
    cap = resolvers.MAX_ITEMS_PER_POST
    handler = Records()
    resolvers.logger.addHandler(handler)
    old_level = resolvers.logger.level
    resolvers.logger.setLevel(logging.DEBUG)

    def photos(n):
        return [resolvers.MediaItem("photo", url=f"https://secret.test/{i}.jpg") for i in range(n)]

    def provider_of(items):
        async def provider(url):
            return resolvers.Resolved("test", "many", items())
        return provider

    async def resolved_with(items):
        resolvers.PROVIDERS["test"] = [("many", provider_of(items))]
        reset()
        return await resolvers.resolve("test", "http://example/1")

    try:
        for n, want in ((1, 1), (35, 35), (cap, cap), (cap + 1, cap), (1000, cap)):
            handler.records.clear()
            got = await resolved_with(lambda n=n: photos(n))
            check(f"{n} item(s) in a delivery -> {want}", len(got.items) == want, str(len(got.items)))
            warned = [r for r in handler.records if r.levelno == logging.WARNING]
            check(f"...{'one warning' if n > cap else 'no warning'}", len(warned) == (1 if n > cap else 0), str(len(warned)))
            if n > cap:
                text = warned[0].getMessage()
                check("...which has numbers and no part of the answer in it",
                      f"{n}" in text and f"{cap}" in text and "secret" not in text and "test" not in text, text)
        got = await resolved_with(lambda: photos(cap + 30))
        check("the files kept are the first ones, in order",
              [i.url for i in got.items] == [f"https://secret.test/{i}.jpg" for i in range(cap)])

        # Files already on disk (yt-dlp writes them) for the items that are cut: gone.
        folder = tempfile.mkdtemp()
        try:
            def on_disk():
                items = []
                for i in range(cap + 5):
                    path = os.path.join(folder, f"{i}.mp4")
                    with open(path, "wb") as f:
                        f.write(b"x")
                    items.append(resolvers.MediaItem("video", path=path))
                return items
            got = await resolved_with(on_disk)
            left = sorted(os.listdir(folder), key=lambda n: int(n.split(".")[0]))
            check("an item that is cut takes its file with it; the others keep theirs",
                  len(got.items) == cap and left == [f"{i}.mp4" for i in range(cap)], str(left))
        finally:
            for name in os.listdir(folder):
                os.remove(os.path.join(folder, name))
            os.rmdir(folder)

        # The setting.
        resolvers.MAX_ITEMS_PER_POST = 3
        got = await resolved_with(lambda: photos(10))
        check("the limit is the setting's value", len(got.items) == 3)
    finally:
        resolvers.MAX_ITEMS_PER_POST = cap
        resolvers.PROVIDERS.pop("test", None)
        resolvers.logger.removeHandler(handler)
        resolvers.logger.setLevel(old_level)


def item_cap_tests():
    print("\n=== a delivery is cut to a number of files ===")
    asyncio.run(item_cap_scenario())
    code = "import json, os, sys\nsys.path[:0] = json.loads(os.environ['T_PATH'])\nimport resolvers\nprint(resolvers.MAX_ITEMS_PER_POST)\n"
    for setting, want in ((None, "40"), ("7", "7"), ("0", "1")):
        env = dict(os.environ, T_PATH=json.dumps(sys.path), PYTHONDONTWRITEBYTECODE="1")
        env.pop("DBOT_MAX_ITEMS_PER_POST", None)
        if setting is not None:
            env["DBOT_MAX_ITEMS_PER_POST"] = setting
        done = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=30)
        got = done.stdout.strip().splitlines()[-1] if done.stdout.strip() else done.stderr[-200:]
        check(f"DBOT_MAX_ITEMS_PER_POST={setting!r} -> {want} (unset is 40; never less than one)", got == want, got)
    check("a TikTok slideshow's 35 photos fit under the default", 35 <= 40)


def main() -> int:
    asyncio.run(scenario())
    tidy_tests()
    meta_tests()
    item_cap_tests()
    hostile_tests()

    for platform, chain in resolvers.PROVIDERS.items():
        names = [name for name, _ in chain]
        check(f"{platform}: has at least one route", bool(names))
        ytdlp = [n for n in names if n.startswith("ytdlp:")]
        if ytdlp:
            check(f"{platform}: yt-dlp is last, after the routes that work",
                  names[-1] == ytdlp[0] and len(ytdlp) == 1)

    # yt-dlp quotes its own issue tracker in most of its errors, and Telegram
    # expands the first URL in a message into a full-width preview card --
    # which is how a failed download once arrived as a GitHub repository with
    # a logo (v1.1.3). /providers and the probe report both carry those errors
    # verbatim, so they are stripped rather than trusted.
    noisy = ("ERROR: [TikTok] 123: Unexpected response from webpage request; "
             "please report this issue on https://github.com/yt-dlp/yt-dlp/issues?q= , "
             "filling out the appropriate issue template. Confirm you are on "
             "the latest version using yt-dlp -U")
    tidied = resolvers.tidy_error(noisy)
    check("an error report carries no link for Telegram to expand",
          "http" not in tidied)
    check("and is one line, not a paragraph",
          len(tidied) <= 150 and "\n" not in tidied)
    check("while still saying what went wrong",
          "Unexpected response" in tidied)

    print()
    if failures:
        print(f"FAILURES ({len(failures)}):")
        for f in failures:
            print("  -", f)
        return 1
    print("the chain survives its providers")
    return 0


if __name__ == "__main__":
    sys.exit(main())
