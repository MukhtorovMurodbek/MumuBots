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
    width: int
    height: int
    megapixels: float
    half: bool
    out_width: int
    out_height: int
    out_megapixels: float


def _rawpy():
    try:
        import rawpy
        return rawpy
    except ImportError as exc:      # pragma: no cover - formats.py gates this
        raise ConversionError("Camera raw photos aren't supported on this host.") from exc


def _open(path, src_ext: str, unpack: bool = True):
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
    return f"{value:.0f}" if value >= 10 else f"{value:.2f}"


def _tidy(exc: Exception) -> str:
    text = " ".join(str(exc).split())
    return text[:160] or type(exc).__name__


def _sizes(raw) -> "tuple[int, int]":
    width, height = int(raw.sizes.width), int(raw.sizes.height)
    if int(getattr(raw.sizes, "flip", 0) or 0) in (5, 6):
        width, height = height, width
    return width, height


def inspect(path, src_ext: str = "dng") -> RawInfo:
    with _open(path, src_ext, unpack=False) as raw:
        width, height = _sizes(raw)
    megapixels = width * height / 1_000_000
    half = megapixels > FULL_MP
    out_w, out_h = (width // 2, height // 2) if half else (width, height)
    return RawInfo(width, height, megapixels, half, out_w, out_h, out_w * out_h / 1_000_000)


def megapixels(path, src_ext: str) -> "float | None":
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


def _srgb_profile() -> "bytes | None":
    try:
        from PIL import ImageCms
        return ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    except Exception:
        return None


def _write_tiff16(rgb, out_path) -> None:
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
    tags = [
        (256, LONG, 1, width), (257, LONG, 1, height),
        (258, SHORT, 3, struct.pack("<3H", 16, 16, 16)),
        (259, SHORT, 1, 1), (262, SHORT, 1, 2),
        (273, LONG, 1, None),
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
    try:
        return inspect(Path(path), src_ext)
    except ConversionError:
        return None
