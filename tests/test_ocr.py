"""Text read from pictures and scanned PDFs, on the machine (ocr_convert.py).

    python3 tests/test_ocr.py

ConvertBot recognises text with the Tesseract engine that PyMuPDF carries inside
it: a picture or a PDF to TXT, and a picture or a PDF to a "searchable PDF"
(the page as it was, with an invisible text layer on top). This file holds the
module's own checks; what the menu offers, what it costs and how the worker is
asked is checked where it is wired (test_conversions.py and the scenarios).

Nothing here is a fixture on disk: every input is drawn at run time -- text set
with PyMuPDF's own Helvetica (it has the Cyrillic and Uzbek letters), turned
into a picture, given a little noise, and put on a PDF page or saved as a file.
What comes out is compared with what was drawn.

Four things are measured by hooks rather than by looking at the result, because
the result looks the same either way:
  * the biggest picture the engine was handed (a 48 MP JPEG killed a 1 GB
    container before it was brought down while it is decoded);
  * the biggest picture Pillow decoded;
  * MuPDF's cache, shrunk once a page (it grew 26 MB a page without);
  * the progress callback.

Needs PyMuPDF with the engine in it, Pillow, and the language data (TESSDATA_PREFIX,
or C:\\Bitura\\.Projects\\MumuBots\\TempTools\\tessdata where that exists); each check says
SKIP without them and the file still passes.
"""
import array
import contextlib
import io
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / ".mirror" / "convert_bot") if (HERE / ".mirror").exists()
                else str(HERE.parent / "convert_bot"))
sys.path.insert(1, str(HERE.parent / "shared"))
sys.path.append(str(HERE / "stubs"))             # after site-packages: the real Pillow beats the stand-in
os.environ.setdefault("CBOT_TOKEN", "test")
os.environ.setdefault("CBOT_USERNAME", "test")
os.environ.setdefault("DATABASE_URL", "postgresql://stub")
os.environ["ENV_FILES"] = "off"
_LAPTOP_TESSDATA = pathlib.Path(r"C:\Bitura\.Projects\MumuBots\TempTools\tessdata")
if not os.environ.get("TESSDATA_PREFIX") and _LAPTOP_TESSDATA.is_dir():
    os.environ["TESSDATA_PREFIX"] = str(_LAPTOP_TESSDATA)

import convert_utils as cu  # noqa: E402
import formats  # noqa: E402
import ocr_convert as oc  # noqa: E402

failures = []
WORK = pathlib.Path(tempfile.mkdtemp(prefix="ocr-"))

try:
    import pymupdf
except ImportError:
    pymupdf = None
try:
    from PIL import Image, ImageChops, ImageFile
    HAVE_PIL = hasattr(Image, "Transpose") and hasattr(ImageFile, "ImageFile")
except ImportError:
    HAVE_PIL = False
HAVE_ENGINE = pymupdf is not None and hasattr(pymupdf.Pixmap, "pdfocr_tobytes")
HAVE_DATA = oc.tessdata_dir() is not None
CAN_READ = HAVE_ENGINE and HAVE_DATA and HAVE_PIL


def check(name, ok, detail=""):
    print(("  ok  " if ok else "  FAIL  ") + name + (f" -- {detail}" if not ok and detail else ""))
    if not ok:
        failures.append(name)


def skip(name, why):
    print(f"  SKIP  {name} -- {why}")


def raises(fn, exc=Exception):
    try:
        fn()
    except exc as error:
        return error
    return None


def big_images(page):
    """The pictures on a page that are pictures: the text layer carries a pair
    of 1 by 1 stand-ins where the engine's own copy of the page was."""
    return [item for item in page.get_images(full=True) if item[2] * item[3] > 100]


def words(text):
    return " ".join(str(text).split())


# ---------------------------------------------------------------------------
# Inputs, drawn here
# ---------------------------------------------------------------------------

def _font():
    return pymupdf.Font("helv")


def text_png(path, text, size=36, width=1100, height=160, dpi=150):
    """A line of `text` as a PNG file, black on white."""
    doc = pymupdf.open()
    page = doc.new_page(width=width, height=height)
    writer = pymupdf.TextWriter(page.rect)
    writer.append((20, 90), text, font=_font(), fontsize=size)
    writer.write_text(page)
    page.get_pixmap(dpi=dpi).save(str(path))
    doc.close()
    return pathlib.Path(path)


def text_image(text, size=36, width=1100, height=160, dpi=150):
    """The same, as a grey Pillow image."""
    doc = pymupdf.open()
    page = doc.new_page(width=width, height=height)
    writer = pymupdf.TextWriter(page.rect)
    writer.append((20, 90), text, font=_font(), fontsize=size)
    writer.write_text(page)
    pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY)
    image = Image.frombytes("L", (pix.width, pix.height), pix.samples)
    doc.close()
    return image


def scan_jpeg(text, noise=True):
    """What a scanner leaves on a page: the text, paper with a little grain,
    JPEG. The grain is what makes the file as big as a real scan, so that a
    copy of the picture inside the result would show."""
    image = text_image(text, size=26, width=420, height=260, dpi=150)
    if noise:
        image = ImageChops.add(image, Image.effect_noise(image.size, 8), 1.0, -128)
    out = io.BytesIO()
    image.convert("RGB").save(out, format="JPEG", quality=85)
    return out.getvalue()


def add_text_page(doc, text, invisible=False):
    page = doc.new_page(width=420, height=260)
    page.insert_text((30, 80), text, fontsize=14, render_mode=3 if invisible else 0)
    return page


def add_scan_page(doc, text, rotate=0):
    """A page that is only a picture of `text`. With rotate=90 the page is
    turned, and the picture stored sideways so that it reads upright once the
    page is shown -- which is how a sideways-scanned book comes out of a
    scanner."""
    data = scan_jpeg(text)
    if rotate:
        image = Image.open(io.BytesIO(data)).transpose(Image.Transpose.ROTATE_90)
        out = io.BytesIO()
        image.save(out, format="JPEG", quality=85)
        data = out.getvalue()
        page = doc.new_page(width=260, height=420)
        page.insert_image(page.rect, stream=data)
        page.set_rotation(rotate)
    else:
        page = doc.new_page(width=420, height=260)
        page.insert_image(page.rect, stream=data)
    return page


VISIBLE = "Quarterly report: revenue grew by twelve percent."
HIDDEN = "Hidden layer text of an earlier recognition run."
SCAN_1 = "Invoice number four hundred"
SCAN_2 = "Rotated scan reads fine"
SCAN_3 = "Last scanned page here"


def mixed_pdf(path):
    """Five pages: text, a scan, a page that is a picture AND already carries
    its text (invisible, and different from what the picture says), a scan
    that is stored turned, and a last scan."""
    doc = pymupdf.open()
    add_text_page(doc, VISIBLE)
    add_scan_page(doc, SCAN_1)
    page = add_text_page(doc, HIDDEN, invisible=True)
    page.insert_image(pymupdf.Rect(30, 120, 390, 240), stream=scan_jpeg("Picture words that differ"))
    add_scan_page(doc, SCAN_2, rotate=90)
    add_scan_page(doc, SCAN_3)
    doc.save(str(path))
    doc.close()
    return pathlib.Path(path)


