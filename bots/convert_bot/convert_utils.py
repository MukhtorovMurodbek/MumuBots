from __future__ import annotations

import json
import logging
import math
import os
import shutil
import stat
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import NamedTuple

from PIL import Image

import big_files
import formats

logger = logging.getLogger(__name__)


class Result(NamedTuple):
    path: Path
    items: int = 1
    zipped: bool = False


class ConversionError(Exception):
    pass


class OutOfMemory(ConversionError):
    pass


def _killed(returncode) -> bool:
    return returncode in (-9, 137)


def remove_tree(path) -> None:
    path = str(path)
    tried: set = set()

    def make_writable(target: str) -> None:
        try:
            mode = os.lstat(target).st_mode
            if stat.S_ISLNK(mode):
                return
            os.chmod(target, (mode | (stat.S_IRWXU if stat.S_ISDIR(mode) else stat.S_IRUSR | stat.S_IWUSR)) & 0o777)
        except OSError:
            pass

    def retry(function, target, _error) -> None:
        target = str(target)
        if target in tried:
            return
        tried.add(target)
        if target != path:
            make_writable(os.path.dirname(target))
        make_writable(target)
        try:
            if function in (os.scandir, os.listdir, os.open):
                shutil.rmtree(target, **{_ON_ERROR: retry})
            elif function is os.path.islink:
                return
            else:
                function(target)
        except OSError:
            pass

    try:
        if os.path.islink(path):
            os.unlink(path)
            return
        if not os.path.lexists(path):
            return
        waiting = [path]
        while waiting:
            folder = waiting.pop()
            make_writable(folder)
            try:
                with os.scandir(folder) as entries:
                    for entry in entries:
                        if entry.is_dir(follow_symlinks=False):
                            waiting.append(entry.path)
                        elif os.name == "nt" and entry.is_file(follow_symlinks=False):
                            make_writable(entry.path)
            except OSError:
                continue
        shutil.rmtree(path, **{_ON_ERROR: retry})
    except (OSError, RecursionError):
        logger.debug("Could not remove %s", path, exc_info=True)


_ON_ERROR = "onexc" if sys.version_info >= (3, 12) else "onerror"


PROGRESS = None


def report(done: float, total: float) -> None:
    if PROGRESS is None or not total:
        return
    try:
        PROGRESS(min(done, total), total)
    except Exception:
        pass


