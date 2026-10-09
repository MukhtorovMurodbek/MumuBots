"""Camera RAW: CR2, CR3, NEF, ARW, DNG, RAF, ORF and RW2 to JPG, PNG, WEBP, AVIF,
HEIC or TIFF.

A photo straight off a camera -- or a phone: the owner's S23 Ultra shoots 50 and
200 MP DNG in Expert RAW -- is a mosaic of single-colour sensor readings and
what the camera decided about white balance. Turning it into a picture is
LibRaw's job (`rawpy`, MIT, with LibRaw's LGPL/CDDL inside, in a wheel for Linux
and Windows, CR3 included). What is asked of it here is what a person who did
not ask for anything expects: the camera's own white balance, no automatic
brightening, sRGB out, AHD demosaicing and the camera's own orientation.
Pillow takes it from there, through the same table every other still picture is
written by, so each of the six targets is the one the rest of the bot makes.
TIFF is the exception, and on purpose: it comes out at 16 bits per channel, for
somebody who means to edit it (Pillow cannot write that, so the file is written
here -- uncompressed, which is what an editor wants to open).

## The limit that matters is memory

Measured 2026-10-07 on synthetic DNGs, peak resident memory of the decode in a
process of its own (8-bit output; 16-bit adds about 20%):

        24 MP full   346 MB        24 MP half   147 MB
        50 MP full   684 MB        50 MP half   271 MB
       100 MP full 1,334 MB       100 MP half   509 MB
                                  200 MP half   984 MB

About 13 MB a megapixel, which is why "the full picture up to 100 MP" in
PLANNED.md was wrong for a 1 GB container: a 50 MP photo is most of the host
before it is encoded to anything. So:

  * up to FULL_MP (40) the full picture;
  * past it, LibRaw's half-size decode -- a quarter of the pixels, no
    demosaicing at all -- and the person is told, before they pay, what size
    they will get;
  * past MAX_MP (110) it is refused with a sentence and nothing is charged: even
    the half-size decode of a 200 MP file takes a gigabyte.

The megapixel count is read from the file's own metadata, without decoding it
(`inspect`), so the menu, the quote and the refusal all know it the way they
know a JPEG's.
"""
from __future__ import annotations

import os
import struct
from pathlib import Path
from typing import NamedTuple

import formats
from convert_utils import ConversionError, OutOfMemory, save_image

FORMATS = formats.RAW_FORMATS
FULL_MP = float(os.environ.get("CONVERT_RAW_FULL_MP") or 40)
MAX_MP = float(os.environ.get("CONVERT_RAW_MAX_MP") or 110)

WRITES = frozenset({"jpg", "png", "webp", "avif", "heic", "tiff"})


class RawInfo(NamedTuple):
    width: int                  # the picture's own size, what a person would call it
    height: int
    megapixels: float
    half: bool                  # will it be decoded at half size
    out_width: int
    out_height: int
    out_megapixels: float       # what comes out: what the size estimates are about


def _rawpy():
    try:
        import rawpy
        return rawpy
    except ImportError as exc:      # pragma: no cover - formats.py gates this
        raise ConversionError("Camera raw photos aren't supported on this host.") from exc


def _open(path, src_ext: str, unpack: bool = True):
    """The file opened by LibRaw. `unpack=False` reads the header only:
    `rawpy.imread` always unpacks -- the whole raw image decoded into memory,
    about 200 MB for 100 MP -- which a question about the size does not need
    (review, 2026-10-08)."""
    rawpy = _rawpy()
    try:
        if not unpack:
            raw = rawpy.RawPy()
            raw.open_file(str(path))
            return raw
        return rawpy.imread(str(path))
    except MemoryError:
        raise
    except Exception as exc:
        raise ConversionError(f"Couldn't read that {src_ext.upper()} as a camera raw photo: "
                              f"{_tidy(exc)}") from exc


def _mp(value: float) -> str:
    """A megapixel count for a sentence: whole numbers for photos, two places
    for the small ones a test or a thumbnail can be."""
    return f"{value:.0f}" if value >= 10 else f"{value:.2f}"


def _tidy(exc: Exception) -> str:
    text = " ".join(str(exc).split())
    return text[:160] or type(exc).__name__


def _sizes(raw) -> "tuple[int, int]":
    """The picture's own width and height, after the camera's turn."""
    width, height = int(raw.sizes.width), int(raw.sizes.height)
    if int(getattr(raw.sizes, "flip", 0) or 0) in (5, 6):           # turned a quarter: the sides swap
        width, height = height, width
    return width, height


