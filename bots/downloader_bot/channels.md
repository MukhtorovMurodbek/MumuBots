# `channels.py`

[↑ downloader_bot](README.md) · [code](channels.py)

DownloaderBot in a channel: what a channel's settings are, how a tap changes them, and what
becomes of a post with a link.

Asked for on 2026-11-07: the same thing in a channel, where an admin posts a link and the bot
replaces it with the download. Two separate messages would not be practical, so the downloaded
video is edited into the message with the link where possible; otherwise the file goes as a
separate message and the link-only message is deleted, which can be turned off in the settings.
The admin who adds the bot may be messaged directly, to clarify things and change settings.

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

```text
on         put media into link posts at all
platforms  which sites' links (groups.PLATFORMS)
quality    the tallest picture to fetch, 0 for the best that fits (groups.QUALITIES)
as_file    files (uncompressed) instead of playable videos and photos
credit     the "via @bot" line at the end of the caption
repost     when the post cannot hold the media: post it anew, delete the link post
lang       the language of the credit line: that of the admin who added the bot
```

Only the channel's admins change them, in a private chat with the bot, each tap checked with
Telegram. The row goes when the bot leaves the channel or is removed from it.

Pure: no Telegram, no database.

_At `CAPTION_MAX = 1024`:_

Telegram's limits: a caption, in UTF-16 code units once its entities are parsed, and an album.

_At `PREFIX = "cs:"`:_

Callback data: "cs:&lt;channel id>:&lt;action>[:&lt;value>]". The longest, "cs:-1001234567890:p:instagram",
is 29 bytes, well under Telegram's 64.

### `normalize()`

A complete, valid settings dict from whatever was stored (or None), marked as a channel's:
unknown keys dropped, missing or invalid ones at their default.

### `is_channel()`

Whether a stored row is a channel's (a group's row has no kind).

### `apply()`

The settings after one tap, or None for a tap that means nothing. Never changes `settings`.

### `parse()`

(-1001234567890, "p", "tiktok") from "cs:-1001234567890:p:tiktok"; None for anything else.

### `utf16_length()`

A text's length the way Telegram counts it, for its limits and its entities' offsets.

### `caption_for()`

The caption: the post's text, then the credit line if it still fits; the text alone if not.

### `plan()`

What becomes of a link post whose link gave `items` files: "edit" (into the post itself),
"repost" (posted anew, then the link post deleted), or None (the post stays as it is).
A caption over Telegram's limit fits neither way: the text would be cut, and it is the
channel's own words.

### `missing_rights()`

The admin rights the bot lacks for what it is set to do: "edit", "post", "delete".
