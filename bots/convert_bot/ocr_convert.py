"""Text from pictures and scanned PDFs, recognised on this machine.

Four conversions: a picture to TXT, a picture to a searchable PDF ("ocrpdf",
a pseudo-target whose file is `<stem>.pdf`), a PDF to TXT and a PDF to a
searchable PDF. The engine is Tesseract, and it is already in the bot: MuPDF
links it, so PyMuPDF can write a page with an invisible text layer
(`Pixmap.pdfocr_tobytes`). There is no `tesseract` binary and no pytesseract;
the only thing added to the image is the language data (`tessdata_dir`).
Nothing is sent anywhere: the text is read on the machine that holds the file.

A page counts as "scanned" when its own text is shorter than
OCR_MIN_TEXT_CHARS (`is_scanned`). The same predicate prices the job
(`count_scanned`, which the bot asks in a process of its own, as it does for a
RAW photo) and does the work, so the price never counts a page that will not
be read and the worker never reads one the price did not count. A page with
text keeps its own: TXT takes it as it is, the searchable PDF leaves the page
alone.

## What it costs, measured 2026-10-08/09 (PyMuPDF 1.28.2, the laptop)

  time      1.1-2.0 s a page at 300 dpi; production is estimated 1.5-2.5x slower.
            The worst seen was 94 s of CPU on a 12 MP image of pure noise, so a
            page that runs away is the worker's time limit's job (jobs.py), not
            this module's.
  memory    17-19 MB per megapixel of the picture handed to the engine: a 48 MP
            photograph killed the container. So no picture reaches it over
            OCR_MAX_PIXELS: a page is drawn at the dpi that keeps it under
            (`ocr_dpi`), and a photograph is brought down while it is decoded
            (`_picture`). MuPDF's own cache is the other half: it grew 26 MB a
            page, up to 256 MB, until it was shrunk after every page.
  size      the text layer is laid over the page that was there (rather than
            the page being replaced by the engine's), +5 KB a page. The page
            keeps its size, rotation and picture and renders pixel for pixel
            as before at 0, 90, 180 and 270 degrees.
"""
from __future__ import annotations

import math
import os
import sys
from pathlib import Path

import convert_utils
import formats
from convert_utils import ConversionError, OutOfMemory, Result, report

# A picture handed to the engine is never over this many pixels.
OCR_MAX_PIXELS = int(os.environ.get("CONVERT_OCR_MAX_PIXELS") or 12_000_000)
# Seconds a page is allowed to take, which is what jobs.ocr_time_limit() and so
# the price are made of: measured 1.1-2.0 s on the laptop, production perhaps
# 2.5x that, so about three times what is expected.
OCR_PAGE_S = int(os.environ.get("CONVERT_OCR_PAGE_SECONDS") or 15)
# The most scanned pages one job reads; the menu does not offer past it.
OCR_MAX_PAGES = int(os.environ.get("CONVERT_OCR_MAX_PAGES") or 30)
# The most the menu waits, in total, for a PDF's pages to be counted
# (jobs.describe_pdf). The bot handles one update at a time and the menu is
# drawn inside that handler, so a stranger's PDF that takes the counting
# minutes (a page of a million glyphs is 1.8 KB and took 17 s) would hold every
# other person's buttons for as long. Past this the child is killed, and the
# menu is drawn with the page count and no scanned pages: today's TXT, no OCR.
OCR_DESCRIBE_SECONDS = float(os.environ.get("CONVERT_OCR_DESCRIBE_SECONDS") or 5)
# Scanned pages a job may have and still be free (when its size is, too).
OCR_FREE_PAGES = int(os.environ.get("CONVERT_OCR_FREE_PAGES") or 1)
# A page whose own text is shorter than this (whitespace left out) is a picture
# of one. Not zero: a scan often carries a stamp or a page number as real text.
OCR_MIN_TEXT_CHARS = int(os.environ.get("CONVERT_OCR_MIN_TEXT_CHARS") or 20)
# Below this the engine reads garbage. A page that would need fewer dpi to stay
# under OCR_MAX_PIXELS (a poster, a drawing: past about 5000 pt a side) is not
# recognised, and so not counted either.
OCR_MIN_DPI = int(os.environ.get("CONVERT_OCR_MIN_DPI") or 50)
# A picture that is not a JPEG is decoded whole, so past this many pixels it is
# refused before anything is decoded (a JPEG is decoded at a fraction of its
# size -- see _picture -- and is judged by what it is decoded to).
OCR_MAX_DECODE_PIXELS = int(os.environ.get("CONVERT_OCR_MAX_DECODE_PIXELS") or 64_000_000)
MAX_DPI = 300

