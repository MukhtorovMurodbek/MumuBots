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

MIN_PDF_PHOTOS = 2
MIN_ZIP_ITEMS = 2
MAX_MEGAPIXELS = 20
MAX_PAGE_POINTS = 14400
MAX_SIDE = 65_500
JPEG_QUALITY = 92
MAX_HEADER_PARTS = 1000
MAX_PNG_IDATS = 50_000
MAX_GIF_SUB_BLOCKS = 300_000
_MAX_JPEG_FILL = 4096
MAX_JPEG_SCANS = 200
_CHUNK_HEAD = struct.Struct(">I4s")

PHOTO_FORMATS = ("JPEG", "PNG", "WEBP", "GIF")

_EXIF_ORIENTATION = 0x0112


class MergeError(Exception):
    pass


class PictureTooLarge(MergeError):
    pass


class NotAPhoto(MergeError):
    pass


class PhotoCutShort(MergeError):
    pass


class NothingToMerge(MergeError):
    pass


def output_name(platform: str, ext: str, now: "datetime | None" = None) -> str:
    now = now or datetime.now(timezone.utc)
    stem = re.sub(r"[^a-z0-9]+", "", (platform or "").lower()) or "album"
    return f"{stem}_{now:%Y%m%d-%H%M}.{ext}"


def _safe_ext(path: str, default: str = "bin") -> str:
    ext = os.path.splitext(str(path))[1].lstrip(".").lower()
    return ext if ext and len(ext) <= 5 and ext.isascii() and ext.isalnum() else default


def entry_name(index: int, path: str, width: int = 2) -> str:
    return f"{index:0{width}d}.{_safe_ext(path)}"


def build_zip(paths: "list[str]", out_path: str) -> int:
    if not paths:
        raise NothingToMerge("nothing to put in a ZIP")
    width = max(2, len(str(len(paths))))
    stamp = datetime.now(timezone.utc).timetuple()[:6]
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_STORED) as archive:
        for number, path in enumerate(paths, start=1):
            info = zipfile.ZipInfo(entry_name(number, path, width), date_time=stamp)
            info.compress_type = zipfile.ZIP_STORED
            info.external_attr = 0o644 << 16
            with open(path, "rb") as source, archive.open(info, "w", force_zip64=False) as target:
                shutil.copyfileobj(source, target, 1024 * 1024)
    return len(paths)


_NEXT_MARKER = re.compile(rb"\xff[^\x00\xd0-\xd7\xff]")
_NOT_FILL = re.compile(rb"[^\xff]")
_MAX_SEGMENTS = 10_000


def _jpeg_layout(data: bytes):
    n = len(data)
    if data[:2] != b"\xff\xd8":
        return None
    pos, frame, segments = 2, None, 0
    while pos + 2 <= n:
        if data[pos] != 0xFF:
            return None
        marker = data[pos + 1]
        if marker == 0xFF:
            found = _NOT_FILL.search(data, pos)
            if not found:
                return None
            pos = found.start() - 1
            continue
        segments += 1
        if segments > _MAX_SEGMENTS:
            return None
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
        if marker in (0xC0, 0xC2):
            body = data[pos + 4:pos + 2 + length]
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
    if _orientation(image) not in (2, 3, 4, 5, 6, 7, 8):
        return image
    from PIL import ImageOps
    turned = ImageOps.exif_transpose(image)
    if turned is not image:
        image.close()
    return turned


def _flatten(image):
    if image.mode in ("I;16", "I;16L", "I;16B", "I"):
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
    pos, parts, filler = 2, 0, 0
    while True:
        source.seek(pos)
        pair = source.read(2)
        if len(pair) < 2:
            raise PhotoCutShort("a JPEG that ends before its picture")
        if pair[0] != 0xFF or pair[1] in (0x00, 0xFF):
            filler += 1
            if filler > _MAX_JPEG_FILL:
                raise NotAPhoto("junk between a JPEG's segments")
            pos += 1
            continue
        parts += 1
        _too_many(parts)
        marker = pair[1]
        if marker in (0xDA, 0xD9):
            return
        if marker == 0x01 or 0xD0 <= marker <= 0xD8:
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
            if pos == size and idats:
                return
            raise PhotoCutShort("a PNG that ends before its picture is over")
        if not offset:
            raise PhotoCutShort("a PNG that ends inside a chunk's header")


