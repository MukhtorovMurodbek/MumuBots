"""An album as one file: a PDF of its photos, or a ZIP of everything in it.

The two buttons under a delivered album (bot.py, merge_callback) end up here,
with the files already on disk. Nothing is kept: whoever calls this deletes
every input and the output as soon as the output has been sent.

THE PDF IS WRITTEN HERE, BY HAND, because the one thing a PDF of photographs
needs -- a page the size of the picture, with the picture on it -- is a few
dozen lines, and a library for it (PyMuPDF) is a binary dependency this bot's
image does not otherwise carry. What it writes:

  * one page per image, as many points as the image has pixels, so a 1080 x 1350
    post is a 1080 x 1350 pt page; scaled down, proportions kept, when a side
    would pass 14400 (the largest page Acrobat opens);
  * a baseline or progressive JPEG in RGB or grayscale goes in exactly as it
    came -- the same bytes, no second generation of compression -- as a
    DCTDecode stream. Everything else (PNG, WEBP, GIF, CMYK, a JPEG with an
    EXIF turn, anything with an alpha channel or a palette) goes through
    Pillow: turned upright, flattened onto white, converted to RGB and written
    as a JPEG at quality 92;
  * streamed to the output file as it goes -- each image is read, written and
    let go before the next is opened, so the memory a PDF takes is one decoded
    image, not the album -- with the cross-reference table built from the
    offsets the file actually has;
  * an image over 20 megapixels is refused (PictureTooLarge). The size is read
    from the file's header, so the refusal costs nothing; decoding is what
    would not be affordable on a small machine;
  * only a still JPEG, PNG, WEBP or GIF is a photo here. Pillow's open() takes
    some forty formats whatever the file is called, and a few of them decode
    inside open() (an ICO's embedded PNG), which would get past the check
    above; an animated picture decodes every frame's canvas. Neither is a
    photo (NotAPhoto), found before a pixel is decoded -- a WEBP's animation
    flag and canvas, and a GIF's canvas, from the file's first 30 bytes, before
    Pillow opens it. build_pdf leaves such a file out, as the bot leaves a video
    out of an album's PDF, and builds from the photographs; only when fewer
    than two are left is there no PDF (NothingToMerge). A picture that is too
    big is not skipped but refuses the whole PDF: it is one the person expects
    to see;
  * a hostile file's structure is judged before Pillow reads it: _check_header
    walks the JPEG's segments (to the first SOS), the PNG's chunks and the
    GIF's blocks (to the end), the WEBP's chunks, in constant memory and linear
    time, and refuses a file with more parts than a photo has (see
    MAX_HEADER_PARTS); a file cut short inside its structure is PhotoCutShort,
    not left out; a side over MAX_SIDE is PictureTooLarge;
  * one PDF is built at a time in the whole process (build_pdf_queued): a
    decode is a few hundred MB, and the five bots share one container. The gate
    stays shut until the build's thread has really ended, even if the caller was
    cancelled (a thread cannot be).

The ZIP stores its files, without compressing them: they are photographs and
video, which are compressed already, so deflate would spend the time and save
nothing. Its entries are named 01.jpg, 02.mp4 ... -- the position in the post
and the extension of the file that was downloaded, never a name that came from
the other end of the wire.

This module knows nothing about Telegram or the network.
"""
import asyncio
import io
import os
import re
import shutil
import struct
import zipfile
from datetime import datetime, timezone

import resources

from PIL import Image

# What a PDF of this bot's will take, and what it will not.
MIN_PDF_PHOTOS = 2            # a PDF of one photograph is the photograph
MIN_ZIP_ITEMS = 2
MAX_MEGAPIXELS = 20            # the largest these platforms serve is X's 4096 x 4096 (16.8 MP). Decoding one
                              # page peaks (measured) at 321 MB for a 20 MP WebP, 269 MB for a 16.8 MP WebP and
                              # 240 MB for a 20 MP LA PNG, in a process the five bots share: see pdf_gate
MAX_PAGE_POINTS = 14400
MAX_SIDE = 65_500             # JPEG's own ceiling is 65,500 a side: a 20,000,000 x 1 PNG (20 MP) passes the megapixel
                              # check and then cannot be written as a JPEG, so a longer side is refused as too large