WRITES = frozenset({"txt", "ocrpdf"})

# The person's choice (user_data["ocr_lang"]) -> what Tesseract is told. Uzbek is
# written in two scripts and one pass reads both, at no measurable extra cost.
# The order is the order the toggle cycles in.
LANGS = {"eng": "eng", "rus": "rus", "uzb": "uzb+uzb_cyrl"}
CHOICES = tuple(LANGS)
# What a person who has not chosen is given: the language they use the bot in.
DEFAULT_CHOICE = {"en": "eng", "ru": "rus", "uz": "uzb"}
# What has to be on disk for all of them.
_TRAINEDDATA = ("eng", "rus", "uzb", "uzb_cyrl")


def tesseract_language(choice) -> str:
    """The language string for a choice. Anything unknown or empty is English:
    a job queued by an older build, or a hand-edited row, still runs."""
    return LANGS.get(choice or "", LANGS["eng"])


def default_choice(lang) -> str:
    """The text language a person gets before they have chosen one."""
    return DEFAULT_CHOICE.get(lang or "", "eng")


def tessdata_dir() -> "str | None":
    """TESSDATA_PREFIX when it holds all four language files, else None."""
    folder = os.environ.get("TESSDATA_PREFIX") or ""
    if folder and all(os.path.isfile(os.path.join(folder, f"{name}.traineddata")) for name in _TRAINEDDATA):
        return folder
    return None


def available() -> bool:
    # Read from the installed version, not by importing it: since PyMuPDF 2.0 the import also
    # loads pymupdf4llm and onnxruntime, some 90 MB, into a bot that may never see a document.
    import importlib.metadata
    import re
    try:
        found = re.match(r"(\d+)\.(\d+)", importlib.metadata.version("PyMuPDF"))
    except importlib.metadata.PackageNotFoundError:
        return False
    if found is None or (int(found.group(1)), int(found.group(2))) < (1, 24):
        return False
    return tessdata_dir() is not None


def _pymupdf():
    try:
        import pymupdf
        return pymupdf
    except ImportError as exc:      # pragma: no cover - formats.py gates this
        raise ConversionError("Text recognition isn't available on this host.") from exc


def _tidy(exc: Exception) -> str:
    text = " ".join(str(exc).split())
    return text[:160] or type(exc).__name__


# ---------------------------------------------------------------------------
# Which pages, and at what size
# ---------------------------------------------------------------------------

def ocr_dpi(width_pt, height_pt) -> "int | None":
    """The dpi to draw a page of this size at for the engine: 300 where that
    stays under OCR_MAX_PIXELS, less where it does not (an A0 poster comes out
    at 87 or so). None when that would be under OCR_MIN_DPI, and for a page
    with no size (zero, negative, NaN): it is not recognised.

    The drawing is rounded out to whole pixels on each side, so one pixel more
    a side is allowed for -- without it a picture exactly at the limit came
    out a few thousand pixels over it."""
    try:
        width, height = float(width_pt), float(height_pt)
    except (TypeError, ValueError):
        return None
    if not (width > 0 and height > 0):
        return None
    dpi = min(MAX_DPI, math.floor(72 * math.sqrt(OCR_MAX_PIXELS / (width * height)) + 1e-6))
    while dpi >= OCR_MIN_DPI and ((math.ceil(width * dpi / 72) + 1)
                                  * (math.ceil(height * dpi / 72) + 1)) > OCR_MAX_PIXELS:
        dpi -= 1
    return dpi if dpi >= OCR_MIN_DPI else None


