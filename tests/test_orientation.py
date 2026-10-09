"""A photo comes out the way up it was taken.

    python3 tests/test_orientation.py

A phone stores a portrait photograph as its sensor saw it -- lying on its
side -- and adds an EXIF tag that says how to turn it. Pillow reads the tag and
does not act on it, and writing the picture to PNG, WEBP or a PDF page drops
it. So a photo sent to ConvertBot, or to StickerBot for a sticker, *as a file*
came back lying down. (Sent as a photo it did not: Telegram turns those
itself, which is why it went unnoticed -- and the owner's own phone, a Galaxy
S23 Ultra, is the kind that writes the tag.)

This pins the direction of every one of the seven turns pixel by pixel against
the EXIF specification's own table, not against Pillow; that a picture with no
tag, or tag 1, is left alone; that formats whose decoders already turn the
picture (HEIC, TIFF) or whose container does (HEIF, AVIF) are not turned a
second time; and that the PDF and the sticker are upright too.

Needs Pillow. HEIC and AVIF are checked where this Pillow can write them.
"""
import io
import os
import pathlib
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / ".mirror" / "convert_bot") if (HERE / ".mirror").exists()
                else str(HERE.parent / "convert_bot"))
sys.path.insert(1, str(HERE / ".mirror" / "sticker_bot") if (HERE / ".mirror").exists()
                else str(HERE.parent / "sticker_bot"))
sys.path.insert(2, str(HERE.parent / "shared"))
sys.path.append(str(HERE / "stubs"))             # after site-packages: the real Pillow beats the stand-in
os.environ.setdefault("CBOT_TOKEN", "test")
os.environ.setdefault("CBOT_USERNAME", "test")
os.environ.setdefault("DATABASE_URL", "postgresql://stub")
os.environ["ENV_FILES"] = "off"

failures = []
WORK = pathlib.Path(tempfile.mkdtemp(prefix="orientation-"))


def check(name, ok, detail=""):
    print(("  ok  " if ok else "  FAIL  ") + name + (f" -- {detail}" if not ok and detail else ""))
    if not ok:
        failures.append(name)


W, H = 40, 24          # stored wider than tall: a portrait shot lying on its side


def source_pixel(x, y):
    """Every pixel of the stored picture is different, so a wrong turn cannot pass."""
    return (x * 6 + 10, y * 10 + 10, (x * 3 + y * 5) % 200 + 20)


# Where each displayed pixel (x, y) comes from in the stored picture, for each
# EXIF Orientation value -- the table in the EXIF specification, written out.
# Values 5 to 8 swap the sides: the displayed picture is H wide and W tall.
ORIGIN = {
    2: lambda x, y: (W - 1 - x, y),                 # mirrored left to right
    3: lambda x, y: (W - 1 - x, H - 1 - y),         # turned 180
    4: lambda x, y: (x, H - 1 - y),                 # mirrored top to bottom
    5: lambda x, y: (y, x),                         # mirrored along the main diagonal
    6: lambda x, y: (y, H - 1 - x),                 # turned 90 clockwise
    7: lambda x, y: (W - 1 - y, H - 1 - x),         # mirrored along the other diagonal
    8: lambda x, y: (W - 1 - y, x),                 # turned 90 counter-clockwise
}


def stored_picture(Image):
    img = Image.new("RGB", (W, H))
    img.putdata([source_pixel(x, y) for y in range(H) for x in range(W)])
    return img


def tagged(Image, orientation, fmt="PNG"):
    """The stored picture as a file that says it is to be turned `orientation`."""
    exif = Image.Exif()
    if orientation is not None:
        exif[0x0112] = orientation
    path = WORK / f"o{orientation}.{fmt.lower()}"
    stored_picture(Image).save(path, format=fmt, exif=exif.tobytes())
    return path


