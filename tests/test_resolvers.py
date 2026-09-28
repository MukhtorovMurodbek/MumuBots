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
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "stubs"))
MIRROR = ROOT / ".mirror" / "downloader_bot"
sys.path.insert(0, str(MIRROR if MIRROR.exists()
                      else ROOT.parent / "downloader_bot"))
sys.path.insert(1, str(ROOT.parent / "shared"))   # beside the real folder

import resolvers  # noqa: E402

failures = []


def check(name, ok):
    print(("  ok  " if ok else "  FAIL  ") + name)
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


def main() -> int:
    asyncio.run(scenario())

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
