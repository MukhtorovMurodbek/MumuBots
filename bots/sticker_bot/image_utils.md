# `image_utils.py`

[↑ sticker_bot](README.md) · [code](image_utils.py)

Resize/convert arbitrary images into Telegram static-sticker-compliant PNGs.

Telegram's rule for static stickers: exactly one side must be 512px,
the other side <= 512px, image must have transparency support (PNG/WEBP).

_At `Image.MAX_IMAGE_PIXELS = 40_000_000`:_

Pillow's own guard against decompression bombs -- a small file that claims
enormous dimensions and expands into gigabytes once decoded. Pillow warns
at 89 megapixels by default and never refuses; anything a user sends here
is going to be scaled to 512px anyway, so 40 megapixels (about 8000x5000,
well past any phone camera) is a generous ceiling and turns the attack into
a clean error instead of an OOM kill.

### `_upright()`

The picture the way up it was taken, from its EXIF Orientation tag -- 2
to 8 are the seven ways a camera can be held that are not the way it is
stored. The same as ConvertBot's convert_utils.upright(), which explains
why it is needed.

### `to_sticker_png()`

Blocking (Pillow decodes and resamples in-process) -- call it through
asyncio.to_thread so a large image doesn't stall every other user.

- At `img.draft("RGB", (STICKER_SIDE, STICKER_SIDE))`:
  For a JPEG this asks libjpeg to decode straight to a smaller size,
  which it can do almost for free -- a 12 MP phone photo is decoded at
  roughly 1/8 scale rather than being expanded to 48 MB of RGBA and then
  thrown away. No effect on formats that don't support it.
- At `img = _upright(img)`:
  A phone photo sent as a file is stored on its side, with a tag that says
  how to turn it, and Pillow does not turn it: the sticker came out lying
  down. Done after the draft, so it is the reduced picture that is turned.
- At `img = img.resize((new_w, new_h), Image.LANCZOS)`:
  Resample first, convert after: LANCZOS over the source's own mode and
  then one conversion of the 512px result, rather than expanding the full
  original to RGBA first.