def blank_pdf(path, pages=1, width=420, height=260):
    """Pages with nothing on them at all: not scanned (there is no picture to read)."""
    doc = pymupdf.open()
    for _ in range(pages):
        doc.new_page(width=width, height=height)
    doc.save(str(path))
    doc.close()
    return pathlib.Path(path)


def paper_pdf(path, pages=1, width=420, height=260):
    """Pages that are a picture of blank paper and nothing else: scanned (a
    picture, no text of their own), and with no text to be found in them."""
    out = io.BytesIO()
    Image.new("L", (40, 26), 245).save(out, format="JPEG")
    doc = pymupdf.open()
    xref = 0
    for _ in range(pages):
        page = doc.new_page(width=width, height=height)
        xref = page.insert_image(page.rect, stream=out.getvalue()) if not xref else page.insert_image(page.rect, xref=xref)
    doc.save(str(path))
    doc.close()
    return pathlib.Path(path)


# ---------------------------------------------------------------------------
# Hooks
# ---------------------------------------------------------------------------

@contextlib.contextmanager
def hooks():
    """Record, and change nothing: the pictures the engine is given and the
    language it is told, the pictures Pillow decodes, the calls that shrink
    MuPDF's cache, and the progress reports."""
    record = {"engine_pixels": [], "languages": [], "tessdata": [], "decoded_pixels": [], "shrinks": 0,
              "progress": []}
    real_ocr = pymupdf.Pixmap.pdfocr_tobytes
    real_load = ImageFile.ImageFile.load
    real_progress = cu.PROGRESS

    def ocr_spy(self, *args, **kwargs):
        record["engine_pixels"].append(self.width * self.height)
        record["languages"].append(kwargs.get("language"))
        record["tessdata"].append(kwargs.get("tessdata"))
        return real_ocr(self, *args, **kwargs)

    def load_spy(self, *args, **kwargs):
        record["decoded_pixels"].append(self.size[0] * self.size[1])
        return real_load(self, *args, **kwargs)

    # TOOLS is a class used as a namespace, and store_shrink a static method of it:
    # put back exactly what was there.
    holder = pymupdf.TOOLS
    had_own = "store_shrink" in vars(holder)
    saved = vars(holder).get("store_shrink")
    real_shrink = holder.store_shrink

    def shrink_spy(percent):
        record["shrinks"] += 1
        return real_shrink(percent)

    pymupdf.Pixmap.pdfocr_tobytes = ocr_spy
    ImageFile.ImageFile.load = load_spy
    holder.store_shrink = staticmethod(shrink_spy)
    cu.PROGRESS = lambda done, total: record["progress"].append((done, total))
    try:
        yield record
    finally:
        pymupdf.Pixmap.pdfocr_tobytes = real_ocr
        ImageFile.ImageFile.load = real_load
        if had_own:
            holder.store_shrink = saved
        else:
            del holder.store_shrink
        cu.PROGRESS = real_progress


def convert(path, src, target, name, lang="eng"):
    return oc.convert(path, src, target, WORK, name, lang)


# ---------------------------------------------------------------------------
# The checks
# ---------------------------------------------------------------------------

def constants():
    check("the languages: English, Russian, Uzbek (both scripts in one pass), in the order the toggle cycles",
          oc.LANGS == {"eng": "eng", "rus": "rus", "uzb": "uzb+uzb_cyrl"} and oc.CHOICES == ("eng", "rus", "uzb"),
          f"{oc.LANGS} {oc.CHOICES}")
    check("an unknown or empty choice is English",
          [oc.tesseract_language(c) for c in ("xx", "", None, "ENG")] == ["eng"] * 4)
    check("the targets it writes", oc.WRITES == frozenset({"txt", "ocrpdf"}), str(oc.WRITES))
    check("the limits are the measured ones",
          (oc.OCR_MAX_PIXELS, oc.OCR_PAGE_S, oc.OCR_MAX_PAGES, oc.OCR_FREE_PAGES, oc.OCR_MIN_TEXT_CHARS,
           oc.OCR_MIN_DPI, oc.OCR_MAX_DECODE_PIXELS)
          == (12_000_000, 15, 30, 1, 20, 50, 64_000_000), str((oc.OCR_MAX_PIXELS, oc.OCR_PAGE_S, oc.OCR_MAX_PAGES,
                                                              oc.OCR_FREE_PAGES, oc.OCR_MIN_TEXT_CHARS,
                                                              oc.OCR_MIN_DPI, oc.OCR_MAX_DECODE_PIXELS)))
    check("the menu waits 5 seconds for a PDF's pages to be looked at", oc.OCR_DESCRIBE_SECONDS == 5,
          str(oc.OCR_DESCRIBE_SECONDS))
    # Each one can be changed by an environment variable, read when the module is.
    env = dict(os.environ, CONVERT_OCR_MAX_PIXELS="1", CONVERT_OCR_PAGE_SECONDS="2", CONVERT_OCR_MAX_PAGES="3",
               CONVERT_OCR_FREE_PAGES="0", CONVERT_OCR_MIN_TEXT_CHARS="4", CONVERT_OCR_MIN_DPI="5",
               CONVERT_OCR_MAX_DECODE_PIXELS="6", CONVERT_OCR_DESCRIBE_SECONDS="7", PYTHONPATH=os.pathsep.join(sys.path[:3]),
               CBOT_TOKEN="t", CBOT_USERNAME="t", DATABASE_URL="postgresql://stub", ENV_FILES="off")
    code = ("import sys; sys.path.append(%r); import ocr_convert as o; print(o.OCR_MAX_PIXELS, o.OCR_PAGE_S, "
            "o.OCR_MAX_PAGES, o.OCR_FREE_PAGES, o.OCR_MIN_TEXT_CHARS, o.OCR_MIN_DPI, o.OCR_MAX_DECODE_PIXELS, "
            "o.OCR_DESCRIBE_SECONDS)"
            % str(HERE / "stubs"))
    run = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=60)
    check("CONVERT_OCR_* change the limits (a fresh interpreter)", run.stdout.split() == ["1", "2", "3", "0", "4", "5", "6", "7.0"],
          (run.stdout + run.stderr)[-300:])