_LOCAL_BOT_API = bool(os.environ.get("LOCAL_BOT_API_URL"))
big_files.configure("CBOT", "convertbot")
BIG_FILES = _LOCAL_BOT_API or big_files.CONFIGURED
BOT_API_MAX_MB = big_files.BOT_API_DOWNLOAD_BYTES // (1024 * 1024)
MAX_FILE_MB = int(os.environ.get("CONVERT_MAX_FILE_MB")
                  or (big_files.MTPROTO_MAX_BYTES // (1024 * 1024) if BIG_FILES else BOT_API_MAX_MB))
if not BIG_FILES:
    MAX_FILE_MB = min(MAX_FILE_MB, BOT_API_MAX_MB)


def _big_files_refused() -> None:
    global BIG_FILES, MAX_FILE_MB
    if _LOCAL_BOT_API:
        return
    BIG_FILES = False
    MAX_FILE_MB = min(MAX_FILE_MB, BOT_API_MAX_MB)


big_files.on_disabled(_big_files_refused)


def max_file_mb_in(chat_id: int) -> int:
    if _LOCAL_BOT_API or big_files.usable(chat_id):
        return MAX_FILE_MB
    return min(MAX_FILE_MB, BOT_API_MAX_MB)


Image.MAX_IMAGE_PIXELS = 80_000_000

MAX_FRAMES = int(os.environ.get("CONVERT_MAX_FRAMES") or 1200)

PAGE_DPI = int(os.environ.get("CONVERT_PAGE_DPI") or 150)


_MB = 1024 * 1024
FREE_UNDER_MB = 3


def price_for_size(size_bytes: int) -> int | None:
    if size_bytes > MAX_FILE_MB * _MB:
        return None
    return _price(size_bytes, size_bytes, 1.0)


BILLABLE = (os.environ.get("CONVERT_BILLABLE") or "sum").strip().lower()

_RATIO_BY_PAIR = {
    ("docx", "pdf"): 20.0, ("docx", "html"): 1.2, ("docx", "md"): 0.3, ("docx", "txt"): 0.15,
    ("md", "pdf"): 4.0, ("md", "html"): 1.3, ("md", "txt"): 0.9,
    ("txt", "pdf"): 3.0, ("txt", "html"): 1.2,
    ("html", "pdf"): 2.0, ("html", "txt"): 0.3,
    ("pdf", "txt"): 0.05, ("pdf", "html"): 0.3, ("pdf", "svg"): 1.5,
    ("epub", "pdf"): 2.5, ("epub", "txt"): 0.25, ("epub", "html"): 0.7,
    ("mobi", "pdf"): 2.5, ("mobi", "txt"): 0.25, ("mobi", "html"): 0.7,
    ("fb2", "pdf"): 2.5, ("fb2", "txt"): 0.3, ("fb2", "html"): 0.8,
    ("xps", "pdf"): 1.1, ("cbz", "pdf"): 1.05,
    ("pdf", "docx"): 0.6,
    ("wav", "mp3"): 0.10, ("wav", "m4a"): 0.09, ("wav", "ogg"): 0.10,
    ("wav", "opus"): 0.07, ("wav", "aac"): 0.09, ("wav", "flac"): 0.6,
    ("flac", "mp3"): 0.25, ("flac", "wav"): 1.7, ("flac", "m4a"): 0.22,
    ("flac", "opus"): 0.18, ("flac", "ogg"): 0.25, ("flac", "aac"): 0.22,
    ("aiff", "mp3"): 0.10, ("aiff", "flac"): 0.6,
    ("mp3", "wav"): 9.0, ("mp3", "flac"): 5.0, ("mp3", "aiff"): 9.0,
    ("m4a", "wav"): 9.0, ("ogg", "wav"): 9.0, ("opus", "wav"): 12.0,
    ("png", "jpg"): 0.15, ("png", "webp"): 0.25, ("png", "avif"): 0.15,
    ("jpg", "png"): 3.5, ("jpg", "bmp"): 12.0, ("jpg", "tiff"): 10.0,
    ("svg", "png"): 20.0, ("svg", "jpg"): 8.0, ("svg", "pdf"): 1.5,
    ("heic", "jpg"): 2.0, ("heic", "png"): 6.0,
    ("7z", "zip"): 1.6, ("rar", "zip"): 1.15, ("txz", "zip"): 1.25, ("tbz2", "zip"): 1.1,
    ("tgz", "zip"): 1.02, ("tar", "zip"): 0.5,
    ("zip", "7z"): 0.8, ("zip", "txz"): 0.85, ("zip", "tgz"): 1.0, ("zip", "tbz2"): 0.95, ("zip", "tar"): 1.5,
    ("pptx", "pdf"): 0.8, ("pptx", "ppt"): 1.5, ("pptx", "odp"): 0.9, ("pptx", "txt"): 0.02,
    ("ppt", "pptx"): 0.6, ("ppt", "pdf"): 0.6, ("ppt", "odp"): 0.6, ("ppt", "txt"): 0.01,
    ("odp", "pptx"): 1.1, ("odp", "ppt"): 1.6, ("odp", "pdf"): 0.9, ("odp", "txt"): 0.02,
    ("pdf", "pptx"): 0.8, ("pdf", "odp"): 0.8,
    ("doc", "docx"): 0.4, ("doc", "pdf"): 1.5, ("doc", "odt"): 0.4, ("doc", "rtf"): 2.0, ("doc", "txt"): 0.1,
    ("odt", "docx"): 1.1, ("odt", "pdf"): 3.0, ("odt", "doc"): 2.5, ("odt", "rtf"): 4.0, ("odt", "txt"): 0.2,
    ("rtf", "docx"): 0.2, ("rtf", "pdf"): 0.5, ("rtf", "doc"): 0.6, ("rtf", "odt"): 0.2, ("rtf", "txt"): 0.05,
    ("docx", "odt"): 1.1, ("docx", "doc"): 2.5, ("docx", "rtf"): 4.0,
    ("xlsx", "ods"): 1.1, ("xlsx", "xls"): 2.5, ("xls", "xlsx"): 0.4, ("xls", "ods"): 0.4,
    ("ods", "xlsx"): 1.1, ("ods", "xls"): 2.5, ("xls", "pdf"): 1.0, ("ods", "pdf"): 2.0,
}

_RAW_RATIO = {"jpg": 0.25, "png": 3.0, "webp": 0.2, "avif": 0.15, "heic": 0.2, "tiff": 4.0}

_RATIO_BY_CATEGORY = {
    (formats.VIDEO, "gif"): 6.0, (formats.VIDEO, "apng"): 9.0, (formats.VIDEO, "webp"): 2.0,
    (formats.VIDEO, "mp4"): 0.9, (formats.VIDEO, "webm"): 0.7, (formats.VIDEO, "mkv"): 1.02,
    (formats.VIDEO, "mov"): 1.05, (formats.VIDEO, "avi"): 1.3,
    (formats.VIDEO, "mp3"): 0.10, (formats.VIDEO, "m4a"): 0.09, (formats.VIDEO, "aac"): 0.09,
    (formats.VIDEO, "ogg"): 0.10, (formats.VIDEO, "opus"): 0.07,
    (formats.VIDEO, "wav"): 0.9, (formats.VIDEO, "flac"): 0.5,
    (formats.VIDEO, "png"): 0.05, (formats.VIDEO, "jpg"): 0.02,
    (formats.IMAGE, "pdf"): 1.05, (formats.IMAGE, "ico"): 0.05, (formats.IMAGE, "icns"): 0.1,
    (formats.PRESENTATION, "png"): 2.0, (formats.PRESENTATION, "jpg"): 0.8,
    (formats.DATA, "xlsx"): 0.6, (formats.DATA, "pdf"): 2.0, (formats.DATA, "html"): 1.4,
    (formats.DATA, "md"): 1.1, (formats.DATA, "json"): 1.3, (formats.DATA, "yaml"): 1.1,
    (formats.SUBTITLE, "txt"): 0.7,
    (formats.ARCHIVE, "zip"): 1.1, (formats.ARCHIVE, "tgz"): 1.1, (formats.ARCHIVE, "tbz2"): 1.05,
    (formats.ARCHIVE, "txz"): 0.95, (formats.ARCHIVE, "7z"): 0.9, (formats.ARCHIVE, "tar"): 1.5,
}

_RATIO_BY_TARGET = {
    "png": 3.0, "bmp": 10.0, "ppm": 10.0, "tga": 8.0, "tiff": 5.0, "sgi": 8.0,
    "jpg": 0.4, "webp": 0.4, "avif": 0.3, "heic": 0.4, "jp2": 0.8, "qoi": 2.5,
    "gif": 1.5, "ico": 0.05, "icns": 0.1,
    "txt": 0.4, "md": 0.5, "html": 1.2, "pdf": 2.0, "svg": 1.2,
    "wav": 8.0, "flac": 4.0, "aiff": 8.0, "mp3": 0.8, "m4a": 0.8, "aac": 0.8,
    "ogg": 0.8, "opus": 0.6, "wma": 0.9, "ac3": 1.0,
    "docx": 0.8,
}

_BYTES_PER_PAGE = {"png": 350_000, "jpg": 120_000, "webp": 90_000,
                   "tiff": 1_200_000, "svg": 60_000, "pdf": 90_000}

_WORK_BY_TARGET = {
    "mp4": 2.0, "webm": 2.4, "mkv": 2.0, "mov": 2.0, "avi": 1.6,
    "apng": 1.8, "gif": 1.5, "avif": 1.8, "heic": 1.3,
}
_WORK_BY_CATEGORY = {
    (formats.DOCUMENT, "pdf"): 1.4, (formats.DOCUMENT, "png"): 1.4,
    (formats.DOCUMENT, "jpg"): 1.4, (formats.DOCUMENT, "webp"): 1.4,
    (formats.DOCUMENT, "tiff"): 1.5, (formats.DOCUMENT, "svg"): 1.4,
    (formats.DOCUMENT, "docx"): 1.4,
    (formats.VIDEO, "png"): 1.0, (formats.VIDEO, "jpg"): 1.0,
    (formats.VIDEO, "mp3"): 1.1, (formats.VIDEO, "m4a"): 1.1,
    (formats.ARCHIVE, "zip"): 1.4, (formats.ARCHIVE, "tgz"): 1.5, (formats.ARCHIVE, "tbz2"): 2.2,
    (formats.ARCHIVE, "txz"): 2.6, (formats.ARCHIVE, "7z"): 2.6, (formats.ARCHIVE, "tar"): 1.1,
    (formats.DOCUMENT, "odt"): 1.6, (formats.DOCUMENT, "doc"): 1.6, (formats.DOCUMENT, "rtf"): 1.6,
    (formats.DOCUMENT, "pptx"): 2.0, (formats.DOCUMENT, "odp"): 2.0,
    **{(formats.PRESENTATION, target): 1.8 for target in ("pdf", "pptx", "ppt", "odp", "txt")},
    (formats.PRESENTATION, "png"): 2.0, (formats.PRESENTATION, "jpg"): 2.0,
    (formats.DATA, "ods"): 1.4, (formats.DATA, "xls"): 1.4,
}


RAW_WORK_FACTOR = 2.0


def work_factor(src_ext: str, target_ext: str) -> float:
    src_ext, target_ext = formats.normalise(src_ext), formats.normalise(target_ext)
    category = formats.CATEGORY_OF.get(src_ext)
    if src_ext in formats.RAW_FORMATS:
        return RAW_WORK_FACTOR * _WORK_BY_TARGET.get(target_ext, 1.0)
    if (category, target_ext) in _WORK_BY_CATEGORY:
        return _WORK_BY_CATEGORY[(category, target_ext)]
    return _WORK_BY_TARGET.get(target_ext, 1.0)


def estimate_output_bytes(src_ext: str, target_ext: str, in_bytes: int,
                          pages: "int | None" = None) -> int:
    src_ext, target_ext = formats.normalise(src_ext), formats.normalise(target_ext)
    if pages and target_ext in _BYTES_PER_PAGE and \
            formats.CATEGORY_OF.get(src_ext) == formats.DOCUMENT:
        return max(1, int(pages * _BYTES_PER_PAGE[target_ext]))
    category = formats.CATEGORY_OF.get(src_ext)
    ratio = _RAW_RATIO.get(target_ext) if src_ext in formats.RAW_FORMATS else None
    if ratio is None:
        ratio = _RATIO_BY_PAIR.get((src_ext, target_ext))
    if ratio is None:
        ratio = _RATIO_BY_CATEGORY.get((category, target_ext))
    if ratio is None:
        ratio = _RATIO_BY_TARGET.get(target_ext, 1.0)
    return max(1, int(in_bytes * ratio))


def billable_bytes(in_bytes: int, out_bytes: int) -> int:
    return max(in_bytes, out_bytes) if BILLABLE == "max" else in_bytes + out_bytes


def ocr_job_pages(src_ext: str, target_ext: str, ocr_pages: int = 0, files: int = 1) -> int:
    import ocr_convert
    src_ext, target_ext = formats.normalise(src_ext), formats.normalise(target_ext)
    if target_ext not in ocr_convert.WRITES or files > 1:
        return 0
    if src_ext == "pdf":
        counted = max(0, int(ocr_pages or 0))
        return counted if counted <= ocr_convert.OCR_MAX_PAGES else 0
    if src_ext in formats.RAW_FORMATS or src_ext == "svg":
        return 0
    return 1 if formats.category_of(src_ext, False) == formats.IMAGE else 0


_OCR_TEXT_BYTES_PER_PAGE = 4096
_OCR_LAYER_BYTES_PER_PAGE = 10 * 1024


def ocr_limit(src_ext: str, target_ext: str, in_bytes: int, out_bytes: int, read_pages: int,
              paid: bool, pages: "int | None" = None) -> int:
    import jobs
    src_ext, target_ext = formats.normalise(src_ext), formats.normalise(target_ext)
    if src_ext == "pdf" and target_ext == "txt":
        out_bytes = max(out_bytes, estimate_output_bytes(src_ext, target_ext, in_bytes, pages))
    return jobs.ocr_time_limit(read_pages, paid, billable_bytes(in_bytes, out_bytes),
                               work_factor(src_ext, target_ext))


def _ocr_price(src_ext: str, target_ext: str, in_bytes: int, pages: "int | None",
               read_pages: int) -> tuple:
    import jobs
    import ocr_convert
    import pricing
    if target_ext == "txt":
        out_bytes = _OCR_TEXT_BYTES_PER_PAGE * max(pages or 0, read_pages)
    elif src_ext == "pdf":
        out_bytes = in_bytes + _OCR_LAYER_BYTES_PER_PAGE * read_pages
    else:
        out_bytes = int(in_bytes * 1.2) + _OCR_LAYER_BYTES_PER_PAGE
    if billable_bytes(in_bytes, out_bytes) < FREE_UNDER_MB * _MB and read_pages <= ocr_convert.OCR_FREE_PAGES:
        return 0, out_bytes
    return pricing.price(pricing.cost(ocr_limit(src_ext, target_ext, in_bytes, out_bytes, read_pages, True, pages),
                                      jobs.big_send_limit(out_bytes or in_bytes))), out_bytes


def price_for_job(src_ext: str, target_ext: str, in_bytes: int,
                  pages: "int | None" = None, ocr_pages: int = 0, files: int = 1) -> tuple:
    src_ext, target_ext = formats.normalise(src_ext), formats.normalise(target_ext)
    reading = ocr_job_pages(src_ext, target_ext, ocr_pages, files)
    if reading:
        return _ocr_price(src_ext, target_ext, in_bytes, pages, reading)
    out_bytes = estimate_output_bytes(src_ext, target_ext, in_bytes, pages)
    return _price(in_bytes, out_bytes, work_factor(src_ext, target_ext)), out_bytes


def _price(in_bytes: int, out_bytes: int, work: float) -> int:
    import jobs
    import pricing
    billable = billable_bytes(in_bytes, out_bytes)
    if billable < FREE_UNDER_MB * _MB:
        return 0
    return pricing.price(pricing.cost(jobs.time_limit_for(billable, work, paid=True),
                                      jobs.big_send_limit(out_bytes or in_bytes)))


def document_pages(path, src_ext: str) -> "int | None":
    if formats.CATEGORY_OF.get(formats.normalise(src_ext)) != formats.DOCUMENT:
        return None
    try:
        import pymupdf
    except ImportError:
        return None
    try:
        with pymupdf.open(path) as doc:
            return int(doc.page_count)
    except Exception:
        return None


def least_paid_price() -> int:
    import jobs
    import pricing
    return pricing.price(pricing.cost(jobs.TIME_BASE_S, jobs.SEND_LIMIT_BYTES))


def price_tiers_text(lang: str = "en") -> str:
    import i18n
    import jobs
    import pricing

    example_mb = min(100, MAX_FILE_MB)
    example, _ = price_for_job("mp4", "mp4", example_mb * _MB)
    most = pricing.price(pricing.cost(jobs.TIME_LIMIT_S, jobs.big_send_limit(MAX_FILE_MB * _MB)))
    lines = [
        "  " + i18n.t(lang, "price_free_upto", mb=FREE_UNDER_MB) + i18n.t(lang, "price_free"),
        "  " + i18n.t(lang, "price_from", price=least_paid_price()),
        "  " + i18n.t(lang, "price_example", mb=example_mb, price=example),
        "  " + i18n.t(lang, "price_at_most", mb=MAX_FILE_MB, price=most),
    ]
    note = "" if BIG_FILES else " " + i18n.t(lang, "price_cloud_api_note")
    lines.append("  " + i18n.t(lang, "price_over", mb=MAX_FILE_MB) + i18n.t(lang, "price_not_supported") + note)
    lines.append("  " + i18n.t(lang, "price_quoted_first"))
    return "\n".join(lines)


_OPENERS_REGISTERED = False


def register_openers() -> None:
    global _OPENERS_REGISTERED
    if _OPENERS_REGISTERED:
        return
    try:
        import pillow_heif
        pillow_heif.register_heif_opener()
    except Exception:
        pass
    _OPENERS_REGISTERED = True


_EXIF_ORIENTATION = 0x0112
_ORIENTED_BY_CONTAINER = {"HEIF", "AVIF"}


def upright(img: Image.Image) -> Image.Image:
    if img.format in _ORIENTED_BY_CONTAINER:
        return img
    try:
        orientation = img.getexif().get(_EXIF_ORIENTATION, 1)
    except Exception:
        return img
    if orientation not in (2, 3, 4, 5, 6, 7, 8):
        return img
    from PIL import ImageOps
    turned = ImageOps.exif_transpose(img)
    if turned is not img:
        img.close()
    return turned


def _open_image(path) -> Image.Image:
    register_openers()
    try:
        img = Image.open(path)
        img.load()
        return upright(img)
    except Exception as exc:
        raise ConversionError(f"Couldn't read that as an image: {exc}") from exc


def gif_is_animated(path) -> bool:
    try:
        register_openers()
        with Image.open(path) as img:
            return getattr(img, "n_frames", 1) > 1
    except Exception:
        return False


_IMAGE_TARGETS = {
    "png":  ("RGBA", "PNG", {}),
    "jpg":  ("RGB", "JPEG", {"quality": 92}),
    "webp": ("RGBA", "WEBP", {"quality": 92}),
    "bmp":  ("RGB", "BMP", {}),
    "tiff": ("RGB", "TIFF", {"compression": "tiff_lzw"}),
    "avif": ("RGBA", "AVIF", {"quality": 80}),
    "heic": ("RGB", "HEIF", {"quality": 80}),
    "jp2":  ("RGB", "JPEG2000", {"quality_mode": "rates", "quality_layers": [20]}),
    "tga":  ("RGB", "TGA", {}),
    "ppm":  ("RGB", "PPM", {}),
    "qoi":  ("RGB", "QOI", {}),
    "gif":  ("P", "GIF", {}),
    "ico":  ("RGBA", "ICO", {"sizes": [(256, 256), (128, 128), (64, 64), (32, 32), (16, 16)]}),
    "icns": ("RGBA", "ICNS", {}),
}


def _flatten_onto_white(img: Image.Image) -> Image.Image:
    background = Image.new("RGB", img.size, (255, 255, 255))
    rgba = img.convert("RGBA")
    background.paste(rgba, mask=rgba.split()[-1])
    return background


def convert_image(in_path, target_ext: str, out_path) -> None:
    target_ext = formats.normalise(target_ext)
    if target_ext not in _IMAGE_TARGETS:
        raise ConversionError(f"Unsupported image target format: {target_ext}")
    save_image(_open_image(in_path), target_ext, out_path)


def save_image(img: Image.Image, target_ext: str, out_path) -> None:
    target_ext = formats.normalise(target_ext)
    spec = _IMAGE_TARGETS.get(target_ext)
    if spec is None:
        img.close()
        raise ConversionError(f"Unsupported image target format: {target_ext}")
    mode, pillow_format, kwargs = spec
    register_openers()
    try:
        if mode == "RGB" and img.mode in ("RGBA", "LA", "P"):
            img = _flatten_onto_white(img)
        elif mode == "P":
            img = img.convert("RGB").convert("P", palette=Image.ADAPTIVE)
        else:
            img = img.convert(mode)
        if target_ext == "icns":
            img = img.convert("RGBA").resize((1024, 1024))
        img.save(out_path, format=pillow_format, **kwargs)
    except ConversionError:
        raise
    except Exception as exc:
        raise ConversionError(f"Image conversion failed: {exc}") from exc
    finally:
        img.close()


def images_to_pdf(in_paths, out_path) -> int:
    pages: list[Image.Image] = []
    try:
        for path in in_paths:
            img = _open_image(path)
            try:
                if img.mode in ("RGBA", "LA", "P"):
                    pages.append(_flatten_onto_white(img))
                else:
                    pages.append(img.convert("RGB"))
            finally:
                img.close()
        if not pages:
            raise ConversionError("No images to put in the PDF.")
        pages[0].save(
            out_path, format="PDF", save_all=True, append_images=pages[1:],
            resolution=PAGE_DPI,
        )
        return len(pages)
    except ConversionError:
        raise
    except Exception as exc:
        raise ConversionError(f"Couldn't build the PDF: {exc}") from exc
    finally:
        for page in pages:
            try:
                page.close()
            except Exception:
                pass


FFMPEG_TIMEOUT_S = int(os.environ.get("FFMPEG_TIMEOUT_SECONDS", "600"))
FFMPEG_THREADS = os.environ.get("FFMPEG_THREADS", "2")
FFMPEG_CAP_IN = ["-threads", FFMPEG_THREADS, "-filter_threads", FFMPEG_THREADS,
                 "-filter_complex_threads", FFMPEG_THREADS]
FFMPEG_CAP_OUT = ["-threads", FFMPEG_THREADS]

_EVEN = "scale=trunc(iw/2)*2:trunc(ih/2)*2"
_H264 = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
         "-pix_fmt", "yuv420p", "-vf", _EVEN, "-c:a", "aac"]

_FFMPEG_ARGS = {
    "mp4":  _H264 + ["-movflags", "+faststart"],
    "mov":  _H264,
    "mkv":  _H264,
    "avi":  ["-c:v", "mpeg4", "-qscale:v", "5", "-vf", _EVEN, "-c:a", "libmp3lame", "-q:a", "4"],
    "webm": ["-c:v", "libvpx-vp9", "-crf", "32", "-b:v", "0", "-row-mt", "1",
             "-pix_fmt", "yuv420p", "-vf", _EVEN, "-c:a", "libopus"],
    "gif":  ["-vf", "fps=15,scale=480:-1:flags=lanczos,split[a][b];[a]palettegen[p];[b][p]paletteuse", "-loop", "0"],
    "webp": ["-c:v", "libwebp", "-vf", "fps=15,scale=480:-1:flags=lanczos", "-loop", "0", "-q:v", "70", "-an"],
    "apng": ["-c:v", "apng", "-vf", "fps=15,scale=480:-1:flags=lanczos", "-plays", "0", "-an"],
    "mp3":  ["-vn", "-c:a", "libmp3lame", "-q:a", "2"],
    "wav":  ["-vn", "-c:a", "pcm_s16le"],
    "aiff": ["-vn", "-c:a", "pcm_s16be"],
    "ogg":  ["-vn", "-c:a", "libvorbis", "-q:a", "5"],
    "opus": ["-vn", "-c:a", "libopus", "-b:a", "128k"],
    "m4a":  ["-vn", "-c:a", "aac", "-b:a", "192k"],
    "aac":  ["-vn", "-c:a", "aac", "-b:a", "192k"],
    "flac": ["-vn", "-c:a", "flac"],
    "wma":  ["-vn", "-c:a", "wmav2", "-b:a", "192k"],
    "ac3":  ["-vn", "-c:a", "ac3", "-b:a", "192k"],
    "srt":  ["-c:s", "srt"],
    "vtt":  ["-c:s", "webvtt"],
    "ass":  ["-c:s", "ass"],
    "png":  ["-frames:v", "1", "-an"],
    "jpg":  ["-frames:v", "1", "-q:v", "2", "-an"],
}


def _ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


_HDR_TRANSFERS = {"smpte2084", "arib-std-b67"}
_TONEMAP = ("zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,"
            "tonemap=tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv,format=yuv420p")
_SDR_VIDEO = {"mp4", "mov", "mkv", "avi", "webm"}
_SDR_TARGETS = _SDR_VIDEO | {"gif", "webp", "apng", "png", "jpg"}
_ANIMATED_SCALE = "fps=15,scale=480:-1:flags=lanczos"
_filters_seen: "dict[str, bool]" = {}


def _ffmpeg_has_filters(*names: str) -> bool:
    if not _filters_seen:
        try:
            listing = subprocess.run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True,
                                     text=True, timeout=20).stdout
        except Exception:
            listing = ""
        for line in listing.splitlines():
            parts = line.split()
            if len(parts) >= 2:
                _filters_seen[parts[1]] = True
        _filters_seen.setdefault("", False)
    return all(_filters_seen.get(name) for name in names)