JPEG_QUALITY = 92
# Pillow reads a file's whole header inside Image.open(), before any check here, and
# keeps what it reads: a JPEG's every segment (applist), a GIF's comment (by `+=`, which
# is quadratic), a PNG's every chunk. Measured on a valid 64 x 48 picture: 6 million
# 4-byte APP5 segments (41 MiB) cost 1.32 GB and 8.9 s; COM segments (19 MiB) 392 MB; tiny
# unknown PNG chunks (19 MiB) 217 MB and 4 s; a GIF of 1-byte comment sub-blocks took 1.6 s
# at 0.8 MB and did not finish at 3.2 MB (and it runs inside the PDF gate, so every PDF
# waited). _check_header therefore walks the header itself, in constant memory, and refuses
# a file with more than this many segments / chunks / blocks / sub-blocks before the first
# picture data. A real photo has tens, a big ICC profile a hundred or so. A JPEG is walked to
# its first SOS (libjpeg does the rest in C); a PNG and a GIF to their end, because Pillow
# also parses what follows the picture (load_end(), the comment loop at any frame, the
# frame count) in Python: a PNG's chunks other than IDAT are capped at this too, its IDATs at
# MAX_PNG_IDATS (a real 48 MB PNG in 8 KB IDATs has about 6,000), a GIF's comment
# sub-blocks at this and ALL its sub-blocks at MAX_GIF_SUB_BLOCKS (a real 48 MB GIF in
# 255-byte sub-blocks has about 190,000). A second frame in a GIF is an animation.
MAX_HEADER_PARTS = 1000
MAX_PNG_IDATS = 50_000
MAX_GIF_SUB_BLOCKS = 300_000
# Bytes of fill or junk between a JPEG's segments that are tolerated (Pillow skips them one
# at a time, in Python: a 48 MB run of 0xFF would take it about a minute).
_MAX_JPEG_FILL = 4096
# A progressive JPEG has about ten scans; libjpeg has no limit, and every scan of a
# 20-megapixel picture costs it a pass over the coefficients (measured 0.26 ms for
# a 17-byte scan: 666 KB of scans held the PDF gate for 7.9 s, 48 MB would be ~13
# minutes, in a thread that cannot be stopped). Counted over the whole file, in C.
MAX_JPEG_SCANS = 200
_CHUNK_HEAD = struct.Struct(">I4s")        # a PNG chunk's length and type

# The only formats a photo may be in. Not a list of what Pillow can read: open()
# tries ~40 plugins whatever the extension says, and one of them (ICO) decodes
# a PNG of any size inside open(), before the megapixel check can run.
PHOTO_FORMATS = ("JPEG", "PNG", "WEBP", "GIF")

_EXIF_ORIENTATION = 0x0112


class MergeError(Exception):
    """Something this module will not or cannot build."""


class PictureTooLarge(MergeError):
    """An image over MAX_MEGAPIXELS. Refused from its header, before a pixel
    is decoded."""


class NotAPhoto(MergeError):
    """A file that is not a still picture in one of PHOTO_FORMATS: another
    format, or an animation. Found before a pixel is decoded. build_pdf leaves
    it out of the PDF rather than raising it."""


class PhotoCutShort(MergeError):
    """A JPEG, PNG or GIF whose file ends inside its own header: a photo that
    was cut. Not NotAPhoto -- build_pdf does not leave it out, so the person is
    told something went wrong with it (as for one cut short in its picture
    data) instead of getting a PDF with a page quietly missing."""


class NothingToMerge(MergeError):
    """No file was given, or not enough of them were photographs to make a PDF
    of (build_pdf left the others out)."""


# ---------------------------------------------------------------------------
# Names
# ---------------------------------------------------------------------------

def output_name(platform: str, ext: str, now: "datetime | None" = None) -> str:
    """`instagram_20261007-1432.pdf`: what the person sees the file called.

    UTC, like every other file this family names after the time. The platform
    is cut down to letters and digits, though it comes from a list this bot
    keeps, because it is going into a filename that someone else's software
    will open."""
    now = now or datetime.now(timezone.utc)
    stem = re.sub(r"[^a-z0-9]+", "", (platform or "").lower()) or "album"
    return f"{stem}_{now:%Y%m%d-%H%M}.{ext}"


