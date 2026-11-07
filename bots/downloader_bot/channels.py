"""DownloaderBot in a channel: what a channel's settings are, how a tap changes them, and what
becomes of a post with a link.

The owner (2026-11-07): "consider if its possible to implement the same thing in a channel, where
the owner/admin sends a link and the bot automatically replaces/sends the link. Sending 2 separate
messages wouldn't be practical so maybe edit the downloaded video into the message with the link? If
not practical, next approach would be sending the downloaded file as a separate message and delete
the previous message with link only - this should also be possible to turn off in settings
somewhere. You can message the owner/admin who adds the bot in direct messages to clarify and
change settings."

It is practical. Telegram lets a bot that may edit a channel's posts turn a text post into a photo
or a video (editMessageMedia), the post's text becoming its caption. So a post that is text with a
link becomes that video in place: the same post, its views and comments, and no second
notification; Telegram marks it edited. A post holds one photo or video, and a caption is at most
1024 characters, so for several files, or a longer text, the bot instead posts the media anew
(silently: the link post already notified everybody) and deletes the link post -- unless `repost`
is off, and then the post stays as it is. The same when the bot lacks the admin right it needs; the
private chat with the bot says which.

A channel's settings are one row of db.group_settings, the table groups use (one row per chat,
nothing about people), marked kind "channel" so that /channels can list the channels:

  on         put media into link posts at all
  platforms  which sites' links (groups.PLATFORMS)
  quality    the tallest picture to fetch, 0 for the best that fits (groups.QUALITIES)
  as_file    files (uncompressed) instead of playable videos and photos
  credit     the "via @bot" line at the end of the caption
  repost     when the post cannot hold the media: post it anew, delete the link post
  lang       the language of the credit line: that of the admin who added the bot

Only the channel's admins change them, in a private chat with the bot, each tap checked with
Telegram. The row goes when the bot leaves the channel or is removed from it.

Pure: no Telegram, no database.
"""
import groups

KIND = "channel"
PLATFORMS = groups.PLATFORMS
QUALITIES = groups.QUALITIES
LANGS = ("en", "uz", "ru")
TOGGLES = ("on", "as_file", "credit", "repost")

DEFAULTS = {
    "on": True,
    "platforms": list(PLATFORMS),
    "quality": 0,
    "as_file": False,
    "credit": True,
    "repost": True,
    "lang": "en",
}

# Telegram's limits: a caption, in UTF-16 code units once its entities are parsed, and an album.
CAPTION_MAX = 1024
ALBUM_MAX = 10

# Callback data: "cs:<channel id>:<action>[:<value>]". The longest, "cs:-1001234567890:p:instagram",
# is 29 bytes, well under Telegram's 64.
PREFIX = "cs:"


def normalize(raw) -> dict:
    """A complete, valid settings dict from whatever was stored (or None), marked as a channel's:
    unknown keys dropped, missing or invalid ones at their default."""
    raw = raw if isinstance(raw, dict) else {}
    settings = dict(DEFAULTS, platforms=list(PLATFORMS), kind=KIND)
    for key in TOGGLES:
        if isinstance(raw.get(key), bool):
            settings[key] = raw[key]
    if isinstance(raw.get("platforms"), list):
        settings["platforms"] = [p for p in PLATFORMS if p in raw["platforms"]]
    if raw.get("quality") in QUALITIES:
        settings["quality"] = raw["quality"]
    if raw.get("lang") in LANGS:
        settings["lang"] = raw["lang"]
    return settings


def is_channel(raw) -> bool:
    """Whether a stored row is a channel's (a group's row has no kind)."""
    return isinstance(raw, dict) and raw.get("kind") == KIND


def apply(settings: dict, action: str, value: str = "") -> "dict | None":
    """The settings after one tap, or None for a tap that means nothing. Never changes `settings`."""
    changed = dict(settings, platforms=list(settings["platforms"]))
    if action in TOGGLES:
        changed[action] = not settings[action]
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


def data(chat_id: int, action: str, value: str = "") -> str:
    return f"{PREFIX}{int(chat_id)}:{action}" + (f":{value}" if value else "")


def parse(text: str) -> "tuple[int, str, str] | None":
    """(-1001234567890, "p", "tiktok") from "cs:-1001234567890:p:tiktok"; None for anything else."""
    if not text.startswith(PREFIX):
        return None
    chat, _, rest = text[len(PREFIX):].partition(":")
    action, _, value = rest.partition(":")
    try:
        chat_id = int(chat)
    except ValueError:
        return None
    return (chat_id, action, value) if action else None


def utf16_length(text: str) -> int:
    """A text's length the way Telegram counts it, for its limits and its entities' offsets."""
    return len(text.encode("utf-16-le")) // 2


def caption_for(text: str, credit: "str | None") -> str:
    """The caption: the post's text, then the credit line if it still fits; the text alone if not."""
    if credit:
        joined = f"{text}\n\n{credit}" if text else credit
        if utf16_length(joined) <= CAPTION_MAX:
            return joined
    return text


def plan(items: int, caption: str, can_edit: bool, can_post: bool, can_delete: bool,
         repost: bool) -> "str | None":
    """What becomes of a link post whose link gave `items` files: "edit" (into the post itself),
    "repost" (posted anew, then the link post deleted), or None (the post stays as it is).
    A caption over Telegram's limit fits neither way: the text would be cut, and it is the
    channel's own words."""
    if items < 1 or utf16_length(caption) > CAPTION_MAX:
        return None
    if items == 1 and can_edit:
        return "edit"
    if repost and can_post and can_delete:
        return "repost"
    return None


def missing_rights(can_edit: bool, can_post: bool, can_delete: bool, repost: bool) -> list:
    """The admin rights the bot lacks for what it is set to do: "edit", "post", "delete"."""
    missing = [] if can_edit else ["edit"]
    if repost:
        missing += [name for name, has in (("post", can_post), ("delete", can_delete)) if not has]
    return missing