def dpi():
    check("an A4 page is drawn at 300 dpi", oc.ocr_dpi(595.28, 841.89) == 300, str(oc.ocr_dpi(595.28, 841.89)))
    a0 = oc.ocr_dpi(2384, 3370)
    check("an A0 page is drawn at about 88 dpi, to stay under the pixel limit", a0 is not None and 85 <= a0 <= 88, str(a0))
    check("a 14400 by 14400 pt page is not recognised", oc.ocr_dpi(14400, 14400) is None, str(oc.ocr_dpi(14400, 14400)))
    check("the floor: 4900 pt a side is read at 50 dpi, 5100 is not read",
          oc.ocr_dpi(4900, 4900) == 50 and oc.ocr_dpi(5100, 5100) is None,
          f"{oc.ocr_dpi(4900, 4900)} {oc.ocr_dpi(5100, 5100)}")
    bad = [(0, 100), (100, 0), (-5, 100), (100, -5), (float("nan"), 100), (100, float("nan")),
           (float("inf"), 100), (None, 100), ("wide", 100)]
    check("a page with no size is not recognised", all(oc.ocr_dpi(w, h) is None for w, h in bad),
          str([(w, h, oc.ocr_dpi(w, h)) for w, h in bad if oc.ocr_dpi(w, h) is not None]))
    import math
    over = []
    for w, h in ((595, 842), (842, 595), (2384, 3370), (1000, 4800), (4800, 1000), (3000, 3000), (240, 180),
                 (960, 720), (8, 11000), (11000, 8), (4900, 4900), (3499.5, 3499.5)):
        d = oc.ocr_dpi(w, h)
        if d is None:
            continue
        pixels = math.ceil(w * d / 72) * math.ceil(h * d / 72)
        if pixels > oc.OCR_MAX_PIXELS:
            over.append((w, h, d, pixels))
    check("whatever the page, the picture drawn from it is under the limit", not over, str(over))


def availability():
    keep = os.environ.get("TESSDATA_PREFIX")
    empty = pathlib.Path(tempfile.mkdtemp(prefix="nodata-", dir=WORK))
    partial = pathlib.Path(tempfile.mkdtemp(prefix="partial-", dir=WORK))
    try:
        os.environ["TESSDATA_PREFIX"] = str(empty)
        check("available() is False when TESSDATA_PREFIX holds no language data",
              oc.available() is False and oc.tessdata_dir() is None)
        if HAVE_DATA:
            for name in ("eng", "rus", "uzb"):                 # uzb_cyrl missing
                shutil.copy(pathlib.Path(keep) / f"{name}.traineddata", partial / f"{name}.traineddata")
            os.environ["TESSDATA_PREFIX"] = str(partial)
            check("... and when one of the four is missing", oc.available() is False and oc.tessdata_dir() is None)
        os.environ.pop("TESSDATA_PREFIX")
        check("... and when TESSDATA_PREFIX is not set", oc.available() is False and oc.tessdata_dir() is None)
        refused = raises(lambda: oc.convert(WORK / "x.png", "png", "txt", WORK, "x"), oc.ConversionError)
        check("a conversion without language data is refused with a sentence",
              refused is not None and "isn't available" in str(refused), repr(refused))
    finally:
        if keep is None:
            os.environ.pop("TESSDATA_PREFIX", None)
        else:
            os.environ["TESSDATA_PREFIX"] = keep
    if CAN_READ:
        check("available() is True with the data and the engine", oc.available() is True and oc.tessdata_dir() == keep)
    else:
        skip("available() is True with the data and the engine", "PyMuPDF with the engine, Pillow or the language data is missing")


def languages():
    cases = (
        ("eng", "eng", "The quick brown fox jumps over the lazy dog"),
        ("rus", "rus", "Быстрая коричневая лиса прыгает через ленивую собаку"),
        ("uzbek latin", "uzb", "Salom dunyo, qalaysiz, bugun havo juda yaxshi"),
        ("uzbek cyrillic", "uzb", "Салом дунё, яхшимисиз, бугун ҳаво жуда яхши"),
    )
    for label, choice, text in cases:
        path = text_png(WORK / f"lang-{label.replace(' ', '_')}.png", text)
        result = convert(path, "png", "txt", f"lang-{label.replace(' ', '_')}", choice)
        got = words(result.path.read_text(encoding="utf-8"))
        check(f"a picture of {label} text is read back with the {choice!r} choice", got == text, f"{got!r}")
    # The choice is what decides: Cyrillic Uzbek read as Russian has no ҳ in it.
    path = WORK / "lang-uzbek_cyrillic.png"
    as_russian = words(convert(path, "png", "txt", "lang-as-rus", "rus").path.read_text(encoding="utf-8"))
    check("the language is the one chosen (Uzbek Cyrillic read as Russian is not Uzbek)", "ҳ" not in as_russian, as_russian)
    with hooks() as seen:
        convert(path, "png", "txt", "lang-spy", "uzb")
        convert(path, "png", "txt", "lang-spy2", "rus")
        convert(path, "png", "txt", "lang-spy3", "nonsense")
    check("the engine is told uzb+uzb_cyrl for Uzbek, rus for Russian, eng for anything unknown, and where the data is",
          seen["languages"] == ["uzb+uzb_cyrl", "rus", "eng"] and set(seen["tessdata"]) == {oc.tessdata_dir()},
          f"{seen['languages']} {set(seen['tessdata'])}")


def mixed():
    path = mixed_pdf(WORK / "mixed.pdf")
    check("count_scanned: five pages, three of them pictures of text",
          oc.count_scanned(path) == {"pages": 5, "scanned": 3}, str(oc.count_scanned(path)))
    with hooks() as seen:
        result = convert(path, "pdf", "txt", "mixed-txt")
    text = result.path.read_text(encoding="utf-8")
    original = pymupdf.open(str(path))
    visible, hidden = original[0].get_text(), original[2].get_text()
    original.close()
    check("a page with its own text gives that text, exactly (visible, and an invisible layer that is not what the picture says)",
          visible in text and hidden in text and "differ" not in text, repr(text))
    spots = [text.find(part) for part in (VISIBLE, SCAN_1, HIDDEN, SCAN_2, SCAN_3)]
    check("the scanned pages are recognised, and the pages stay in order (the turned scan is read upright)",
          all(spot >= 0 for spot in spots) and spots == sorted(spots), f"{spots} {text!r}")
    check("only the pictures of text are recognised, once each (the pages with text are not)",
          len(seen["engine_pixels"]) == 3, str(seen["engine_pixels"]))
    check("MuPDF's cache is shrunk once a page", seen["shrinks"] == 3, str(seen["shrinks"]))
    check("progress is reported once a page, from 0 to the end", seen["progress"] == [(0, 3), (1, 3), (2, 3), (3, 3)],
          str(seen["progress"]))
    check("no picture handed to the engine is over the pixel limit",
          max(seen["engine_pixels"]) <= oc.OCR_MAX_PIXELS, str(seen["engine_pixels"]))

    # Text pages come out the way pdf -> txt has always put them out.
    texty = WORK / "texty.pdf"
    doc = pymupdf.open()
    for line in ("First page of plain text for the comparison.", "Second page, also plain text.", "And a third one."):
        add_text_page(doc, line)
    doc.save(str(texty))
    doc.close()
    import doc_convert
    old = doc_convert.convert(texty, "pdf", "txt", WORK, "texty-old").path.read_bytes()
    new = convert(texty, "pdf", "txt", "texty-new").path.read_bytes()
    check("a PDF with text only gives the same TXT as before (pages separated the same way)", old == new, f"{old!r} {new!r}")


