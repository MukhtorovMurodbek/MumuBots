from __future__ import annotations

import importlib.util
import logging
import os
import shutil
import subprocess

logger = logging.getLogger(__name__)


IMAGE = "image"
VIDEO = "video"
AUDIO = "audio"
DOCUMENT = "document"
DATA = "data"
SUBTITLE = "subtitle"
ARCHIVE = "archive"
PRESENTATION = "presentation"

CATEGORIES = (IMAGE, VIDEO, AUDIO, DOCUMENT, PRESENTATION, DATA, SUBTITLE, ARCHIVE)


ALIASES = {
    "jpeg": "jpg", "jpe": "jpg", "jfif": "jpg",
    "tif": "tiff",
    "dib": "bmp",
    "j2c": "jp2", "jpf": "jp2", "jpx": "jp2",
    "pgm": "ppm", "pbm": "ppm", "pnm": "ppm",
    "yml": "yaml",
    "markdown": "md", "mdown": "md",
    "htm": "html", "xhtml": "html",
    "ssa": "ass",
    "oga": "ogg",
    "mpeg": "mpg", "m2v": "mpg",
    "mts": "m2ts",
    "aif": "aiff", "aifc": "aiff",
    "text": "txt",
    "tbz": "tbz2", "tb2": "tbz2", "taz": "tgz",
    "pps": "ppt", "ppsx": "pptx", "pptm": "pptx",
}

COMPOUND_EXTENSIONS = (("tar.gz", "tgz"), ("tar.bz2", "tbz2"), ("tar.xz", "txz"))


def compound_extension(filename: "str | None") -> "str | None":
    lowered = (filename or "").lower()
    for ending, name in COMPOUND_EXTENSIONS:
        if lowered.endswith("." + ending):
            return name
    return None


RAW_FORMATS = ("dng", "cr2", "cr3", "nef", "arw", "raf", "orf", "rw2")

SOURCES = {
    IMAGE: (
        "png", "jpg", "webp", "bmp", "tiff", "ico", "cur",
        "heic", "heif", "avif", "jp2", "psd", "tga", "pcx", "dds",
        "ppm", "sgi", "xbm", "xpm", "icns", "qoi", "blp", "im", "svg",
        *RAW_FORMATS,
    ),
    VIDEO: (
        "mp4", "mov", "m4v", "avi", "webm", "mkv", "flv", "wmv",
        "ts", "m2ts", "mpg", "3gp", "ogv", "asf", "gif", "apng",
    ),
    AUDIO: (
        "mp3", "wav", "ogg", "opus", "m4a", "aac", "flac", "aiff",
        "wma", "amr", "ac3", "mp2", "au", "caf", "w64", "voc",
    ),
    DOCUMENT: (
        "pdf", "docx", "doc", "odt", "rtf", "epub", "mobi", "fb2", "cbz", "xps", "md",
        "txt", "html",
    ),
    PRESENTATION: ("pptx", "ppt", "odp"),
    DATA: ("csv", "tsv", "json", "yaml", "xlsx", "xls", "ods", "xml"),
    SUBTITLE: ("srt", "vtt", "ass"),
    ARCHIVE: ("zip", "tar", "tgz", "tbz2", "txz", "7z", "rar"),
}

CATEGORY_OF: dict[str, str] = {}
for _category, _exts in SOURCES.items():
    for _ext in _exts:
        CATEGORY_OF.setdefault(_ext, _category)

ANIMATED_CAPABLE = ("gif", "webp", "apng")
GIF_IS_VIDEO = True

ANIMATED_TARGETS = ("mp4", "webm", "gif", "webp", "apng", "mkv", "mov", "png", "jpg")