def main() -> int:
    try:
        from PIL import Image
        Image.Exif
    except (ImportError, AttributeError):
        print("skipped: Pillow is not installed here (only the stand-in in tests/stubs is).")
        return 0
    import convert_utils

    print("=== every turn, pixel by pixel (lossless, so nothing is a tolerance) ===")
    for orientation, origin in ORIGIN.items():
        out = WORK / f"out{orientation}.png"
        convert_utils.convert_image(str(tagged(Image, orientation)), "png", out)
        got = Image.open(out).convert("RGB")
        swaps = orientation >= 5
        expected_size = (H, W) if swaps else (W, H)
        if got.size != expected_size:
            check(f"orientation {orientation} comes out {expected_size[0]}x{expected_size[1]}",
                  False, f"{got.size[0]}x{got.size[1]}")
            continue
        wrong = [(x, y) for y in range(got.size[1]) for x in range(got.size[0])
                 if got.getpixel((x, y)) != source_pixel(*origin(x, y))]
        check(f"orientation {orientation} comes out the way up it was taken",
              not wrong, f"{len(wrong)} of {got.size[0] * got.size[1]} pixels are somewhere else")

    print("\n=== a picture that needs no turning is not touched ===")
    for label, orientation in (("no tag", None), ("tag 1", 1), ("tag 0", 0), ("tag 9, which is not one", 9)):
        out = WORK / "same.png"
        convert_utils.convert_image(str(tagged(Image, orientation)), "png", out)
        got = Image.open(out).convert("RGB")
        same = got.size == (W, H) and all(got.getpixel((x, y)) == source_pixel(x, y)
                                          for y in range(H) for x in range(W))
        check(f"{label}: unchanged", same)

    print("\n=== a photo as JPEG, which is what a phone writes ===")
    jpeg = WORK / "phone.jpg"
    exif = Image.Exif()
    exif[0x0112] = 6
    big = Image.new("RGB", (200, 100), (255, 0, 0))
    big.paste((0, 0, 255), (100, 0, 200, 100))       # red on the left, blue on the right
    big.save(jpeg, "JPEG", quality=95, exif=exif.tobytes())
    for target in ("png", "webp", "jpg", "bmp"):
        out = WORK / f"phone.{target}"
        convert_utils.convert_image(str(jpeg), target, out)
        got = Image.open(out).convert("RGB")
        if got.size != (100, 200):
            check(f"jpg to {target} is portrait, red above blue", False, f"{got.size[0]}x{got.size[1]}")
            continue
        top, bottom = got.getpixel((50, 10)), got.getpixel((50, 190))
        check(f"jpg to {target} is portrait, red above blue",
              top[0] > 200 and top[2] < 60 and bottom[2] > 200 and bottom[0] < 60,
              f"top {top} bottom {bottom}")

    print("\n=== and on a PDF page ===")
    pdf = WORK / "phone.pdf"
    convert_utils.images_to_pdf([str(jpeg)], pdf)
    try:
        import pymupdf
        page = pymupdf.open(pdf)[0]
        check("a landscape-stored photo tagged 6 is a portrait page", page.rect.height > page.rect.width,
              f"{page.rect.width:.0f} x {page.rect.height:.0f}")
    except ImportError:
        print("  skipped  the page check: PyMuPDF is not installed")

    print("\n=== a decoder that has already turned it is not turned again ===")
    try:
        import pillow_heif
        pillow_heif.register_heif_opener()
        path = WORK / "phone.heic"
        exif = Image.Exif()
        exif[0x0112] = 6
        stored_picture(Image).save(path, format="HEIF", quality=90, exif=exif.tobytes())
        out = WORK / "heic.png"
        convert_utils.register_openers()
        convert_utils.convert_image(str(path), "png", out)
        size = Image.open(out).size
        check("HEIC tagged 6: portrait, once", size == (H, W), str(size))
    except Exception as exc:                            # noqa: BLE001
        print(f"  skipped  HEIC: {type(exc).__name__}")
    try:
        path = WORK / "phone.tiff"
        exif = Image.Exif()
        exif[0x0112] = 6
        stored_picture(Image).save(path, format="TIFF", exif=exif.tobytes())
        out = WORK / "tiff.png"
        convert_utils.convert_image(str(path), "png", out)
        size = Image.open(out).size
        check("TIFF tagged 6: portrait, once", size == (H, W), str(size))
    except Exception as exc:                            # noqa: BLE001
        print(f"  skipped  TIFF: {type(exc).__name__}")
    try:
        path = WORK / "phone.avif"
        exif = Image.Exif()
        exif[0x0112] = 6
        stored_picture(Image).save(path, format="AVIF", quality=90, exif=exif.tobytes())
        out = WORK / "avif.png"
        convert_utils.convert_image(str(path), "png", out)
        size = Image.open(out).size
        check("AVIF: its own container says which way up it is, and EXIF beside it is ignored",
              size == (W, H), str(size))
    except Exception as exc:                            # noqa: BLE001
        print(f"  skipped  AVIF: {type(exc).__name__}")

    print("\n=== StickerBot ===")
    import image_utils
    for orientation, expected in ((None, (512, 307)), (1, (512, 307)), (6, (307, 512)), (8, (307, 512)),
                                  (3, (512, 307))):
        exif = Image.Exif()
        if orientation is not None:
            exif[0x0112] = orientation
        buffer = io.BytesIO()
        Image.new("RGB", (200, 120), (200, 30, 30)).save(buffer, "JPEG", exif=exif.tobytes())
        sticker = Image.open(image_utils.to_sticker_png(buffer.getvalue()))
        label = "no tag" if orientation is None else f"tag {orientation}"
        check(f"a 200x120 photo, {label}: a sticker {expected[0]}x{expected[1]}",
              sticker.size == expected, str(sticker.size))

    print()
    if failures:
        print(f"FAILURES ({len(failures)}):")
        for name in failures:
            print("  -", name)
        return 1
    print("a photo comes out the way up it was taken.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
