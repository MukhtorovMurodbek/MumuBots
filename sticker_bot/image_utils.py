"""Resize/convert arbitrary images into Telegram static-sticker-compliant PNGs.

Telegram's rule for static stickers: exactly one side must be 512px,
the other side <= 512px, image must have transparency support (PNG/WEBP).
"""
from io import BytesIO

from PIL import Image

STICKER_SIDE = 512

# Pillow's own guard against decompression bombs -- a small file that claims
# enormous dimensions and expands into gigabytes once decoded. Pillow warns
# at 89 megapixels by default and never refuses; anything a user sends here
# is going to be scaled to 512px anyway, so 40 megapixels (about 8000x5000,
# well past any phone camera) is a generous ceiling and turns the attack into
# a clean error instead of an OOM kill.
Image.MAX_IMAGE_PIXELS = 40_000_000


def _upright(img):
    """The picture the way up it was taken, from its EXIF Orientation tag -- 2
    to 8 are the seven ways a camera can be held that are not the way it is
    stored. The same as ConvertBot's convert_utils.upright(), which explains
    why it is needed."""
    try:
        orientation = img.getexif().get(0x0112, 1)
    except Exception:
        return img
    if orientation not in (2, 3, 4, 5, 6, 7, 8):
        return img
    from PIL import ImageOps
    turned = ImageOps.exif_transpose(img)
    if turned is not img:
        img.close()
    return turned


def to_sticker_png(image_bytes: bytes) -> BytesIO:
    """Blocking (Pillow decodes and resamples in-process) -- call it through
    asyncio.to_thread so a large image doesn't stall every other user."""
    img = Image.open(BytesIO(image_bytes))

    # For a JPEG this asks libjpeg to decode straight to a smaller size,
    # which it can do almost for free -- a 12 MP phone photo is decoded at
    # roughly 1/8 scale rather than being expanded to 48 MB of RGBA and then
    # thrown away. No effect on formats that don't support it.
    img.draft("RGB", (STICKER_SIDE, STICKER_SIDE))

    # A phone photo sent as a file is stored on its side, with a tag that says
    # how to turn it, and Pillow does not turn it: the sticker came out lying
    # down. Done after the draft, so it is the reduced picture that is turned.
    img = _upright(img)

    w, h = img.size
    if w >= h:
        new_w = STICKER_SIDE
        new_h = max(1, round(h * STICKER_SIDE / w))
    else:
        new_h = STICKER_SIDE
        new_w = max(1, round(w * STICKER_SIDE / h))

    # Resample first, convert after: LANCZOS over the source's own mode and
    # then one conversion of the 512px result, rather than expanding the full
    # original to RGBA first.
    img = img.resize((new_w, new_h), Image.LANCZOS)
    if img.mode != "RGBA":
        img = img.convert("RGBA")

    out = BytesIO()
    img.save(out, format="PNG")
    img.close()
    out.seek(0)
    out.name = "sticker.png"
    return out