TARGETS_BY_CATEGORY = {
    IMAGE: (
        "png", "jpg", "webp", "pdf", "ico", "tiff",
        "bmp", "gif", "avif", "heic", "jp2", "tga", "ppm", "qoi", "icns",
        "txt", "ocrpdf",
    ),
    VIDEO: (
        "mp4", "webm", "gif", "mp3", "mkv", "mov",
        "avi", "webp", "apng", "m4a", "aac", "ogg", "opus", "wav", "flac",
    ),
    AUDIO: (
        "mp3", "m4a", "ogg", "wav", "flac", "opus",
        "aac", "aiff", "wma", "ac3",
    ),
    DATA: (
        "json", "csv", "xlsx", "yaml", "md", "html",
        "tsv", "pdf", "txt",
    ),
    SUBTITLE: ("srt", "vtt", "ass", "txt"),
    ARCHIVE: ("zip", "7z", "tgz", "tar", "txz", "tbz2"),
    PRESENTATION: ("pdf", "pptx", "ppt", "odp", "png", "jpg", "txt"),
}

TARGETS_BY_FORMAT = {
    "pdf": ("docx", "pptx", "png", "jpg", "txt", "ocrpdf", "html", "webp", "svg", "tiff", "odp"),
    "epub": ("pdf", "docx", "txt", "html", "png", "jpg"),
    "mobi": ("pdf", "docx", "txt", "html", "png", "jpg"),
    "fb2": ("pdf", "docx", "txt", "html", "png", "jpg"),
    "xps": ("pdf", "docx", "txt", "html", "png", "jpg"),
    "cbz": ("pdf", "png", "jpg"),
    "docx": ("pdf", "odt", "doc", "html", "md", "txt", "rtf", "png", "jpg"),
    "doc": ("pdf", "docx", "odt", "txt", "rtf", "html", "png", "jpg"),
    "odt": ("pdf", "docx", "doc", "txt", "rtf", "html", "png", "jpg"),
    "rtf": ("pdf", "docx", "doc", "odt", "txt", "html"),
    "xlsx": ("json", "csv", "ods", "xls", "yaml", "md", "html", "tsv", "pdf", "txt"),
    "xls": ("xlsx", "csv", "ods", "pdf", "json", "html", "md", "yaml", "tsv", "txt"),
    "ods": ("xlsx", "csv", "xls", "pdf", "json", "html", "md", "yaml", "tsv", "txt"),
    "md": ("pdf", "docx", "html", "txt"),
    "html": ("pdf", "docx", "txt", "png", "jpg"),
    "txt": ("pdf", "docx", "html"),
    "svg": ("png", "pdf", "jpg", "webp"),
    **{raw: ("jpg", "png", "webp", "avif", "heic", "tiff") for raw in RAW_FORMATS},
    "gif": ("mp4", "webm", "webp", "apng", "png", "jpg"),
    "apng": ("gif", "mp4", "webm", "webp", "png"),
    "flac": ("wav", "aiff", "mp3", "m4a", "ogg", "opus", "aac"),
    "wav": ("mp3", "flac", "m4a", "ogg", "opus", "aiff", "aac"),
    "xml": ("json", "yaml", "md", "html", "txt", "pdf"),
}

POPULAR_COUNT = 6


FORMAT_NEEDS = {
    "heic": ("heif",), "heif": ("heif",),
    "avif": ("avif",),
    "jp2": ("jp2",),
    "pdf": ("mupdf",), "svg": ("mupdf",),
    "epub": ("mupdf",), "mobi": ("mupdf",), "fb2": ("mupdf",),
    "cbz": ("mupdf",), "xps": ("mupdf",),
    "docx": ("mammoth",),
    "md": ("markdown",),
    "html": ("mupdf",),
    "xlsx": ("openpyxl",),
    "yaml": ("yaml",),
    "xml": ("xml",),
    "7z": ("bsdtar",), "rar": ("bsdtar",),
    **{raw: ("rawpy",) for raw in RAW_FORMATS},
    "ocrpdf": ("ocr",),

    **{office: ("office",) for office in ("pptx", "ppt", "odp", "doc", "odt", "rtf", "xls", "ods")},
}

TARGET_NEEDS = {
    (IMAGE, "pdf"): (),
    (DOCUMENT, "docx"): ("pydocx", "mupdf"),
    (IMAGE, "txt"): ("ocr",),

    (DOCUMENT, "pptx"): ("office", "pdfimport"),
    (DOCUMENT, "odp"): ("office", "pdfimport"),
}

