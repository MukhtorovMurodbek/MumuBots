# `groups.py`

[↑ downloader_bot](README.md) · [code](groups.py)

DownloaderBot in a group chat: what a group's settings are, and how a tap changes them.

Asked for on 2026-10-28: added to a group chat, the bot downloads and sends the video to that
chat by itself. By default every supported link is downloaded, as a video (not as a file), and
group members can change the settings so that only links from chosen platforms, or in a chosen
quality, are downloaded.

So a group has, in db.group_settings, one row of these settings and nothing about its members:

```text
on          download links at all (a group can pause the bot without removing it)
platforms   which sites' links are downloaded
quality     the tallest picture to fetch, 0 for the best that fits; only where the site
            offers a choice (yt-dlp and cobalt routes), the rest send what the site gives
as_file     send as files (uncompressed) instead of as playable videos and photos
credit      the "via @bot" line under what is sent
admins_only only the group's admins may change these
```

Anybody in the group may change them unless admins_only is on; only an admin may switch
admins_only itself, so a member cannot lock everybody else out. The row is deleted when the
bot leaves or is removed from the group.

Pure: no Telegram, no database.

_At `PLATFORMS = ("instagram", "tiktok", "twitter", "pinterest", "reddit", platforms.UNADVERTISED)`:_

The order the settings screen lists them in. The last one is shown as "other video sites":
the bot handles those links quietly and never names the site (see platforms.UNADVERTISED_RE).

_At `PREFIX = "gs:"`:_

Callback data: "gs:&lt;action>[:&lt;value>]", well under Telegram's 64 bytes.

### `normalize()`

A complete, valid settings dict from whatever was stored (or None): unknown keys
dropped, missing or invalid ones at their default. A stored row from a later version
with a setting this one does not know still reads.

### `needs_admin()`

Whether a tap on `action` is for the group's admins only.

### `apply()`

The settings after one tap, or None for a tap that means nothing (an old or made-up
button). Never changes `settings` itself.

### `parse()`

("p", "tiktok") from "gs:p:tiktok"; None for anything else.