def _is_hdr(in_path) -> bool:
    if shutil.which("ffprobe") is None:
        return False
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=color_transfer",
             "-of", "default=noprint_wrappers=1:nokey=1", str(in_path)],
            capture_output=True, text=True, timeout=20)
    except Exception:
        return False
    return (probe.stdout or "").strip().lower() in _HDR_TRANSFERS


def _tonemapped(args: list, target_ext: str) -> list:
    args = list(args)
    if "-vf" in args:
        at = args.index("-vf") + 1
        chain = args[at]
        if chain.startswith(_ANIMATED_SCALE):
            args[at] = _ANIMATED_SCALE + "," + _TONEMAP + chain[len(_ANIMATED_SCALE):]
        else:
            args[at] = _TONEMAP + "," + chain
    else:
        args = ["-vf", _TONEMAP] + args
    if target_ext in _SDR_VIDEO:
        args += ["-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709"]
    return args


FIT_PIXELS = int(os.environ.get("CONVERT_VIDEO_FIT_PIXELS") or 2_900_000)
HUGE_PIXELS = 3840 * 2160
FIT_BOX = 1920
_FIT_FILTER = (f"scale={FIT_BOX}:{FIT_BOX}:force_original_aspect_ratio=decrease:"
               "force_divisible_by=2:flags=bicubic")