def _safe_ext(path: str, default: str = "bin") -> str:
    """The extension of a file on OUR disk, or `default` if it is not plainly
    one. resolvers.download() has already made that so; this is the second
    lock on the same door, because the name goes into an archive that is
    sent to somebody."""
    ext = os.path.splitext(str(path))[1].lstrip(".").lower()
    return ext if ext and len(ext) <= 5 and ext.isascii() and ext.isalnum() else default


def entry_name(index: int, path: str, width: int = 2) -> str:
    """`01.jpg` for the first file, `02.mp4` for the second. From the position
    and from the downloaded file's own extension; nothing the source said the
    file was called."""
    return f"{index:0{width}d}.{_safe_ext(path)}"


# ---------------------------------------------------------------------------
# ZIP
# ---------------------------------------------------------------------------

def build_zip(paths: "list[str]", out_path: str) -> int:
    """Every file in `paths`, in order, stored (not compressed) in one ZIP.
    Returns how many went in."""
    if not paths:
        raise NothingToMerge("nothing to put in a ZIP")
    width = max(2, len(str(len(paths))))
    stamp = datetime.now(timezone.utc).timetuple()[:6]
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_STORED) as archive:
        for number, path in enumerate(paths, start=1):
            # A ZipInfo of our own rather than archive.write(path, name): that
            # would copy the file's mode and modification time into the
            # archive, which are the server's and not worth sending.
            info = zipfile.ZipInfo(entry_name(number, path, width), date_time=stamp)
            info.compress_type = zipfile.ZIP_STORED
            info.external_attr = 0o644 << 16
            with open(path, "rb") as source, archive.open(info, "w", force_zip64=False) as target:
                shutil.copyfileobj(source, target, 1024 * 1024)
    return len(paths)


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------

# The first real marker after a scan's entropy-coded data: a 0xFF followed by
# anything but 0x00 (a stuffed 0xFF), 0xD0-0xD7 (a restart) or another 0xFF
# (fill: the marker is the last of the run). Found by the regex engine, in C --
# a walk in Python spends microseconds per byte, and a hostile file is all 0xFF.
_NEXT_MARKER = re.compile(rb"\xff[^\x00\xd0-\xd7\xff]")
_NOT_FILL = re.compile(rb"[^\xff]")
# A real JPEG has tens of segments between its scans, a hundred with a big
# ICC profile. A file with this many is not one, and walking 12 million
# four-byte segments in Python would take seconds: Pillow decodes it instead,
# under the megapixel limit.
_MAX_SEGMENTS = 10_000