def searchable_pdf():
    path = pathlib.Path(WORK / "mixed.pdf")
    with hooks() as seen:
        result = convert(path, "pdf", "ocrpdf", "mixed-pdf")
    check("the result is <stem>.pdf", result.path.name == "mixed-pdf.pdf" and result.path.exists(), str(result.path))
    original, produced = pymupdf.open(str(path)), pymupdf.open(str(result.path))
    try:
        check("same page count", produced.page_count == original.page_count == 5, f"{produced.page_count}")
        differ = []
        for index in range(original.page_count):
            before, after = original[index].get_pixmap(dpi=50), produced[index].get_pixmap(dpi=50)
            if (before.width, before.height) != (after.width, after.height) or before.samples != after.samples:
                differ.append(index)
        check("every page looks exactly as before, at 50 dpi (rotated page included)", not differ, f"pages {differ}")
        check("the scanned pages have their text, whole, and searchable (the turned page too)",
              all(words(produced[i].get_text()) == part for i, part in ((1, SCAN_1), (3, SCAN_2), (4, SCAN_3)))
              and produced[1].search_for("number") and produced[3].search_for("fine") and produced[4].search_for("here"),
              str([produced[i].get_text() for i in (1, 3, 4)]))
        # Where the words are, not only what they say: the layer, turned into the page as shown, against
        # the engine's own reading of the page as shown. (A layer laid on a turned page without turning
        # it ran sideways and was cut off at the stored width, and the page looked the same.)
        off = []
        for index in (1, 3, 4):
            pix = original[index].get_pixmap(dpi=72)
            shown = pymupdf.open("pdf", pix.pdfocr_tobytes(language="eng", tessdata=oc.tessdata_dir()))[0].get_text("words")
            layer = [(w[4], pymupdf.Rect(w[:4]) * produced[index].rotation_matrix) for w in produced[index].get_text("words")]
            if [w[4] for w in shown] != [w for w, _ in layer]:
                off.append((index, "words", [w[4] for w in shown], [w for w, _ in layer]))
                continue
            for word, (_, rect) in zip(shown, layer):
                if max(abs(word[0] - rect.x0), abs(word[1] - rect.y0), abs(word[2] - rect.x1), abs(word[3] - rect.y1)) > 4:
                    off.append((index, word[4], tuple(word[:4]), tuple(rect)))
        check("the text layer sits on the words of the picture, on turned pages too", not off, str(off))
        check("the pages with text are as they were (the same text, the same content)",
              all(produced[i].get_text() == original[i].get_text() and produced[i].read_contents() == original[i].read_contents()
                  for i in (0, 2)), "")
        check("the picture on a page with an invisible layer is not read again",
              "differ" not in produced[2].get_text() and len(seen["engine_pixels"]) == 3, str(seen["engine_pixels"]))
    finally:
        original.close()
        produced.close()
    limit = path.stat().st_size + 20 * 1024 * 3
    check("the size is at most the input plus 20 KB a scanned page", result.path.stat().st_size <= limit,
          f"{result.path.stat().st_size} > {limit} (input {path.stat().st_size})")


def pictures():
    png = text_png(WORK / "pic.png", "Searchable from a photograph")
    result = convert(png, "png", "ocrpdf", "pic-pdf")
    produced = pymupdf.open(str(result.path))
    try:
        page = produced[0]
        check("a picture becomes a one-page searchable PDF that still shows the picture",
              produced.page_count == 1 and result.path.suffix == ".pdf" and len(big_images(page)) == 1
              and words(page.get_text()) == "Searchable from a photograph" and bool(page.search_for("photograph")),
              f"{produced.page_count} {page.get_images()} {page.get_text()!r}")
        rect = page.rect
        check("... on a page the size of the picture at 300 dpi",
              abs(rect.width - Image.open(png).size[0] * 72 / 300) < 1 and abs(rect.height - Image.open(png).size[1] * 72 / 300) < 1,
              str(rect))
    finally:
        produced.close()
    with hooks() as seen:
        convert(png, "png", "txt", "pic-txt")
    check("a picture is one page: progress 0 of 1, then 1 of 1; the cache shrunk once",
          seen["progress"] == [(0, 1), (1, 1)] and seen["shrinks"] == 1, f"{seen['progress']} {seen['shrinks']}")

    # Transparent PNG: black text where the alpha is, nothing elsewhere.
    mask = ImageChops.invert(text_image("Text on glass"))
    black = Image.new("L", mask.size, 0)
    glass = Image.merge("RGBA", (black, black, black, mask))
    glass.save(WORK / "glass.png")
    got = words(convert(WORK / "glass.png", "png", "txt", "glass").path.read_text(encoding="utf-8"))
    check("a transparent picture is read on white, not on black", got == "Text on glass", repr(got))

    # JPEG with the camera's own orientation: stored on its side.
    upright = text_image("Held the camera sideways")
    sideways = upright.transpose(Image.Transpose.ROTATE_90)                  # the way a sensor stores it for orientation 6
    exif = Image.Exif()
    exif[0x0112] = 6
    sideways.convert("RGB").save(WORK / "turned.jpg", format="JPEG", quality=92, exif=exif)
    got = words(convert(WORK / "turned.jpg", "jpg", "txt", "turned").path.read_text(encoding="utf-8"))
    check("a photo stored sideways (EXIF orientation) is read the right way up", got == "Held the camera sideways", repr(got))

    # 1-bit fax scan and 16-bit grey scan, as TIFF.
    text_image("Fax machine page").convert("1").save(WORK / "fax.tif")
    got = words(convert(WORK / "fax.tif", "tiff", "txt", "fax").path.read_text(encoding="utf-8"))
    check("a 1-bit TIFF is read", got == "Fax machine page", repr(got))
    base = text_image("Sixteen bit grey")
    wide = Image.frombytes("I;16", base.size, array.array("H", [value * 257 for value in base.tobytes()]).tobytes())
    wide.save(WORK / "grey16.tif")
    got = words(convert(WORK / "grey16.tif", "tiff", "txt", "grey16").path.read_text(encoding="utf-8"))
    check("a 16-bit grey TIFF is read (not clipped to white)", got == "Sixteen bit grey", repr(got))


