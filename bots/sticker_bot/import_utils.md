# `import_utils.py`

[↑ sticker_bot](README.md) · [code](import_utils.py)

Mass-import stickers into a pack that's currently being edited.

```text
Two sources are supported:
- Another Telegram sticker pack (public, any name/link) -- reuses each
  source sticker's existing file_id, so there's no re-encoding at all: full
  quality, and it keeps the original emoji tags.
- A WhatsApp sticker-pack export (.wastickers / .zip) -- these are just zip
  files full of .webp images, optionally with a contents.json manifest (the
  format used by most open-source "sticker maker" apps) mapping each image
  to its emoji list. Falls back to the default emoji when there's no
  manifest, or it doesn't parse.
```

- `MAX_IMPORT_PER_RUN = 100` — stays comfortably under Telegram's ~120-sticker set cap

_At `_MAX_UNCOMPRESSED_ENTRY = 32 * 1024 * 1024`:_

A zip entry's declared uncompressed size, above which it is skipped rather
than decompressed. A sticker image is tens of kilobytes; anything claiming
32 MB is either not a sticker or is a zip bomb, and either way expanding it
to find out is the expensive mistake.

### `parse_telegram_pack_source()`

Pulls a short sticker-set name out of a link or raw name the user sent.

### `fetch_importable_stickers()`

Returns the list of Sticker objects from a public set, or raises
ImportError\_ with a human-readable reason.

### `sticker_to_input_sticker()`

Reuses the source sticker's file_id directly -- no download/re-encode
needed since it's already a valid Telegram sticker file. Returns None
for formats that can't be carried over (Lottie/.tgs animated stickers).

- `if sticker.is_animated:` — Lottie/.tgs

_At `_IMG_EXT_RE = re.compile(r"\.(webp|png|jpg|jpeg)$", re.IGNORECASE)`:_

---------- WhatsApp zip import ----------

### `parse_whatsapp_zip()`

Returns a list of (sticker_png_bytes, emoji_list) pulled out of a
.wastickers/.zip export. Raises ImportError\_ if it isn't a readable zip
or has no usable images inside.

- At `emoji_by_filename: dict[str, list[str]] = {}`:
  Optional manifest used by most WhatsApp "sticker maker" export tools:
  {"stickers": [{"image_file": "01.webp", "emojis": ["😀"]}, ...]}
- `pass` — manifest is a nice-to-have, not required
- At `break`:
  Stop at the cap rather than converting the whole archive and
  then slicing it: a 400-image export used to mean 400 decodes
  and 400 PNGs held in memory to use the first hundred.
- `continue` — a zip bomb, or something that was never a sticker
- `continue` — skip unreadable entries rather than failing the whole import