def _jpeg_layout(data: bytes):
    """(width, height, components, length) of a JPEG that can go into a PDF
    untouched, or None.

    "Untouched" means what DCTDecode takes: baseline (SOF0) or progressive
    (SOF2), 8 bits, one or three components, with a size in the header, and a
    clean end. The walk below follows the file marker by marker, through the
    entropy-coded data of every scan, to the EOI -- so `length` stops at the
    end of the first picture, and whatever follows it (the second image of a
    phone's MPO, a trailing blob) is not carried into the PDF. A file that
    runs out before an EOI is not embedded; it goes to Pillow, which says so.
    """
    n = len(data)
    if data[:2] != b"\xff\xd8":
        return None
    pos, frame, segments = 2, None, 0
    while pos + 2 <= n:
        if data[pos] != 0xFF:
            return None
        marker = data[pos + 1]
        if marker == 0xFF:                       # fill bytes before a marker: skip the whole run
            found = _NOT_FILL.search(data, pos)
            if not found:
                return None
            pos = found.start() - 1              # on the last 0xFF, with the marker after it
            continue
        segments += 1
        if segments > _MAX_SEGMENTS:
            return None
        if marker == 0xD9:                       # EOI
            return frame + (pos + 2,) if frame else None
        if marker == 0x00:
            return None
        if marker == 0x01 or 0xD0 <= marker <= 0xD8:     # no length
            pos += 2
            continue
        if pos + 4 > n:
            return None
        length = int.from_bytes(data[pos + 2:pos + 4], "big")
        if length < 2:
            return None
        if marker in (0xC0, 0xC2):
            body = data[pos + 4:pos + 2 + length]
            if len(body) < 6 or frame is not None or body[0] != 8:
                return None
            height, width = int.from_bytes(body[1:3], "big"), int.from_bytes(body[3:5], "big")
            if not width or not height or body[5] not in (1, 3):
                return None
            frame = (width, height, body[5])
        elif marker in (0xC1, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            return None                          # extended, lossless, arithmetic: not DCTDecode's
        pos += 2 + length
        if marker == 0xDA:                       # SOS: entropy-coded data, up to the next real marker
            if frame is None:
                return None
            found = _NEXT_MARKER.search(data, pos)
            if not found:
                return None
            pos = found.start()
    return None


def _orientation(image) -> int:
    try:
        return int(image.getexif().get(_EXIF_ORIENTATION, 1) or 1)
    except Exception:
        return 1


def _upright(image):
    """The picture the way up the camera meant it. Only when there is
    something to do: exif_transpose copies, and a 20-megapixel copy is
    60 MB. The original is let go as soon as the turned one exists."""
    if _orientation(image) not in (2, 3, 4, 5, 6, 7, 8):
        return image
    from PIL import ImageOps      # here, so a stand-in for Pillow (tests/stubs) still imports
    turned = ImageOps.exif_transpose(image)
    if turned is not image:
        image.close()
    return turned


def _flatten(image):
    """RGB, with any transparency laid over white. A JPEG has no alpha, and
    dropping it instead of flattening turns every transparent pixel into
    whatever colour happened to be stored under it -- usually black."""
    if image.mode in ("I;16", "I;16L", "I;16B", "I"):
        # 16 bits a pixel: a plain convert() clips at 255 and returns a white page.
        image = image.point(lambda value: value * (1 / 256)).convert("L")
    if image.mode in ("RGBA", "LA", "PA", "RGBa", "La") or "transparency" in image.info:
        rgba = image if image.mode == "RGBA" else image.convert("RGBA")
        page = Image.new("RGB", rgba.size, (255, 255, 255))
        page.paste(rgba, mask=rgba)
        return page
    return image if image.mode == "RGB" else image.convert("RGB")


def _check_size(width: int, height: int) -> None:
    if width * height > MAX_MEGAPIXELS * 1_000_000:
        raise PictureTooLarge(f"{width} x {height} is over {MAX_MEGAPIXELS} megapixels")
    if max(width, height) > MAX_SIDE:
        raise PictureTooLarge(f"{width} x {height} has a side over {MAX_SIDE} pixels")


def _too_many(parts: int) -> None:
    if parts > MAX_HEADER_PARTS:
        raise NotAPhoto(f"more than {MAX_HEADER_PARTS} parts in a header before the picture")


def _count_scans(source) -> None:
    """Refuses a JPEG with more than MAX_JPEG_SCANS start-of-scan markers. Inside a
    scan's data a 0xFF is always stuffed (0xFF 0x00) or a restart, so FF DA is a
    scan, or - in a header segment's own bytes - a coincidence the limit is far
    above (a megabyte of ICC profile has ~16). Read in 1 MiB pieces; the byte
    before each piece is carried so a marker split across two is still seen."""
    source.seek(0)
    scans, tail = 0, b""
    while True:
        piece = source.read(1 << 20)
        if not piece:
            return
        scans += (tail + piece).count(b"\xff\xda")
        if scans > MAX_JPEG_SCANS:
            raise NotAPhoto(f"more than {MAX_JPEG_SCANS} scans in a JPEG")
        tail = piece[-1:]


def _walk_jpeg(source, size: int) -> None:
    """Segment by segment, jumping over each by its length, from the SOI to the
    first SOS (or EOI). Linear in the number of segments, and that is capped."""
    pos, parts, filler = 2, 0, 0
    while True:
        source.seek(pos)
        pair = source.read(2)
        if len(pair) < 2:
            raise PhotoCutShort("a JPEG that ends before its picture")
        if pair[0] != 0xFF or pair[1] in (0x00, 0xFF):      # junk, a stuffed byte or fill
            filler += 1
            if filler > _MAX_JPEG_FILL:
                raise NotAPhoto("junk between a JPEG's segments")
            pos += 1
            continue
        parts += 1
        _too_many(parts)
        marker = pair[1]
        if marker in (0xDA, 0xD9):                          # SOS, EOI: the picture starts, or ends
            return
        if marker == 0x01 or 0xD0 <= marker <= 0xD8:        # no length
            pos += 2
            continue
        raw = source.read(2)
        if len(raw) < 2:
            raise PhotoCutShort("a JPEG that ends inside a segment's length")
        length = int.from_bytes(raw, "big")
        if length < 2:
            raise NotAPhoto("a JPEG segment without a length")
        pos += 2 + length
        if pos > size:
            raise PhotoCutShort("a JPEG segment that runs past the end of the file")


def _walk_png(source, size: int) -> None:
    """Chunk by chunk (length, type, data, CRC), jumping over each, from the
    signature to IEND (or the end of the file). The WHOLE file, not up to the
    first IDAT: Pillow's load_end() walks the chunks after the picture in
    Python too. Chunks other than IDAT are capped at MAX_HEADER_PARTS in all,
    IDATs at MAX_PNG_IDATS. Read in 64 KiB windows, because 50,000 IDATs of 12
    bytes are 50,000 calls otherwise."""
    pos, others, idats = 8, 0, 0
    while True:
        source.seek(pos)
        window = source.read(65536)
        end = len(window)
        offset = 0
        while offset + 8 <= end:
            length, kind = _CHUNK_HEAD.unpack_from(window, offset)
            if kind == b"IDAT":
                idats += 1
                if idats > MAX_PNG_IDATS:
                    raise NotAPhoto(f"more than {MAX_PNG_IDATS} IDAT chunks in a PNG")
            else:
                others += 1
                _too_many(others)
                if kind == b"IEND":
                    return
            offset += 12 + length
            if pos + offset > size:
                raise PhotoCutShort("a PNG chunk that runs past the end of the file")
        pos += offset
        if pos >= size:
            if pos == size and idats:                       # the file stops on a chunk boundary after the picture
                return
            raise PhotoCutShort("a PNG that ends before its picture is over")
        if not offset:                                      # fewer than 8 bytes left: inside a chunk's header
            raise PhotoCutShort("a PNG that ends inside a chunk's header")


def _gif_sub_blocks(source, pos: int, count: list, comment: bool) -> int:
    """Past the sub-blocks that start at `pos` (each a length byte and that many
    bytes, ended by a zero length): the position after the terminator. `count` is
    [all sub-blocks so far, comment sub-blocks so far], kept across calls. Read
    in 64 KiB windows and counted in a tight loop: a call per sub-block, 300,000
    of them, is too slow."""
    seen = count[0]
    room = MAX_GIF_SUB_BLOCKS - seen
    if comment:
        room = min(room, MAX_HEADER_PARTS - count[1])
    taken = 0
    while True:
        source.seek(pos)
        window = source.read(65536)
        if not window:
            raise PhotoCutShort("a GIF block that runs past the end of the file")
        end = len(window)
        offset = 0
        while offset < end:
            length = window[offset]
            if not length:
                count[0] += taken
                if comment:
                    count[1] += taken
                return pos + offset + 1
            offset += 1 + length
            taken += 1
            if taken > room:
                if comment and taken + count[1] > MAX_HEADER_PARTS:
                    raise NotAPhoto(f"more than {MAX_HEADER_PARTS} sub-blocks in a GIF comment")
                raise NotAPhoto(f"more than {MAX_GIF_SUB_BLOCKS} sub-blocks in a GIF")
        pos += offset


def _walk_gif(source, size: int, head: bytes) -> None:
    """Block by block, the WHOLE file, to the trailer (or the end of the file):
    extension blocks and the picture's own data, sub-block by sub-block, every
    one counted. Pillow's comment loop (`+=`) runs for a comment anywhere in the
    file, and its is_animated / n_frames skip a frame's data a sub-block at a
    time, in Python. A second image descriptor is a second frame: an animated
    picture, which is no photo. Pillow skips a stray byte at a time, in Python,
    so anything that is not a block is refused."""
    if len(head) < 13:
        raise PhotoCutShort("a GIF without its screen descriptor")
    pos = 13 + (3 * (2 << (head[10] & 7)) if head[10] & 0x80 else 0)      # past the global colour table
    blocks, images, count = 0, 0, [0, 0]
    while True:
        source.seek(pos)
        block = source.read(1)
        if not block or block == b"\x3b":                   # trailer, or the file just stops
            if images:
                return
            raise PhotoCutShort("a GIF that ends before its picture")
        if block == b"\x21":                                # an extension block
            blocks += 1
            _too_many(blocks)
            label = source.read(1)
            if not label:
                raise PhotoCutShort("a GIF extension that runs past the end of the file")
            pos = _gif_sub_blocks(source, pos + 2, count, label == b"\xfe")
        elif block == b"\x2c":                              # an image descriptor, and its data
            images += 1
            if images > 1:
                raise NotAPhoto("an animated picture")
            descriptor = source.read(9)
            if len(descriptor) < 9:
                raise PhotoCutShort("a GIF that ends inside its picture's descriptor")
            pos += 10
            if descriptor[8] & 0x80:
                pos += 3 * (2 << (descriptor[8] & 7))       # past the local colour table
            pos = _gif_sub_blocks(source, pos + 1, count, False)      # +1: the LZW code size byte
        else:
            raise NotAPhoto("something in a GIF that is not a block")


def _walk_webp(source, size: int) -> None:
    """Chunk by chunk (fourcc, little-endian size, data padded to even), up to
    the first picture chunk."""
    pos, parts = 12, 0
    while True:
        source.seek(pos)
        head = source.read(8)
        if len(head) < 8:
            raise NotAPhoto("a WEBP that ends before its picture")
        parts += 1
        _too_many(parts)
        if head[:4] in (b"VP8 ", b"VP8L", b"ANMF"):
            return
        length = int.from_bytes(head[4:8], "little")
        pos += 8 + length + (length & 1)
        if pos > size:
            raise NotAPhoto("a WEBP chunk that runs past the end of the file")


def _check_header(path: str) -> None:
    """Refuse, from the file's start and before Pillow is asked to open it,
    what open() itself would pay for. Some Pillow versions build the WEBP
    animation decoder, with a canvas for every frame, inside open() -- a
    96-byte file claiming 20 MP measured 403 MB there -- so whether the picture
    is animated, or too big, is read here from the VP8X chunk (flags at byte 20,
    canvas size as 24-bit little-endian values less one at 24 and 27). A GIF's
    logical screen (bytes 6-9) is its canvas. Then the header's own parts are
    counted (MAX_HEADER_PARTS): open() would otherwise read and keep millions
    of them. Anything else, and any header that does not say, is left to the
    checks after open()."""
    with open(path, "rb") as source:
        head = source.read(30)
        size = os.fstat(source.fileno()).st_size
        if len(head) >= 30 and head[:4] == b"RIFF" and head[8:12] == b"WEBP" and head[12:16] == b"VP8X":
            _check_size(1 + int.from_bytes(head[24:27], "little"), 1 + int.from_bytes(head[27:30], "little"))
            if head[20] & 0x02:
                raise NotAPhoto("an animated picture")
        elif len(head) >= 10 and head[:6] in (b"GIF87a", b"GIF89a"):
            _check_size(int.from_bytes(head[6:8], "little"), int.from_bytes(head[8:10], "little"))
        if head[:3] == b"\xff\xd8\xff":
            _walk_jpeg(source, size)
            _count_scans(source)
        elif head[:8] == b"\x89PNG\r\n\x1a\n":
            _walk_png(source, size)
        elif head[:6] in (b"GIF87a", b"GIF89a"):
            _walk_gif(source, size, head)
        elif head[:4] == b"RIFF" and head[8:12] == b"WEBP":
            _walk_webp(source, size)


def _animated(image) -> bool:
    """More than one picture in it, now that Pillow has opened it. Every frame
    of a WEBP or GIF costs a canvas the size of the whole picture. An MPO -- a
    phone's JPEG with a second picture after the first -- counts its pictures
    as frames too, but it is one photo: the first picture, which is the only
    one this module reads."""
    if image.format == "MPO":
        return False
    if image.format == "GIF":
        return False        # _walk_gif has seen the whole file and refused a second frame; is_animated would walk it again in Python
    return bool(getattr(image, "is_animated", False)) or getattr(image, "n_frames", 1) > 1


def _open_photo(path: str):
    """The file opened as a Pillow image, if it is a still photograph of a size
    this module takes: everything that can be told without decoding a pixel.
    PictureTooLarge if it is over the limit, NotAPhoto if it is not a still
    JPEG, PNG, WEBP or GIF. The caller closes the image."""
    _check_header(path)
    try:
        # Pillow warns past ~89 MP; the limit that matters is ours, below. (Not
        # caught with warnings.catch_warnings(): that swaps a process-wide list,
        # and this runs in a pool thread beside every other bot's threads.)
        image = Image.open(path, formats=PHOTO_FORMATS)
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise PictureTooLarge("an image far over the limit") from exc
    except Image.UnidentifiedImageError as exc:
        raise NotAPhoto("not a JPEG, PNG, WEBP or GIF") from exc
    except OSError as exc:
        # A corrupt or cut WEBP: libwebp refuses it in open(). Nothing of it can be
        # shown, and the page tree is numbered up front, so it is left out like any
        # other file that is not a photo. (A JPEG or PNG cut short raises later, in
        # load(), and stays a failure: the person should know a photo was cut.)
        if "could not create decoder object" in str(exc):
            raise NotAPhoto("a WEBP that cannot be decoded") from exc
        raise
    try:
        width, height = image.size           # from the header: nothing is decoded
        _check_size(width, height)
        if _animated(image):
            raise NotAPhoto("an animated picture")
    except Exception:
        image.close()
        raise
    return image


def _prepare(path: str):
    """(width, height, colour space, JPEG bytes) for one page of the PDF. The
    JPEG bytes are a `bytes`, or a memoryview of the file's own when only the
    start of the file is the picture: both go to out.write() and len()."""
    image = _open_photo(path)
    with image:
        width, height = image.size
        if _orientation(image) == 1:
            with open(path, "rb") as source:
                data = source.read()
            layout = _jpeg_layout(data)
            if layout and layout[:2] == (width, height):
                w, h, components, length = layout
                # A view, not data[:length]: that copies the picture, and the file is already in memory.
                return (w, h, "/DeviceGray" if components == 1 else "/DeviceRGB",
                        data if length == len(data) else memoryview(data)[:length])
            del data
        image.load()
        _check_size(*image.size)             # the header is the file's word; this is what came out
        picture = _flatten(_upright(image))
        buffer = io.BytesIO()
        picture.save(buffer, "JPEG", quality=JPEG_QUALITY)
        size = picture.size
        picture.close()
        return size[0], size[1], "/DeviceRGB", buffer.getvalue()


def _points(value: float) -> bytes:
    # At least one point a side: a page of 0 pt is no page (the longest side is
    # capped at MAX_PAGE_POINTS, so a very thin picture would round to it).
    text = f"{max(1.0, min(value, MAX_PAGE_POINTS)):.3f}".rstrip("0").rstrip(".")
    return text.encode()


def build_pdf(paths: "list[str]", out_path: str) -> int:
    """One page per photograph in `paths`, in order, written to `out_path`.
    Returns the page count. A file that is not a still JPEG, PNG, WEBP or GIF
    is left out, found before anything is written (the page tree is numbered
    up front); NothingToMerge if none is left, or if some were left out and
    fewer than MIN_PDF_PHOTOS remain. PictureTooLarge for an image over the
    limit refuses the whole PDF, and so does whatever Pillow raises for a
    photograph it cannot decode -- then `out_path` is partial and the caller
    deletes it."""
    if not paths:
        raise NothingToMerge("nothing to put in a PDF")
    photos = []
    for path in paths:
        try:
            _open_photo(path).close()
        except NotAPhoto:
            continue
        photos.append(path)
    if not photos or (len(photos) < len(paths) and len(photos) < MIN_PDF_PHOTOS):
        raise NothingToMerge(f"{len(photos)} of {len(paths)} files are photographs")
    paths = photos
    pages = len(paths)
    # Fixed numbering, so the page tree can be written before the pages are
    # known to have worked: 1 catalog, 2 page tree, then three objects (page,
    # its content, its image) for each image.
    total = 3 + 3 * pages
    offsets: "dict[int, int]" = {}
    with open(out_path, "wb") as out:
        def obj(number: int, body: bytes, stream: "bytes | None" = None) -> None:
            offsets[number] = out.tell()
            out.write(b"%d 0 obj\n" % number)
            if stream is None:
                out.write(body + b"\nendobj\n")
            else:
                out.write(body[:-2] + b" /Length %d >>\nstream\n" % len(stream))
                out.write(stream)
                out.write(b"\nendstream\nendobj\n")

        out.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        obj(1, b"<< /Type /Catalog /Pages 2 0 R >>")
        kids = b" ".join(b"%d 0 R" % (3 + 3 * i) for i in range(pages))
        obj(2, b"<< /Type /Pages /Count %d /Kids [%s] >>" % (pages, kids))
        for index, path in enumerate(paths):
            width, height, colour, jpeg = _prepare(path)
            scale = min(1.0, MAX_PAGE_POINTS / max(width, height))
            page_w, page_h = _points(width * scale), _points(height * scale)
            page, content, image = 3 + 3 * index, 4 + 3 * index, 5 + 3 * index
            obj(page, b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %s %s] "
                      b"/Resources << /XObject << /Im0 %d 0 R >> >> /Contents %d 0 R >>"
                % (page_w, page_h, image, content))
            obj(content, b"<< >>", b"q %s 0 0 %s 0 0 cm /Im0 Do Q" % (page_w, page_h))
            obj(image, b"<< /Type /XObject /Subtype /Image /Width %d /Height %d /ColorSpace %s "
                       b"/BitsPerComponent 8 /Filter /DCTDecode >>" % (width, height, colour.encode()),
                jpeg)
            del jpeg
            out.flush()                  # on disk before the next image is opened
        table = out.tell()
        out.write(b"xref\n0 %d\n0000000000 65535 f \n" % total)
        for number in range(1, total):
            out.write(b"%010d 00000 n \n" % offsets[number])
        out.write(b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (total, table))
    return pages