def _quarter_turn(stream: dict) -> bool:
    turn = None
    for data in stream.get("side_data_list") or ():
        if isinstance(data, dict) and "rotation" in data:
            turn = data["rotation"]
            break
    if turn is None:
        turn = (stream.get("tags") or {}).get("rotate")
    try:
        turn = float(turn)
    except (TypeError, ValueError):
        return False
    if not math.isfinite(turn):
        return False
    return round(turn / 90) % 2 == 1


def video_geometry(in_path) -> "tuple[int, int, bool] | None":
    if shutil.which("ffprobe") is None:
        return None
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_streams", "-of", "json", str(in_path)],
            capture_output=True, text=True, timeout=20)
        stream = json.loads(probe.stdout or "{}")["streams"][0]
        width, height = int(stream["width"]), int(stream["height"])
    except Exception:
        return None
    if width <= 0 or height <= 0:
        return None
    if _quarter_turn(stream):
        width, height = height, width
    return width, height, str(stream.get("color_transfer") or "").lower() in _HDR_TRANSFERS


def video_needs_fit(width: int, height: int, hdr: bool, target_ext: str) -> bool:
    if not FIT_PIXELS or width * height <= FIT_PIXELS:
        return False
    target_ext = formats.normalise(target_ext)
    if target_ext not in _SDR_VIDEO:
        return False
    return hdr or target_ext == "webm" or width * height > HUGE_PIXELS