ENCODERS = {
    "mp4": "libx264", "mkv": "libx264", "mov": "libx264", "avi": "libx264",
    "webm": "libvpx-vp9",
    "gif": "gif", "apng": "apng", "webp": "libwebp",
    "mp3": "libmp3lame", "ogg": "libvorbis", "opus": "libopus",
    "m4a": "aac", "aac": "aac", "flac": "flac", "wav": "pcm_s16le",
    "aiff": "pcm_s16be", "wma": "wmav2", "ac3": "ac3",
    "srt": "srt", "vtt": "webvtt", "ass": "ass",
}

FFMPEG_CATEGORIES = (VIDEO, AUDIO, SUBTITLE)


_MET: set[str] = set()
_ENCODERS_PRESENT: set[str] = set()
_PROBED = False


def _module(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _pillow_feature(name: str) -> bool:
    try:
        from PIL import features
        return bool(features.check(name))
    except Exception:
        return False


def bsdtar_path() -> "str | None":
    explicit = os.environ.get("CBOT_BSDTAR")
    if explicit and os.path.isfile(explicit):
        return explicit
    found = shutil.which("bsdtar")
    if found:
        return found
    if os.name == "nt":
        candidate = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "tar.exe")
        if os.path.isfile(candidate):
            return candidate
    return None


def _ffmpeg_encoders() -> set[str]:
    if not shutil.which("ffmpeg"):
        return set()
    try:
        proc = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True, text=True, timeout=20,
        )
    except Exception:
        logger.warning("Could not ask ffmpeg what it can encode; ffmpeg targets are off.")
        return set()
    names = set()
    for line in proc.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and len(parts[0]) == 6 and parts[0][0] in "VAS":
            names.add(parts[1])
    return names


def _ocr_available() -> bool:
    try:
        import ocr_convert
        return bool(ocr_convert.available())
    except Exception:
        logger.debug("Text recognition is not available", exc_info=True)
        return False


def probe() -> set[str]:
    global _PROBED, _MET, _ENCODERS_PRESENT
    found = set()
    if _module("pillow_heif"):
        found.add("heif")
    if _pillow_feature("avif") or _module("pillow_heif"):
        found.add("avif")
    if _pillow_feature("jpg_2000"):
        found.add("jp2")
    for requirement, module in (
        ("mupdf", "pymupdf"), ("mammoth", "mammoth"), ("markdown", "markdown"),
        ("openpyxl", "openpyxl"), ("yaml", "yaml"), ("xml", "defusedxml"),
        ("pydocx", "docx"), ("rawpy", "rawpy"),
    ):
        if _module(module):
            found.add(requirement)
    if bsdtar_path():
        found.add("bsdtar")
    if _ocr_available():
        found.add("ocr")
    try:
        import office_convert
        if office_convert.soffice_path():
            found.add("office")
            if office_convert.has_pdf_import():
                found.add("pdfimport")
    except Exception:
        logger.debug("LibreOffice could not be looked for", exc_info=True)
    _ENCODERS_PRESENT = _ffmpeg_encoders()
    if _ENCODERS_PRESENT:
        found.add("ffmpeg")
    _MET = found
    _PROBED = True
    logger.info(
        "Converters available: %s; %d ffmpeg encoders.",
        ", ".join(sorted(found)) or "Pillow only", len(_ENCODERS_PRESENT),
    )
    absent = [name for name in ("mupdf", "heif", "openpyxl", "yaml", "markdown", "mammoth",
                                "pydocx", "bsdtar", "rawpy", "ocr", "office")
              if name not in found]
    if absent:
        logger.warning(
            "Not installed, so those formats are not offered: %s. "
            "pip install -r requirements.txt (LibreOffice: the Dockerfile) puts them back.", ", ".join(absent),
        )
    return found


def _ensure_probed() -> None:
    if not _PROBED:
        probe()