# One PDF at a time in the whole process (on a small container; see pdfs_at_once). Decoding a page peaks at 240-320 MB
# (see MAX_MEGAPIXELS) and up to three merges can be in pool threads at once:
# three 20 MP WebPs together measured 957 MB, which in a 1 GB container shared
# by all five bots is the kernel killing every one of them. A ZIP only copies
# bytes and needs no gate. Made on first use, like bot._slots(): a Semaphore
# belongs to the event loop it first has to wait in, and the bot has one.
_pdf_gate: "asyncio.Semaphore | None" = None


def pdfs_at_once() -> int:
    """How many album PDFs are built at once: DBOT_PDF_AT_ONCE when it is set, else one for
    each 1024 MB of the bot's share of the memory (a decode peaks at 240-320 MB, and three
    together measured 957 MB), from one up to four; one when the memory is not known."""
    fixed = os.environ.get("DBOT_PDF_AT_ONCE")
    if fixed:
        return max(1, int(fixed))
    job_memory = resources.job_memory_mb("downloader_bot")
    return 1 if job_memory is None else max(1, min(4, job_memory // 1024))


def pdf_gate() -> "asyncio.Semaphore":
    global _pdf_gate
    if _pdf_gate is None:
        _pdf_gate = asyncio.Semaphore(pdfs_at_once())
    return _pdf_gate


async def build_pdf_queued(paths: "list[str]", out_path: str) -> int:
    """build_pdf in a pool thread, after any other PDF being built has
    finished. Someone who waits sees what they would see anyway: the working
    message."""
    gate = pdf_gate()
    await gate.acquire()
    try:
        # The thread as a task of its own, shielded from the caller. A thread
        # cannot be cancelled: if the caller is (a restart, a timeout), the build
        # runs on, and the gate must stay shut until it has really finished, or the
        # next decode starts on top of it. Whoever ends it -- here, or the callback
        # once the thread is done -- is the one that lets go of the gate.
        task = asyncio.ensure_future(asyncio.to_thread(build_pdf, paths, out_path))
    except BaseException:
        gate.release()
        raise

    def finished(done: "asyncio.Future") -> None:
        gate.release()
        if not done.cancelled():
            done.exception()                    # seen, so an abandoned failure is not logged as never retrieved

    task.add_done_callback(finished)
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        # Nobody will be told about this file, and the thread may yet create it:
        # it goes when the thread is done.
        def discard(_done: "asyncio.Future") -> None:
            try:
                os.remove(out_path)
            except OSError:
                pass

        task.add_done_callback(discard)
        raise