def is_scanned(page) -> bool:
    """A page that is a picture of text: there is a picture on it, it has too
    little text of its own, and it can be drawn at a size the engine can read.
    The one predicate the count (and so the price) and the conversion both use.

    The picture comes first. A page with none has nothing to read -- a blank
    separator page, a title slide, a drawn diagram -- and counting those priced
    an ordinary report with two blank pages as a text-recognition job and
    refused a deck of title slides (reviewed 2026-10-10). It is also the cheap
    test: the page's list of pictures is read from its resources, where the text
    is laid out from its whole content."""
    if not page.get_images():
        return False
    rect = page.rect
    if ocr_dpi(rect.width, rect.height) is None:
        return False
    return len("".join(page.get_text("text").split())) < OCR_MIN_TEXT_CHARS


def _open_pdf(path):
    pymupdf = _pymupdf()
    try:
        doc = pymupdf.open(str(path))
    except Exception as exc:
        raise ConversionError(f"Couldn't read that PDF: {_tidy(exc)}") from exc
    if not doc.is_pdf:
        doc.close()
        raise ConversionError("Couldn't read that as a PDF.")
    if doc.needs_pass:
        doc.close()
        raise ConversionError("That PDF is password-protected, so its pages can't be read.")
    return doc


def count_scanned(path, on_pages=None, scan: bool = True, stop_after: "int | None" = None) -> dict:
    """{"pages": n, "scanned": m} for a PDF: its page count, and how many of
    its first formats.MAX_PAGES pages are scanned. Read here, in a process of
    its own (jobs.describe_pdf), because MuPDF reading a stranger's file is not
    something the bot's own process should do. Raises ConversionError for a file
    that is not a readable PDF.

    That process is on a deadline (OCR_DESCRIBE_SECONDS), and looking at the
    pages can be slow where counting them is not, so the answer is given in
    two steps. `on_pages(n)` is called with the page count as soon as the file
    is open, before any page is looked at. `scan=False` looks at none: the
    answer is {"pages": n, "scanned": None} (a host that cannot read text has
    no use for the number). `stop_after` ends the looking once more than that
    many pages are scanned, with `stop_after + 1` as the count: nothing needs
    the exact number past the most a conversion reads."""
    doc = _open_pdf(path)
    try:
        pages = int(doc.page_count)
        if on_pages is not None:
            on_pages(pages)
        if not scan:
            return {"pages": pages, "scanned": None}
        scanned = 0
        for index in range(min(pages, formats.MAX_PAGES)):
            page = doc.load_page(index)
            if is_scanned(page):
                scanned += 1
            del page
            if stop_after is not None and scanned > stop_after:
                break
        return {"pages": pages, "scanned": scanned}
    except ConversionError:
        raise
    except MemoryError:
        raise OutOfMemory("reading that PDF ran out of memory") from None
    except Exception as exc:
        raise ConversionError(f"Couldn't read that PDF: {_tidy(exc)}") from exc
    finally:
        doc.close()


# ---------------------------------------------------------------------------
# One page
# ---------------------------------------------------------------------------

