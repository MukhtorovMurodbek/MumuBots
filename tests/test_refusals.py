"""A site refusing the server has to come back as a sentence, not as yt-dlp.

There are two refusals and they are not the same promise:

  bot_check       YouTube asking the server to prove it is not a bot. It may
                  well pass on the next attempt, so "try again in a while" is
                  honest.
  login_required  Instagram declining to serve an anonymous caller from a
                  datacenter address. No retry will pass this: it wants a
                  credential the bot does not have, and telling somebody to
                  try again later is a lie that costs them the try.

Until v1.1.3 only the first was recognised. Instagram's wording matched
nothing, so it fell through to the raw-error path and the user was shown
yt-dlp's message verbatim -- including the link to its FAQ, which Telegram
expanded into a full-width GitHub preview card underneath a failed download.
Nothing had crashed, and it read exactly as though something had.

The markers are matched against real yt-dlp output, so this file holds the
actual strings the sites produce rather than paraphrases of them.

    python tests/test_refusals.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".mirror", "downloader_bot"))
sys.path.insert(0, os.path.join(HERE, "stubs"))

import video

failures = []


def check(name, ok, detail=""):
    print(("  ok  " if ok else "  FAIL  ") + name + ("" if ok else f"   -- {detail}"))
    if not ok:
        failures.append(name)


def classify(text: str):
    """What download_video's except-branch would make of this error."""
    lowered = text.lower()
    if any(m in lowered for m in video.BOT_CHECK_MARKERS):
        return "bot_check"
    if any(m in lowered for m in video.LOGIN_WALL_MARKERS):
        return "login_required"
    return None


# The one from the owner's chat, verbatim.
INSTAGRAM_REEL = (
    "ERROR: [Instagram] Da3Q3XFO-eo: The webpage request was redirected to the "
    "login page. You have exceeded the rate-limit for accessing posts "
    "anonymously. Use --cookies-from-browser or --cookies for the "
    "authentication. See https://github.com/yt-dlp/yt-dlp/wiki/FAQ"
    "#how-do-i-pass-cookies-to-yt-dlp for how to manually pass cookies"
)
INSTAGRAM_RATE_LIMIT = (
    "ERROR: [Instagram] 1234: Requested content is not available, "
    "rate-limit reached or login required"
)
YOUTUBE_BOT_CHECK = (
    "ERROR: [youtube] abc123: Sign in to confirm you're not a bot. "
    "Use --cookies-from-browser or --cookies for the authentication."
)
NOT_A_REFUSAL = "ERROR: [Instagram] abc: Unable to download webpage: HTTP Error 404: Not Found"

check("the reel from the report is recognised as a login wall",
      classify(INSTAGRAM_REEL) == "login_required", classify(INSTAGRAM_REEL))
check("Instagram's other wording too",
      classify(INSTAGRAM_RATE_LIMIT) == "login_required", classify(INSTAGRAM_RATE_LIMIT))
check("YouTube's bot check is still its own kind, not a login wall",
      classify(YOUTUBE_BOT_CHECK) == "bot_check", classify(YOUTUBE_BOT_CHECK))
check("an ordinary 404 is neither, and still surfaces as itself",
      classify(NOT_A_REFUSAL) is None, classify(NOT_A_REFUSAL))

# BlockedBySource has to carry the kind, or bot.py cannot tell the two apart.
exc = video.BlockedBySource("x", "login_required", "instagram")
check("BlockedBySource carries kind and site",
      (exc.kind, exc.site) == ("login_required", "instagram"), (exc.kind, exc.site))
check("and defaults to the older behaviour when not given one",
      video.BlockedBySource("x").kind == "bot_check")

# Per-site credentials pick the right jar. Handing Instagram a Google
# account's cookies is worse than handing it none.
check("a site is identified from its URL",
      [video._site_of(u) for u in (
          "https://www.instagram.com/reel/Da3Q3XFO-eo/",
          "https://vt.tiktok.com/ZSabc/",
          "https://youtu.be/abc",
          "https://example.com/x")] == ["instagram", "tiktok", "youtube", "other"],
      [video._site_of("https://www.instagram.com/reel/x/")])

sys.exit(1 if failures else 0)