def inspect(path, src_ext: str = "dng") -> RawInfo:
    """Size and plan from the file's header, without decoding a pixel. Raises
    ConversionError for a file that is not a raw photo."""
    with _open(path, src_ext, unpack=False) as raw:
        width, height = _sizes(raw)
    megapixels = width * height / 1_000_000
    half = megapixels > FULL_MP
    out_w, out_h = (width // 2, height // 2) if half else (width, height)
    return RawInfo(width, height, megapixels, half, out_w, out_h, out_w * out_h / 1_000_000)


def megapixels(path, src_ext: str) -> "float | None":
    """The size the way jobs.describe_image gives it for a picture -- None
    for anything that cannot be read -- but the OUTPUT size, because that is
    what the estimates of what comes out are about."""
    try:
        return inspect(path, src_ext).out_megapixels
    except ConversionError:
        return None


def convert(in_path, src_ext: str, target_ext: str, out_path) -> None:
    src_ext, target_ext = formats.normalise(src_ext), formats.normalise(target_ext)
    if target_ext not in WRITES:
        raise ConversionError(f"A camera raw photo doesn't convert to .{target_ext}.")
    rawpy = _rawpy()
    with _open(in_path, src_ext) as raw:
        width, height = _sizes(raw)
        megapixels_ = width * height / 1_000_000
        if megapixels_ > MAX_MP:
            raise ConversionError(
                f"That photo is {_mp(megapixels_)} megapixels. This server converts camera raw photos up to "
                f"{_mp(MAX_MP)}: past that even the half-size decode needs more memory than it has.")
        bits = 16 if target_ext == "tiff" else 8
        try:
            rgb = raw.postprocess(
                use_camera_wb=True, no_auto_bright=True, output_bps=bits,
                output_color=rawpy.ColorSpace.sRGB,
                demosaic_algorithm=rawpy.DemosaicAlgorithm.AHD,
                half_size=megapixels_ > FULL_MP)
        except MemoryError:
            raise OutOfMemory("decoding that raw photo ran out of memory") from None
        except Exception as exc:
            raise ConversionError(f"Couldn't develop that raw photo: {_tidy(exc)}") from exc
    if target_ext == "tiff":
        _write_tiff16(rgb, out_path)
        return
    from PIL import Image
    try:
        picture = Image.fromarray(rgb)
        del rgb
        save_image(picture, target_ext, out_path)
    except ConversionError:
        raise
    except MemoryError:
        raise OutOfMemory("saving that raw photo ran out of memory") from None
    except Exception as exc:
        raise ConversionError(f"Image conversion failed: {exc}") from exc


# ---------------------------------------------------------------------------
# 16 bits per channel
# ---------------------------------------------------------------------------

def _srgb_profile() -> "bytes | None":
    """Pillow carries lcms; with it the TIFF says which colours its numbers are,
    which is what an editor asks about an untagged 16-bit file. Without it the
    file is still valid and is read as sRGB, which is what it is."""
    try:
        from PIL import ImageCms
        return ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    except Exception:
        return None


def _write_tiff16(rgb, out_path) -> None:
    """An uncompressed baseline TIFF of an (height, width, 3) uint16 array: one
    strip, little-endian, written without making a second copy of the pixels."""
    import numpy as np
    height, width, channels = rgb.shape
    if channels != 3 or rgb.dtype != np.uint16:
        raise ConversionError("The developed picture was not 16-bit RGB, so no TIFF was made.")
    data_bytes = width * height * 6
    if data_bytes >= 2 ** 32 - 1024:
        raise ConversionError("That picture is too big for one TIFF strip.")
    profile = _srgb_profile()
    rgb = np.ascontiguousarray(rgb.astype("<u2", copy=False))

    SHORT, LONG, RATIONAL, UNDEFINED = 3, 4, 5, 7
    tags = [  # (tag, type, count, value) -- value is an int, or bytes for anything over 4 bytes
        (256, LONG, 1, width), (257, LONG, 1, height),
        (258, SHORT, 3, struct.pack("<3H", 16, 16, 16)),
        (259, SHORT, 1, 1), (262, SHORT, 1, 2),
        (273, LONG, 1, None),                                       # strip offset, filled in below
        (277, SHORT, 1, 3), (278, LONG, 1, height), (279, LONG, 1, data_bytes),
        (282, RATIONAL, 1, struct.pack("<II", 72, 1)), (283, RATIONAL, 1, struct.pack("<II", 72, 1)),
        (284, SHORT, 1, 1), (296, SHORT, 1, 2),
    ]
    if profile:
        tags.append((34675, UNDEFINED, len(profile), profile))
    tags.sort(key=lambda t: t[0])
    ifd_size = 2 + len(tags) * 12 + 4
    extra_at = 8 + ifd_size
    extra = bytearray()
    placed = []
    for tag, typ, count, value in tags:
        if isinstance(value, bytes) and len(value) > 4:
            placed.append((tag, typ, count, extra_at + len(extra)))
            extra += value + (b"\0" if len(value) % 2 else b"")
        elif isinstance(value, bytes):
            placed.append((tag, typ, count, int.from_bytes(value.ljust(4, b"\0"), "little")))
        else:
            placed.append((tag, typ, count, value))
    data_at = extra_at + len(extra)
    with open(out_path, "wb") as out:
        out.write(b"II*\0" + struct.pack("<I", 8) + struct.pack("<H", len(tags)))
        for tag, typ, count, value in placed:
            if tag == 273:
                value = data_at
            out.write(struct.pack("<HHII", tag, typ, count, value))
        out.write(struct.pack("<I", 0))
        out.write(extra)
        rgb.tofile(out)


def describe(path, src_ext: str) -> "RawInfo | None":
    """inspect(), or None for a file that cannot be read -- for the menu, which
    would rather say nothing than refuse a file the converter might still take."""
    try:
        return inspect(Path(path), src_ext)
    except ConversionError:
        return None