def met(requirement: str) -> bool:
    _ensure_probed()
    return requirement in _MET


def _needs_are_met(needs) -> bool:
    _ensure_probed()
    return all(need in _MET for need in needs)


def _encoder_ok(category: str, target: str) -> bool:
    if category not in FFMPEG_CATEGORIES:
        return True
    encoder = ENCODERS.get(target)
    if encoder is None:
        return True
    return encoder in _ENCODERS_PRESENT


def normalise(ext: str | None) -> str:
    if not ext:
        return ""
    ext = ext.lower().lstrip(".").strip()
    return ALIASES.get(ext, ext)


def category_of(ext: str, animated: bool | None = None) -> str | None:
    ext = normalise(ext)
    if ext in ANIMATED_CAPABLE and animated is not None:
        return VIDEO if animated else IMAGE
    if ext == "gif":
        return VIDEO if GIF_IS_VIDEO else IMAGE
    return CATEGORY_OF.get(ext)


def is_supported_source(ext: str) -> bool:
    ext = normalise(ext)
    if category_of(ext) is None:
        return False
    return _needs_are_met(FORMAT_NEEDS.get(ext, ()))


def declared_targets(ext: str, animated: bool | None = None) -> tuple[str, ...]:
    ext = normalise(ext)
    if ext in ANIMATED_CAPABLE and animated is not None:
        listed = ANIMATED_TARGETS if animated else TARGETS_BY_CATEGORY[IMAGE]
    else:
        listed = TARGETS_BY_FORMAT.get(ext)
        if listed is None:
            listed = TARGETS_BY_CATEGORY.get(category_of(ext), ())
    return tuple(target for target in listed if target != ext)


def targets_for(ext: str, animated: bool | None = None) -> list[str]:
    ext = normalise(ext)
    category = category_of(ext, animated)
    if category is None or not is_supported_source(ext):
        return []
    out = []
    for target in declared_targets(ext, animated):
        needs = TARGET_NEEDS.get((category, target), FORMAT_NEEDS.get(target, ()))
        if _needs_are_met(needs) and _encoder_ok(category, target):
            out.append(target)
    return out


def popular_targets(ext: str, animated: bool | None = None) -> list[str]:
    targets = targets_for(ext, animated)
    if len(targets) <= POPULAR_COUNT + 2:
        return targets
    return targets[:POPULAR_COUNT]


def supported_sources(category: str) -> list[str]:
    return [ext for ext in SOURCES.get(category, ())
            if category_of(ext) == category and is_supported_source(ext)]


def supported_targets(category: str) -> list[str]:
    seen = []
    for source in supported_sources(category):
        for target in targets_for(source):
            if target not in seen:
                seen.append(target)
    declared = [target for target in TARGETS_BY_CATEGORY.get(category, ()) if target in seen]
    return declared + [target for target in seen if target not in declared]


def counts() -> tuple[int, int, int]:
    sources, targets, pairs = set(), set(), 0
    for category in CATEGORIES:
        for source in supported_sources(category):
            available = targets_for(source)
            if not available:
                continue
            sources.add(source)
            targets.update(available)
            pairs += len(available)
    return len(sources), len(targets), pairs


def find_format(text: str) -> str | None:
    if not text:
        return None
    word = text.strip().lower().rsplit("/", 1)[-1].strip().lstrip(".")
    word = word.split()[0] if word.split() else ""
    word = normalise(word)
    return word if word in CATEGORY_OF else None


MAX_PAGES = int(os.environ.get("CONVERT_MAX_PAGES") or 200)
MAX_ROWS = int(os.environ.get("CONVERT_MAX_ROWS") or 100_000)
MAX_CELLS = int(os.environ.get("CONVERT_MAX_CELLS") or 2_000_000)
MAX_PAGE_MEGAPIXELS = int(os.environ.get("CONVERT_MAX_PAGE_MEGAPIXELS") or 100)
MAX_BATCH = int(os.environ.get("CONVERT_MAX_BATCH") or 20)