def picture_formats():
    """The same line of text, saved the ways pictures arrive."""
    cu.register_openers()
    line = "Edge case words"
    base = text_image(line, size=40, width=700, height=140)
    rgb = base.convert("RGB")
    mask = ImageChops.invert(base)
    black = Image.new("L", base.size, 0)
    palette = Image.new("RGBA", base.size, (255, 255, 255, 0))
    palette.paste(Image.new("RGBA", base.size, (0, 0, 0, 255)), mask=mask)
    still_gif = base.convert("P")
    saves = (
        ("bmp", "bmp", lambda path: rgb.save(path)),
        ("gif", "gif", lambda path: still_gif.save(path)),
        ("a CMYK jpg", "jpg", lambda path: rgb.convert("CMYK").save(path, quality=92)),
        ("a grey jpg", "jpg", lambda path: base.save(path, quality=92)),
        ("webp", "webp", lambda path: rgb.save(path, quality=90)),
        ("a TIFF with transparency", "tiff", lambda path: Image.merge("RGBA", (black, black, black, mask)).save(path)),
        ("a PNG with a transparent palette color", "png",
         lambda path: palette.convert("P", palette=Image.ADAPTIVE).save(path, transparency=0)),
        ("a TIFF written with save_all that holds one page", "tiff", lambda path: rgb.save(path, save_all=True)),
        # Two pictures in a phone photo (a depth map, a gain map): the first is the photograph.
        ("an MPO photo with a second picture in it (the first is read)", "jpg",
         lambda path: rgb.save(path, format="MPO", save_all=True, append_images=[rgb])),
        ("heic", "heic", lambda path: rgb.save(path, format="HEIF", quality=90)),
        ("avif", "avif", lambda path: rgb.save(path, format="AVIF")),
    )
    for number, (label, ext, save) in enumerate(saves):
        path = WORK / f"format{number}.{ext}"
        try:
            save(path)
        except Exception as error:
            skip(f"{label} is read", f"this Pillow cannot write it ({type(error).__name__})")
            continue
        got = words(convert(path, ext, "txt", f"format{number}").path.read_text(encoding="utf-8"))
        check(f"{label} is read", got == line, repr(got))
    # A TIFF of several pages is refused, not read as its first page: the text of
    # every other page used to be dropped without a word.
    for target in ("txt", "ocrpdf"):
        three = WORK / f"three-{target}.tif"
        rgb.save(three, save_all=True, append_images=[rgb, rgb])
        error = raises(lambda: convert(three, "tiff", target, f"three-{target}"), oc.ConversionError)
        check(f"a TIFF of three pages, to {target}: refused, with the number of pages and what to do instead",
              error is not None and str(error) == "That TIFF holds 3 pages. Text is read from a one-page picture: "
              "convert the TIFF to PDF first, then choose TXT or Searchable PDF.", repr(error))
        check(f"... and no file is left ({target})",
              not (WORK / f"three-{target}.{'txt' if target == 'txt' else 'pdf'}").exists())
    two = WORK / "two.tiff"
    rgb.save(two, save_all=True, append_images=[rgb])
    error = raises(lambda: convert(two, "tiff", "txt", "two"), oc.ConversionError)
    check("... two pages is already too many (a 2-frame TIFF)", error is not None and "holds 2 pages" in str(error), repr(error))
    # The refusal comes before anything is decoded or recognised.
    with hooks() as seen:
        raises(lambda: convert(two, "tiff", "ocrpdf", "two-pdf"), oc.ConversionError)
    check("... before a pixel is decoded or the engine is run", not seen["decoded_pixels"] and not seen["engine_pixels"],
          f"{seen['decoded_pixels']} {seen['engine_pixels']}")
    # A moving picture is not read: it is not a picture of a page.
    frames = [rgb, ImageChops.invert(rgb)]
    frames[0].save(WORK / "moving.gif", save_all=True, append_images=frames[1:], duration=100, loop=0)
    error = raises(lambda: convert(WORK / "moving.gif", "gif", "txt", "moving"), oc.ConversionError)
    check("an animated GIF is refused", error is not None and "animation" in str(error), repr(error))
    # And a very small picture is a refusal with nothing found, not a crash.
    for size in ((1, 1), (3, 3), (10, 10)):
        Image.new("RGB", size, (255, 255, 255)).save(WORK / "tiny.png")
        error = raises(lambda: convert(WORK / "tiny.png", "png", "ocrpdf", "tiny"), oc.ConversionError)
        check(f"a {size[0]} by {size[1]} picture: nothing found",
              error is not None and str(error) == "No text was found in that picture.", repr(error))


def big_pictures():
    # A 48 MP photograph: decoded at half size, so that the engine never meets it whole.
    base = text_image("Mountain village photo", size=100, width=3600, height=300, dpi=72)
    photo = Image.new("L", (8000, 6000), 232)
    photo.paste(base, (500, 2200))
    photo.save(WORK / "big48.jpg", format="JPEG", quality=70)
    del photo
    with hooks() as seen:
        result = convert(WORK / "big48.jpg", "jpg", "txt", "big48")
    got = words(result.path.read_text(encoding="utf-8"))
    check("a 48 MP JPEG is decoded at a fraction of its size (draft), never whole",
          seen["decoded_pixels"] and max(seen["decoded_pixels"]) <= oc.OCR_MAX_PIXELS * 1.01, str(seen["decoded_pixels"]))
    check("... the engine is never handed more than 12 MP",
          seen["engine_pixels"] and max(seen["engine_pixels"]) <= oc.OCR_MAX_PIXELS, str(seen["engine_pixels"]))
    check("... and the text on it is read", got == "Mountain village photo", repr(got))

    # 24 MP cannot be drafted (that needs a factor of two): decoded, brought down to 12 MP.
    base = text_image("Mountain village photo", size=85, width=3000, height=300, dpi=72)
    photo = Image.new("L", (6000, 4000), 232)
    photo.paste(base, (400, 1500))
    photo.save(WORK / "mid24.jpg", format="JPEG", quality=70)
    del photo
    with hooks() as seen:
        result = convert(WORK / "mid24.jpg", "jpg", "ocrpdf", "mid24")
    produced = pymupdf.open(str(result.path))
    try:
        sizes = [(item[2], item[3]) for item in big_images(produced[0])]
        check("a 24 MP picture is brought down to the limit before it is put in the PDF",
              len(sizes) == 1 and sizes[0][0] * sizes[0][1] <= oc.OCR_MAX_PIXELS, str(sizes))
        check("... the engine is never handed more than 12 MP either",
              max(seen["engine_pixels"]) <= oc.OCR_MAX_PIXELS, str(seen["engine_pixels"]))
        check("... and it is still searchable", bool(produced[0].search_for("village")), repr(produced[0].get_text()))
    finally:
        produced.close()

    # A picture that is not a JPEG is decoded whole, so a big one is refused first.
    Image.new("1", (9000, 8000)).save(WORK / "huge.png")
    with hooks() as seen:
        error = raises(lambda: convert(WORK / "huge.png", "png", "txt", "huge"), oc.ConversionError)
    check("a 72 MP PNG is refused as too large, before it is decoded",
          error is not None and "megapixels" in str(error) and not seen["decoded_pixels"],
          f"{error!r} {seen['decoded_pixels']}")
    check("... and nothing is left behind", not (WORK / "huge.txt").exists())

    # And a big page: an A0 poster scan is drawn at a lower dpi, not refused.
    poster = WORK / "poster.pdf"
    strip = text_image("Poster text here", size=70, width=2384, height=200, dpi=87)
    out = io.BytesIO()
    strip.save(out, format="JPEG", quality=80)
    doc = pymupdf.open()
    page = doc.new_page(width=2384, height=3370)
    page.insert_image(pymupdf.Rect(0, 1200, 2384, 1200 + 2384 * strip.size[1] / strip.size[0]), stream=out.getvalue())
    doc.save(str(poster))
    doc.close()
    check("an A0 page counts as scanned", oc.count_scanned(poster) == {"pages": 1, "scanned": 1}, str(oc.count_scanned(poster)))
    with hooks() as seen:
        got = words(convert(poster, "pdf", "txt", "poster").path.read_text(encoding="utf-8"))
    check("an A0 scan is read, from a picture under the pixel limit",
          got == "Poster text here" and max(seen["engine_pixels"]) <= oc.OCR_MAX_PIXELS and max(seen["engine_pixels"]) > 8_000_000,
          f"{got!r} {seen['engine_pixels']}")


