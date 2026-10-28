"""DownloaderBot in a group chat: what a group's settings are, and how a tap changes them.

The owner (2026-10-28): "When added to a group chat, it should automatically download and
send the video to that chat. By default it should download all supported links in video
format (not as a file), and if possible group members should be able to edit the settings so
it only downloads the links from set platforms or in set quality."

So a group has, in db.group_settings, one row of these settings and nothing about its members:

  on          download links at all (a group can pause the bot without removing it)
  platforms   which sites' links are downloaded
  quality     the tallest picture to fetch, 0 for the best that fits; only where the site
              offers a choice (yt-dlp and cobalt routes), the rest send what the site gives
  as_file     send as files (uncompressed) instead of as playable videos and photos
  credit      the "via @bot" line under what is sent
  admins_only only the group's admins may change these

Anybody in the group may change them unless admins_only is on; only an admin may switch
admins_only itself, so a member cannot lock everybody else out. The row is deleted when the
bot leaves or is removed from the group.

Pure: no Telegram, no database.
"""

# The order the settings screen lists them in. "youtube" is shown as "other video sites":
# the bot handles those links quietly and never names the site (see platforms.YOUTUBE_RE).
PLATFORMS = ("instagram", "tiktok", "twitter", "pinterest", "reddit", "youtube")
QUALITIES = (0, 720, 480, 360)

DEFAULTS = {
    "on": True,
    "platforms": list(PLATFORMS),
    "quality": 0,
    "as_file": False,
    "credit": True,
    "admins_only": False,
}

# Callback data: "gs:<action>[:<value>]", well under Telegram's 64 bytes.
PREFIX = "gs:"


def normalize(raw) -> dict:
    """A complete, valid settings dict from whatever was stored (or None): unknown keys
    dropped, missing or invalid ones at their default. A stored row from a later version
    with a setting this one does not know still reads."""
    raw = raw if isinstance(raw, dict) else {}
    settings = dict(DEFAULTS)
    for key in ("on", "as_file", "credit", "admins_only"):
        if isinstance(raw.get(key), bool):
            settings[key] = raw[key]
    if isinstance(raw.get("platforms"), list):
        settings["platforms"] = [p for p in PLATFORMS if p in raw["platforms"]]
    if raw.get("quality") in QUALITIES:
        settings["quality"] = raw["quality"]
    return settings


def needs_admin(action: str, settings: dict) -> bool:
    """Whether a tap on `action` is for the group's admins only."""
    return action == "admins" or settings["admins_only"]


def apply(settings: dict, action: str, value: str = "") -> "dict | None":
    """The settings after one tap, or None for a tap that means nothing (an old or made-up
    button). Never changes `settings` itself."""
    changed = dict(settings, platforms=list(settings["platforms"]))
    if action in ("on", "as_file", "credit", "admins"):
        key = "admins_only" if action == "admins" else action
        changed[key] = not settings[key]
    elif action == "p" and value in PLATFORMS:
        if value in changed["platforms"]:
            changed["platforms"].remove(value)
        else:
            changed["platforms"] = [p for p in PLATFORMS if p in changed["platforms"] or p == value]
    elif action == "q" and value.isdigit() and int(value) in QUALITIES:
        changed["quality"] = int(value)
    else:
        return None
    return changed


def parse(data: str) -> "tuple[str, str] | None":
    """("p", "tiktok") from "gs:p:tiktok"; None for anything else."""
    if not data.startswith(PREFIX):
        return None
    action, _, value = data[len(PREFIX):].partition(":")
    return (action, value) if action else None