def fitted_size(width: int, height: int) -> "tuple[int, int]":
    scale = min(1.0, FIT_BOX / width, FIT_BOX / height)
    return max(2, int(width * scale) // 2 * 2), max(2, int(height * scale) // 2 * 2)


def _fitted(args: list) -> list:
    args = list(args)
    if "-vf" in args:
        at = args.index("-vf") + 1
        args[at] = _FIT_FILTER + "," + args[at]
        return args
    return ["-vf", _FIT_FILTER] + args


def video_fit_note(in_path) -> "tuple[str, int, int, int, int] | None":
    geometry = video_geometry(in_path)
    if geometry is None:
        return None
    width, height, hdr = geometry
    if not FIT_PIXELS or width * height <= FIT_PIXELS:
        return None
    fw, fh = fitted_size(width, height)
    which = "hdr" if hdr else ("huge" if width * height > HUGE_PIXELS else "big")
    return (which, width, height, fw, fh)


def convert_media_ffmpeg(in_path, target_ext: str, out_path) -> None:
    target_ext = formats.normalise(target_ext)
    args = _FFMPEG_ARGS.get(target_ext)
    if args is None:
        raise ConversionError(f"Unsupported target format: {target_ext}")
    if not _ffmpeg_available():
        raise ConversionError(
            "ffmpeg isn't installed on this host, so video, audio and subtitle "
            "conversion isn't available."
        )
    if not os.path.getsize(in_path):
        raise ConversionError("That file came through empty -- try sending it again.")
    hdr = target_ext in _SDR_TARGETS and _is_hdr(in_path)
    if hdr and _ffmpeg_has_filters("zscale", "tonemap"):
        args = _tonemapped(args, target_ext)
    if target_ext in _SDR_VIDEO and FIT_PIXELS:
        geometry = video_geometry(in_path)
        if geometry and video_needs_fit(geometry[0], geometry[1], hdr or geometry[2], target_ext):
            args = _fitted(args)

    duration = _media_duration(in_path) if PROGRESS is not None else None
    if duration:
        _ffmpeg_with_progress(in_path, args, out_path, duration)
        return

    cmd = ["ffmpeg", "-nostdin", "-y", *FFMPEG_CAP_IN, "-i", str(in_path)]
    cmd += args + FFMPEG_CAP_OUT
    cmd.append(str(out_path))

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=FFMPEG_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        raise ConversionError(
            f"Conversion took longer than {FFMPEG_TIMEOUT_S}s and was stopped. "
            "Try a shorter or smaller file."
        ) from None
    if _killed(proc.returncode):
        raise OutOfMemory(f"ffmpeg was killed (exit {proc.returncode}), for memory: "
                          f"{(proc.stderr or '').strip()[-300:]}")
    if proc.returncode != 0 or not os.path.exists(out_path):
        note = (proc.stderr or "").strip()[-500:] or "ffmpeg failed with no output"
        raise ConversionError(f"Conversion failed: {note}")


def _media_duration(in_path) -> "float | None":
    if shutil.which("ffprobe") is None:
        return None
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(in_path)],
            capture_output=True, text=True, timeout=20)
        seconds = float((probe.stdout or "").strip().splitlines()[0])
    except Exception:
        return None
    return seconds if seconds > 0 else None


