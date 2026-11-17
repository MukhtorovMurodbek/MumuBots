from __future__ import annotations

import math
import os
import sys
from pathlib import Path

import convert_utils
import formats
from convert_utils import ConversionError, OutOfMemory, Result, report

OCR_MAX_PIXELS = int(os.environ.get("CONVERT_OCR_MAX_PIXELS") or 12_000_000)
OCR_PAGE_S = int(os.environ.get("CONVERT_OCR_PAGE_SECONDS") or 15)
OCR_MAX_PAGES = int(os.environ.get("CONVERT_OCR_MAX_PAGES") or 30)
OCR_DESCRIBE_SECONDS = float(os.environ.get("CONVERT_OCR_DESCRIBE_SECONDS") or 5)
OCR_FREE_PAGES = int(os.environ.get("CONVERT_OCR_FREE_PAGES") or 1)
OCR_MIN_TEXT_CHARS = int(os.environ.get("CONVERT_OCR_MIN_TEXT_CHARS") or 20)
OCR_MIN_DPI = int(os.environ.get("CONVERT_OCR_MIN_DPI") or 50)
OCR_MAX_DECODE_PIXELS = int(os.environ.get("CONVERT_OCR_MAX_DECODE_PIXELS") or 64_000_000)
MAX_DPI = 300

WRITES = frozenset({"txt", "ocrpdf"})

LANGS = {"eng": "eng", "rus": "rus", "uzb": "uzb+uzb_cyrl"}
CHOICES = tuple(LANGS)
DEFAULT_CHOICE = {"en": "eng", "ru": "rus", "uz": "uzb"}
_TRAINEDDATA = ("eng", "rus", "uzb", "uzb_cyrl")


def tesseract_language(choice) -> str:
    return LANGS.get(choice or "", LANGS["eng"])


def default_choice(lang) -> str:
    return DEFAULT_CHOICE.get(lang or "", "eng")


def tessdata_dir() -> "str | None":
    folder = os.environ.get("TESSDATA_PREFIX") or ""
    if folder and all(os.path.isfile(os.path.join(folder, f"{name}.traineddata")) for name in _TRAINEDDATA):
        return folder
    return None


def available() -> bool:
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


def ocr_dpi(width_pt, height_pt) -> "int | None":
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


def _recognise(pymupdf, page, language: str, tessdata: str, layer: bool) -> str:
    dpi = ocr_dpi(page.rect.width, page.rect.height)
    if dpi is None:
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
            rotation = page.rotation
            page.set_rotation(0)
            try:
                page.show_pdf_page(page.rect, ocr, 0, overlay=True, rotate=rotation)
            finally:
                page.set_rotation(rotation)
    finally:
        ocr.close()
        pymupdf.TOOLS.store_shrink(100)
    return text


def _pdf(in_path, target_ext: str, out_path: Path, language: str, tessdata: str,
         max_scanned: "int | None" = None) -> None:
    import doc_convert
    pymupdf = _pymupdf()
    doc = _open_pdf(in_path)
    try:
        pages = doc_convert._page_count(doc)
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
                f"most {OCR_MAX_PAGES} such pages in one conversion. Split it and send it in parts.")
        if max_scanned is not None and len(scanned) > max_scanned:
            raise ConversionError(
                f"{len(scanned)} pages of that PDF are pictures without text, more than the {max_scanned} "
                "this conversion was priced for. Send the PDF again.")
        if target_ext == "ocrpdf" and not scanned:
            raise ConversionError("Every page of that PDF has its text already, so there is nothing to recognise.")

        wanted = set(scanned)
        texts = []
        found = False
        done = 0
        for index in (range(pages) if target_ext == "txt" else scanned):
            page = doc.load_page(index)
            if index in wanted:
                report(done, len(scanned))
                text = _recognise(pymupdf, page, language, tessdata, layer=target_ext == "ocrpdf")
                done += 1
            else:
                text = page.get_text()
            found = found or bool(text.strip())
            del page
            if target_ext == "txt":
                texts.append(text)
        report(len(scanned), len(scanned))

        if not found:
            raise ConversionError("No text was found in that PDF.")
        if target_ext == "txt":
            out_path.write_text("\n".join(texts), encoding="utf-8")
        else:
            doc.save(str(out_path), garbage=3, deflate=True)
    finally:
        doc.close()


_EXIF_ORIENTATION = 0x0112


def _too_large(pixels: float = 0) -> ConversionError:
    size = f"{pixels / 1_000_000:.0f} megapixels" if pixels else "too big"
    return ConversionError(
        f"That picture is {size}. Text is read from pictures up to "
        f"{OCR_MAX_DECODE_PIXELS // 1_000_000} megapixels here, or from a JPEG of any size the bot takes. "
        "Send a smaller copy.")


_GREY_16_TO_8 = [value >> 8 for value in range(65536)]
_GREY_BAND_PIXELS = 1_000_000


def _eight_bit(im):
    from PIL import Image
    mode = im.mode
    if mode in ("L", "RGB", "RGBA"):
        return im
    if mode == "1":
        return im.convert("L")
    if mode == "I":
        return im.point(_GREY_16_TO_8, "L")
    if mode.startswith("I;16"):
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
    try:
        im.seek(1)
    except Exception:
        return 1
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
            raise ConversionError(
                f"That TIFF holds {pages} pages. Text is read from a one-page picture: "
                "convert the TIFF to PDF first, then choose TXT or Searchable PDF.")
        width, height = im.size
        if width * height > OCR_MAX_PIXELS:
            scale = math.sqrt(OCR_MAX_PIXELS / (width * height))
            im.draft("RGB", (max(1, math.ceil(width * scale)), max(1, math.ceil(height * scale))))
        if im.size[0] * im.size[1] > OCR_MAX_DECODE_PIXELS:
            raise _too_large(width * height)
        try:
            im.load()
        except MemoryError:
            raise OutOfMemory("decoding that picture ran out of memory") from None
        except Exception as exc:
            raise ConversionError(f"Couldn't read that as an image: {_tidy(exc)}") from exc
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


def convert(in_path, src_ext: str, target_ext: str, work_dir, stem: str = "converted",
            lang: str = "eng", max_scanned: "int | None" = None) -> Result:
    src_ext = formats.normalise(src_ext)
    target_ext = formats.normalise(target_ext)
    if target_ext not in WRITES:
        raise ConversionError(f"Text isn't recognised into .{target_ext}.")
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


def selftest() -> None:
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
    font = pymupdf.Font("helv")
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