def _recognise(pymupdf, page, language: str, tessdata: str, layer: bool) -> str:
    """The text on one scanned page. With `layer`, the page also gets an
    invisible copy of it, laid over what was there.

    The engine makes a PDF page of its own holding the picture it was given and
    the text; the picture is deleted from it (kept, it doubled the file), and
    what is left is laid over the original page. The page keeps its size and
    rotation, so it is not replaced and nothing about it moves."""
    dpi = ocr_dpi(page.rect.width, page.rect.height)
    if dpi is None:                         # callers have asked is_scanned(); this is the backstop
        return ""
    pix = page.get_pixmap(dpi=dpi)
    try:
        data = pix.pdfocr_tobytes(compress=True, language=language, tessdata=tessdata)
    finally:
        del pix
    ocr = pymupdf.open("pdf", data)
    del data
    try:
        text = ocr[0].get_text()
        if layer and text.strip():
            for item in ocr[0].get_images(full=True):
                ocr[0].delete_image(item[0])
            # The layer was drawn from the page as shown, so on a turned page
            # (/Rotate 90, 180, 270) it is turned back to sit on the page as
            # stored. Laid on as it is, the words ran sideways beside the
            # picture and everything past the stored width was cut off, and
            # nothing in how the page looks gives that away (found 2026-10-09).
            rotation = page.rotation
            page.set_rotation(0)
            try:
                page.show_pdf_page(page.rect, ocr, 0, overlay=True, rotate=rotation)
            finally:
                page.set_rotation(rotation)
    finally:
        ocr.close()
        # MuPDF keeps what it decoded and laid out, and without this it held
        # on to 26 MB more with every page, up to 256 MB.
        pymupdf.TOOLS.store_shrink(100)
    return text


# ---------------------------------------------------------------------------
# A PDF
# ---------------------------------------------------------------------------

def _pdf(in_path, target_ext: str, out_path: Path, language: str, tessdata: str,
         max_scanned: "int | None" = None) -> None:
    import doc_convert
    pymupdf = _pymupdf()
    doc = _open_pdf(in_path)
    try:
        pages = doc_convert._page_count(doc)            # the limit every PDF conversion has
        if not pages:
            raise ConversionError("That document had no pages in it.")
        scanned = []
        for index in range(pages):
            page = doc.load_page(index)
            if is_scanned(page):
                scanned.append(index)
            del page
        if len(scanned) > OCR_MAX_PAGES:
            raise ConversionError(
                f"{len(scanned)} pages of that PDF are pictures without text. Text is recognised on at "
                f"most {OCR_MAX_PAGES} such pages in one conversion. Split it and send the part you need.")
        if max_scanned is not None and len(scanned) > max_scanned:
            # The job was priced for max_scanned pages; the menu counted them with
            # this same predicate, so this is the file changing, not a rounding.
            raise ConversionError(
                f"{len(scanned)} pages of that PDF are pictures without text, more than the {max_scanned} "
                "this conversion was priced for. Send the PDF again.")
        if target_ext == "ocrpdf" and not scanned:
            raise ConversionError("Every page of that PDF has its text already, so there is nothing to recognise.")

        wanted = set(scanned)
        texts = []                  # for TXT: one entry per page, in order
        found = False
        done = 0
        # A searchable PDF only touches the scanned pages; TXT goes through all of them.
        for index in (range(pages) if target_ext == "txt" else scanned):
            page = doc.load_page(index)
            if index in wanted:
                report(done, len(scanned))
                text = _recognise(pymupdf, page, language, tessdata, layer=target_ext == "ocrpdf")
                done += 1
            else:
                text = page.get_text()          # a page with its own text (TXT only)
            found = found or bool(text.strip())
            del page
            if target_ext == "txt":
                texts.append(text)
        report(len(scanned), len(scanned))

        if not found:
            raise ConversionError("No text was found in that PDF.")
        if target_ext == "txt":
            # Pages joined as a PDF's text is everywhere else in this bot
            # (doc_convert._to_text): each page's own newline, then one more.
            out_path.write_text("\n".join(texts), encoding="utf-8")
        else:
            doc.save(str(out_path), garbage=3, deflate=True)
    finally:
        doc.close()


# ---------------------------------------------------------------------------
# A picture
# ---------------------------------------------------------------------------

_EXIF_ORIENTATION = 0x0112


def _too_large(pixels: float = 0) -> ConversionError:
    size = f"{pixels / 1_000_000:.0f} megapixels" if pixels else "too big"
    return ConversionError(
        f"That picture is {size}. Text is read from pictures up to "
        f"{OCR_MAX_DECODE_PIXELS // 1_000_000} megapixels here, or from a JPEG of any size the bot takes. "
        "Send a smaller copy.")


