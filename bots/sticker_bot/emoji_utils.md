# `emoji_utils.py`

[↑ sticker_bot](README.md) · [code](emoji_utils.py)

Heuristics for the 'send emoji right after a sticker to override the default' flow.

Not a full grapheme-cluster-aware emoji parser (ZWJ sequences like family emoji
will split into parts) -- good enough for single emoji / simple combos, which is
the common case for tagging stickers.

- `DEFAULT_EMOJI = "\U0001F62D"` — 😭

### `looks_like_emoji_message()`

Only emoji: no letters or digits of any script ("ok", "да" and "rahmat" are words,
not emoji), and at least one symbol -- or a character drawn as emoji, which the variation
selector after it says (‼️, ↔️). Punctuation alone ("?", "...") is not emoji either.
Since 2.2.0 this also decides whether a reply in a group is meant to retag a sticker.

### `split_emoji()`

Best-effort split of a short string into individual emoji.