def huge_page():
    both = WORK / "huge-page.pdf"
    doc = pymupdf.open()
    doc.new_page(width=14400, height=14400)
    add_scan_page(doc, SCAN_1)
    doc.save(str(both))
    doc.close()
    check("count_scanned: a 14400 pt page is not counted (it will not be read)",
          oc.count_scanned(both) == {"pages": 2, "scanned": 1}, str(oc.count_scanned(both)))
    with hooks() as seen:
        txt = convert(both, "pdf", "txt", "huge-page-txt").path.read_text(encoding="utf-8")
        pdf = convert(both, "pdf", "ocrpdf", "huge-page-pdf").path
    produced = pymupdf.open(str(pdf))
    try:
        check("the page too big to read is left as it is, and only the other is recognised",
              len(seen["engine_pixels"]) == 2 and SCAN_1 in words(txt) and produced.page_count == 2
              and produced[0].rect.width == 14400 and produced[0].rect.height == 14400
              and SCAN_1 in words(produced[1].get_text()),
              f"{seen['engine_pixels']} {txt!r} {produced.page_count}")
    finally:
        produced.close()
    only = blank_pdf(WORK / "huge-only.pdf", 1, 14400, 14400)
    error = raises(lambda: convert(only, "pdf", "txt", "huge-only"), oc.ConversionError)
    check("a PDF with nothing readable on it is told so", error is not None and str(error) == "No text was found in that PDF.", repr(error))


def counts_and_limits():
    long_pdf = paper_pdf(WORK / "long.pdf", formats.MAX_PAGES + 1)
    got = oc.count_scanned(long_pdf)
    check("count_scanned counts pages in full and scanned ones up to the page limit",
          got == {"pages": formats.MAX_PAGES + 1, "scanned": formats.MAX_PAGES}, str(got))
    error = raises(lambda: convert(long_pdf, "pdf", "txt", "long"), oc.ConversionError)
    check("a PDF over the page limit is refused as every PDF conversion refuses it",
          error is not None and f"{formats.MAX_PAGES}-page" in str(error), repr(error))
    many = paper_pdf(WORK / "many.pdf", oc.OCR_MAX_PAGES + 1)
    with hooks() as seen:
        error = raises(lambda: convert(many, "pdf", "txt", "many"), oc.ConversionError)
    check("more scanned pages than the limit is refused before any is read",
          error is not None and f"at most {oc.OCR_MAX_PAGES}" in str(error) and not seen["engine_pixels"],
          f"{error!r} {seen['engine_pixels']}")
    exactly = paper_pdf(WORK / "exactly.pdf", oc.OCR_MAX_PAGES)
    check("... and exactly the limit is counted as scanned", oc.count_scanned(exactly)["scanned"] == oc.OCR_MAX_PAGES)

    (WORK / "not.pdf").write_bytes(b"this is not a PDF at all")
    check("count_scanned raises ConversionError for a file that is not a PDF",
          raises(lambda: oc.count_scanned(WORK / "not.pdf"), oc.ConversionError) is not None)
    locked = WORK / "locked.pdf"
    doc = pymupdf.open()
    add_text_page(doc, VISIBLE)
    doc.save(str(locked), encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="secret", owner_pw="owner")
    doc.close()
    error = raises(lambda: convert(locked, "pdf", "txt", "locked"), oc.ConversionError)
    check("a PDF that needs a password is refused with a sentence",
          error is not None and "password" in str(error) and raises(lambda: oc.count_scanned(locked), oc.ConversionError) is not None,
          repr(error))
    blank = paper_pdf(WORK / "blank.pdf", 1)
    error = raises(lambda: convert(blank, "pdf", "ocrpdf", "blank-pdf"), oc.ConversionError)
    check("a searchable PDF of a PDF that is only a picture of blank paper: no text was found",
          error is not None and str(error) == "No text was found in that PDF.", repr(error))
    nothing = blank_pdf(WORK / "nothing.pdf", 2)
    error = raises(lambda: convert(nothing, "pdf", "ocrpdf", "nothing-pdf"), oc.ConversionError)
    check("... and of one with nothing on its pages at all: there is nothing to recognise",
          error is not None and "nothing to recognise" in str(error), repr(error))
    error = raises(lambda: convert(nothing, "pdf", "txt", "nothing-txt"), oc.ConversionError)
    check("... and its TXT says no text was found", error is not None and str(error) == "No text was found in that PDF.",
          repr(error))
    full = WORK / "texty-only.pdf"
    doc = pymupdf.open()
    add_text_page(doc, VISIBLE)
    doc.save(str(full))
    doc.close()
    error = raises(lambda: convert(full, "pdf", "ocrpdf", "full-of-text"), oc.ConversionError)
    check("a searchable PDF of a PDF with no scanned page is refused (the menu does not offer it)",
          error is not None and "nothing to recognise" in str(error), repr(error))
    # Never over the input.
    same = WORK / "same-name.pdf"
    shutil.copy(WORK / "mixed.pdf", same)
    before = same.read_bytes()
    error = raises(lambda: oc.convert(same, "pdf", "ocrpdf", WORK, "same-name"), oc.ConversionError)
    check("the result never replaces the original", error is not None and same.read_bytes() == before, repr(error))


def nothing_found():
    white = WORK / "white.png"
    Image.new("RGB", (800, 400), (255, 255, 255)).save(white)
    for target in ("txt", "ocrpdf"):
        error = raises(lambda: convert(white, "png", target, f"white-{target}"), oc.ConversionError)
        check(f"a picture with no text, to {target}: the refusal",
              error is not None and str(error) == "No text was found in that picture.", repr(error))
        check(f"... and no file is left ({target})", not list(WORK.glob(f"white-{target}.*")))
    scan = WORK / "scan-blank.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=420, height=260)
    out = io.BytesIO()
    Image.new("RGB", (800, 500), (250, 250, 250)).save(out, format="JPEG")
    page.insert_image(page.rect, stream=out.getvalue())
    doc.save(str(scan))
    doc.close()
    for target in ("txt", "ocrpdf"):
        error = raises(lambda: convert(scan, "pdf", target, f"scan-blank-{target}"), oc.ConversionError)
        check(f"a scanned PDF with no text, to {target}: the refusal",
              error is not None and str(error) == "No text was found in that PDF.", repr(error))
        check(f"... and no file is left ({target})", not list(WORK.glob(f"scan-blank-{target}.*")))


