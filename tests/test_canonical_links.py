"""The same post is the same link, whoever shared it and from wherever.

    python3 tests/test_canonical_links.py

Every share sheet adds its own trackers (`?igsh=`, `?si=`, `?utm_source=`, ...), so
one post used to arrive as a different string for every person, and everything the
bot keys on the string -- the five-minute delivery cache, the redo memory, the
"same link again" check, the file-ID store -- saw many posts where there was one.
`platforms.canonical_link` writes a link the one way that identifies the post, and
`detect_platform` (the single place a link enters the bot) applies it.

Pure: no network, no database.
"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "stubs"))
MIRROR = ROOT / ".mirror" / "downloader_bot"
sys.path.insert(0, str(MIRROR if MIRROR.exists()
                       else ROOT.parent / "downloader_bot"))
sys.path.insert(1, str(ROOT.parent / "shared"))   # beside the real folder

import platforms   # noqa: E402

failures = []
total = 0


def check(name, ok, detail=""):
    global total
    total += 1
    print(("  ok  " if ok else "  FAIL  ") + name + (f" -- {detail}" if not ok and detail else ""))
    if not ok:
        failures.append(name)


seen_inputs: list = []


def canon(platform, url):
    seen_inputs.append((platform, url))
    return platforms.canonical_link(platform, url)


def same(platform, url, expected):
    label = ""
    if ": " in platform:                       # "platform: what this case shows"
        platform, label = platform.split(": ", 1)
        label += " -- "
    got = canon(platform, url)
    check(f"{platform}: {label}{url[:70]}", got == expected, f"got {got!r}, wanted {expected!r}")


def main() -> int:
    print("\n=== Instagram ===")
    IG = "https://www.instagram.com/reel/C8aBcDeFgHi/"
    for url in (
        "https://www.instagram.com/reel/C8aBcDeFgHi/?igsh=MWN4ZHJ1cGZzcTA3Mg==",
        "https://www.instagram.com/reel/C8aBcDeFgHi/?igshid=NTc4MTIwNjQ2YQ%3D%3D",
        "https://www.instagram.com/reel/C8aBcDeFgHi/?utm_source=ig_web_copy_link&igsh=abc",
        "https://www.instagram.com/reel/C8aBcDeFgHi?utm_source=ig_web_copy_link",
        "https://instagram.com/reel/C8aBcDeFgHi/",
        "http://m.instagram.com/reel/C8aBcDeFgHi/",
        "HTTPS://WWW.INSTAGRAM.COM/reel/C8aBcDeFgHi/",
        "https://www.instagram.com/reel/C8aBcDeFgHi/#fragment",
        "https://www.instagram.com/reels/C8aBcDeFgHi/",
        "https://www.instagram.com/some.user_1/reel/C8aBcDeFgHi/?igsh=x",
        "https://www.instagram.com/some.user_1/reels/C8aBcDeFgHi/",
        "https://www.instagram.com/reel/C8aBcDeFgHi/embed/",
    ):
        same("instagram", url, IG)
    same("instagram", "https://www.instagram.com/p/CODE_a-1/?img_index=2&igsh=zz", "https://www.instagram.com/p/CODE_a-1/")
    same("instagram", "https://www.instagram.com/someone/p/CODE_a-1/?img_index=2", "https://www.instagram.com/p/CODE_a-1/")
    same("instagram", "https://www.instagram.com/p/CODE_a-1/c/179/?hl=en", "https://www.instagram.com/p/CODE_a-1/")
    same("instagram", "https://www.instagram.com/tv/XyZ123/?igsh=1", "https://www.instagram.com/tv/XyZ123/")
    same("instagram", "https://www.instagram.com/someone/tv/XyZ123/", "https://www.instagram.com/tv/XyZ123/")
    same("instagram", "https://www.instagram.com/reel/reel/C8aBcDeFgHi/", "https://www.instagram.com/reel/C8aBcDeFgHi/")
    same("instagram: a case-sensitive code keeps its case", "https://www.instagram.com/p/AbCdEf/", "https://www.instagram.com/p/AbCdEf/")
    # Everything else keeps its path, loses its query and fragment, and goes to www.
    same("instagram", "https://instagram.com/stories/someone/3412345678901234567/?igsh=x",
         "https://www.instagram.com/stories/someone/3412345678901234567/")
    same("instagram", "https://www.instagram.com/share/reel/BAbCdEf/?igsh=x#y", "https://www.instagram.com/share/reel/BAbCdEf/")
    same("instagram", "https://www.instagram.com/share/BAbCdEf?igsh=x", "https://www.instagram.com/share/BAbCdEf")
    same("instagram", "https://m.instagram.com/someone/?hl=en", "https://www.instagram.com/someone/")
    same("instagram", "https://www.instagram.com/stories/reel/123/", "https://www.instagram.com/stories/reel/123/")
    same("instagram: prose around it (trailing ) and .)", "https://www.instagram.com/reel/C8aBcDeFgHi/?igsh=x).", IG)
    same("instagram: a bare ) on a link with no query", "https://www.instagram.com/reel/C8aBcDeFgHi/)", IG)
    same("instagram: a sentence's full stop on a profile", "https://www.instagram.com/someone.", "https://www.instagram.com/someone")

    print("\n=== TikTok ===")
    TT = "https://www.tiktok.com/@scout2015/video/6718335390845095173"
    for url in (
        TT,
        TT + "?is_from_webapp=1&sender_device=pc",
        TT + "?_t=8abc&_r=1",
        TT + "?utm_source=share&utm_medium=android",
        "https://tiktok.com/@scout2015/video/6718335390845095173?lang=en",
        "http://m.tiktok.com/@scout2015/video/6718335390845095173",
        TT + "#frag",
        TT + ").",
        TT + "?is_from_webapp=1,",
    ):
        same("tiktok", url, TT)
    same("tiktok", "https://vm.tiktok.com/ZMabc12/", "https://vm.tiktok.com/ZMabc12/")
    same("tiktok", "https://vm.tiktok.com/ZMabc12/?utm_source=x", "https://vm.tiktok.com/ZMabc12/")
    same("tiktok", "https://VT.TikTok.com/ZSabc12/?x=1", "https://vt.tiktok.com/ZSabc12/")
    same("tiktok", "http://vm.tiktok.com/ZMabc12", "https://vm.tiktok.com/ZMabc12")
    same("tiktok: the path keeps its case", "https://www.tiktok.com/@Scout/video/1", "https://www.tiktok.com/@Scout/video/1")
    same("tiktok: m. goes to www.", "https://m.tiktok.com/v/6718335390845095173.html?x=1", "https://www.tiktok.com/v/6718335390845095173.html")

    print("\n=== YouTube ===")
    YT = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    forms = [
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://youtube.com/watch?v=dQw4w9WgXcQ&si=abc",
        "https://m.youtube.com/watch?v=dQw4w9WgXcQ&pp=ygUJ&feature=shared",
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=42s&list=PLabc&index=3",
        "https://www.youtube.com/watch?feature=share&si=zz&v=dQw4w9WgXcQ",
        "https://youtu.be/dQw4w9WgXcQ",
        "https://youtu.be/dQw4w9WgXcQ?si=abc",
        "https://youtu.be/dQw4w9WgXcQ?t=42&si=abc",
        "https://youtu.be/dQw4w9WgXcQ#t=42",
        "https://www.youtube.com/shorts/dQw4w9WgXcQ",
        "https://www.youtube.com/shorts/dQw4w9WgXcQ?si=abc",
        "https://youtube.com/shorts/dQw4w9WgXcQ/",
        "https://www.youtube.com/live/dQw4w9WgXcQ?si=abc&feature=share",
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ).",
        "https://youtu.be/dQw4w9WgXcQ?si=abc,",
    ]
    for url in forms:
        same("youtube", url, YT)
    check("youtube: the four forms give one link",
          len({canon("youtube", u) for u in forms}) == 1)
    check("youtube: youtu.be/ID?si=x == watch?v=ID&si=y",
          canon("youtube", "https://youtu.be/dQw4w9WgXcQ?si=x") == canon("youtube", "https://www.youtube.com/watch?v=dQw4w9WgXcQ&si=y"))
    same("youtube: an ID with - and _", "https://youtu.be/a-b_c-d_e-f?si=1", "https://www.youtube.com/watch?v=a-b_c-d_e-f")
    for bad in (
        "https://youtu.be/dQw4w9WgXc",                       # 10 characters
        "https://www.youtube.com/watch?v=dQw4w9WgXc&si=1",   # 10 characters
        "https://www.youtube.com/watch?v=dQw4w9WgXcQQ",      # 12 characters
        "https://www.youtube.com/shorts/short",
        "https://www.youtube.com/watch?si=abc",              # no v at all
        "https://www.youtube.com/watch?v=dQw4w9WgX!Q",       # not an ID character
        "https://www.youtube.com/watch?v=",
    ):
        same("youtube: no valid ID, unchanged", bad, bad)

    print("\n=== Twitter / X ===")
    TW = "https://x.com/SpaceX/status/2042988940756480302"
    for url in (
        TW,
        "https://twitter.com/SpaceX/status/2042988940756480302",
        "https://www.twitter.com/SpaceX/status/2042988940756480302",
        "https://mobile.twitter.com/SpaceX/statuses/2042988940756480302",
        "https://x.com/SpaceX/status/2042988940756480302?s=20",
        "https://x.com/SpaceX/status/2042988940756480302?s=46&t=abcDEF",
        "https://twitter.com/SpaceX/status/2042988940756480302?ref_src=twsrc%5Etfw",
        "https://x.com/SpaceX/status/2042988940756480302/photo/1",
        "https://x.com/SpaceX/status/2042988940756480302#x",
        "https://x.com/SpaceX/status/2042988940756480302).",
    ):
        same("twitter", url, TW)
    same("twitter: the user keeps its case", "https://twitter.com/NASA_Hubble/status/1", "https://x.com/NASA_Hubble/status/1")

    print("\n=== Reddit ===")
    RD = "https://www.reddit.com/r/pics/comments/1abcde/a_title_here/"
    for url in (
        RD,
        "https://reddit.com/r/pics/comments/1abcde/a_title_here/?utm_source=share&utm_medium=web2x&context=3",
        "https://old.reddit.com/r/pics/comments/1abcde/a_title_here/",
        "https://m.reddit.com/r/pics/comments/1abcde/a_title_here/?rdt=12345",
        "http://www.reddit.com/r/pics/comments/1abcde/a_title_here/#comment",
        RD + ").",
    ):
        same("reddit", url, RD)
    same("reddit", "https://www.reddit.com/r/pics/s/AbCdEfGh?utm_source=share", "https://www.reddit.com/r/pics/s/AbCdEfGh")
    same("reddit", "https://redd.it/1abcde", "https://redd.it/1abcde")
    same("reddit", "https://redd.it/1abcde?utm_source=share", "https://redd.it/1abcde")
    same("reddit", "http://REDD.IT/1abcde.", "https://redd.it/1abcde")

    print("\n=== Pinterest ===")
    PIN = "https://www.pinterest.com/pin/664281013778109217/"
    for url in (
        PIN,
        "https://pinterest.com/pin/664281013778109217/",
        "https://www.pinterest.com/pin/664281013778109217",
        "https://www.pinterest.co.uk/pin/664281013778109217/",
        "https://www.pinterest.de/pin/664281013778109217/?utm_source=ios_share&utm_medium=pinterest_ios&utm_campaign=pin_create",
        "https://co.pinterest.com/pin/664281013778109217/sent/?invite_code=abc&sender=1&sfo=1",
        "https://in.pinterest.com/pin/664281013778109217/#x",
        "https://br.pinterest.com/pin/664281013778109217/).",
        "https://www.pinterest.com.au/pin/664281013778109217/?mt=login",
    ):
        same("pinterest", url, PIN)
    same("pinterest", "https://pin.it/2yLmNoP", "https://pin.it/2yLmNoP")
    same("pinterest", "https://pin.it/abc?utm_source=x", "https://pin.it/abc")
    same("pinterest", "https://pin.it/abc.", "https://pin.it/abc")
    same("pinterest: another path keeps host and path", "https://co.pinterest.com/someone/boards/?x=1", "https://co.pinterest.com/someone/boards/")
    same("pinterest: a slugged pin keeps host and path",
         "https://www.pinterest.com/pin/dive-into-serenity--2885187256207927?x=1",
         "https://www.pinterest.com/pin/dive-into-serenity--2885187256207927")

    print("\n=== things it must leave alone, and never fail on ===")
    for platform, url in (
        ("instagram", "not a url at all"),
        ("instagram", "https://evil.example/reel/ABC/?igsh=1"),
        ("tiktok", "https://example.com/@x/video/1?x=1"),
        ("twitter", "https://x.com/SpaceX"),
        ("reddit", "https://example.com/r/x"),
        ("pinterest", "https://pinterest.attacker.example/pin/1/"),
        ("nonsense", "https://www.instagram.com/reel/ABC/?igsh=1"),
        ("instagram", "https://www.instagram.com/reel/AB[C/?x=[::1"),
        ("youtube", ""),
        ("instagram", "https://[::1/"),
    ):
        try:
            got = platforms.canonical_link(platform, url)
        except Exception as exc:                                 # noqa: BLE001
            check(f"{platform}: {url[:40]!r} never raises", False, type(exc).__name__)
            continue
        check(f"{platform}: {url[:40]!r} never raises", isinstance(got, str))
    for platform, url in (("instagram", "not a url at all"), ("instagram", "https://evil.example/reel/ABC/?igsh=1"),
                          ("twitter", "https://x.com/SpaceX"), ("nonsense", "https://www.instagram.com/reel/ABC/?igsh=1"),
                          ("instagram", "https://[::1/"), ("pinterest", "https://pinterest.attacker.example/pin/1/")):
        check(f"{platform}: {url[:40]!r} is returned unchanged", platforms.canonical_link(platform, url) == url)

    print("\n=== writing it through again changes nothing ===")
    bad = [(p, u) for p, u in seen_inputs
           if platforms.canonical_link(p, platforms.canonical_link(p, u)) != platforms.canonical_link(p, u)]
    check(f"idempotent on all {len(seen_inputs)} inputs above", not bad, str(bad[:3]))

    print("\n=== detect_platform hands back the canonical link ===")
    found = platforms.detect_platform("look: https://www.instagram.com/reel/ABC/?igsh=xyz ok")
    check("an Instagram link in a sentence", found == ("instagram", "https://www.instagram.com/reel/ABC/"), str(found))
    found = platforms.detect_platform("(https://youtu.be/dQw4w9WgXcQ?si=abc).")
    check("a YouTube link in brackets", found == ("youtube", "https://www.youtube.com/watch?v=dQw4w9WgXcQ"), str(found))
    found = platforms.detect_platform("https://vm.tiktok.com/ZMabc12/ nice")
    check("a TikTok short link", found == ("tiktok", "https://vm.tiktok.com/ZMabc12/"), str(found))
    found = platforms.detect_platform("https://twitter.com/SpaceX/status/2042988940756480302")
    check("a twitter.com link comes back as x.com", found == ("twitter", "https://x.com/SpaceX/status/2042988940756480302"), str(found))
    found = platforms.detect_platform("https://old.reddit.com/r/pics/comments/1abcde/t/?utm_source=share")
    check("a Reddit link", found == ("reddit", "https://www.reddit.com/r/pics/comments/1abcde/t/"), str(found))
    found = platforms.detect_platform("https://br.pinterest.com/pin/664281013778109217/?mt=login")
    check("a Pinterest link", found == ("pinterest", "https://www.pinterest.com/pin/664281013778109217/"), str(found))
    check("two shares of the post are one link",
          platforms.detect_platform("https://www.instagram.com/reel/ABC/?igsh=one")
          == platforms.detect_platform("https://instagram.com/reel/ABC?utm_source=ig_web_copy_link"))
    check("no link: None", platforms.detect_platform("hello there") is None)
    check("the platform is still the first pattern that matches",
          (platforms.detect_platform("https://x.com/a/status/1 https://www.instagram.com/p/AB/") or ("",))[0] == "instagram")

    print()
    if failures:
        print(f"FAILURES ({len(failures)} of {total}):")
        for name in failures:
            print("  -", name)
        return 1
    print(f"all {total} checks passed: one post is one link.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
