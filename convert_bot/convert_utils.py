"""The conversions themselves, and the Telegram Stars pricing table.

Three backends, chosen by what the source file is:

  Pillow          still images, in-process. Also the one that writes a PDF
                  from pictures, which is the thing most people come here for.
  ffmpeg          video, audio and subtitles, shelled out under a timeout.
  doc_convert /   documents and structured data. Both import their heavy
  data_convert    libraries lazily, so a host without them still runs.

`formats.py` decides *what* is offered; this file does it. The split matters:
the menu, the invoice and /formats all have to agree about what is possible
before any money changes hands, and they agree by reading one table rather
than by each knowing a little about ffmpeg.

Everything here is path-in, path-out and blocking, and every caller runs it
through `asyncio.to_thread`. Deliberately: the caller already has the upload
on disk and is going to stream the result straight back to Telegram, so
routing it through two `bytes` objects would mean holding the file in memory
twice over for nothing. With files past the Bot API's 20 MB that is a couple
of hundred megabytes per conversion.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import zipfile
from pathlib import Path
from typing import NamedTuple

from PIL import Image

import big_files
import formats

# ---------- what the caller gets back ----------


class Result(NamedTuple):
    """One file to send, and enough about it to caption it honestly.

    `items` is how many things went into it -- pages rendered, images bound
    into a PDF, files converted -- and it is 1 for the ordinary case. `zipped`
    says the file is an archive rather than the format on the button, which
    happens whenever one input turns into more than one output and is the
    single most surprising thing this bot does. The caption says so.
    """
    path: Path
    items: int = 1
    zipped: bool = False


class ConversionError(Exception):
    pass


# ---------- how far a conversion has got ----------
# Set by convert_worker.py, and only there: a callable taking (done, total) in
# whatever unit the step counts -- seconds of media for ffmpeg, pages for a
# document, files for an album. The bot turns it into the progress bar on the
# conversion's status message. Nothing here depends on it being set, and a
# failure inside it is never a failed conversion.
PROGRESS = None


def report(done: float, total: float) -> None:
    if PROGRESS is None or not total:
        return
    try:
        PROGRESS(min(done, total), total)
    except Exception:
        pass


# ---------- limits ----------
# The cloud Bot API refuses to let a bot download anything over 20 MB. Two
# things lift that: MTProto for the big files alone (CBOT_API_ID and
# CBOT_API_HASH -- see big_files.py), or a Bot API server of your own
# (LOCAL_BOT_API_URL, https://github.com/tdlib/telegram-bot-api). Either way
# the transport carries 2000 MB, and since 2.0.0 that is the limit.
#
# It was 100 MB, on the reasoning that the largest file is what a conversion
# can finish in jobs.TIME_LIMIT_S. Size was a poor proxy for that: a minute of
# the owner's 4K60 HDR video is about 500 MB and more work than half an hour
# of a lecture recording the same size. The owner, 2026-09-28: "Raise the
# upload limit to a safe max". What makes a big file safe is the guards on
# what it costs, not a number of megabytes:
#
#   disk      checked against the size Telegram declares, before anything is
#             downloaded (jobs.USER_STORAGE_MB, STORAGE_MB, MIN_FREE_MB);
#   transfer  stopped when it stalls rather than at a fixed time
#             (big_files.STALL_S);
#   time      a conversion on course to miss its limit is stopped within its
#             first minute and refunded, rather than run to the limit and
#             then refunded (jobs.run_worker);
#   memory    capped by what is in the file -- MAX_MEGAPIXELS, MAX_PAGES,
#             MAX_CELLS -- which a file's size never measured anyway;
#   money     priced by size, past 200 MB by the 100 MB, and
#             tests/test_pricing.py holds every size up to this one to
#             covering its worst case.
#
# CONVERT_MAX_FILE_MB still lowers it. With neither route it is clamped back
# to 20, so the bot cannot promise a limit Telegram will not let it meet.
_LOCAL_BOT_API = bool(os.environ.get("LOCAL_BOT_API_URL"))
big_files.configure("CBOT", "convertbot")      # shared/big_files.py serves DownloaderBot too
BIG_FILES = _LOCAL_BOT_API or big_files.CONFIGURED
BOT_API_MAX_MB = big_files.BOT_API_DOWNLOAD_BYTES // (1024 * 1024)
MAX_FILE_MB = int(os.environ.get("CONVERT_MAX_FILE_MB")
                  or (big_files.MTPROTO_MAX_BYTES // (1024 * 1024) if BIG_FILES else BOT_API_MAX_MB))
if not BIG_FILES:
    MAX_FILE_MB = min(MAX_FILE_MB, BOT_API_MAX_MB)


def _big_files_refused() -> None:
    """Telegram refused the API id and hash (big_files.verify): promise, and
    take, only what the Bot API allows -- as if they had never been set."""
    global BIG_FILES, MAX_FILE_MB
    if _LOCAL_BOT_API:
        return
    BIG_FILES = False
    MAX_FILE_MB = min(MAX_FILE_MB, BOT_API_MAX_MB)


big_files.on_disabled(_big_files_refused)


def max_file_mb_in(chat_id: int) -> int:
    """The largest upload taken in this chat. Over MTProto that is private
    chats only; a local Bot API server has no such distinction."""
    if _LOCAL_BOT_API or big_files.usable(chat_id):
        return MAX_FILE_MB
    return min(MAX_FILE_MB, BOT_API_MAX_MB)


# Pillow's own guard against decompression bombs: a tiny file that declares
# enormous dimensions and expands into gigabytes of pixels once decoded. The
# default only *warns*, at 89 megapixels, and then decodes anyway -- which on
# a container sized to the cheapest plan that fits is an OOM kill rather than
# an error message. 80 megapixels is past any real camera and turns the whole
# class of problem into a caught exception.
Image.MAX_IMAGE_PIXELS = 80_000_000

# How many frames of an animation are allowed through. A GIF is under the
# size limit long before it is under the *work* limit -- a few hundred
# kilobytes can be thousands of frames -- and this is the ceiling that keeps
# an animated WEBP from becoming a minute of encoding sold for nothing.
MAX_FRAMES = int(os.environ.get("CONVERT_MAX_FRAMES") or 1200)

# What a page is rendered at when a document becomes pictures. 150 dpi is
# readable on a phone and about a quarter of the pixels of 300.
PAGE_DPI = int(os.environ.get("CONVERT_PAGE_DPI") or 150)


# ---------- pricing ----------
# What a conversion is sold for is shared/pricing.py's rule -- its cost plus a
# markup equal to the cost while it is small, growing more slowly after --
# applied to the most this conversion is allowed to cost: how long it may run
# (jobs.time_limit_for) and how much it may send back (jobs.big_send_limit).
# Both limits come from the file and what it is estimated to become, so the
# price does too, and the quote shown after the format is chosen is the
# price charged.
#
# Until 2.0.0 prices were a table of size tiers -- 20 ⚡ for 3 MB, 450 for
# 200, and ⚡200 more for every 100 MB after -- set for what the owner judged
# fair rather than for what a job costs. A 224 MB phone video to MP4 was
# quoted ⚡2,100; under this rule it is ⚡36, and nothing is ever quoted below
# what its job can cost (tests/test_pricing.py).
#
# Size is still a rough proxy for work: a 900 KB PDF can be four hundred
# pages. The answer is not a more elaborate price table, which nobody could
# predict from looking at their file -- it is the ceilings in formats.py
# (MAX_PAGES, MAX_ROWS, MAX_CELLS) and MAX_FRAMES above, which cap the work
# instead of charging for it, and the time limit, which a job on course to
# miss is stopped within its first minute of and refunded.
_MB = 1024 * 1024
# Under this much -- the file and its estimated result together -- a
# conversion is free, and held to jobs.FREE_TIME_LIMIT_S instead. In 1.6.0
# the owner moved it from 1 MB to 3.
FREE_UNDER_MB = 3


def price_for_size(size_bytes: int) -> int | None:
    """None if a file this size cannot be taken at all (over MAX_FILE_MB);
    otherwise what converting it into something its own size would cost.
    The real quote waits for the format, which decides what comes out.

    Credits, not Stars. Callers spend the result out of a balance; nothing
    here charges anybody."""
    if size_bytes > MAX_FILE_MB * _MB:
        return None
    return _price(size_bytes, size_bytes, 1.0)


# ---------------------------------------------------------------------------
# What will come out, and how hard it will be to make
# ---------------------------------------------------------------------------
# Until 1.7.0 a conversion was priced on the file that arrived, and the price
# was quoted before anybody had said what they wanted it turned into. That is
# the wrong number on both ends. A 78 KB .docx becomes a 1.6 MB PDF -- twenty
# times the bytes to make, hold and send -- and a 12 MB video becomes either a
# 1 MB MP3 or a 70 MB GIF depending on which button is tapped. The owner:
#
#   "the price should be shown after calculating the approximate output size.
#    the flow should go like this: User sends a file, chooses what to convert
#    to, and they get their price which should be set including the input and
#    output sizes. Maybe even consider the specific file types which would use
#    more hardware than others."
#
# So there are two tables here and neither of them pretends to be exact.
#
# SIZE_RATIO is what the output tends to weigh against the input, looked up as
# (source, target) first and (category, target) second, with a per-target
# fallback. Compression ratios vary hugely with content -- a photograph and a
# screenshot of the same pixel count are not the same PNG -- which is why the
# quote says "about", and why the price is fixed at the quote: if the result
# comes out bigger than estimated, the person who already agreed to a number
# is not asked for more.
#
# WORK_FACTOR is the part size cannot express. Encoding video is arithmetic
# per frame per second of footage; copying a JPEG's pixels into a PNG is one
# pass over a buffer. AVIF is worse still -- 187 CPU-seconds in 15 wall
# seconds locally, and CPU is billed per core-second, so a multithreaded
# encoder is not free just because it finishes quickly.

# Bytes handled: the input plus the estimated output ("sum"), or the larger of
# the two ("max"). Sum is the honest count of what a conversion moves through
# the container -- down the wire, through the encoder, back up the wire -- and
# is what the price is built on. "max" is here because it is the one other
# defensible answer, and switching is one environment variable rather than an
# edit.
BILLABLE = (os.environ.get("CONVERT_BILLABLE") or "sum").strip().lower()

_RATIO_BY_PAIR = {
    # Documents, where the spread is widest. docx -> pdf is measured: the
    # owner's 78 KB assignment came back as a 1.6 MB PDF.
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
    # Audio: what comes out depends almost entirely on what went in.
    ("wav", "mp3"): 0.10, ("wav", "m4a"): 0.09, ("wav", "ogg"): 0.10,
    ("wav", "opus"): 0.07, ("wav", "aac"): 0.09, ("wav", "flac"): 0.6,
    ("flac", "mp3"): 0.25, ("flac", "wav"): 1.7, ("flac", "m4a"): 0.22,
    ("flac", "opus"): 0.18, ("flac", "ogg"): 0.25, ("flac", "aac"): 0.22,
    ("aiff", "mp3"): 0.10, ("aiff", "flac"): 0.6,
    ("mp3", "wav"): 9.0, ("mp3", "flac"): 5.0, ("mp3", "aiff"): 9.0,
    ("m4a", "wav"): 9.0, ("ogg", "wav"): 9.0, ("opus", "wav"): 12.0,
    # Images where the pair matters more than the target alone.
    ("png", "jpg"): 0.15, ("png", "webp"): 0.25, ("png", "avif"): 0.15,
    ("jpg", "png"): 3.5, ("jpg", "bmp"): 12.0, ("jpg", "tiff"): 10.0,
    ("svg", "png"): 20.0, ("svg", "jpg"): 8.0, ("svg", "pdf"): 1.5,
    ("heic", "jpg"): 2.0, ("heic", "png"): 6.0,
}

# Per category of source, then per target. Only where it differs from the
# per-target default below.
_RATIO_BY_CATEGORY = {
    (formats.VIDEO, "gif"): 6.0, (formats.VIDEO, "apng"): 9.0, (formats.VIDEO, "webp"): 2.0,
    (formats.VIDEO, "mp4"): 0.9, (formats.VIDEO, "webm"): 0.7, (formats.VIDEO, "mkv"): 1.02,
    (formats.VIDEO, "mov"): 1.05, (formats.VIDEO, "avi"): 1.3,
    # Pulling the sound out of a video, which is most of what is left.
    (formats.VIDEO, "mp3"): 0.10, (formats.VIDEO, "m4a"): 0.09, (formats.VIDEO, "aac"): 0.09,
    (formats.VIDEO, "ogg"): 0.10, (formats.VIDEO, "opus"): 0.07,
    (formats.VIDEO, "wav"): 0.9, (formats.VIDEO, "flac"): 0.5,
    (formats.VIDEO, "png"): 0.05, (formats.VIDEO, "jpg"): 0.02,
    (formats.IMAGE, "pdf"): 1.05, (formats.IMAGE, "ico"): 0.05, (formats.IMAGE, "icns"): 0.1,
    (formats.DATA, "xlsx"): 0.6, (formats.DATA, "pdf"): 2.0, (formats.DATA, "html"): 1.4,
    (formats.DATA, "md"): 1.1, (formats.DATA, "json"): 1.3, (formats.DATA, "yaml"): 1.1,
    (formats.SUBTITLE, "txt"): 0.7,
}

# The fallback: roughly what this format weighs relative to whatever it came
# from. 1.0 for anything not worth an entry, which is most of them.
_RATIO_BY_TARGET = {
    "png": 3.0, "bmp": 10.0, "ppm": 10.0, "tga": 8.0, "tiff": 5.0, "sgi": 8.0,
    "jpg": 0.4, "webp": 0.4, "avif": 0.3, "heic": 0.4, "jp2": 0.8, "qoi": 2.5,
    "gif": 1.5, "ico": 0.05, "icns": 0.1,
    "txt": 0.4, "md": 0.5, "html": 1.2, "pdf": 2.0, "svg": 1.2,
    "wav": 8.0, "flac": 4.0, "aiff": 8.0, "mp3": 0.8, "m4a": 0.8, "aac": 0.8,
    "ogg": 0.8, "opus": 0.6, "wma": 0.9, "ac3": 1.0,
    "docx": 0.8,
}

# Bytes a rendered page tends to weigh, so that a 900 KB PDF of four hundred
# pages is not quoted as though it were four hundred kilobytes of PNG. Used
# whenever the page count is known -- see document_pages().
_BYTES_PER_PAGE = {"png": 350_000, "jpg": 120_000, "webp": 90_000,
                   "tiff": 1_200_000, "svg": 60_000, "pdf": 90_000}

# How much hardware a pair burns for its size. 1.0 is "one pass over the
# bytes"; everything above it is work that size alone does not predict.
_WORK_BY_TARGET = {
    "mp4": 2.0, "webm": 2.4, "mkv": 2.0, "mov": 2.0, "avi": 1.6,
    "apng": 1.8, "gif": 1.5, "avif": 1.8, "heic": 1.3,
}
_WORK_BY_CATEGORY = {
    # Laying a document out on a page, or rendering one to pictures, is the
    # PDF engine doing real work rather than a re-encode.
    (formats.DOCUMENT, "pdf"): 1.4, (formats.DOCUMENT, "png"): 1.4,
    (formats.DOCUMENT, "jpg"): 1.4, (formats.DOCUMENT, "webp"): 1.4,
    (formats.DOCUMENT, "tiff"): 1.5, (formats.DOCUMENT, "svg"): 1.4,
    (formats.DOCUMENT, "docx"): 1.4,
    # Video to a still or to sound is a decode and then almost nothing.
    (formats.VIDEO, "png"): 1.0, (formats.VIDEO, "jpg"): 1.0,
    (formats.VIDEO, "mp3"): 1.1, (formats.VIDEO, "m4a"): 1.1,
}


def work_factor(src_ext: str, target_ext: str) -> float:
    """How much hardware this pair costs for its size, as a multiplier."""
    src_ext, target_ext = formats.normalise(src_ext), formats.normalise(target_ext)
    category = formats.CATEGORY_OF.get(src_ext)
    if (category, target_ext) in _WORK_BY_CATEGORY:
        return _WORK_BY_CATEGORY[(category, target_ext)]
    return _WORK_BY_TARGET.get(target_ext, 1.0)


def estimate_output_bytes(src_ext: str, target_ext: str, in_bytes: int,
                          pages: "int | None" = None) -> int:
    """About how big the result will be. Never exact, and never presented as
    exact -- see the note above.

    `pages` overrides the ratio for the conversions where size is a bad proxy
    for the work: a PDF is mostly its pictures, and rendering it to PNGs
    produces a file per page whether the PDF was compressed well or badly.
    """
    src_ext, target_ext = formats.normalise(src_ext), formats.normalise(target_ext)
    if pages and target_ext in _BYTES_PER_PAGE and \
            formats.CATEGORY_OF.get(src_ext) == formats.DOCUMENT:
        return max(1, int(pages * _BYTES_PER_PAGE[target_ext]))
    category = formats.CATEGORY_OF.get(src_ext)
    ratio = _RATIO_BY_PAIR.get((src_ext, target_ext))
    if ratio is None:
        ratio = _RATIO_BY_CATEGORY.get((category, target_ext))
    if ratio is None:
        ratio = _RATIO_BY_TARGET.get(target_ext, 1.0)
    return max(1, int(in_bytes * ratio))


def billable_bytes(in_bytes: int, out_bytes: int) -> int:
    """What the price is charged on. See BILLABLE."""
    return max(in_bytes, out_bytes) if BILLABLE == "max" else in_bytes + out_bytes


def price_for_job(src_ext: str, target_ext: str, in_bytes: int,
                  pages: "int | None" = None) -> tuple:
    """(credits, estimated output bytes) for one conversion.

    Free under FREE_UNDER_MB, however hard the pair is; above it, the price
    of the most the job may cost -- its time limit, which grows with the size
    and how hard the pair is to run, and its send limit, which grows with the
    estimated result."""
    out_bytes = estimate_output_bytes(src_ext, target_ext, in_bytes, pages)
    return _price(in_bytes, out_bytes, work_factor(src_ext, target_ext)), out_bytes


def _price(in_bytes: int, out_bytes: int, work: float) -> int:
    import jobs      # here, not above: jobs imports this module
    import pricing
    billable = billable_bytes(in_bytes, out_bytes)
    if billable < FREE_UNDER_MB * _MB:
        return 0
    return pricing.price(pricing.cost(jobs.time_limit_for(billable, work, paid=True),
                                      jobs.big_send_limit(out_bytes or in_bytes)))


def document_pages(path, src_ext: str) -> "int | None":
    """How many pages a document has, read from its index rather than by
    rendering it. None when the engine is missing or the file will not open --
    the estimate falls back to a ratio, which is what it did before."""
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
    """What the cheapest paid conversion costs: the shortest time a paid job
    is given and the least it may send back."""
    import jobs
    import pricing
    return pricing.price(pricing.cost(jobs.TIME_BASE_S, jobs.SEND_LIMIT_BYTES))


def price_tiers_text(lang: str = "en") -> str:
    """The price list: free under FREE_UNDER_MB, the least a paid conversion
    costs, one worked example, and the most anything can -- every number
    worked out by the same rule a quote is."""
    import i18n  # local import -- avoids a hard import-order dependency for callers that only need pricing math
    import jobs
    import pricing

    example_mb = min(100, MAX_FILE_MB)
    example, _ = price_for_job("mp4", "mp4", example_mb * _MB)
    most = pricing.price(pricing.cost(jobs.TIME_LIMIT_S, jobs.big_send_limit(MAX_FILE_MB * _MB)))
    lines = [
        f"  0–{FREE_UNDER_MB} MB: " + i18n.t(lang, "price_free"),
        "  " + i18n.t(lang, "price_from", price=least_paid_price()),
        "  " + i18n.t(lang, "price_example", mb=example_mb, price=example),
        "  " + i18n.t(lang, "price_at_most", mb=MAX_FILE_MB, price=most),
    ]
    note = "" if BIG_FILES else " " + i18n.t(lang, "price_cloud_api_note")
    lines.append(f"  over {MAX_FILE_MB} MB: " + i18n.t(lang, "price_not_supported") + note)
    lines.append("  " + i18n.t(lang, "price_quoted_first"))
    return "\n".join(lines)


# ---------- Pillow, and the openers that are not built in ----------

_OPENERS_REGISTERED = False


def register_openers() -> None:
    """Teach Pillow the formats that arrive as a plugin.

    HEIC is the one that matters: it is what an iPhone camera writes, so it
    is the format most likely to turn up from somebody who has never
    converted a file before. Idempotent, and a missing pillow-heif is not an
    error -- formats.py has already stopped offering HEIC in that case.
    """
    global _OPENERS_REGISTERED
    if _OPENERS_REGISTERED:
        return
    try:
        import pillow_heif
        pillow_heif.register_heif_opener()
    except Exception:
        pass
    _OPENERS_REGISTERED = True


def _open_image(path) -> Image.Image:
    register_openers()
    try:
        img = Image.open(path)
        img.load()
        return img
    except Exception as exc:
        raise ConversionError(f"Couldn't read that as an image: {exc}") from exc


def gif_is_animated(path) -> bool:
    """Whether a GIF, WEBP or APNG actually has more than one frame.

    Cheap -- Pillow reads the header, not the frames -- and it is what decides
    whether "convert to PNG" is a reasonable offer or a way of throwing an
    animation away.
    """
    try:
        register_openers()
        with Image.open(path) as img:
            return getattr(img, "n_frames", 1) > 1
    except Exception:
        return False


# How each still target is written. Kept as one table rather than a chain of
# ifs because the list is now long enough that a missing `elif` would be a
# format silently falling through to "unsupported" instead of a syntax error.
#
#   mode    what the image has to be converted to first. RGB drops alpha,
#           which for a format with no alpha channel is the honest answer
#           and for JPEG needs the white-background paste below.
#   fmt     Pillow's own name for it, where it differs from the extension.
#   kwargs  quality and the like.
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
    # Both icon formats are a set of sizes rather than one picture, and both
    # have a ceiling: an ICO entry is at most 256 px, an ICNS 1024.
    "ico":  ("RGBA", "ICO", {"sizes": [(256, 256), (128, 128), (64, 64), (32, 32), (16, 16)]}),
    "icns": ("RGBA", "ICNS", {}),
}


def _flatten_onto_white(img: Image.Image) -> Image.Image:
    """A transparent PNG saved as JPEG has to become *something* where it was
    see-through, and black is the one answer nobody wants."""
    background = Image.new("RGB", img.size, (255, 255, 255))
    rgba = img.convert("RGBA")
    background.paste(rgba, mask=rgba.split()[-1])
    return background


def convert_image(in_path, target_ext: str, out_path) -> None:
    target_ext = formats.normalise(target_ext)
    spec = _IMAGE_TARGETS.get(target_ext)
    if spec is None:
        raise ConversionError(f"Unsupported image target format: {target_ext}")
    mode, pillow_format, kwargs = spec

    img = _open_image(in_path)
    try:
        if mode == "RGB" and img.mode in ("RGBA", "LA", "P"):
            img = _flatten_onto_white(img)
        elif mode == "P":
            img = img.convert("RGB").convert("P", palette=Image.ADAPTIVE)
        else:
            img = img.convert(mode)
        if target_ext == "icns":
            # ICNS refuses anything that is not one of its own square sizes,
            # and refuses it after the encode has already started.
            img = img.convert("RGBA").resize((1024, 1024))
        img.save(out_path, format=pillow_format, **kwargs)
    except ConversionError:
        raise
    except Exception as exc:
        raise ConversionError(f"Image conversion failed: {exc}") from exc
    finally:
        img.close()


def images_to_pdf(in_paths, out_path) -> int:
    """Every picture given, in order, one to a page.

    The conversion this bot exists for as far as most people are concerned,
    and the only one that takes more than one file. Returns the page count.
    """
    pages: list[Image.Image] = []
    try:
        for path in in_paths:
            img = _open_image(path)
            try:
                # A PDF page is RGB. Anything with alpha would otherwise be
                # rejected by the encoder after the whole set had been decoded.
                if img.mode in ("RGBA", "LA", "P"):
                    pages.append(_flatten_onto_white(img))
                else:
                    pages.append(img.convert("RGB"))
            finally:
                # convert() and the flatten both return a new image, so the
                # one that was opened is finished with either way.
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


# ---------- ffmpeg ----------
# Two limits that matter on a small shared host:
#
#   -threads   ffmpeg sizes its thread pool from the *host's* core count, not
#              this container's slice, so an unbounded encode takes CPU away
#              from every other user's messages for no throughput in return.
#              And memory: each thread holds frames. Before an input's -i it
#              caps only the decoder; the encoder and the filters size their
#              own pools. Railway's container reports 48 cores and holds 1 GB,
#              and there an ordinary 1080p video -- 19 MB -- asked for 945 MB
#              before its first frame and was killed (2026-09-29). So the cap
#              goes on all three: FFMPEG_CAP_IN before -i, FFMPEG_CAP_OUT
#              after the encoder's arguments.
#   timeout    without one, a malformed input can leave ffmpeg spinning on a
#              core until the container is restarted, while the user who sent
#              it just sees nothing happen.
FFMPEG_TIMEOUT_S = int(os.environ.get("FFMPEG_TIMEOUT_SECONDS", "600"))
FFMPEG_THREADS = os.environ.get("FFMPEG_THREADS", "2")
FFMPEG_CAP_IN = ["-threads", FFMPEG_THREADS, "-filter_threads", FFMPEG_THREADS,
                 "-filter_complex_threads", FFMPEG_THREADS]
FFMPEG_CAP_OUT = ["-threads", FFMPEG_THREADS]

# The encoder settings per target. Video first, then the audio-only targets,
# then subtitles -- which are the same ffmpeg but with no stream to encode,
# only a text format to re-serialise.
# Two arguments earn their place on every H.264 and VP9 target, and both are
# there because of the same class of input: a GIF, a screen recording, a
# phone video from an app that did not care.
#
#   -pix_fmt yuv420p   a GIF arrives as a palette and a PNG-in-video as RGB,
#                      and neither encoder will open with those. "Could not
#                      open encoder before EOF" is what the user saw.
#   _EVEN              H.264 and VP9 need even dimensions, and a 601-pixel
#                      screenshot is not something anybody should have to
#                      know that about.
_EVEN = "scale=trunc(iw/2)*2:trunc(ih/2)*2"
_H264 = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
         "-pix_fmt", "yuv420p", "-vf", _EVEN, "-c:a", "aac"]

_FFMPEG_ARGS = {
    "mp4":  _H264 + ["-movflags", "+faststart"],
    "mov":  _H264,
    "mkv":  _H264,
    "avi":  ["-c:v", "mpeg4", "-qscale:v", "5", "-vf", _EVEN, "-c:a", "libmp3lame", "-q:a", "4"],
    # -row-mt lets VP9 use the threads it is given; without it the -threads
    # cap above mostly serialises the encode instead.
    "webm": ["-c:v", "libvpx-vp9", "-crf", "32", "-b:v", "0", "-row-mt", "1",
             "-pix_fmt", "yuv420p", "-vf", _EVEN, "-c:a", "libopus"],
    # A GIF built from the default 256-colour web palette bands badly on
    # anything filmed. palettegen/paletteuse spends one extra pass working out
    # the palette this particular clip needs, and it is the difference between
    # a usable GIF and a poster for why nobody uses GIFs.
    "gif":  ["-vf", "fps=15,scale=480:-1:flags=lanczos,split[a][b];[a]palettegen[p];[b][p]paletteuse", "-loop", "0"],
    "webp": ["-c:v", "libwebp", "-vf", "fps=15,scale=480:-1:flags=lanczos", "-loop", "0", "-q:v", "70", "-an"],
    "apng": ["-c:v", "apng", "-vf", "fps=15,scale=480:-1:flags=lanczos", "-plays", "0", "-an"],
    # -vn on every audio target: it is what makes "extract the audio" the same
    # code path as "re-encode this MP3".
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
    # A still target for a moving source means one frame -- the first one.
    # Only reachable from an animated GIF, APNG or WEBP, where "give me the
    # picture" is a reasonable thing to ask.
    "png":  ["-frames:v", "1", "-an"],
    "jpg":  ["-frames:v", "1", "-q:v", "2", "-an"],
}


def _ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


# ---------- HDR ----------
# A phone video shot in HDR -- HDR10 or HLG; 4K60 HDR10 is what the owner's
# Galaxy S23 Ultra records -- stores brightness on a curve (PQ or HLG) that an
# SDR file does not have. Copied as it is into any target above, all of them
# 8-bit and SDR, it plays grey and washed out. So an HDR source going to one
# of them is tonemapped on the way: to linear light, BT.2020's colours into
# BT.709's, and a tone curve (Hable) that keeps the highlights rather than
# clipping them. It is not cheap at 4K, which is part of what jobs'
# early stop is for.
#
# It needs ffmpeg's zscale (libzimg). Without it the file converts as it
# always did -- washed out, but converted.
_HDR_TRANSFERS = {"smpte2084", "arib-std-b67"}
_TONEMAP = ("zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,"
            "tonemap=tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv,format=yuv420p")
_SDR_VIDEO = {"mp4", "mov", "mkv", "avi", "webm"}
_SDR_TARGETS = _SDR_VIDEO | {"gif", "webp", "apng", "png", "jpg"}
_ANIMATED_SCALE = "fps=15,scale=480:-1:flags=lanczos"
_filters_seen: "dict[str, bool]" = {}


def _ffmpeg_has_filters(*names: str) -> bool:
    """Whether this ffmpeg was built with every one of these filters. Asked
    once per process."""
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
        _filters_seen.setdefault("", False)      # asked, even if nothing came back
    return all(_filters_seen.get(name) for name in names)


def _is_hdr(in_path) -> bool:
    """Whether the first video stream is on an HDR curve, from ffprobe."""
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
    """The same arguments, with the tonemap in the video filter -- after the
    downscale for the small animated targets, where it costs a fraction of
    what it would at 4K, and first everywhere else -- and the result labelled
    as the BT.709 it now is."""
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
    if target_ext in _SDR_TARGETS and _is_hdr(in_path) and _ffmpeg_has_filters("zscale", "tonemap"):
        args = _tonemapped(args, target_ext)

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
    if proc.returncode != 0 or not os.path.exists(out_path):
        note = (proc.stderr or "").strip()[-500:] or "ffmpeg failed with no output"
        raise ConversionError(f"Conversion failed: {note}")


def _media_duration(in_path) -> "float | None":
    """How long the file plays, in seconds, from ffprobe -- or None, and the
    progress bar simply does not appear."""
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
    """The same ffmpeg run, reading its -progress output as it goes. stderr goes
    to a file rather than a pipe, so a chatty encoder can never fill a pipe
    nobody is reading and stall; the time limit is the same one."""
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
            raise ConversionError(f"Conversion failed: {note}")


def _reject_overlong_animation(in_path, src_ext: str) -> None:
    """An animation with more frames than MAX_FRAMES is refused up front.

    It is a work limit rather than a size limit, and it belongs before the
    invoice: a few hundred kilobytes of GIF can be an hour of encoding, and
    finding that out after taking somebody's Stars is the wrong order.
    """
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


# ---------- packing several outputs into one file ----------

def zip_files(paths, out_path) -> None:
    """One page per entry, deflated.

    A PDF with forty pages converted to PNG is forty files, and Telegram
    sends one document per message. A zip is the only answer that arrives as
    a single thing the user can do something with.
    """
    try:
        with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for path in paths:
                archive.write(path, arcname=Path(path).name)
    except Exception as exc:
        raise ConversionError(f"Couldn't package the results: {exc}") from exc


# ---------- the dispatcher ----------

def _out(work_dir, stem: str, ext: str) -> Path:
    return Path(work_dir) / f"{stem}.{ext}"


def convert(in_paths, src_ext: str, target_ext: str, work_dir, stem: str = "converted") -> Result:
    """Everything the bot converts, dispatched by what the source is.

    `in_paths` is a list because one conversion -- pictures into a PDF -- takes
    a set of files, and because "convert all of these to WEBP" is the same
    question asked once per file. Every other conversion takes exactly one and
    ignores the rest of the list.

    The result is always a single file, because a Telegram message is a single
    document. Where a conversion genuinely produces many (a PDF rendered to
    images, a batch converted one by one) they are zipped and `Result.zipped`
    says so, so the caption can.
    """
    in_paths = [str(path) for path in in_paths]
    if not in_paths:
        raise ConversionError("Nothing to convert.")
    src_ext = formats.normalise(src_ext)
    target_ext = formats.normalise(target_ext)
    category = formats.category_of(src_ext)
    work_dir = Path(work_dir)

    if len(in_paths) > 1:
        return _convert_batch(in_paths, src_ext, target_ext, work_dir, stem)

    in_path = in_paths[0]
    out_path = _out(work_dir, stem, target_ext)

    # The file settles which of the two a GIF, WEBP or APNG is. Asked here as
    # well as in the menu, because the menu is a message the user could have
    # been looking at for a quarter of an hour and the answer has to be the
    # one the conversion is actually run under.
    if src_ext in formats.ANIMATED_CAPABLE:
        category = formats.category_of(src_ext, gif_is_animated(in_path))

    if category == formats.IMAGE and src_ext != "svg":
        if target_ext == "pdf":
            images_to_pdf([in_path], out_path)
        elif target_ext in _IMAGE_TARGETS:
            convert_image(in_path, target_ext, out_path)
        else:
            # A still frame asked for a moving target -- mp4 from a one-frame
            # GIF. ffmpeg is the one who can, and it is a legitimate request.
            convert_media_ffmpeg(in_path, target_ext, out_path)
        return Result(out_path)

    if category in (formats.VIDEO, formats.AUDIO, formats.SUBTITLE):
        _reject_overlong_animation(in_path, src_ext)
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

    raise ConversionError(f"Nothing here converts a .{src_ext} file.")


def _convert_batch(in_paths, src_ext, target_ext, work_dir, stem) -> Result:
    """Several files at once: one PDF, or one zip of individual results.

    Only pictures arrive in batches -- Telegram albums are photos and videos,
    and a set of videos is not something anybody wants merged. So the two
    answers are "bind them into a document" and "do each of them", and the
    second is a zip because the first would be a lie.
    """
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