def bad_input():
    png = text_png(WORK / "ok.png", "Some words")
    cases = (
        ("a camera raw photo", lambda: oc.convert(png, "dng", "txt", WORK, "r")),
        ("an SVG", lambda: oc.convert(png, "svg", "txt", WORK, "s")),
        ("an audio file", lambda: oc.convert(png, "mp3", "txt", WORK, "a")),
        ("a target it does not write", lambda: oc.convert(png, "png", "docx", WORK, "d")),
        ("a file that is not a picture", lambda: oc.convert(WORK / "not.pdf", "png", "txt", WORK, "n")),
        ("a file that is not a PDF", lambda: oc.convert(WORK / "not.pdf", "pdf", "txt", WORK, "n2")),
    )
    for label, call in cases:
        error = raises(call, oc.ConversionError)
        check(f"{label}: a ConversionError with a sentence", error is not None and str(error).strip(), repr(error))
    cut = WORK / "cut.png"
    cut.write_bytes(png.read_bytes()[:200])
    check("a truncated picture: a ConversionError", raises(lambda: oc.convert(cut, "png", "txt", WORK, "cut"),
                                                            oc.ConversionError) is not None)
    check("an out-of-memory error is an OutOfMemory (a ConversionError of its own kind)",
          issubclass(oc.OutOfMemory, oc.ConversionError))


LOREM = "This is an ordinary line of body text in a normal report, long enough to count. " * 18


def scan_page_count_rules():
    """What `is_scanned`, the one predicate behind the count, the price and the
    work, takes a page for. A page needs a picture on it (found 2026-10-10: a
    report with two blank pages was priced as a text-recognition job, and a
    deck of 31 title slides was refused for having 31 "scanned" pages)."""
    class Page:
        """A page that is not to be asked for its text."""
        def __init__(self, images, width=420, height=260):
            self._images = images
            self.rect = pymupdf.Rect(0, 0, width, height)

        def get_images(self, *args, **kwargs):
            return self._images

        def get_text(self, *args, **kwargs):
            raise AssertionError("the text of a page that cannot be a scan was asked for")

    check("is_scanned: a page with no picture is not scanned, and its text is not even asked for",
          oc.is_scanned(Page([])) is False)
    check("... nor is a page with a picture too big to draw at a readable size, and its text is not asked for either",
          oc.is_scanned(Page([(7, 0, 100, 100)], 14400, 14400)) is False)

    # Real pages: every kind a stranger's PDF can hold.
    doc = pymupdf.open()
    doc.new_page()                                                         # blank
    title = doc.new_page()
    title.insert_text((200, 400), "Chapter 3", fontsize=30)
    textbox = doc.new_page()
    textbox.insert_textbox(pymupdf.Rect(50, 50, 550, 750), LOREM, fontsize=10)
    add_scan_page(doc, SCAN_1)                                             # a plain scan
    scan_with_stamp = add_scan_page(doc, SCAN_1)
    scan_with_stamp.insert_text((10, 20), "Page 7", fontsize=8)           # under 20 characters: still a scan
    text_over_picture = add_scan_page(doc, SCAN_1)
    text_over_picture.insert_textbox(pymupdf.Rect(10, 10, 410, 250), LOREM, fontsize=10)
    placed = doc.new_page(width=420, height=260)                           # a scan placed as a form (show_pdf_page)
    source = pymupdf.open()
    add_scan_page(source, SCAN_3)
    placed.show_pdf_page(placed.rect, source, 0)
    huge = doc.new_page(width=14400, height=14400)
    huge.insert_image(pymupdf.Rect(0, 0, 400, 300), stream=scan_jpeg(SCAN_1))
    verdicts = [oc.is_scanned(doc[i]) for i in range(doc.page_count)]
    check("is_scanned on real pages: blank, a title, a page of text, a scan, a scan with a stamp, a scan with its text on it, "
          "a scan placed as a form, a page too big to read",
          verdicts == [False, False, False, True, True, False, True, False], str(verdicts))
    doc.close()

    # The two documents the reviewer found.
    report = WORK / "report.pdf"
    doc = pymupdf.open()
    for kind in ["text"] * 5 + ["blank"] + ["text"] * 5 + ["blank"]:
        page = doc.new_page()
        if kind == "text":
            page.insert_textbox(pymupdf.Rect(50, 50, 550, 750), LOREM, fontsize=10)
    doc.save(str(report))
    doc.close()
    counted = oc.count_scanned(report)
    check("a report of 10 text pages and 2 blank ones has no scanned page", counted == {"pages": 12, "scanned": 0}, str(counted))
    size = report.stat().st_size
    check("... so its TXT is priced exactly as before text was recognised, and is free",
          cu.price_for_job("pdf", "txt", size, counted["pages"], counted["scanned"])
          == cu.price_for_job("pdf", "txt", size, counted["pages"]) and cu.price_for_job("pdf", "txt", size, 12, 0)[0] == 0)
    check("... and the work: TXT of it is today's, with no recogniser asked and no page read",
          cu.ocr_job_pages("pdf", "txt", counted["scanned"]) == 0 and cu.ocr_job_pages("pdf", "ocrpdf", counted["scanned"]) == 0)
    deck = WORK / "deck.pdf"
    doc = pymupdf.open()
    for kind in ["title"] * (oc.OCR_MAX_PAGES + 1) + ["text"] * 9:
        page = doc.new_page()
        if kind == "title":
            page.insert_text((200, 400), "Chapter 3", fontsize=30)
        else:
            page.insert_textbox(pymupdf.Rect(50, 50, 550, 750), LOREM, fontsize=10)
    doc.save(str(deck))
    doc.close()
    counted = oc.count_scanned(deck)
    check(f"a deck of {oc.OCR_MAX_PAGES + 1} title-only slides and 9 text ones has no scanned page (it was refused as having "
          f"{oc.OCR_MAX_PAGES + 1})", counted == {"pages": oc.OCR_MAX_PAGES + 10, "scanned": 0}, str(counted))
    import doc_convert
    old = doc_convert.convert(deck, "pdf", "txt", WORK, "deck-old").path.read_bytes()
    new = convert(deck, "pdf", "txt", "deck-new").path.read_bytes()
    check("... and its TXT is the ordinary one", old == new)

    # The conversion asks the same predicate: a page the count did not take for a scan is not read.
    nothing = blank_pdf(WORK / "nothing-pages.pdf", 2)
    with hooks() as seen:
        raises(lambda: convert(nothing, "pdf", "txt", "nothing-again"), oc.ConversionError)
    check("a PDF of pages with nothing on them: no page is read", not seen["engine_pixels"], str(seen["engine_pixels"]))