def _ffmpeg_with_progress(in_path, args, out_path, duration: float) -> None:
    import tempfile
    import time
    cmd = ["ffmpeg", "-nostdin", "-y", *FFMPEG_CAP_IN, "-progress", "pipe:1", "-nostats",
           "-i", str(in_path)]
    cmd += args + FFMPEG_CAP_OUT
    cmd.append(str(out_path))
    deadline = time.monotonic() + FFMPEG_TIMEOUT_S
    with tempfile.TemporaryFile() as errors:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=errors, text=True)
        try:
            for line in proc.stdout:
                key, _, value = line.strip().partition("=")
                if key in ("out_time_us", "out_time_ms") and value.isdigit():
                    report(int(value) / 1_000_000, duration)
                if time.monotonic() > deadline:
                    proc.kill()
                    proc.wait()
                    raise ConversionError(
                        f"Conversion took longer than {FFMPEG_TIMEOUT_S}s and was stopped. "
                        "Try a shorter or smaller file.")
            proc.wait(timeout=max(1, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            raise ConversionError(
                f"Conversion took longer than {FFMPEG_TIMEOUT_S}s and was stopped. "
                "Try a shorter or smaller file.") from None
        if proc.returncode != 0 or not os.path.exists(out_path):
            errors.seek(0)
            note = errors.read().decode("utf-8", "replace").strip()[-500:] or "ffmpeg failed with no output"
            if _killed(proc.returncode):
                raise OutOfMemory(f"ffmpeg was killed (exit {proc.returncode}), for memory: {note[-300:]}")
            raise ConversionError(f"Conversion failed: {note}")


ANIMATION_TARGETS = ("gif", "webp", "apng")
ANIMATION_FPS = 15
ANIMATION_MAX_SECONDS = float(os.environ.get("CONVERT_ANIMATION_MAX_SECONDS") or MAX_FRAMES / ANIMATION_FPS)


def animation_seconds(paths) -> "float | None":
    total = 0.0
    for path in paths:
        seconds = _media_duration(path)
        if seconds is None:
            return None
        total += seconds
    return total


def animation_frames(path) -> int:
    try:
        register_openers()
        with Image.open(path) as img:
            return int(getattr(img, "n_frames", 1) or 1)
    except Exception:
        return 1


def _reject_overlong_animation(in_path, src_ext: str) -> None:
    if src_ext not in ("gif", "apng", "webp"):
        return
    try:
        register_openers()
        with Image.open(in_path) as img:
            frames = getattr(img, "n_frames", 1)
    except Exception:
        return
    if frames > MAX_FRAMES:
        raise ConversionError(
            f"That animation has {frames} frames, over the {MAX_FRAMES}-frame "
            "limit. Try a shorter clip."
        )


def zip_files(paths, out_path) -> None:
    try:
        with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for path in paths:
                archive.write(path, arcname=Path(path).name)
    except Exception as exc:
        raise ConversionError(f"Couldn't package the results: {exc}") from exc


def _out(work_dir, stem: str, ext: str) -> Path:
    return Path(work_dir) / f"{stem}.{ext}"


def convert(in_paths, src_ext: str, target_ext: str, work_dir, stem: str = "converted",
            ocr_lang: str = "", ocr_pages: int = 0) -> Result:
    in_paths = [str(path) for path in in_paths]
    if not in_paths:
        raise ConversionError("Nothing to convert.")
    src_ext = formats.normalise(src_ext)
    target_ext = formats.normalise(target_ext)
    category = formats.category_of(src_ext)
    work_dir = Path(work_dir)

    import ocr_convert
    if len(in_paths) > 1:
        if target_ext in ocr_convert.WRITES and (target_ext == "ocrpdf" or category == formats.IMAGE):
            raise ConversionError("Text is read from one picture or PDF at a time, not from an album.")
        return _convert_batch(in_paths, src_ext, target_ext, work_dir, stem)

    in_path = in_paths[0]
    out_path = _out(work_dir, stem, target_ext)

    if src_ext in formats.ANIMATED_CAPABLE:
        category = formats.category_of(src_ext, gif_is_animated(in_path))

    if target_ext in ocr_convert.WRITES:
        reading = ocr_job_pages(src_ext, target_ext, ocr_pages, len(in_paths))
        picture = category == formats.IMAGE and src_ext not in formats.RAW_FORMATS and src_ext != "svg"
        if picture or (src_ext == "pdf" and (reading or target_ext == "ocrpdf")):
            return ocr_convert.convert(in_path, src_ext, target_ext, work_dir, stem,
                                       lang=ocr_lang, max_scanned=reading)
        if target_ext == "ocrpdf":
            raise ConversionError(f"A .{src_ext} file can't be turned into a searchable PDF.")

    import office_convert
    if office_convert.handles(src_ext, target_ext):
        return office_convert.convert(in_path, src_ext, target_ext, work_dir, stem)

    if category == formats.IMAGE and src_ext in formats.RAW_FORMATS:
        import raw_convert
        raw_convert.convert(in_path, src_ext, target_ext, out_path)
        return Result(out_path)

    if category == formats.IMAGE and src_ext != "svg":
        if target_ext == "pdf":
            images_to_pdf([in_path], out_path)
        elif target_ext in _IMAGE_TARGETS:
            convert_image(in_path, target_ext, out_path)
        else:
            convert_media_ffmpeg(in_path, target_ext, out_path)
        return Result(out_path)

    if category in (formats.VIDEO, formats.AUDIO, formats.SUBTITLE):
        _reject_overlong_animation(in_path, src_ext)
        if (category == formats.VIDEO and target_ext in ANIMATION_TARGETS
                and src_ext not in formats.ANIMATED_CAPABLE):
            seconds = animation_seconds([in_path])
            if seconds is None or seconds > ANIMATION_MAX_SECONDS:
                raise ConversionError(
                    f"That video is too long to become an animation (over "
                    f"{ANIMATION_MAX_SECONDS:.0f} seconds). Trim it, or choose a video format.")
        if category == formats.SUBTITLE and target_ext == "txt":
            import data_convert
            data_convert.subtitle_to_text(in_path, src_ext, out_path)
            return Result(out_path)
        convert_media_ffmpeg(in_path, target_ext, out_path)
        return Result(out_path)

    if category == formats.DOCUMENT or src_ext == "svg":
        import doc_convert
        return doc_convert.convert(in_path, src_ext, target_ext, work_dir, stem)

    if category == formats.DATA:
        import data_convert
        return data_convert.convert(in_path, src_ext, target_ext, work_dir, stem)

    if category == formats.ARCHIVE:
        import archive_convert
        return archive_convert.convert(in_path, src_ext, target_ext, work_dir, stem)

    raise ConversionError(f"Nothing here converts a .{src_ext} file.")


def _convert_batch(in_paths, src_ext, target_ext, work_dir, stem) -> Result:
    if len(in_paths) > formats.MAX_BATCH:
        raise ConversionError(
            f"That is {len(in_paths)} files, over the {formats.MAX_BATCH}-file "
            "limit for one conversion."
        )
    if target_ext == "pdf" and formats.category_of(src_ext) == formats.IMAGE:
        out_path = _out(work_dir, stem, "pdf")
        pages = images_to_pdf(in_paths, out_path)
        return Result(out_path, items=pages)

    produced = []
    for index, path in enumerate(in_paths, start=1):
        report(index - 1, len(in_paths))
        one = convert([path], src_ext, target_ext, work_dir, f"{stem}-{index:02d}")
        produced.append(one.path)
    report(len(in_paths), len(in_paths))
    out_path = _out(work_dir, stem, "zip")
    zip_files(produced, out_path)
    for path in produced:
        Path(path).unlink(missing_ok=True)
    return Result(out_path, items=len(produced), zipped=True)
