"""An album as one PDF or one ZIP: DownloaderBot's two buttons under a delivered album.

    python tests/test_merge.py

After a carousel, an X photo set or a TikTok slideshow the bot's follow-up line
carries two more buttons, 📄 PDF (offered from two photos) and 🗜 ZIP (from two
files of any kind). A tap fetches the post again -- nothing is kept, which is
what PRIVACY.md says -- builds one file, sends it as one document and deletes
everything it touched. downloader_bot/merge.py builds the two files; the PDF is
written by hand (no new dependency), so this is where it is checked against
something that is not the code that wrote it:

  * the PDF's own structure, read back by a parser written here (the cross-
    reference table's byte offsets, the trailer) and by PyMuPDF, which also
    renders the pages -- page count, aspect ratios, colours, which way up;
  * what goes in as it came (RGB and gray JPEGs, baseline and progressive, byte
    for byte) and what is re-encoded (PNG with alpha onto white, CMYK, all
    eight EXIF orientations, 16-bit gray, palette);
  * the refusals: past 20 megapixels (from the header: nothing decoded), too
    many files, too many MB (fetching stops at the file that passes it);
  * one decoded image in memory at a time, and the file growing as it goes;
  * what is not a photo: only a still JPEG, PNG, WEBP or GIF goes in -- an ICO
    named .jpg (Pillow decodes its embedded PNG inside open()), a BMP, a TIFF
    and anything animated are found before a pixel is decoded and left out of
    the PDF, as a video is left out of an album's (no PDF when fewer than two
    photographs are left, said as "no photos", not as a failure); a picture
    too big still refuses the whole PDF; a phone's MPO is still one photo; and
    the size is checked again after decoding;
  * PDFs built one at a time in the whole process (three at once measured
    957 MB), a ZIP not held up behind them, and the gate let go of after a
    failure;
  * a JPEG's marker walk giving the answers it always gave (against the old
    walk, on real files and on fuzzed ones) in C, so that a 48 MB file of 0xFF
    costs a fraction of a second and not fourteen, and a JPEG that is the start
    of its file going in as a view of it, not a copy;
  * the header walk: files of millions of JPEG segments, PNG chunks (before, after
    and between the IDATs), GIF comment and LZW sub-blocks (at any frame), WEBP
    chunks, refused in a few milliseconds and a few KiB with Pillow never asked,
    each cap just below and just above; a file cut short is told apart from one
    that is not a photo; a side over 65,500 px; a page never under one point; a
    corrupt WEBP left out; a cancelled caller not releasing the PDF gate;
  * a tap taking away only its own button, and a refused allowance giving it back;
  * the ZIP's entries named by position and the downloaded file's own
    extension, never by what the source called the file;
  * the real merge_callback and _merge_and_send, driven with the routes, the
    database and Telegram faked: the allowance and the download record before
    anything is fetched, what a PDF fetches (the photos, not the video), what
    each refusal says, and every file deleted on every ending, including a send
    that fails; and a double tap -- one after the other, or both in flight at
    once -- starting one merge and counting one download;
  * the wiring, read from the source: the handler is registered, the allowance
    is asked before the download, the cleanup is in a `finally`, no remote name
    is read, both limits come from the environment and are in .env.example.

Needs Pillow for the picture checks; PyMuPDF for the rendering ones (each says
so and skips without). No network, no database, nothing from Telegram.

To see a check catch breakage, copy the bot into tests/.mirror, break the copy
(the way tests/README.md describes) and run this again: it imports from the
mirror when there is one.
"""
import ast
import asyncio
import dataclasses
import gc
import io
import json
import logging
import os
import pathlib
import random
import re
import shutil
import sys
import tempfile
import threading
import time
import tracemalloc
import uuid
import zipfile
import zlib
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent
SRC = HERE / ".mirror" / "downloader_bot"
if not SRC.exists():
    SRC = REPO / "downloader_bot"
sys.path.insert(0, str(SRC))
sys.path.insert(1, str(REPO / "shared"))

# The real Pillow, imported before tests/stubs goes on the path: the stand-in
# there has no ImageOps, and the stubs come first for everything else.
try:
    import PIL.Image
    import PIL.ImageOps  # noqa: F401
    from PIL import Image, ImageOps
    PIL_OK = hasattr(ImageOps, "exif_transpose") and hasattr(Image, "frombytes")
except ImportError:
    PIL_OK = False
sys.path.insert(0, str(HERE / "stubs"))

SCRATCH = pathlib.Path(tempfile.mkdtemp(prefix="downloaderbot-merge-"))     # the bot's work directory
FIXTURES = pathlib.Path(tempfile.mkdtemp(prefix="downloaderbot-merge-in-"))  # what the tests make
os.environ.update(ENV_FILES="off", DBOT_TOKEN="123:test", DBOT_USERNAME="test", DATABASE_URL="postgresql://stub",
                  DBOT_WORK_DIR=str(SCRATCH))
for key in ("DBOT_MERGE_MAX_ITEMS", "DBOT_MERGE_MAX_MB", "DBOT_MAX_DOWNLOAD_MB", "DBOT_MIN_FREE_MB"):
    os.environ.pop(key, None)

import bot             # noqa: E402
import i18n            # noqa: E402
import large_files     # noqa: E402
import merge           # noqa: E402
import net             # noqa: E402
import problems        # noqa: E402
import resolvers       # noqa: E402

logging.disable(logging.CRITICAL)           # the refusals below log their tracebacks; the output is the checks

try:
    import pymupdf
except ImportError:
    try:
        import fitz as pymupdf
    except ImportError:
        pymupdf = None

MB = 1024 * 1024
failures = []


def check(name, ok, detail=""):
    print(("  ok  " if ok else "  FAIL  ") + name + (f" -- {detail}" if not ok and detail else ""))
    if not ok:
        failures.append(name)


def skip(name, why):
    print(f"  --  {name}: skipped, {why}")


# ---------------------------------------------------------------------------
# Pictures, made here
# ---------------------------------------------------------------------------

def save(name, image, fmt, **kwargs):
    path = FIXTURES / name
    image.save(path, fmt, **kwargs)
    return str(path)


def gradient(size, mode="RGB"):
    ramp = Image.linear_gradient("L").resize(size)
    if mode == "L":
        return ramp
    return Image.merge("RGB", (ramp, ramp.transpose(Image.Transpose.FLIP_TOP_BOTTOM), ramp.point(lambda v: 255 - v)))


def noise(size, seed=1):
    return Image.frombytes("RGB", size, random.Random(seed).randbytes(size[0] * size[1] * 3))