def count_scanned_in_steps():
    """What the child of jobs.describe_pdf is built from: the page count first,
    nothing looked at where text cannot be read, and an end to the looking."""
    path = mixed_pdf(WORK / "steps.pdf")
    announced, order = [], []
    real = oc.is_scanned
    oc.is_scanned = lambda page: (order.append(list(announced)), real(page))[1]
    try:
        got = oc.count_scanned(path, on_pages=announced.append)
    finally:
        oc.is_scanned = real
    check("on_pages is told the page count, and before any page is looked at",
          got == {"pages": 5, "scanned": 3} and announced == [5] and order == [[5]] * 5, f"{got} {announced} {order}")
    announced, order = [], []
    oc.is_scanned = lambda page: (order.append(1), real(page))[1]
    try:
        got = oc.count_scanned(path, on_pages=announced.append, scan=False)
    finally:
        oc.is_scanned = real
    check("scan=False says the pages and looks at none: scanned is None",
          got == {"pages": 5, "scanned": None} and announced == [5] and not order, f"{got} {announced} {order}")
    many = paper_pdf(WORK / "steps-many.pdf", 40)
    order = []
    oc.is_scanned = lambda page: (order.append(1), real(page))[1]
    try:
        got = oc.count_scanned(many, stop_after=3)
        looked = len(order)
        del order[:]
        whole = oc.count_scanned(many)
        looked_whole = len(order)
        del order[:]
        capped = oc.count_scanned(many, stop_after=oc.OCR_MAX_PAGES)
        looked_capped = len(order)
        del order[:]
        short = oc.count_scanned(paper_pdf(WORK / "steps-few.pdf", 5), stop_after=oc.OCR_MAX_PAGES)
    finally:
        oc.is_scanned = real
    check("stop_after ends the looking at the first page past it, and the count is stop_after + 1",
          got == {"pages": 40, "scanned": 4} and looked == 4, f"{got} {looked}")
    check("... without it every page is looked at", whole == {"pages": 40, "scanned": 40} and looked_whole == 40,
          f"{whole} {looked_whole}")
    check(f"... at the most a conversion reads, 40 pages count as {oc.OCR_MAX_PAGES + 1}",
          capped == {"pages": 40, "scanned": oc.OCR_MAX_PAGES + 1} and looked_capped == oc.OCR_MAX_PAGES + 1,
          f"{capped} {looked_capped}")
    check("... and a PDF under it is counted whole", short == {"pages": 5, "scanned": 5}, str(short))


def grey_16_bit():
    """A 16-bit grey picture is taken to 8 bits as it always was (the high
    byte), without the whole picture being made a 32-bit one on the way: a
    64 MP scan peaked near 640 MB."""
    def old(im):
        wide = im if im.mode == "I" else im.convert("I")
        return wide.point(lambda value: value * (1 / 256)).convert("L")

    every = array.array("H", range(65536)).tobytes()
    pictures = [("I;16", Image.frombytes("I;16", (256, 256), every)),
                ("I;16B", Image.frombytes("I;16B", (256, 256), every)),
                ("I;16L", Image.frombytes("I;16L", (256, 256), every))]
    wide = Image.new("I", (1000, 1))
    wide.putdata(list(range(-500, 70500, 71)))                  # below zero, every byte, and past 16 bits
    pictures.append(("I", wide))
    for label, picture in pictures:
        got = oc._eight_bit(picture)
        check(f"a {label} picture comes out as the same L picture as before", got.mode == "L"
              and got.size == picture.size and got.tobytes() == old(picture).tobytes(), f"{got.mode} {got.size}")
    # In bands: the same, with the last band short and with a band of one row.
    real_band = oc._GREY_BAND_PIXELS
    try:
        for band in (256 * 5 + 7, 10):
            oc._GREY_BAND_PIXELS = band
            for label, picture in pictures[:3]:
                check(f"... a {label} picture in bands of {band} pixels is the same too",
                      oc._eight_bit(picture).tobytes() == old(picture).tobytes())
    finally:
        oc._GREY_BAND_PIXELS = real_band
    check("... and L, RGB and RGBA are handed back as they are (no copy)",
          all(oc._eight_bit(image) is image for image in (Image.new("L", (3, 3)), Image.new("RGB", (3, 3)), Image.new("RGBA", (3, 3)))))

    # What is made on the way. Every picture Pillow makes from another goes through _new.
    made = []
    real_new = Image.Image._new

    def spy(self, im):
        out = real_new(self, im)
        made.append((out.mode, out.size[0] * out.size[1]))
        return out
    big = Image.new("I;16", (1000, 2000))
    big32 = Image.new("I", (1000, 2000))                         # made before the spy is put in
    Image.Image._new = spy
    try:
        grey = oc._eight_bit(big)
        by_16 = list(made)
        del made[:]
        oc._eight_bit(big32)
        by_32 = list(made)
    finally:
        Image.Image._new = real_new
    wide_sizes = [pixels for mode, pixels in by_16 if mode in ("I", "F")]
    check("a 2 MP 16-bit picture: no 32-bit picture of the whole of it is ever made (at most a band is)",
          grey.mode == "L" and wide_sizes and max(wide_sizes) <= oc._GREY_BAND_PIXELS < 2_000_000, str(by_16))
    check("... and a 32-bit one goes to 8 bits in one step, with nothing but L made",
          {mode for mode, _ in by_32} == {"L"}, str(by_32))


def selftest():
    start = __import__("time").monotonic()
    error = raises(oc.selftest, BaseException)
    took = __import__("time").monotonic() - start
    check("selftest() passes where the data is, in under five seconds", error is None and took < 5, f"{error!r} {took:.1f}s")
    keep = os.environ.get("TESSDATA_PREFIX")
    empty = tempfile.mkdtemp(prefix="nodata-", dir=WORK)
    os.environ["TESSDATA_PREFIX"] = empty
    captured = io.StringIO()
    try:
        with contextlib.redirect_stderr(captured):
            error = raises(oc.selftest, BaseException)
    finally:
        os.environ["TESSDATA_PREFIX"] = keep
    check("... and exits 1 with a one-line reason where it is not",
          isinstance(error, SystemExit) and error.code == 1 and len(captured.getvalue().strip().splitlines()) == 1
          and "eng" in captured.getvalue(), f"{error!r} {captured.getvalue()!r}")


def main():
    constants()
    dpi()
    availability()
    if not CAN_READ:
        why = "PyMuPDF with the engine, Pillow or the language data is missing"
        for name in ("the four languages are read back", "a PDF of text and scans gives TXT", "... and a searchable PDF",
                     "pictures, to TXT and to a searchable PDF", "pictures in every format", "big pictures and big pages", "a page too big to read",
                     "counts and limits", "what a scanned page is", "counting in steps", "16-bit grey",
                     "no text found", "bad input", "selftest"):
            skip(name, why)
        return
    for group in (languages, mixed, searchable_pdf, pictures, picture_formats, big_pictures, huge_page, counts_and_limits,
                  scan_page_count_rules, count_scanned_in_steps, grey_16_bit, nothing_found, bad_input, selftest):
        try:
            group()
        except Exception as error:                       # a crash is a failure, not the end of the run
            import traceback
            check(f"{group.__name__} ran to the end", False, "".join(traceback.format_exception_only(type(error), error)).strip())
            traceback.print_exc()


if __name__ == "__main__":
    try:
        main()
    finally:
        shutil.rmtree(WORK, ignore_errors=True)
    print()
    if failures:
        print(f"FAILURES: {len(failures)}")
        for f in failures:
            print("  -", f)
        sys.exit(1)
    print("All OCR checks passed.")