# 16-bit grey to 8: the high byte, which is what dividing by 256 gave. The one
# lookup table the 32-bit "I" mode is taken to "L" with (Pillow reads 65536
# entries for that; an index outside them is clipped to the first or the last).
_GREY_16_TO_8 = [value >> 8 for value in range(65536)]
# Rows of a 16-bit picture turned to grey at a time: about a megapixel, so the
# 32-bit copy that conversion needs is 4 MB and not four bytes a pixel of the
# whole picture (a 64 MP scan peaked near 640 MB when it was made whole).
_GREY_BAND_PIXELS = 1_000_000


def _eight_bit(im):
    """The picture as L, RGB or RGBA, which is what a resize and a JPEG can
    take. A 1-bit fax scan and a 16-bit grey scan become grey; the rest RGB,
    keeping transparency until it is flattened."""
    from PIL import Image
    mode = im.mode
    if mode in ("L", "RGB", "RGBA"):
        return im
    if mode == "1":
        return im.convert("L")
    if mode == "I":
        # 16-bit grey: Pillow's own conversion would clip everything over 255
        # to white. One lookup table, straight to 8 bits.
        return im.point(_GREY_16_TO_8, "L")
    if mode.startswith("I;16"):
        # The same, a band of rows at a time: the table is for the 32-bit mode,
        # and turning a whole 16-bit picture into that first is the 640 MB.
        width, height = im.size
        rows = max(1, _GREY_BAND_PIXELS // max(1, width))
        grey = Image.new("L", im.size)
        for top in range(0, height, rows):
            band = im.crop((0, top, width, min(height, top + rows)))
            grey.paste(band.convert("I").point(_GREY_16_TO_8, "L"), (0, top))
        return grey
    if mode in ("LA", "PA") or (mode == "P" and "transparency" in im.info):
        return im.convert("RGBA")
    return im.convert("RGB")


def _tiff_pages(im) -> int:
    """How many pages (directories) an opened TIFF holds, counted from the file's
    headers and without decoding a pixel. 1 when that cannot be told: the decode
    that follows reports a file that is damaged."""
    try:
        im.seek(1)
    except Exception:
        return 1                        # EOFError for the one page; anything else is the decode's to report
    try:
        return max(2, int(im.n_frames))
    except Exception:
        return 2
    finally:
        try:
            im.seek(0)
        except Exception:
            pass


def _picture(in_path):
    """A picture ready to be a PDF page: (bytes, format name, width, height).

    A big JPEG is decoded at a fraction of its size (Pillow's `draft`, which
    makes libjpeg scale while it decodes), so the full photograph is never in
    memory. Anything else is decoded whole and resized, which is why one over
    OCR_MAX_DECODE_PIXELS is refused first. The picture comes out at most
    OCR_MAX_PIXELS, the right way up, on white where it was see-through."""
    import io
    from PIL import Image
    convert_utils.register_openers()
    try:
        im = Image.open(in_path)
    except Image.DecompressionBombError as exc:
        raise _too_large() from exc
    except MemoryError:
        raise
    except Exception as exc:
        raise ConversionError(f"Couldn't read that as an image: {_tidy(exc)}") from exc
    try:
        source_format = im.format
        pages = _tiff_pages(im) if source_format == "TIFF" else 1
        if pages > 1:
            # Pillow decodes the first and says nothing of the rest, so the
            # text of every other page was dropped without a word. A scan of a
            # document is one page each in a PDF; say so rather than guess.
            raise ConversionError(
                f"That TIFF holds {pages} pages. Text is read from a one-page picture: "
                "convert the TIFF to PDF first, then choose TXT or Searchable PDF.")
        width, height = im.size
        if width * height > OCR_MAX_PIXELS:
            scale = math.sqrt(OCR_MAX_PIXELS / (width * height))
            im.draft("RGB", (max(1, math.ceil(width * scale)), max(1, math.ceil(height * scale))))
        # Judged by what is about to be decoded: after the draft for a JPEG,
        # the whole picture for anything else.
        if im.size[0] * im.size[1] > OCR_MAX_DECODE_PIXELS:
            raise _too_large(width * height)
        try:
            im.load()
        except MemoryError:
            raise OutOfMemory("decoding that picture ran out of memory") from None
        except Exception as exc:
            raise ConversionError(f"Couldn't read that as an image: {_tidy(exc)}") from exc
        # Asked after the ceiling above, not before it: a PNG's EXIF can follow
        # its pixels, so Pillow decodes the whole picture to find it.
        orientation = 1
        if source_format not in convert_utils._ORIENTED_BY_CONTAINER:
            try:
                orientation = im.getexif().get(_EXIF_ORIENTATION, 1)
            except Exception:
                orientation = 1
        picture = _eight_bit(im)
        if picture is not im:
            im.close()
        if picture.size[0] * picture.size[1] > OCR_MAX_PIXELS:
            scale = math.sqrt(OCR_MAX_PIXELS / (picture.size[0] * picture.size[1]))
            smaller = picture.resize((max(1, math.floor(picture.size[0] * scale)),
                                      max(1, math.floor(picture.size[1] * scale))), Image.LANCZOS)
            picture.close()
            picture = smaller
        if picture.mode == "RGBA":
            flat = convert_utils._flatten_onto_white(picture)
            picture.close()
            picture = flat
        if orientation in (2, 3, 4, 5, 6, 7, 8):
            turn = Image.Transpose
            turned = picture.transpose({2: turn.FLIP_LEFT_RIGHT, 3: turn.ROTATE_180, 4: turn.FLIP_TOP_BOTTOM,
                                        5: turn.TRANSPOSE, 6: turn.ROTATE_270, 7: turn.TRANSVERSE,
                                        8: turn.ROTATE_90}[orientation])
            picture.close()
            picture = turned
        out = io.BytesIO()
        try:
            if source_format == "PNG":
                picture.save(out, format="PNG")
            else:
                picture.save(out, format="JPEG", quality=90)
        except MemoryError:
            raise OutOfMemory("saving that picture ran out of memory") from None
        except Exception as exc:
            raise ConversionError(f"Couldn't read that as an image: {_tidy(exc)}") from exc
        result = (out.getvalue(), picture.size[0], picture.size[1])
        picture.close()
        return result
    finally:
        im.close()


def _image(in_path, src_ext: str, target_ext: str, out_path: Path, language: str, tessdata: str) -> None:
    pymupdf = _pymupdf()
    data, width, height = _picture(in_path)
    # One page the size of the picture at 300 dpi, holding the picture: from
    # here it is a scanned page like any other, so the picture becomes a
    # searchable PDF by the same code a scanned PDF does.
    doc = pymupdf.open()
    try:
        page = doc.new_page(width=max(width, 1) * 72 / MAX_DPI, height=max(height, 1) * 72 / MAX_DPI)
        page.insert_image(page.rect, stream=data)
        del data
        report(0, 1)
        text = _recognise(pymupdf, page, language, tessdata, layer=target_ext == "ocrpdf")
        report(1, 1)
        if not text.strip():
            raise ConversionError("No text was found in that picture.")
        if target_ext == "txt":
            out_path.write_text(text, encoding="utf-8")
        else:
            doc.save(str(out_path), garbage=3, deflate=True)
    finally:
        doc.close()


# ---------------------------------------------------------------------------
# The entry point
# ---------------------------------------------------------------------------

def convert(in_path, src_ext: str, target_ext: str, work_dir, stem: str = "converted",
            lang: str = "eng", max_scanned: "int | None" = None) -> Result:
    """Text from a picture or a PDF as `target_ext`: "txt", or "ocrpdf" (a
    searchable PDF, written as `<stem>.pdf`). `lang` is the person's choice
    (a key of LANGS). `max_scanned` is the number of pages the job was priced
    for (the menu's count): a file with more scanned pages than that is
    refused before any is read, so a job never recognises a page it was not
    sold. None: no limit but OCR_MAX_PAGES. Raises ConversionError, and
    OutOfMemory for a machine too small for the picture."""
    src_ext = formats.normalise(src_ext)
    target_ext = formats.normalise(target_ext)
    if target_ext not in WRITES:
        raise ConversionError(f"Text isn't recognised into .{target_ext}.")
    # The file settles which of the two a GIF, WEBP or APNG is, as it does in the dispatcher.
    animated = convert_utils.gif_is_animated(in_path) if src_ext in formats.ANIMATED_CAPABLE else None
    if animated:
        raise ConversionError("Text isn't recognised in an animation.")
    is_picture = (formats.category_of(src_ext, animated) == formats.IMAGE
                  and src_ext not in formats.RAW_FORMATS and src_ext != "svg")
    if src_ext != "pdf" and not is_picture:
        raise ConversionError(f"Text can't be recognised in a .{src_ext} file.")
    if src_ext != "pdf" and max_scanned is not None and max_scanned < 1:
        raise ConversionError("That conversion was not priced for reading any page.")
    tessdata = tessdata_dir()
    if tessdata is None:
        raise ConversionError("Text recognition isn't available on this host.")
    out_path = Path(work_dir) / f"{stem}.{'txt' if target_ext == 'txt' else 'pdf'}"
    if os.path.realpath(in_path) == os.path.realpath(out_path):
        raise ConversionError("The result would overwrite the original.")
    language = tesseract_language(lang)
    try:
        if src_ext == "pdf":
            _pdf(in_path, target_ext, out_path, language, tessdata, max_scanned)
        else:
            _image(in_path, src_ext, target_ext, out_path, language, tessdata)
    except ConversionError:
        out_path.unlink(missing_ok=True)
        raise
    except MemoryError:
        out_path.unlink(missing_ok=True)
        raise OutOfMemory("recognising the text ran out of memory") from None
    except Exception as exc:
        out_path.unlink(missing_ok=True)
        raise ConversionError(f"Couldn't recognise the text: {_tidy(exc)}") from exc
    return Result(out_path)


# ---------------------------------------------------------------------------
# The build-time check
# ---------------------------------------------------------------------------

def selftest() -> None:
    """Exits 1 with a one-line reason unless the language data is there and the
    engine reads a line of English and a line of Russian back. Run when the
    image is built (convert_bot/Dockerfile), so a build that cannot read text
    never ships. Under five seconds."""
    def fail(reason: str):
        sys.stderr.write(f"OCR self-test failed: {reason}\n")
        raise SystemExit(1)

    folder = os.environ.get("TESSDATA_PREFIX") or ""
    missing = [name for name in _TRAINEDDATA if not os.path.isfile(os.path.join(folder, f"{name}.traineddata"))]
    if missing:
        fail(f"no language data for {', '.join(missing)} in TESSDATA_PREFIX={folder!r}")
    try:
        import pymupdf
    except ImportError:
        fail("PyMuPDF is not installed")
    if not hasattr(pymupdf.Pixmap, "pdfocr_tobytes"):
        fail("this PyMuPDF has no text recognition (Pixmap.pdfocr_tobytes)")
    font = pymupdf.Font("helv")             # has the Cyrillic letters; no font file is needed
    for language, line, words in (("eng", "MumuBots OCR 2026", ("MumuBots", "OCR", "2026")),
                                  ("rus", "Проверка текста", ("Проверка", "текста"))):
        try:
            doc = pymupdf.open()
            page = doc.new_page(width=520, height=120)
            writer = pymupdf.TextWriter(page.rect)
            writer.append((20, 70), line, font=font, fontsize=36)
            writer.write_text(page)
            recognised = pymupdf.open("pdf", page.get_pixmap(dpi=200).pdfocr_tobytes(
                compress=True, language=language, tessdata=folder))[0].get_text()
        except Exception as exc:
            fail(f"reading {language} failed: {_tidy(exc)}")
        for word in words:
            if word not in recognised:
                fail(f"read {line!r} ({language}) as {' '.join(recognised.split())!r}")