def _gif_sub_blocks(source, pos: int, count: list, comment: bool) -> int:
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
    if len(head) < 13:
        raise PhotoCutShort("a GIF without its screen descriptor")
    pos = 13 + (3 * (2 << (head[10] & 7)) if head[10] & 0x80 else 0)
    blocks, images, count = 0, 0, [0, 0]
    while True:
        source.seek(pos)
        block = source.read(1)
        if not block or block == b"\x3b":
            if images:
                return
            raise PhotoCutShort("a GIF that ends before its picture")
        if block == b"\x21":
            blocks += 1
            _too_many(blocks)
            label = source.read(1)
            if not label:
                raise PhotoCutShort("a GIF extension that runs past the end of the file")
            pos = _gif_sub_blocks(source, pos + 2, count, label == b"\xfe")
        elif block == b"\x2c":
            images += 1
            if images > 1:
                raise NotAPhoto("an animated picture")
            descriptor = source.read(9)
            if len(descriptor) < 9:
                raise PhotoCutShort("a GIF that ends inside its picture's descriptor")
            pos += 10
            if descriptor[8] & 0x80:
                pos += 3 * (2 << (descriptor[8] & 7))
            pos = _gif_sub_blocks(source, pos + 1, count, False)
        else:
            raise NotAPhoto("something in a GIF that is not a block")


def _walk_webp(source, size: int) -> None:
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
    if image.format == "MPO":
        return False
    if image.format == "GIF":
        return False
    return bool(getattr(image, "is_animated", False)) or getattr(image, "n_frames", 1) > 1


def _open_photo(path: str):
    _check_header(path)
    try:
        image = Image.open(path, formats=PHOTO_FORMATS)
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise PictureTooLarge("an image far over the limit") from exc
    except Image.UnidentifiedImageError as exc:
        raise NotAPhoto("not a JPEG, PNG, WEBP or GIF") from exc
    except OSError as exc:
        if "could not create decoder object" in str(exc):
            raise NotAPhoto("a WEBP that cannot be decoded") from exc
        raise
    try:
        width, height = image.size
        _check_size(width, height)
        if _animated(image):
            raise NotAPhoto("an animated picture")
    except Exception:
        image.close()
        raise
    return image


def _prepare(path: str):
    image = _open_photo(path)
    with image:
        width, height = image.size
        if _orientation(image) == 1:
            with open(path, "rb") as source:
                data = source.read()
            layout = _jpeg_layout(data)
            if layout and layout[:2] == (width, height):
                w, h, components, length = layout
                return (w, h, "/DeviceGray" if components == 1 else "/DeviceRGB",
                        data if length == len(data) else memoryview(data)[:length])
            del data
        image.load()
        _check_size(*image.size)
        picture = _flatten(_upright(image))
        buffer = io.BytesIO()
        picture.save(buffer, "JPEG", quality=JPEG_QUALITY)
        size = picture.size
        picture.close()
        return size[0], size[1], "/DeviceRGB", buffer.getvalue()


def _points(value: float) -> bytes:
    text = f"{max(1.0, min(value, MAX_PAGE_POINTS)):.3f}".rstrip("0").rstrip(".")
    return text.encode()


def build_pdf(paths: "list[str]", out_path: str) -> int:
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
            out.flush()
        table = out.tell()
        out.write(b"xref\n0 %d\n0000000000 65535 f \n" % total)
        for number in range(1, total):
            out.write(b"%010d 00000 n \n" % offsets[number])
        out.write(b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (total, table))
    return pages


_pdf_gate: "asyncio.Semaphore | None" = None


def pdfs_at_once() -> int:
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
    gate = pdf_gate()
    await gate.acquire()
    try:
        task = asyncio.ensure_future(asyncio.to_thread(build_pdf, paths, out_path))
    except BaseException:
        gate.release()
        raise

    def finished(done: "asyncio.Future") -> None:
        gate.release()
        if not done.cancelled():
            done.exception()

    task.add_done_callback(finished)
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        def discard(_done: "asyncio.Future") -> None:
            try:
                os.remove(out_path)
            except OSError:
                pass

        task.add_done_callback(discard)
        raise
