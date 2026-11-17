# `cards.py`

[↑ downloader_bot](README.md) · [code](cards.py)

Renders a plain, consistent 'quote card' image for text-based Reddit/
Twitter posts -- the actual reason to download these instead of just
screenshotting: no UI chrome, no notification banners caught mid-shot, no
light/dark-mode mismatch, and it's already square-ish and clean enough to
repost as-is.

Uses Pillow's built-in scalable default font (load_default(size=...),
Pillow >=10.1) instead of bundling a .ttf -- works identically wherever
this bot ends up hosted, no font files to ship or find on the host OS.

### `_font()`

Cached: there are exactly four sizes in this file, and rebuilding the
same four font objects for every card is work with no result.

### `render_card()`

platform: "reddit" or "twitter" (picks the accent color).
source: e.g. "r/aww" or "@handle" -- shown large, in the accent color.
author: e.g. "u/someone" or "Display Name" -- shown small, muted.
body: the post/tweet text, wrapped and truncated to MAX_BODY_LINES.
meta: an optional footer line, e.g. score/like counts.

- `max_text_width = WIDTH - PADDING * 2 - 16` — 16 for the accent bar