def corner_marked(size):
    """Blue, with a red block in the top-left corner: which of the eight ways
    up it is shows in where the red ends up."""
    image = Image.new("RGB", size, (0, 0, 255))
    image.paste(Image.new("RGB", (size[0] * 2 // 5, size[1] * 2 // 5), (255, 0, 0)), (0, 0))
    return image


def exif_with(orientation):
    exif = Image.Exif()
    exif[0x0112] = orientation
    return exif


def near(pixel, want, tolerance=40):
    return all(abs(a - b) <= tolerance for a, b in zip(pixel, want))


# ---------------------------------------------------------------------------
# A PDF, read without trusting the code that wrote it
# ---------------------------------------------------------------------------

def structure_problems(data: bytes):
    """Everything wrong with a PDF's skeleton, by reading it the way a viewer
    does: startxref, the table it points to, and every offset in the table."""
    found = []
    if not data.startswith(b"%PDF-1."):
        found.append("no header")
    tail = re.search(rb"startxref\s+(\d+)\s+%%EOF\s*$", data)
    if not tail:
        return found + ["no startxref / %%EOF at the end"]
    table = int(tail.group(1))
    if data[table:table + 4] != b"xref":
        return found + [f"startxref {table} does not point at 'xref'"]
    head = re.match(rb"xref\n0 (\d+)\n", data[table:])
    if not head:
        return found + ["the table has no subsection header"]
    count = int(head.group(1))
    body = data[table + head.end():]
    entries = [body[20 * n:20 * (n + 1)] for n in range(count)]
    if any(len(entry) != 20 or not re.fullmatch(rb"\d{10} \d{5} [nf] \r?\n|\d{10} \d{5} [nf]\r\n", entry) for entry in entries):
        found.append("an entry in the table is not 20 bytes of the right shape")
        return found
    for number, entry in enumerate(entries):
        offset, kind = int(entry[:10]), entry[17:18]
        if number == 0:
            if kind != b"f":
                found.append("entry 0 is not the free list head")
            continue
        if kind != b"n" or not data[offset:].startswith(b"%d 0 obj" % number):
            found.append(f"object {number}: offset {offset} does not land on '{number} 0 obj'")
    declared = re.findall(rb"(?m)^(\d+) 0 obj\b", data)
    if sorted(int(n) for n in declared) != list(range(1, count)):
        found.append(f"the file holds objects {sorted(int(n) for n in declared)}, the table lists 1..{count - 1}")
    trailer = data[table + head.end() + 20 * count:]
    if f"/Size {count}".encode() not in trailer or b"/Root 1 0 R" not in trailer:
        found.append("the trailer does not say /Size and /Root")
    return found


def embedded_images(data: bytes):
    """[(dictionary text, stream bytes)] for every image in the PDF, cut out
    by the /Length the file gives."""
    found = []
    for match in re.finditer(rb"<<([^>]*?/Subtype /Image[^>]*?)/Length (\d+) >>\nstream\n", data):
        length = int(match.group(2))
        stream = data[match.end():match.end() + length]
        if data[match.end() + length:match.end() + length + 10] != b"\nendstream":
            raise AssertionError("an image stream is not followed by endstream where /Length says")
        found.append((match.group(1).decode("latin-1"), stream))
    return found


def pages(data: bytes):
    """[(width, height)] of every page's MediaBox, in order."""
    return [(float(w), float(h)) for w, h in re.findall(rb"/MediaBox \[0 0 ([\d.]+) ([\d.]+)\]", data)]


def build(paths, name="out.pdf"):
    out = FIXTURES / name
    merge.build_pdf(paths, str(out))
    return out.read_bytes(), out


def decode(stream: bytes):
    return Image.open(io.BytesIO(stream))


# ---------------------------------------------------------------------------
# The PDF
# ---------------------------------------------------------------------------

def pdf_checks():
    print("== a JPEG goes in as it came ==")
    rgb = save("rgb.jpg", gradient((120, 80)), "JPEG", quality=85)
    rgb_bytes = pathlib.Path(rgb).read_bytes()
    data, _ = build([rgb])
    images = embedded_images(data)
    check("an RGB JPEG's bytes appear in the PDF unchanged", rgb_bytes in data)
    check("and are the whole image stream, DCTDecode, DeviceRGB, 8 bits",
          len(images) == 1 and images[0][1] == rgb_bytes and "/DCTDecode" in images[0][0]
          and "/DeviceRGB" in images[0][0] and "/BitsPerComponent 8" in images[0][0], images[:1])
    check("on a page the size of the picture in points", pages(data) == [(120.0, 80.0)], pages(data))
    check("with the table's offsets, and the trailer, as a viewer reads them", not structure_problems(data),
          structure_problems(data))

    progressive = save("progressive.jpg", gradient((90, 70)), "JPEG", quality=80, progressive=True)
    data, _ = build([progressive])
    check("a progressive JPEG too", pathlib.Path(progressive).read_bytes() in data
          and "/DeviceRGB" in embedded_images(data)[0][0])

    gray = save("gray.jpg", gradient((50, 60), "L"), "JPEG", quality=90)
    gray_bytes = pathlib.Path(gray).read_bytes()
    data, _ = build([gray])
    images = embedded_images(data)
    check("a grayscale JPEG goes in as it came, as DeviceGray", gray_bytes in data and len(images) == 1
          and images[0][1] == gray_bytes and "/DeviceGray" in images[0][0] and "/DeviceRGB" not in images[0][0],
          images[:1])

    # A phone's file can carry a second picture after the first one's end; the
    # PDF takes the first picture, not whatever follows it.
    junk = pathlib.Path(FIXTURES / "trailing.jpg")
    junk.write_bytes(rgb_bytes + b"\xff\xd8 a second picture, or just a blob " * 50)
    data, _ = build([str(junk)])
    check("what follows a JPEG's end is not carried into the PDF",
          embedded_images(data)[0][1] == rgb_bytes, len(embedded_images(data)[0][1]))

    cut = pathlib.Path(FIXTURES / "cut.jpg")
    cut.write_bytes(rgb_bytes[:len(rgb_bytes) // 2])
    try:
        build([str(cut)])
        refused = False
    except Exception:
        refused = True
    check("a JPEG cut short is not embedded blindly: it is decoded, and refused", refused)

    print("\n== everything else is re-encoded, upright, on white ==")
    flat = Image.new("RGBA", (60, 40), (255, 0, 0, 0))       # transparent, with red stored under it
    flat.paste(Image.new("RGBA", (20, 20), (0, 0, 255, 255)), (30, 10))
    alpha_png = save("alpha.png", flat, "PNG")
    data, _ = build([alpha_png])
    images = embedded_images(data)
    shown = decode(images[0][1])
    check("a PNG with alpha becomes a JPEG in RGB", shown.format == "JPEG" and shown.mode == "RGB"
          and "/DeviceRGB" in images[0][0] and "/DCTDecode" in images[0][0], (shown.format, shown.mode))
    shown = shown.convert("RGB")
    check("transparent pixels are white, not whatever was stored under them", near(shown.getpixel((5, 5)), (255, 255, 255), 8),
          shown.getpixel((5, 5)))
    check("opaque pixels are what they were", near(shown.getpixel((40, 20)), (0, 0, 255), 30), shown.getpixel((40, 20)))

    palette = Image.new("P", (20, 20), 0)
    palette.putpalette([255, 0, 0] + [0, 0, 0] * 255)
    palette.info["transparency"] = 0
    data, _ = build([save("palette.png", palette, "PNG", transparency=0)])
    check("a palette image with a transparent index is white there too",
          near(decode(embedded_images(data)[0][1]).convert("RGB").getpixel((3, 3)), (255, 255, 255), 8))

    sixteen = Image.new("I;16", (16, 16), 40000)
    data, _ = build([save("sixteen.png", sixteen, "PNG")])
    value = decode(embedded_images(data)[0][1]).convert("RGB").getpixel((3, 3))
    check("a 16-bit gray PNG is scaled, not clipped to a white page", near(value, (156, 156, 156), 12), value)

    cmyk = save("cmyk.jpg", Image.new("CMYK", (40, 40), (0, 255, 255, 0)), "JPEG", quality=92)
    cmyk_bytes = pathlib.Path(cmyk).read_bytes()
    data, _ = build([cmyk])
    images = embedded_images(data)
    shown = decode(images[0][1])
    check("a CMYK JPEG is not embedded as it is: it is converted", cmyk_bytes not in data and shown.mode == "RGB"
          and "/DeviceRGB" in images[0][0] and "/DeviceCMYK" not in images[0][0], (shown.mode, images[0][0]))
    check("and its colour survives (CMYK 0,255,255,0 is red)", near(shown.getpixel((5, 5)), (255, 0, 0), 30),
          shown.getpixel((5, 5)))

    print("\n== the way up it was taken ==")
    wrong = []
    for orientation in range(1, 9):
        path = save(f"orient{orientation}.jpg", corner_marked((80, 60)), "JPEG", quality=95,
                    exif=exif_with(orientation))
        data, _ = build([path])
        with Image.open(path) as source:
            want = ImageOps.exif_transpose(source)
            want.load()
        got = decode(embedded_images(data)[0][1]).convert("RGB")
        page = pages(data)[0]
        same_size = got.size == want.size and page == (float(want.size[0]), float(want.size[1]))
        w, h = want.size
        probes = [(w // 8, h // 8), (w - w // 8 - 1, h // 8), (w // 8, h - h // 8 - 1), (w - w // 8 - 1, h - h // 8 - 1)]
        same_pixels = same_size and all(near(got.getpixel(p), want.convert("RGB").getpixel(p), 45) for p in probes)
        if not (same_size and same_pixels):
            wrong.append((orientation, got.size, want.size, page))
        if orientation == 1:
            check("orientation 1 is embedded as it came", pathlib.Path(path).read_bytes() in data)
    check("all eight EXIF orientations come out as Pillow's own exif_transpose has them -- size, page and corners",
          not wrong, wrong)
    rotated = save("rotated.jpg", corner_marked((40, 20)), "JPEG", quality=95, exif=exif_with(6))
    data, _ = build([rotated])
    check("a 40 x 20 photo stored on its side (orientation 6) is a 20 x 40 page, width and height swapped",
          pages(data) == [(20.0, 40.0)], pages(data))
    check("and an EXIF-turned JPEG is not embedded as it came", pathlib.Path(rotated).read_bytes() not in data)

    print("\n== page sizes ==")
    wide = save("wide.png", Image.new("L", (20000, 100), 90), "PNG")
    tall = save("tall.png", Image.new("L", (100, 20000), 90), "PNG")
    data, _ = build([wide, tall])
    check("a side past 14400 points is scaled down to it, proportions kept: 20000 x 100 is 14400 x 72",
          pages(data) == [(14400.0, 72.0), (72.0, 14400.0)], pages(data))
    check("a small image is not scaled up or down: 120 x 80 pixels are 120 x 80 points",
          pages(build([rgb])[0]) == [(120.0, 80.0)])
    check("the structure of a two-page PDF holds too", not structure_problems(data), structure_problems(data))

    print("\n== past 20 megapixels ==")
    big = save("big.png", Image.new("L", (5001, 4000), 7), "PNG")         # 20.004 MP
    loaded = []
    from PIL import ImageFile
    real_load, real_file_load = Image.Image.load, ImageFile.ImageFile.load
    Image.Image.load = lambda self, *a, **k: loaded.append(1) or real_load(self, *a, **k)
    ImageFile.ImageFile.load = lambda self, *a, **k: loaded.append(1) or real_file_load(self, *a, **k)
    try:
        try:
            build([big], "big.pdf")
            outcome = None
        except merge.PictureTooLarge as exc:
            outcome = exc
    finally:
        Image.Image.load, ImageFile.ImageFile.load = real_load, real_file_load
    check("an image over 20 megapixels is refused", isinstance(outcome, merge.PictureTooLarge), outcome)
    check("from its header: not one pixel of it was decoded", not loaded, len(loaded))
    check("and the limit is 20 megapixels, not something near it", merge.MAX_MEGAPIXELS == 20)
    fits = save("fits.png", Image.new("L", (5000, 4000), 7), "PNG")      # exactly 20.000 MP
    try:
        data, _ = build([fits], "fits.pdf")
        accepted = pages(data) == [(5000.0, 4000.0)]
    except merge.MergeError:
        accepted = False
    check("exactly 20 megapixels is fine", accepted)

    real_limit, real_cap = merge.MAX_MEGAPIXELS, Image.MAX_IMAGE_PIXELS
    try:
        merge.MAX_MEGAPIXELS = 0.01                                          # 10,000 pixels, for small fakes
        small_ok = save("s100.png", Image.new("RGB", (100, 100), 1), "PNG")     # 10,000
        small_bad = save("s101.png", Image.new("RGB", (101, 100), 1), "PNG")    # 10,100
        build([small_ok], "edge.pdf")
        try:
            build([small_bad], "edge.pdf")
            edge = False
        except merge.PictureTooLarge:
            edge = True
        check("the limit is the pixel count and its edge is exact: 10,000 passes, 10,100 does not", edge)
        merge.MAX_MEGAPIXELS = real_limit
        Image.MAX_IMAGE_PIXELS = 1000                  # Pillow's own bomb guard trips at twice this
        try:
            build([small_ok], "bomb.pdf")
            guarded = False
        except merge.PictureTooLarge:
            guarded = True
        check("and Pillow's own decompression-bomb error is a refusal too, not a crash", guarded)
    finally:
        merge.MAX_MEGAPIXELS, Image.MAX_IMAGE_PIXELS = real_limit, real_cap

    print("\n== a PDF is streamed, one decoded image at a time ==")
    paths = [save(f"n{i}.png", noise((300, 200), seed=i), "PNG") for i in range(6)]
    gc.collect()
    opened, real_open = [], Image.open
    out = FIXTURES / "stream.pdf"

    def watching_open(*args, **kwargs):
        gc.collect()
        live = sum(1 for o in gc.get_objects() if isinstance(o, Image.Image))
        opened.append((live, os.path.getsize(out) if out.exists() else 0))
        return real_open(*args, **kwargs)

    baseline = sum(1 for o in gc.get_objects() if isinstance(o, Image.Image))
    Image.open = watching_open
    try:
        merge.build_pdf(paths, str(out))
    finally:
        Image.open = real_open
    live = [n - baseline for n, _ in opened]
    sizes = [size for _, size in opened]
    check("each image is opened twice: for its header, before anything is written, and to build its page",
          len(opened) == 12 and all(size == 0 for size in sizes[:6]), sizes)
    check("and when the next one is opened the last is gone: no decoded image is kept", max(live) <= 0, live)
    sizes = sizes[6:]
    check("the file is already on disk, and growing, while the later images are still being read",
          all(b > a for a, b in zip(sizes, sizes[1:])) and sizes[-1] > 5 * 1000, sizes)
    check("and it is a whole PDF at the end", not structure_problems(out.read_bytes()), structure_problems(out.read_bytes()))

    print("\n== the file ==")
    check("a PDF with nothing in it is refused", _raises(merge.NothingToMerge, merge.build_pdf, [], str(FIXTURES / "none.pdf")))
    check("so is a file that is not an image at all: no photograph to build a page from",
          _raises(merge.NothingToMerge, merge.build_pdf, [str(_write("text.jpg", b"not a picture"))],
                  str(FIXTURES / "text.pdf")))
    check("and nothing was written for it", not (FIXTURES / "text.pdf").exists())


def _raises(kind, function, *args):
    try:
        function(*args)
    except kind:
        return True
    except Exception:
        return False
    return False


def _write(name, content):
    path = FIXTURES / name
    path.write_bytes(content)
    return path


def render_checks():
    print("\n== PyMuPDF opens it, and sees what was put in ==")
    ex = exif_with(6)
    inputs = [
        (save("m1.jpg", gradient((120, 80)), "JPEG"), (120, 80), None),
        (save("m2.jpg", gradient((50, 60), "L"), "JPEG", progressive=True), (50, 60), None),
        (save("m3.png", Image.new("RGBA", (30, 30), (255, 0, 0, 0)), "PNG"), (30, 30), (255, 255, 255)),
        (save("m4.jpg", Image.new("CMYK", (40, 40), (0, 255, 255, 0)), "JPEG"), (40, 40), (255, 0, 0)),
        (save("m5.jpg", corner_marked((40, 20)), "JPEG", quality=95, exif=ex), (20, 40), None),
        (save("m6.webp", Image.new("RGB", (64, 48), (0, 200, 0)), "WEBP"), (64, 48), (0, 200, 0)),
        (save("m7.gif", Image.new("P", (32, 32), 0), "GIF"), (32, 32), None),
    ]
    data, out = build([path for path, _, _ in inputs], "mixed.pdf")
    doc = pymupdf.open(str(out))
    try:
        check("the page count is the number of images", doc.page_count == len(inputs), doc.page_count)
        check("opening it needed no repair: the cross-reference table is right", not doc.is_repaired)
        ratios = [(round(page.rect.width / page.rect.height, 3), round(w / h, 3))
                  for page, (_, (w, h), _) in zip(doc, inputs)]
        check("every page has the aspect ratio of its (upright) image", all(a == b for a, b in ratios), ratios)
        check("and its size in points", all((round(p.rect.width), round(p.rect.height)) == size
                                            for p, (_, size, _) in zip(doc, inputs)),
              [(p.rect.width, p.rect.height) for p in doc])
        wrong = []
        for number, (page, (_, size, colour)) in enumerate(zip(doc, inputs)):
            if colour is None:
                continue
            pixel = page.get_pixmap(alpha=False).pixel(size[0] // 2, size[1] // 2)
            if not near(pixel, colour, 30):
                wrong.append((number + 1, pixel, colour))
        check("rendered, the pages show the colours: white under alpha, red from CMYK, green from a WEBP",
              not wrong, wrong)
        marked = doc[4].get_pixmap(alpha=False)       # 40 x 20 stored on its side: red top-left block, turned
        top, bottom = marked.pixel(10, 5), marked.pixel(10, 35)
        # orientation 6 turns the stored picture a quarter clockwise: its top-left corner goes to the top-right.
        corner = marked.pixel(marked.width - 4, 4)
        check("rendered, the EXIF-turned photo is upright (the red corner went to the top right)",
              near(corner, (255, 0, 0), 60) and near(marked.pixel(3, marked.height - 4), (0, 0, 255), 60),
              (corner, top, bottom))
    finally:
        doc.close()
    scaled = build([save("w.png", Image.new("L", (20000, 100), 9), "PNG")], "scaled.pdf")[1]
    doc = pymupdf.open(str(scaled))
    try:
        check("a page scaled to 14400 keeps the image's proportions in PyMuPDF too",
              round(doc[0].rect.width) == 14400 and round(doc[0].rect.width / doc[0].rect.height) == 200
              and not doc.is_repaired, (doc[0].rect, doc.is_repaired))
    finally:
        doc.close()


# ---------------------------------------------------------------------------
# What is a photo, and the JPEG walk
# ---------------------------------------------------------------------------

@contextmanager
def counting_decodes():
    """Yields a list with one entry for every pixel decode started inside the block."""
    from PIL import ImageFile
    loaded = []
    real_load, real_file_load = Image.Image.load, ImageFile.ImageFile.load
    Image.Image.load = lambda self, *a, **k: loaded.append(1) or real_load(self, *a, **k)
    ImageFile.ImageFile.load = lambda self, *a, **k: loaded.append(1) or real_file_load(self, *a, **k)
    try:
        yield loaded
    finally:
        Image.Image.load, ImageFile.ImageFile.load = real_load, real_file_load


def refusal_of(path):
    """(what build_pdf([path]) raised or None, how many decodes it started)."""
    with counting_decodes() as loaded:
        try:
            merge.build_pdf([path], str(FIXTURES / "refused.pdf"))
            outcome = None
        except Exception as exc:
            outcome = exc
    return outcome, len(loaded)


def refused_before_open(path):
    """(what build_pdf([path]) raised or None, how many times Pillow was asked to open anything)."""
    opened, real_open = [], Image.open
    Image.open = lambda *a, **k: opened.append(a) or real_open(*a, **k)
    try:
        try:
            merge.build_pdf([path], str(FIXTURES / "refused.pdf"))
            outcome = None
        except Exception as exc:
            outcome = exc
    finally:
        Image.open = real_open
    return outcome, len(opened)


def webp_header(width, height, animated):
    """The 30 bytes a WEBP with a VP8X chunk starts with: RIFF, size, WEBP, VP8X, 10, flags, canvas less one."""
    return (b"RIFF" + (22).to_bytes(4, "little") + b"WEBPVP8X" + (10).to_bytes(4, "little")
            + bytes([0x02 if animated else 0, 0, 0, 0]) + (width - 1).to_bytes(3, "little")
            + (height - 1).to_bytes(3, "little"))


def gif_header(width, height):
    """GIF89a, and the logical screen descriptor: the canvas."""
    return b"GIF89a" + width.to_bytes(2, "little") + height.to_bytes(2, "little") + b"\x00\x00\x00"


class GrowsOnLoad:
    """A picture whose header says one size and whose decoder makes another
    (Pillow's own plugins do not do this today; a header is only a file's word)."""

    def __init__(self, real, grown):
        self._real, self._grown, self._loaded = real, grown, False

    @property
    def size(self):
        return self._grown if self._loaded and self._grown else self._real.size

    def load(self):
        self._loaded = True
        return self._real.load()

    def __getattr__(self, name):
        return getattr(self._real, name)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._real.close()


def format_checks():
    print("\n== only a still JPEG, PNG, WEBP or GIF is a photo ==")
    still = {kind: save(f"still.{kind.lower()}", gradient((60, 40)), kind) for kind in ("JPEG", "PNG", "WEBP", "GIF")}
    data, _ = build(list(still.values()), "four.pdf")
    check("a still JPEG, PNG, WEBP and GIF each make a page", pages(data) == [(60.0, 40.0)] * 4, pages(data))
    check("and the four formats are what merge.PHOTO_FORMATS says", set(merge.PHOTO_FORMATS) == set(still),
          merge.PHOTO_FORMATS)

    # Named like a photo, and not one. Pillow's open() takes ~40 formats whatever the name says.
    icon = save("icon.jpg", gradient((48, 32)), "ICO")
    bmp = save("bitmap.jpg", gradient((30, 20)), "BMP")
    tiff = save("scan.jpg", gradient((30, 20)), "TIFF")
    with Image.open(icon) as probe:
        taken_for = probe.format
    check("(the fixture: with no formats= Pillow takes an ICO called .jpg for an ICO)", taken_for == "ICO", taken_for)
    for label, path in (("an ICO called .jpg", icon), ("a BMP called .jpg", bmp), ("a TIFF called .jpg", tiff)):
        outcome, decodes = refusal_of(path)
        check(f"{label} is not a photo, found before a pixel is decoded: nothing is left to build",
              isinstance(outcome, merge.NothingToMerge) and decodes == 0 and _raises(merge.NotAPhoto, merge._open_photo, path),
              (outcome, decodes))
    words = str(_write("words.png", b"<html>not a picture</html>"))
    check("so is a file that is not a picture at all",
          _raises(merge.NothingToMerge, merge.build_pdf, [words], str(FIXTURES / "words.pdf"))
          and _raises(merge.NotAPhoto, merge._open_photo, words))
    check("NotAPhoto is a MergeError and not a PictureTooLarge (the bot's own words for that one are about size)",
          issubclass(merge.NotAPhoto, merge.MergeError) and not issubclass(merge.NotAPhoto, merge.PictureTooLarge))

    # Animations: a WEBP or GIF claims a canvas of the whole picture for every frame.
    red, blue = Image.new("RGB", (40, 30), (255, 0, 0)), Image.new("RGB", (40, 30), (0, 0, 255))
    canvas = [Image.new("RGB", (2000, 1500), (10, 200, 30)), Image.new("RGB", (2000, 1500), (200, 10, 30))]
    animated = {
        "an animated GIF": save("moving.gif", red, "GIF", save_all=True, append_images=[blue], duration=50),
        "an animated PNG": save("moving.png", red, "PNG", save_all=True, append_images=[blue], duration=50),
        "an animated WEBP": save("moving.webp", red, "WEBP", save_all=True, append_images=[blue], duration=50,
                                 lossless=True),
        "an animated WEBP with a 3 MP canvas in a few hundred bytes":
            save("canvas.webp", canvas[0], "WEBP", save_all=True, append_images=canvas[1:], duration=50, lossless=True),
    }
    for label, path in animated.items():
        with Image.open(path) as probe:
            really = bool(getattr(probe, "is_animated", False))
        outcome, decodes = refusal_of(path)
        check(f"{label} is not a photo, with no pixel decoded: nothing is left to build",
              really and isinstance(outcome, merge.NothingToMerge) and decodes == 0, (really, outcome, decodes))

    # Some Pillow versions build a WEBP's animation decoder, with its canvases, inside open() (a 96-byte file
    # claiming 20 MP measured 403 MB): so the header is read first, and Pillow is not asked at all.
    fakes = {
        "an animated WEBP with a 100 x 100 canvas": (webp_header(100, 100, True), merge.NothingToMerge),
        "a 96-byte animated WEBP claiming a 4470 x 4470 canvas (19.98 MP)": (webp_header(4470, 4470, True), merge.NothingToMerge),
        "a still WEBP claiming a 5001 x 4000 canvas (20.004 MP)": (webp_header(5001, 4000, False), merge.PictureTooLarge),
        "an animated WEBP claiming 5001 x 4000": (webp_header(5001, 4000, True), merge.PictureTooLarge),
        "a GIF whose screen is 5001 x 4000": (gif_header(5001, 4000), merge.PictureTooLarge),
        "a GIF whose screen is 65535 x 65535": (gif_header(65535, 65535), merge.PictureTooLarge),
    }
    for label, (header, wanted) in fakes.items():
        outcome, opens = refused_before_open(str(_write("header.bin", header)))
        check(f"{label}: judged from its first bytes ({wanted.__name__}), and Pillow is never asked to open it",
              isinstance(outcome, wanted) and opens == 0, (outcome, opens))
    outcome, opens = refused_before_open(animated["an animated WEBP"])
    check("a real animated WEBP is judged from its header too, without Pillow opening it",
          isinstance(outcome, merge.NothingToMerge) and opens == 0, (outcome, opens))
    outcome, opens = refused_before_open(animated["an animated GIF"])
    check("an animated GIF is known from its second image descriptor, found by the walk of the file: Pillow is never asked, "
          "and it is left out",
          isinstance(outcome, merge.NothingToMerge) and opens == 0, (outcome, opens))
    outcome, opens = refused_before_open(str(_write("small.bin", gif_header(40, 30) + b"\x2c" + bytes(8) + b"\x00\x02\x00;")))
    check("a header that says nothing wrong is passed on to Pillow (a GIF screen of 40 x 30, WEBP flags of a still)",
          opens >= 1 and not isinstance(outcome, merge.PictureTooLarge), (outcome, opens))
    outcome, opens = refused_before_open(str(_write("small2.bin", webp_header(40, 30, False) + b"VP8 \x00\x00\x00\x00")))
    check("...and so is a still WEBP's VP8X (here a header with an empty picture chunk behind it, which Pillow then cannot read)",
          opens == 1 and not isinstance(outcome, merge.PictureTooLarge), (outcome, opens))
    alpha_webp = save("alpha.webp", Image.new("RGBA", (30, 20), (1, 2, 3, 4)), "WEBP")     # alpha: written with a VP8X
    outcome, opens = refused_before_open(alpha_webp)
    check("a real still WEBP with a VP8X chunk (it has alpha) is not refused: opened to be judged, and again to be built",
          outcome is None and opens == 2, (outcome, opens))

    print("\n== the odd one out is left out of the PDF, as a video is ==")
    odds = {"an ICO called .jpg": icon, "a BMP called .jpg": bmp, "a TIFF called .jpg": tiff, "a text file called .png": words,
            "an animated GIF": animated["an animated GIF"], "an animated PNG": animated["an animated PNG"],
            "an animated WEBP": animated["an animated WEBP"],
            "a WEBP header that says animated": str(_write("odd_header.webp", webp_header(100, 100, True)))}
    narrow = save("narrow.jpg", gradient((30, 50)), "JPEG")
    for label, odd in odds.items():
        try:
            data, _ = build([still["JPEG"], odd, narrow], "mixed.pdf")
            ok = pages(data) == [(60.0, 40.0), (30.0, 50.0)] and not structure_problems(data)
            detail = pages(data)
        except Exception as exc:
            ok, detail = False, exc
        check(f"{label} between two photographs is left out: a PDF of the two, in order, whole", ok, detail)
    try:
        data, _ = build([odds["an ICO called .jpg"], still["JPEG"], odds["an animated GIF"], still["PNG"], bmp, narrow], "mixed2.pdf")
        ok, detail = pages(data) == [(60.0, 40.0), (60.0, 40.0), (30.0, 50.0)], pages(data)
    except Exception as exc:
        ok, detail = False, exc
    check("three odd files among three photographs: the three photographs, in order", ok, detail)
    target = FIXTURES / "one_left.pdf"
    check("one photograph and one odd file: one is not enough for a PDF (NothingToMerge), and nothing is written",
          _raises(merge.NothingToMerge, merge.build_pdf, [still["JPEG"], icon], str(target)) and not target.exists())
    check("one photograph on its own is still a PDF of one page, as it was: only a skipped file brings in the minimum",
          pages(build([still["JPEG"]], "single.pdf")[0]) == [(60.0, 40.0)])
    target = FIXTURES / "toobig.pdf"
    check("a picture that is too big is not left out: it refuses the whole PDF, written or not",
          _raises(merge.PictureTooLarge, merge.build_pdf,
                  [still["JPEG"], str(_write("big_header.webp", webp_header(5001, 4000, False))), narrow], str(target))
          and not target.exists())

    # A phone's MPO has two pictures and is one photo.
    second = Image.new("RGB", (64, 48), (0, 255, 0))
    phone = save("phone.jpg", corner_marked((64, 48)), "MPO", save_all=True, append_images=[second])
    with Image.open(phone) as probe:
        seen = (probe.format, getattr(probe, "n_frames", 1))
    check("(the fixture: Pillow sees an MPO of two frames, and calls it animated)", seen == ("MPO", 2), seen)
    try:
        data, _ = build([phone, still["JPEG"]], "phone.pdf")
        first = embedded_images(data)[0][1]
        check("a phone's MPO is one photo: a page, of the first picture only, not refused as an animation",
              pages(data) == [(64.0, 48.0), (60.0, 40.0)] and decode(first).size == (64, 48)
              and len(first) < pathlib.Path(phone).stat().st_size, (pages(data), len(first)))
    except merge.MergeError as exc:
        check("a phone's MPO is one photo: a page, of the first picture only, not refused as an animation", False, exc)
    turned = save("phone6.jpg", corner_marked((40, 20)), "MPO", save_all=True, append_images=[second], exif=exif_with(6))
    try:
        data, _ = build([turned], "phone6.pdf")
        check("also when it has to be turned (re-encoded by Pillow rather than passed through)",
              pages(data) == [(20.0, 40.0)], pages(data))
    except merge.MergeError as exc:
        check("also when it has to be turned (re-encoded by Pillow rather than passed through)", False, exc)

    # The size is read again once the decoder has made the picture.
    real_open = Image.open
    try:
        Image.open = lambda path, *a, **k: GrowsOnLoad(real_open(path, *a, **k), (6000, 4000))
        try:
            merge.build_pdf([still["PNG"]], str(FIXTURES / "grown.pdf"))
            grown = None
        except merge.PictureTooLarge as exc:
            grown = exc
        Image.open = lambda path, *a, **k: GrowsOnLoad(real_open(path, *a, **k), None)
        merge.build_pdf([still["PNG"]], str(FIXTURES / "same.pdf"))
        same = True
    finally:
        Image.open = real_open
    check("a picture that is 24 MP once decoded is refused, though its header said 60 x 40",
          isinstance(grown, merge.PictureTooLarge), grown)
    check("and one that is the size it said goes through (the stand-in itself is not what refuses)", same)

    # Pillow's own global limit is neither read nor needed: other bots in the process set it, ConvertBot to None.
    real_limit, real_cap = merge.MAX_MEGAPIXELS, Image.MAX_IMAGE_PIXELS
    try:
        merge.MAX_MEGAPIXELS, Image.MAX_IMAGE_PIXELS = 0.01, None
        small_bad = save("s101b.png", Image.new("RGB", (101, 100), 1), "PNG")        # 10,100 pixels
        outcome, decodes = refusal_of(small_bad)
    finally:
        merge.MAX_MEGAPIXELS, Image.MAX_IMAGE_PIXELS = real_limit, real_cap
    check("with Pillow's own limit switched off (MAX_IMAGE_PIXELS = None) the bot's limit still refuses, undecoded",
          isinstance(outcome, merge.PictureTooLarge) and decodes == 0, (outcome, decodes))


def reference_layout(data: bytes):
    """The marker walk as it was before it moved into the regex engine: the same
    answers are what is asked of the new one. (Kept in pure Python on purpose.)"""
    n = len(data)
    if data[:2] != b"\xff\xd8":
        return None
    pos, frame = 2, None
    while pos + 2 <= n:
        if data[pos] != 0xFF:
            return None
        marker = data[pos + 1]
        if marker == 0xFF:
            pos += 1
            continue
        if marker == 0xD9:
            return frame + (pos + 2,) if frame else None
        if marker == 0x00:
            return None
        if marker == 0x01 or 0xD0 <= marker <= 0xD8:
            pos += 2
            continue
        if pos + 4 > n:
            return None
        length = int.from_bytes(data[pos + 2:pos + 4], "big")
        if length < 2:
            return None
        body = data[pos + 4:pos + 2 + length]
        if marker in (0xC0, 0xC2):
            if len(body) < 6 or frame is not None or body[0] != 8:
                return None
            height, width = int.from_bytes(body[1:3], "big"), int.from_bytes(body[3:5], "big")
            if not width or not height or body[5] not in (1, 3):
                return None
            frame = (width, height, body[5])
        elif marker in (0xC1, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            return None
        pos += 2 + length
        if marker == 0xDA:
            if frame is None:
                return None
            while True:
                pos = data.find(b"\xff", pos)
                if pos < 0 or pos + 1 >= n:
                    return None
                follower = data[pos + 1]
                if follower == 0x00 or 0xD0 <= follower <= 0xD7:
                    pos += 2
                elif follower == 0xFF:
                    pos += 1
                else:
                    break
    return None


def cpu_seconds(function, *args):
    start = time.process_time()
    result = function(*args)
    return result, time.process_time() - start


def walk_checks():
    print("\n== the JPEG walk: the answers it always gave, found in C ==")
    def jpeg_bytes(image, **kwargs):
        buffer = io.BytesIO()
        image.save(buffer, "JPEG", **kwargs)
        return buffer.getvalue()

    icc = b"\x00" * 3000
    shapes = {
        "baseline 4:2:0": jpeg_bytes(gradient((70, 50)), quality=85),
        "baseline 4:4:4": jpeg_bytes(gradient((70, 50)), quality=85, subsampling=0),
        "grayscale": jpeg_bytes(gradient((70, 50), "L")),
        "progressive": jpeg_bytes(gradient((70, 50)), progressive=True),
        "progressive 4:4:4 with an EXIF turn": jpeg_bytes(gradient((70, 50)), progressive=True, subsampling=0,
                                                         exif=exif_with(6)),
        "optimized tables": jpeg_bytes(gradient((70, 50)), optimize=True),
        "with an ICC profile": jpeg_bytes(gradient((70, 50)), icc_profile=icc),
        "noise (a 0xFF every few bytes in the scan)": jpeg_bytes(noise((64, 48), seed=5), quality=95),
        "CMYK": jpeg_bytes(Image.new("CMYK", (40, 40), (0, 255, 255, 0))),
    }
    try:
        shapes["restart markers"] = jpeg_bytes(noise((64, 48), seed=6), quality=90, restart_marker_blocks=2)
    except (TypeError, ValueError):
        skip("restart markers", "this Pillow cannot write them")
    base = shapes["baseline 4:2:0"]
    shapes["trailing data after the EOI"] = base + b"\xff\xd8 a second picture, or just a blob " * 20
    shapes["fill bytes before the scan"] = base.replace(b"\xff\xda", b"\xff\xff\xff\xff\xda", 1)
    shapes["cut in the middle"] = base[:len(base) // 2]
    shapes["cut just before the EOI"] = base[:-2]
    shapes["a lone 0xFF at the end"] = base[:-2] + b"\xff"
    shapes["not a JPEG"] = b"\x89PNG\r\n\x1a\n" + base
    shapes["empty"] = b""
    buffer = io.BytesIO()
    gradient((64, 48)).save(buffer, "MPO", save_all=True, append_images=[gradient((32, 24))])
    shapes["an MPO of two pictures"] = buffer.getvalue()

    differing = [name for name, data in shapes.items() if merge._jpeg_layout(data) != reference_layout(data)]
    check("on every shape of file the answer is the old walk's", not differing, differing)
    answers = {name: merge._jpeg_layout(data) for name, data in shapes.items()}
    sizes = {"baseline 4:2:0": (70, 50), "baseline 4:4:4": (70, 50), "grayscale": (70, 50), "progressive": (70, 50),
             "progressive 4:4:4 with an EXIF turn": (70, 50), "optimized tables": (70, 50),
             "with an ICC profile": (70, 50), "noise (a 0xFF every few bytes in the scan)": (64, 48)}
    if "restart markers" in shapes:
        sizes["restart markers"] = (64, 48)
    broken = [name for name, size in sizes.items()
              if not (answers[name] and answers[name][:2] == size and answers[name][3] == len(shapes[name]))]
    check("and a valid JPEG still is one: its size, and its length to the EOI", not broken, broken)
    check("a gray JPEG has one component, a colour one three",
          answers["grayscale"][2] == 1 and answers["baseline 4:2:0"][2] == 3, (answers["grayscale"], answers["baseline 4:2:0"]))
    check("what follows the EOI is not part of it: the length is the first picture's",
          answers["trailing data after the EOI"][3] == len(base), answers["trailing data after the EOI"])
    check("a JPEG cut short, a lone 0xFF at the end, and a file that is not a JPEG are not embedded",
          answers["cut in the middle"] is None and answers["a lone 0xFF at the end"] is None
          and answers["not a JPEG"] is None and answers["empty"] is None)
    check("and four components (CMYK) are not DCTDecode's RGB or gray", answers["CMYK"] is None)
    check("the first picture of an MPO ends where its first EOI does",
          answers["an MPO of two pictures"] is not None
          and answers["an MPO of two pictures"][3] < len(shapes["an MPO of two pictures"]))

    # The same answers on files that are no longer valid: every kind of damage, from a seeded dice.
    rng = random.Random(20261008)
    interesting = [0x00, 0x01, 0xC0, 0xC1, 0xC2, 0xC4, 0xD0, 0xD7, 0xD8, 0xD9, 0xDA, 0xDB, 0xE0, 0xE1, 0xFE, 0xFF]
    different = []
    tried = 0
    for name, original in shapes.items():
        if not original or len(original) > 20000:
            continue
        for _ in range(250):
            data = bytearray(original)
            for _ in range(rng.randint(1, 4)):
                where = rng.randrange(len(data) + 1)
                kind = rng.randrange(6)
                if kind == 0 and data:
                    data[min(where, len(data) - 1)] = rng.choice(interesting)
                elif kind == 1:
                    data[where:where] = b"\xff" * rng.randint(1, 40)
                elif kind == 2:
                    data[where:where] = bytes([0xFF, rng.choice(interesting)])
                elif kind == 3:
                    del data[where:where + rng.randint(1, 30)]
                elif kind == 4:
                    del data[where:]
                else:
                    data[where:where] = rng.randbytes(rng.randint(1, 20))
            tried += 1
            if merge._jpeg_layout(bytes(data)) != reference_layout(bytes(data)):
                different.append((name, bytes(data[:40])))
    check(f"and on {tried} damaged files, every answer is the old walk's", not different, different[:2])

    # The files the old walk could not be given: 48 MB of 0xFF took it 14 s. Measured in CPU time, so that
    # other work on the machine does not decide it; the old walk needs more than 10 s on the first of these alone.
    sos = bytes.fromhex("FFDA000C03010002110311003F00")
    head = b"\xff\xd8" + bytes.fromhex("FFC00011080176017603012200021101031101") + sos
    hostile = {
        "48 MB of 0xFF inside a scan": lambda: head + b"\xff" * (48 * MB),
        "24M stuffed 0xFF00 pairs inside a scan": lambda: head + b"\xff\x00" * (24 * MB),
        "48 MB of fill bytes after the SOI": lambda: b"\xff\xd8" + b"\xff" * (48 * MB),
        "12M four-byte segments after the SOI": lambda: b"\xff\xd8" + b"\xff\xe0\x00\x02" * (12 * MB),
    }
    for label, make in hostile.items():
        data = make()
        result, spent = cpu_seconds(merge._jpeg_layout, data)
        check(f"{label}: no picture, and under 2 s of CPU (took {spent:.2f} s)", result is None and spent < 2.0,
              (result, spent))
        del data


def memoryview_checks():
    print("\n== a JPEG that is the start of its file is not copied ==")
    picture = io.BytesIO()
    noise((1400, 1000), seed=9).save(picture, "JPEG", quality=95)
    picture = picture.getvalue()
    path = _write("tail.jpg", picture + b"\x00" * 64)
    gc.collect()
    tracemalloc.start()
    try:
        width, height, colour, jpeg = merge._prepare(str(path))
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    size = len(picture) + 64
    check(f"the picture goes in as a view of the file's bytes (a memoryview), equal to the picture",
          isinstance(jpeg, memoryview) and bytes(jpeg) == picture and (width, height) == (1400, 1000), type(jpeg))
    check(f"and reading it holds about one copy of the file, not two ({peak / MB:.1f} MB for a {size / MB:.1f} MB file)",
          peak < 1.3 * size, (peak, size))
    del jpeg
    whole = _write("whole.jpg", picture)
    jpeg = merge._prepare(str(whole))[3]
    check("a file that is the picture and nothing else goes in as it is", bytes(jpeg) == picture)
    out_path = FIXTURES / "tail.pdf"
    merge.build_pdf([str(path)], str(out_path))
    check("and the PDF written from the view has exactly the picture's bytes in it",
          embedded_images(out_path.read_bytes())[0][1] == picture)


# ---------------------------------------------------------------------------
# The ZIP, and the names
# ---------------------------------------------------------------------------

def zip_checks():
    print("\n== the ZIP: stored, and named by position ==")
    sources = {}
    for name, content in (("a1b2.JPG", b"first"), ("c3d4.mp4", b"second" * 1000), ("e5f6.weird.name.PNG", b"third"),
                          ("noextension", b"fourth"), ("nice.toolongext", b"fifth"),
                          ("x.jpg;rm -rf", b"sixth")):
        sources[str(_write(name, content))] = content
    paths = list(sources)
    out = FIXTURES / "out.zip"
    count = merge.build_zip(paths, str(out))
    with zipfile.ZipFile(out) as archive:
        names = archive.namelist()
        check("an entry is named for its position and its file's own extension: 01.jpg, 02.mp4, 03.png",
              names[:3] == ["01.jpg", "02.mp4", "03.png"], names)
        check("a file with no extension, a long one, or a strange one is .bin -- never carried over",
              names[3:] == ["04.bin", "05.bin", "06.bin"], names)
        check("every file went in, in order, byte for byte",
              count == 6 and [archive.read(n) for n in names] == list(sources.values()))
        check("stored, not compressed: they are compressed already",
              all(i.compress_type == zipfile.ZIP_STORED for i in archive.infolist()))
        check("and the archive is sound", archive.testzip() is None)
        check("no entry has a path in it", all("/" not in n and "\\" not in n and ".." not in n for n in names))
        check("nor the server's file modes and times: one mode for all, today's date",
              {i.external_attr >> 16 for i in archive.infolist()} == {0o644}
              and all(i.date_time[0] >= datetime.now(timezone.utc).year for i in archive.infolist()))
    many = [str(_write(f"many{n}.jpg", b"x")) for n in range(101)]
    merge.build_zip(many, str(FIXTURES / "many.zip"))
    with zipfile.ZipFile(FIXTURES / "many.zip") as archive:
        names = archive.namelist()
        check("past 99 files the numbers widen, and still sort in order", names[0] == "001.jpg"
              and names[-1] == "101.jpg" and names == sorted(names), names[:2] + names[-1:])
    check("a ZIP with nothing in it is refused", _raises(merge.NothingToMerge, merge.build_zip, [], str(FIXTURES / "n.zip")))

    print("\n== the name the person sees ==")
    when = datetime(2026, 10, 7, 14, 32, 5, tzinfo=timezone.utc)
    check("<platform>_<YYYYMMDD-HHMM>.pdf, in UTC", merge.output_name("instagram", "pdf", when) == "instagram_20261007-1432.pdf")
    check("and the same with .zip", merge.output_name("twitter", "zip", when) == "twitter_20261007-1432.zip")
    check("a platform that is not plainly one is cut down to letters and digits",
          merge.output_name("../x y/Tik-Tok", "zip", when) == "xytiktok_20261007-1432.zip"
          and merge.output_name("", "pdf", when).startswith("album_"))
    check("the default is the time now", re.fullmatch(r"tiktok_\d{8}-\d{4}\.pdf", merge.output_name("tiktok", "pdf")))


# ---------------------------------------------------------------------------
# The buttons
# ---------------------------------------------------------------------------

def button_data(button):
    return getattr(button, "callback_data", None) or getattr(button, "kwargs", {}).get("callback_data")


def button_text(button):
    return getattr(button, "text", None) or (getattr(button, "args", None) or [None])[0]


def row_data(markup):
    return [[button_data(b) for b in row] for row in (markup.inline_keyboard if markup else [])]


def legacy_state_checks():
    print("\n== a delivery saved by 2.1.0 (spent: true) is still answered ==")
    # 2.1.0 stored a bare True once its redo button was used; user_data is saved in Postgres and
    # survives an upgrade. `action in True` is a TypeError: the tap got no answer, only a spinner.
    old = {"platform": "instagram", "url": "https://x/y", "lossless": False, "photos": 3, "items": 3, "spent": True}
    try:
        kb = bot._remembered_kb("en", "tok", old)
        shown = "ok"
    except Exception as exc:
        kb, shown = None, repr(exc)
    check("its keyboard is drawn, with the redo button already gone", shown == "ok" and kb is not None
          and not any("redo" in str(button_data(b)) for row in kb.inline_keyboard for b in row), shown)
    try:
        first = bot._spend_action(old, "pdf")
        second = bot._spend_action(old, "pdf")
        bot._unspend_action(old, "pdf")
        third = bot._spend_action(old, "pdf")
        redo = bot._spend_action(old, "redo")
        outcome = (first, second, third, redo, old["spent"])
    except Exception as exc:
        outcome = repr(exc)
    check("a PDF tap is taken once, given back, taken again; the old redo stays spent",
          outcome == (True, False, True, False, ["redo", "pdf"]), outcome)


def keyboard_checks():
    print("\n== which buttons a delivery gets ==")
    token = bot._remember_delivery(SimpleNamespace(user_data={}), "instagram", "https://instagram.com/p/x", False)
    redo = f"redo:{token}"
    cases = [
        ("three photos: both", dict(photos=3, items=3), [f"merge:pdf:{token}", f"merge:zip:{token}"]),
        ("two photos: both", dict(photos=2, items=2), [f"merge:pdf:{token}", f"merge:zip:{token}"]),
        ("one photo and a video: ZIP only -- there is one page to make, and two things to carry",
         dict(photos=1, items=2), [f"merge:zip:{token}"]),
        ("a photo and nothing else: no row", dict(photos=1, items=1), None),
        ("a single video: no row", dict(photos=0, items=1), None),
        ("two videos: ZIP only", dict(photos=0, items=2), [f"merge:zip:{token}"]),
    ]
    for name, counts, want in cases:
        rows = row_data(bot._nudge_kb("en", token, False, **counts))
        merge_rows = [r for r in rows if r and r[0].startswith("merge:")]
        check(f"{name}", (merge_rows[0] if merge_rows else None) == want and len(merge_rows) <= 1, rows)
    rows = row_data(bot._nudge_kb("en", token, False, photos=3, items=3))
    check("the redo button is still the first row, and the merge buttons the next", rows[0] == [redo]
          and rows[1][0].startswith("merge:pdf:"), rows)
    check("the buttons say 📄 PDF and 🗜 ZIP",
          [button_text(b) for b in bot._nudge_kb("en", token, False, photos=3, items=3).inline_keyboard[1]]
          == ["📄 PDF", "🗜 ZIP"])
    sizes = [len(d.encode()) for r in rows for d in r]
    check("callback data fits Telegram's 64 bytes: the longest is 20 (merge:pdf: and a 10-character token)",
          len(token) == 10 and max(sizes) == 20 and max(sizes) < 64 and len(rows[1][0]) == len("merge:pdf:") + len(token),
          (sizes, token))
    check("without a token there are no buttons at all (nothing to find the link by)",
          row_data(bot._nudge_kb("en", None, False, photos=3, items=3)) == [])
    check("and the spent keyboard (what a tap leaves) has neither",
          not any(d.startswith(("merge:", "redo:")) for r in row_data(bot._nudge_kb()) for d in r))


# ---------------------------------------------------------------------------
# The real handlers, with the routes, the database and Telegram faked
# ---------------------------------------------------------------------------

class World:
    def __init__(self, items=(), payloads=None, caption=False, send_fails=False, free=10_000, preset=False):
        self.items = list(items)            # MediaItem, in post order
        self.payloads = dict(payloads or {})
        self.caption, self.send_fails, self.free = caption, send_fails, free
        self.preset = preset                # a route (yt-dlp) that has already put the files on disk
        self.resolves, self.downloads, self.sent, self.shown, self.texts, self.jobs, self.calls = [], [], [], [], [], [], []
        self.deleted = 0
        self.spawned = []
        self.allowance = {"allowed": True}


W = World()


class Message:
    async def reply_document(self, document, *args, **kwargs):
        if W.send_fails:
            raise RuntimeError("Telegram said no")
        W.sent.append(dict(kwargs, file=document.name, data=document.read(), existed=os.path.exists(document.name)))

    async def reply_text(self, text, **kwargs):
        W.texts.append((text, kwargs.get("reply_markup")))

    async def reply_media_group(self, group, **kwargs):
        W.texts.append(("<album>", group))

    async def reply_photo(self, photo, **kwargs):
        W.texts.append(("<photo>", kwargs.get("reply_markup")))


class Status:
    async def set(self, bot_, text, **kwargs):
        W.shown.append(text)
        return self

    async def delete(self, *args, **kwargs):
        W.deleted += 1


class FakeLive:
    @classmethod
    async def reply_to(cls, message, text, **kwargs):
        W.shown.append(text)
        return Status()


@asynccontextmanager
async def no_busy(*args, **kwargs):
    yield


async def english(user_id, context=None):
    return "en"


async def fake_resolve(platform, url, max_mb=None):
    W.resolves.append((platform, url))
    items = [dataclasses.replace(each, path=None) for each in W.items]      # a route answers with new ones
    if W.preset:
        for each in items:
            path = SCRATCH / f"{uuid.uuid4().hex}.{resolvers._ext_of(each.filename, 'jpg')}"
            path.write_bytes(b"preset")
            each.path = str(path)
    return resolvers.Resolved(platform, "fake", items)


async def fake_download(item, work_dir=None, max_mb=None):
    if item.path:                       # what resolvers.download() does for a file a route already fetched
        return item.path
    W.downloads.append(item.url)
    payload = W.payloads[item.url]
    if isinstance(payload, Exception):
        raise payload
    path = SCRATCH / f"{uuid.uuid4().hex}.{resolvers._ext_of(item.filename, 'jpg')}"
    path.write_bytes(payload)
    item.path = str(path)
    return str(path)


async def nothing(*args, **kwargs):
    return None


def _fake_allowance(user_id):
    W.calls.append("allowance")
    return W.allowance


def _fake_record(user_id, platform):
    W.calls.append(("record", platform))


def _fake_spawn(context, update, work):
    W.calls.append("spawn")
    W.spawned.append(work)


resolvers.resolve, resolvers.download = fake_resolve, fake_download
bot.LiveMessage = FakeLive
bot.lifecycle.busy = no_busy
bot.get_caption_enabled = lambda user_id: W.caption
bot.get_lossless_enabled = lambda user_id: False
bot.note_job = lambda ok: W.jobs.append(ok)
bot._after_send = nothing
bot._free_mb = lambda: W.free
bot.download_allowance = _fake_allowance
bot.record_download = _fake_record
bot._spawn_download = _fake_spawn
i18n.get_lang = english

USER, CHAT = 7, 7
MESSAGE = Message()


def item(kind, name, filename=None):
    return resolvers.MediaItem(kind, url=f"https://cdn.example/{name}", filename=filename or name)


def update_with(query=None, user=USER):
    return SimpleNamespace(callback_query=query, effective_user=SimpleNamespace(id=user),
                           effective_chat=SimpleNamespace(id=user), effective_message=MESSAGE, message=MESSAGE)


def context_with(**delivery):
    return SimpleNamespace(bot=SimpleNamespace(), user_data=delivery)


def left_on_disk():
    return sorted(p.name for p in SCRATCH.iterdir())


def clean_scratch():
    for p in SCRATCH.iterdir():
        p.unlink()


def say(key, **fields):
    return i18n.t("en", key, items=bot.MERGE_MAX_ITEMS, mb=bot.MERGE_MAX_MB, mp=merge.MAX_MEGAPIXELS, **fields)


async def run_merge(world, kind, platform="instagram"):
    global W
    W = world
    clean_scratch()
    try:
        return await asyncio.wait_for(
            bot._merge_and_send(update_with(), context_with(), kind, platform, "https://instagram.com/p/x"), 8)
    except asyncio.TimeoutError:
        # A gate left shut by an earlier build would hold every later PDF for ever; say so, and go on with a fresh one.
        check("a merge ends (it was held up for 8 s: a PDF gate nobody let go of?)", False)
        merge._pdf_gate = None
        return False


async def flow_checks():
    global W
    real =(bot.MERGE_MAX_ITEMS, bot.MERGE_MAX_MB, merge.MAX_MEGAPIXELS, large_files.BOT_API_SEND_BYTES)
    check("the limits default to 20 files and 48 MB", real[:2] == (20, 48), real[:2])
    bot.MERGE_MAX_ITEMS, bot.MERGE_MAX_MB = 3, 1                  # small fakes

    print("\n== a ZIP, end to end ==")
    items = [item("photo", "p1", "../../etc/SECRET_NAME.jpg"), item("video", "v1", "clip.mp4"),
             item("photo", "p2", "x/../y.jpg")]
    payloads = {i.url: bytes([n]) * 300_000 for n, i in enumerate(items)}      # 0.86 MB in all
    sent = await run_merge(World(items, payloads), "zip")
    check("a ZIP of a three-file post is sent", sent and len(W.sent) == 1, W.shown)
    doc = W.sent[0] if W.sent else {}
    check("as a document, with content-type detection off, under a proper name",
          doc.get("disable_content_type_detection") is True
          and re.fullmatch(r"instagram_\d{8}-\d{4}\.zip", doc.get("filename") or ""), doc.get("filename"))
    with zipfile.ZipFile(io.BytesIO(doc.get("data", b""))) as archive:
        names = archive.namelist()
        check("whose entries are named by position and extension, not by what the source called them",
              names == ["01.jpg", "02.mp4", "03.jpg"], names)
        check("holding the three files in order",
              [archive.read(n) for n in names] == [payloads[i.url] for i in items])
    check("the file existed when it was sent, and nothing at all is left on disk afterwards",
          doc.get("existed") and left_on_disk() == [], left_on_disk())
    check("the status line is cleared, the job counts as done, nothing was said to the person",
          W.deleted == 1 and W.jobs == [True] and W.texts == [], (W.deleted, W.jobs, W.texts))
    check("the post was fetched again, once, as the link it was", W.resolves == [("instagram", "https://instagram.com/p/x")])
    check("no caption unless /caption is on, and then the bot's own", doc.get("caption") is None)
    await run_merge(World(items, payloads, caption=True), "zip")
    check("with /caption on, the same caption the normal delivery has",
          W.sent and W.sent[0].get("caption") == i18n.t("en", "download_credit_caption", username=bot.BOT_USERNAME),
          W.sent[:1])

    print("\n== the limits, with small fakes (3 files, 1 MB) ==")
    four = [item("photo", f"q{n}") for n in range(4)]
    tiny = {i.url: b"x" for i in four}
    built = []
    real_zip = merge.build_zip
    merge.build_zip = lambda *a, **k: built.append(1) or real_zip(*a, **k)
    try:
        sent = await run_merge(World(four, tiny), "zip")
        check("four files over a limit of three: refused, and not one was fetched", not sent and W.downloads == [],
              W.downloads)
        check("the person is told the limits, with the code that says it is not a fault",
              W.shown[-1] == say("merge_too_big") and W.shown[-1].endswith("DL-MERGE-TOO-BIG")
              and "3 files" in W.shown[-1] and "1 MB" in W.shown[-1], W.shown[-1])
        check("nothing was built, sent or left on disk", not built and not W.sent and left_on_disk() == [])
        check("it is not counted against the bot as a failed job", True not in W.jobs and False not in W.jobs, W.jobs)

        sent = await run_merge(World(four[:3], tiny), "zip")
        check("exactly the limit's worth of files is fine", sent and len(W.sent) == 1, W.shown)

        bot.MERGE_MAX_ITEMS = 5                                         # so the count is not what stops it
        sized = [item("photo", f"s{n}") for n in range(5)]
        big = {i.url: b"z" * 400_000 for i in sized}                   # 0.38 MB each: the 3rd takes it to 1.14 MB
        built.clear()
        sent = await run_merge(World(sized, big), "zip")
        check("past 1 MB in all: refused", not sent and not W.sent and not built, W.shown[-1:])
        check("fetching stopped at the file that passed it: three fetched, not five", len(W.downloads) == 3,
              len(W.downloads))
        check("said, with the same code", W.shown[-1] == say("merge_too_big"), W.shown[-1])
        check("and everything fetched was deleted", left_on_disk() == [], left_on_disk())
        bot.MERGE_MAX_ITEMS = 3

        half = [item("photo", f"h{n}") for n in range(3)]
        exact = {half[0].url: b"a" * (MB // 2), half[1].url: b"b" * (MB // 2), half[2].url: b""}
        sent = await run_merge(World(half, exact), "zip")
        check("a total of exactly 1 MB has not passed it", sent and len(W.sent) == 1, W.shown[-1:])
    finally:
        merge.build_zip = real_zip

    check("a file over the per-file ceiling (the fetch refuses it) is the same message, and clears up",
          not await run_merge(World(four[:2], {four[0].url: b"ok", four[1].url: net.TooLarge("too big")}), "zip")
          and W.shown[-1] == say("merge_too_big") and left_on_disk() == [], W.shown[-1:])
    check("a route that finds the post too big says so in the same words",
          not await _run_with_resolve_error(resolvers.NothingWorked("instagram", {
              "x": resolvers.ProviderFailed("big", "too_big")}))
          and W.shown[-1] == say("merge_too_big"), W.shown[-1:])

    preset = [item("photo", f"u{n}") for n in range(4)]
    sent = await run_merge(World(preset, preset=True), "zip")
    check("files a route had already put on disk (yt-dlp does) are deleted when the merge refuses the post",
          not sent and W.shown[-1] == say("merge_too_big") and left_on_disk() == [], left_on_disk())
    sent = await run_merge(World(preset[:3], preset=True), "zip")
    with zipfile.ZipFile(io.BytesIO(W.sent[0]["data"] if W.sent else b"")) as archive:
        entries = archive.namelist()
    check("and when it goes through, they are what is zipped, and are deleted too",
          sent and entries == ["01.jpg", "02.jpg", "03.jpg"] and left_on_disk() == [], (entries, left_on_disk()))

    bot.MERGE_MAX_ITEMS, bot.MERGE_MAX_MB = 20, 48
    twenty = [item("photo", f"t{n}") for n in range(20)]
    sent = await run_merge(World(twenty, {i.url: b"t" for i in twenty}), "zip")
    check("at the real limits, twenty files go through", sent and len(W.sent) == 1, W.shown[-1:])
    twenty_one = twenty + [item("photo", "t20")]
    sent = await run_merge(World(twenty_one, {i.url: b"t" for i in twenty_one}), "zip")
    check("and twenty-one do not", not sent and W.downloads == [] and W.shown[-1] == say("merge_too_big"), W.shown[-1:])
    bot.MERGE_MAX_ITEMS, bot.MERGE_MAX_MB = 3, 1

    print("\n== a PDF: the photos, and only the photos ==")
    if not PIL_OK:
        skip("PDF flows", "Pillow is not installed")
    else:
        photos = [item("photo", "a1", "one.jpg"), item("video", "v", "clip.mp4"), item("photo", "a2", "two.jpg")]
        pictures = {}
        for n, size in ((0, (64, 48)), (2, (30, 50))):
            buffer = io.BytesIO()
            gradient(size).save(buffer, "JPEG")
            pictures[photos[n].url] = buffer.getvalue()
        pictures[photos[1].url] = b"VIDEO BYTES MUST NEVER BE FETCHED FOR A PDF"
        sent = await run_merge(World(photos, pictures), "pdf")
        check("a PDF of a post with two photos and a video is sent", sent and len(W.sent) == 1, W.shown)
        check("the video was never fetched", photos[1].url not in W.downloads and len(W.downloads) == 2, W.downloads)
        doc = W.sent[0] if W.sent else {}
        check("under a .pdf name, as a document, with detection off",
              re.fullmatch(r"instagram_\d{8}-\d{4}\.pdf", doc.get("filename") or "")
              and doc.get("disable_content_type_detection") is True, doc.get("filename"))
        check("it is a PDF of two pages, the size of the two photos, in post order",
              pages(doc.get("data", b"")) == [(64.0, 48.0), (30.0, 50.0)], pages(doc.get("data", b"")))
        check("and its structure is sound", not structure_problems(doc.get("data", b"")))
        check("nothing left on disk", left_on_disk() == [], left_on_disk())

        one_photo = [item("photo", "b1"), item("video", "b2", "x.mp4"), item("video", "b3", "y.mp4")]
        sent = await run_merge(World(one_photo, {i.url: b"x" for i in one_photo}), "pdf")
        check("one photo among videos: no PDF, and nothing is fetched", not sent and W.downloads == [] and not W.sent,
              W.downloads)
        check("the person is told why, with a code that offers no Report button",
              W.shown[-1] == say("merge_no_photos") and W.shown[-1].endswith("DL-MERGE-NO-PHOTOS")
              and not problems.reportable("DL-MERGE-NO-PHOTOS"), W.shown[-1])
        sent = await run_merge(World(one_photo, {i.url: b"x" for i in one_photo}), "zip")
        check("the same post as a ZIP is fine", sent and len(W.sent) == 1, W.shown[-1:])

        good = pictures[photos[0].url]
        html = b"<html>not a picture</html>"
        bad = [item("photo", "c1"), item("photo", "c2")]
        sent = await run_merge(World(bad, {bad[0].url: good, bad[1].url: html}), "pdf")
        check("a 'photo' that is not an image is left out; one photo left is no PDF: nothing is sent", not sent and not W.sent)
        check("the person is told 'no photos', a code that offers no Report button -- not that something went wrong",
              W.shown[-1] == say("merge_no_photos") and W.shown[-1].endswith("DL-MERGE-NO-PHOTOS")
              and not problems.reportable("DL-MERGE-NO-PHOTOS"), W.shown[-1])
        check("it is not counted as a failed job, and everything it fetched is gone",
              W.jobs == [] and left_on_disk() == [], (W.jobs, left_on_disk()))
        everything_odd = [item("photo", "d1"), item("photo", "d2")]
        sent = await run_merge(World(everything_odd, {i.url: html for i in everything_odd}), "pdf")
        check("an album of nothing but odd files is the same ending", not sent and W.shown[-1] == say("merge_no_photos")
              and W.jobs == [] and left_on_disk() == [], (W.shown[-1:], W.jobs))
        mixed = [item("photo", "e1"), item("photo", "e2"), item("photo", "e3")]
        sent = await run_merge(World(mixed, {mixed[0].url: good, mixed[1].url: html, mixed[2].url: good}), "pdf")
        check("a post of two photos and an odd file gets its PDF, of the two photos", sent and len(W.sent) == 1
              and pages(W.sent[0]["data"]) == [(64.0, 48.0), (64.0, 48.0)] and not structure_problems(W.sent[0]["data"]),
              (W.shown[-2:], [pages(s["data"]) for s in W.sent]))
        check("with nothing said to the person, counted as a done job, and nothing left on disk",
              W.texts == [] and W.jobs == [True] and left_on_disk() == [], (W.texts, W.jobs, left_on_disk()))
        broken = [item("photo", "f1"), item("photo", "f2")]
        sent = await run_merge(World(broken, {broken[0].url: good, broken[1].url: good[:len(good) // 2]}), "pdf")
        check("a JPEG cut short is still a photograph that cannot be built: nothing is sent", not sent and not W.sent)
        check("the person is told it went wrong, with the code that DOES offer a Report button",
              W.shown[-1] == say("merge_failed") and W.shown[-1].endswith("DL-MERGE-FAILED")
              and problems.reportable("DL-MERGE-FAILED"), W.shown[-1])
        check("it counts as a failed job, and everything it fetched and half-built is gone",
              W.jobs == [False] and left_on_disk() == [], (W.jobs, left_on_disk()))

        real_mp = merge.MAX_MEGAPIXELS
        merge.MAX_MEGAPIXELS = 0.001                                 # 1,000 pixels: both photos are over
        try:
            sent = await run_merge(World(photos[::2], {photos[0].url: pictures[photos[0].url],
                                                       photos[2].url: pictures[photos[2].url]}), "pdf")
        finally:
            merge.MAX_MEGAPIXELS = real_mp
        check("a picture over the megapixel limit is refused with its own message, and cleared up",
              not sent and W.shown[-1] == i18n.t("en", "merge_picture_too_big", mp=0.001) and left_on_disk() == []
              and W.shown[-1].endswith("DL-MERGE-TOO-BIG"), W.shown[-1])

    print("\n== sending that cannot, and downloads that cannot ==")
    sent = await run_merge(World(items, payloads, send_fails=True), "zip")
    check("a send that fails says so, counts as failed, and leaves nothing on disk",
          not sent and W.shown[-1] == say("merge_failed") and W.jobs == [False] and left_on_disk() == [],
          (W.shown[-1:], W.jobs, left_on_disk()))
    sent = await run_merge(World(items, {**payloads, items[1].url: net.FetchError("That file is unavailable.")}), "zip")
    check("a file that cannot be fetched is told as the normal flow tells it, and the first one is deleted",
          not sent and W.shown[-1] == "That file is unavailable." and left_on_disk() == [], W.shown[-1:])
    sent = await run_merge(World(items, payloads, free=5), "zip")
    check("short on disk: nothing is fetched", not sent and W.resolves == [] and W.downloads == []
          and W.shown[-1] == i18n.t("en", "download_short_on_space"), W.shown[-1:])
    real_cap = large_files.BOT_API_SEND_BYTES
    large_files.BOT_API_SEND_BYTES = 100
    try:
        sent = await run_merge(World(items, payloads), "zip")
    finally:
        large_files.BOT_API_SEND_BYTES = real_cap
    check("a result over what the Bot API takes is refused as too big, not sent to fail", not sent and not W.sent
          and W.shown[-1] == say("merge_too_big") and left_on_disk() == [], W.shown[-1:])

    print("\n== PDFs are built one at a time; a ZIP is not held up behind them ==")
    lock = threading.Lock()
    seen = {"pdf_now": 0, "pdf_peak": 0, "zip_saw_pdf": False, "failing": False}

    def slow_pdf(paths, out_path):
        with lock:
            seen["pdf_now"] += 1
            seen["pdf_peak"] = max(seen["pdf_peak"], seen["pdf_now"])
        try:
            time.sleep(0.3)
            if seen["failing"]:
                raise RuntimeError("out of memory, say")
            pathlib.Path(out_path).write_bytes(b"%PDF-1.4 fake")
            return len(paths)
        finally:
            with lock:
                seen["pdf_now"] -= 1

    def quick_zip(paths, out_path):
        time.sleep(0.05)                    # the PDF thread has begun by now, if it is going to
        seen["zip_saw_pdf"] = seen["zip_saw_pdf"] or seen["pdf_now"] > 0
        pathlib.Path(out_path).write_bytes(b"PK fake")
        return len(paths)

    pair = [item("photo", "g1"), item("photo", "g2")]
    real_pdf, real_zip = merge.build_pdf, merge.build_zip
    merge.build_pdf, merge.build_zip = slow_pdf, quick_zip
    try:
        clean_scratch()
        W = World(pair, {i.url: b"x" for i in pair})
        began = time.perf_counter()
        results = await asyncio.gather(*(bot._merge_and_send(update_with(user=user), context_with(), "pdf", "instagram",
                                                              "https://instagram.com/p/x") for user in (101, 102, 103)))
        took = time.perf_counter() - began
        check("three people tap PDF at once: all three get their PDF", all(results) and len(W.sent) == 3, (results, W.shown[-3:]))
        check("but never two are being built at the same moment (three 20 MP WEBPs together measured 957 MB)",
              seen["pdf_peak"] == 1, seen["pdf_peak"])
        check("one after the other, so it took about three builds' time", took >= 0.85, took)
        check("the gate is open again afterwards, and nothing is left on disk",
              not merge.pdf_gate().locked() and left_on_disk() == [], left_on_disk())

        clean_scratch()
        W = World(pair, {i.url: b"x" for i in pair})
        seen["zip_saw_pdf"] = False
        results = await asyncio.gather(*(bot._merge_and_send(update_with(user=user), context_with(), kind, "instagram",
                                                              "https://instagram.com/p/x")
                                         for user, kind in ((111, "pdf"), (112, "pdf"), (113, "zip"))))
        check("a ZIP asked for while a PDF is being built is built at once, not made to wait for it",
              all(results) and seen["zip_saw_pdf"] and seen["pdf_peak"] == 1, (results, seen))

        seen["failing"] = True
        clean_scratch()
        W = World(pair, {i.url: b"x" for i in pair})
        failed = await bot._merge_and_send(update_with(user=121), context_with(), "pdf", "instagram", "https://x/p")
        check("a build that fails lets go of the gate...", not failed and not merge.pdf_gate().locked()
              and W.shown[-1] == say("merge_failed") and left_on_disk() == [], (W.shown[-1:], left_on_disk()))
        seen["failing"] = False
        clean_scratch()
        W = World(pair, {i.url: b"x" for i in pair})
        try:                                # a gate nobody let go of would wait here for ever
            again = await asyncio.wait_for(
                bot._merge_and_send(update_with(user=122), context_with(), "pdf", "instagram", "https://x/p"), 5)
        except asyncio.TimeoutError:
            again = False
        check("...so the next PDF is built", again and len(W.sent) == 1, W.shown[-2:])
    finally:
        merge.build_pdf, merge.build_zip = real_pdf, real_zip
        merge._pdf_gate = None              # a gate of its own for what follows, whatever this section found (a leaked one would hang it)
        clean_scratch()

    print("\n== the tap itself ==")
    token = "tok1234567"

    def new_delivery():
        # A tap spends its token, so every tap gets a delivery of its own unless a test is about two taps of one.
        return {"redeliver": {token: {"platform": "tiktok", "url": "https://vm.tiktok.com/x", "lossless": False,
                                      "photos": 3, "items": 4}}}

    async def tap(data, world=None, allowance=None, context=None, yielding=False):
        """`yielding`: Telegram's answer takes a moment, as it does for real, and another tap is handled meanwhile."""
        global W
        W = world or World()
        if allowance:
            W.allowance = allowance
        answers, edits = [], []

        async def answer(text=None, **kw):
            W.calls.append("answer")
            answers.append((text, kw))
            if yielding:
                await asyncio.sleep(0)

        async def edit(reply_markup=None):
            W.calls.append("edit")
            edits.append(reply_markup)
            if yielding:
                await asyncio.sleep(0)

        query = SimpleNamespace(data=data, message=MESSAGE, answer=answer, edit_message_reply_markup=edit)
        handler = bot.redeliver_callback if data.startswith("redo:") else bot.merge_callback
        await handler(update_with(query), context or context_with(**new_delivery()))
        return answers, edits

    answers, edits = await tap(f"merge:pdf:{token}")
    check("a tap answers the query first, takes the buttons off, asks the allowance, records the download, then starts",
          W.calls == ["answer", "edit", "allowance", ("record", "tiktok"), "spawn"], W.calls)
    check("and the keyboard it leaves has no PDF button, but still the redo and the ZIP buttons",
          [d for r in row_data(edits[0]) for d in r] == [f"redo:{token}", f"merge:zip:{token}"], row_data(edits[0]))
    W.items = [item("photo", "k1"), item("photo", "k2")]
    for it in W.items:
        W.payloads[it.url] = pictures[photos[0].url] if PIL_OK else b"x"
    if PIL_OK:
        await W.spawned[0]()
        check("and what it starts is the merge of the link that was stored, for the platform that was stored",
              W.resolves == [("tiktok", "https://vm.tiktok.com/x")] and len(W.sent) == 1
              and W.sent[0]["filename"].startswith("tiktok_") and W.sent[0]["filename"].endswith(".pdf"),
              (W.resolves, [s.get("filename") for s in W.sent]))
    answers, edits = await tap(f"merge:zip:{token}")
    check("ZIP is the same tap with another word", W.calls[-1] == "spawn" and W.spawned)

    answers, edits = await tap("merge:pdf:nothere")
    check("an unknown token is answered with the expired-button pop-up, and nothing else happens",
          answers == [(i18n.t("en", "redeliver_expired"), {"show_alert": True})] and W.calls == ["answer"] and not edits,
          (answers, W.calls))
    check("which is the existing DL-BUTTON-EXPIRED", answers[0][0].endswith("DL-BUTTON-EXPIRED"))
    answers, edits = await tap(f"merge:rar:{token}")
    check("so is a kind that is neither pdf nor zip", answers and answers[0][1] == {"show_alert": True}
          and W.calls == ["answer"] and not edits, W.calls)

    out_of = {"allowed": False, "scope": "hour", "hour": 12, "hour_max": 12, "wait_min": 5, "donor": True}
    answers, edits = await tap(f"merge:pdf:{token}", allowance=out_of)
    check("somebody out of downloads is told so, is not recorded, and nothing starts; the keyboard is put back whole",
          W.calls == ["answer", "edit", "allowance", "edit"] and not W.spawned
          and W.texts and W.texts[-1][0] == bot._allowance_message("en", out_of)
          and [d for r in row_data(edits[-1]) for d in r] == [f"redo:{token}", f"merge:pdf:{token}", f"merge:zip:{token}"],
          (W.calls, W.texts[-1:], row_data(edits[-1]) if edits else None))

    print("\n== a double tap: each action once for an album, and the others still work ==")
    pdf_tap, zip_tap, redo_tap = f"merge:pdf:{token}", f"merge:zip:{token}", f"redo:{token}"
    expired = [(i18n.t("en", "redeliver_expired"), {"show_alert": True})]
    started = [(None, {})]

    def spent_of(delivery_context):
        return delivery_context.user_data["redeliver"][token].get("spent", [])

    def recorded(w):
        return [c for c in w.calls if isinstance(c, tuple)]

    context = context_with(**new_delivery())
    world = World()
    first, _ = await tap(pdf_tap, world=world, context=context)
    second, second_edits = await tap(pdf_tap, world=world, context=context)
    check("the first PDF tap starts the merge", first == started and world.calls.count("spawn") == 1, (first, world.calls))
    check("a second tap of the same action is answered as an expired button, and touches nothing",
          second == expired and not second_edits, second)
    check("one download recorded and one merge started, not two",
          world.calls.count("spawn") == 1 and recorded(world) == [("record", "tiktok")]
          and world.calls.count("allowance") == 1, world.calls)
    await tap(zip_tap, world=world, context=context)
    check("a different action on the same album still works: the ZIP after the PDF is a second merge and a second download",
          world.calls.count("spawn") == 2 and recorded(world) == [("record", "tiktok")] * 2, world.calls)
    again, _ = await tap(zip_tap, world=world, context=context)
    check("...once: its own second tap is expired too", again == expired and world.calls.count("spawn") == 2, again)
    await tap(redo_tap, world=world, context=context)
    check("the 'as a file' redelivery of the same album is a third action, and still works",
          world.calls.count("spawn") == 3 and recorded(world) == [("record", "tiktok")] * 3, world.calls)
    again, _ = await tap(redo_tap, world=world, context=context)
    check("...once: tapped twice it is expired the second time, and nothing more starts",
          again == expired and world.calls.count("spawn") == 3, again)
    again, _ = await tap(pdf_tap, world=world, context=context)
    check("the album stays in memory (the other actions need it); what is spent is the action",
          token in context.user_data["redeliver"] and spent_of(context) == ["pdf", "zip", "redo"] and again == expired,
          spent_of(context))
    check("(nothing is left to tap by then: the keyboard that last tap left has only the nudge row, or nothing)",
          not any(d.startswith(("merge:", "redo:")) for r in row_data(bot._remembered_kb("en", token, context.user_data["redeliver"][token]))
                  for d in r))
    check("and what is remembered of it can be saved as JSON (user_data is)",
          json.loads(json.dumps(context.user_data)) == context.user_data)

    context = context_with(**new_delivery())
    world = World()
    await tap(redo_tap, world=world, context=context)
    await tap(pdf_tap, world=world, context=context)
    check("the redelivery first, and the PDF is still there to ask for", world.calls.count("spawn") == 2, world.calls)

    context = context_with(**new_delivery())
    world = World()
    results = await asyncio.gather(tap(pdf_tap, world=world, context=context, yielding=True),
                                   tap(pdf_tap, world=world, context=context, yielding=True))
    began = [answers for answers, _ in results if answers == started]
    refused = [answers for answers, _ in results if answers == expired]
    check("both taps of one action in flight at once, each waiting on Telegram's answer: one starts the merge, "
          "the other is told it has expired",
          len(began) == 1 and len(refused) == 1 and world.calls.count("spawn") == 1
          and world.calls.count(("record", "tiktok")) == 1, (results, world.calls))
    context = context_with(**new_delivery())
    world = World()
    results = await asyncio.gather(tap(pdf_tap, world=world, context=context, yielding=True),
                                   tap(zip_tap, world=world, context=context, yielding=True),
                                   tap(redo_tap, world=world, context=context, yielding=True))
    check("three different actions in flight at once all start: spending one does not spend the others",
          all(answers == started for answers, _ in results) and world.calls.count("spawn") == 3
          and world.calls.count(("record", "tiktok")) == 3, (results, world.calls))

    context = context_with(**new_delivery())
    world = World()
    await tap(f"merge:rar:{token}", world=world, context=context)
    check("a tap that is not a pdf or a zip spends nothing", spent_of(context) == [], spent_of(context))
    await tap(pdf_tap, world=world, context=context)
    check("so the real one after it still works", world.calls.count("spawn") == 1 and spent_of(context) == ["pdf"],
          world.calls)
    context = context_with(**new_delivery())
    await tap("merge:pdf:" + token[::-1], world=World(), context=context)
    check("and a wrong token spends nothing of the right one", spent_of(context) == [])

    context = context_with(**new_delivery())
    world = World()
    await tap(pdf_tap, world=world, allowance=out_of, context=context)
    again, _ = await tap(pdf_tap, world=world, allowance=out_of, context=context)
    check("a tap by somebody out of downloads spends nothing: the action is given back, and can be tapped again",
          spent_of(context) == [] and "spawn" not in world.calls and again != expired
          and world.calls.count("allowance") == 2, (spent_of(context), world.calls, again))

    print("\n== the follow-up line after an album carries the buttons ==")
    async def delivered(items_with_kinds):
        global W
        W = World()
        entries = []
        for n, kind in enumerate(items_with_kinds):
            path = FIXTURES / f"album{n}.{'mp4' if kind == 'video' else 'jpg'}"
            path.write_bytes(b"x")
            entries.append((resolvers.MediaItem(kind, url="https://x", filename=path.name), str(path)))
        await bot._send_media(update_with(), context_with(), entries, "en", lossless=False, redeliver_token=token)
        return [kb for text, kb in W.texts if text == i18n.t("en", "album_delivered", count=len(entries))]

    for kinds, want in ((["photo"] * 3, ["merge:pdf:", "merge:zip:"]), (["photo", "video"], ["merge:zip:"]),
                        (["photo", "photo", "video"], ["merge:pdf:", "merge:zip:"]), (["video", "video"], ["merge:zip:"])):
        keyboards = await delivered(kinds)
        got = [d for r in row_data(keyboards[0]) for d in r if d.startswith("merge:")] if keyboards else None
        check(f"an album of {', '.join(kinds)}: " + " and ".join(w.rstrip(':') for w in want),
              got == [w + token for w in want], got)
    W = World()
    path = FIXTURES / "single.jpg"
    path.write_bytes(b"x")
    await bot._send_media(update_with(), context_with(), [(resolvers.MediaItem("photo", url="https://x", filename="s.jpg"),
                                                           str(path))], "en", lossless=False, redeliver_token=token)
    shown = [kb for text, kb in W.texts if text == "<photo>"]
    rows = row_data(shown[0]) if shown else None
    check("a single photo gets the redo button and no merge row",
          rows is not None and rows[0] == [f"redo:{token}"] and not any(d.startswith("merge:") for r in rows for d in r), rows)


async def _run_with_resolve_error(error):
    global W
    W = World()
    clean_scratch()

    async def failing(platform, url, max_mb=None):
        raise error

    real = resolvers.resolve
    resolvers.resolve = failing
    try:
        return await bot._merge_and_send(update_with(), context_with(), "zip", "instagram", "https://instagram.com/p/x")
    finally:
        resolvers.resolve = real


# ---------------------------------------------------------------------------
# Wiring, from the syntax tree
# ---------------------------------------------------------------------------

def function_named(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)) and node.name == name:
            return node
    return None


def calls_in(node, wanted):
    """Lines of every call to `wanted` -- a bare name, or the last part of a dotted one."""
    found = []
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            func = child.func
            label = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else None
            if label == wanted:
                found.append(child.lineno)
    return found


def mentions(node, wanted):
    """Whether `wanted` is a name, or the last part of a dotted one, anywhere in `node`."""
    return any((isinstance(c, ast.Name) and c.id == wanted) or (isinstance(c, ast.Attribute) and c.attr == wanted)
               for c in ast.walk(node))


def names_in(node, wanted):
    return sorted(c.lineno for c in ast.walk(node) if isinstance(c, ast.Name) and c.id == wanted)


def wiring_checks():
    print("\n== wiring, read from the source ==")
    source = (SRC / "bot.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    registered = False
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "CallbackQueryHandler"
                and node.args and isinstance(node.args[0], ast.Name) and node.args[0].id == "merge_callback"):
            pattern = [k.value for k in node.keywords if k.arg == "pattern"]
            registered = bool(pattern) and isinstance(pattern[0], ast.Constant) and pattern[0].value == "^merge:"
    check("the ^merge: handler is registered, to merge_callback", registered)

    callback = function_named(tree, "merge_callback")
    check("merge_callback exists", callback is not None)
    if callback is not None:
        allowance, record = names_in(callback, "download_allowance"), names_in(callback, "record_download")
        starts = sorted(names_in(callback, "_spawn_download") + names_in(callback, "_merge_and_send"))
        check("it asks download_allowance, then record_download, and only then starts anything",
              bool(allowance and record and starts) and allowance[0] < record[0] < starts[0],
              (allowance, record, starts))
        check("a refusal by the allowance returns before record_download",
              any(isinstance(n, ast.If) and "allowed" in ast.dump(n.test)
                  and any(isinstance(x, ast.Return) for x in n.body) and n.lineno < record[0]
                  for n in ast.walk(callback)) if record else False)
        check("an unknown token is answered with redeliver_expired",
              "redeliver_expired" in ast.dump(callback))
    work = function_named(tree, "_merge_and_send")
    check("_merge_and_send exists", work is not None)
    if work is not None:
        covering = [t for t in ast.walk(work) if isinstance(t, ast.Try) and t.finalbody
                    and any(calls_in(s, "_remove_files") or calls_in(s, "remove") or calls_in(s, "unlink")
                            for s in t.finalbody)]
        def inside(name):
            return any(mentions(ast.Module(body=t.body, type_ignores=[]), name) for t in covering)

        check("every file is removed in a `finally`", bool(covering))
        check("which covers the downloading, the building and the sending",
              inside("download") and inside("build_pdf_queued") and inside("build_zip") and inside("reply_document"),
              (inside("download"), inside("build_pdf_queued"), inside("build_zip"), inside("reply_document")))
        check("it sends one document, without content-type sniffing",
              any(isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute) and c.func.attr == "reply_document"
                  and any(k.arg == "disable_content_type_detection" and getattr(k.value, "value", None) is True
                          for k in c.keywords) for c in ast.walk(work)))
        check("the caption is the /caption setting's", bool(names_in(work, "get_caption_enabled")))
        check("the work goes through the slots and the free-space check the normal flow uses",
              bool(names_in(work, "_slots") and names_in(work, "_user_slot") and names_in(work, "_free_mb")
                   and names_in(work, "MIN_FREE_MB")))
        check("it fetches with resolvers.download, the function every download uses",
              bool(calls_in(work, "download")))
        check("both limits are read in it, from the module's constants",
              bool(names_in(work, "MERGE_MAX_ITEMS") and names_in(work, "MERGE_MAX_MB")))
        remote = [c.lineno for c in ast.walk(work) if isinstance(c, ast.Attribute) and c.attr == "filename"]
        merge_tree = ast.parse((SRC / "merge.py").read_text(encoding="utf-8"))
        remote += [c.lineno for c in ast.walk(merge_tree) if isinstance(c, ast.Attribute) and c.attr == "filename"]
        check("no name from the other end of the wire is read: not one .filename, here or in merge.py", not remote, remote)

    constants = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name) \
                and node.targets[0].id in ("MERGE_MAX_ITEMS", "MERGE_MAX_MB"):
            constants[node.targets[0].id] = node.value
    for name, variable, default in (("MERGE_MAX_ITEMS", "DBOT_MERGE_MAX_ITEMS", 20), ("MERGE_MAX_MB", "DBOT_MERGE_MAX_MB", 48)):
        node = constants.get(name)
        if node is None:
            check(f"{name} is a constant of the bot", False)
            continue

        def evaluate(environment):
            return eval(compile(ast.Expression(node), "<constant>", "eval"),
                        {"os": SimpleNamespace(environ=environment)})

        check(f"{name} is {default}, unless {variable} says otherwise",
              evaluate({}) == default and evaluate({variable: "7"}) == 7 and evaluate({variable: ""}) == default,
              (evaluate({}), evaluate({variable: "7"})))
    example = (REPO / "downloader_bot" / ".env.example").read_text(encoding="utf-8")
    check("both are in .env.example, with their defaults",
          re.search(r"(?m)^#\s*DBOT_MERGE_MAX_ITEMS=20\s*$", example) and re.search(r"(?m)^#\s*DBOT_MERGE_MAX_MB=48\s*$", example))

    # The words that state the megapixel limit are the limit the code has.
    readme = (REPO / "downloader_bot" / "README.md").read_text(encoding="utf-8")
    decoded = " ".join(" ".join(part) for part in (problems.PROBLEMS["DL-MERGE-TOO-BIG"],
                                                   problems.PROBLEMS["DL-MERGE-FAILED"]))
    for where, text in ((".env.example", example), ("the README", readme), ("the decoding of DL-MERGE-*", decoded)):
        stated = re.findall(r"(\d+(?:\.\d+)?)\s*(?:megapixels|MP\b)", text)
        check(f"{where} states the megapixel limit the code has ({merge.MAX_MEGAPIXELS}), and no other",
              stated and all(float(n) == merge.MAX_MEGAPIXELS for n in stated), stated)
    row = re.search(r"At most (\d+) files and (\d+) MB", readme)
    check("the README row for the buttons gives the file and MB limits the bot has (20 and 48)",
          row and (int(row.group(1)), int(row.group(2))) == (20, 48), row and row.groups())
    check("and says what the buttons are, one PDF of the photos or one ZIP of the files, and that nothing is kept",
          re.search(r"(?m)^\|[^|]*📄 PDF[^|]*🗜 ZIP[^|]*\|[^|]*PDF[^|]*ZIP[^|]*nothing kept", readme))

    merge_tree = ast.parse((SRC / "merge.py").read_text(encoding="utf-8"))
    check("merge.py does not use warnings.catch_warnings: it swaps a process-wide list, from a pool thread",
          not calls_in(merge_tree, "catch_warnings") and not mentions(merge_tree, "warnings"))
    check("and does not read or set Pillow's MAX_IMAGE_PIXELS: other bots set that global, ConvertBot to None",
          not mentions(merge_tree, "MAX_IMAGE_PIXELS"))
    prepare = function_named(merge_tree, "_open_photo")
    check("Image.open is given the formats, in _open_photo (the one place a picture is opened)",
          prepare is not None and any(isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute) and c.func.attr == "open"
                                      and any(k.arg == "formats" for k in c.keywords) for c in ast.walk(prepare)))
    check("bot.py says 'no photos', not 'failed', when build_pdf finds fewer than two (NothingToMerge, NotAPhoto)",
          work is not None and any(
              isinstance(h, ast.ExceptHandler) and h.type is not None and mentions(h.type, "NothingToMerge")
              and mentions(h.type, "NotAPhoto") and "merge_no_photos" in ast.dump(ast.Module(body=h.body, type_ignores=[]))
              for h in ast.walk(work)))
    queued = function_named(merge_tree, "build_pdf_queued")
    check("build_pdf_queued takes the pdf_gate, builds in a thread kept as a task shielded from the caller, and lets go "
          "of the gate in that task's done-callback -- not in an `async with`, which a cancelled caller leaves early",
          queued is not None and bool(calls_in(queued, "pdf_gate") and calls_in(queued, "acquire")
                                      and calls_in(queued, "to_thread") and calls_in(queued, "shield")
                                      and calls_in(queued, "add_done_callback") and calls_in(queued, "release"))
          and not any(isinstance(n, ast.AsyncWith) for n in ast.walk(queued)))
    gate = function_named(merge_tree, "pdf_gate")
    check("and the gate is a Semaphore of one, made when first wanted",
          gate is not None and any(isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
                                   and c.func.attr == "Semaphore" and len(c.args) == 1
                                   and getattr(c.args[0], "value", None) == 1 for c in ast.walk(gate)))
    check("the ZIP does not wait for it: bot.py builds it with a bare to_thread",
          work is not None and not mentions(work, "build_pdf") and calls_in(work, "build_pdf_queued")
          and any(isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute) and c.func.attr == "to_thread"
                  and c.args and isinstance(c.args[0], ast.Attribute) and c.args[0].attr == "build_zip"
                  for c in ast.walk(work)))

    print("\n== the words, and the codes ==")
    table = i18n.STRINGS
    keys = ("merge_too_big", "merge_picture_too_big", "merge_no_photos", "merge_failed")
    for lang in ("en", "uz", "ru"):
        check(f"{lang}: the four merge messages exist", all(table[lang].get(k) for k in keys),
              [k for k in keys if not table[lang].get(k)])
        text = table[lang]["help_text"]
        check(f"{lang}: the help text mentions the two buttons, in one line",
              "📄 PDF" in text and "🗜 ZIP" in text
              and len([l for l in text.split("\n") if "PDF" in l and "ZIP" in l]) == 1)
    informal = re.compile(r"\b(?:пришли|возьми|ты|тебе|тебя|тобой|твой|твоя|твоё|твои|твоих|напиши|нажми|отправь|"
                          r"попробуй|жди)\b", re.IGNORECASE)
    found = [(key, match.group()) for key in keys for match in informal.finditer(table["ru"][key])]
    check("ru: the merge messages speak to the reader as вы, never ты (пришли, возьми, твой ...)", not found, found)
    check("ru: and where they ask for something it is the formal way: Пришлите, возьмите, пришлите",
          "Пришлите ссылку" in table["ru"]["merge_no_photos"] and "возьмите файлы" in table["ru"]["merge_no_photos"]
          and "пришлите ссылку" in table["ru"]["merge_failed"])
    check("uz and ru are their own sentences, not the English",
          all(table[l][k] != table["en"][k] for l in ("uz", "ru") for k in keys + ("help_text",)))
    check("and every one of them says the numbers it needs",
          all("{items}" in table[l]["merge_too_big"] and "{mb}" in table[l]["merge_too_big"]
              and "{mp}" in table[l]["merge_picture_too_big"] for l in ("en", "uz", "ru")))
    codes = {key: problems.code_for("downloader_bot", key) for key in keys}
    check("the keys carry DL-MERGE-TOO-BIG (twice), DL-MERGE-NO-PHOTOS and DL-MERGE-FAILED",
          codes == {"merge_too_big": "DL-MERGE-TOO-BIG", "merge_picture_too_big": "DL-MERGE-TOO-BIG",
                    "merge_no_photos": "DL-MERGE-NO-PHOTOS", "merge_failed": "DL-MERGE-FAILED"}, codes)
    check("too big and no photos are simple (no Report button): not a fault, and the person can act",
          {"DL-MERGE-TOO-BIG", "DL-MERGE-NO-PHOTOS"} <= problems.SIMPLE
          and not problems.reportable("DL-MERGE-TOO-BIG") and not problems.reportable("DL-MERGE-NO-PHOTOS"))
    check("failed is not: the person cannot fix it, so the button appears",
          "DL-MERGE-FAILED" not in problems.SIMPLE and problems.reportable("DL-MERGE-FAILED"))
    check("each is decoded, and the two simple ones sit in a kind",
          all(c in problems.PROBLEMS for c in ("DL-MERGE-TOO-BIG", "DL-MERGE-NO-PHOTOS", "DL-MERGE-FAILED"))
          and problems.kind("DL-MERGE-TOO-BIG") and problems.kind("DL-MERGE-NO-PHOTOS")
          and problems.kind("DL-MERGE-FAILED") is None)


# ---------------------------------------------------------------------------
# A header that is mostly junk, a picture that is too long, a WEBP that cannot be decoded
# ---------------------------------------------------------------------------

@contextmanager
def pillow_forbidden():
    """Inside the block Pillow is not to be asked to open anything: a refusal that comes
    from the file's own header never gets as far as open(), and a broken refusal fails
    here at once instead of sitting in Pillow's quadratic or memory-hungry header code."""
    real = Image.open

    def refuse(*args, **kwargs):
        raise RuntimeError("Pillow was asked to open it")

    Image.open = refuse
    try:
        yield
    finally:
        Image.open = real


def judged(path):
    """(what merge._check_header raised or None, seconds, peak bytes Python allocated meanwhile).
    Two runs: the time is taken without tracemalloc, which slows allocation down several times over."""
    began = time.perf_counter()
    try:
        merge._check_header(str(path))
        outcome = None
    except Exception as exc:
        outcome = exc
    took = time.perf_counter() - began
    tracemalloc.start()
    try:
        merge._check_header(str(path))
    except Exception:
        pass
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    return outcome, took, peak


def scan_checks():
    print("\n== a progressive JPEG of thousands of scans is refused from the file, before libjpeg runs them ==")
    # Review of 2026-10-10: 666 KB of 17-byte scans held the PDF gate for 7.9 s at 19 MP (0.26 ms a scan),
    # 48 MB would be ~13 minutes in a thread nobody can stop. A photo has about ten scans.
    check("the cap is a named constant, far above a photo's scans and far below a bomb's",
          50 <= merge.MAX_JPEG_SCANS <= 500, merge.MAX_JPEG_SCANS)
    buffer = io.BytesIO()
    gradient((64, 48)).save(buffer, "JPEG", progressive=True)
    real = buffer.getvalue()
    sos = b"\xff\xda\x00\x08\x01\x01\x00\x00\x3f\x00" + b"\x00\x00"
    first_scan = real.count(b"\xff\xda")
    check("an honest progressive JPEG has a handful of scans, well under the cap",
          1 < first_scan < merge.MAX_JPEG_SCANS // 4, first_scan)

    def with_scans(n):
        return real[:-2] + sos * n + real[-2:]

    def judge(data, name):
        path = SCRATCH / name
        path.write_bytes(data)
        began = time.perf_counter()
        try:
            merge._check_header(str(path))
            outcome = None
        except Exception as exc:
            outcome = exc
        return outcome, time.perf_counter() - began

    outcome, took = judge(real, "honest.jpg")
    check("the honest progressive JPEG passes the header check", outcome is None, outcome)
    room = merge.MAX_JPEG_SCANS - first_scan
    outcome, took = judge(with_scans(room), "at_cap.jpg")
    check("exactly at the cap it still passes", outcome is None, outcome)
    outcome, took = judge(with_scans(room + 1), "over_cap.jpg")
    check("one scan over the cap is refused as not a photo", isinstance(outcome, merge.NotAPhoto), outcome)
    outcome, took = judge(with_scans(30_000), "bomb.jpg")
    check(f"30,000 extra scans: refused as not a photo, in well under a second ({took:.3f} s)",
          isinstance(outcome, merge.NotAPhoto) and took < 0.5, (outcome, took))
    # A marker cut in two by a read boundary is still counted: put the FF of the first FF DA at the last
    # byte of the first 1 MiB piece and its DA at the first byte of the second.
    body = real[:2] + b"\xff\xfe\x00\x02" * 1000
    cut = body + b"\x00" * ((1 << 20) - 1 - len(body)) + sos * (merge.MAX_JPEG_SCANS + 1)
    check("the boundary case: the first marker of the run straddles the 1 MiB read",
          cut[(1 << 20) - 1:(1 << 20) + 1] == b"\xff\xda")
    path = SCRATCH / "straddle.jpg"
    path.write_bytes(cut)
    try:
        with open(path, "rb") as source:
            merge._count_scans(source)
        straddled = None
    except merge.NotAPhoto as exc:
        straddled = exc
    check("scans on both sides of a 1 MiB read boundary are all counted", straddled is not None, straddled)
    # And through the PDF path: a bomb beside real pictures is left out, the pictures stay.
    good = SCRATCH / "good1.jpg"
    gradient((64, 48)).save(good, "JPEG")
    good2 = SCRATCH / "good2.jpg"
    gradient((64, 48)).save(good2, "JPEG")
    began = time.perf_counter()
    try:
        pages_made = merge.build_pdf([str(SCRATCH / "bomb.jpg"), str(good), str(good2)], str(SCRATCH / "scans.pdf"))
    except Exception as exc:
        pages_made = exc
    took = time.perf_counter() - began
    check(f"build_pdf leaves the bomb out and keeps the two photos ({took:.2f} s)", pages_made == 2 and took < 2, pages_made)


def header_checks():
    print("\n== a header of millions of parts is refused from the file, before Pillow keeps them ==")
    cap = merge.MAX_HEADER_PARTS
    check("the cap is a named constant of 1,000", cap == 1000, cap)

    buffer = io.BytesIO()
    gradient((64, 48)).save(buffer, "JPEG")
    jpeg = buffer.getvalue()
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), (9, 99, 199)).save(buffer, "PNG")
    png = buffer.getvalue()
    buffer = io.BytesIO()
    Image.new("P", (8, 8)).save(buffer, "GIF")
    gif = buffer.getvalue()
    buffer = io.BytesIO()
    gradient((64, 48)).save(buffer, "WEBP", lossless=True)
    webp = buffer.getvalue()

    png_at = png.index(b"IDAT") - 4
    gif_at = 13 + (3 * (2 << (gif[10] & 7)) if gif[10] & 0x80 else 0)
    png_chunk = lambda kind, body: len(body).to_bytes(4, "big") + kind + body + zlib.crc32(kind + body).to_bytes(4, "big")
    sub_blocks = lambda n: b"\x21\xfe" + b"\x01\x00" * n + b"\x00"       # one comment of n one-byte sub-blocks
    webp_junk = lambda n: b"JUNK\x00\x00\x00\x00" * n

    def jpeg_with(extra):
        return jpeg[:2] + extra + jpeg[2:]

    def png_with(extra):
        return png[:png_at] + extra + png[png_at:]

    def gif_with(extra):
        return gif[:gif_at] + extra + gif[gif_at:]

    def webp_with(extra):
        return webp[:12] + extra + webp[12:]

    def png_after(extra):                       # between the picture and IEND, where only load_end() reads
        return png[:-12] + extra + png[-12:]

    def gif_after(extra):                       # between the picture and the trailer
        return gif[:-1] + extra + gif[-1:]

    assert gif[gif_at] == 0x2C, "the fixture GIF has an extension before its picture; the LZW cases below assume none"
    gif_data_at = gif_at + 10 + (3 * (2 << (gif[gif_at + 9] & 7)) if gif[gif_at + 9] & 0x80 else 0)     # the LZW code size byte

    def gif_picture(blocks):                    # the one picture, with its data as these sub-blocks
        return gif[:gif_data_at] + b"\x08" + blocks + b"\x00;"

    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), (255, 0, 0)).save(buffer, "GIF", save_all=True,
                                               append_images=[Image.new("RGB", (8, 8), (0, 0, 255))], duration=50)
    moving = buffer.getvalue()

    # Each: the file, what it must be refused as. All of them in well under half a second, with Pillow
    # never asked, and in memory that does not grow with the file.
    hostile = {
        "a 64 x 48 JPEG with 12 M four-byte APP5 segments (48 MiB; Pillow: 1.3 GB and 9 s)":
            (lambda: jpeg_with(b"\xff\xe5\x00\x02" * (12 * MB)), merge.NotAPhoto),
        "a JPEG with 12 M empty COM segments (48 MiB)": (lambda: jpeg_with(b"\xff\xfe\x00\x02" * (12 * MB)), merge.NotAPhoto),
        f"a JPEG with {cap + 1} segments more than a photo has": (lambda: jpeg_with(b"\xff\xe5\x00\x02" * (cap + 1)), merge.NotAPhoto),
        "a JPEG of 48 MiB of fill bytes after the SOI": (lambda: b"\xff\xd8\xff" + b"\xff" * (48 * MB), merge.NotAPhoto),
        "a JPEG of 48 MiB of zeros after its first segment": (lambda: b"\xff\xd8\xff\xe0\x00\x02" + bytes(48 * MB), merge.NotAPhoto),
        "a PNG with 4 M tiny unknown chunks (48 MiB; Pillow: 217 MB per 19 MiB)":
            (lambda: png_with(png_chunk(b"abCd", b"") * (4 * MB)), merge.NotAPhoto),
        "a PNG with 3 M tEXt chunks": (lambda: png_with(png_chunk(b"tEXt", b"k\x00v") * (3 * MB)), merge.NotAPhoto),
        f"a PNG with {cap + 1} chunks more than a photo has": (lambda: png_with(png_chunk(b"abCd", b"") * (cap + 1)), merge.NotAPhoto),
        "a GIF of 1.6 M one-byte comment sub-blocks (3.2 MB; Pillow's `+=` did not finish it in 200 s)":
            (lambda: gif_with(sub_blocks(1_600_000)), merge.NotAPhoto),
        "a GIF of 24 M one-byte comment sub-blocks (48 MB)": (lambda: gif_with(sub_blocks(24 * MB)), merge.NotAPhoto),
        f"a GIF with {cap + 1} comment sub-blocks": (lambda: gif_with(sub_blocks(cap + 1)), merge.NotAPhoto),
        f"a GIF with {cap + 1} extension blocks": (lambda: gif_with(b"\x21\xfe\x00" * (cap + 1)), merge.NotAPhoto),
        "a GIF of 48 MiB of bytes that are not blocks": (lambda: gif[:gif_at] + bytes(48 * MB), merge.NotAPhoto),
        "a WEBP with 6 M tiny unknown chunks (48 MiB)": (lambda: webp_with(webp_junk(6 * MB)), merge.NotAPhoto),
        f"a WEBP with {cap + 1} chunks more than a photo has": (lambda: webp_with(webp_junk(cap + 1)), merge.NotAPhoto),
        # What Pillow parses AFTER the picture, or inside it, in Python: the whole file is walked.
        "a PNG with 4 M tiny chunks between the picture and IEND (48 MiB; Pillow's load_end() reads them)":
            (lambda: png_after(png_chunk(b"abCd", b"") * (4 * MB)), merge.NotAPhoto),
        f"a PNG with {cap + 1} chunks after the picture": (lambda: png_after(png_chunk(b"abCd", b"") * (cap + 1)), merge.NotAPhoto),
        "a PNG with 4 M empty IDAT chunks (48 MiB)": (lambda: png_with(png_chunk(b"IDAT", b"") * (4 * MB)), merge.NotAPhoto),
        f"a PNG with {merge.MAX_PNG_IDATS + 1} IDAT chunks": (lambda: png_with(png_chunk(b"IDAT", b"") * (merge.MAX_PNG_IDATS + 1)), merge.NotAPhoto),
        "a GIF with a 48 MB comment (24 M one-byte sub-blocks) after the picture":
            (lambda: gif_after(sub_blocks(24 * MB)), merge.NotAPhoto),
        "a GIF with a 48 MB comment in a second frame (Pillow's `+=` runs for a comment at any frame)":
            (lambda: gif[:-1] + sub_blocks(24 * MB) + gif[gif_at:], merge.NotAPhoto),
        f"a GIF with {cap + 1} comment sub-blocks after the picture": (lambda: gif_after(sub_blocks(cap + 1)), merge.NotAPhoto),
        "a GIF whose one picture is 24 M one-byte LZW sub-blocks (48 MB; is_animated would skip them in Python)":
            (lambda: gif_picture(b"\x01\x00" * (24 * MB)), merge.NotAPhoto),
        f"a GIF whose picture has {merge.MAX_GIF_SUB_BLOCKS + 1} sub-blocks": (lambda: gif_picture(b"\x01\x00" * (merge.MAX_GIF_SUB_BLOCKS + 1)), merge.NotAPhoto),
        "a GIF with a second frame (an animated GIF, however small)": (lambda: moving, merge.NotAPhoto),
        "a GIF whose picture ends inside its descriptor": (lambda: gif[:gif_at + 5], merge.PhotoCutShort),
        # Cut short or lying about its length: not odd but cut, which the person is told, and still cheap.
        "a JPEG that ends inside its first segments": (lambda: jpeg[:30], merge.PhotoCutShort),
        "a JPEG whose segment says 65 535 bytes and the file has 100": (lambda: b"\xff\xd8\xff\xe0\xff\xff" + bytes(100), merge.PhotoCutShort),
        "a PNG whose chunk says 4 GB": (lambda: png_with(b"\xff\xff\xff\xffabCd" + bytes(50)), merge.PhotoCutShort),
        "a PNG that ends inside its header": (lambda: png[:40], merge.PhotoCutShort),
        "a GIF whose comment never ends": (lambda: gif[:gif_at] + b"\x21\xfe\xff" + bytes(20), merge.PhotoCutShort),
        "a GIF that ends before any picture": (lambda: gif[:gif_at], merge.PhotoCutShort),
        "a WEBP whose chunk says 4 GB": (lambda: webp_with(b"JUNK\xff\xff\xff\xff" + bytes(40)), merge.NotAPhoto),
    }
    path = FIXTURES / "hostile.bin"
    for label, (make, wanted) in hostile.items():
        path.write_bytes(make())
        outcome, took, peak = judged(path)
        check(f"{label}: refused as {wanted.__name__}, in {took * 1000:.0f} ms, {peak // 1024} KiB of memory",
              type(outcome) is wanted and took < 0.5 and peak < 1024 * 1024, (outcome, took, peak))
        with pillow_forbidden():
            try:
                merge.build_pdf([str(path)], str(FIXTURES / "refused.pdf"))
                ended = None
            except Exception as exc:
                ended = exc
        want_end = merge.NothingToMerge if wanted is merge.NotAPhoto else wanted
        check(f"...and build_pdf ends it as {want_end.__name__} with Pillow never asked", type(ended) is want_end, ended)
    path.unlink()
    check("a PhotoCutShort is a MergeError, not a NotAPhoto (the person is told; the page is not quietly dropped)",
          issubclass(merge.PhotoCutShort, merge.MergeError) and not issubclass(merge.PhotoCutShort, merge.NotAPhoto))

    # And what a real photo has is not refused: the segments of a big ICC profile, a long comment, a lot of fill.
    fine = {
        f"a JPEG with {cap - 100} more segments (a photo has about 10)": jpeg_with(b"\xff\xe5\x00\x02" * (cap - 100)),
        "a JPEG with 2 000 bytes of fill after a segment": jpeg[:20] + b"\xff" * 2000 + jpeg[20:],
        f"a PNG with {cap - 100} more chunks": png_with(png_chunk(b"abCd", b"") * (cap - 100)),
        "a PNG with a 2 MB text chunk": png_with(png_chunk(b"abCd", bytes(2 * MB))),
        f"a GIF with a comment of {cap - 100} sub-blocks": gif_with(sub_blocks(cap - 100)),
        f"a WEBP with {cap - 100} more chunks": webp_with(webp_junk(cap - 100)),
        f"a PNG with {cap - 100} chunks after the picture": png_after(png_chunk(b"abCd", b"") * (cap - 100)),
        f"a PNG with {merge.MAX_PNG_IDATS - 10} IDAT chunks": png_with(png_chunk(b"IDAT", b"") * (merge.MAX_PNG_IDATS - 10)),
        "a 49 MB PNG in 6,000 IDATs of 8 KB, as a real big one is":
            png[:png_at] + png_chunk(b"IDAT", bytes(8192)) * 6000 + png[-12:],
        "a GIF whose picture is 190,000 sub-blocks of 255 bytes (48 MB), as a real big one is":
            gif_picture((b"\xff" + bytes(255)) * 190_000),
        f"a GIF whose picture has {merge.MAX_GIF_SUB_BLOCKS - 100} one-byte sub-blocks (the most it may)":
            gif_picture(b"\x01\x00" * (merge.MAX_GIF_SUB_BLOCKS - 100)),
        f"a GIF with a comment of {cap - 100} sub-blocks after the picture": gif_after(sub_blocks(cap - 100)),
    }
    for label, data in fine.items():
        path = _write("fine.bin", data)
        outcome, took, _ = judged(path)
        check(f"{label} passes the header check, in {took * 1000:.0f} ms", outcome is None and took < 0.5, (outcome, took))
    comment = save("comment.gif", Image.new("P", (20, 10)), "GIF", comment=b"a long comment " * 100)
    jpg_icc = save("icc.jpg", gradient((30, 20)), "JPEG", icc_profile=bytes(300_000))
    check("a real GIF with a 1,400-byte comment and a real JPEG with a 300 kB ICC profile each still make a page",
          pages(build([comment, jpg_icc], "fine.pdf")[0]) == [(20.0, 10.0), (30.0, 20.0)])

    print("\n== a picture too long to be a JPEG, a page too thin to exist ==")
    cap_side = merge.MAX_SIDE
    check("the longest side is capped at 65,500, the most a JPEG can have", cap_side == 65_500, cap_side)
    long_png = save("long.png", Image.new("L", (20_000_000, 1)), "PNG")
    outcome, decodes = refusal_of(long_png)
    check("a 20,000,000 x 1 PNG (20 MP: the megapixel check passes it) is refused as PictureTooLarge, nothing decoded",
          isinstance(outcome, merge.PictureTooLarge) and decodes == 0, (outcome, decodes))
    for size in ((cap_side + 1, 1), (1, cap_side + 1)):
        path = save(f"side{size[0]}x{size[1]}.png", Image.new("L", size), "PNG")
        outcome, decodes = refusal_of(path)
        check(f"{size[0]} x {size[1]}: PictureTooLarge, in either direction", isinstance(outcome, merge.PictureTooLarge) and decodes == 0,
              (outcome, decodes))
    for label, header in (("a GIF screen of 65,501 x 1", gif_header(cap_side + 1, 1)),
                          ("a GIF screen of 1 x 65,501", gif_header(1, cap_side + 1))):
        outcome, opens = refused_before_open(str(_write("header.bin", header)))
        check(f"{label}: PictureTooLarge from the header", isinstance(outcome, merge.PictureTooLarge) and opens == 0, (outcome, opens))
    wide = save("wide.png", Image.new("L", (cap_side, 1), 128), "PNG")
    tall = save("tall.png", Image.new("L", (1, cap_side), 128), "PNG")
    data, _ = build([wide, tall, still_jpeg()], "thin.pdf")
    thin = pages(data)
    check("the thinnest allowed pictures, 65,500 x 1 and 1 x 65,500, are built, and no page is under one point a side",
          len(thin) == 3 and all(w >= 1 and h >= 1 for w, h in thin) and max(thin[0]) == 14400 and max(thin[1]) == 14400
          and not structure_problems(data), thin)
    check("(a thin page keeps its long side at the Acrobat limit)", thin[0] == (14400.0, 1.0) and thin[1] == (1.0, 14400.0), thin)

    print("\n== a WEBP that cannot be decoded is left out, as a file that is not a photo ==")
    good_webp = save("good.webp", gradient((50, 40)), "WEBP")
    raw = pathlib.Path(good_webp).read_bytes()
    corrupt = {
        "a WEBP cut in half": raw[:len(raw) // 2],
        "a WEBP cut after its header": raw[:30],
        "a WEBP whose chunk name is wrong": raw[:12] + b"VP8L" + raw[16:],
    }
    still = still_jpeg()
    for label, data in corrupt.items():
        path = str(_write("corrupt.webp", data))
        try:
            with Image.open(path, formats=("WEBP",)) as probe:
                probe.load()
            raised = None
        except Exception as exc:
            raised = exc
        check(f"(the fixture: Pillow itself raises OSError 'could not create decoder object' for {label})",
              isinstance(raised, OSError) and "decoder" in str(raised), raised)
        check(f"{label} is NotAPhoto", _raises(merge.NotAPhoto, merge._open_photo, path))
        try:
            built, _ = build([still, path, still], "withcorrupt.pdf")
            ended = pages(built)
        except Exception as exc:
            ended = exc
        check(f"{label} among two photos: left out, a PDF of the two", ended == [(64.0, 48.0)] * 2, ended)
        outcome, _ = refusal_of(path)
        check(f"{label} with one photo: no PDF (NothingToMerge, 'no photos'), not a failure",
              isinstance(outcome, merge.NothingToMerge), outcome)
    png_cut = pathlib.Path(save("whole.png", noise((60, 40)), "PNG")).read_bytes()
    jpg_cut = pathlib.Path(save("whole.jpg", noise((60, 40)), "JPEG")).read_bytes()
    for label, data, name in (("a PNG cut short in its picture", png_cut[:len(png_cut) * 2 // 3], "cut.png"),
                              ("a JPEG cut short in its picture", jpg_cut[:len(jpg_cut) * 2 // 3], "cut.jpg")):
        path = str(_write(name, data))
        try:
            build([still, path], "cut.pdf")
            ended = None
        except Exception as exc:
            ended = exc
        check(f"{label} stays a failure, not a photo quietly left out",
              ended is not None and not isinstance(ended, (merge.NotAPhoto, merge.NothingToMerge)), ended)


def still_jpeg():
    return save("still64.jpg", gradient((64, 48)), "JPEG")


async def gate_and_keyboard_checks():
    global W
    print("\n== a cancelled caller does not let go of the PDF gate while its thread still runs ==")
    merge._pdf_gate = None
    real_build = merge.build_pdf
    state = {"running": 0, "peak": 0, "starts": []}
    started, release = threading.Event(), threading.Event()
    lock = threading.Lock()

    def slow_build(paths, out_path):
        with lock:
            state["running"] += 1
            state["peak"] = max(state["peak"], state["running"])
            state["starts"].append(out_path)
        started.set()
        release.wait(10)
        pathlib.Path(out_path).write_bytes(b"made late")      # the thread creates its output whenever it ends
        with lock:
            state["running"] -= 1
        return 1

    async def until(condition, seconds=5):
        end = time.monotonic() + seconds
        while not condition() and time.monotonic() < end:
            await asyncio.sleep(0.01)
        return condition()

    first_out, second_out = str(SCRATCH / "gate-first.pdf"), str(SCRATCH / "gate-second.pdf")
    merge.build_pdf = slow_build
    try:
        first = asyncio.create_task(merge.build_pdf_queued(["a"], first_out))
        await until(started.is_set)
        first.cancel()
        try:
            await first
            cancelled = False
        except asyncio.CancelledError:
            cancelled = True
        check("the caller of a build is cancelled mid-build: it gets its CancelledError", cancelled)
        check("but the gate is still shut, because the thread (which cannot be cancelled) is still building",
              merge.pdf_gate().locked() and state["running"] == 1, (merge.pdf_gate().locked(), state))
        second = asyncio.create_task(merge.build_pdf_queued(["b"], second_out))
        await asyncio.sleep(0.3)
        check("so a second build waits behind it, and the two never overlap", state["starts"] == [first_out] and state["peak"] == 1,
              state)
        release.set()
        result = await asyncio.wait_for(second, 5)
        check("once the first thread has ended the second one is built", result == 1 and state["starts"] == [first_out, second_out]
              and state["peak"] == 1, state)
        await until(lambda: not os.path.exists(first_out))
        check("the output the cancelled build created after nobody was waiting for it is removed",
              not os.path.exists(first_out), os.path.exists(first_out))
        check("and the second's is its caller's to remove, as ever", os.path.exists(second_out))
        await until(lambda: not merge.pdf_gate().locked())
        check("the gate is open at the end", not merge.pdf_gate().locked())

        # Cancelled while only waiting for the gate: nothing was held, so nothing is let go of.
        started.clear()
        release.clear()
        os.remove(second_out)
        holder = asyncio.create_task(merge.build_pdf_queued(["c"], first_out))
        await until(started.is_set)
        waiter = asyncio.create_task(merge.build_pdf_queued(["d"], second_out))
        await asyncio.sleep(0.1)
        waiter.cancel()
        try:
            await waiter
        except asyncio.CancelledError:
            pass
        check("a caller cancelled while it waits for the gate leaves the gate with the build that holds it",
              merge.pdf_gate().locked() and state["running"] == 1, (merge.pdf_gate().locked(), state))
        release.set()
        await asyncio.wait_for(holder, 5)
        await until(lambda: not merge.pdf_gate().locked())
        check("...and when that ends the gate is open, and the cancelled waiter's build never ran",
              not merge.pdf_gate().locked() and second_out not in state["starts"][2:], state["starts"])
        os.path.exists(first_out) and os.remove(first_out)

        # A build that raises, with nobody cancelled: the gate is let go of and the error reaches the caller.
        def failing(paths, out_path):
            raise OSError("disk full")

        merge.build_pdf = failing
        try:
            await merge.build_pdf_queued(["e"], first_out)
            raised = None
        except OSError as exc:
            raised = exc
        check("a build that fails reaches its caller as it failed, and lets go of the gate",
              isinstance(raised, OSError) and not merge.pdf_gate().locked(), raised)
    finally:
        release.set()
        merge.build_pdf = real_build
        merge._pdf_gate = None
        clean_scratch()

    print("\n== a tap takes away its own button and no other ==")
    token = "tokabcdef1"
    pdf_tap, zip_tap, redo_tap = f"merge:pdf:{token}", f"merge:zip:{token}", f"redo:{token}"

    def delivery(photos=3, items=4, **more):
        remembered = {"platform": "tiktok", "url": "https://vm.tiktok.com/x", "lossless": False, "photos": photos, "items": items}
        return context_with(redeliver={token: dict(remembered, **more)})

    async def tap(context, data, world, edit_fails=False):
        global W
        W = world
        edits = []

        async def answer(text=None, **kw):
            world.calls.append("answer")

        async def edit(reply_markup=None):
            world.calls.append("edit")
            if edit_fails:
                raise RuntimeError("message is not modified")
            edits.append(reply_markup)

        query = SimpleNamespace(data=data, message=MESSAGE, answer=answer, edit_message_reply_markup=edit)
        handler = bot.redeliver_callback if data.startswith("redo:") else bot.merge_callback
        await handler(update_with(query), context)
        return [d for row in row_data(edits[-1]) for d in row] if edits else None

    context, world = delivery(), World()
    after_pdf = await tap(context, pdf_tap, world)
    check("a PDF tap leaves the redo and the ZIP buttons", after_pdf == [redo_tap, zip_tap], after_pdf)
    after_zip = await tap(context, zip_tap, world)
    check("the ZIP after it leaves the redo button only: the PDF button does not come back", after_zip == [redo_tap], after_zip)
    after_redo = await tap(context, redo_tap, world)
    check("and the redo after that leaves nothing", after_redo == [], after_redo)
    check("all three went through, once each", world.calls.count("spawn") == 3, world.calls)

    context = delivery()
    check("ZIP first: the redo and the PDF stay", await tap(context, zip_tap, World()) == [redo_tap, pdf_tap])
    context = delivery()
    check("redo first: the PDF and the ZIP stay (the redo button took away only itself)",
          await tap(context, redo_tap, World()) == [pdf_tap, zip_tap])
    context = delivery(photos=1, items=2)
    check("an album with one photo and a video has no PDF button to begin with: the ZIP tap leaves the redo only",
          await tap(context, zip_tap, World()) == [redo_tap])
    context = delivery()
    world = World()
    await tap(context, pdf_tap, world, edit_fails=True)
    check("Telegram refusing the edit does not stop the merge", world.calls.count("spawn") == 1, world.calls)
    check("what the keyboard is rebuilt from is plain JSON (user_data is saved)",
          json.loads(json.dumps(context.user_data)) == context.user_data)

    print("\n== the album's keyboard is rebuilt from what was sent ==")
    context = context_with(redeliver={token: {"platform": "tiktok", "url": "https://x", "lossless": True}})
    W = World()
    entries = []
    for n, kind in enumerate(("photo", "photo", "video")):
        path = FIXTURES / f"rebuilt{n}.{'mp4' if kind == 'video' else 'jpg'}"
        path.write_bytes(b"x")
        entries.append((resolvers.MediaItem(kind, url="https://x", filename=path.name), str(path)))
    await bot._send_media(update_with(), context, entries, "en", lossless=True, redeliver_token=token)
    sent = [kb for text, kb in W.texts if text == i18n.t("en", "album_delivered", count=3)]
    remembered = context.user_data["redeliver"][token]
    check("_send_media keeps the counts the keyboard was built from", (remembered.get("photos"), remembered.get("items")) == (2, 3),
          remembered)
    check("and the keyboard rebuilt from them (nothing spent yet) is the keyboard that was sent",
          sent and row_data(bot._remembered_kb("en", token, remembered)) == row_data(sent[0]) and row_data(sent[0])[1],
          (row_data(sent[0]) if sent else None, row_data(bot._remembered_kb("en", token, remembered))))
    remembered["spent"] = ["pdf"]
    check("with the PDF spent, it is that keyboard less the PDF button", [d for r in row_data(bot._remembered_kb("en", token, remembered)) for d in r]
          == [f"redo:{token}", f"merge:zip:{token}"])

    print("\n== a tap by somebody out of downloads gives the button back ==")
    out_of = {"allowed": False, "scope": "hour", "hour": 12, "hour_max": 12, "wait_min": 5, "donor": True}
    full = [redo_tap, pdf_tap, zip_tap]
    for data, name in ((pdf_tap, "PDF"), (zip_tap, "ZIP"), (redo_tap, "redo")):
        context, world = delivery(), World()
        world.allowance = out_of
        shown = await tap(context, data, world)
        spent = context.user_data["redeliver"][token].get("spent", [])
        check(f"{name}: refused by the allowance, it is not spent, the whole keyboard is back, and the person is told",
              spent == [] and shown == full and "spawn" not in world.calls and ("record", "tiktok") not in world.calls
              and world.texts and world.texts[-1][0] == bot._allowance_message("en", out_of), (spent, shown, world.calls))
        world.allowance = {"allowed": True}
        shown = await tap(context, data, world)
        check(f"{name}: the same button, tapped again once there is an allowance, works", "spawn" in world.calls
              and [d for d in (shown or [])] == [x for x in full if x != data], (shown, world.calls))
    context, world = delivery(), World()
    await tap(context, pdf_tap, world)
    world.allowance = out_of
    shown = await tap(context, zip_tap, world)
    check("a refused ZIP after a taken PDF gives back the ZIP only: the PDF stays spent",
          shown == [redo_tap, zip_tap] and context.user_data["redeliver"][token]["spent"] == ["pdf"], shown)
    context, world = delivery(), World()
    world.allowance = out_of
    await tap(context, pdf_tap, world)
    check("a refused tap asks the allowance once and records no download (nothing started)",
          world.calls.count("allowance") == 1 and not [c for c in world.calls if isinstance(c, tuple)], world.calls)
    world.allowance = {"allowed": True}
    await tap(context, pdf_tap, world)
    check("an allowed tap records its download when it starts, before anything is fetched (a refusal later still counts)",
          [c for c in world.calls if isinstance(c, tuple)] == [("record", "tiktok")], world.calls)
    clean_scratch()


def main():
    wiring_checks()
    keyboard_checks()
    legacy_state_checks()
    zip_checks()
    if PIL_OK:
        pdf_checks()
        format_checks()
        header_checks()
        scan_checks()
        walk_checks()
        memoryview_checks()
        if pymupdf is None:
            skip("rendering the pages", "PyMuPDF is not installed")
        else:
            render_checks()
    else:
        skip("the PDF checks", "Pillow is not installed")
    asyncio.run(flow_checks())
    asyncio.run(gate_and_keyboard_checks())


try:
    main()
finally:
    shutil.rmtree(SCRATCH, ignore_errors=True)
    shutil.rmtree(FIXTURES, ignore_errors=True)

print()
if failures:
    print(f"FAILURES ({len(failures)}):")
    for name in failures:
        print("  -", name)
    sys.exit(1)
print("an album is one PDF or one ZIP, and nothing is kept")
