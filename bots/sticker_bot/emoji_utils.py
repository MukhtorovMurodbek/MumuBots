"""Heuristics for the 'send emoji right after a sticker to override the default' flow.

Not a full grapheme-cluster-aware emoji parser (ZWJ sequences like family emoji
will split into parts) -- good enough for single emoji / simple combos, which is
the common case for tagging stickers.
"""
import unicodedata

DEFAULT_EMOJI = "\U0001F62D"  # 😭
_VARIATION_SELECTOR = "\uFE0F"
MAX_LEN = 16
MAX_EMOJI_PER_STICKER = 20


def looks_like_emoji_message(text: str) -> bool:
    """Only emoji: no letters or digits of any script ("ok", "да" and "rahmat" are words,
    not emoji), and at least one symbol -- or a character drawn as emoji, which the variation
    selector after it says (‼️, ↔️). Punctuation alone ("?", "...") is not emoji either.
    Since 2.2.0 this also decides whether a reply in a group is meant to retag a sticker."""
    text = text.strip()
    if not text or len(text) > MAX_LEN or any(c.isalnum() for c in text):
        return False
    return _VARIATION_SELECTOR in text or any(unicodedata.category(c) == "So" for c in text)


def split_emoji(text: str) -> list[str]:
    """Best-effort split of a short string into individual emoji."""
    chars = list(text.strip())
    out: list[str] = []
    i = 0
    while i < len(chars):
        c = chars[i]
        if i + 1 < len(chars) and chars[i + 1] == _VARIATION_SELECTOR:
            out.append(c + chars[i + 1])
            i += 2
        else:
            out.append(c)
            i += 1

    seen = set()
    deduped = []
    for e in out:
        if e not in seen:
            seen.add(e)
            deduped.append(e)
    return deduped[:MAX_EMOJI_PER_STICKER] or [DEFAULT_EMOJI]
