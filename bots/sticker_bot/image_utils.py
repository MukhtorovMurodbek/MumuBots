from io import BytesIO

from PIL import Image

STICKER_SIDE = 512

Image.MAX_IMAGE_PIXELS = 40_000_000


def _upright(img):
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
    img = Image.open(BytesIO(image_bytes))

    img.draft("RGB", (STICKER_SIDE, STICKER_SIDE))

    img = _upright(img)

    w, h = img.size
    if w >= h:
        new_w = STICKER_SIDE
        new_h = max(1, round(h * STICKER_SIDE / w))
    else:
        new_h = STICKER_SIDE
        new_w = max(1, round(w * STICKER_SIDE / h))

    img = img.resize((new_w, new_h), Image.LANCZOS)
    if img.mode != "RGBA":
        img = img.convert("RGBA")

    out = BytesIO()
    img.save(out, format="PNG")
    img.close()
    out.seek(0)
    out.name = "sticker.png"
    return out
